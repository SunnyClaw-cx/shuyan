"""枢言语言 —— 桌面工作台启动器。

为什么需要单独一个启动器
------------------------
图形界面依赖标准库 `tkinter`，而**不是每个 Python 发行版都自带它**：
精简版、部分包管理器安装的版本、以及一些嵌入式发行版会把它拆成
独立包。本项目的承诺是「零第三方依赖」，故不能为此引入 PyQt 之类，
只能在**已有的解释器里挑一个可用的**。

启动器职责：
1. 测当前解释器有没有 tkinter（能 import 且能建窗口）；
2. 没有再找系统里其它 Python（`py` 启动器、PATH、常见安装目录）；
3. 找到后用**那个解释器**重新拉起自己，转交参数；
4. 都找不到时给出**可照做的**指引，而不是一句「启动失败」。

注意第 1 步必须**真的建一次窗口**：`import tkinter` 成功但
找不到显示设备 / Tcl 库缺失时报错会晚到 `Tk()` 那一步，
只测 import 会得到假通过。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

PROBE = (
    "import tkinter\n"
    "r = tkinter.Tk()\n"
    "r.withdraw()\n"
    "r.destroy()\n"
    "print('OK')\n"
)


def has_tk(executable: str | None = None) -> bool:
    """判断给定解释器能否**真正**建起一个窗口。"""
    exe = executable or sys.executable
    try:
        proc = subprocess.run([exe, "-c", PROBE], capture_output=True,
                              timeout=25, text=True)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and "OK" in (proc.stdout or "")


def candidate_interpreters() -> list[str]:
    """列出本机可能的 Python 解释器（去重、保序）。"""
    out: list[str] = []
    seen: set[str] = set()

    def add(p: str | None):
        if not p:
            return
        real = os.path.realpath(p)
        if real not in seen and os.path.isfile(p):
            seen.add(real)
            out.append(p)

    add(sys.executable)
    # Windows：`py` 启动器是发现「另一个 Python」最可靠的方式
    for name in ("py", "python3", "python"):
        add(shutil.which(name))
    # Windows 常见安装位置（只扫一层，不做全盘搜索）
    for base in (os.environ.get("LOCALAPPDATA"), os.environ.get("PROGRAMFILES"),
                 os.environ.get("PROGRAMFILES(X86)"), "C:\\"):
        if not base:
            continue
        for pattern in ("Programs/Python", "Python"):
            d = os.path.join(base, *pattern.split("/"))
            if not os.path.isdir(d):
                continue
            try:
                for name in sorted(os.listdir(d), reverse=True):
                    add(os.path.join(d, name, "python.exe"))
            except OSError:
                pass
    return out


def find_gui_interpreter() -> str | None:
    """找一个能建窗口的解释器；找不到返回 None。"""
    for exe in candidate_interpreters():
        if has_tk(exe):
            return exe
    return None


def _relaunch(exe: str, argv: list[str]) -> int:
    """用指定解释器重新运行图形界面。"""
    here = os.path.dirname(os.path.abspath(__file__))
    cmd = [exe, "-X", "utf8", "-m", "shuyan.gui", *argv]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [os.path.dirname(here), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    # PYTHONUTF8：让子进程的默认编码也是 UTF-8，中文源码路径才不出乱码
    env["PYTHONUTF8"] = "1"
    try:
        return subprocess.call(cmd, env=env)
    except OSError as exc:
        print(f"无法启动图形界面：{exc}", file=sys.stderr)
        return 2


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if has_tk(sys.executable):
        from . import gui
        return gui.main(argv)

    print("当前 Python 解释器没有可用的 tkinter，正在查找其它解释器…")
    exe = find_gui_interpreter()
    if exe and os.path.realpath(exe) != os.path.realpath(sys.executable):
        print(f"找到可用解释器：{exe}")
        return _relaunch(exe, argv)

    print(
        "\n未找到带 tkinter 的 Python，图形界面无法启动。\n\n"
        "可以这样做（任选其一）：\n"
        "  1. 用 Windows 官方版 Python 运行本命令 —— 官方版自带 tkinter；\n"
        "  2. 若用的是 Linux 发行版，安装 tk 组件：\n"
        "       Debian/Ubuntu:  sudo apt install python3-tk\n"
        "       Fedora/RHEL:    sudo dnf install python3-tkinter\n"
        "  3. 直接用命令行工具，功能完全相同：\n"
        "       sy 运行 <文件.sy>\n"
        "       sy 检查 <文件.sy>\n"
        "       sy 格式化 <文件.sy> --写入\n"
        "       sy 词法 <文件.sy> / sy 语法树 <文件.sy>\n",
        file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.exit(main())