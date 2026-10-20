"""枢言 —— 系统压测探针（找出崩溃 / 静默错误 / 报错不清）

对每一类边界情形，记录三种结果之一：
  OK    —— 正常返回，且结果正确
  THROW —— 抛出枢言异常（应可被捕获，文案应可行动）
  CRASH —— **宿主异常**（这是实现缺陷：用户程序不该让编译器崩）
  WRONG —— 结果错误但未报错（静默失败，最危险）

用法：python tools/probe_stress.py
"""

import io
import os
import sys
import traceback

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from shuyan.builtins import StdLib                      # noqa: E402
from shuyan.diagnostics import LexError, ParseError, SemanticError, Severity  # noqa: E402
from shuyan.errors import ExitSignal, ShuyanThrow       # noqa: E402
from shuyan.interpreter import Interpreter              # noqa: E402
from shuyan.lexer import Lexer                          # noqa: E402
from shuyan.parser import Parser                        # noqa: E402
from shuyan.semantic import Analyzer                    # noqa: E402

RESULTS = []


def run(src, argv=None):
    """运行枢言源码，返回 (状态, 说明)。"""
    lib = StdLib(argv=argv or [])
    lib.capture = True
    try:
        program = Parser(Lexer(src).tokenize()).parse()
        analyzer = Analyzer(lib)
        _, diags = analyzer.analyze(program)
        errs = [d for d in diags if d.severity is Severity.ERROR]
        if errs:
            return "THROW", "编译期 %s: %s" % (errs[0].code, errs[0].message)
        interp = Interpreter(lib, source_lines=src.splitlines(), analyzer=analyzer)
        code = interp.run(program)
        return "OK", (code, "\n".join(lib.written))
    except (LexError, ParseError) as exc:
        codes = [d.code for d in exc.diagnostics]
        return "THROW", "词法/语法 %s" % codes
    except SemanticError as exc:
        return "THROW", "语义 %s" % [d.code for d in exc.diagnostics]
    except ShuyanThrow as exc:
        return "THROW", "运行期异常: %s" % exc._describe()
    except ExitSignal as exc:
        return "OK", ("exit", exc.code)
    except RecursionError:
        return "CRASH", "Python RecursionError（解释器递归未受控）"
    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc().strip().splitlines()[-1]
        return "CRASH", "%s: %s | %s" % (type(exc).__name__, exc, tb)


def case(name, src, expect="THROW", argv=None):
    status, detail = run(src, argv)
    verdict = ""
    if status == "CRASH":
        verdict = "★★ 缺陷：宿主崩溃"
    elif status == "OK" and expect == "THROW":
        verdict = "○ 未报错（符合预期则忽略）"
    elif status == "THROW" and expect == "OK":
        verdict = "☆ 意外报错"
    RESULTS.append((name, status, detail, verdict))
    print("  [%s] %s" % (status, name))
    print("        %s  %s" % (detail, verdict))


print("=" * 70)
print("一、数值与算术边界")
print("=" * 70)
case("整数除零", "输出(1 / 0)")
case("整数取模零", "输出(1 % 0)")
case("小数除零", "输出(1.0 / 0.0)")
case("负数取模", '输出(-7 % 3)', "OK")
case("大整数溢出", '输出(9223372036854775807 + 1)', "OK")
case("极小数精度", '输出(0.1 + 0.2)', "OK")
case("平方根负数", '输出(数学.平方根(-1))')
case("对数非正", '输出(数学.对数(0))')
case("次方溢出", '输出(次方(10, 10000))', "OK")
case("取整文本", '输出(取整("甲"))')

print()
print("=" * 70)
print("二、容器与索引边界")
print("=" * 70)
case("空列表最大值", '输出(最大([]))')
case("空列表平均值", '输出(平均值([]))')
case("空列表求和", '输出(求和([]))', "OK")
case("索引越界", '定义 甲 = [1,2]\n输出(甲[5])')
case("负索引", '定义 甲 = [1,2]\n输出(甲[-1])', "OK")
case("索引非整数", '定义 甲 = [1,2]\n输出(甲["x"])')
case("映射取不存在的键", '定义 甲 = {"a":1}\n输出(甲["b"])')
case("映射键不可哈希", '定义 甲 = {}\n甲[[1,2]] = 3')
case("切片越界", '输出(切片([1,2,3], 0, 99))', "OK")
case("切片负长度", '输出(切片([1,2,3], 1, 0))', "OK")
case("嵌套深列表", '定义 甲 = [[[[[1]]]]]\n输出(甲)', "OK")
case("排序混合类型", '输出(排序([1, "甲", 真]))')
case("连接非列表", '输出(连接(1, ","))')
case("归约空列表无初值", '输出(归约([], 函数(a,b) 返回 a+b 结束))')
case("转整数超范围文本", '输出(转整数("999999999999999999999999"))')

print()
print("=" * 70)
print("三、文本与中文边界")
print("=" * 70)
case("空文本长度", '输出(长度(""))', "OK")
case("截取超长", '输出(截取("甲乙", 0, 99))', "OK")
case("截取负起", '输出(截取("甲乙丙", -2))', "OK")
case("替换空串", '输出(替换("甲乙", "", "-"))', "OK")
case("分割空串", '输出(分割("", ","))', "OK")
case("emoji 长度", '输出(长度("👍"))', "OK")
case("组合字符长度", '输出(长度("é"))', "OK")
case("查找非文本", '输出(查找(1, "a"))')
case("填充负宽度", '输出(文本.填充("甲", -5))', "OK")
case("格式化缺键", '输出(文本.格式化("你好{名字}", {}))')
case("码点非法字符", '输出(文本.字符(9999999999))')
case("码点负值", '输出(文本.字符(-1))')

print()
print("=" * 70)
print("四、类型系统与转换")
print("=" * 70)
case("文本加整数", '输出("甲" + 1)', "OK")
case("整数加文本", '输出(1 + "甲")', "OK")
case("文本减整数", '输出("甲" - 1)')
case("布尔加整数", '输出(真 + 1)', "OK")
case("空加整数", '输出(空 + 1)')
case("整数赋给文本变量", '定义 甲: 文本 = 1\n输出(甲)', "OK")
case("文本赋给整数变量", '定义 甲: 整数 = "1"')
case("小数赋给整数", '定义 甲: 整数 = 1.5')
case("列表加列表", '输出([1] + [2])', "OK")
case("映射加映射", '输出({"a":1} + {"b":2})')
case("类型名结构体", '类型 点 = {横: 整数}\n定义 甲 = 点(横 是 1)\n输出(类型名(甲))', "OK")
case("任意比较大小", '输出([1] > [2])')

print()
print("=" * 70)
print("五、控制流与函数")
print("=" * 70)
case("深递归 5000", '请 f(n)\n    如果 n <= 0 则\n        返回 0\n    结束\n    返回 f(n-1) + 1\n结束\n输出(f(5000))')
case("深递归 100", '请 f(n)\n    如果 n <= 0 则\n        返回 0\n    结束\n    返回 f(n-1) + 1\n结束\n输出(f(100))', "OK")
case("无限循环带跳出", '定义 i = 0\n当 真 成立\n    i = i + 1\n    如果 i > 3 则\n        跳出\n    结束\n结束\n输出(i)', "OK")
case("函数缺参数", '请 f(a, b)\n    返回 a + b\n结束\n输出(f(1))')
case("函数多参数", '请 f(a)\n    返回 a\n结束\n输出(f(1, 2))')
case("返回类型不符", '请 f() 返回 整数\n    返回 "甲"\n结束\n输出(f())')
case("除零被捕获", '试\n    定义 甲 = 1 / 0\n捕获 错\n    输出("已捕获")\n结束', "OK")

print()
print("=" * 70)
print("六、模块与引入")
print("=" * 70)
case("引入不存在的模块", '引入 "不存在"')
case("引入内置模块", '引入 "数学"', "OK")
case("命名空间调未定义成员", '引入 "数学"\n输出(数学.不存在(1))')
case("模块名与变量冲突", '引入 "数学"\n定义 数学 = 1\n输出(数学)', "OK")

print()
print("=" * 70)
print("七、运行期自省")
print("=" * 70)
case("输出嵌套结构体", '类型 内 = {值: 整数}\n类型 外 = {子: 内}\n输出(外(子 是 内(值 是 1)))', "OK")
case("结构体字段缺失", '类型 点 = {横: 整数, 纵: 整数}\n定义 甲 = 点(横 是 1)')
case("结构体字段多余", '类型 点 = {横: 整数}\n定义 甲 = 点(横 是 1, 纵 是 2)')
case("访问不存在字段", '类型 点 = {横: 整数}\n定义 甲 = 点(横 是 1)\n输出(甲.纵)')
case("枚举不存在成员", '类型 星期 = {一, 二}\n输出(星期.三)')
case("JSON 文本转结构体", '类型 点 = {横: 整数}\n定义 甲 = 转结构体("{\\"横\\":1}", 点)', "OK")

print()
print("=" * 70)
print("八、格式化器与 Lint 的语义保持")
print("=" * 70)
from shuyan.formatter import format_source  # noqa: E402
from shuyan.linter import lint_source       # noqa: E402

FMT_CASES = [
    ("多字符运算符不被拆开", "定义 甲 = 1 <= 2\n定义 乙 = 1 == 1\n定义 丙 = 1\n丙 += 2\n输出(甲, 乙, 丙)"),
    ("负号紧贴数字", "定义 甲 = -5\n输出(甲 + -3)"),
    ("幂运算符", "输出(2 ^ 10)"),
    ("取模与除", "输出(7 % 3, 7 / 2)"),
]
for name, src in FMT_CASES:
    try:
        formatted, notes = format_source(src, "<probe>")
        _s1, _ = run(src)
        _s2, _ = run(formatted)
        same = (_s1 == _s2)
        if not same:
            RESULTS.append((name, "WRONG", "格式化前后行为不同", "★★ 缺陷：格式化器改语义"))
        print("  [%s] %s" % ("OK" if same else "WRONG", name))
        if not same:
            print("        原文: %r" % src)
            print("        格式化后: %r" % formatted)
    except Exception as exc:  # noqa: BLE001
        print("  [CRASH] %s -> %s: %s" % (name, type(exc).__name__, exc))
        RESULTS.append((name, "CRASH", str(exc), "★★ 缺陷：格式化器崩溃"))

print()
print("=" * 70)
print("汇总")
print("=" * 70)
crashes = [r for r in RESULTS if r[1] == "CRASH"]
wrongs = [r for r in RESULTS if r[1] == "WRONG"]
print("总用例 %d · 宿主崩溃 %d · 静默错误 %d" % (len(RESULTS), len(crashes), len(wrongs)))
for name, status, detail, verdict in crashes + wrongs:
    print("  ★ %s -> %s" % (name, detail))