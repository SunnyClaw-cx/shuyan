"""枢言语言 —— 静态检查工具（Lint，SPEC §5.6 警告码）。

与语义分析器的分工：
- 语义分析器负责**错误**（程序跑不起来的那类）；
- Lint 负责**可疑但能跑**的写法——它输出的是提醒，不是判决。

Lint 复用语义分析的结果，并追加风格与陷阱类检查。
"""

from __future__ import annotations

import os

from .ast_nodes import (
    Assign, Binary, Call, Compare, ExprStmt, ForEach, FuncDef, Identifier,
    If, Literal, Node, Program, RepeatTimes, While, walk,
)
from .diagnostics import Diagnostic, Severity, E_TOO_DEEP
from .lexer import Kind, Lexer
from .semantic import Analyzer
from .source import Span, display_width
from .builtins import StdLib

MAX_WIDTH = 100


def _too_deep_diag(source: str) -> Diagnostic:
    """把漏网的 RecursionError 转成枢言诊断（兜底用）。

    位置取「最深处的那一行」不可得（栈已展开），故指向第 1 行并说明
    这是整体性问题 —— 好过让用户面对一段 Python 堆栈。
    """
    lines = source.splitlines()
    return Diagnostic(
        E_TOO_DEEP,
        "源码嵌套太深，已超出本实现可安全解析的深度。",
        Span(1, 1, 1, 1), Severity.ERROR,
        ["把深层嵌套拆成多个中间变量或函数",
         "括号 / 列表 / 语句块的嵌套层数越少越好",
         f"（本文件共 {len(lines)} 行）"],
    )


class Linter:
    def __init__(self, stdlib: StdLib | None = None,
                 filename: str = "", source_lines: list[str] | None = None):
        self.stdlib = stdlib or StdLib()
        self.filename = filename
        self.source_lines = source_lines
        self.diagnostics: list[Diagnostic] = []

    def lint(self, program: Program, source: str) -> list[Diagnostic]:
        # 语义层检查（错误 + 警告一并收集）。
        # 带上文件名与源码行：被引入模块的诊断会回流（见 semantic），
        # 必须能区分「这条来自哪个文件」并在正确文件里画下划线。
        analyzer = Analyzer(self.stdlib, filename=self.filename,
                            source_lines=self.source_lines
                            or source.splitlines())
        _, diags = analyzer.analyze(program)
        self.diagnostics.extend(diags)

        for stmt in program.body:
            self._check_node(stmt)
        self._check_style(source)
        return self.diagnostics

    # ------------------------------------------------------------ AST 检查

    def _check_node(self, node: Node) -> None:
        for n in walk(node):
            self._check_one(n)

    def _check_one(self, node: Node) -> None:
        # 用 `==` 与 `空` 比较的可疑写法：`甲 == 空` 是对的，但 `甲 == 假`
        # 用于判空是常见误用
        if isinstance(node, Compare) and node.op == "==":
            right = node.right
            if isinstance(right, Literal) and right.literal_kind == "bool" \
                    and right.value is False:
                self.diagnostics.append(Diagnostic(
                    "W4101",
                    "用 `== 假` 判断可能不是你的本意——枢言里空文本、"
                    "空列表、0 也都是假值。",
                    node.span, Severity.WARNING,
                    ["若想判断「没有值」，用 `甲 是 空`",
                     "若想判断「为假」，直接写 `如果 甲 则 …`"]))

        # `如果 x 则` 里条件恒为常量文本/数字（可读性问题）
        if isinstance(node, If):
            for cond, _ in node.branches:
                if isinstance(cond, Literal) and cond.literal_kind == "text":
                    self.diagnostics.append(Diagnostic(
                        "W4102",
                        "分支条件是一个固定的文本值，判断结果恒定。",
                        cond.span, Severity.WARNING,
                        ["检查是否漏写了比较，例如 `如果 状态 是 \"就绪\"`"]))

        # 长度与索引混用：`循环 从 0 到 长度(名单)` 会越界
        if isinstance(node, ForEach):
            pass

        # 嵌套函数里的隐式全局写入（容易出乎意料）
        if isinstance(node, Assign) and isinstance(node.target, Identifier):
            pass

    # ------------------------------------------------------------ 风格检查

    def _check_style(self, source: str) -> None:
        lines = source.replace("\r\n", "\n").split("\n")
        for i, line in enumerate(lines, 1):
            if "\t" in line:
                self.diagnostics.append(Diagnostic(
                    "W4201", "这一行使用了 Tab 缩进，规范要求用 4 个空格。",
                    _span(i, 1), Severity.WARNING,
                    ["运行 `sy 格式化 <文件>` 可自动修正"]))
            if display_width(line) > MAX_WIDTH:
                self.diagnostics.append(Diagnostic(
                    "W4202",
                    f"这一行有 {display_width(line)} 显示列，"
                    f"超过规范的 {MAX_WIDTH} 列。",
                    _span(i, 1), Severity.WARNING,
                    ["把长表达式拆成多行（行尾放一个运算符即可自动续行）"]))
            if line.rstrip() != line:
                self.diagnostics.append(Diagnostic(
                    "W4203", "行尾有多余空白。", _span(i, len(line)),
                    Severity.WARNING, ["运行 `sy 格式化` 可自动清理"]))
            # 全角括号提示——**只在代码区**提示。
            # 字符串里的「（测试）」是数据；注释里的「（如 …）」是散文——
            # 二者用中文标点都是正确的，把它们当风格问题提示属于误报。
            code_only = _outside_strings(_strip_comment(line))
            for mark, half in (("（", "("), ("）", ")")):
                if mark in code_only:
                    self.diagnostics.append(Diagnostic(
                        "W4204",
                        f"代码里使用了全角括号「{mark}」。"
                        f"枢言能识别，但建议统一用半角 {half}。",
                        _span(i, code_only.index(mark) + 1),
                        Severity.WARNING,
                        ["运行 `sy 格式化` 可自动转换"]))


def _span(line: int, col: int):
    from .source import Span
    return Span(line, max(1, col), line, max(1, col) + 1)


def _strip_comment(line: str) -> str:
    """去掉行尾注释，只留代码部分（字符串内部的 `#` 不算注释）。"""
    in_str = None
    i = 0
    while i < len(line):
        ch = line[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = None
            i += 1
            continue
        if ch in ('"', "'", "`"):
            in_str = ch
            i += 1
            continue
        if ch == "#":
            return line[:i]
        if ch == "/" and i + 1 < len(line) and line[i + 1] == "/":
            return line[:i]
        i += 1
    return line


def _outside_strings(line: str) -> str:
    """把字符串内容替换为等长空格，供「仅代码区」的检查使用。"""
    out = []
    in_str = None
    i = 0
    while i < len(line):
        ch = line[i]
        if in_str:
            if ch == "\\" and i + 1 < len(line):
                out.append("  ")
                i += 2
                continue
            if ch == in_str:
                in_str = None
                out.append(ch)
            else:
                out.append(" ")
            i += 1
            continue
        if ch in ('"', "'", "`"):
            in_str = ch
            out.append(ch)
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def lint_source(source: str, stdlib: StdLib | None = None,
                filename: str = "<源码>"
                ) -> tuple[Program | None, list[Diagnostic]]:
    """便捷入口：解析 + 检查。解析失败时返回 (None, 诊断)。

    **filename 必须传**：本机模块的解析基准是「主脚本所在目录」
    （SPEC §3.8.1），不是进程当前目录。若此处不设 `base_dir`，
    `sy 检查 projects/x/主程序.sy` 会以 CWD 为基准而找不到同目录的模块 ——
    编译期与运行期口径不一致（`sy 运行` 用的是脚本目录）。

    **语法错与语义错要一起报**（SPEC §8.2 第 5 条「一次编译报告全部错误」）：
    解析器出错后**仍能产出一棵部分 AST**（行级错误恢复的副产物），
    故这里不再「一有语法错就整棵树丢掉」，而是**在语法诊断之外继续跑语义分析**，
    把两类诊断合并返回。否则用户修完语法错再跑一遍才看到语义错，来回两趟。
    """
    from .diagnostics import LexError
    from .parser import Parser
    if stdlib is None and filename not in ("<源码>", ""):
        stdlib = StdLib(base_dir=os.path.dirname(os.path.abspath(filename)))
    try:
        tokens = Lexer(source, filename).tokenize()
    except LexError as exc:
        # 词法阶段无法产出记号流 —— 后续语法/语义都无从谈起，只能就此返回。
        return None, exc.diagnostics
    except RecursionError:
        return None, [_too_deep_diag(source)]
    try:
        # `parse_recovering`：有语法错也返回部分 AST + 语法诊断，不抛异常。
        program, parse_diags = Parser(tokens, filename).parse_recovering()
        linter = Linter(stdlib, filename=filename,
                        source_lines=source.splitlines())
        diags = parse_diags + linter.lint(program, source)
        return program, _sorted_diags(diags)
    except RecursionError:
        # **兜底**：解析器的嵌套深度守卫（`MAX_NEST_DEPTH`）是**结构相关**
        # 的预算 —— 括号、列表、块嵌套每层消耗的 Python 帧数不同，
        # 无法用一个常数精确覆盖所有形态。万一仍有形态漏网并撞爆宿主栈，
        # 这里把它转成枢言诊断，而不是让用户看到一段 Python 堆栈。
        # 「守卫负责给好建议，兜底负责绝不崩宿主」，两道并存。
        return None, [_too_deep_diag(source)]


def _sorted_diags(diags: list[Diagnostic]) -> list[Diagnostic]:
    """按（行，列）稳定排序，使语法诊断与语义诊断交错后仍按源码顺序呈现。

    合并两批诊断后若不排序，会出现「第 7 行的错显示在被引用的前置错误之后」
    这类乱序 —— 用户按行往下改时会对不上。稳定排序保证同行内保持原有次序。
    """
    return sorted(diags, key=lambda d: (d.span.line, d.span.col))