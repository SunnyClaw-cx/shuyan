"""枢言语言 —— 语义分析器（SPEC §5、§8）。

职责：遍历 AST，做作用域校验、类型检查、静态诊断。

本模块承担 SPEC 的核心承诺之一——**报错即教学**：
- 未定义标识符要列出「你是不是想写 X」（编辑距离候选）；
- 类型不符要说清两侧类型与转换写法；
- 一次遍历**报告全部问题**，不遇错即停。

设计取舍：v0.1 采用**保守推断**——凡是推不出的类型一律标为 `任意`，
绝不为「凑出结论」而猜测类型。宁可少报一个错，不可虚报一个错。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from .ast_nodes import (
    Assert, Assign, Binary, Block, Break, Call, Compare, Continue, ExprStmt,
    ForEach, FuncDef, FunctionExpr, Identifier, If, Index, InterpolatedString,
    ListLiteral, Literal, Logical, MapLiteral, Member, Node, Program, RangeExpr,
    RepeatRange, RepeatTimes, Return, Ternary, Throw, Try, TypeDecl, Unary,
    Use, VarDecl, While, walk,
)
from .builtins import StdLib
from .diagnostics import (
    Diagnostic, E_ARG_COUNT, E_BAD_ASSIGN_TARGET, E_BAD_INDEX,
    E_COMPARE_CHAIN, E_CONST_NO_INIT, E_DIVIDE_ZERO, E_IMPORT_CYCLE,
    E_LOOP_KEYWORD,
    E_NO_ATTRIBUTE, E_NO_FIELD, E_NO_MEMBER, E_NOT_CALLABLE, E_REDEFINED,
    E_RETURN_OUTSIDE_FN,
    E_RETURN_TYPE, E_STRUCT_FIELD, E_TYPE_MISMATCH, E_UNDEFINED,
    SemanticError, Severity,
    W_CONST_COND, W_EMPTY_BLOCK, W_SHADOW, W_UNREACHABLE, W_UNUSED,
)
from .names import CORE_FUNCTION_NAMES, MODULE_NAMES
from .source import Span
from .values import TYPE_ALIASES, can_assign, normalized_type

ANY = "任意"

# 枚举值的**只读属性**（SPEC §3.9.3）：所有枚举通用。
ENUM_PROPERTIES: dict[str, str] = {"序号": "整数", "名称": "文本"}

# 内置函数的**参数类型**约束：{名称: [类型 or None, ...]}（None 表示不限）
# 用途：让 `长度(1)` 这类错误在**编译期**就暴露，而不是拖到运行时。
# 这直接支撑「报错即教学」的承诺——错误越早出现，修复成本越低。
CORE_PARAM_TYPES: dict[str, list[str | None]] = {
    "长度": ["文本|列表|映射"],
    "类型名": [None], "是否空": [None],
    "转文本": [None], "转整数": ["数字|文本"], "转小数": ["数字|文本"],
    "转布尔": [None],
    "取整": ["数字"], "四舍五入": ["数字", "整数"],
    "取小": ["数字", "数字"], "绝对值": ["数字"], "次方": ["数字", "数字"],
    "幂运算": ["数字", "数字"],
    "求和": ["列表"], "平均值": ["列表"], "范围": ["整数", "整数", "整数"],
    "排序": ["列表"], "反转": ["列表"], "去重": ["列表"],
    "连接": ["列表"], "分割": ["文本"], "入列": ["列表", None],
    "切片": ["文本|列表", "整数", "整数"], "枚举": ["文本|列表|映射"],
    "截取": ["文本", "整数", "整数"], "查找": ["文本", "文本"],
    "替换": ["文本", "文本", "文本"], "去空白": ["文本"],
    "开头是": ["文本", "文本"], "结尾是": ["文本", "文本"],
    "大写": ["文本"], "小写": ["文本"],
    "复制": ["文本", "整数"],
    "取键": ["映射"], "取值": ["映射"], "有键": ["映射", None],
    # 高阶函数：第 2 个参数是**函数**，静态推断无法验证签名兼容性，
    # 故一律写 None（不限）——运行期由 `StdLib._invoke` 给出明确报错。
    # 若在这里假装能校验，只会造出「有时误报、有时漏报」的假保证。
    "筛选": ["文本|列表|映射", None],
    "映射列表": ["文本|列表|映射", None],
    "归约": ["文本|列表|映射", None, None],
    "任何": ["文本|列表|映射", None],
    "全部": ["文本|列表|映射", None],
    "查找首个": ["文本|列表|映射", None],
    "排序依据": ["文本|列表|映射", None, "布尔"],
    "分组依据": ["文本|列表|映射", None],
    "映射值": ["映射", None],
    "计数依据": ["文本|列表|映射", None],
}

# 内置模块成员的参数类型约束
MODULE_PARAM_TYPES: dict[str, list[str | None]] = {
    "文本.填充": ["文本", "整数", "文本", "文本"],
    "文本.重复": ["文本", "整数"],
    "文本.逐字": ["文本"], "文本.码点": ["文本"],
    "文本.显示宽度": ["文本"], "文本.格式化": ["文本", "映射"],
    "列表.去重": ["列表"], "列表.扁平": ["列表"], "列表.分组": ["列表", "整数"],
    "列表.求和": ["列表"], "列表.排序": ["列表"],
    "映射.合并": ["映射"], "映射.键列表": ["映射"], "映射.值列表": ["映射"],
    "数学.正弦": ["数字"], "数学.余弦": ["数字"], "数学.正切": ["数字"],
    "数学.平方根": ["数字"], "数学.幂": ["数字", "数字"],
    "数学.对数": ["数字", "数字"], "数学.取整": ["数字"],
    "数学.绝对值": ["数字"],
    "随机.随机数": ["整数", "整数"], "随机.选一个": ["列表"],
    "随机.打乱": ["列表"], "随机.设随机种子": [None],
    "时间.休眠": ["数字"],
    "文件.读文本": ["文本"], "文件.写文本": ["文本", None],
    "文件.追加": ["文本", None], "文件.存在": ["文本"], "文件.删除": ["文本"],
    "文件.列表": ["文本"], "文件.创建目录": ["文本"],
    "编码.编码Base64": ["文本"], "编码.解码Base64": ["文本"],
    "加密.MD5": ["文本"], "加密.SHA256": ["文本"],
    "正则.匹配": ["文本", "文本"], "正则.是否匹配": ["文本", "文本"],
    "正则.替换": ["文本", "文本", "文本"], "正则.分组": ["文本", "文本"],
}

# 数值参数也接受「整数/小数」两种写法
_TYPE_GROUP: dict[str, frozenset[str]] = {
    "数字": frozenset({"整数", "小数", "任意"}),
    "文本": frozenset({"文本", "任意"}),
    "整数": frozenset({"整数", "任意"}),
    "列表": frozenset({"列表", "任意"}),
    "映射": frozenset({"映射", "任意"}),
    "文本|列表|映射": frozenset({"文本", "列表", "映射", "任意"}),
    "文本|列表": frozenset({"文本", "列表", "任意"}),
    "数字|文本": frozenset({"整数", "小数", "文本", "任意"}),
}

# 内置函数的静态签名：{名称: (必填数, 最多参数数 or None, 返回类型)}
# 用于在调用点做参数个数校验，让错误在**运行前**就暴露。
CORE_SIGNATURES: dict[str, tuple[int, int | None, str]] = {
    "长度": (1, 1, "整数"), "类型名": (1, 1, "文本"), "是否空": (1, 1, "布尔"),
    "转文本": (1, 1, "文本"), "转整数": (1, 1, "整数"), "转小数": (1, 1, "小数"),
    "转布尔": (1, 1, "布尔"), "取整": (1, 1, "整数"), "四舍五入": (1, 2, "整数"),
    "取小": (2, 2, ANY), "最大": (1, None, ANY), "最小": (1, None, ANY),
    "绝对值": (1, 1, ANY), "次方": (2, 2, ANY), "幂运算": (2, 2, ANY),
    "求和": (1, 1, ANY), "平均值": (1, 1, "小数"),
    "范围": (1, 3, "列表"), "排序": (1, 2, "列表"), "反转": (1, 1, "列表"),
    "连接": (1, 2, "文本"), "分割": (1, 2, "列表"), "入列": (2, 2, "列表"),
    "去重": (1, 1, "列表"), "切片": (2, 3, ANY), "枚举": (1, 1, "列表"),
    "截取": (2, 3, "文本"), "查找": (2, 2, "整数"), "替换": (3, 3, "文本"),
    "去空白": (1, 2, "文本"), "开头是": (2, 2, "布尔"), "结尾是": (2, 2, "布尔"),
    "大写": (1, 1, "文本"), "小写": (1, 1, "文本"), "包含": (2, 2, "布尔"),
    "复制": (2, 3, "文本"), "取键": (1, 1, "列表"), "取值": (1, 1, "列表"),
    "有键": (2, 2, "布尔"), "输入": (0, 1, "文本"),
    # 高阶函数：返回类型按用途固定；`排序依据` 保留原列表类型，
    # 故用 列表（文本输入也返回列表，与实现一致）。
    "筛选": (2, 2, "列表"), "映射列表": (2, 2, "列表"),
    "归约": (2, 3, ANY), "任何": (2, 2, "布尔"), "全部": (2, 2, "布尔"),
    "查找首个": (2, 2, ANY), "排序依据": (2, 3, "列表"),
    "分组依据": (2, 2, "映射"), "映射值": (2, 2, "映射"),
    "计数依据": (2, 2, "映射"),
    "输出": (0, None, "空"), "打印": (0, None, "空"), "说": (0, None, "空"),
}

# 内置模块的静态签名（按 `模块.函数` 键）
MODULE_SIGNATURES: dict[str, tuple[int, int | None, str]] = {
    "文本.填充": (2, 4, "文本"), "文本.重复": (2, 3, "文本"),
    "文本.逐字": (1, 1, "列表"), "文本.字符": (1, 1, "文本"),
    "文本.码点": (1, 1, "整数"), "文本.格式化": (2, 2, "文本"),
    "文本.正则匹配": (2, 2, "列表"), "文本.替换正则": (3, 3, "文本"),
    "文本.对齐": (2, 4, "文本"), "文本.显示宽度": (1, 1, "整数"),
    "列表.去重": (1, 1, "列表"), "列表.扁平": (1, 1, "列表"),
    "列表.分组": (2, 2, "列表"), "列表.压缩": (2, 2, "列表"),
    "列表.枚举": (1, 1, "列表"), "列表.切片": (2, 3, ANY),
    "列表.求和": (1, 1, ANY), "列表.排序": (1, 2, "列表"),
    "映射.合并": (1, None, "映射"), "映射.键列表": (1, 1, "列表"),
    "映射.值列表": (1, 1, "列表"), "映射.有键": (2, 2, "布尔"),
    "映射.删除": (2, 2, "映射"),
    "数学.圆周率": (0, 0, "小数"), "数学.自然常数": (0, 0, "小数"),
    "数学.正弦": (1, 1, "小数"), "数学.余弦": (1, 1, "小数"),
    "数学.正切": (1, 1, "小数"), "数学.平方根": (1, 1, "小数"),
    "数学.幂": (2, 2, "小数"), "数学.对数": (1, 2, "小数"),
    "数学.取整": (1, 1, "整数"), "数学.向下取整": (1, 1, "整数"),
    "数学.向上取整": (1, 1, "整数"), "数学.绝对值": (1, 1, ANY),
    "数学.最大": (1, None, ANY), "数学.最小": (1, None, ANY),
    "随机.随机数": (1, 2, "整数"), "随机.随机小数": (0, 0, "小数"),
    "随机.选一个": (1, 1, ANY), "随机.打乱": (1, 1, "列表"),
    "随机.设随机种子": (1, 1, "空"),
    "时间.现在": (0, 0, "文本"), "时间.时间戳": (0, 0, "小数"),
    "时间.格式化": (1, 2, "文本"), "时间.休眠": (1, 1, "空"),
    "时间.年": (0, 0, "整数"), "时间.月": (0, 0, "整数"),
    "时间.日": (0, 0, "整数"), "时间.时": (0, 0, "整数"),
    "时间.分": (0, 0, "整数"), "时间.秒": (0, 0, "整数"),
    "文件.读文本": (1, 2, "文本"), "文件.写文本": (2, 3, "空"),
    "文件.追加": (2, 3, "空"), "文件.存在": (1, 1, "布尔"),
    "文件.删除": (1, 1, "空"), "文件.列表": (0, 1, "列表"),
    "文件.创建目录": (1, 1, "空"), "文件.路径合并": (1, None, "文本"),
    "文件.目录名": (1, 1, "文本"), "文件.文件名": (1, 1, "文本"),
    "系统.参数": (0, 0, "列表"), "系统.环境变量": (1, 2, "文本"),
    "系统.平台": (0, 0, "文本"), "系统.退出": (0, 1, "空"),
    "系统.执行": (1, 2, "映射"), "系统.当前目录": (0, 0, "文本"),
    "编码.编码Base64": (1, 1, "文本"), "编码.解码Base64": (1, 1, "文本"),
    "编码.编码十六进制": (1, 1, "文本"), "编码.解码十六进制": (1, 1, "文本"),
    "加密.哈希": (1, 2, "文本"), "加密.MD5": (1, 1, "文本"),
    "加密.SHA256": (1, 1, "文本"),
    "正则.匹配": (2, 2, "列表"), "正则.替换": (3, 3, "文本"),
    "正则.是否匹配": (2, 2, "布尔"), "正则.分组": (2, 2, "列表"),
}


@dataclass
class Symbol:
    name: str
    type: str
    is_const: bool = False
    span: Span = field(default_factory=lambda: Span(0, 0, 0, 0))
    used: bool = False
    is_function: bool = False
    params: list = field(default_factory=list)
    return_type: str | None = None
    is_global: bool = False
    is_param: bool = False
    has_explicit_type: bool = False
    # 类型由初始值 `空` 推出（`定义 v = 空`）。此时类型是**待定**的，
    # 不锁定后续赋值 —— 见 `_check_assign` 的说明。
    from_nil_init: bool = False


class Scope:
    def __init__(self, parent: "Scope | None", kind: str = "block"):
        self.parent = parent
        self.kind = kind                    # global | function | block
        self.symbols: dict[str, Symbol] = {}

    def define(self, sym: Symbol) -> Symbol | None:
        """定义符号。返回被遮蔽的旧符号（若有）。"""
        old = self.symbols.get(sym.name)
        self.symbols[sym.name] = sym
        return old

    def lookup(self, name: str) -> Symbol | None:
        scope: Scope | None = self
        while scope is not None:
            if name in scope.symbols:
                return scope.symbols[name]
            scope = scope.parent
        return None

    def lookup_local(self, name: str) -> Symbol | None:
        return self.symbols.get(name)

    def function_scope(self) -> "Scope":
        scope: Scope = self
        while scope.parent is not None and scope.kind not in ("function", "global"):
            scope = scope.parent
        return scope

    def is_global_scope(self) -> bool:
        return self.kind == "global"


def edit_distance(a: str, b: str) -> int:
    """Levenshtein 距离（用于「你是不是想写 X」候选）。"""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(
                prev[j] + 1,          # 删除
                cur[j - 1] + 1,       # 插入
                prev[j - 1] + (ca != cb),  # 替换
            ))
        prev = cur
    return prev[-1]


class Analyzer:
    def __init__(self, stdlib: StdLib | None = None,
                 filename: str = "", source_lines: list[str] | None = None):
        self.stdlib = stdlib or StdLib()
        # 当前分析的源文件与其行内容 —— 诊断要带上它们才能跨文件正确定位。
        self.filename = filename
        self.source_lines = source_lines
        self.diagnostics: list[Diagnostic] = []
        self.global_scope = Scope(None, "global")
        self.scope = self.global_scope
        self.functions: dict[str, Symbol] = {}
        # 类型注册表（SPEC §3.9）：结构体与枚举。
        # `type_aliases` 是**解析后**的别名目标（支持别名链与前置引用）。
        self.struct_types: dict[str, StructType] = {}
        self.enum_types: dict[str, EnumType] = {}
        self.type_aliases: dict[str, TypeDecl] = {}
        self._resolve_cache: dict[str | None, str] = {}
        # 合法的「类型名出现位置」标记（构造器标的是 Call 节点，
        # 成员访问的容器由 `_expr_Member` 直接处理，不经过 `_expr_Identifier`）；
        # 未标记的裸类型名出现在**值位置**即为用法错误。
        self._type_entry_uses: set[int] = set()
        # 正在加载的本机模块（防循环引入无限递归）。
        # 用 `set` 判重（按 realpath），用 `list` 记**引入链**用于报错展示。
        self._loading_modules: set[str] = set()
        self._loading_names: list[str] = []
        # 以「命名空间」形式引入的本机模块：{模块名: {导出名, ...}}
        # 用于 `工具.平方(...)` 的成员校验（与内置模块同一套检查）
        self.imported_file_modules: dict[str, set[str]] = {}
        # 以「命名空间」形式引入的本机模块名集合（供构造器识别）
        self.imported_file_names: set[str] = set()
        # 「裸类型名」→ 它来自哪个模块文件（仅对**被引入模块**的类型登记）。
        #
        # 为什么需要：整模块引入（`引入 "领域模型"`）按 SPEC §3.8.1 只做
        # **命名空间绑定**，运行期不会把 `领域模型` 里的类型绑到本文件，
        # 故本文件裸写 `单据状态` 应当在**编译期**就报错，而不是拖到运行期
        # 变成「没有定义」。此处记录来源，专为生成这句可行动的诊断。
        self._module_types: dict[str, str] = {}
        # 由调用方注入的模块**完整路径解析函数**（与解释器共用同一份实现）。
        # 两处各写一份候选路径必然分叉 —— 这正是项目铁律的落实。
        self._module_fullpath = None
        # 供解释器复用的旁表（id(节点) → 类型信息）
        self.struct_instances: dict[int, StructType] = {}
        self.enum_instances: dict[int, EnumType] = {}
        self._fn_return_declared: str | None = None
        self._in_function = 0
        self._loop_depth = 0
        self._imported_modules: set[str] = set()
        # 供解释器复用的静态类型表：id(节点) → 类型名
        self.node_types: dict[int, str] = {}

    # ------------------------------------------------------------ 工具

    def err(self, code: str, message: str, span: Span,
            hints: list[str] | None = None) -> None:
        self.diagnostics.append(
            Diagnostic(code, message, span, Severity.ERROR, hints or [],
                       file=self.filename, source_lines=self.source_lines))

    def warn(self, code: str, message: str, span: Span,
             hints: list[str] | None = None) -> None:
        self.diagnostics.append(
            Diagnostic(code, message, span, Severity.WARNING, hints or [],
                       file=self.filename, source_lines=self.source_lines))

    def _push(self, kind: str = "block") -> Scope:
        self.scope = Scope(self.scope, kind)
        return self.scope

    def _pop(self) -> None:
        if self.scope.parent is not None:
            self.scope = self.scope.parent

    def _suggest(self, name: str) -> list[str]:
        """按编辑距离给出拼写候选（最多 3 个）。"""
        pool: set[str] = set(self._all_visible_names())
        pool.update(CORE_SIGNATURES.keys())
        pool.update(self.stdlib.core_names())
        scored = []
        for cand in pool:
            d = edit_distance(name, cand)
            if d <= 2 or d <= max(1, len(name) // 2):
                scored.append((d, cand))
        scored.sort()
        return [c for _, c in scored[:3]]

    def _all_visible_names(self):
        scope: Scope | None = self.scope
        while scope is not None:
            yield from scope.symbols.keys()
            scope = scope.parent

    # ------------------------------------------------------------ 入口

    def analyze(self, program: Program) -> tuple[dict[int, str], list[Diagnostic]]:
        # 第一遍：登记全部顶层函数与类型（支持相互递归与前置引用）
        self._hoist(program)
        # 第二遍：逐语句检查
        for stmt in program.body:
            self._visit_stmt(stmt)
        # 收尾：未使用变量告警（全局）
        self._report_unused(self.global_scope)
        return self.node_types, self.diagnostics

    def _hoist(self, program: Program) -> None:
        for stmt in program.body:
            if isinstance(stmt, FuncDef):
                sym = Symbol(name=stmt.name, type="函数",
                             span=stmt.span, is_function=True,
                             params=stmt.params,
                             return_type=normalized_type(stmt.return_type),
                             is_global=True)
                sym.used = False
                old = self.global_scope.define(sym)
                if old is not None:
                    self.err(E_REDEFINED,
                             f"函数「{stmt.name}」被重复定义。", stmt.span,
                             [f"它在第 {old.span.line} 行已经定义过了",
                              "改用不同的名字，或删掉其中一处"])
                self.functions[stmt.name] = sym
            elif isinstance(stmt, TypeDecl):
                self._register_type(stmt)

    def _register_type(self, stmt: TypeDecl) -> None:
        """登记类型定义（第一遍调用，支持前置引用与别名链）。"""
        name = stmt.name
        if stmt.kind == "struct":
            if name in self.struct_types or name in self.enum_types:
                self.err(E_REDEFINED, f"类型「{name}」被重复定义。", stmt.span,
                         ["类型名不能与已声明的类型重名"])
                return
            self.struct_types[name] = StructType(
                name=name, fields=[(f, t) for f, t, _ in stmt.fields],
                span=stmt.span)
        elif stmt.kind == "enum":
            if name in self.struct_types or name in self.enum_types:
                self.err(E_REDEFINED, f"类型「{name}」被重复定义。", stmt.span,
                         ["类型名不能与已声明的类型重名"])
                return
            self.enum_types[name] = EnumType(
                name=name, members=[(m, i) for i, (m, _) in
                                    enumerate(stmt.members, start=1)],
                span=stmt.span)
        else:
            self.type_aliases[name] = stmt

    # ------------------------------------------------------ 类型名解析

    def _resolve_type(self, name: str | None) -> str:
        """把类型名解析到「最终类型」（展开别名链，带缓存）。

        `类型 编号 = 整数` 后，`编号` 解析为 `整数`。别名链有环时报
        `D3019` 并停下（不死循环）。
        """
        if name is None:
            return ANY
        cache = getattr(self, "_resolve_cache", None)
        if cache is None:
            cache = self._resolve_cache = {}
        if name in cache:
            return cache[name]
        seen: list[str] = []
        cur = name
        while True:
            if cur in self.struct_types or cur in self.enum_types:
                cache[name] = cur
                return cur
            if cur in self.type_aliases:
                if cur in seen:
                    self.err(E_NO_MEMBER,
                             "类型别名形成了循环：" + " → ".join(seen + [cur]),
                             self.type_aliases[cur].span,
                             ["让别名最终指向一个具体类型"])
                    cache[name] = ANY
                    return ANY
                seen.append(cur)
                cur = self.type_aliases[cur].definition
                if cur in cache:                    # 借助已有缓存收尾
                    cache[name] = cache[cur]
                    return cache[name]
                continue
            cache[name] = cur
            return cur

    def _is_composite(self, t: str) -> bool:
        return t in self.struct_types or t in self.enum_types

    # ------------------------------------------------------------ 语句

    def _visit_stmt(self, node: Node) -> None:
        method = getattr(self, f"_stmt_{type(node).__name__}", None)
        if method is not None:
            method(node)

    def _check_shadow_builtin(self, name: str, span: Span, kind: str) -> None:
        """检查新名字是否**遮蔽了内置模块 / 核心函数**，并给出可行动提醒。

        **为什么必须在「定义处」提醒**：`请 左对齐(文本, 宽度)` 这样的形参
        会遮蔽同名内置模块，于是函数体里的 `文本.填充(...)` 取到的是那个
        参数（一个字符串），运行期报出「「文本」没有「填充」属性」。
        用户拿着这条报错去文本模块里翻 `填充` —— 那里明明有，于是
        完全找不到真因（实测踩到：企业级工程 `projects/应收对账/报表.sy`）。

        报错发生在**调用处**，真因在**定义处**。把提醒前移到定义处，
        一眼就能看到「是这个名字挡了路」。
        """
        if name in MODULE_NAMES:
            self.warn(
                W_SHADOW,
                f"{kind}「{name}」与内置模块「{name}」同名；"
                f"在本作用域里写 `{name}.某成员` 取到的是它，而**不是**模块。",
                span,
                [f"给{kind}换个名字（例如 `{name}值` / `原文`）",
                 f"换名后 `{name}.某成员` 才会回到内置模块"])
        elif name in CORE_FUNCTION_NAMES:
            self.warn(
                W_SHADOW,
                f"{kind}「{name}」与内置函数「{name}」同名；"
                f"在本作用域里写 `{name}(...)` 调用的是它，而**不是**内置函数。",
                span,
                [f"给{kind}换个名字（例如 `{name}结果`）"])

    def _stmt_VarDecl(self, node: VarDecl) -> None:
        if node.is_global:
            target = self.global_scope
            existing = target.lookup_local(node.name)
            if existing is not None:
                # 全局声明用于**回写**已存在的全局变量
                if node.init is not None:
                    vt = self._visit_expr(node.init)
                    self._check_assign(vt, existing.type, node.init.span,
                                       existing.name)
                existing.used = True
                return
            node.is_global = True

        if node.is_const and node.init is None:
            self.err(E_CONST_NO_INIT, f"常量「{node.name}」必须给出初始值。",
                     node.span, ["写成 `常量 名字 = 值`，或改用 `定义`"])

        init_type = ANY
        if node.init is not None:
            init_type = self._visit_expr(node.init)

        declared = normalized_type(node.type_ann)
        if declared is not None:
            declared = self._resolve_type(declared)
            self._check_assign(init_type, declared, node.span, node.name,
                               is_decl=True, init=node.init)
            final_type = declared
        else:
            final_type = init_type

        # 遮蔽检查
        outer = None
        scope: Scope | None = self.scope.parent
        while scope is not None:
            found = scope.lookup_local(node.name)
            if found is not None:
                outer = found
                break
            scope = scope.parent
        if outer is not None:
            self.warn(W_SHADOW,
                      f"变量「{node.name}」遮蔽了外层同名变量"
                      f"（在第 {outer.span.line} 行定义）。",
                      node.span,
                      ["如果是有意为之可忽略；否则换一个名字",
                       "也可考虑给外层变量改个更具体的名字"])

        sym = Symbol(name=node.name, type=final_type, is_const=node.is_const,
                     span=node.span,
                     has_explicit_type=declared is not None,
                     from_nil_init=(declared is None
                                    and node.init is not None
                                    and final_type == "空"))
        self._check_shadow_builtin(node.name, node.span, "变量")
        old = self.scope.define(sym)
        if old is not None and self.scope.is_global_scope():
            self.err(E_REDEFINED,
                     f"「{node.name}」被重复定义。", node.span,
                     [f"它在第 {old.span.line} 行已经定义过了",
                      "改用不同的名字"])
        self.node_types[id(node)] = final_type

    def _stmt_Assign(self, node: Assign) -> None:
        value_type = self._visit_expr(node.value)

        if isinstance(node.target, Identifier):
            name = node.target.name
            sym = self.scope.lookup(name)
            if sym is None:
                self.err(E_UNDEFINED,
                         f"变量「{name}」还没有定义。", node.target.span,
                         self._undefined_hints(name))
                return
            sym.used = True
            # **空值初始化 ⇒ 类型待定，不锁死**。
            #
            # `定义 v = 空` 是「先声明、后按条件赋值」这一企业代码高频模式
            # 的写法。若把类型锁定为「空」，`v = 1` 会报
            # 「不能把整数赋值给空变量」—— 用户被迫写 `定义 v: 任意 = 空`，
            # 而这在语义上并没说清任何东西，只是绕开了检查。
            #
            # 因此把「由 `空` 推出」视为**占位推断**：首次赋非空值时，
            # 类型细化为该值的类型（与下方 `ANY` 的细化同一口径）。
            if sym.from_nil_init:
                if value_type != "空":
                    sym.type = value_type
                    sym.from_nil_init = False
                self.node_types[id(node)] = value_type
                return
            self._check_assign(value_type, sym.type, node.value.span, name)
            if sym.is_const:
                self.err(E_TYPE_MISMATCH,
                         f"不能给常量「{name}」重新赋值。", node.span,
                         [f"常量在第 {sym.span.line} 行用 `常量` 声明，不可修改",
                          "如需可变，请改用 `定义`"])
                return
            # 赋值可能细化类型（如先 `定义 甲` 后赋整数）
            if sym.type == ANY and value_type != ANY:
                sym.type = value_type
            self.node_types[id(node)] = value_type
            return

        if isinstance(node.target, Member):
            self._visit_expr(node.target)
            self._check_member_write(node.target, value_type)
            self.node_types[id(node)] = value_type
            return

        if isinstance(node.target, Index):
            self._visit_expr(node.target)
            self.node_types[id(node)] = value_type
            return

        self.err(E_BAD_ASSIGN_TARGET,
                 "赋值号左侧必须是变量、列表元素或对象属性。",
                 node.span, ["检查左侧表达式"])

    def _stmt_ExprStmt(self, node: ExprStmt) -> None:
        if node.expr is not None:
            self._visit_expr(node.expr)

    def _stmt_If(self, node: If) -> None:
        for cond, body in node.branches:
            if cond is not None:
                self._visit_expr(cond)
                if isinstance(cond, Literal) and cond.literal_kind == "bool":
                    self.warn(W_CONST_COND,
                              "这个分支条件是一个固定的布尔值，"
                              "会造成另一分支永远不执行。",
                              cond.span,
                              ["检查是否误写；如需恒真循环请用 `循环`"])
            self._visit_block(body)

    def _stmt_While(self, node: While) -> None:
        if node.cond is not None:
            self._visit_expr(node.cond)
            if (isinstance(node.cond, Literal)
                    and node.cond.literal_kind == "bool"
                    and node.cond.value is True
                    and not self._block_has_exit(node.body)):
                self.warn("W4006",
                          "这是一个恒真的循环，且循环体内没有 `跳出`，"
                          "程序将不会结束。",
                          node.span,
                          ["确认这是有意为之（如常驻服务）",
                           "否则补上退出条件或 `跳出`"])
        self._loop_depth += 1
        self._visit_block(node.body)
        self._loop_depth -= 1

    def _stmt_RepeatTimes(self, node: RepeatTimes) -> None:
        t = self._visit_expr(node.times)
        if t not in (ANY, "整数", "小数"):
            self.err(E_TYPE_MISMATCH,
                     f"`重复 N 次` 的 N 必须是整数，实际是「{t}」。",
                     node.times.span if node.times else node.span,
                     ["用整数，或先用 `取整(...)` 转换"])
        self._loop_depth += 1
        self._visit_block(node.body)
        self._loop_depth -= 1

    def _stmt_RepeatRange(self, node: RepeatRange) -> None:
        for expr in (node.start, node.stop, node.step):
            if expr is not None:
                self._visit_expr(expr)
        self._push("block")
        self.scope.define(Symbol(name=node.var_name, type="整数",
                                 span=node.span, used=True))
        self._loop_depth += 1
        self._visit_block(node.body, new_scope=False)
        self._loop_depth -= 1
        self._pop()

    def _stmt_ForEach(self, node: ForEach) -> None:
        it = ANY
        if node.iterable is not None:
            it = self._visit_expr(node.iterable)
        if it not in (ANY, "列表", "文本", "映射"):
            self.err(E_TYPE_MISMATCH,
                     f"`遍历` 需要一个列表、文本或映射，实际是「{it}」。",
                     node.iterable.span if node.iterable else node.span,
                     ["用 `列表` 或 `文本` 作遍历对象"])
        elem = "文本" if it == "文本" else ANY
        self._push("block")
        self._check_shadow_builtin(node.var_name, node.span, "循环变量")
        self.scope.define(Symbol(name=node.var_name, type=elem,
                                 span=node.span, used=True))
        self._loop_depth += 1
        self._visit_block(node.body, new_scope=False)
        self._loop_depth -= 1
        self._pop()

    def _stmt_FuncDef(self, node: FuncDef) -> None:
        sym = self.global_scope.lookup_local(node.name)
        if sym is None:
            sym = Symbol(name=node.name, type="函数", span=node.span,
                         is_function=True, params=node.params,
                         return_type=normalized_type(node.return_type))
            self.global_scope.define(sym)
        else:
            sym.used = True

        self._in_function += 1
        prev_return = self._fn_return_declared
        self._fn_return_declared = sym.return_type

        self._push("function")
        for p in node.params:
            pt = normalized_type(p.type_ann) or ANY
            self._check_shadow_builtin(p.name, p.span, "参数")
            self.scope.define(Symbol(name=p.name, type=pt, span=p.span,
                                     used=True, is_param=True,
                                     has_explicit_type=p.type_ann is not None))
            if p.default is not None:
                self._visit_expr(p.default)
        for stmt in node.body:
            self._visit_stmt(stmt)
        self._report_unused(self.scope)
        self._pop()

        self._fn_return_declared = prev_return
        self._in_function -= 1
        self.node_types[id(node)] = "函数"

    def _stmt_Return(self, node: Return) -> None:
        if self._in_function == 0:
            self.err(E_RETURN_OUTSIDE_FN,
                     "`返回` 只能出现在函数内部。", node.span,
                     ["把它放进 `请 函数名(...)` 的函数体里"])
            if node.value is not None:
                self._visit_expr(node.value)
            return
        if node.value is None:
            return
        vt = self._visit_expr(node.value)
        declared = self._fn_return_declared
        if declared and not can_assign(vt, declared):
            self.err(E_RETURN_TYPE,
                     f"函数声明的返回类型是「{declared}」，"
                     f"但这里返回的是「{vt}」。",
                     node.value.span,
                     [self._convert_hint(vt, declared)])

    def _stmt_Throw(self, node: Throw) -> None:
        if node.value is not None:
            self._visit_expr(node.value)

    def _stmt_Try(self, node: Try) -> None:
        self._visit_block(node.body)
        for var, body in node.handlers:
            self._push("block")
            if var:
                self.scope.define(Symbol(name=var, type=ANY,
                                         span=node.span, used=True))
            self._visit_block(body, new_scope=False)
            self._pop()
        if node.finalizer is not None:
            self._visit_block(node.finalizer)

    def _stmt_Break(self, node: Break) -> None:
        if self._loop_depth == 0:
            self.err(E_LOOP_KEYWORD,
                     "`跳出` 只能出现在循环内部。", node.span,
                     ["把它移进 `遍历` / `当` / `重复` 的循环体"])

    def _stmt_Continue(self, node: Continue) -> None:
        if self._loop_depth == 0:
            self.err(E_LOOP_KEYWORD,
                     "`继续` 只能出现在循环内部。", node.span,
                     ["把它移进 `遍历` / `当` / `重复` 的循环体"])

    def _stmt_Use(self, node: Use) -> None:
        name = node.module or ""
        if node.names is not None and not name:
            return

        # ① 内置模块
        if self.stdlib.has_module(name):
            self._imported_modules.add(name)
            for member in self.stdlib.modules[name]:
                key = f"{name}.{member}"
                self.global_scope.define(Symbol(name=key, type=ANY,
                                                span=node.span, used=True))
            if node.names:
                for n in node.names:
                    if n not in self.stdlib.modules[name]:
                        self.err(E_NO_ATTRIBUTE,
                                 f"模块「{name}」里没有「{n}」这个成员。",
                                 node.span,
                                 [f"可用成员：{'、'.join(sorted(self.stdlib.modules[name]))}"])
            return

        # ② 本机枢言模块（.sy 文件）——多文件工程的基础
        if self._import_module_file(node, name):
            return

        # ③ 都不是：给出可行动的诊断
        available = "、".join(self.stdlib.module_names())
        self.err(E_UNDEFINED,
                 f"找不到模块「{name}」——既不是内置模块，"
                 f"也不是可读的 .sy 文件。",
                 node.span,
                 [f"内置模块：{available}",
                  f"本机模块：确认当前目录下有「{name}.sy」",
                  "路径相对于**主脚本所在的目录**"])

    def _import_module_file(self, node: Use, name: str) -> bool:
        """把本机 `.sy` 模块引入当前作用域。

        **引入口径与内置模块完全一致**（这是刻意的，避免出现两套心智模型）：
          - `引入 "工具"`             → 绑定命名空间 `工具`，用 `工具.成员`
          - `引入 { 平方 } 从 "工具"`  → 直接绑定 `平方`

        与运行期 `_load_module_file` 共用**同一套候选路径**。
        两处若各写一遍，迟早出现「语义层说行、运行期说不行」的错位。
        """
        from .lexer import Lexer
        from .parser import Parser

        # 模块路径解析走**唯一入口** `StdLib.resolve_module_file` ——
        # 与解释器 `_load_module_file` 共用同一份候选路径实现。
        full = self.stdlib.resolve_module_file(name)
        if full is None:
            return False
        real = os.path.realpath(full)
        if real in self._loading_modules:
            # **循环引入：明确报错，不再静默返回**。
            #
            # 早期版本此处 `return True`（什么都不引入，也不报错），本意是
            # 「避免死递归」。但实测发现两个问题：
            #   ① **运行期没有同口径防护** —— 语义层放行、运行期照旧递归，
            #      子解释器层层展开直到撞上「递归太深」，报出的是一个与真因
            #      无关的错误（实测：甲↔乙 循环引入 ⇒ D4002）；
            #   ② 静默不引入会使模块成员查不到，报出「没有定义 模块名」，
            #      同样指不到真因。
            # 循环引入在业务工程里几乎总是写错了（ERP 里极易在「服务层互相
            # 调用」时踩到），且**有确定的正确改法**：把公共部分抽成第三个模块。
            # 故此处直接报错并给出改法，两侧同口径。
            self.err(E_IMPORT_CYCLE,
                     f"模块循环引入：「{name}」在引入链上又回到了自己"
                     f"（{' → '.join(self._loading_names + [name])}）。",
                     node.span,
                     ["把两个模块都要用的部分抽成第三个模块，两边各自引入它",
                      "或把互相调用的逻辑合并到同一个模块里"])
            return True
        self._loading_modules.add(real)
        self._loading_names.append(name)

        try:
            return self._import_module_file_inner(node, name, full, real)
        finally:
            # **必须清理**：否则「同一模块被引入两次」时，第二次会被
            # 上面那行 `in self._loading_modules` 误判为循环引入，
            # 直接 `return True` 而**什么都不引入** ⇒ 报「没有定义 模块名」。
            # 实测症状：同一文件先 `引入 { 某函数 } 从 "甲"`，再
            # `引入 "甲"`，第二次静默失效。用 finally 而非各分支补齐，
            # 是因为中途还有多处 `return`（找不到文件、解析失败等）。
            self._loading_modules.discard(real)
            if self._loading_names and self._loading_names[-1] == name:
                self._loading_names.pop()

    def _import_module_file_inner(self, node: Use, name: str,
                                  full: str, real: str) -> bool:
        from .lexer import Lexer
        from .parser import Parser

        try:
            with open(full, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError:
            return False

        sub = Analyzer(self.stdlib, filename=full,
                       source_lines=text.splitlines())
        sub._loading_modules = self._loading_modules
        sub._loading_names = self._loading_names
        # **被引入模块的诊断必须回流**。此前 `sub.analyze(...)` 的返回值
        # 被直接丢掉，于是模块内部的错误（未定义变量、类型不符……）
        # **一条都不会上报**：`sy 检查 主.sy` 报「没有发现问题」，
        # 而主文件引入的模块里有个必然崩溃的错误 —— 静态检查形同虚设。
        # 多文件工程（尤其 ERP 这类 8~10 个文件的）对此最敏感。
        # 子分析器已按自身 filename / source_lines 生成诊断，故无需补标。
        sub.analyze(Parser(Lexer(text, full).tokenize(), full).parse())
        self.diagnostics.extend(sub.diagnostics)

        # 只导出**顶层**定义；下划线开头的名字视为模块私有
        exported = {n: s for n, s in sub.global_scope.symbols.items()
                    if not n.startswith("_")}
        # **模块里的类型（结构体 / 枚举）也算导出成员** —— 否则
        # `引入 "领域模型"` 之后 `领域模型.状态` 会被判为「没有该成员」。
        # 类型定义不放在作用域符号表里，故须显式并入。
        for tname in list(sub.struct_types) + list(sub.enum_types):
            if not tname.startswith("_"):
                exported.setdefault(tname, None)
        modname = os.path.splitext(os.path.basename(name))[0]

        if node.names:
            for n in node.names:
                if n in exported:
                    sym = exported[n]
                    if sym is None:
                        # **被点名导出的名字是「类型」**。
                        #
                        # 类型不放在作用域符号表里（上面用 `setdefault(n, None)`
                        # 并入了导出集），故此处 `sym` 为 None —— 早期代码直接
                        # 读 `sym.type`，于是 `引入 { 类型名 } 从 "模块"`
                        # **让宿主 Python 抛 AttributeError 崩掉**，用户看到的
                        # 是一段 Python 堆栈，而不是枢言诊断。这是「宿主崩溃」
                        # 而非「语言报错」，性质比普通缺陷更严重。
                        # 类型本身已由下面「按名引入」分支并入类型表，
                        # 这里只补一条符号，使名字在作用域里「有定义」。
                        self.global_scope.define(Symbol(
                            name=n, type=ANY, span=node.span, used=True))
                        continue
                    self.global_scope.define(Symbol(
                        name=n, type=sym.type, span=node.span, used=True,
                        is_function=sym.is_function, params=sym.params,
                        return_type=sym.return_type))
                else:
                    cands = self._suggest_from(n, list(exported))
                    hints = [f"模块「{modname}」导出："
                             f"{'、'.join(sorted(exported))}"
                             if exported else "该模块没有导出任何名字"]
                    if cands:
                        hints.insert(0, "你是不是想写：" + " / ".join(cands))
                    self.err(E_NO_ATTRIBUTE,
                             f"模块「{modname}」里没有「{n}」。",
                             node.span, hints)
        else:
            self.global_scope.define(Symbol(name=modname, type=ANY,
                                            span=node.span, used=True))
            self.imported_file_modules[modname] = set(exported)
            self.imported_file_names.add(modname)

        # 类型定义一并合并：模块里的结构体 / 枚举要能在本文件使用。
        #
        # **但合并的范围取决于引入形式**（SPEC §3.8.1）：
        #   · 按名引入（`引入 { X } 从 "模块"`）→ 类型**直接绑定**，
        #     `X(...)` / `X.成员` 都能裸用，故并入本文件；
        #   · 整模块引入（`引入 "模块"`）→ 只绑**命名空间**，
        #     必须写 `模块.X(...)` / `模块.X.成员`，**不能裸用**。
        #
        # 早期实现**不分引入形式一律并入**，于是整模块引入后裸用类型名
        # 也能通过静态检查 —— 可运行期只绑命名空间，结果必然报
        # 「没有定义」；更糟的是报错位置显示为「第 0 行，第 0 列」
        # （运行期的类型查找拿不到源码位置），用户完全无从下手。
        # 「同一语义两处实现必然分叉」：语义层宽松、运行期严格，
        # 于是**假通过**。现在两侧同口径。
        mod_types = {k: v for k, v in sub.struct_types.items()
                     if not k.startswith("_")}
        mod_enums = {k: v for k, v in sub.enum_types.items()
                     if not k.startswith("_")}
        mod_aliases = {k: v for k, v in sub.type_aliases.items()
                       if not k.startswith("_")}
        if node.names:
            # 按名引入：只并入**被点名的**类型，与「按名直接绑定」口径一致。
            picked = set(node.names)
            self.struct_types.update(
                {k: v for k, v in mod_types.items() if k in picked})
            self.enum_types.update(
                {k: v for k, v in mod_enums.items() if k in picked})
            self.type_aliases.update(
                {k: v for k, v in mod_aliases.items() if k in picked})
        else:
            # 整模块引入：**不并入**，只登记来源，供裸用时给出改写建议。
            for k in list(mod_types) + list(mod_enums) + list(mod_aliases):
                self._module_types.setdefault(k, modname)

        # **传递引入命名空间**（实测缺陷修复）。
        #
        # 若本文件用 `引入 { 某函数 } 从 "甲"`，而 `甲` 内部写了
        # `引入 "乙"`，则 `乙` 的类型已被上面几行并入本文件 —— 但
        # `乙` 这个**命名空间名**并没有绑定，于是本文件写 `乙.某枚举.成员`
        # 会报「没有定义 乙」。用户看到的是「我明明引入了，却说没定义」。
        #
        # 两种选择：① 让 `甲` 的传递引入**可见**（本实现）；② 让 `甲`
        # 内部引入**完全私有**（则调用方无法触及，更接近多数语言的模块
        # 语义）。此处选 ① 并**显式绑定命名空间**，理由是枢言的模块引入
        # 本就是「展开式」的（类型已并入），半展开半隐藏才最令人困惑。
        for inner_name, inner_exports in sub.imported_file_modules.items():
            if inner_name in self.imported_file_modules:
                self.imported_file_modules[inner_name] |= set(inner_exports)
            else:
                self.imported_file_modules[inner_name] = set(inner_exports)
                self.global_scope.define(Symbol(name=inner_name, type=ANY,
                                                span=node.span, used=True))
        return True

    def _stmt_TypeDecl(self, node: TypeDecl) -> None:
        # 字段类型校验放在第二遍（登记在第一遍），因此写在前面的结构体
        # 可以引用写在后面的类型 —— 支持相互引用。
        self.node_types[id(node)] = ANY
        if node.kind != "struct":
            return
        bad = False
        for fname, ftype, fspan in node.fields:
            if self._is_unknown_type(ftype):
                self.err(E_NO_MEMBER,
                         f"字段「{fname}」的类型「{ftype}」没有定义。",
                         fspan,
                         ["可用类型：整数 小数 文本 布尔 空 列表 映射 函数 任意",
                          f"或先声明 `类型 {ftype} = ...`"])
                bad = True
        if bad:
            return
        # 把解析后的字段类型回填（供构造与访问时逐字段校验）
        if node.name in self.struct_types:
            self.struct_types[node.name].fields = [
                (f, self._resolve_type(t)) for f, t, _ in node.fields]

    def _is_unknown_type(self, name: str) -> bool:
        """判断类型名是否指向一个不存在的类型（仅用于诊断）。"""
        if name.startswith("[") and name.endswith("]"):
            return False                              # [文本] 这类列表类型
        if name in self.struct_types or name in self.enum_types:
            return False
        if name in self.type_aliases:
            resolved = self._resolve_type(name)
            if resolved.startswith("[") and resolved.endswith("]"):
                return False
            return (resolved not in self.struct_types
                    and resolved not in self.enum_types
                    and resolved not in TYPE_ALIASES)
        return name not in TYPE_ALIASES

    def _stmt_Assert(self, node: Assert) -> None:
        if node.cond is not None:
            self._visit_expr(node.cond)
        if node.message is not None:
            self._visit_expr(node.message)

    def _stmt_Block(self, node: Block) -> None:
        self._visit_block(node.body)

    # ------------------------------------------------------------ 块

    def _visit_block(self, body: list[Node], new_scope: bool = True) -> None:
        if new_scope:
            self._push("block")
        if not body:
            return
        reachable = True
        for stmt in body:
            if not reachable:
                if isinstance(stmt, (FuncDef, TypeDecl)):
                    self._visit_stmt(stmt)
                    continue
                self.warn(W_UNREACHABLE,
                          "这段代码永远不会执行——它前面的语句已经"
                          "返回或抛出了。",
                          stmt.span if isinstance(stmt.span, Span)
                          else _span_of(stmt),
                          ["删掉这段代码，或调整前面的控制流"])
                continue
            self._visit_stmt(stmt)
            if isinstance(stmt, (Return, Throw, Break, Continue)):
                reachable = False
        if new_scope:
            self._report_unused(self.scope)
            self._pop()

    def _report_unused(self, scope: Scope) -> None:
        for sym in scope.symbols.values():
            if sym.used or sym.is_param or sym.name.startswith("_"):
                continue
            if sym.is_function:
                continue
            self.warn(W_UNUSED,
                      f"变量「{sym.name}」定义了但从未使用。",
                      sym.span,
                      [f"删除它，或改用 `_` 开头（`_` 开头的名字不会提示）"])

    def _block_has_exit(self, body: list[Node]) -> bool:
        for stmt in body:
            if isinstance(stmt, (Break, Return, Throw)):
                return True
            for sub in walk(stmt):
                if isinstance(sub, Break):
                    return True
        return False

    # ------------------------------------------------------------ 表达式

    def _visit_expr(self, node: Node | None) -> str:
        if node is None:
            return ANY
        method = getattr(self, f"_expr_{type(node).__name__}", None)
        if method is None:
            return ANY
        t = method(node)
        self.node_types[id(node)] = t
        return t

    def _expr_Literal(self, node: Literal) -> str:
        return {"number": ("整数" if isinstance(node.value, int)
                           and not isinstance(node.value, bool)
                           else "小数" if isinstance(node.value, float)
                           else ANY),
                "text": "文本", "bool": "布尔", "nil": "空"}.get(
            node.literal_kind, ANY)

    def _expr_InterpolatedString(self, node: InterpolatedString) -> str:
        for kind, payload in node.parts:
            if kind == "expr":
                self._visit_expr(payload)
        return "文本"

    def _expr_Identifier(self, node: Identifier) -> str:
        sym = self.scope.lookup(node.name)
        if sym is not None:
            sym.used = True
            return sym.type
        # 类型名（结构体 / 枚举）作为构造器与成员容器的入口。
        # **在值位置**使用类型名是用法错误，须在编译期拦下：
        # 若静默通过，运行期才报「类型不是值」，位置与原因都丢失。
        if node.name in self.struct_types or node.name in self.enum_types:
            if id(node) not in self._type_entry_uses:
                self._report_bare_type_use(node)
            return node.name
        if node.name in self.type_aliases:
            return self._resolve_type(node.name)
        if self.stdlib.has_core(node.name):
            self.global_scope.define(Symbol(
                name=node.name, type="函数", span=node.span,
                is_function=True, used=True))
            return "函数"
        self.err(E_UNDEFINED,
                 f"没有定义「{node.name}」。", node.span,
                 self._undefined_hints(node.name))
        return ANY

    def _report_bare_type_use(self, node: Identifier) -> None:
        kind = "结构体" if node.name in self.struct_types else "枚举"
        if kind == "结构体":
            hint = f"先构造成实例：`定义 某值 = {node.name}(字段 是 ...)`"
        else:
            members = self.enum_types[node.name].member_names
            hint = (f"取成员写成 `{node.name}.成员名`，"
                    f"例如 `{node.name}.{members[0]}`" if members else
                    f"写成 `{node.name}.成员名`")
        self.err(E_UNDEFINED,
                 f"「{node.name}」是一个{kind}类型，不能当作值使用。",
                 node.span, [hint, "类型名本身不是值"])

    def _undefined_hints(self, name: str) -> list[str]:
        hints = []
        # **裸用被引入模块的类型名**：这是整模块引入（命名空间绑定）下
        # 最常见的写法错误，且必须在**编译期**拦下。
        # 若放行，运行期只会报「没有定义 X」且位置是「第 0 行 0 列」，
        # 用户根本不知道要去写 `模块.X`。
        owner = self._module_types.get(name)
        if owner:
            hints.append(
                f"「{name}」是模块「{owner}」里的类型，整模块引入只绑命名空间："
                f"请写 `{owner}.{name}`，或改成 `引入 {{ {name} }} 从 \"{owner}\"`")
        cands = self._suggest(name)
        if cands:
            hints.append("你是不是想写：" + " / ".join(cands))
        hints.append("用 `定义 %s = ...` 先声明它" % name)
        return hints

    def _expr_ListLiteral(self, node: ListLiteral) -> str:
        for item in node.items:
            self._visit_expr(item)
        return "列表"

    def _expr_MapLiteral(self, node: MapLiteral) -> str:
        for k, v in node.pairs:
            self._visit_expr(k)
            self._visit_expr(v)
        return "映射"

    def _expr_Unary(self, node: Unary) -> str:
        t = self._visit_expr(node.operand)
        if node.op == "not":
            return "布尔"
        if t in ("整数", "小数", ANY):
            return t
        hints = ["该运算符需要数字"]
        # 绝大多数情况下，一元 `+` / `-` 出现在行首并非用户本意，
        # 而是**长表达式换行时把运算符留在了下一行行首**。枢言的续行规则
        # 要求运算符留在**上一行行尾**（SPEC §2.7）。这里给出可直接照做的
        # 改写建议，比只说「需要数字」有用得多。
        if node.op in ("+", "-"):
            hints.insert(0, "如果是长表达式换行，请把运算符放在上一行行尾"
                            "（枢言靠行尾运算符自动续行）；"
                            "也可以用圆括号把整个表达式括起来")
        self.err(E_TYPE_MISMATCH,
                 f"运算符「{node.op}」不能作用在「{t}」上。",
                 node.span, hints)
        return ANY

    def _expr_Binary(self, node: Binary) -> str:
        lt = self._visit_expr(node.left)
        rt = self._visit_expr(node.right)

        if node.op == "+":
            if lt == "文本" or rt == "文本":
                return "文本"
            if lt == "小数" or rt == "小数":
                return "小数"
            return "整数" if (lt == "整数" and rt == "整数") else ANY

        if node.op in ("-", "*", "/", "%", "^"):
            if lt not in ("整数", "小数", ANY) or rt not in ("整数", "小数", ANY):
                self.err(E_TYPE_MISMATCH,
                         f"运算符「{node.op}」需要两侧都是数字，"
                         f"实际左边是「{lt}」、右边是「{rt}」。",
                         node.span, ["用 `转整数(...)` 或 `转小数(...)` 转换"])
                return ANY
            if node.op == "/":
                if lt == "整数" and rt == "整数":
                    return "小数"
                return "小数"
            if node.op == "^":
                return "小数" if (lt == "小数" or rt == "小数") else "整数"
            if lt == "小数" or rt == "小数":
                return "小数"
            return "整数"

        return ANY

    def _expr_Logical(self, node: Logical) -> str:
        lt = self._visit_expr(node.left)
        rt = self._visit_expr(node.right)
        # `且`/`或` 返回操作数本身（SPEC §4.7）
        if lt == rt and lt != ANY:
            return lt
        return ANY

    def _expr_Compare(self, node: Compare) -> str:
        lt = self._visit_expr(node.left)
        rt = self._visit_expr(node.right)
        # 比较链检测（SPEC §4.1，D3010）
        if isinstance(node.left, Compare) or isinstance(node.right, Compare):
            self.err(E_COMPARE_CHAIN,
                     "枢言不允许连续比较（如 `1 < 2 < 3`），"
                     "因为它的含义容易被误读。",
                     node.span,
                     ["改写为 `1 < 2 且 2 < 3`"])
        if node.op in ("in", "not_in"):
            base = rt[:-1] if rt.startswith("[") and rt.endswith("]") else rt
            if rt not in ("列表", "文本", "映射", ANY):
                self.err(E_TYPE_MISMATCH,
                         f"「属于」需要右侧是列表、文本或映射，实际是「{rt}」。",
                         node.span, ["把右侧改成一个集合类对象"])
            return "布尔"
        if lt != ANY and rt != ANY:
            if lt == "文本" and rt == "文本":
                pass
            elif lt in ("整数", "小数") and rt in ("整数", "小数"):
                pass
            elif lt == "布尔" and rt == "布尔":
                pass
            elif lt == "空" or rt == "空":
                pass
            elif lt == "列表" and rt == "列表":
                pass
            elif lt == "映射" and rt == "映射":
                pass
            elif ("布尔" in (lt, rt)
                  and ("整数" in (lt, rt) or "小数" in (lt, rt))):
                # SPEC §4.6：`真 == 1` 是**合法比较**，结果恒为假（布尔不隐式
                # 转数值）。这是刻意的设计——它让 `0/1` 与布尔的混淆在运行期
                # 立刻显形，而不是悄悄相等。故此处不报错。
                pass
            elif lt != rt:
                self.err(E_TYPE_MISMATCH,
                         f"不能比较「{lt}」和「{rt}」——两者的类型不同。",
                         node.span,
                         ["先转换成同一类型，例如 `转文本(...)` 或 `转整数(...)`"])
        return "布尔"

    def _expr_Ternary(self, node: Ternary) -> str:
        self._visit_expr(node.cond)
        a = self._visit_expr(node.then)
        b = self._visit_expr(node.otherwise)
        return a if a == b else ANY

    def _expr_Call(self, node: Call) -> str:
        rt = self._expr_CallTarget(node)
        arg_types = [self._visit_expr(arg) for arg in node.args]
        for v in node.kwargs.values():
            self._visit_expr(v)
        self._check_builtin_arg_types(node, arg_types)
        self._check_stray_kwargs(node)
        return rt

    def _check_stray_kwargs(self, node: Call) -> None:
        """守卫：**具名参数不得被静默丢弃**。

        背景（已实测的真实缺陷）：`输出(甲 是 5)` 会被解析成一项具名参数
        `甲=5`。而 `输出` 声明为可变参数（签名 `(0, None, "空")`），
        参数个数检查因此通过，运行期又只遍历 `node.args`
        ⇒ **该调用什么都不输出也不报错**。这正是本项目最危险的失败形态。

        修复口径（与用户函数一致）：**内置函数不支持具名参数**。
        要么改用位置参数，要么说明清楚。绝不静默。
        """
        callee = node.callee
        if not node.kwargs:
            return

        # 结构体构造：具名参数是**正常写法**，由 `_check_struct_construct` 负责
        if isinstance(callee, Identifier) \
                and callee.name in self.struct_types:
            return

        # 用户定义函数：支持具名参数（运行期确实会绑定），无需拦截
        if isinstance(callee, Identifier):
            sym = self.scope.lookup(callee.name)
            if sym is not None and sym.is_function:
                return

        # 判断是否内置函数 / 内置模块成员
        is_builtin = (
            isinstance(callee, Identifier) and self.stdlib.has_core(callee.name)
        ) or (
            isinstance(callee, Member)
            and isinstance(callee.target, Identifier)
            and self.stdlib.has_module(callee.target.name)
            and callee.name in self.stdlib.modules[callee.target.name]
        )
        if not is_builtin:
            return

        display_name = callee.name
        if isinstance(callee, Member):
            display_name = f"{callee.target.name}.{callee.name}"

        first = next(iter(node.kwargs.items()))
        self.err(E_ARG_COUNT,
                 f"内置函数「{display_name}」不支持具名参数，"
                 f"参数「{first[0]}」会被忽略。",
                 first[1].span if first[1] is not None else node.span,
                 [f"改用位置参数：`{display_name}(值)`",
                  f"若本意是比较，请把 `是` 换成 `等于`："
                  f"`{display_name}(甲 等于 值)`",
                  "具名参数形式「名字 是 值」只用于**用户函数**与"
                  "**结构体构造**"])
        self.diagnostics[-1].notes.append(
            "枢言的内置函数没有参数名，因此 `输出(甲 是 5)` 里的"
            "`甲 是 5` 是一个具名参数而不是比较表达式")

    def _check_builtin_arg_types(self, node: Call,
                                 arg_types: list[str]) -> None:
        """校验内置函数的**参数类型**（编译期发现 `长度(1)` 这类错误）。"""
        spec: list[str | None] | None = None
        name = ""
        callee = node.callee
        if isinstance(callee, Identifier) and self.stdlib.has_core(callee.name):
            # 用户若定义了同名函数，则调用的是用户函数，内置约束不适用
            sym = self.scope.lookup(callee.name)
            if sym is not None and sym.is_function:
                return
            spec = CORE_PARAM_TYPES.get(callee.name)
            name = callee.name
        elif isinstance(callee, Member) and isinstance(callee.target, Identifier) \
                and self.stdlib.has_module(callee.target.name):
            key = f"{callee.target.name}.{callee.name}"
            spec = MODULE_PARAM_TYPES.get(key)
            name = key
        if not spec:
            return
        for i, want in enumerate(spec):
            if i >= len(arg_types) or want is None:
                continue
            got = arg_types[i]
            if got == ANY:
                continue
            allowed = _TYPE_GROUP.get(want)
            if allowed is None:
                allowed = frozenset({want, ANY})
            if got not in allowed:
                shown = want.replace("|", " / ")
                self.err(E_TYPE_MISMATCH,
                         f"函数「{name}」的第 {i + 1} 个参数需要「{shown}」，"
                         f"实际是「{got}」。",
                         node.args[i].span,
                         [self._convert_hint(got, want.split("|")[0])])

    def _expr_CallTarget(self, node: Call) -> str:
        callee = node.callee

        # 0) 结构体构造：`点(横 是 3, 纵 是 4)`
        if isinstance(callee, Identifier) \
                and callee.name in self.struct_types:
            self._type_entry_uses.add(id(callee))
            return self._check_struct_construct(node, callee.name)

        # 0.05) 命名空间引入的模块成员调用：`工具.平方(5)`
        #       模块里的结构体也走这里（`领域模型.造点(...)` 返回结构体）
        if isinstance(callee, Member) and isinstance(callee.target, Identifier) \
                and callee.target.name in self.imported_file_modules:
            mod = callee.target.name
            exported = self.imported_file_modules[mod]
            if callee.name not in exported:
                cands = self._suggest_from(callee.name, list(exported))
                hints = [f"模块「{mod}」导出：{'、'.join(sorted(exported))}"]
                if cands:
                    hints.insert(0, "你是不是想写：" + " / ".join(cands))
                self.err(E_NO_ATTRIBUTE,
                         f"模块「{mod}」里没有「{callee.name}」。",
                         node.span, hints)
            return ANY

        # 0.1) 对枚举类型本身的调用没有意义，明确说明
        if isinstance(callee, Identifier) \
                and callee.name in self.enum_types:
            self.err(E_NO_MEMBER,
                     f"枚举「{callee.name}」不能像函数那样调用。",
                     node.span,
                     [f"写法是 `{callee.name}.成员名`，"
                      f"例如 `{callee.name}.{self.enum_types[callee.name].member_names[0]}`"])
            return ANY

        # 0.2) **用户定义优先**：内置函数只是预先放进全局作用域的名字，
        #    用户用同名函数覆盖它是合法的（运行期亦如此），语义层必须同序，
        #    否则会出现「语义层按内置签名报参数个数、运行期却调用了用户函数」
        #    的错位。
        if isinstance(callee, Identifier):
            sym = self.scope.lookup(callee.name)
            if sym is not None and sym.is_function:
                sym.used = True
                self._check_user_arity(sym, node, node.span)
                return sym.return_type or ANY
            if sym is not None and sym.type == "函数":
                sym.used = True
                return ANY

        # 1) 内置核心函数
        if isinstance(callee, Identifier) and self.stdlib.has_core(callee.name):
            sym = self.scope.lookup(callee.name)
            if sym is not None:
                sym.used = True
            sig = CORE_SIGNATURES.get(callee.name)
            if sig is not None:
                self._check_arity(callee.name, len(node.args), sig, node.span)
            return sig[2] if sig else ANY

        # 2) 内置模块成员 `模块.函数`
        if isinstance(callee, Member) and isinstance(callee.target, Identifier):
            mod = callee.target.name
            # **成员名取源码原文**：与 `_expr_Member` 同一口径。
            # `文本.重复` 的 `重复` 是关键字（loop），用归一名 `callee.name`
            # 会查不到并报出「没有『loop』这个成员」——与成员访问路径分叉。
            key = callee.name_text or callee.name
            if self.stdlib.has_module(mod):
                members = self.stdlib.modules[mod]
                if key not in members:
                    self.err(E_NO_ATTRIBUTE,
                             f"模块「{mod}」里没有「{key}」这个成员。",
                             node.span,
                             ["可用成员：" + "、".join(sorted(members))])
                    return ANY
                sig = MODULE_SIGNATURES.get(f"{mod}.{key}")
                if sig is not None:
                    self._check_arity(f"{mod}.{key}", len(node.args),
                                      sig, node.span)
                return sig[2] if sig else ANY
            if mod in self._imported_modules:
                return ANY

        # 3) 用户函数（未在上面命中时的兜底）
        if isinstance(callee, Identifier):
            sym = self.scope.lookup(callee.name)
            if sym is not None and sym.type != "函数" and sym.type != ANY:
                self.err(E_NOT_CALLABLE,
                         f"「{callee.name}」是「{sym.type}」，不是函数，不能调用。",
                         node.span,
                         [f"检查是否把它当成函数用了（它在第 "
                          f"{sym.span.line} 行定义）"])
                return ANY
            if sym is None and not self.stdlib.has_core(callee.name):
                self.err(E_UNDEFINED,
                         f"没有定义函数「{callee.name}」。", callee.span,
                         self._undefined_hints(callee.name))
                return ANY
            if sym is None:
                return ANY

        self._visit_expr(callee)
        return ANY

    def _check_arity(self, name: str, given: int,
                     sig: tuple[int, int | None, str], span: Span) -> None:
        need, most, _ = sig
        if given < need or (most is not None and given > most):
            expected = (str(need) if most == need
                        else f"{need} 到 {most}" if most is not None
                        else f"至少 {need}")
            self.err(E_ARG_COUNT,
                     f"函数「{name}」需要 {expected} 个参数，"
                     f"但这里给了 {given} 个。",
                     span,
                     [f"调用 `{name}` 时补上/删掉多余的参数"])

    def _check_user_arity(self, sym: Symbol, node: Call, span: Span) -> None:
        """用户函数的参数个数校验。

        **必须把具名参数计入**——运行期 `_call_shuyan` 确实会绑定 kwargs，
        语义层若只看 `len(node.args)` 就会把 `问候(名字 是 "老李")`
        误判为「给了 0 个参数」。这正是「语义层与运行期不同序」的经典
        形态，必须两侧同口径。
        """
        params = sym.params
        param_names = {p.name for p in params}
        variadic = any(p.is_variadic for p in params)

        # 未知具名参数：运行期会静默忽略 ⇒ 编译期必须拦下
        for kname, kexpr in node.kwargs.items():
            if kname not in param_names:
                candidate = self._suggest_from(kname, list(param_names))
                hints = [f"该函数的参数：{'、'.join(p.name for p in params)}"]
                if candidate:
                    hints.insert(0, "你是不是想写：" + " / ".join(candidate))
                self.err(E_ARG_COUNT,
                         f"函数「{sym.name}」没有名为「{kname}」的参数。",
                         kexpr.span if kexpr is not None else span, hints)

        given = len(node.args) + len(node.kwargs)
        required = sum(1 for p in params if p.default is None
                       and not p.is_variadic)
        total = len(params)
        if given < required or (not variadic and given > total):
            expected = (f"{required}" if required == total
                        else f"{required} 到 {total}")
            self.err(E_ARG_COUNT,
                     f"函数「{sym.name}」需要 {expected} 个参数，"
                     f"但这里给了 {given} 个。",
                     span,
                     [f"函数定义在第 {sym.span.line} 行"])

    def _expr_Index(self, node: Index) -> str:
        tt = self._visit_expr(node.target)
        if node.is_slice:
            if node.start is not None:
                self._visit_expr(node.start)
            if node.stop is not None:
                self._visit_expr(node.stop)
            if tt not in ("列表", "文本", ANY):
                self.err(E_BAD_INDEX,
                         f"「{tt}」不支持切片（只有列表和文本支持）。",
                         node.span, [])
            return tt
        it = self._visit_expr(node.index)
        if tt == "文本":
            if it not in ("整数", ANY):
                self.err(E_BAD_INDEX,
                         f"文本的下标必须是整数，实际是「{it}」。",
                         node.span, ["用整数下标，例如 `名字[0]`"])
            return "文本"
        if tt == "列表":
            if it not in ("整数", ANY):
                self.err(E_BAD_INDEX,
                         f"列表的下标必须是整数，实际是「{it}」。",
                         node.span, ["用整数下标，例如 `名单[0]`"])
            return ANY
        if tt == "映射":
            return ANY
        if tt == ANY:
            return ANY
        self.err(E_BAD_INDEX,
                 f"「{tt}」不支持下标访问。", node.span,
                 ["只有列表、文本、映射支持下标"])
        return ANY

    def _expr_Member(self, node: Member) -> str:
        # 成员名一律取**源码原文**：`文本.重复` 的 `重复` 是关键字（loop），
        # 归一后（`loop`）查不到成员；枚举成员（`星期.一`）同理。
        key = node.name_text or node.name

        # 模块成员：`数学.圆周率`（模块不是作用域里的符号，须先特判）
        if isinstance(node.target, Identifier) \
                and self.stdlib.has_module(node.target.name):
            mod = node.target.name
            members = self.stdlib.modules[mod]
            if key not in members:
                self.err(E_NO_ATTRIBUTE,
                         f"模块「{mod}」里没有「{key}」。",
                         node.span,
                         ["可用成员：" + "、".join(sorted(members))])
            return ANY

        # 目标是否是「裸类型名」？`星期.三` 的 `星期`、`点.横` 的 `点`
        #
        # **先查作用域**：若存在同名局部符号（参数或变量），它就是**值**，
        # 优先于类型名。否则形如 `请 检查信用(客户, ...)` 的参数 `客户`
        # 会被误判成结构体类型名，报「请先构造成实例」—— 而用户明明
        # 传的就是实例。实测于 `projects/应收对账/账龄服务.sy`。
        target_is_value = False
        if isinstance(node.target, Identifier):
            _sym = self.scope.lookup(node.target.name)
            if _sym is not None and not _sym.is_function:
                target_is_value = True

        if isinstance(node.target, Identifier) and not target_is_value:
            tname = node.target.name
            # 以命名空间形式引入的本机模块：`工具.平方` / `领域模型.状态`
            if tname in self.imported_file_modules:
                exported = self.imported_file_modules[tname]
                if node.name in exported:
                    return ANY
                cands = self._suggest_from(node.name, list(exported))
                hints = [f"模块「{tname}」导出："
                         f"{'、'.join(sorted(exported))}"
                         if exported else "该模块没有导出任何名字"]
                if cands:
                    hints.insert(0, "你是不是想写：" + " / ".join(cands))
                self.err(E_NO_ATTRIBUTE,
                         f"模块「{tname}」里没有「{node.name}」。",
                         node.span, hints)
                return ANY
            if tname in self.enum_types:
                return self._enum_member_or_property(node, tname)
            if tname in self.struct_types:
                # 类型本身没有字段值 —— 明确报错，不静默放行（否则会拖到
                # 运行期变成「未定义标识符」，把人往错方向带）
                self.err(E_NO_FIELD,
                         f"「{tname}」是一个结构体类型，"
                         f"请先构造成实例，再访问字段。",
                         node.span,
                         [f"先写 `定义 某值 = {tname}(... )` 构造实例",
                          "类型名本身不是值"])
                return ANY
            if tname in self.type_aliases:
                resolved = self._resolve_type(tname)
                if resolved in self.enum_types:
                    return self._enum_member_or_property(node, resolved)
                if resolved in self.struct_types:
                    return self._check_struct_field_read(node, resolved)

        tt = self._resolve_type(self._visit_expr(node.target))
        if tt in self.enum_types:
            return self._enum_member_or_property(node, tt)
        if tt in self.struct_types:
            return self._check_struct_field_read(node, tt)
        if tt in ("列表", "文本"):
            # 列表/文本的方法尚未开放：明确提示，而非静默给空值
            self.err(E_NO_ATTRIBUTE,
                     f"「{tt}」没有「{node.name}」属性。",
                     node.span, [])
            return ANY
        return ANY

    def _enum_member_or_property(self, node: Member,
                                 tname: str) -> str:
        """`星期.三` 取成员；`某枚举.序号` / `.名称` 取属性。

        成员名按**源码原文**（`name_text`）匹配 —— `一` 在词法上是数词，
        归一后的 `name` 是 `"1"`，只有原文才与声明 `{ 一, 二 }` 对得上。
        """
        et = self.enum_types[tname]
        key = node.name_text or node.name
        # 属性优先：`序号`/`名称` 是所有枚举通用的只读属性
        if key in ENUM_PROPERTIES:
            return ENUM_PROPERTIES[key]
        if et.ordinal_of(key) is not None:
            self.enum_instances[id(node)] = et
            return tname
        cands = self._suggest_from(key, et.member_names + list(ENUM_PROPERTIES))
        hints = [f"可用成员：{'、'.join(et.member_names)}",
                 f"另有属性：{'、'.join(ENUM_PROPERTIES)}"]
        if cands:
            hints.insert(0, "你是不是想写：" + " / ".join(cands))
        self.err(E_NO_MEMBER,
                 f"枚举「{tname}」没有成员「{key}」。",
                 node.span, hints)
        return ANY

    # ------------------------------------------------- 结构体 / 枚举 校验

    @staticmethod
    def _suggest_from(name: str, pool: list[str]) -> list[str]:
        """在给定集合里按编辑距离找候选（最多 3 个）。

        「报错即教学」的通用件：字段名、成员名、参数名的拼写候选
        都走这一处 —— 同一语义只保留一个实现，避免各处阈值漂移。
        """
        scored = []
        for cand in pool:
            d = edit_distance(name, cand)
            if d <= 2 or d <= max(1, len(name) // 2):
                scored.append((d, cand))
        scored.sort()
        return [c for _, c in scored[:3]]

    def _check_struct_field_read(self, node: Member, tname: str) -> str:
        st = self.struct_types[tname]
        if node.name in st.field_names:
            self.struct_instances[id(node)] = st
            return st.field_type(node.name) or ANY
        # **不静默返回空**：不存在的字段必须报错（本项目的一条铁律）
        cands = self._suggest_from(node.name, st.field_names)
        hints = [f"可用字段：{'、'.join(st.field_names)}"]
        if cands:
            hints.insert(0, "你是不是想写：" + " / ".join(cands))
        self.err(E_NO_FIELD,
                 f"结构体「{tname}」没有字段「{node.name}」。",
                 node.span, hints)
        return ANY

    def _check_member_write(self, target: Member, value_type: str) -> None:
        # 枚举成员不可写
        if isinstance(target.target, Member) \
                or (isinstance(target.target, Identifier)
                    and target.target.name in self.enum_types):
            self.err(E_NO_MEMBER,
                     "枚举成员不可修改——枚举值在枢言里是常量。",
                     target.span,
                     ["枚举成员由类型定义固定，运行时不能改变",
                      "若需要可变状态，请用 `定义` 声明的变量或结构体字段"])
            return
        tt = self._resolve_type(self.node_types.get(id(target.target), ANY))
        if tt in self.struct_types:
            st = self.struct_types[tt]
            if target.name not in st.field_names:
                cands = self._suggest_from(target.name, st.field_names)
                hints = [f"可用字段：{'、'.join(st.field_names)}"]
                if cands:
                    hints.insert(0, "你是不是想写：" + " / ".join(cands))
                self.err(E_NO_FIELD,
                         f"结构体「{tt}」没有字段「{target.name}」，不能赋值。",
                         target.span, hints)
                return
            want = st.field_type(target.name) or ANY
            self._check_assign(value_type, want, target.span, target.name)

    def _check_struct_construct(self, node: Call, tname: str) -> str:
        """校验结构体构造调用：`点(横 是 3, 纵 是 4)`。

        校验四项，**全部明确报错、绝不静默补齐**：
          ① 位置参数（结构体只能用具名参数）
          ② 未知字段
          ③ 字段类型不符
          ④ 缺字段
        """
        st = self.struct_types[tname]
        field_names = st.field_names
        given: dict[str, str] = {}

        if node.args:
            self.err(E_STRUCT_FIELD,
                     f"构造结构体「{tname}」要用具名参数，不能按位置传值。",
                     node.args[0].span,
                     [f"写成 `{tname}("
                      + "、".join(f"{f} 是 值" for f in field_names[:2])
                      + ", ...)`"])
            for a in node.args:
                self._visit_expr(a)

        for fname, fexpr in node.kwargs.items():
            ftype = self._visit_expr(fexpr)
            if fname not in field_names:
                cands = self._suggest_from(fname, field_names)
                hints = [f"可用字段：{'、'.join(field_names)}"]
                if cands:
                    hints.insert(0, "你是不是想写：" + " / ".join(cands))
                self.err(E_STRUCT_FIELD,
                         f"结构体「{tname}」没有字段「{fname}」。",
                         fexpr.span, hints)
                continue
            want = st.field_type(fname) or ANY
            self._check_assign(ftype, want, fexpr.span, fname)
            given[fname] = ftype
            # 回填：解释器按字段声明顺序取值
            self.node_types[id(fexpr)] = want

        missing = [f for f in field_names if f not in given]
        if missing:
            self.err(E_STRUCT_FIELD,
                     f"构造结构体「{tname}」缺少字段：{'、'.join(missing)}。",
                     node.span,
                     ["、".join(f"{m} 是 ..." for m in missing),
                      f"全部字段：{'、'.join(field_names)}"])
        self.struct_instances[id(node)] = st
        return tname

    def _expr_RangeExpr(self, node: RangeExpr) -> str:
        self._visit_expr(node.start)
        self._visit_expr(node.stop)
        return "列表"

    def _expr_FunctionExpr(self, node: FunctionExpr) -> str:
        self._in_function += 1
        self._push("function")
        for p in node.params:
            self.scope.define(Symbol(name=p.name,
                                     type=normalized_type(p.type_ann) or ANY,
                                     span=p.span, used=True, is_param=True))
        for stmt in node.body:
            self._visit_stmt(stmt)
        self._report_unused(self.scope)
        self._pop()
        self._in_function -= 1
        return "函数"

    # ------------------------------------------------------------ 类型校验

    def _check_assign(self, value_type: str, target_type: str, span: Span,
                      name: str, is_decl: bool = False,
                      init: Node | None = None) -> None:
        value_type = self._resolve_type(value_type)
        target_type = self._resolve_type(target_type)
        if can_assign(value_type, target_type):
            return
        if value_type == ANY or target_type == ANY:
            return
        kind = "声明" if is_decl else "赋值"
        # 结构体 / 枚举：给出比通用文案更准确的原因
        if self._is_composite(value_type) or self._is_composite(target_type):
            src_comp = self._is_composite(value_type)
            dst_comp = self._is_composite(target_type)
            if src_comp and dst_comp:
                why = (f"「{value_type}」与「{target_type}」是两种不同的复合类型，"
                       f"不能相互转换")
            elif src_comp:
                why = f"「{value_type}」是复合类型，只能赋给同名类型或「任意」"
            else:
                why = f"「{target_type}」是复合类型，只接受同名类型或「任意」"
            # 把原因写进**正文**而不是只放建议里：用户第一眼就该看到为什么
            self.err(E_TYPE_MISMATCH,
                     f"不能把「{value_type}」{kind}给「{target_type}」"
                     f"变量「{name}」——{why}。",
                     span, [self._convert_hint(value_type, target_type)])
            return
        self.err(E_TYPE_MISMATCH,
                 f"不能把「{value_type}」{kind}给「{target_type}」变量「{name}」。",
                 span,
                 [self._convert_hint(value_type, target_type)])
        if init is not None:
            self.diagnostics[-1].notes.append(
                f"此处得到的类型是「{value_type}」")

    def _convert_hint(self, src: str, dst: str) -> str:
        if dst == "整数" and src in ("文本", "小数"):
            return f"改用 `整({src}值)` 转换，或把变量声明为 `{src}`"
        if dst == "小数" and src == "文本":
            return "改用 `转小数(文本)` 转换"
        if dst == "文本":
            return "改用 `转文本(值)` 转换"
        if dst == "布尔":
            return "改用 `转布尔(值)` 转换"
        if src == "小数" and dst == "整数":
            return "改用 `取整(小数)` 显式转换（小数不会自动变整数）"
        return f"把变量类型改为「{src}」，或显式转换后再赋值"


def _span_of(node: Node) -> Span:
    return getattr(node, "span", Span(0, 0, 0, 0))


@dataclass
class StructType:
    """结构体类型（SPEC §3.9.2）。`fields` 为 [(字段名, 类型), ...]，保序。"""

    name: str
    fields: list = field(default_factory=list)      # [(名, 类型)]
    span: Span = field(default_factory=lambda: Span(0, 0, 0, 0))

    @property
    def field_names(self) -> list[str]:
        return [f for f, _ in self.fields]

    def field_type(self, name: str) -> str | None:
        for f, t in self.fields:
            if f == name:
                return t
        return None


@dataclass
class EnumType:
    """枚举类型（SPEC §3.9.3）。`members` 为 [(成员名, 序号)]，序号从 1 起。"""

    name: str
    members: list = field(default_factory=list)     # [(名, 序号)]
    span: Span = field(default_factory=lambda: Span(0, 0, 0, 0))

    @property
    def member_names(self) -> list[str]:
        return [m for m, _ in self.members]

    def ordinal_of(self, name: str) -> int | None:
        for m, i in self.members:
            if m == name:
                return i
        return None


def analyze(program: Program, stdlib: StdLib | None = None,
            raise_on_error: bool = True
            ) -> tuple[dict[int, str], list[Diagnostic]]:
    analyzer = Analyzer(stdlib)
    types, diags = analyzer.analyze(program)
    if raise_on_error and any(d.severity is Severity.ERROR for d in diags):
        raise SemanticError(diags)
    return types, diags