"""企业场景压测探针 —— 金额、状态机、排序、聚合、文本报表

针对「企业级应用」的真实高频需求，逐项探测语言是否够用：
  分/元金额、四舍五入与精度、多键排序、分组汇总、账龄分桶、
  文本对齐报表、状态机、不可变约束、可选值处理。

用法：python tools/probe_enterprise.py
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from shuyan.builtins import StdLib                      # noqa: E402
from shuyan.diagnostics import LexError, ParseError, SemanticError, Severity  # noqa: E402
from shuyan.errors import ExitSignal, ShuyanThrow       # noqa: E402
from shuyan.interpreter import Interpreter              # noqa: E402
from shuyan.lexer import Lexer                          # noqa: E402
from shuyan.parser import Parser                        # noqa: E402
from shuyan.semantic import Analyzer                    # noqa: E402

FAILS = []


def run(src):
    lib = StdLib()
    lib.capture = True
    try:
        program = Parser(Lexer(src).tokenize()).parse()
        analyzer = Analyzer(lib)
        _, diags = analyzer.analyze(program)
        errs = [d for d in diags if d.severity is Severity.ERROR]
        if errs:
            return "COMPILE", "%s: %s" % (errs[0].code, errs[0].message)
        interp = Interpreter(lib, source_lines=src.splitlines(), analyzer=analyzer)
        code = interp.run(program)
        return "OK", (code, "\n".join(lib.written))
    except (LexError, ParseError) as exc:
        return "COMPILE", [d.code for d in exc.diagnostics]
    except SemanticError as exc:
        return "COMPILE", [d.code for d in exc.diagnostics]
    except ShuyanThrow as exc:
        return "THROW", exc._describe()
    except ExitSignal as exc:
        return "OK", ("exit", exc.code)
    except Exception as exc:  # noqa: BLE001
        return "CRASH", "%s: %s" % (type(exc).__name__, exc)


def case(name, src, want=None):
    """want 为期望输出行（列表）或 None（只看不崩）。"""
    status, detail = run(src)
    ok = True
    note = ""
    if status == "CRASH":
        ok = False
        note = "★★ 宿主崩溃"
    elif status == "COMPILE":
        ok = False
        note = "☆ 编译期拒绝：%s" % detail
    elif want is not None:
        got = detail[1].split("\n") if isinstance(detail, tuple) else []
        got = [x for x in got if x != ""]
        if status == "THROW":
            ok = False
            note = "☆ 运行期异常：%s" % detail
        elif got != want:
            ok = False
            note = "☆ 结果不符：期望 %r，实得 %r" % (want, got)
    else:
        note = ("OK" if status == "OK" else "throws: %s" % detail)
    print("  [%s] %s%s" % ("OK" if ok else "FAIL", name,
                           "   " + note if not ok else ""))
    if not ok:
        FAILS.append((name, status, detail))


print("=" * 70)
print("一、金额与精度（企业最要紧）")
print("=" * 70)
case("分为单位整数运算", '输出(1999 * 3)', ["5997"])
case("四舍五入两位", '输出(四舍五入(1.005, 2))')
case("四舍五入舍入方向（真·四舍五入，5 一律进位）",
     '输出(四舍五入(2.5), 四舍五入(3.5), 四舍五入(-2.5))',
     ["3 4 -3"])
case("省略位数返回整数", '输出(类型名(四舍五入(2.5)), 四舍五入(1699.0))', ["整数 1699"])
case("指定位数返回小数", '输出(类型名(四舍五入(3.14159, 2)), 四舍五入(3.14159, 2))',
     ["小数 3.14"])
case("金额可直接赋给整数变量", '定义 金额: 整数 = 四舍五入(1999 * 85 / 100)\n输出(金额)',
     ["1699"])
case("金额转元显示", '输出(转文本(1999 / 100))', ["19.99"])
case("小数直接比较", '输出(0.1 + 0.2 是 0.3)', ["假"])
case("累加 100 次 0.01", '定义 s = 0.0\n重复 100 次\n    s = s + 0.01\n结束\n输出(s)', ["1.0"])
case("大金额累加", '定义 s = 0\n重复 10000 次\n    s = s + 199\n结束\n输出(s)', ["1990000"])

print()
print("=" * 70)
print("二、排序与多键排序")
print("=" * 70)
case("基本排序", '输出(排序([3,1,2]))', ["[1, 2, 3]"])
case("倒序", '输出(排序([3,1,2], 真))', ["[3, 2, 1]"])
case("按字段排序依据",
     '类型 单 = {金额: 整数}\n定义 表 = [单(金额 是 3), 单(金额 是 1)]\n'
     '定义 r = 排序依据(表, 函数(甲) 返回 甲.金额 结束)\n输出(r[0].金额, r[1].金额)',
     ["1 3"])
case("多键排序（先按部门再按金额）",
     '类型 单 = {部门: 文本, 金额: 整数}\n'
     '定义 表 = [单(部门 是 "乙", 金额 是 1), 单(部门 是 "甲", 金额 是 5), 单(部门 是 "甲", 金额 是 2)]\n'
     '定义 r = 排序依据(表, 函数(甲) 返回 甲.部门 + 文本.填充(转文本(甲.金额), 6, "0", "右") 结束)\n'
     '输出(r[0].部门, r[0].金额, r[1].部门, r[1].金额, r[2].部门, r[2].金额)',
     ["乙 1 甲 2 甲 5"])
case("排序依据 带倒序", '输出(排序依据([1,3,2], 函数(x) 返回 x 结束, 真))', ["[3, 2, 1]"])

print()
print("=" * 70)
print("三、分组聚合与账龄分桶")
print("=" * 70)
case("分组依据计数",
     '类型 单 = {客户: 文本}\n'
     '定义 表 = [单(客户 是 "甲"), 单(客户 是 "乙"), 单(客户 是 "甲")]\n'
     '定义 g = 分组依据(表, 函数(甲) 返回 甲.客户 结束)\n'
     '定义 计 = 计数依据(表, 函数(甲) 返回 甲.客户 结束)\n输出(计["甲"], 计["乙"])',
     ["2 1"])
case("映射值 汇总金额",
     '类型 单 = {客户: 文本, 金额: 整数}\n'
     '定义 表 = [单(客户 是 "甲", 金额 是 100), 单(客户 是 "乙", 金额 是 50), 单(客户 是 "甲", 金额 是 30)]\n'
     '定义 g = 分组依据(表, 函数(甲) 返回 甲.客户 结束)\n'
     '定义 汇总 = {}\n遍历 g 中的每个 键\n    定义 合计 = 0\n'
     '    遍历 g[键] 中的每个 项\n        合计 = 合计 + 项.金额\n    结束\n'
     '    汇总[键] = 合计\n结束\n输出(汇总["甲"], 汇总["乙"])',
     ["130 50"])
case("账龄分桶（区间边界）",
     '类型 单 = {账龄: 整数}\n'
     '定义 表 = [单(账龄 是 0), 单(账龄 是 30), 单(账龄 是 31), 单(账龄 是 90), 单(账龄 是 91)]\n'
     '定义 桶 = {"未到期": 0, "1-30天": 0, "31-90天": 0, "90天以上": 0}\n'
     '遍历 表 中的每个 项\n'
     '    如果 项.账龄 <= 0 则\n        桶["未到期"] = 桶["未到期"] + 1\n'
     '    否则若 项.账龄 <= 30 则\n        桶["1-30天"] = 桶["1-30天"] + 1\n'
     '    否则若 项.账龄 <= 90 则\n        桶["31-90天"] = 桶["31-90天"] + 1\n'
     '    否则\n        桶["90天以上"] = 桶["90天以上"] + 1\n'
     '    结束\n结束\n'
     '输出(桶["未到期"], 桶["1-30天"], 桶["31-90天"], 桶["90天以上"])',
     ["1 1 2 1"])

print()
print("=" * 70)
print("四、文本报表与对齐（中文表格）")
print("=" * 70)
case("中英混排对齐宽度", '输出(文本.显示宽度("销售额"), 文本.显示宽度("Sales"))', ["6 5"])
case("填充对齐（中文列）",
     '输出(文本.填充("客户", 10) + 文本.填充("金额", 10, " ", "右"))',
     ["客户            金额"])
case("数字千分位格式化", '输出(文本.格式化("{值:>10}", {"值": 1999}))')
case("文本格式化基础", '输出(文本.格式化("客户 {名} 欠款 {额} 元", {"名": "甲", "额": 1999}))',
     ["客户 甲 欠款 1999 元"])
case("多行报表渲染",
     '类型 行 = {客户: 文本, 金额: 整数}\n'
     '定义 表 = [行(客户 是 "甲公司", 金额 是 1999), 行(客户 是 "乙", 金额 是 50)]\n'
     '定义 文本列表 = 映射列表(表, 函数(甲) 返回 文本.填充(甲.客户, 10) + 转文本(甲.金额) 结束)\n'
     '输出(连接(文本列表, "\\n"))',
     ["甲公司    1999", "乙        50"])

print()
print("=" * 70)
print("五、状态机与业务规则")
print("=" * 70)
case("枚举状态机",
     '类型 状态 = {草稿, 已提交, 已审核, 已驳回}\n'
     '请 下一步(s)\n    如果 s 是 状态.草稿 则\n        返回 状态.已提交\n    结束\n'
     '    如果 s 是 状态.已提交 则\n        返回 状态.已审核\n    结束\n    返回 s\n结束\n'
     '输出(下一步(状态.草稿) 是 状态.已提交)',
     ["真"])
case("状态映射为中文名",
     '类型 状态 = {草稿, 已提交}\n'
     '定义 名 = {"草稿": "草稿箱", "已提交": "待审"}\n'
     '输出(名[转文本(状态.草稿)])')
case("非法状态转移拒绝", '类型 状态 = {草稿, 已审核}\n定义 s = 状态.草稿\n如果 s 是 状态.已审核 则\n    输出("非法")\n否则\n    输出("合法")\n结束',
     ["合法"])
case("断言校验", '定义 金额 = -1\n断言 金额 >= 0, "金额不能为负"', None)
case("除零防御（业务规则）", '试\n    定义 单价 = 0\n    输出(100 / 单价)\n捕获 错\n    输出("单价不能为零")\n结束',
     ["单价不能为零"])

print()
print("=" * 70)
print("六、可选值与缺失字段")
print("=" * 70)
case("映射缺键返回空", '定义 m = {"a": 1}\n定义 v = m["b"]\n输出(是否空(v))', ["真"])
case("有键判断后取值", '定义 m = {"a": 1}\n如果 有键(m, "b") 则\n    输出(m["b"])\n否则\n    输出("无")\n结束',
     ["无"])
case("空值合并模式", '定义 m = {}\n定义 v = 空\n如果 有键(m, "a") 则\n    v = m["a"]\n结束\n如果 v 是 空 则\n    v = 0\n结束\n输出(v)',
     ["0"])
case("嵌套映射取值", '定义 m = {"客户": {"名": "甲"}}\n输出(m["客户"]["名"])')
case("结构体转文本", '类型 单 = {额: 整数}\n定义 甲 = 单(额 是 1)\n输出(类型名(甲), 甲.额)', ["单 1"])

print()
print("=" * 70)
print("汇总")
print("=" * 70)
print("失败 %d 项" % len(FAILS))
for name, status, detail in FAILS:
    print("  ★ [%s] %s -> %s" % (status, name, detail))