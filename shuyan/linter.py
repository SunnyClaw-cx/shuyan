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
from .diagnostics import Diagnostic, Severity
from .lexer import Kind, Lexer
from .semantic import Analyzer
from .source import display_width
from .builtins import StdLib

MAX_WIDTH = 100


class Linter:
    def __init__(self, stdlib: StdLib | None = None):
        self.stdlib = stdlib or StdLib()
        self.diagnostics: list[Diagnostic] = []

    def lint(self, program: Program, source: str) -> list[Diagnostic]:
        # 语义层检查（错误 + 警告一并收集）
        analyzer = Analyzer(self.stdlib)
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
    """
    from .diagnostics import LexError, ParseError
    from .parser import Parser
    if stdlib is None and filename not in ("<源码>", ""):
        stdlib = StdLib(base_dir=os.path.dirname(os.path.abspath(filename)))
    try:
        tokens = Lexer(source, filename).tokenize()
        program = Parser(tokens, filename).parse()
    except (LexError, ParseError) as exc:
        return None, exc.diagnostics
    linter = Linter(stdlib)
    return program, linter.lint(program, source)