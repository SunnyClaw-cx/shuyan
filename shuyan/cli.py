"""枢言语言 —— 命令行工具（sy）。

子命令（中文与拉丁双写法，降低输入成本）：

    sy 运行    <文件>            运行枢言程序
    sy 检查    <文件>            静态检查（语义 + Lint），不执行
    sy 格式化  <文件> [--写入]    格式化源码（默认打印，--写入 原地修改）
    sy 词法    <文件>            打印记号流（调试词法层）
    sy 语法树  <文件>            打印 AST（调试语法层）
    sy 测试                      运行内置测试集
    sy 版本                      版本信息
    sy 帮助                      帮助

退出码：0 成功 / 1 编译或运行错误 / 2 用法错误。
"""

from __future__ import annotations

import dataclasses
import os
import sys

from . import __version__
from .ast_nodes import Node
from .builtins import StdLib
from .diagnostics import (
    LexError, ParseError, SemanticError, Severity, render_all,
)
from .formatter import format_source
from .interpreter import Interpreter
from .lexer import Lexer, describe_tokens
from .linter import lint_source
from .parser import Parser
from .semantic import Analyzer

BANNER = """枢言（Shuyan）—— 中文原生编程语言 · 自研编译器工具链"""

HELP = f"""{BANNER}
版本 {__version__}

用法：sy <子命令> [参数]

子命令：
  运行     <文件.sy>              运行枢言程序
  检查     <文件.sy>              静态检查（语义 + Lint），不执行
  格式化   <文件.sy> [--写入]      格式化源码；--写入 表示原地保存
  词法     <文件.sy>              打印记号流
  语法树   <文件.sy>              打印抽象语法树
  测试                           运行内置测试集
  版本                           显示版本
  帮助                           显示本帮助

示例：
  sy 运行 examples/你好世界.sy
  sy 格式化 examples/斐波那契.sy --写入
  sy 检查 examples/中文切分演示.sy

等价拉丁写法：run / check / fmt / lex / ast / test / version / help
"""

ALIASES = {
    "run": "运行", "check": "检查", "fmt": "格式化", "format": "格式化",
    "lex": "词法", "ast": "语法树", "test": "测试",
    "version": "版本", "--version": "版本", "-v": "版本",
    "help": "帮助", "--help": "帮助", "-h": "帮助",
}


def _read_source(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _prepare_console() -> None:
    """Windows 控制台切到 UTF-8，否则中文输出乱码。

    这是中文语言工具链的必做项——若不做，用户在 cmd 里看到的
    会是 ``，第一印象即崩。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass


def _compile(source: str, path: str, stdlib: StdLib):
    """词法 → 语法 → 语义。抛出的异常由调用方渲染。"""
    tokens = Lexer(source, path).tokenize()
    program = Parser(tokens, path).parse()
    analyzer = Analyzer(stdlib)
    types, diags = analyzer.analyze(program)
    errors = [d for d in diags if d.severity is Severity.ERROR]
    if errors:
        raise SemanticError(diags)
    return program, analyzer, diags


def cmd_run(args: list[str]) -> int:
    """运行程序。**支持多个文件**：`sy 运行 examples/*.sy` 会依次运行每个
    文件（各自独立，互不共享状态），任一失败则整体返回非零。

    此前只取 `args[0]`，导致 `examples/*.sy` 形状**只跑了第一个文件**
    却报告成功 —— 静默遗漏。「只看第一个参数」在 shell 里几乎总是错的。
    """
    if not args:
        print("用法：sy 运行 <文件.sy> [更多文件...] [程序参数...]", file=sys.stderr)
        return 2

    files: list[str] = []
    for a in args:
        if os.path.exists(a):
            files.append(a)
        else:
            break                       # 第一个不存在的路径之后视为程序参数
    if not files:
        print(f"找不到文件：{args[0]}", file=sys.stderr)
        return 2
    extra = args[len(files):]
    multi = len(files) > 1

    worst = 0
    for path in files:
        if multi:
            print(f"── {path} ──")
        code = _run_one(path, extra)
        if code != 0:
            worst = code
    return worst


def _run_one(path: str, extra: list[str]) -> int:
    source = _read_source(path)
    stdlib = StdLib(argv=extra, base_dir=os.path.dirname(
        os.path.abspath(path)))
    try:
        program, analyzer, diags = _compile(source, path, stdlib)
    except (LexError, ParseError, SemanticError) as exc:
        print(render_all(exc.diagnostics, source), file=sys.stderr)
        return 1

    # 运行期错误与警告分开渲染：警告是提醒，不是故障。
    # 混在同一段「运行时错误」里会让人误以为程序跑不动了。
    interp = Interpreter(stdlib, path, source.splitlines(), analyzer)
    code = interp.run(program)
    warnings = [d for d in diags if d.severity is Severity.WARNING]
    if warnings and code == 0:
        print(f"（{len(warnings)} 条提醒，不影响运行）", file=sys.stderr)
        for w in warnings:
            print(f"  第 {w.span.line} 行：{w.message}", file=sys.stderr)
    return code


def cmd_check(args: list[str]) -> int:
    """静态检查。**支持多个文件** —— CI 与 Makefile 用 `examples/*.sy` 调用，
    若只检查第一个文件却报告「✓ 没有发现问题」，等于让自动检查形同虚设
    （静默遗漏比报错危险）。"""
    if not args:
        print("用法：sy 检查 <文件.sy> [更多文件...]", file=sys.stderr)
        return 2
    worst = 0
    for path in args:
        if not os.path.exists(path):
            print(f"找不到文件：{path}", file=sys.stderr)
            return 2
        source = _read_source(path)
        _, diags = lint_source(source, filename=path)
        if not diags:
            print(f"✓ {path}：没有发现问题。")
            continue
        print(render_all(diags, source))
        errors = [d for d in diags if d.severity is Severity.ERROR]
        if errors:
            worst = 1
    return worst


def cmd_format(args: list[str]) -> int:
    """格式化。**支持多个文件**：与 `检查` 同理，`examples/*.sy` 必须全处理。"""
    files = [a for a in args if not a.startswith("-")]
    write = "--写入" in args or "--write" in args or "-w" in args
    if not files:
        print("用法：sy 格式化 <文件.sy> [更多文件...] [--写入]", file=sys.stderr)
        return 2
    for path in files:
        if not os.path.exists(path):
            print(f"找不到文件：{path}", file=sys.stderr)
            return 2
        source = _read_source(path)
        formatted, issues = format_source(source, path)
        if write:
            if formatted == source:
                print(f"✓ {path}：已经是规范格式，无需改动。")
            else:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(formatted)
                print(f"✓ {path}：已格式化并保存。")
        else:
            if len(files) > 1:
                print(f"── {path} ──")
            sys.stdout.write(formatted)
        for issue in issues:
            print(f"  提示：{issue}", file=sys.stderr)
    return 0


def cmd_lex(args: list[str]) -> int:
    if not args:
        print("用法：sy 词法 <文件.sy>", file=sys.stderr)
        return 2
    path = args[0]
    source = _read_source(path)
    try:
        tokens = Lexer(source, path).tokenize()
    except LexError as exc:
        print(render_all(exc.diagnostics, source), file=sys.stderr)
        return 1
    print(describe_tokens(tokens))
    return 0


def cmd_ast(args: list[str]) -> int:
    if not args:
        print("用法：sy 语法树 <文件.sy>", file=sys.stderr)
        return 2
    path = args[0]
    source = _read_source(path)
    try:
        tokens = Lexer(source, path).tokenize()
        program = Parser(tokens, path).parse()
    except (LexError, ParseError) as exc:
        print(render_all(exc.diagnostics, source), file=sys.stderr)
        return 1
    print(_dump_ast(program))
    return 0


def _dump_ast(node, indent: int = 0, label: str = "") -> str:
    """把 AST 打印成可读的树形结构。"""
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

    def children_of(n):
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
    for child in children_of(node):
        lines.append(_dump_ast(child, indent + 1))
    return "\n".join(lines)


def cmd_test(args: list[str]) -> int:
    from .tests import run_all
    return run_all(verbose="-v" in args or "--详细" in args)


def cmd_version(args: list[str]) -> int:
    print(f"{BANNER}")
    print(f"版本：{__version__}")
    print(f"Python：{sys.version.split()[0]}")
    print(f"平台：{sys.platform}")
    return 0


def main(argv: list[str] | None = None) -> int:
    _prepare_console()
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(HELP)
        return 0
    command = argv[0]
    command = ALIASES.get(command, command)
    rest = argv[1:]

    table = {
        "运行": cmd_run, "检查": cmd_check, "格式化": cmd_format,
        "词法": cmd_lex, "语法树": cmd_ast, "测试": cmd_test,
        "版本": cmd_version, "帮助": lambda a: (print(HELP), 0)[1],
    }
    handler = table.get(command)
    if handler is None:
        print(f"未知的子命令「{argv[0]}」。\n", file=sys.stderr)
        print(HELP, file=sys.stderr)
        return 2
    return handler(rest)


if __name__ == "__main__":
    sys.exit(main())