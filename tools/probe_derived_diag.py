"""枢言 —— 「同一位置衍生报错未抑制」探针。

背景
----
枢言的具名参数写作 `名字 是 值`（SPEC §4.3.1）。在**参数列表内部**，
`标识符 是 表达式` 一律判为**具名参数**，而不是相等比较。这是确定性
规则，代价是作者要改用 `等于`。

但这个规则会产生一个**衍生后果**：`转文本(甲 是 乙)` 里位置参数个数
是 0（`甲 是 乙` 整段成了具名参数）。若参数个数检查（`_check_arity`）
只看 `len(node.args)`，就会先报一条

    函数「转文本」需要 1 个参数，但这里给了 0 个。

再报一条真因

    内置函数「转文本」不支持具名参数，参数「甲」会被忽略。

**第一条是错的方向**：它引着用户去「补参数」，而真因是「不该用 `是`
写比较」。这正是本项目「报错点 ≠ 真因点」的经典形态 —— 报错点正确、
真因提示错误，用户按提示改完还是错。

正确口径（SPEC §8.2.5「同一语句已报错须抑制衍生报错」）
----------------------------------------------------
内置函数**没有参数名**，任何具名参数都不属于它的形参 ⇒
「位置参数不足」是具名参数的**必然后果**，不是独立错误。
所以内置函数路径下，**只要出现具名参数，就不做参数个数检查**，
把诊断统一交给「不支持具名参数」这一条。

判定（本项目的唯一基准）
------------------------
宿主异常一律算失败（CRASH）。此外本探针额外判定：
**同一 (行, 列) 上是否出现两条 D3007**。若出现，即为衍生报错未抑制。

用法：
    python tools/probe_derived_diag.py
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable

堆栈特征 = ("Traceback (most recent call last)",
            "shuyan.diagnostics.",
            "  File \"",
            "Error: ")

# 情形：(名称, 源码, 期望在输出里看到的关键词, 期望不得出现的关键词)
情形 = [
    ("内置函数收具名参数（核心）",
     '定义 甲 = 3\n定义 乙 = 3\n输出(转文本(甲 是 乙))\n',
     "不支持具名参数",
     "需要 1 个参数"),
    ("内置函数收具名参数（可变参）",
     '定义 甲 = 1\n输出(甲 是 5)\n',
     "不支持具名参数",
     None),
    ("内置模块成员收具名参数（关键字成员名）",
     '定义 甲 = "abc"\n定义 乙 = 3\n输出(文本.重复(甲 是 乙))\n',
     "不支持具名参数",
     None),
    ("用户函数收具名参数（正常语义，不应报错）",
     '请 取(值)\n    返回 值\n结束\n'
     '定义 甲 = 3\n输出(取(值 是 甲))\n',
     None,
     "不支持具名参数"),
    # ---- 参数类型标注必须真校验（对标真实工程后补齐，v0.4.1）----
    ("参数标了类型就必须校验（实参不符）",
     '请 f(数量: 整数)\n    返回 数量\n结束\nf([1, 2])\n',
     "参数「数量」需要「整数」",
     None),
    ("回调参数标了函数就不能收非函数",
     '请 f(回调: 函数)\n    返回 1\n结束\nf(123)\n',
     "需要「函数」",
     None),
    ("兼容的调用不得被误报（含拓宽 整数→文本）",
     '请 f(名字: 文本)\n    返回 名字\n结束\n输出(f(123))\n',
     None,
     "D3004"),
]


def 写(路径: str, 内容: str) -> None:
    os.makedirs(os.path.dirname(路径), exist_ok=True)
    with open(路径, "w", encoding="utf-8", newline="\n") as f:
        f.write(内容)


def 跑(参数: list[str], cwd: str) -> tuple[int, str]:
    进程 = subprocess.run([PY, os.path.join(ROOT, "sy.py"), *参数],
                        cwd=cwd, capture_output=True)
    出 = (进程.stdout or b"").decode("utf-8", "replace")
    出 += (进程.stderr or b"").decode("utf-8", "replace")
    return 进程.returncode, 出


def 诊断位置(出: str) -> list[tuple[int, int, str]]:
    """抽出所有「第 L 行，第 C 列：… （Dxxxx）」的位置与码。"""
    结果 = []
    for m in re.finditer(r"第 (\d+) 行，第 (\d+) 列：.*?（(D\d{4})）", 出):
        结果.append((int(m.group(1)), int(m.group(2)), m.group(3)))
    return 结果


def main() -> int:
    print("枢言 · 衍生报错抑制 + 假通过 探针")
    print("=" * 62)
    失败 = 0
    for 名, 源码, 要出现, 不得出现 in 情形:
        根 = tempfile.mkdtemp(prefix="sy衍生")
        try:
            写(os.path.join(根, "样例.sy"), 源码)
            码检查, 出检查 = 跑(["检查", os.path.join(根, "样例.sy")], 根)
            码运行, 出运行 = 跑(["运行", os.path.join(根, "样例.sy")], 根)

            问题: list[str] = []
            for 出 in (出检查, 出运行):
                if any(特 in 出 for 特 in 堆栈特征):
                    问题.append("宿主异常冒到用户面前（应为枢言诊断）")

            # **假通过校验**：`检查` 报 0 错，但 `运行` 出了运行时错误
            # ⇒ 编译期与运行期口径不一致。这是本项目最危险的形态。
            检查零错 = "0 个错误" in 出检查 or "没有发现问题" in 出检查
            运行有错 = "运行时错误" in 出运行 or "D4" in 出运行
            if 检查零错 and 运行有错:
                问题.append("假通过：检查报 0 错，运行却报错（编译期与"
                          "运行期口径不一致）")

            # 同一 (行, 列) 上出现两条诊断 ⇒ 衍生报错未抑制
            位置 = 诊断位置(出检查)
            计数: dict[tuple[int, int], int] = {}
            for L, C, _ in 位置:
                计数[(L, C)] = 计数.get((L, C), 0) + 1
            重复 = [k for k, v in 计数.items() if v > 1]
            if 重复:
                问题.append("同一位置报了两条诊断（衍生报错未抑制）："
                          + "、".join(f"第{L}行第{C}列" for L, C in 重复))

            if 要出现 and 要出现 not in 出检查:
                问题.append(f"输出里没有期望关键词「{要出现}」")
            if 不得出现 and 不得出现 in 出检查:
                问题.append(f"出现了不该有的关键词「{不得出现}」")

            if 问题:
                失败 += 1
                print(f"\n✗ {名}")
                for p in 问题:
                    print("  · " + p)
                for 行 in 出检查.strip().splitlines()[:8]:
                    print("    " + 行)
            else:
                print(f"\n✓ {名}")
                首 = [x for x in 出检查.strip().splitlines()
                      if "错误" in x or "D3" in x or "D2" in x]
                for 行 in 首[:2]:
                    print("    " + 行)
        finally:
            shutil.rmtree(根, ignore_errors=True)

    print("\n" + "=" * 62)
    print(f"共 {len(情形)} 项：通过 {len(情形) - 失败}，失败 {失败}")
    return 1 if 失败 else 0


if __name__ == "__main__":
    sys.exit(main())