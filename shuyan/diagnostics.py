"""枢言语言 —— 诊断系统。

设计原则（对应 SPEC §8）：
1. 错误信息必须**说清哪一行、什么错、怎么改**；
2. 一次编译报告**全部**错误，不遇错即停；
3. 必须带源码摘录与显示列对齐的下划线（汉字算 2 列）。

一个常见的工程教训：**报错文案说错原因，就是在把人往错误方向带**。
因此本模块刻意区分「错误级别」之外的「类别」——用户输入问题、
语言用法问题、环境问题；提示语按类别给，不混用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable

from .source import Span, display_width


class Severity(str, Enum):
    ERROR = "错误"
    WARNING = "警告"
    NOTE = "提示"


# 诊断码 → 人类可读的类别名。
# 编号约定：D1xxx 词法 / D2xxx 语法 / D3xxx 语义 / D4xxx 运行时；
# W4xxx 为警告（**不是运行时错误**——早期版本从码号第二位推断类别，
# 导致 W4204 被误标为「运行时错误」，这是会误导用户的缺陷，已修正）。
_CODE_CATEGORY: dict[str, str] = {
    "1": "词法错误",
    "2": "语法错误",
    "3": "语义错误",
    "4": "运行时错误",
}

_WARNING_CATEGORIES: dict[str, str] = {
    "W4": "代码提醒",
    "W": "代码提醒",
}


@dataclass
class Diagnostic:
    code: str
    message: str
    span: Span
    severity: Severity = Severity.ERROR
    hints: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # 诊断所属**源文件**。跨文件工程里必须带上 —— 否则「被引入模块内部
    # 的错误」无法说明自己在哪个文件，用户会拿主文件的正确源码去对照，
    # 越看越困惑。空字符串表示「当前文件」（单文件场景）。
    file: str = ""
    # 该诊断**自己所在文件**的源码行。让诊断自包含渲染所需的一切：
    # 子模块的错误行号是相对子模块的，用主文件源码去画下划线会指错位置。
    # 共享同一个 list 对象，内存开销可忽略。
    source_lines: list[str] | None = None

    @property
    def category(self) -> str:
        if not self.code:
            return "诊断"
        # 警告码一律称为「代码提醒」——绝不叫「错误」。
        # 报错文案说错类别，等同于把人往错方向带。
        if self.severity is Severity.WARNING or self.code.startswith("W"):
            return "代码提醒"
        if len(self.code) >= 2:
            return _CODE_CATEGORY.get(self.code[1], "诊断")
        return "诊断"


class ShuyanError(Exception):
    """所有枢言编译期/运行期错误的基类。

    同时承载「单条诊断」与「诊断集合」两种形态：
    - 词法/语法层通常抛出单条；
    - 语义层聚合后抛出集合，供 CLI 一次性渲染。
    """

    def __init__(self, diagnostics: Iterable[Diagnostic] | Diagnostic):
        if isinstance(diagnostics, Diagnostic):
            self.diagnostics: list[Diagnostic] = [diagnostics]
        else:
            self.diagnostics = list(diagnostics)
        first = self.diagnostics[0] if self.diagnostics else None
        super().__init__(first.message if first else "枢言错误")

    @property
    def primary(self) -> Diagnostic | None:
        return self.diagnostics[0] if self.diagnostics else None


class LexError(ShuyanError):
    """词法阶段错误。"""


class ParseError(ShuyanError):
    """语法阶段错误。"""


class SemanticError(ShuyanError):
    """语义阶段错误（可聚合多条）。"""


class RuntimeShuyanError(ShuyanError):
    """运行期未捕获异常，携带枢言栈轨迹。"""

    def __init__(self, diagnostics: Iterable[Diagnostic] | Diagnostic,
                 trace: list[str] | None = None):
        super().__init__(diagnostics)
        self.trace: list[str] = trace or []


MAX_REPORTED = 50


def render(diag: Diagnostic, source_lines: list[str],
           show_hints: bool = True, main_file: str = "") -> str:
    """把单条诊断渲染成中文报告块。

    source_lines 为源码按行切分的结果（0 起索引，即 source_lines[line-1]）。
    **若诊断自带 source_lines，优先用它** —— 跨文件工程里，一条来自被引入
    模块的诊断，行号是相对那个模块的；用主文件源码去画下划线会指错位置。
    main_file 为当前被检查的主文件；诊断来自**别的文件**时才加文件名前缀，
    单文件场景不加，避免噪音。
    """
    if diag.source_lines is not None:
        source_lines = diag.source_lines
    loc = ""
    if diag.file and main_file and diag.file != main_file:
        loc = f"{diag.file} · "
    head = (f"{loc}第 {diag.span.line} 行，第 {diag.span.col} 列："
            f"{diag.category}（{diag.code}）")
    lines = [f"{head}"]
    lines.append(f"  {diag.message}")

    src = ""
    if 1 <= diag.span.line <= len(source_lines):
        src = source_lines[diag.span.line - 1]

    if src.strip():
        gutter = str(diag.span.line)
        pad = " " * len(gutter)
        lines.append("")
        lines.append(f"  {gutter} | {src.rstrip()}")
        width = display_width(src)
        start = max(0, diag.span.col - 1)
        end = diag.span.end_col - 1
        if diag.span.end_line != diag.span.line or end <= start:
            end = max(end, start + 1)
        length = max(1, min(end, width) - start)
        lines.append(f"  {pad} | {' ' * start}{'^' * length}")

    for note in diag.notes:
        lines.append(f"  · {note}")

    if show_hints:
        for hint in diag.hints:
            lines.append(f"  → 建议：{hint}")

    return "\n".join(lines)


def render_all(diagnostics: list[Diagnostic], source_text: str,
               main_file: str = "", source_line_map: dict | None = None) -> str:
    """渲染全部诊断。超过上限时聚合汇总，避免刷屏。

    main_file 用于判断「诊断是否来自别的文件」；source_line_map 可给出
    {文件名: 行列表}，供不带 source_lines 的诊断按 file 取源码。
    """
    source_lines = source_text.splitlines()
    shown = diagnostics[:MAX_REPORTED]

    def _lines_for(d: Diagnostic) -> list[str]:
        if d.source_lines is not None:
            return d.source_lines
        if d.file and source_line_map and d.file in source_line_map:
            return source_line_map[d.file]
        return source_lines

    blocks = [render(d, _lines_for(d), main_file=main_file) for d in shown]

    errors = sum(1 for d in diagnostics if d.severity is Severity.ERROR)
    warnings = sum(1 for d in diagnostics if d.severity is Severity.WARNING)

    blocks.append("")
    summary = f"共 {errors} 个错误"
    if warnings:
        summary += f"、{warnings} 个警告"
    if len(diagnostics) > MAX_REPORTED:
        summary += f"（另有 {len(diagnostics) - MAX_REPORTED} 条未显示）"
    blocks.append(summary)
    return "\n".join(blocks)


E_UNCLOSED_STRING = "D1002"
E_UNCLOSED_COMMENT = "D1003"
E_UNKNOWN_CHAR = "D1001"
E_AMBIGUOUS_PUNCT = "D1004"
E_BAD_NUMBER = "D1005"
E_IDENT_STARTS_DIGIT = "D1006"
E_INTERP_UNCLOSED = "D1007"

E_UNEXPECTED_TOKEN = "D2001"
E_EXPECTED_TOKEN = "D2002"
E_EXPECTED_EXPRESSION = "D2003"
E_UNCLOSED_BLOCK = "D2009"
E_STRAY_END = "D2010"
E_BAD_ASSIGN_TARGET = "D2011"
# 表达式语句的值算完就丢，不产生任何效果（行首运算符续写会落到这里）
E_DISCARDED_EXPR = "D2012"
# 嵌套太深：递归下降解析器会撞爆宿主 Python 栈。必须在超限时**提前**
# 报枢言诊断，而不是硬撑到 RecursionError —— 后者给用户的是一段
# Python 堆栈，属于「宿主崩溃」，比普通报错严重得多。
E_TOO_DEEP = "D2013"

E_CONST_NO_INIT = "D3001"
E_DEFAULT_PARAM_ORDER = "D3002"
E_NO_OVERLOAD = "D3003"
E_TYPE_MISMATCH = "D3004"
E_UNDEFINED = "D3005"
E_REDEFINED = "D3006"
E_ARG_COUNT = "D3007"
E_NOT_CALLABLE = "D3008"
E_RETURN_TYPE = "D3009"
E_COMPARE_CHAIN = "D3010"
E_LOOP_KEYWORD = "D3011"
E_DIVIDE_ZERO = "D3012"
E_NO_ATTRIBUTE = "D3014"
E_BAD_INDEX = "D3015"
E_RETURN_OUTSIDE_FN = "D3016"
# 结构体与枚举（SPEC §3.9）
E_STRUCT_FIELD = "D3017"        # 构造字段不符：缺少 / 多余 / 未知 / 类型不符
E_NO_FIELD = "D3018"            # 结构体字段不存在（读或写）
E_NO_MEMBER = "D3019"           # 枚举成员不存在 / 类型定义非法
E_IMPORT_CYCLE = "D3020"        # 模块循环引入（甲 → 乙 → 甲）
E_IMPORT_SELF = "D3021"         # 模块引入自身

W_SHADOW = "W4001"
W_UNUSED = "W4002"
W_UNREACHABLE = "W4003"
W_CONST_COND = "W4004"
W_EMPTY_BLOCK = "W4005"