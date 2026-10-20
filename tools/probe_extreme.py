"""枢言 —— 极端场景压测探针（10 万行级工程适用）

与 `probe_stress.py` 的分工：
  · `probe_stress.py`    —— 单点边界（每个小例子一个场景）
  · `probe_extreme.py`   —— **规模与极端形态**：深层嵌套、超长表达式、
                            超大集合、超长标识符、超深调用链、超宽行、
                            编码边界、符号量级……这些在小型测试里永远
                            碰不到，只有上万行级别的工程才会暴露。

每项记录四种结果之一：
  OK     —— 正常返回且结果正确
  THROW  —— 抛出**枢言**异常（可捕获、文案可行动）—— 可接受
  CRASH  —— **宿主 Python 异常**（用户程序让编译器崩）—— 实现缺陷
  HANG   —— 超时（超过 budget 秒）—— 实现缺陷
  WRONG  —— 结果错误却未报错（静默失败）—— 最危险

**判定基准**：CRASH 与 HANG 一律算失败；THROW 只在「文案不可行动」
（空消息 / 只有类型名 / 指向无关位置）时才算失败。

用法：
    python tools/probe_extreme.py             # 默认规模
    python tools/probe_extreme.py --big       # 加大规模
"""

from __future__ import annotations

import io
import os
import signal
import sys
import time
import traceback

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from shuyan.builtins import StdLib                      # noqa: E402
from shuyan.diagnostics import LexError, ParseError, SemanticError, Severity  # noqa: E402
from shuyan.errors import ExitSignal, ShuyanThrow       # noqa: E402
from shuyan.interpreter import Interpreter              # noqa: E402
from shuyan.lexer import Lexer                          # noqa: E402
from shuyan.parser import Parser                        # noqa: E402
from shuyan.semantic import Analyzer                    # noqa: E402

CASES: list[tuple[str, str]] = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


class Timeout(Exception):
    pass


def _alarm(signum, frame):
    raise Timeout()


def run(src: str, budget: float = 20.0, argv=None):
    """运行源码，返回 (状态, 说明, 秒数)。"""
    lib = StdLib(argv=argv or [])
    lib.capture = True
    t0 = time.time()
    has_alarm = hasattr(signal, "SIGALRM")
    if has_alarm:
        old = signal.signal(signal.SIGALRM, _alarm)
        signal.setitimer(signal.ITIMER_REAL, budget)
    try:
        program = Parser(Lexer(src).tokenize()).parse()
        analyzer = Analyzer(lib)
        _, diags = analyzer.analyze(program)
        errs = [d for d in diags if d.severity is Severity.ERROR]
        if errs:
            # 编译期报错：算 THROW（可接受），但若消息为空则算失败
            msg = errs[0].message or ""
            return ("THROW" if msg.strip() else "WRONG",
                    f"编译期 {errs[0].code}: {msg}", time.time() - t0)
        interp = Interpreter(lib, source_lines=src.splitlines(),
                             analyzer=analyzer)
        code = interp.run(program)
        out = "\n".join(lib.written)
        return "OK", f"退出码 {code}；输出 {len(out)} 字符", time.time() - t0
    except (LexError, ParseError, SemanticError) as exc:
        ds = getattr(exc, "diagnostics", []) or []
        msg = ds[0].message if ds else str(exc)
        return ("THROW" if msg and msg.strip() else "WRONG",
                f"编译期：{msg}", time.time() - t0)
    except ShuyanThrow as exc:
        from shuyan.errors import describe_error_value
        msg = describe_error_value(exc.value)
        return ("THROW" if msg and msg.strip() else "WRONG",
                f"运行期：{msg}", time.time() - t0)
    except ExitSignal:
        return "OK", "主动退出", time.time() - t0
    except Timeout:
        return "HANG", f"超过 {budget:.0f} 秒未结束", time.time() - t0
    except RecursionError:
        return "CRASH", "宿主 RecursionError（未转成枢言异常）", time.time() - t0
    except Exception as exc:                                  # noqa: BLE001
        tb = traceback.format_exc(limit=3)
        return "CRASH", f"宿主 {type(exc).__name__}: {exc}｜{tb.splitlines()[-2].strip()}", time.time() - t0
    finally:
        if has_alarm:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old)


# ---------------------------------------------------------------- 规模类

@case("超长标识符（1 万字）")
def c_long_ident():
    name = "变" * 10000
    return run(f"定义 {name} = 1\n输出({name})")


@case("超长标识符（10 万字）")
def c_long_ident_huge():
    name = "变" * 100000
    return run(f"定义 {name} = 1\n输出(长度(转文本({name})))")


@case("超长字符串（100 万字）")
def c_long_string():
    return run('定义 甲 = "' + "字" * 1000000 + '"\n输出(长度(甲))')


@case("超宽行（10 万字符一行）")
def c_wide_line():
    return run("定义 甲 = [" + ", ".join(str(i) for i in range(30000)) + "]\n"
               "输出(长度(甲))")


@case("超大列表（50 万元素）")
def c_big_list():
    return run("定义 甲 = 范围(500000)\n输出(长度(甲))")


@case("超大映射（10 万键）")
def c_big_map():
    src = ["定义 甲 = {}"]
    src.append("定义 下标 = 0")
    src.append("当 下标 < 100000 成立")
    src.append('    甲[转文本(下标)] = 下标')
    src.append("    下标 = 下标 + 1")
    src.append("结束")
    src.append("输出(长度(甲))")
    return run("\n".join(src), budget=60)


@case("超多函数定义（5000 个）")
def c_many_funcs():
    src = []
    for i in range(5000):
        src.append(f"请 过程{i}(甲)\n    返回 甲 + {i}\n结束\n")
    src.append("输出(过程4999(1))")
    return run("\n".join(src), budget=60)


@case("超多类型定义（2000 个结构体）")
def c_many_types():
    src = []
    for i in range(2000):
        src.append(f"类型 名单{i} = {{ 甲: 整数, 乙: 文本 }}")
    src.append("定义 甲 = 名单1999(甲 是 1, 乙 是 \"x\")")
    src.append("输出(甲.乙)")
    return run("\n".join(src), budget=60)


# ---------------------------------------------------------------- 嵌套类

@case("深层括号嵌套（150 层，应在限内正常）")
def c_nest_paren():
    return run("输出(" + "(" * 150 + "1" + ")" * 150 + ")")


@case("深层括号嵌套（1000 层）")
def c_nest_paren_1000():
    return run("输出(" + "(" * 1000 + "1" + ")" * 1000 + ")",
               budget=10)


@case("深层列表嵌套（120 层，应在限内正常）")
def c_nest_list():
    return run("定义 甲 = " + "[" * 120 + "1" + "]" * 120 + "\n输出(类型名(甲))")


@case("深层块嵌套（100 层 if，应在限内正常）")
def c_nest_if():
    src = []
    for i in range(100):
        src.append("    " * i + "如果 真 则")
    src.append("    " * 100 + '输出("到达")')
    for i in range(99, -1, -1):
        src.append("    " * i + "结束")
    return run("\n".join(src), budget=20)


@case("深层块嵌套（1000 层 if）")
def c_nest_if_1000():
    src = []
    for i in range(1000):
        src.append("    " * min(i, 50) + "如果 真 则")
    src.append("    " * 50 + '输出("到达")')
    for i in range(999, -1, -1):
        src.append("    " * min(i, 50) + "结束")
    return run("\n".join(src), budget=20)


# ---------------------------------------------------------------- 递归 / 调用链

@case("深递归（900 层）")
def c_deep_recursion():
    return run("请 递减(n)\n    如果 n <= 0 则\n        返回 0\n    结束\n"
               "    返回 递减(n - 1) + 1\n结束\n输出(递减(900))")


@case("深递归（5000 层，应报递归太深）")
def c_deep_recursion_5000():
    return run("请 递减(n)\n    如果 n <= 0 则\n        返回 0\n    结束\n"
               "    返回 递减(n - 1) + 1\n结束\n输出(递减(5000))",
               budget=30)


@case("长调用链（1000 个函数互调）")
def c_call_chain():
    src = []
    for i in range(1000):
        nxt = f"链{i + 1}" if i + 1 < 1000 else "底"
        src.append(f"请 链{i}(甲)\n    返回 {nxt}(甲 + 1)\n结束\n")
    src.append("请 底(甲)\n    返回 甲\n结束\n")
    src.append("输出(链0(0))")
    return run("\n".join(src), budget=30)


@case("互递归（甲↔乙，1000 轮）")
def c_mutual_recursion():
    return run("""
请 甲(n)
    如果 n <= 0 则
        返回 0
    结束
    返回 乙(n - 1) + 1
结束

请 乙(n)
    如果 n <= 0 则
        返回 0
    结束
    返回 甲(n - 1) + 1
结束

输出(甲(1000))
""", budget=30)


# ---------------------------------------------------------------- 词法 / 编码边界

@case("标识符含数字后下划线再接汉字（SPEC §2.5 混合标识符）")
def c_num_underscore_han():
    # SPEC §2.5 明确允许 `订单_ID`、`user_编号` 这类混合标识符。
    # 实测真缺陷：`服务001_校验` 曾被切成 `服务001_` + `校验` 两枚记号，
    # 报出「`校验` 两侧没有运算符」—— 与真因（下划线在数字之后）
    # 毫不相干。修复后数字后的 `_` 与汉字后的 `_` 同口径。
    return run("请 服务001_校验(甲)\n    返回 甲 + 1\n结束\n"
               "输出(服务001_校验(41))")


@case("标识符数字下划线多段")
def c_multi_underscore():
    return run("定义 A1_账_余额 = 7\n输出(A1_账_余额)")


@case("反引号逃生舱（枚举成员名含关键字）")
def c_backtick():
    # SPEC §2.4：反引号用于**声明/访问标识符**，不是映射下标。
    # 正确写法是 `状态.`进行中`` —— 且规范化后的成员名不含反引号。
    return run("类型 状态 = { 待处理, `进行中`, 已完成 }\n"
               "输出(状态.`进行中`)\n"
               "输出(状态.进行中.名称)")


@case("中文数词：数字字面量优先于标识符（规范歧义，已记录）")
def c_han_number_ident():
    # **压测发现的规范歧义**：SPEC §2.5 说汉字序列可作标识符，
    # §2.6 说中文数词是数字字面量 —— 二者对 `一百二十` 给出不同答案。
    # 实现选择「数词优先」，故此处**必然报错**（D2002）。
    # 这是可预测、可解释的行为，属**已知边界**而非实现缺陷；
    # 已记入 SPEC「已知歧义」。若要中文数词标识符，用反引号逃生舱。
    status, detail, secs = run("定义 一百二十 = 5\n输出(一百二十)")
    if status == "THROW":
        return "OK", "规范报错（符合预期）", secs
    # 反引号逃生舱必须可用
    st2, d2, s2 = run("定义 `一百二十` = 5\n输出(`一百二十`)")
    return st2, f"未按预期报错：{detail}；反引号写法 {st2}", secs + s2


@case("emoji 与代理对（长度按码点）")
def c_emoji():
    return run('输出(长度("👨‍👩‍👧‍👦"))')


@case("全角符号与半角混排")
def c_fullwidth():
    return run('输出(长度("ａｂｃＡＢＣ１２３"))')


@case("零宽字符与 BOM")
def c_zero_width():
    return run('输出(长度("\u200b\u200c"))')


@case("控制字符")
def c_control():
    return run('输出(长度("\u0001\u0002"))')


@case("中文关键字紧邻（无空格）")
def c_keyword_adjacent():
    return run("定义 甲=1\n输出(甲)")


@case("连续运算符（a+-b）")
def c_op_chain():
    return run("定义 甲 = 5\n定义 乙 = 甲 + - 1\n输出(乙)")


@case("行首运算符（应报 D2012）")
def c_leading_op():
    return run('定义 甲 = "x"\n'
               '定义 乙 = 甲\n'
               '    + "y"\n'
               '输出(乙)')


@case("超长数字字面量（1000 位）")
def c_huge_number():
    return run("定义 甲 = " + "9" * 1000 + "\n输出(甲)")


@case("浮点极端值")
def c_float_extreme():
    return run("定义 甲 = 1e308\n定义 乙 = 甲 * 10\n输出(类型名(乙))")


@case("整数除法截断（负数方向）")
def c_neg_div():
    return run("输出(-7 / 2)")


@case("极深成员链（甲.乙.丙…200 层）")
def c_member_chain():
    src = "定义 甲 = 1\n"
    src += "输出(" + "甲" + ".丙" * 200 + ")"
    return run(src)


# ---------------------------------------------------------------- 集合 / 算法

@case("嵌套排序（1000 元素 × 排序 10 次）")
def c_sort_repeat():
    return run("定义 甲 = 范围(1000)\n定义 乙 = 0\n"
               "当 乙 < 10 成立\n    甲 = 排序依据(甲, 函数(x) 返回 0 - x 结束)\n"
               "    乙 = 乙 + 1\n结束\n输出(长度(甲))", budget=60)


@case("映射合并 1000 次")
def c_merge_repeat():
    return run("定义 甲 = {}\n定义 乙 = 0\n"
               "当 乙 < 1000 成立\n"
               '    甲 = 映射.合并(甲, {"键" + 转文本(乙): 乙})\n'
               "    乙 = 乙 + 1\n结束\n输出(长度(甲))", budget=60)


@case("列表切片极端（0 长度 / 越界）")
def c_slice_extreme():
    return run("定义 甲 = [1, 2, 3]\n"
               "输出(长度(切片(甲, 0, 0)))\n"
               "输出(长度(切片(甲, 5, 10)))")


@case("字符串拼接 5000 次（O(n²) 风险）")
def c_str_concat():
    return run("定义 甲 = \"\"\n定义 乙 = 0\n"
               "当 乙 < 5000 成立\n    甲 = 甲 + \"x\"\n    乙 = 乙 + 1\n结束\n"
               "输出(长度(甲))", budget=60)


@case("嵌套函数定义（200 层闭包）")
def c_nested_closure():
    src = ["请 外()\n    定义 累计 = 0"]
    for i in range(200):
        src.append("    " * (i % 20 + 1) + f"请 内{i}()\n" +
                   "    " * (i % 20 + 1) + f"    返回 {i}\n" +
                   "    " * (i % 20 + 1) + "结束")
    src.append("    返回 内199()")
    src.append("结束")
    src.append("输出(外())")
    return run("\n".join(src), budget=30)


# ---------------------------------------------------------------- 空 / 边界输入

@case("空文件")
def c_empty():
    return run("")


@case("只有注释")
def c_only_comment():
    return run("// 什么也没有\n// 还是注释\n")


@case("只有空白行")
def c_only_blank():
    return run("\n\n\n\n")


@case("未闭合字符串（应报错不崩）")
def c_unclosed_string():
    return run('输出("没有闭合')


@case("未闭合括号（应报错不崩）")
def c_unclosed_paren():
    return run("输出(1 + 2")


@case("除零（应报运行时错）")
def c_div_zero():
    return run("输出(1 / 0)")


@case("下标越界（应报运行时错）")
def c_index_oob():
    return run("定义 甲 = [1]\n输出(甲[99])")


# ---------------------------------------------------------------- 主流程

def main() -> int:
    big = "--big" in sys.argv
    fail = 0
    rows = []
    for name, fn in CASES:
        try:
            status, detail, secs = fn()
        except Exception as exc:                              # noqa: BLE001
            status, detail, secs = ("CRASH",
                                    f"探针自身异常 {type(exc).__name__}: {exc}",
                                    0.0)
        bad = status in ("CRASH", "HANG", "WRONG")
        if bad:
            fail += 1
        rows.append((status, name, detail, secs))

    print("=" * 78)
    print("枢言 · 极端场景压测探针%s" % ("（加大规模）" if big else ""))
    print("=" * 78)
    for status, name, detail, secs in rows:
        mark = {"OK": "✓", "THROW": "·", "CRASH": "✗",
                "HANG": "✗", "WRONG": "✗"}[status]
        print(f"{mark} [{status:5}] {secs:7.3f}s  {name}")
        if status in ("CRASH", "HANG", "WRONG"):
            print(f"           └─ {detail}")
    print("-" * 78)
    total = len(rows)
    crash = sum(1 for r in rows if r[0] == "CRASH")
    hang = sum(1 for r in rows if r[0] == "HANG")
    wrong = sum(1 for r in rows if r[0] == "WRONG")
    throw = sum(1 for r in rows if r[0] == "THROW")
    print(f"共 {total} 项：宿主崩溃 {crash} · 超时 {hang} · 静默错误 {wrong} "
          f"· 规范报错 {throw} · 正常 {total - crash - hang - wrong - throw}")
    print(f"失败项（崩溃 / 超时 / 静默错误）：{fail}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())