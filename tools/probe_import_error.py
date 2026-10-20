"""枢言 —— 「引入一个含语法错的模块」探针。

背景
----
`sy 检查` 一个引用了**语法有错**的模块时，语义分析器要深入那个模块做
导入分析。若它用的是 `Parser.parse()`（一见诊断就 `raise ParseError`），
而调用处没有捕获，宿主异常就会直接冒到用户面前 —— 一段 **Python 堆栈**。

判定（本项目的唯一基准）
------------------------
宿主异常一律算失败（CRASH）。用户看到的应该是**枢言诊断**：
行号、列号、诊断码、源码摘录、修复建议 —— 而不是 `Traceback (most recent call last)`。

做法
----
搭两个工程，各自让主程序引一个「有问题」的模块：

  甲：被引模块**语法错**（行首运算符造 D2012）
  乙：被引模块**语义错**（调未定义函数造 D3005）
  丙：被引模块**词法错**（未闭合字符串）

对每个都跑 `sy 检查 主程序.sy`，检查输出里有没有 Python 堆栈特征
（`Traceback` / `File "…` / `Error:` 之类）。

用法：
    python tools/probe_import_error.py
"""

from __future__ import annotations

import os
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


def 写(路径: str, 内容: str) -> None:
    os.makedirs(os.path.dirname(路径), exist_ok=True)
    with open(路径, "w", encoding="utf-8", newline="\n") as f:
        f.write(内容)


def 跑(参数: list[str], cwd: str) -> tuple[int, str]:
    r = subprocess.run([PY, os.path.join(ROOT, "sy.py"), *参数],
                       capture_output=True, text=True, cwd=cwd,
                       encoding="utf-8", errors="replace")
    return r.returncode, ((r.stdout or "") + (r.stderr or ""))


def main() -> int:
    情形 = [
        ("语法错（行首运算符）",
         "请 坏()\n    返回 1\n        + 2\n结束\n"),
        ("语义错（调未定义函数）",
         "请 坏()\n    返回 不存在的函数(1)\n结束\n"),
        ("词法错（未闭合字符串）",
         "请 坏()\n    返回 \"没关引号\n结束\n"),
    ]
    失败 = 0
    print("枢言 · 「引入含错模块」宿主崩溃探针")
    print("=" * 62)
    for 名, 坏模块 in 情形:
        根 = tempfile.mkdtemp(prefix="sy引入")
        try:
            写(os.path.join(根, "坏模块.sy"), 坏模块)
            写(os.path.join(根, "主程序.sy"),
               '引入 "坏模块"\n输出("我引了它")\n')
            码, 出 = 跑(["检查", os.path.join(根, "主程序.sy")], 根)
            是堆栈 = any(特 in 出 for 特 in 堆栈特征)
            if 是堆栈:
                失败 += 1
                print(f"\n✗ {名}")
                print("  宿主异常冒到用户面前（应为枢言诊断）：")
                for 行 in 出.strip().splitlines()[-6:]:
                    print("    " + 行)
            else:
                print(f"\n✓ {名}")
                # 展示给用户看到的是什么（应为带码的诊断）
                首 = [x for x in 出.strip().splitlines()
                      if "错误" in x or "D2" in x or "D3" in x or "D1" in x]
                for 行 in 首[:3]:
                    print("    " + 行)
        finally:
            shutil.rmtree(根, ignore_errors=True)

    print("\n" + "=" * 62)
    print(f"共 {len(情形)} 项：通过 {len(情形) - 失败}，失败（宿主异常）{失败}")
    return 1 if 失败 else 0


if __name__ == "__main__":
    sys.exit(main())