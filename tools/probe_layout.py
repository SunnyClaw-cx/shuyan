"""枢言 —— 分层工程的「检查口」与「运行口」口径探针。

要回答的问题
------------
真实的大厂项目一定是**分层目录**：

    工程/主程序.sy
    工程/框架/基础.sy
    工程/采购/服务.sy        ← 它要引入 框架/基础

中层模块写 `引入 "框架/基础"`（相对工程根，这是分层工程的自然写法）。
现在同时跑两条命令，看结论是否一致：

    sy 运行 工程/主程序.sy     ← 基准 = 工程/    （主脚本目录）
    sy 检查 工程/采购/服务.sy  ← 基准 = 工程/采购/（该文件目录）

**「检查通过」必须真能跑** 是本项目的硬约束（v0.3.0 假通过缺陷的教训）。
若两条命令给出相反结论，这个约束就守不住。

决定性做法
----------
`resolve_module_file` 的候选顺序是
    ① base_dir/<name>.sy      ② <name>.sy（相对进程 CWD）    ③ CWD/<name>.sy
候选 ① 落空后才能看出 ②③ 是否在起作用。故本探针**固定主脚本目录**、
**只切换进程 CWD**：

    cwd = 工程/     → ②③ 能命中 工程/框架/基础.sy
    cwd = 空白目录/ → ②③ 全落空

输出随 CWD 变化，即证明解析**受当前工作目录影响** ——
而 SPEC §3.8.1 第 1 条明文写着「**不以当前工作目录为准**」。

用法：
    python tools/probe_layout.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable


class 计数:
    def __init__(self) -> None:
        self.ok = 0
        self.fail = 0
        self.msg: list[str] = []

    def 检(self, 名: str, 条件: bool, 附: str = "") -> None:
        if 条件:
            self.ok += 1
            print(f"  ✓ {名}")
        else:
            self.fail += 1
            self.msg.append(f"{名}  {附}")
            print(f"  ✗ {名}   {附}")


def 写(路径: str, 内容: str) -> None:
    os.makedirs(os.path.dirname(路径), exist_ok=True)
    with open(路径, "w", encoding="utf-8", newline="\n") as f:
        f.write(内容)


def 跑(参数: list[str], cwd: str) -> tuple[int, str]:
    r = subprocess.run([PY, os.path.join(ROOT, "sy.py"), *参数],
                       capture_output=True, text=True, cwd=cwd,
                       encoding="utf-8", errors="replace")
    return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()


def 建分层工程(根: str) -> dict:
    """框架/基础.sy ← 采购/服务.sy ← 主程序.sy，引入一律相对**工程根**。"""
    P = os.path.join(根, "工程")
    写(os.path.join(P, "框架", "基础.sy"),
       "请 加一(甲)\n    返回 甲 + 1\n结束\n")
    写(os.path.join(P, "采购", "服务.sy"),
       '引入 "框架/基础"\n\n'
       '请 加二(甲)\n    返回 基础.加一(基础.加一(甲))\n结束\n')
    写(os.path.join(P, "主程序.sy"),
       '引入 "采购/服务"\n输出(服务.加二(40))\n')
    return {
        "工程": P,
        "主程序": os.path.join(P, "主程序.sy"),
        "中层": os.path.join(P, "采购", "服务.sy"),
        "底层": os.path.join(P, "框架", "基础.sy"),
    }


def main() -> int:
    c = 计数()
    print("枢言 · 分层工程「检查口 / 运行口」口径探针")
    print("=" * 62)

    根 = tempfile.mkdtemp(prefix="sy分层")
    try:
        g = 建分层工程(根)
        空白 = os.path.join(根, "空白")
        os.makedirs(空白, exist_ok=True)

        print("\n【一、运行口：主脚本在工程根，基准 = 工程根】")
        码运, 出运 = 跑(["运行", g["主程序"]], g["工程"])
        c.检("主程序可运行，输出 42", 码运 == 0 and "42" in 出运,
             f"码={码运} 出={出运[-160:]!r}")
        c.检("中间模块被正确加载（引入按工程根写得通）", "没有定义" not in 出运,
             f"{出运[-160:]!r}")

        print("\n【二、检查口（cwd = 工程根）】")
        码查工, 出查工 = 跑(["检查", g["中层"]], g["工程"])
        c.检("中层模块检查通过", 码查工 == 0,
             f"码={码查工} 出={出查工[-200:]!r}")

        print("\n【三、检查口（cwd = 空白目录）—— 同行同文件，只换 cwd】")
        码查空, 出查空 = 跑(["检查", g["中层"]], 空白)
        c.检("中层模块检查仍通过（**预期做不到**）", 码查空 == 0,
             "已知缺口：候选①落空后回退到 CWD，故结论随 cwd 变化")

        同口径 = (码查工 == 码查空)
        c.检("检查结论不受 cwd 影响（SPEC §3.8.1 第 1 条）", 同口径,
             f"cwd=工程根 得 {码查工}；cwd=空白 得 {码查空}")

        if not 同口径:
            print("\n  ── 缺口复现 ──")
            失败行 = [x for x in 出查空.splitlines()
                     if "找不到模块" in x or "D3005" in x]
            for 行 in 失败行[:4]:
                print("    " + 行)
            print(f"  cwd=工程根 → 检查通过；cwd=别处 → 报错。")
            print("  同一份源码、同一台机器，结论取决于进程当前目录。")

        print("\n【四、结论一致性：检查口 vs 运行口】")
        c.检("「检查通过」与「能运行」结论一致", (码查工 == 0) == (码运 == 0),
             "两端口径不同即违反「检查通过必须真能跑」")

        print("\n" + "=" * 62)
        print(f"共 {c.ok + c.fail} 项：通过 {c.ok}，失败 {c.fail}")
        if c.msg:
            print("\n失败项：")
            for m in c.msg:
                print("  ✗ " + m)
        print("\n说明：失败项集中在「三」「四」两节，指向同一个能力缺口 ——")
        print("      SPEC §3.8.1 只定义了「基准 = 主脚本目录」，")
        print("      实现却额外回退了进程 CWD；两者相加导致：")
        print("      ① 解析结果依赖 cwd（规范明文禁止）；")
        print("      ② 分层工程无法「逐文件独立检查」；")
        print("      ③ 检查口与运行口基准不同，结论可能相反。")
        return 1 if c.fail else 0
    finally:
        shutil.rmtree(根, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())