"""枢言（Shuyan）性能基准套件。

**为什么需要它**：一门语言的「够不够用」不只取决于功能是否完整，还取决于
它在真实写法下的速度。基准的作用不是刷数字，而是**发现量级异常** ——
某个能力比其他能力慢一两个数量级，几乎必然是实现缺陷而非「语言特性」。

**测量口径（重要）**：
枢言是解释器，一次运行包含三段独立开销：
    ① 词法 → ② 语法 → ③ 语义分析（合称「编译期」）→ ④ 执行（「运行期」）
本套件**分别计时**。只看总耗时会被编译期掩盖：一段只循环 1000 次的程序，
总耗时的 90% 可能其实是启动开销。分开测量才能看清真正的执行效率。

**与正式链路同口径**：调用 `Lexer` / `Parser` / `Analyzer` / `Interpreter`，
与 `sy 运行` 走同一条路径，不使用任何捷径 —— 否则量的就不是用户会遇到的性能。

用法：
    python benchmarks/run_bench.py            # 完整基准
    python benchmarks/run_bench.py --quick    # 冒烟（CI 用，只跑 10% 规模）
    python benchmarks/run_bench.py --json     # 输出 JSON（供脚本消费）
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from shuyan.builtins import StdLib          # noqa: E402
from shuyan.interpreter import Interpreter  # noqa: E402
from shuyan.lexer import Lexer              # noqa: E402
from shuyan.parser import Parser            # noqa: E402
from shuyan.semantic import Analyzer        # noqa: E402

# ============================================================ 基准用例


def _src_loop(n: int) -> str:
    """纯数值循环：测最基础的语句分派与算术开销。"""
    return f"""
定义 累加 = 0
定义 甲 = 0
当 甲 小于 {n}
    累加 = 累加 + 甲
    甲 = 甲 + 1
结束
输出(累加)
"""


def _src_function(n: int) -> str:
    """函数调用：测调用栈开销。函数调用是解释器最贵的动作之一。"""
    return f"""
请 加一(数)
    返回 数 + 1
结束
定义 甲 = 0
当 甲 小于 {n}
    甲 = 加一(甲)
结束
输出(甲)
"""


def _src_recursion(n: int) -> str:
    """递归：测递归深度与栈帧开销（对比循环的差距很能说明问题）。"""
    return f"""
请 斐波(数)
    如果 数 小于 2 则
        返回 数
    结束
    返回 斐波(数 - 1) + 斐波(数 - 2)
结束
输出(斐波({n}))
"""


def _src_string(n: int) -> str:
    """文本拼接：测字符串构建与内存分配。"""
    return f"""
定义 缓冲 = ""
定义 甲 = 0
当 甲 小于 {n}
    缓冲 = 缓冲 + "字"
    甲 = 甲 + 1
结束
输出(长度(缓冲))
"""


def _src_chinese(n: int) -> str:
    """中文处理：测字符计数与显示宽度两条专用路径。

    这是本项目**最关心的性能点** —— 中文处理若退化成按字节或调用
    Python 的 unicodedata，会比纯 ASCII 慢一个数量级。
    """
    return f"""
定义 样本 = "数据中台的建设与治理"
定义 累计 = 0
定义 甲 = 0
当 甲 小于 {n}
    累计 = 累计 + 长度(样本) + 文本.显示宽度(样本)
    甲 = 甲 + 1
结束
输出(累计)
"""


def _src_list(n: int) -> str:
    """列表操作：测容器增删与下标访问。"""
    return f"""
定义 数据 = []
定义 甲 = 0
当 甲 小于 {n}
    追加元素(数据, 甲 * 2)
    甲 = 甲 + 1
结束
定义 合计 = 0
遍历 数据 中的每个 项
    合计 = 合计 + 项
结束
输出(合计)
"""


def _src_map(n: int) -> str:
    """映射操作：测哈希查找（与列表的顺序访问对比）。"""
    return f"""
定义 表 = {{}}
定义 甲 = 0
当 甲 小于 {n}
    表["键" + 转文本(甲)] = 甲
    甲 = 甲 + 1
结束
定义 合计 = 0
定义 乙 = 0
当 乙 小于 {n}
    合计 = 合计 + 表["键" + 转文本(乙)]
    乙 = 乙 + 1
结束
输出(合计)
"""


# 名称、构造函数、完整规模、冒烟规模
CASES: list[tuple[str, object, int, int]] = [
    ("循环·数值",    _src_loop,      100_000, 10_000),
    ("函数·调用",    _src_function,   50_000,  5_000),
    ("递归·斐波那契", _src_recursion,     20,      18),
    ("文本·拼接",    _src_string,     20_000,  2_000),
    ("中文·字符计数", _src_chinese,   100_000, 10_000),
    ("列表·追加遍历", _src_list,      100_000, 10_000),
    ("映射·哈希查找", _src_map,       50_000,  5_000),
]


# ============================================================ 计时核心


def _compile(source: str, filename: str):
    """走完整编译期，返回 (program, analyzer, 耗时秒)。"""
    t0 = time.perf_counter()
    tokens = Lexer(source, filename).tokenize()
    program = Parser(tokens, filename).parse()
    analyzer = Analyzer(StdLib())
    analyzer.analyze(program)
    return program, analyzer, time.perf_counter() - t0


def measure(source: str, filename: str, rounds: int) -> dict:
    """测量单个用例，返回编译期/运行期各自的耗时统计。

    取**中位数**而非平均值：解释器的单次运行偶发受到系统调度、GC 干扰，
    平均值会被离群值拉偏；中位数更能代表「典型一次运行要多久」。
    """
    compile_times: list[float] = []
    run_times: list[float] = []

    # 先编译一次并保留，供「仅运行期」重复计时（避免把编译算进运行期）
    program, analyzer, compile_sec = _compile(source, filename)
    compile_times.append(compile_sec)

    for _ in range(rounds - 1):
        _, _, sec = _compile(source, filename)
        compile_times.append(sec)

    for _ in range(rounds):
        lib = StdLib()
        lib.capture = True                  # 不打印，避免 I/O 污染计时
        interp = Interpreter(lib, filename=filename,
                             source_lines=source.splitlines(),
                             analyzer=analyzer)
        t0 = time.perf_counter()
        interp.run(program)
        run_times.append(time.perf_counter() - t0)

    compile_med = statistics.median(compile_times)
    run_med = statistics.median(run_times)
    return {
        "编译期": compile_med,
        "运行期": run_med,
        "合计": compile_med + run_med,
        "编译期样本": compile_times,
        "运行期样本": run_times,
    }


def _fmt(sec: float) -> str:
    """人类可读的耗时（自动选单位，避免一屏 0.000123）。"""
    if sec >= 1:
        return f"{sec:.3f} s"
    if sec >= 0.001:
        return f"{sec * 1000:.2f} ms"
    return f"{sec * 1_000_000:.1f} µs"


# ============================================================ 主流程


def run(quick: bool, rounds: int, as_json: bool) -> int:
    results: list[dict] = []
    header = f"{'用例':<16}{'规模':>10}{'编译期':>14}{'运行期':>14}{'合计':>14}"
    rule = "─" * len(header)

    if not as_json:
        print()
        print("枢言性能基准" + ("（冒烟模式）" if quick else ""))
        print(rule)
        print(header)
        print(rule)

    failures = 0
    for name, factory, full_n, quick_n in CASES:
        n = quick_n if quick else full_n
        source = factory(n)
        filename = f"<基准·{name}>"
        try:
            m = measure(source, filename, rounds)
        except Exception as exc:                     # noqa: BLE001
            failures += 1
            if not as_json:
                print(f"{name:<16}{n:>10}  ✗ 运行失败：{exc}")
            results.append({"用例": name, "规模": n, "错误": str(exc)})
            continue

        results.append({
            "用例": name, "规模": n,
            "编译期秒": m["编译期"], "运行期秒": m["运行期"], "合计秒": m["合计"],
        })
        if not as_json:
            print(f"{name:<16}{n:>10}"
                  f"{_fmt(m['编译期']):>14}{_fmt(m['运行期']):>14}"
                  f"{_fmt(m['合计']):>14}")

    if as_json:
        print(json.dumps({
            "模式": "冒烟" if quick else "完整",
            "轮次": rounds,
            "环境": {
                "Python": platform.python_version(),
                "平台": platform.platform(),
                "处理器": platform.processor(),
            },
            "结果": results,
        }, ensure_ascii=False, indent=2))
        return 1 if failures else 0

    print(rule)
    if failures:
        print(f"⚠ {failures} 个用例运行失败。")
        return 1

    # 落盘（目录已在 .gitignore 中忽略，属本地产物）
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
    os.makedirs(out_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_path = os.path.join(out_dir, f"bench-{stamp}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "模式": "冒烟" if quick else "完整",
            "轮次": rounds,
            "Python": platform.python_version(),
            "平台": platform.platform(),
            "结果": results,
        }, f, ensure_ascii=False, indent=2)

    print(f"环境：Python {platform.python_version()} · {platform.platform()}")
    print(f"轮次：{rounds}（取中位数）")
    print(f"结果已写入：{os.path.relpath(out_path, ROOT)}")
    print()
    print("说明：编译期 = 词法 + 语法 + 语义分析；运行期 = 解释执行。")
    print("      两段分开计时，避免启动开销掩盖真实执行效率。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="枢言性能基准套件",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true",
                    help="冒烟模式：规模缩到 1/10，仅供 CI 验证可执行")
    ap.add_argument("--rounds", type=int, default=5,
                    help="每个用例的测量轮次（默认 5，取中位数）")
    ap.add_argument("--json", action="store_true",
                    help="以 JSON 输出（不写文件、不打印表格）")
    args = ap.parse_args()
    # 冒烟模式轮次也要降，否则 CI 仍要跑满 5 轮
    rounds = max(1, 2 if args.quick and args.rounds == 5 else args.rounds)
    return run(quick=args.quick, rounds=rounds, as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())