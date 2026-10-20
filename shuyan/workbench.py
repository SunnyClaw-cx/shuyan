"""枢言语言 —— 工作台服务层（编译器六种操作的**唯一实现**）。

为什么单独成模块
----------------
命令行（`cli.py`）与图形界面（`gui.py`）需要**完全相同**的编译行为：
运行、检查、格式化、词法、语法树、测试。若各写一份，必然分叉
—— 这正是本项目反复踩到的工程铁律「同一语义两处实现必然分叉」。

实测过的分叉症状：CLI 的 `运行` 与 `检查` 曾各自解析模块路径，
基准不一致，于是「检查通过、运行报找不到模块」。

故本模块是六种操作的**唯一入口**，返回结构化结果；由界面层决定
如何呈现（终端打印 / 窗口文本控件）。界面层**不得**自行调用
`Lexer` / `Parser` / `Interpreter`。

结果模型
--------
`Outcome` 统一承载一次操作的产物：

- ``ok``        是否成功（无错误级诊断，且运行退出码为 0）
- ``text``      主要文本产物（程序输出、格式化结果、记号表、AST 树、测试报告）
- ``diagnostics`` 诊断列表（错误 + 警告），供界面**按类别着色**；
                 刻意不预先渲染成字符串 —— 渲染格式是界面层的事，
                 而「诊断对象」才是共同语言。
- ``code``      退出码（0 成功 / 1 编译或运行错误 / 2 用法错误）

统一用 `Outcome` 而不是各自返回裸字符串，是为了让界面层能一致地
处理「成功但有警告」「失败但仍有部分输出」这两类中间状态。
"""

from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass, field

from .ast_nodes import Node
from .builtins import StdLib
from .diagnostics import (
    Diagnostic, LexError, ParseError, SemanticError, Severity, render_all,
)
from .formatter import format_source
from .interpreter import Interpreter
from .lexer import Kind, Lexer, describe_tokens
from .linter import lint_source
from .parser import Parser
from .semantic import Analyzer

# 操作名（界面层的动作标识，与 CLI 子命令同名）
RUN = "运行"
CHECK = "检查"
FORMAT = "格式化"
LEX = "词法"
AST = "语法树"
TEST = "测试"

OPERATIONS = (RUN, CHECK, FORMAT, LEX, AST, TEST)


@dataclass
class Outcome:
    """一次工作台操作的完整结果。"""

    operation: str
    ok: bool
    text: str = ""
    diagnostics: list[Diagnostic] = field(default_factory=list)
    code: int = 0
    # 供界面切换显示的文件路径（多为被操作的文件）
    file: str = ""

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics
                if d.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics
                if d.severity is Severity.WARNING]

    def render(self, source: str = "", line_map: dict | None = None) -> str:
        """把诊断渲染成中文报告块（含源码摘录与下划线）。

        渲染逻辑**收敛到 `diagnostics.render_all`**，本方法只负责
        补齐跨文件所需的「其它文件源码」。界面层若自行拼字符串，
        就会丢掉下划线对齐与「共 N 个错误」汇总。
        """
        if not self.diagnostics:
            return ""
        map_ = dict(line_map or {})
        for d in self.diagnostics:
            if d.file and d.file not in map_ and d.source_lines is None:
                try:
                    with open(d.file, "r", encoding="utf-8") as f:
                        map_[d.file] = f.read().splitlines()
                except OSError:
                    pass
        return render_all(self.diagnostics, source,
                          main_file=self.file, source_line_map=map_)


# --------------------------------------------------------------- 共享管线

def compile_unit(source: str, path: str, stdlib: StdLib):
    """词法 → 语法 → 语义。成功返回 (program, analyzer, diags)；
    失败抛 `LexError` / `ParseError` / `SemanticError`。

    ``diags`` 含警告；错误级诊断已在抛出的异常里。
    """
    tokens = Lexer(source, path).tokenize()
    program = Parser(tokens, path).parse()
    analyzer = Analyzer(stdlib, filename=path,
                        source_lines=source.splitlines())
    _, diags = analyzer.analyze(program)
    if [d for d in diags if d.severity is Severity.ERROR]:
        raise SemanticError(diags)
    return program, analyzer, diags


def make_stdlib(path: str, argv: list[str] | None = None,
                stdout=None) -> StdLib:
    """构造以「主脚本所在目录」为基准的标准库实例。

    路径基准**必须**由本函数统一给出：CLI 与界面层各算一次必然分叉，
    且分叉症状是「检查通过、运行找不到模块」这类最难查的形态。
    """
    return StdLib(argv=argv or [], stdout=stdout,
                  base_dir=os.path.dirname(os.path.abspath(path))
                  if path else os.getcwd())


def too_deep_diag(source: str) -> Diagnostic:
    """嵌套太深 → 枢言诊断（兜底）。

    位置取第 1 行并说明这是整体性问题 —— 栈已展开，最深处那一行不可得。
    """
    from .diagnostics import E_TOO_DEEP
    from .source import Span
    return Diagnostic(
        E_TOO_DEEP,
        "源码嵌套太深，已超出本实现可安全解析的深度。",
        Span(1, 1, 1, 1), Severity.ERROR,
        ["把深层嵌套拆成多个中间变量或函数",
         "括号 / 列表 / 语句块的嵌套层数越少越好",
         f"（本文件共 {len(source.splitlines())} 行）"],
    )


def dump_ast(node, indent: int = 0) -> str:
    """把 AST 打印成可读的树形结构（CLI `语法树` 与界面共用）。"""
    pad = "  " * indent
    name = type(node).__name__
    extras = []
    for f in dataclasses.fields(node):
        if f.name == "span":
            continue
        value = getattr(node, f.name)
        if isinstance(value, (str, int, float, bool)) or value is None:
            extras.append(f"{f.name}={value!r}")
    head = f"{pad}{name}"
    if extras:
        head += "(" + ", ".join(extras[:6]) + ")"
    lines = [head]
    for child in _node_children(node):
        lines.append(dump_ast(child, indent + 1))
    return "\n".join(lines)


def _node_children(n):
    for f in dataclasses.fields(n):
        if f.name == "span":
            continue
        v = getattr(n, f.name)
        if isinstance(v, Node):
            yield v
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, Node):
                    yield item
                elif isinstance(item, tuple):
                    for sub in item:
                        if isinstance(sub, Node):
                            yield sub
                        elif isinstance(sub, list):
                            for s in sub:
                                if isinstance(s, Node):
                                    yield s
        elif isinstance(v, dict):
            for x in v.values():
                if isinstance(x, Node):
                    yield x


# --------------------------------------------------------------- 六种操作

def op_run(source: str, path: str, argv: list[str] | None = None,
           stdout=None, capture: bool = True) -> Outcome:
    """运行程序。

    `capture=True` 时输出经 `StdLib.written` 收集，不写 `stdout`
    —— 界面层用它把输出抓进窗口；CLI 传 `capture=False` 直接打到终端。

    **运行期错误与程序输出同走 stdout**：解释器把未捕获异常渲染成 D4001
    后也是经 `emit` 发出的，故二者都在 `text` 里，顺序即用户看到的顺序。
    这**不是**缺陷：终端上本来就是这样呈现的，界面照搬才与 CLI 一致。
    界面层只需据 `code` 判断成功与否，再把 `text` 原样展示 —— 若在此处
    试图「把诊断从输出里切出来」，就得自己写一套识别规则，反而分叉。
    """
    stdlib = make_stdlib(path, argv, stdout)
    stdlib.capture = capture
    try:
        program, analyzer, diags = compile_unit(source, path, stdlib)
    except (LexError, ParseError, SemanticError) as exc:
        return Outcome(RUN, False, "", list(exc.diagnostics), 1, path)
    except RecursionError:
        return Outcome(RUN, False, "", [too_deep_diag(source)], 1, path)

    interp = Interpreter(stdlib, path, source.splitlines(), analyzer)
    code = interp.run(program)
    text = "\n".join(stdlib.written)
    # 警告与「运行期诊断」分开：警告是提醒，不是故障，不当错误处理。
    runtime_diags = [d for d in diags if d.severity is not Severity.ERROR]
    return Outcome(RUN, code == 0, text, runtime_diags, code, path)


def op_check(source: str, path: str) -> Outcome:
    """静态检查（语义 + Lint），不执行。

    走 `lint_source` —— 它用 `parse_recovering`，**语法错与语义错一次报全**。
    若这里改用 `Parser.parse()`，就会退回「语法错吞掉语义错」的老缺陷。
    """
    _, diags = lint_source(source, filename=path)
    diags = diags or []
    ok = not any(d.severity is Severity.ERROR for d in diags)
    return Outcome(CHECK, ok, "", diags, 0 if ok else 1, path)


def op_format(source: str, path: str) -> Outcome:
    """格式化源码。不改文件 —— 是否落盘由调用方决定。"""
    formatted, issues = format_source(source, path)
    notes = []
    if issues:
        from .source import Span
        notes = [Diagnostic("W4006", issue, Span(1, 1, 1, 1),
                            Severity.WARNING) for issue in issues]
    return Outcome(FORMAT, True, formatted, notes, 0, path)


def op_lex(source: str, path: str) -> Outcome:
    """打印记号流。词法阶段失败就没有记号可谈，直接返回诊断。"""
    try:
        tokens = Lexer(source, path).tokenize()
    except LexError as exc:
        return Outcome(LEX, False, "", list(exc.diagnostics), 1, path)
    except RecursionError:
        return Outcome(LEX, False, "", [too_deep_diag(source)], 1, path)
    return Outcome(LEX, True, describe_tokens(tokens), [], 0, path)


def op_ast(source: str, path: str) -> Outcome:
    """打印抽象语法树。"""
    try:
        tokens = Lexer(source, path).tokenize()
        program = Parser(tokens, path).parse()
    except (LexError, ParseError) as exc:
        return Outcome(AST, False, "", list(exc.diagnostics), 1, path)
    except RecursionError:
        return Outcome(AST, False, "", [too_deep_diag(source)], 1, path)
    return Outcome(AST, True, dump_ast(program), [], 0, path)


def run_tests(verbose: bool = False) -> Outcome:
    """运行内置自测集，把报告抓成文本。"""
    import contextlib
    import io
    from .tests import run_all
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = run_all(verbose=verbose)
    return Outcome(TEST, code == 0, buf.getvalue(), [], code, "")


def perform(operation: str, source: str = "", path: str = "",
            argv: list[str] | None = None, stdout=None,
            capture: bool = True) -> Outcome:
    """按操作名分派 —— 界面层的**唯一调用点**。

    界面层不该 import 上述任何一个 `op_*`；统一走这里，
    便于以后新增操作时只改一处。
    """
    table = {
        RUN: lambda: op_run(source, path, argv, stdout, capture),
        CHECK: lambda: op_check(source, path),
        FORMAT: lambda: op_format(source, path),
        LEX: lambda: op_lex(source, path),
        AST: lambda: op_ast(source, path),
        TEST: lambda: run_tests(verbose="详细" in (argv or [])),
    }
    fn = table.get(operation)
    if fn is None:
        raise ValueError(f"未知的工作台操作：{operation}")
    return fn()


# --------------------------------------------------------------- 语法高亮

def highlight_ranges(source: str, path: str) -> list[tuple[int, int, int, str]]:
    """给出**语法高亮区间**：[(行, 起列, 止列, 样式名), ...]。

    **列按「字符」计**（不是显示列）—— 因为界面控件（tkinter Text）
    的索引就是按字符数的。词法记号的 `Span.col` 是**显示列**（汉字算 2），
    两者口径不同，故这里不直接使用 `Span.col`，改为**在行内按记号原文定位**：
    从上一个记号结束处向后搜索 `token.text`，找到的字符下标即真实字符列。
    这是唯一稳妥的做法 —— 把显示列当字符列用，中文行的坐标会整段偏移。

    **为什么复用词法器而不另写扫描器**：高亮需要正确处理**注释、字符串、
    全角归一化、汉字块切分、反引号标识符**，这些规则全部已在 `Lexer` 里。
    自己再写一遍必然分叉（词法规则一改，高亮层不会跟着改）。
    注释不产生记号，但记号之间存在**空档**，空档里的 `#` 即为注释。

    样式名由界面层映射到颜色，本层不关心配色。
    """
    try:
        tokens = Lexer(source, path).tokenize()
    except (LexError, RecursionError):
        # 词法失败：拿不到完整记号流，退化为「只标注释」。
        return _only_comments(source)

    by_line: dict[int, list] = {}
    for t in tokens:
        if t.kind is Kind.EOF:
            continue
        by_line.setdefault(t.span.line, []).append(t)

    lines = source.splitlines()
    styles: list[tuple[int, int, int, str]] = []
    for line_no, toks in by_line.items():
        if not (1 <= line_no <= len(lines)):
            continue
        line = lines[line_no - 1]
        toks.sort(key=lambda t: t.span.col)
        cursor = 0                      # 字符下标（0 起）
        for t in toks:
            text = t.text
            at = line.find(text, cursor) if text else -1
            if at < 0:
                at = _approx_index(line, t, cursor)
            if at > cursor:
                gap = line[cursor:at]
                if "#" in gap:
                    styles.append((line_no, cursor + gap.index("#"),
                                   len(line), "注释"))
            end = at + len(text)
            styles.append((line_no, at, end, _style_of(t)))
            cursor = max(cursor, end)
        tail = line[cursor:]
        if "#" in tail:
            styles.append((line_no, cursor + tail.index("#"),
                           len(line), "注释"))
    return styles


def _approx_index(line: str, token, cursor: int) -> int:
    """记号原文在行内找不到时的近似定位（全角归一化会改写原文）。

    尽力而为：取 `cursor`，至少保证后续记号的相对次序不乱。
    """
    return cursor


def _only_comments(source: str) -> list[tuple[int, int, int, str]]:
    """词法失败时的降级高亮：只标出 `#` 注释，不猜代码结构。"""
    out = []
    for i, line in enumerate(source.splitlines(), start=1):
        idx = line.find("#")
        if idx >= 0:
            out.append((i, idx, len(line), "注释"))
    return out


def _style_of(t) -> str:
    """记号 → 样式名。"""
    if t.kind is Kind.KEYWORD:
        return "关键字"
    if t.kind is Kind.NUMBER:
        return "数字"
    if t.kind is Kind.STRING:
        return "文本"
    if t.kind is Kind.OP:
        return "运算符"
    if t.kind is Kind.IDENT:
        from .names import CORE_FUNCTION_NAMES, MODULE_NAMES, TYPE_NAMES
        if t.text in MODULE_NAMES:
            return "模块"
        if t.text in CORE_FUNCTION_NAMES:
            return "内置函数"
        if t.text in TYPE_NAMES:
            return "类型"
        return "标识符"
    return "普通"