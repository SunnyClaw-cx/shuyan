"""枢言语言 —— 命令行工具（sy）。

子命令（中文与拉丁双写法，降低输入成本）：

    sy 运行    <文件>            运行枢言程序
    sy 检查    <文件>            静态检查（语义 + Lint），不执行
    sy 格式化  <文件> [--写入]    格式化源码（默认打印，--写入 原地修改）
    sy 词法    <文件>            打印记号流（调试词法层）
    sy 语法树  <文件>            打印 AST（调试语法层）
    sy 界面    [文件]            打开桌面工作台（图形界面）
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
from .diagnostics import Severity, render_all
from .formatter import format_source
from .lexer import Lexer
from .linter import lint_source
from .workbench import (
    LEX, AST, op_ast, op_lex, op_run,
)

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
  界面     [文件.sy]              打开桌面工作台（图形界面，可选带文件）
  测试                           运行内置测试集
  版本                           显示版本
  帮助                           显示本帮助

示例：
  sy 运行 examples/你好世界.sy
  sy 格式化 examples/斐波那契.sy --写入
  sy 检查 examples/中文切分演示.sy
  sy 界面 examples/你好世界.sy

等价拉丁写法：run / check / fmt / lex / ast / gui / test / version / help
"""

ALIASES = {
    "run": "运行", "check": "检查", "fmt": "格式化", "format": "格式化",
    "lex": "词法", "ast": "语法树", "gui": "界面", "desktop": "界面",
    "test": "测试",
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


def _render_with_files(diags, main_path: str, main_source: str) -> str:
    """渲染诊断（跨文件定位用）。

    **收敛到与图形界面同一条实现**：此前这里自己拼一遍 `line_map`，
    界面若再拼一遍就是「同一语义两处实现」，迟早分叉。
    现统一走 `workbench.Outcome.render`。
    """
    from .workbench import Outcome
    return Outcome("", False, "", list(diags), 1, main_path).render(main_source)


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
    """运行单个文件。**与图形界面共用 `workbench.op_run`**。

    编译失败时诊断打到 stderr；运行期的程序输出由 `StdLib` 直接写
    标准输出（`capture=False`），保持命令行「边跑边看」的体验。
    """
    source = _read_source(path)
    outcome = op_run(source, path, argv=extra, capture=False)
    if outcome.diagnostics and outcome.code != 0:
        # 编译期失败：程序尚未开始运行，诊断是唯一产物
        print(_render_with_files(outcome.diagnostics, path, source),
              file=sys.stderr)
        return 1
    warnings = outcome.warnings
    if warnings and outcome.code == 0:
        print(f"（{len(warnings)} 条提醒，不影响运行）", file=sys.stderr)
        for w in warnings:
            print(f"  第 {w.span.line} 行：{w.message}", file=sys.stderr)
    return outcome.code


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
        print(_render_with_files(diags, path, source))
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
    outcome = op_lex(source, path)
    if not outcome.ok:
        print(render_all(outcome.diagnostics, source), file=sys.stderr)
        return 1
    print(outcome.text)
    return 0


def cmd_ast(args: list[str]) -> int:
    if not args:
        print("用法：sy 语法树 <文件.sy>", file=sys.stderr)
        return 2
    path = args[0]
    source = _read_source(path)
    outcome = op_ast(source, path)
    if not outcome.ok:
        print(render_all(outcome.diagnostics, source), file=sys.stderr)
        return 1
    print(outcome.text)
    return 0


def cmd_gui(args: list[str]) -> int:
    """打开桌面工作台。

    实现全部在 `shuyan.launcher` —— 它会先确认当前解释器能不能建窗口，
    不行就换一个（tkinter 并非所有 Python 发行版都自带）。
    本函数只做参数透传，不重复那套探测逻辑。
    """
    from .launcher import main as gui_main
    return gui_main(args)


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
        "词法": cmd_lex, "语法树": cmd_ast, "界面": cmd_gui,
        "测试": cmd_test,
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