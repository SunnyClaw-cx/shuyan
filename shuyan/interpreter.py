"""枢言语言 —— 解释执行器（SPEC §3、§6）。

执行策略：**树遍历求值**（tree-walking）。v0.1 的目标是让语义完整跑通，
而不是追求吞吐 —— 性能路线（字节码 VM → LLVM）见 ROADMAP.md。

三个实现要点：
1. **控制流用异常信号传递**（Return / Break / Continue）——
   用返回值层层判空会污染每一层的求值逻辑，异常信号更干净；
2. **闭包按引用捕获**：函数值携带定义时的环境链；
3. **异常模型分层**：枢言 `抛出` 的异常可被 `捕获`，宿主错误直接冒泡
   （否则「用户程序的异常」与「实现层的 bug」会混在一起，无法区分）。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Any

from .ast_nodes import (
    Assert, Assign, Binary, Block, Break, Call, Compare, Continue, ExprStmt,
    ForEach, FuncDef, FunctionExpr, Identifier, If, Index, InterpolatedString,
    ListLiteral, Literal, Logical, MapLiteral, Member, Node, Program, RangeExpr,
    RepeatRange, RepeatTimes, Return, Ternary, Throw, Try, TypeDecl, Unary,
    Use, VarDecl, While,
)
from .ast_nodes import Param
from .builtins import StdLib
from .diagnostics import (
    Diagnostic, RuntimeShuyanError, Severity,
)
from .errors import (
    BreakSignal, ContinueSignal, ExitSignal, ReturnSignal, ShuyanThrow,
    describe_error_value, error, make_error,
)
from .names import MODULE_NAMES
from .source import Span
from .values import (
    NIL, EnumMember, ShuyanMap, ShuyanStruct, ShuyanType,
    display, equals, is_truthy, to_text, type_name,
)

# 内置函数别名归一：白话 `说` → `输出`
BUILTIN_ALIASES = {"说": "输出", "打印": "输出"}


@dataclass
class Env:
    """运行时环境（作用域链）。"""

    parent: "Env | None" = None
    vars: dict[str, Any] = field(default_factory=dict)
    consts: set[str] = field(default_factory=set)
    is_function: bool = False

    def get(self, name: str) -> tuple[bool, Any]:
        env: Env | None = self
        while env is not None:
            if name in env.vars:
                return True, env.vars[name]
            env = env.parent
        return False, NIL

    def set(self, name: str, value: Any) -> bool:
        """赋值：沿作用域链查找已有变量；找不到则在本层新建（隐式声明）。"""
        env: Env | None = self
        while env is not None:
            if name in env.vars:
                env.vars[name] = value
                return True
            env = env.parent
        self.vars[name] = value
        return False

    def define(self, name: str, value: Any, is_const: bool = False) -> None:
        self.vars[name] = value
        if is_const:
            self.consts.add(name)

    def define_here(self, name: str, value: Any) -> None:
        """在当前层强制定义（变量声明用，避免污染外层）。"""
        self.vars[name] = value


@dataclass
class ShuyanFunction:
    name: str
    params: list
    body: list
    closure: Env
    return_type: str | None = None

    def __repr__(self) -> str:
        return f"<函数 {self.name or '匿名函数'}>"

    def __call__(self, *args):
        """允许宿主（如标准库的 排序）把它当普通可调用对象使用。"""
        raise TypeError("枢言函数必须经解释器调用")


class Interpreter:
    def __init__(self, stdlib: StdLib | None = None,
                 filename: str = "<源码>",
                 source_lines: list[str] | None = None,
                 analyzer=None):
        self.stdlib = stdlib or StdLib()
        self.filename = filename
        self.source_lines = source_lines or []
        self.analyzer = analyzer
        self.global_env = Env()
        self.env = self.global_env
        self.call_stack: list[str] = []
        self.exit_code: int | None = None
        self.steps = 0
        self.max_steps = 200_000_000        # 兜底：防死循环卡死
        self._module_cache: dict[str, ShuyanMap] = {}
        self._install_builtins()

    def _install_builtins(self) -> None:
        for name, fn in self.stdlib.core.items():
            self.global_env.vars[name] = fn
        for mod, members in self.stdlib.modules.items():
            m = ShuyanMap(dict(members))
            self.global_env.vars[mod] = m
            self._module_cache[mod] = m
        # **回调宿主注入**：高阶函数（筛选 / 映射列表 / 归约 …）需要从
        # 内置函数内部回调枢言函数。枢言函数必须经解释器执行（它要用到
        # 作用域链、调用栈、返回信号），内置函数自己做不到，故由解释器
        # 把自己的调用能力交给标准库。
        #
        # 只在**解释器存在**时注入 —— 静态检查、文档生成等场景拿不到它，
        # 那些场景下调用高阶函数会给出明确报错，而不是静默失败。
        self.stdlib.callback = self._invoke_shuyan

    def _invoke_shuyan(self, fn, args: list):
        """供标准库回调枢言函数的唯一入口。

        `_call_shuyan` 需要一个 Span 来标注调用链，而内置函数此刻手上
        没有源码位置 —— 用空 Span 占位，调用链里显示为「内置函数」，
        同样能定位到「是哪个内置函数回调进来的」。
        """
        return self._call_shuyan(fn, list(args), {}, Span(0, 0, 0, 0))

    # ------------------------------------------------------------ 入口

    def run(self, program: Program) -> int:
        try:
            for stmt in program.body:
                self._exec_stmt(stmt)
        except ExitSignal as sig:
            return sig.code
        except ShuyanThrow as exc:
            self._report_uncaught(exc)
            return 1
        except RecursionError:
            self._report_recursion()
            return 1
        return 0

    def _report_recursion(self) -> None:
        diag = Diagnostic(
            "D4002",
            "递归太深，可能是无限的递归调用（枢言默认限制 1000 层）。",
            Span(0, 0, 0, 0), Severity.ERROR,
            ["检查递归是否有终止条件",
             "若确实需要深层递归，可改用循环实现"])
        if self.call_stack:
            diag.notes.append("最近的调用链：" + " → ".join(self.call_stack[-6:]))
        self.stdlib.emit(self._render_runtime([diag], []))

    def _report_uncaught(self, exc: ShuyanThrow) -> None:
        # **渲染收敛为唯一入口**：这里曾自己写一份 `类型：信息`，
        # 与 `ShuyanThrow._describe` 各写各的，于是新增的「提示」字段
        # 只出现在一边（CLI 静默丢掉提示，实测踩到）。
        message = describe_error_value(exc.value)
        span = exc.span or Span(0, 0, 0, 0)
        diag = Diagnostic("D4001", "程序抛出了没有被捕获的异常：" + message,
                          span, Severity.ERROR,
                          ["用 `试 ... 捕获 异常名 ... 结束` 接住它，"
                           "或在抛出前处理"])
        self.stdlib.emit(self._render_runtime([diag], exc.trace))

    def _render_runtime(self, diags: list[Diagnostic],
                        trace: list[str]) -> str:
        from .diagnostics import render_all
        text = render_all(diags, "\n".join(self.source_lines))
        if trace:
            text += "\n\n调用链：\n  " + "\n  ".join(trace)
        elif self.call_stack:
            text += "\n\n调用链：\n  " + "\n  ".join(self.call_stack[-8:])
        return text

    # ------------------------------------------------------------ 语句

    def _exec_stmt(self, node: Node) -> None:
        self.steps += 1
        if self.steps > self.max_steps:
            raise error("执行超限",
                        f"程序执行步数超过 {self.max_steps}，已中止"
                        "（可能是死循环）。")
        method = getattr(self, f"_exec_{type(node).__name__}", None)
        if method is not None:
            method(node)

    def _exec_VarDecl(self, node: VarDecl) -> None:
        value = self._eval(node.init) if node.init is not None else NIL
        if node.is_global:
            self._assign_named(node.name, value)
            return
        self.env.define_here(node.name, value)
        if node.is_const:
            self.env.consts.add(node.name)

    def _exec_Assign(self, node: Assign) -> None:
        if node.op == "=":
            value = self._eval(node.value)
        else:
            current = self._eval(node.target)
            rhs = self._eval(node.value)
            value = self._apply_op(node.op[:-1], current, rhs, node.span)

        target = node.target
        if isinstance(target, Identifier):
            self._assign_named(target.name, value)
            return
        if isinstance(target, Index):
            container = self._eval(target.target)
            if target.is_slice:
                raise error("赋值错误", "枢言 v0.1 不支持切片赋值。")
            idx = self._eval(target.index)
            self._set_index(container, idx, value, node.span)
            return
        if isinstance(target, Member):
            obj = self._eval(target.target)
            self._set_member(obj, target.name, value)
            return

    def _assign_named(self, name: str, value) -> None:
        env: Env | None = self.env
        while env is not None:
            if name in env.vars and name in env.consts:
                raise error("赋值错误",
                            f"不能给常量「{name}」重新赋值。", 名字=name)
            if name in env.vars:
                env.vars[name] = value
                return
            env = env.parent
        self.global_env.vars[name] = value

    def _set_index(self, container, idx, value, span: Span) -> None:
        if isinstance(container, list):
            if isinstance(idx, bool) or not isinstance(idx, int):
                raise error("类型错误",
                            f"列表下标必须是整数，实际是「{type_name(idx)}」。")
            n = len(container)
            i = idx + n if idx < 0 else idx
            if not 0 <= i < n:
                raise error("下标越界",
                            f"列表只有 {n} 个元素，下标 {idx} 超出范围。",
                            长度=n, 下标=idx)
            container[i] = value
            return
        if isinstance(container, ShuyanMap):
            container.set(idx, value)
            return
        raise error("类型错误",
                    f"「{type_name(container)}」不支持按下标赋值。")

    def _set_member(self, obj, name: str, value) -> None:
        if isinstance(obj, ShuyanStruct):
            if name not in obj.fields:
                raise error("字段错误",
                            f"结构体「{obj.type_name}」没有字段「{name}」，"
                            f"不能赋值。可用字段："
                            f"{'、'.join(obj.fields)}。",
                            结构体=obj.type_name, 字段=name)
            obj.fields[name] = value
            return
        if isinstance(obj, EnumMember):
            raise error("类型错误",
                        "枚举成员不可修改——枚举值在枢言里是常量。",
                        枚举=obj.enum_name)
        if isinstance(obj, ShuyanMap):
            obj.set(name, value)
            return
        raise error("类型错误",
                    f"「{type_name(obj)}」不支持属性赋值。")

    def _exec_ExprStmt(self, node: ExprStmt) -> None:
        if node.expr is not None:
            self._eval(node.expr)

    def _exec_If(self, node: If) -> None:
        for cond, body in node.branches:
            if cond is None:
                self._exec_block(body)
                return
            if is_truthy(self._eval(cond)):
                self._exec_block(body)
                return

    def _exec_While(self, node: While) -> None:
        while is_truthy(self._eval(node.cond)):
            try:
                self._exec_block(node.body)
            except BreakSignal:
                break
            except ContinueSignal:
                continue
            self.steps += 1
            if self.steps > self.max_steps:
                raise error("执行超限", "循环执行步数超限，已中止。")

    def _exec_RepeatTimes(self, node: RepeatTimes) -> None:
        times = self._eval(node.times)
        if isinstance(times, bool) or not isinstance(times, int):
            if isinstance(times, float) and times.is_integer():
                times = int(times)
            else:
                raise error("类型错误",
                            f"`重复 N 次` 的 N 必须是整数，"
                            f"实际是「{type_name(times)}」。")
        if times < 0:
            raise error("参数错误", f"重复次数不能是负数（收到 {times}）。")
        for _ in range(times):
            try:
                self._exec_block(node.body)
            except BreakSignal:
                break
            except ContinueSignal:
                continue

    def _exec_RepeatRange(self, node: RepeatRange) -> None:
        lo = self._eval(node.start)
        hi = self._eval(node.stop)
        step = self._eval(node.step) if node.step is not None else 1
        for v in (lo, hi, step):
            if isinstance(v, bool) or not isinstance(v, int):
                raise error("类型错误",
                            f"`重复 从…到…` 的边界必须是整数，"
                            f"实际是「{type_name(v)}」。")
        if step == 0:
            raise error("参数错误", "循环步长不能为 0。")
        env = Env(self.env)
        prev = self.env
        self.env = env
        try:
            # 中文语义下 `从 1 到 5` **包含** 5（终点闭合），
            # 与 `范围(1, 5)`（终点不包含）刻意区分 —— 前者是自然语言写法，
            # 后者是集合构造。这一区分已在 SPEC §3.5 与 §6.2 声明。
            stop = hi + 1 if step > 0 else hi - 1
            for i in range(lo, stop, step):
                env.vars[node.var_name] = i
                try:
                    self._exec_block(node.body, new_env=False)
                except BreakSignal:
                    break
                except ContinueSignal:
                    continue
        finally:
            self.env = prev

    def _exec_ForEach(self, node: ForEach) -> None:
        seq = self._eval(node.iterable)
        if isinstance(seq, ShuyanMap):
            items = list(seq.data.keys())
        elif isinstance(seq, str):
            items = list(seq)
        elif isinstance(seq, list):
            items = list(seq)
        else:
            raise error("类型错误",
                        f"`遍历` 需要一个列表、文本或映射，"
                        f"实际是「{type_name(seq)}」。")
        env = Env(self.env)
        prev = self.env
        self.env = env
        try:
            for item in items:
                env.vars[node.var_name] = item
                try:
                    self._exec_block(node.body, new_env=False)
                except BreakSignal:
                    break
                except ContinueSignal:
                    continue
        finally:
            self.env = prev

    def _exec_FuncDef(self, node: FuncDef) -> None:
        fn = ShuyanFunction(name=node.name, params=node.params, body=node.body,
                            closure=self.env, return_type=node.return_type)
        setattr(fn, "__name__", node.name)
        setattr(fn, "__is_shuyan__", True)
        self.env.define_here(node.name, fn)

    def _exec_Return(self, node: Return) -> None:
        value = self._eval(node.value) if node.value is not None else NIL
        raise ReturnSignal(value)

    def _exec_Throw(self, node: Throw) -> None:
        value = self._eval(node.value) if node.value is not None else NIL
        raise ShuyanThrow(value, node.span, list(self.call_stack))

    def _exec_Try(self, node: Try) -> None:
        try:
            self._exec_block(node.body)
        except ShuyanThrow as exc:
            handled = False
            for var, body in node.handlers:
                env = Env(self.env)
                if var:
                    env.vars[var] = exc.value
                prev = self.env
                self.env = env
                try:
                    self._exec_block(body, new_env=False)
                finally:
                    self.env = prev
                handled = True
                break
            if not handled:
                raise
        finally:
            if node.finalizer is not None:
                self._exec_block(node.finalizer)

    def _exec_Break(self, node: Break) -> None:
        raise BreakSignal()

    def _exec_Continue(self, node: Continue) -> None:
        raise ContinueSignal()

    def _exec_Use(self, node: Use) -> None:
        if self.stdlib.has_module(node.module):
            module = self._module_cache.get(node.module)
            if module is None:
                module = ShuyanMap(dict(self.stdlib.modules[node.module]))
                self._module_cache[node.module] = module
            if node.names:
                for n in node.names:
                    if n in module.data:
                        self.env.vars[n] = module.data[n]
            return
        # 尝试按文件加载本机枢言模块
        self._load_module_file(node)

    def _load_module_file(self, node: Use) -> None:
        path = node.module
        if not path.endswith(".sy"):
            path += ".sy"
        # 与语义层 `_import_module_file` **同口径**的候选路径：
        # 先把模块名解析成文件名，再把文件名解析成路径，两处一套规则。
        candidates = []
        base = getattr(self.stdlib, "base_dir", None)
        if base:
            candidates.append(os.path.join(base, path))
        candidates.append(path)
        candidates.append(os.path.join(os.getcwd(), path))
        if os.path.isabs(path):
            candidates.insert(0, path)
        full = next((c for c in candidates if os.path.isfile(c)), None)
        if full is None:
            raise error("引入错误",
                        f"找不到模块「{node.module}」"
                        f"（既不是内置模块，也不是已存在的 .sy 文件）。")
        from .lexer import Lexer
        from .parser import Parser
        from .semantic import Analyzer
        with open(full, "r", encoding="utf-8") as f:
            text = f.read()
        tokens = Lexer(text, full).tokenize()
        program = Parser(tokens, full).parse()
        # **模块也必须做语义分析**并把结果交给子解释器：
        # 枚举的成员表、结构体的字段顺序都存放在 analyzer 上，
        # 若此处不分析，跨模块使用 `模块里的枚举.成员` 会因为成员表为空
        # 而失败 —— 这是「同一语义两处实现」的另一种形态。
        sub_analyzer = Analyzer(self.stdlib)
        sub_analyzer.analyze(program)
        sub = Interpreter(self.stdlib, full, text.splitlines(),
                          analyzer=sub_analyzer)
        sub.global_env = Env()
        sub.env = sub.global_env          # env 必须跟着走，否则语句写进旧环境
        sub._install_builtins()
        # 记录「标准库基线」——模块自己定义的名字才对外导出，
        # 否则模块会连带把 `输出`、`数学` 等一堆内置名一并暴露出去。
        baseline = set(sub.global_env.vars)
        for stmt in program.body:
            sub._exec_stmt(stmt)

        # 导出规则与语义层**完全一致**（两处一套规则，避免错位）：
        #   整模块引入 → 绑定命名空间，用 `工具.成员`
        #   按名引入   → 直接绑定该名字
        exports = {
            n: v for n, v in sub.global_env.vars.items()
            if n not in baseline and not n.startswith("_")
        }
        modname = os.path.splitext(os.path.basename(node.module))[0]
        if node.names:
            for n in node.names:
                if n in exports:
                    self.env.vars[n] = exports[n]
        else:
            # 命名空间里的成员用**原文键**（不带反引号）：
            # `领域模型.状态` 能取到 `状态` 这个类型对象。
            # 反引号形式（`` 领域模型.`处理中` ``）由 `_eval_Member` 兜底处理。
            ns = ShuyanMap(exports)
            self.env.vars[modname] = ns
            self._module_cache[modname] = ns

    def _exec_TypeDecl(self, node: TypeDecl) -> None:
        # 结构体与枚举在运行期登记为**类型对象**，使
        # `点(横 是 3, 纵 是 4)` 与 `星期.三` 有可求值的目标。
        # 别名不产生值（它只是编译期的重命名）。
        if node.kind == "struct" or node.kind == "enum":
            self.env.vars[node.name] = ShuyanType(
                name=node.name, kind=node.kind)

    def _lookup_type(self, name: str):
        """按名找类型对象（结构体 / 枚举），遵守**遮蔽**语义。

        若内层已用同名变量遮蔽了类型名，则返回 None —— 否则
        `定义 点 = 点(...)` 之后的 `点.横` 会错误地回退到类型对象。
        """
        env: Env | None = self.env
        while env is not None:
            if name in env.vars:
                v = env.vars[name]
                return v if isinstance(v, ShuyanType) else None
            env = env.parent
        return None

    def _exec_Assert(self, node: Assert) -> None:
        if not is_truthy(self._eval(node.cond)):
            message = "断言失败"
            if node.message is not None:
                message = display(self._eval(node.message))
            raise ShuyanThrow(make_error("断言失败", message), node.span,
                              list(self.call_stack))

    def _exec_Block(self, node: Block) -> None:
        self._exec_block(node.body)

    def _exec_block(self, body: list[Node], new_env: bool = True) -> None:
        if new_env:
            prev = self.env
            self.env = Env(prev)
            try:
                for stmt in body:
                    self._exec_stmt(stmt)
            finally:
                self.env = prev
        else:
            for stmt in body:
                self._exec_stmt(stmt)

    # ------------------------------------------------------------ 表达式

    def _eval(self, node: Node | None):
        if node is None:
            return NIL
        method = getattr(self, f"_eval_{type(node).__name__}", None)
        if method is None:
            return NIL
        return method(node)

    def _eval_Literal(self, node: Literal):
        return node.value

    def _eval_InterpolatedString(self, node: InterpolatedString) -> str:
        out = []
        for kind, payload in node.parts:
            if kind == "text":
                out.append(payload)
            else:
                out.append(display(self._eval(payload)))
        return "".join(out)

    def _eval_Identifier(self, node: Identifier):
        found, value = self.env.get(node.name)
        if found:
            # 类型对象（结构体 / 枚举）只能作为构造器或成员容器使用，
            # 直接当值使用（`输出(点)`）是用法错误，明确报错而非打印内部对象。
            if isinstance(value, ShuyanType):
                raise error("用法错误",
                            f"「{node.name}」是一个"
                            f"{'结构体' if value.kind == 'struct' else '枚举'}"
                            f"类型，不是值。"
                            f"{'请先构造成实例，例如 `%s(字段 是 ...)`' % node.name if value.kind == 'struct' else '枚举用 `%s.成员名` 取成员' % node.name}",
                            node.span,
                            类型=node.name)
            return value
        if self.stdlib.has_core(node.name):
            return self.stdlib.get_core(node.name)
        raise error("未定义", f"没有定义「{node.name}」。", 名字=node.name)

    def _eval_ListLiteral(self, node: ListLiteral) -> list:
        return [self._eval(item) for item in node.items]

    def _eval_MapLiteral(self, node: MapLiteral) -> ShuyanMap:
        out = ShuyanMap()
        for k, v in node.pairs:
            out.set(self._eval(k), self._eval(v))
        return out

    def _eval_Unary(self, node: Unary):
        value = self._eval(node.operand)
        if node.op == "not":
            return not is_truthy(value)
        if node.op == "+":
            return value
        if node.op == "-":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise error("类型错误",
                            f"取负号需要数字，实际是「{type_name(value)}」。")
            return -value
        return NIL

    def _eval_Binary(self, node: Binary):
        op = node.op
        left = self._eval(node.left)
        right = self._eval(node.right)
        if op == "+":
            return self._add(left, right)
        return self._apply_op(op, left, right, node.span)

    def _add(self, left, right):
        """加法与拼接的统一实现——`+`、`+=` 必须同口径。

        早期版本把 `+` 的文本拼接逻辑只写在二元求值里，导致 `+=` 走
        `_apply_op` 时落空返回 `空`。这类「同一语义两处实现」的分歧
        是最隐蔽的一类缺陷，故此处收敛为唯一入口。
        """
        if isinstance(left, str) or isinstance(right, str):
            return to_text(left) + to_text(right)
        if isinstance(left, bool) or isinstance(right, bool):
            raise error("类型错误",
                        "布尔值不能参与加法运算。",
                        左侧=type_name(left), 右侧=type_name(right))
        self._want_number(left, "+")
        self._want_number(right, "+")
        return left + right

    def _want_number(self, value, op: str):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise error("类型错误",
                        f"运算符「{op}」需要数字，"
                        f"实际是「{type_name(value)}」。")
        return value

    def _apply_op(self, op: str, left, right, span: Span):
        if op == "+":
            return self._add(left, right)
        if op in ("-", "*", "/", "%", "^"):
            self._want_number(left, op)
            self._want_number(right, op)
        if op == "-":
            return left - right
        if op == "*":
            return left * right
        if op == "/":
            if right == 0:
                raise error("除以零", "除数不能是 0。", 运算符="/")
            return left / right
        if op == "%":
            if right == 0:
                raise error("除以零", "取余的除数不能是 0。", 运算符="%")
            return left % right
        if op == "^":
            # **收敛为唯一入口**：`^` 与核心函数 `次方` 是同一语义，
            # 必须共用实现。此前这里另写了一份幂运算，于是
            # 「超大整数的量级防护」只加在了 `次方` 上，`^` 依旧会崩 ——
            # 这正是项目铁律「同一语义两处实现必然分叉」的又一实例。
            return self.stdlib._power(left, right)
        return NIL

    def _eval_Logical(self, node: Logical):
        left = self._eval(node.left)
        truth = is_truthy(left)
        if node.op == "and":
            return self._eval(node.right) if truth else left
        return left if truth else self._eval(node.right)

    def _eval_Compare(self, node: Compare) -> bool:
        left = self._eval(node.left)
        right = self._eval(node.right)
        op = node.op

        if op == "in":
            return self._contains(right, left)
        if op == "not_in":
            return not self._contains(right, left)
        if op == "==":
            return equals(left, right)
        if op == "!=":
            return not equals(left, right)

        if isinstance(left, bool) or isinstance(right, bool):
            raise error("类型错误", "布尔值不能做大小比较。")
        if not isinstance(left, (int, float, str)) \
                or not isinstance(right, (int, float, str)):
            raise error("类型错误",
                        f"「{type_name(left)}」和「{type_name(right)}」"
                        "不能做大小比较。")
        if isinstance(left, str) != isinstance(right, str):
            raise error("类型错误", "文本和数字不能做大小比较。")
        try:
            if op == "<":
                return left < right
            if op == ">":
                return left > right
            if op == "<=":
                return left <= right
            if op == ">=":
                return left >= right
        except TypeError:
            raise error("类型错误",
                        f"「{type_name(left)}」和「{type_name(right)}」"
                        "不能做大小比较。")
        return False

    def _contains(self, container, item) -> bool:
        if isinstance(container, ShuyanMap):
            return item in container.data
        if isinstance(container, str):
            return to_text(item) in container
        if isinstance(container, list):
            return any(equals(item, x) for x in container)
        raise error("类型错误",
                    f"「属于」的右侧需要是列表、文本或映射，"
                    f"实际是「{type_name(container)}」。")

    def _eval_Ternary(self, node: Ternary):
        return self._eval(node.then) if is_truthy(self._eval(node.cond)) \
            else self._eval(node.otherwise)

    def _eval_Index(self, node: Index):
        target = self._eval(node.target)
        if node.is_slice:
            start = self._eval(node.start) if node.start is not None else None
            stop = self._eval(node.stop) if node.stop is not None else None
            if isinstance(target, (list, str)):
                return target[start:stop]
            raise error("类型错误",
                        f"「{type_name(target)}」不支持切片。")
        idx = self._eval(node.index)
        if isinstance(target, list):
            if isinstance(idx, bool) or not isinstance(idx, int):
                raise error("类型错误",
                            f"列表下标必须是整数，实际是「{type_name(idx)}」。")
            n = len(target)
            i = idx + n if idx < 0 else idx
            if not 0 <= i < n:
                raise error("下标越界",
                            f"列表只有 {n} 个元素，下标 {idx} 超出范围。",
                            长度=n, 下标=idx)
            return target[i]
        if isinstance(target, str):
            if isinstance(idx, bool) or not isinstance(idx, int):
                raise error("类型错误",
                            f"文本下标必须是整数，实际是「{type_name(idx)}」。")
            n = len(target)
            i = idx + n if idx < 0 else idx
            if not 0 <= i < n:
                raise error("下标越界",
                            f"文本只有 {n} 个字符，下标 {idx} 超出范围。",
                            长度=n, 下标=idx)
            return target[i]
        if isinstance(target, ShuyanMap):
            return target.get(idx, NIL)
        raise error("类型错误",
                    f"「{type_name(target)}」不支持下标访问。")

    def _eval_Member(self, node: Member):
        # **成员名一律取源码原文**：`文本.重复` 的 `重复` 在词法上是关键字
        # （`loop`），归一后查不到成员、会静默返回空。
        # 枚举成员（`星期.一`）同理。故此处统一走原文。
        key = node.name_text or node.name

        # **裸类型名优先**：`星期.三` 的 `星期` 不应走普通标识符求值
        # （那会报「类型不是值」）。类型名按**名字**解析，
        # 与变量作用域无关 —— 这与语义层同口径。
        if isinstance(node.target, Identifier):
            t = self._lookup_type(node.target.name)
            if t is not None:
                return self._eval_type_member(t, node)

        target = self._eval(node.target)

        # ---- 类型对象：`某表达式的类型` 上取成员 ----
        if isinstance(target, ShuyanType):
            return self._eval_type_member(target, node)

        # ---- 结构体字段 ----
        if isinstance(target, ShuyanStruct):
            if key in target.fields:
                return target.fields[key]
            # **不静默返回空**：不存在的字段必须报错
            raise error(
                "字段错误",
                f"结构体「{target.type_name}」没有字段「{key}」。"
                f"可用字段：{'、'.join(target.fields)}。",
                结构体=target.type_name, 字段=key)

        if isinstance(target, EnumMember):
            return self._eval_enum_property(target, node)

        if isinstance(target, ShuyanMap):
            if key in target.data:
                return target.data[key]
            # 内置模块 / 已引入模块的命名空间：成员不存在是**错误**，
            # 不能静默给空 —— 否则 `文本.重复` 会变成「调用空」，
            # 报错位置离根因十万八千里。
            if self._is_module_namespace(node.target):
                raise error(
                    "成员错误",
                    f"模块里没有「{key}」这个成员。"
                    f"可用成员：{'、'.join(sorted(map(str, target.data)))}。",
                    成员=key)
            return NIL
        if isinstance(target, (list, str)):
            # 列表/文本的“方法”尚不开放，给出明确提示而非静默空值
            raise error("属性错误",
                        f"「{type_name(target)}」没有「{key}」属性。",
                        属性=key,
                        提示=self._shadow_hint(node.target, key))
        raise error("属性错误",
                    f"「{type_name(target)}」没有「{key}」属性。",
                    提示=self._shadow_hint(node.target, key))

    def _shadow_hint(self, target: Node, key: str) -> str | None:
        """成员访问失败时，判断是不是**局部名字遮蔽了内置模块**。

        实测场景（`projects/应收对账/报表.sy`）：
            引入 "文本"
            请 左对齐(文本, 宽度)          // 形参 `文本` 遮蔽了模块 `文本`
                返回 文本.填充(...)        // 这里的 `文本` 是那个字符串参数
            结束
        用户拿到的报错是「「文本」没有「填充」属性」，而文本模块里明明有
        `填充` —— 报错把人往错方向带。这里补一句「你本地有个叫 X 的东西
        把模块挡住了」，让根因可见。
        """
        if not isinstance(target, Identifier):
            return None
        name = target.name
        # 只在「内置模块同名」时提示 —— 否则会对着普通字段错误乱猜。
        if name not in MODULE_NAMES:
            return None
        # 再收一道：该内置模块**确实有**这个名字的成员，才说明用户
        # 本意是访问模块成员，只是被同名局部名字挡住了。
        if key not in self.stdlib.modules.get(name, ()):
            return None
        holder = self.env.get(name)
        if not holder[0] or isinstance(holder[1], ShuyanMap):
            return None
        return (f"注意：当前作用域里有个变量 / 参数也叫「{name}」，"
                f"它把内置模块「{name}」挡住了 —— `{name}.{key}` "
                f"取到的是那个变量，不是模块。给它换个名字即可。")

    def _is_module_namespace(self, target: Node) -> bool:
        """判断成员访问的目标是否是一个**模块命名空间**。"""
        if not isinstance(target, Identifier):
            return False
        name = target.name
        if self.stdlib.has_module(name):
            return True
        return name in self._module_cache

    def _eval_type_member(self, t: ShuyanType, node: Member):
        """求值类型对象上的成员访问。"""
        sp = self._span_for(node)
        if t.kind == "enum":
            key = node.name_text or node.name
            if key in ("序号", "名称"):
                raise error("属性错误",
                            f"「序号」和「名称」是枚举**值**的属性，"
                            f"请先取到成员再访问，"
                            f"例如 `{t.name}.某成员.序号`。",
                            枚举=t.name)
            members = self._enum_members(t.name)
            if key in members:
                return EnumMember(enum_name=t.name, member_name=key,
                                  ordinal=members.index(key) + 1)
            raise error(
                "成员错误",
                f"枚举「{t.name}」没有成员「{key}」。"
                f"可用成员：{'、'.join(members)}。",
                枚举=t.name, 成员=key)
        # 结构体：类型本身不是值
        raise error("类型错误",
                    f"「{t.name}」是一个结构体类型，"
                    f"请先构造成实例再访问字段。",
                    类型=t.name)

    def _eval_enum_property(self, m: EnumMember, node: Member) -> Any:
        key = node.name_text or node.name
        if key == "序号":
            return m.ordinal
        if key == "名称":
            return m.member_name
        members = self._enum_members(m.enum_name)
        raise error("属性错误",
                    f"枚举值「{m}」没有属性「{key}」。"
                    f"可用属性：序号、名称。",
                    枚举=m.enum_name, 属性=key)

    def _enum_members(self, tname: str) -> list[str]:
        """取枚举的成员名序列（按源码原文）。

        优先取自语义分析器的注册表（编译期已解析），
        因此解释器与语义层共用**同一份**成员表 —— 两处各存一份必然分叉。
        """
        if self.analyzer is not None:
            et = self.analyzer.enum_types.get(tname)
            if et is not None:
                return et.member_names
        return []

    @staticmethod
    def _span_for(node: Node) -> Span:
        return node.span if isinstance(node.span, Span) else Span(0, 0, 0, 0)

    def _eval_RangeExpr(self, node: RangeExpr) -> list:
        lo = self._eval(node.start)
        hi = self._eval(node.stop)
        for v in (lo, hi):
            if isinstance(v, bool) or not isinstance(v, int):
                raise error("类型错误",
                            f"区间 `..` 的两端必须是整数，"
                            f"实际是「{type_name(v)}」。")
        if hi < lo:
            return list(range(lo, hi - 1, -1))
        return list(range(lo, hi + 1))

    def _eval_FunctionExpr(self, node: FunctionExpr) -> ShuyanFunction:
        fn = ShuyanFunction(name=node.name, params=node.params, body=node.body,
                            closure=self.env)
        setattr(fn, "__name__", node.name or "匿名函数")
        setattr(fn, "__is_shuyan__", True)
        return fn

    def _eval_Call(self, node: Call):
        # **用户定义优先**：内置函数只是预先放进全局作用域的名字。
        # 若用户在作用域里定义（或引入）了同名函数，必须调用用户的那一个，
        # 否则 `请 求和(...)` 会被静默替换成内置 `求和`。
        if isinstance(node.callee, Identifier):
            found, value = self.env.get(node.callee.name)
            # 结构体构造：`点(横 是 3, 纵 是 4)`
            if not found or not isinstance(value, ShuyanFunction):
                t = self._lookup_type(node.callee.name)
                if t is not None and t.kind == "struct":
                    return self._construct_struct(t, node)
            if found and isinstance(value, ShuyanFunction):
                args = [self._eval(a) for a in node.args]
                kwargs = {k: self._eval(v) for k, v in node.kwargs.items()}
                return self._call_shuyan(value, args, kwargs, node.span)
            if not found and self.stdlib.has_core(node.callee.name):
                fn = self.stdlib.get_core(node.callee.name)
                args = [self._eval(a) for a in node.args]
                for v in node.kwargs.values():
                    args.append(self._eval(v))
                return self._call_host(fn, args, node.callee.name)
            if found and callable(value) and not isinstance(value, ShuyanMap):
                args = [self._eval(a) for a in node.args]
                return self._call_host(value, args, node.callee.name)

        callee = self._eval(node.callee)
        # 模块命名空间里的成员：`工具.平方(5)`。
        # 模块里定义的结构体以**类型对象**形式存在于命名空间 Map 中
        # （由 `_exec_TypeDecl` 放进子解释器的环境，随导出带出来），
        # 因此这里把它当作构造器使用。
        if isinstance(callee, ShuyanType):
            if callee.kind == "struct":
                return self._construct_struct(callee, node)
            raise error("类型错误",
                        f"枚举「{callee.name}」不能像函数那样调用，"
                        f"写法是 `{callee.name}.成员名`。")
        if isinstance(callee, ShuyanMap) and isinstance(node.callee, Member):
            member = callee.data.get(node.callee.name)
            if isinstance(member, ShuyanType):
                if member.kind == "struct":
                    return self._construct_struct(member, node)
                raise error("类型错误",
                            f"枚举「{member.name}」不能像函数那样调用，"
                            f"写法是 `{member.name}.成员名`。")

        args = [self._eval(a) for a in node.args]
        kwargs = {k: self._eval(v) for k, v in node.kwargs.items()}

        if isinstance(callee, ShuyanFunction):
            return self._call_shuyan(callee, args, kwargs, node.span)
        if callable(callee):
            return self._call_host(callee, args, node.callee_name())
        raise error("调用错误",
                    f"「{type_name(callee)}」不是函数，不能调用。")

    def _construct_struct(self, t: ShuyanType, node: Call) -> ShuyanStruct:
        """构造结构体实例（SPEC §3.9.2）。

        字段**按声明顺序**填充 —— 因此打印与相等比较都稳定可预测。
        编译期已做过完整校验；此处仍做一道运行期守卫：
        **不存在的字段绝不静默吸收**（那是本项目最危险的失败形态）。
        """
        st = None
        if self.analyzer is not None:
            st = self.analyzer.struct_types.get(t.name)
        order = st.field_names if st is not None else list(node.kwargs)

        if node.args:
            raise error("构造错误",
                        f"结构体「{t.name}」要用具名参数构造，"
                        f"不能按位置传值。",
                        类型=t.name)

        fields: dict = {}
        # **按声明顺序**插入 —— 打印与相等比较才稳定可预测。
        # 若按 `node.kwargs` 的顺序插入，`点(纵 是 4, 横 是 3)` 会打印成
        # `点{纵: 4, 横: 3}`，同一类型出现两种表示，是实打实的不一致。
        for name in order:
            if name in node.kwargs:
                fields[name] = self._eval(node.kwargs[name])
        for name, expr in node.kwargs.items():
            if name not in fields:
                if st is not None and name not in order:
                    raise error("构造错误",
                                f"结构体「{t.name}」没有字段「{name}」。"
                                f"可用字段：{'、'.join(order)}。",
                                类型=t.name, 字段=name)
                fields[name] = self._eval(expr)

        if st is not None:
            missing = [f for f in order if f not in fields]
            if missing:
                raise error("构造错误",
                            f"构造结构体「{t.name}」缺少字段："
                            f"{'、'.join(missing)}。",
                            类型=t.name, 缺少="、".join(missing))
        return ShuyanStruct(type_name=t.name, fields=fields)

    def _call_host(self, fn, args: list, name: str):
        try:
            return fn(*args)
        except ShuyanThrow:
            raise
        except TypeError as exc:
            raise error("参数错误",
                        f"调用「{name}」时参数不正确：{exc}", 函数=name)

    def _call_shuyan(self, fn: ShuyanFunction, args: list, kwargs: dict,
                     span: Span):
        env = Env(fn.closure)
        env.is_function = True

        variadic_param = None
        positional = list(fn.params)
        for p in fn.params:
            if p.is_variadic:
                variadic_param = p
                break

        required = [p for p in fn.params if p.default is None and not p.is_variadic]
        bound = len(args) + len(kwargs)
        if bound < len(required):
            raise error("参数错误",
                        f"函数「{fn.name}」至少需要 {len(required)} 个参数，"
                        f"实际给了 {bound} 个。", 函数=fn.name)
        max_pos = len([p for p in fn.params if not p.is_variadic])
        if variadic_param is None and bound > max_pos:
            raise error("参数错误",
                        f"函数「{fn.name}」最多接受 {max_pos} 个参数，"
                        f"实际给了 {bound} 个。", 函数=fn.name)
        # 未知具名参数：**绝不静默忽略**（本项目最危险的失败形态）。
        # 语义层已拦一次；此处是运行期的同口径守卫，两侧必须一致。
        param_names = {p.name for p in fn.params}
        for kname in kwargs:
            if kname not in param_names:
                raise error("参数错误",
                            f"函数「{fn.name}」没有名为「{kname}」的参数。"
                            f"可用参数：{'、'.join(p.name for p in fn.params)}。",
                            函数=fn.name, 参数=kname)

        for i, p in enumerate(fn.params):
            if p.is_variadic:
                env.vars[p.name] = args[i:]
                break
            if i < len(args):
                env.vars[p.name] = args[i]
            elif p.name in kwargs:
                env.vars[p.name] = kwargs[p.name]
            elif p.default is not None:
                env.vars[p.name] = self._eval_in_env(p.default, env)
            else:
                raise error("参数错误",
                            f"函数「{fn.name}」缺少参数「{p.name}」。",
                            函数=fn.name, 参数=p.name)

        prev = self.env
        self.env = env
        self.call_stack.append(f"{fn.name}(...)" + f"  ← 第 {span.line} 行")
        try:
            for stmt in fn.body:
                self._exec_stmt(stmt)
        except ReturnSignal as sig:
            return sig.value
        except RecursionError:
            raise
        finally:
            self.env = prev
            if self.call_stack:
                self.call_stack.pop()
        return NIL

    def _eval_in_env(self, node: Node, env: Env):
        """在指定环境下求值（用于默认参数，保证默认值只依赖外层作用域）。"""
        prev = self.env
        self.env = env
        try:
            return self._eval(node)
        finally:
            self.env = prev

    # ------------------------------------------------------------ 工具

    def collect_globals(self) -> dict:
        return dict(self.global_env.vars)


def _callee_name_method(self):
    return _callee_name(self)


def _callee_name(node: Call) -> str:
    callee = node.callee
    if isinstance(callee, Identifier):
        return callee.name
    if isinstance(callee, Member):
        return callee.name
    return "匿名函数"


Call.callee_name = _callee_name     # type: ignore[attr-defined]