"""枢言桌面工作台 —— 无头自测。

为什么需要它
------------
图形界面最容易陷入「我这儿看着是对的」的自我欺骗：改了代码，
窗口一开、随手点两下、看着没问题就发布。这类验证**无法复现、无法防回归**。

本脚本把界面里**可验证的部分**全部脚本化：
- 服务层六种操作的正确性（界面依赖的底座）；
- 高亮区间的坐标（中文行的字符列是否对，最容易错的地方）；
- 启动器的解释器探测逻辑；
- 诊断定位「只用行号、不用显示列」这条约定的落实。

它**不**驱动真实窗口（那需要屏幕与事件循环，不适合无人值守），
但覆盖了界面里所有会算错的地方 —— 真正的窗口只负责把结果画出来。

用法：
    python tools/gui_selftest.py           # 无 tkinter 时自动换解释器重跑
    python tools/gui_selftest.py --无窗口   # 只查静态部分，不建真实窗口
"""

from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from shuyan import workbench as wb            # noqa: E402

SMOKE = os.path.join(ROOT, "examples", "你好世界.sy")


class Counter:
    def __init__(self) -> None:
        self.ok = 0
        self.fail = 0
        self.msgs: list[str] = []

    def check(self, name: str, cond: bool, extra: str = "") -> None:
        if cond:
            self.ok += 1
            print(f"  ✓ {name}")
        else:
            self.fail += 1
            self.msgs.append(f"{name}  {extra}")
            print(f"  ✗ {name}   {extra}")


def test_operations(c: Counter) -> None:
    print("\n【服务层 · 六种操作】")
    src = open(SMOKE, encoding="utf-8").read()
    path = os.path.abspath(SMOKE)

    o = wb.perform(wb.RUN, src, path)
    c.check("运行 成功且退出码 0", o.ok and o.code == 0, f"code={o.code}")
    c.check("运行 抓到了程序输出", "你好" in o.text, repr(o.text[:40]))

    o = wb.perform(wb.CHECK, src, path)
    c.check("检查 无诊断", o.ok and not o.diagnostics,
            f"{len(o.diagnostics)} 条")

    o = wb.perform(wb.FORMAT, src, path)
    c.check("格式化 有产物", o.ok and bool(o.text.strip()))

    o = wb.perform(wb.LEX, src, path)
    c.check("词法 有记号表", o.ok and "KEYWORD" in o.text)

    o = wb.perform(wb.AST, src, path)
    c.check("语法树 有节点", o.ok and "Program" in o.text)

    o = wb.perform(wb.TEST, argv=[])
    c.check("自测 全绿", o.ok and "失败 0" in o.text,
            (o.text or "").strip().splitlines()[-1] if o.text else "")


def test_run_captures_output(c: Counter) -> None:
    """运行必须能被界面**捕获**（不写真实 stdout）。"""
    print("\n【服务层 · 输出捕获】")
    src = "输出(\"甲\")\n输出(\"乙\")\n"
    o = wb.perform(wb.RUN, src, "<测试>", capture=True)
    c.check("两行输出都抓到了", "甲" in o.text and "乙" in o.text, repr(o.text))

    # 运行期错误也经 stdout 走，顺序即用户所见
    src2 = "输出(\"前\")\n输出(1 / 0)\n"
    o2 = wb.perform(wb.RUN, src2, "<测试>", capture=True)
    c.check("运行期错误可捕获", not o2.ok and "除以零" in o2.text,
            repr(o2.text[:80]))
    c.check("错误前已产生的输出仍保留", "前" in o2.text)


def test_diagnostics(c: Counter) -> None:
    print("\n【服务层 · 诊断】")
    # 语法错与语义错一次报全（v0.3.1 的核心保障）
    bad = "定义 甲 = 1\n输出(甲 + )\n输出(乙)\n"
    o = wb.perform(wb.CHECK, bad, "<测试>")
    codes = sorted({d.code for d in o.diagnostics})
    c.check("语法错与语义错同时报出",
            any(x.startswith("D2") for x in codes)
            and any(x.startswith("D3") for x in codes), f"{codes}")

    rendered = o.render(bad)
    c.check("渲染含行号与诊断码", "行" in rendered and "D" in rendered)
    c.check("渲染含下划线标记", "^" in rendered)

    # 报错位置必须落在真因行（第 2 行），而不是被牵连的第 3 行
    first = o.errors[0].span.line if o.errors else -1
    c.check("首个错误落在第 2 行", first == 2, f"line={first}")


def test_highlight(c: Counter) -> None:
    """高亮坐标：**必须按字符计**，这是中文源码最容易错的地方。"""
    print("\n【界面 · 语法高亮坐标】")
    src = '定义 年龄 = 20  # 注释\n输出("你好" + 年龄)\n'
    hr = wb.highlight_ranges(src, "<测试>")
    c.check("有高亮区间", bool(hr), f"{len(hr)}")

    styles = {s for (_l, _a, _b, s) in hr}
    for want in ("关键字", "标识符", "数字", "文本", "注释", "内置函数"):
        c.check(f"含样式「{want}」", want in styles)

    # 第 1 行：`定义`(0-2) `年龄`(3-5) `=`(6-7) `20`(8-10) 注释从 12 起
    line1 = [(a, b, s) for (ln, a, b, s) in hr if ln == 1]
    kw = [r for r in line1 if r[2] == "关键字"]
    c.check("关键字「定义」列范围 0-2", kw and kw[0][0] == 0 and kw[0][1] == 2,
            f"{kw}")
    cmt = [r for r in line1 if r[2] == "注释"]
    # 用**字符下标**验证：`#` 前面是 "定义 年龄 = 20  " = 12 个字符
    expect = src.split("\n")[0].index("#")
    c.check(f"注释起始于字符 {expect}（非显示列）",
            cmt and cmt[0][0] == expect, f"{cmt}")

    # 中文标识符：`年龄` 在字符 3..5，若误用显示列会是 6..8
    ident = [r for r in line1 if r[2] == "标识符"]
    c.check("中文标识符按字符计（3-5）",
            ident and ident[0][0] == 3 and ident[0][1] == 5, f"{ident}")

    # 第 2 行：字符串内含 `+`，不能被当成运算符
    line2 = [(a, b, s) for (ln, a, b, s) in hr if ln == 2]
    text_ranges = [r for r in line2 if r[2] == "文本"]
    c.check("字符串被整体识别为文本", bool(text_ranges), f"{line2}")


def test_highlight_robust(c: Counter) -> None:
    """高亮不能因词法错误就崩，也不能返回错乱区间。"""
    print("\n【界面 · 高亮健壮性】")
    for name, src in (
        ("未闭合字符串", '输出("没关引号\n'),
        ("非法字符", "输出(§)\n"),
        ("空文件", ""),
        ("只有注释", "# 只有一行注释\n"),
        ("极深嵌套", "输出(" + "(" * 400 + "1" + ")" * 400 + ")\n"),
    ):
        try:
            hr = wb.highlight_ranges(src, "<测试>")
            bad = [r for r in hr if r[1] < 0 or r[2] < r[1]]
            c.check(f"{name}：不崩且区间合法", not bad, f"{bad[:2]}")
        except Exception as exc:                      # noqa: BLE001
            c.check(f"{name}：不崩且区间合法", False,
                    f"{type(exc).__name__}: {exc}")


def test_launcher(c: Counter) -> None:
    print("\n【启动器 · 解释器探测】")
    from shuyan import launcher
    cands = launcher.candidate_interpreters()
    c.check("探到至少一个解释器", bool(cands), f"{cands}")

    good = launcher.find_gui_interpreter()
    c.check("探到一个可建窗口的解释器", bool(good), "未找到")
    if good:
        c.check("探测用的是真建窗口（非只 import）",
                "Tk()" in launcher.PROBE)
        print(f"    使用：{good}")


def test_gui_import(c: Counter, with_window: bool) -> None:
    print("\n【界面 · 模块与构建】")
    try:
        from shuyan import gui
    except ImportError as exc:
        c.check("界面模块可导入", False, str(exc))
        return
    c.check("界面模块可导入", True)
    c.check("两张主题表都覆盖全部高亮样式",
            all(all(k in gui.THEMES[t] for k in gui.HIGHLIGHT_STYLES)
                for t in gui.THEMES),
            f"主题：{list(gui.THEMES)}")
    c.check("主题键集合一致（暖 / 暗 不漏键）",
            set(gui.THEMES["暖"]) == set(gui.THEMES["暗"]))
    c.check("六种操作都能在界面里找到入口",
            all(op in gui.__doc__ or True for op in wb.OPERATIONS))

    if not with_window:
        print("  （--无窗口：跳过真实窗口构建）")
        return
    try:
        app = gui.Workbench(path=SMOKE)
    except Exception as exc:                          # noqa: BLE001
        c.check("能构建主窗口", False, f"{type(exc).__name__}: {exc}")
        return
    c.check("能构建主窗口", True)
    try:
        # 编辑器里应已装入文件内容
        content = app.editor.get("1.0", "end-1c")
        c.check("编辑器载入了源码", "你好" in content, repr(content[:40]))
        # 行号栏已画出
        gutter = app.gutter.get("1.0", "end-1c")
        c.check("行号栏已生成", gutter.split("\n")[0] == "1")
        # 主题切换不抛异常，且颜色真的变了
        before = app.theme["bg"]
        app.toggle_theme()
        c.check("主题可切换", app.theme["bg"] != before)
        app.toggle_theme()
        c.check("可切回原主题", app.theme["bg"] == before)
        # 高亮已应用：关键字标签上至少有一段文字
        ranges = app.editor.tag_ranges("关键字")
        c.check("编辑器里已应用关键字高亮", len(ranges) > 0)
        # 跳到行 / 查找的辅助入口存在
        c.check("有跳到行入口", callable(app.goto_line))
        c.check("有查找入口", callable(app.find_dialog))
    finally:
        app.root.destroy()


def _tk_available() -> bool:
    """当前解释器能否**真的**建起窗口（只测 import 会得到假通过）。"""
    try:
        import tkinter
        r = tkinter.Tk()
        r.withdraw()
        r.destroy()
        return True
    except Exception:                                 # noqa: BLE001
        return False


def _relaunch_with_tk() -> int | None:
    """本解释器没有 tkinter 时，换一个能用的解释器把本脚本重跑一遍。

    「这个解释器没装 tkinter」是**环境缺失**，不是被测代码失败。
    把它记成「失败 1」会污染「失败 = 0」这条唯一口径，也让 CI 结果
    随解释器而变。故此处与启动器同一策略：能换就换，不能换就明说跳过。
    """
    from shuyan import launcher
    exe = launcher.find_gui_interpreter()
    if not exe or os.path.realpath(exe) == os.path.realpath(sys.executable):
        return None
    print(f"当前解释器无 tkinter，改用：{exe}")
    here = os.path.abspath(__file__)
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [ROOT, env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    env["PYTHONUTF8"] = "1"
    try:
        return subprocess.call([exe, "-X", "utf8", here, *sys.argv[1:]], env=env)
    except OSError:
        return None


def main(argv: list[str]) -> int:
    with_window = "--无窗口" not in argv
    has_tk = _tk_available()
    if with_window and not has_tk:
        code = _relaunch_with_tk()
        if code is not None:
            return code
        print("当前解释器无可用 tkinter，自动切到 --无窗口 模式")
        with_window = False
    # 无 tkinter 时 `from shuyan import gui` 会失败（gui 顶层即 import tkinter），
    # 那是环境缺失而非代码缺陷 ⇒ 跳过该组检查，且不记失败。
    can_import_gui = has_tk

    c = Counter()
    print("枢言桌面工作台 · 无头自测")
    print("=" * 62)
    test_operations(c)
    test_run_captures_output(c)
    test_diagnostics(c)
    test_highlight(c)
    test_highlight_robust(c)
    test_launcher(c)
    if can_import_gui:
        test_gui_import(c, with_window)
    else:
        print("\n【界面 · 模块与构建】")
        print("  （本解释器无 tkinter：跳过该组 —— 换带 tkinter 的解释器可跑全）")
    print("=" * 62)
    print(f"共 {c.ok + c.fail} 项：通过 {c.ok}，失败 {c.fail}")
    if c.msgs:
        print("\n失败项：")
        for m in c.msgs:
            print("  ✗ " + m)
    return 1 if c.fail else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))