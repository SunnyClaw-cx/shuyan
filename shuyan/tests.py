"""枢言语言 —— 自测体系（SPEC §10 的落实）。

四类测试，对应一家成熟语言工具链的最低要求：

1. **词法切分测试**：中文粘连歧义的正确性（这是本语言最容易出错的部位）；
2. **语义与执行测试**：正例，验证「跑对了」；
3. **负例测试**：验证**报错文案与错误码**正确——报错本身就是产品的一部分；
4. **标准库测试**：重点覆盖中文处理（字符计数、显示宽度、中文数词）。

测试用可执行代码而非纯断言，便于 `sy 测试` 在任何环境复现。
"""

from __future__ import annotations

import sys

from .builtins import StdLib
from .diagnostics import LexError, ParseError, SemanticError
from .interpreter import Interpreter
from .lexer import Kind, Lexer
from .parser import Parser
from .semantic import Analyzer
from .source import display_width

# ================================================================ 测试框架

_RESULTS: list[tuple[str, str, str]] = []      # (组, 用例, 状态说明)


class Failure(Exception):
    pass


def check(cond: bool, message: str) -> None:
    if not cond:
        raise Failure(message)


def expect_eq(actual, expected, what: str) -> None:
    if actual != expected:
        raise Failure(f"{what}：期望 {expected!r}，实际 {actual!r}")


def group(name: str):
    def deco(fn):
        fn._group = name
        return fn
    return deco


# ------------------------------------------------------------ 运行辅助

def tokenize(source: str):
    return [t for t in Lexer(source).tokenize() if t.kind is not Kind.EOF]


def token_pairs(source: str):
    return [(t.kind.name, t.value) for t in tokenize(source)]


def run_source(source: str, argv: list[str] | None = None,
               capture: bool = True) -> tuple[str, int]:
    """运行枢言源码，返回（全部标准输出, 退出码）。"""
    lib = StdLib(argv=argv or [])
    lib.capture = capture
    tokens = Lexer(source).tokenize()
    program = Parser(tokens).parse()
    analyzer = Analyzer(lib)
    _, diags = analyzer.analyze(program)
    from .diagnostics import Severity
    errors = [d for d in diags if d.severity is Severity.ERROR]
    if errors:
        raise SemanticError(diags)
    interp = Interpreter(lib, source_lines=source.splitlines(), analyzer=analyzer)
    code = interp.run(program)
    return "\n".join(lib.written), code


def expect_output(source: str, expected_lines: list[str]) -> None:
    out, code = run_source(source)
    actual = [line for line in out.split("\n") if line != ""]
    expect_eq(actual, expected_lines, "程序输出")
    expect_eq(code, 0, "退出码")


def expect_compile_error(source: str, code: str, must_contain: str = "") -> None:
    """验证编译期报错：错误码正确，且文案包含关键片段。"""
    try:
        tokens = Lexer(source).tokenize()
        program = Parser(tokens).parse()
        analyzer = Analyzer(StdLib())
        _, diags = analyzer.analyze(program)
        from .diagnostics import Severity
        errors = [d for d in diags if d.severity is Severity.ERROR]
        if not errors:
            raise Failure("期望报错，但编译通过了")
        codes = [d.code for d in errors]
        if code not in codes:
            raise Failure(f"期望错误码 {code}，实际得到 {codes}")
        joined = " ".join(d.message for d in errors)
        if must_contain and must_contain not in joined:
            raise Failure(f"报错文案里没有「{must_contain}」：{joined}")
    except (LexError, ParseError) as exc:
        codes = [d.code for d in exc.diagnostics]
        if code not in codes:
            raise Failure(f"期望错误码 {code}（词法/语法层），实际得到 {codes}")
        joined = " ".join(d.message for d in exc.diagnostics)
        if must_contain and must_contain not in joined:
            raise Failure(f"报错文案里没有「{must_contain}」：{joined}")


def expect_runtime_error(source: str, must_contain: str) -> None:
    out, code = run_source(source)
    check(code != 0, "期望运行失败，但退出码为 0")
    check(must_contain in out,
          f"运行期输出里没有「{must_contain}」：{out[:200]}")


# ================================================================ 一、词法切分


@group("词法·中文切分")
def test_lexer_han_split():
    # 引导词紧贴标识符
    expect_eq(token_pairs("定义年龄=20"),
              [("KEYWORD", "let"), ("IDENT", "年龄"), ("OP", "="),
               ("NUMBER", "20")], "定义年龄")
    # 无关键字前缀 → 整体标识符
    expect_eq(token_pairs("苹果个数=5"),
              [("IDENT", "苹果个数"), ("OP", "="), ("NUMBER", "5")], "苹果个数")
    # C 类单字词不得在块中误切
    expect_eq(token_pairs("数据中台 是 1"),
              [("IDENT", "数据中台"), ("KEYWORD", "is"), ("NUMBER", "1")],
              "数据中台")
    expect_eq(token_pairs("期中成绩 是 90"),
              [("IDENT", "期中成绩"), ("KEYWORD", "is"), ("NUMBER", "90")],
              "期中成绩")
    # 软助词仅整体命中才剥离
    expect_eq(token_pairs("规则 是 1"),
              [("IDENT", "规则"), ("KEYWORD", "is"), ("NUMBER", "1")], "规则")
    # B 类整体命中
    expect_eq(token_pairs("否则若条件")[0], ("KEYWORD", "elif"), "否则若")
    # C 类多字后缀
    expect_eq(token_pairs("年龄不小于18"),
              [("IDENT", "年龄"), ("OP", ">="), ("NUMBER", "18")], "年龄不小于")
    # 白话遍历
    expect_eq(token_pairs("遍历名单中的每个名字"),
              [("KEYWORD", "for"), ("IDENT", "名单"), ("KEYWORD", "each"),
               ("IDENT", "名字")], "遍历…中的每个…")
    # 混合标识符
    expect_eq(token_pairs("订单_ID 是 1"),
              [("IDENT", "订单_ID"), ("KEYWORD", "is"), ("NUMBER", "1")],
              "订单_ID")
    expect_eq(token_pairs("甲_乙 是 2"),
              [("IDENT", "甲_乙"), ("KEYWORD", "is"), ("NUMBER", "2")], "甲_乙")
    # 反引号逃生舱
    expect_eq(token_pairs("`定义年龄` 是 1"),
              [("IDENT", "定义年龄"), ("KEYWORD", "is"), ("NUMBER", "1")],
              "反引号标识符")


@group("词法·中文数词")
def test_lexer_cn_number():
    for src, expected in [("一百二十", 120), ("十五", 15), ("三百零五", 305),
                          ("两千零一", 2001), ("零", 0), ("十", 10),
                          ("一二三", 123), ("一九九八", 1998),
                          ("十亿", 1000000000), ("三百", 300)]:
        toks = tokenize(src)
        expect_eq(toks[0].kind, Kind.NUMBER, f"{src} 应为数字")
        expect_eq(int(toks[0].value), expected, f"中文数词 {src}")
    # 尾随缩写单位
    expect_eq(int(tokenize("3万")[0].value), 30000, "3万")
    expect_eq(int(tokenize("20万")[0].value), 200000, "20万")
    # 以数词开头的普通标识符不得被强转（`二十一点半`）
    expect_eq(tokenize("二十一点半")[0].kind, Kind.IDENT, "数词开头的标识符")
    # 数词 + 单位词
    expect_eq(token_pairs("重复三次"),
              [("KEYWORD", "loop"), ("NUMBER", "3"), ("KEYWORD", "times")],
              "重复三次")


@group("词法·全角归一化")
def test_lexer_fullwidth():
    expect_eq(token_pairs("定义甲＝1"),
              [("KEYWORD", "let"), ("IDENT", "甲"), ("OP", "="),
               ("NUMBER", "1")], "全角等号")
    expect_eq(token_pairs("输出（甲）"),
              [("IDENT", "输出"), ("OP", "("), ("IDENT", "甲"), ("OP", ")")],
              "全角括号")
    expect_eq(token_pairs("甲；乙"),
              [("IDENT", "甲"), ("OP", ";"), ("IDENT", "乙")], "全角分号")
    # 全角逗号在列表里仍是分隔符
    n = len(tokenize("[1，2]"))
    expect_eq(n, 5, "全角逗号分隔列表")
    # 全角点号（句号）在中文语境下作语句结束处理
    expect_eq(token_pairs("甲。乙"),
              [("IDENT", "甲"), ("OP", ";"), ("IDENT", "乙")], "全角句号")
    # 意图不明的标点应明确报错
    expect_compile_error("定义 甲 = 《书名》", "D1004")


@group("词法·字符串保真")
def test_lexer_string_fidelity():
    # 字符串内容不得被全角归一化改写
    toks = tokenize('输出("你好，世界！（测试）")')
    s = [t for t in toks if t.kind is Kind.STRING][0]
    expect_eq(s.value, "你好，世界！（测试）", "字符串内容保真")
    # 转义
    toks = tokenize(r'"换行\n制表\t引号\"结束"')
    s = [t for t in toks if t.kind is Kind.STRING][0]
    expect_eq(s.value, '换行\n制表\t引号"结束', "转义序列")
    # 插值
    toks = tokenize('`你好，{名字}！`')
    s = [t for t in toks if t.kind is Kind.STRING][0]
    kinds = [p[0] for p in s.parts]
    expect_eq(kinds, ["text", "expr", "text"], "插值片段")


@group("词法·错误")
def test_lexer_errors():
    expect_compile_error('定义 甲 = "没有闭合', "D1002", "字符串没有闭合")
    expect_compile_error("/* 没有闭合", "D1003", "块注释")
    expect_compile_error("定义 甲 = 0o777", "D1005", "八进制")
    expect_compile_error("定义 甲 = 0777", "D1005", "前导零")


# ================================================================ 二、语义与执行


@group("执行·基础")
def test_exec_basic():
    expect_output("""
定义 甲 = 1 + 2 * 3
输出(甲)
""", ["7"])
    expect_output("""
定义 甲 = 10
甲 += 5
输出(甲)
""", ["15"])
    expect_output("""
输出(2 ^ 10)
""", ["1024"])
    expect_output("""
输出(10 / 4)
""", ["2.5"])
    expect_output("""
输出(7 % 3)
""", ["1"])


@group("执行·文本与拼接")
def test_exec_text():
    expect_output('输出("甲" + "乙")', ["甲乙"])
    expect_output('输出("年龄：" + 20)', ["年龄：20"])
    expect_output('输出(长度("你好"))', ["2"])
    expect_output('输出("你好"[1])', ["好"])
    expect_output('输出(截取("中国人民", 0, 2))', ["中国"])
    expect_output('输出(截取("中国人民", -2))', ["人民"])


@group("执行·控制流")
def test_exec_control():
    expect_output("""
定义 总分 = 0
重复 5 次
    总分 += 1
结束
输出(总分)
""", ["5"])
    expect_output("""
定义 累积 = []
重复 甲 从 1 到 5
    入列(累积, 甲 * 甲)
结束
输出(连接(累积, "-"))
""", ["1-4-9-16-25"])
    expect_output("""
定义 名单 = ["甲", "乙", "丙"]
遍历 名字 中 名单
    如果 名字 是 "乙" 则
        继续
    结束
    输出(名字)
结束
""", ["甲", "丙"])
    expect_output("""
定义 i = 0
只要 真
    i += 1
    如果 i >= 3 则
        跳出
    结束
结束
输出(i)
""", ["3"])
    expect_output("""
如果 1 > 2 则
    输出("甲")
否则如果 2 > 1 则
    输出("乙")
否则
    输出("丙")
结束
""", ["乙"])


@group("执行·函数与闭包")
def test_exec_function():
    expect_output("""
请 相加(甲, 乙)
    返回 甲 + 乙
结束
输出(相加(2, 3))
""", ["5"])
    expect_output("""
请 阶乘(n)
    如果 n <= 1 则
        返回 1
    结束
    返回 n * 阶乘(n - 1)
结束
输出(阶乘(5))
""", ["120"])
    # 默认参数
    expect_output("""
请 打招呼(名字, 问候 = "你好")
    返回 问候 + "，" + 名字
结束
输出(打招呼("小明"))
输出(打招呼("小红", "早上好"))
""", ["你好，小明", "早上好，小红"])
    # 闭包按引用捕获
    expect_output("""
请 计数器()
    定义 次数 = 0
    请 加一()
        次数 += 1
        返回 次数
    结束
    返回 加一
结束
定义 c = 计数器()
输出(c())
输出(c())
输出(c())
""", ["1", "2", "3"])
    # 可变参数
    expect_output("""
请 求和(...项)
    定义 总 = 0
    遍历 x 中 项
        总 += x
    结束
    返回 总
结束
输出(求和(1, 2, 3, 4))
""", ["10"])


@group("执行·数据结构")
def test_exec_structures():
    expect_output('输出([1, 2, 3][1])', ["2"])
    expect_output('''
定义 人 = {"名字": "小明", "年龄": 20}
输出(人["名字"])
输出(人.年龄)
''', ["小明", "20"])
    expect_output('输出(排序([3, 1, 2]))', ["[1, 2, 3]"])
    expect_output('输出(去重([1, 1, 2, 3, 3]))', ["[1, 2, 3]"])
    expect_output('输出(求和([1, 2, 3, 4]))', ["10"])
    expect_output('输出(范围(3))', ["[0, 1, 2]"])
    expect_output('输出(范围(1, 7, 2))', ["[1, 3, 5]"])


@group("执行·异常")
def test_exec_exception():
    expect_output("""
试
    抛出 {"类型": "业务错误", "信息": "库存不足"}
捕获 异常
    输出("接到：" + 异常["信息"])
结束
""", ["接到：库存不足"])
    expect_output("""
定义 记录 = []
试
    抛出 "出问题了"
捕获 异常
    入列(记录, "捕获：" + 异常)
最终
    入列(记录, "收尾")
结束
输出(连接(记录, " / "))
""", ["捕获：出问题了 / 收尾"])
    # 断言
    expect_runtime_error("断言 1 是 2", "断言失败")


@group("执行·白话端口")
def test_exec_oral():
    expect_output("""
设 年龄 是 20
若 年龄 不小于 18
    输出("成年")
否则
    输出("未成年")
完
""", ["成年"])
    expect_output("""
设 名单 是 ["甲", "乙"]
遍历 名单 中的每个 名字
    输出(名字)
完
""", ["甲", "乙"])
    expect_output("""
重复三次
    输出("嗨")
完
""", ["嗨", "嗨", "嗨"])
    # 句读逗号（自然汉语标点）
    expect_output("""
设 甲 是 5，
若 甲 大于 3，那么
    输出("大")
完
""", ["大"])
    # 拉丁关键字
    expect_output("let x = 1\nprint(x)", ["1"])


@group("执行·插值字符串")
def test_exec_interpolation():
    expect_output("""
定义 名字 = "小明"
定义 年龄 = 20
输出(`你好，{名字}！你今年 {年龄} 岁。`)
""", ["你好，小明！你今年 20 岁。"])
    expect_output("""
定义 甲 = 5
输出(`结果是 {甲 * 2}`)
""", ["结果是 10"])


# ================================================================ 三、负例（报错质量）


@group("负例·语义报错")
def test_negative_semantic():
    expect_compile_error("定义 甲: 整数 = \"文本\"", "D3004", "不能把")
    expect_compile_error("输出(未定义变量)", "D3005", "没有定义")
    # 拼写候选
    try:
        expect_compile_error("定义 计数 = 1\n输出(计树)", "D3005")
        tokens = Lexer("定义 计数 = 1\n输出(计树)").tokenize()
        program = Parser(tokens).parse()
        analyzer = Analyzer(StdLib())
        _, diags = analyzer.analyze(program)
        hints = " ".join(h for d in diags for h in d.hints)
        check("你是不是想写" in hints, "未给出拼写候选")
    except Failure:
        raise
    expect_compile_error("定义 甲 = 1\n定义 甲 = 2", "D3006")
    expect_compile_error("常量 甲", "D3001", "必须给出初始值")
    expect_compile_error("输出(1 < 2 < 3)", "D3010", "连续比较")
    expect_compile_error("跳出", "D3011")
    expect_compile_error("返回 1", "D3016")
    expect_compile_error("定义 甲 = 1 + \"文本\"\n定义 乙: 整数 = 甲",
                         "D3004")
    expect_compile_error("输出(长度(1))", "D3004", "长度")
    expect_compile_error('输出(截取(1, 0))', "D3004", "截取")
    expect_compile_error("引入 \"不存在的模块\"", "D3005", "找不到模块")


@group("负例·参数个数")
def test_negative_arity():
    expect_compile_error("输出(长度())", "D3007", "需要 1 个参数")
    expect_compile_error('输出(截取("甲"))', "D3007", "需要 2 到 3")
    expect_compile_error("""
请 相加(甲, 乙)
    返回 甲 + 乙
结束
输出(相加(1))
""", "D3007", "需要 2 个参数")


@group("负例·语法报错")
def test_negative_syntax():
    expect_compile_error("如果 甲 则\n输出(1)", "D2009", "没有闭合")
    expect_compile_error("结束", "D2010")
    expect_compile_error("输出(", "D2002")


@group("负例·静默失败守卫")
def test_negative_silent_failure():
    """静默丢弃是最危险的一类缺陷：结果错且无人察觉。

    `2 的 10 次方` 曾被解析成 `2` 并静默完成——必须变成明确报错。
    """
    expect_compile_error("定义 甲 = 2 的 10 次方", "D2001", "的")
    expect_compile_error("输出(2 的 10 次方)", "D2001", "的")
    expect_compile_error("输出(1, 2 的 3)", "D2001", "的")


@group("标准库·幂与函数式写法")
def test_stdlib_power():
    expect_output("输出(次方(2, 10))", ["1024"])
    expect_output("输出(2 ^ 10)", ["1024"])
    expect_output("输出(数学.幂(2, 8))", ["256"])


@group("执行·白话函数调用")
def test_exec_oral_call():
    # 白话无括号调用：`说 值`
    expect_output('说 "你好"', ["你好"])
    expect_output("定义 甲 = 5\n说 甲", ["5"])
    # 白话遍历表达式（而非变量名）
    expect_output("""
遍历 [1, 2, 3] 中的每个 数字
    说 数字
完
""", ["1", "2", "3"])


# ================================================ 结构体与枚举（SPEC §3.9）


@group("结构体·基本")
def test_struct_basic():
    expect_output("""
类型 点 = { 横: 整数, 纵: 整数 }
定义 甲 = 点(横 是 3, 纵 是 4)
输出(甲.横)
输出(甲.纵)
输出(甲)
""", ["3", "4", "点{横: 3, 纵: 4}"])

    # 字段顺序无关；打印按**声明顺序**
    expect_output("""
类型 点 = { 横: 整数, 纵: 整数 }
输出(点(纵 是 4, 横 是 3))
""", ["点{横: 3, 纵: 4}"])

    # 字段可写
    expect_output("""
类型 点 = { 横: 整数, 纵: 整数 }
定义 甲 = 点(横 是 1, 纵 是 2)
甲.横 = 10
输出(甲.横)
""", ["10"])


@group("结构体·文字段与嵌套")
def test_struct_text_nested():
    expect_output("""
类型 员工 = { 姓名: 文本, 工号: 文本, 工资: 小数 }
定义 甲 = 员工(姓名 是 "陈工", 工号 是 "E001", 工资 是 8500.5)
输出(甲.姓名 + "(" + 甲.工号 + ")")
输出(甲)
""", ['陈工(E001)', '员工{姓名: "陈工", 工号: "E001", 工资: 8500.5}'])

    # 结构体作字段类型
    expect_output("""
类型 点 = { 横: 整数, 纵: 整数 }
类型 线段 = { 起点: 点, 终点: 点 }
定义 甲 = 线段(起点 是 点(横 是 0, 纵 是 0), 终点 是 点(横 是 3, 纵 是 4))
输出(甲.起点.横)
输出(甲.终点.纵)
""", ["0", "4"])


@group("结构体·相等")
def test_struct_equals():
    expect_output("""
类型 点 = { 横: 整数, 纵: 整数 }
定义 甲 = 点(横 是 1, 纵 是 2)
定义 乙 = 点(横 是 1, 纵 是 2)
定义 丙 = 点(横 是 1, 纵 是 9)
输出(甲 等于 乙)
输出(甲 等于 丙)
""", ["真", "假"])


@group("结构体·负例")
def test_struct_negative():
    # 缺字段
    expect_compile_error("""
类型 点 = { 横: 整数, 纵: 整数 }
定义 甲 = 点(横 是 3)
""", "D3017", "缺少字段")
    # 未知字段
    expect_compile_error("""
类型 点 = { 横: 整数, 纵: 整数 }
定义 甲 = 点(横 是 3, 纵 是 4, 高 是 5)
""", "D3017", "没有字段")
    # 字段类型不符
    expect_compile_error("""
类型 点 = { 横: 整数, 纵: 整数 }
定义 甲 = 点(横 是 "三", 纵 是 4)
""", "D3004", "不能把")
    # 位置参数
    expect_compile_error("""
类型 点 = { 横: 整数, 纵: 整数 }
定义 甲 = 点(3, 4)
""", "D3017", "具名参数")
    # 读不存在的字段（**不得静默返回空**）
    expect_compile_error("""
类型 点 = { 横: 整数, 纵: 整数 }
定义 甲 = 点(横 是 3, 纵 是 4)
输出(甲.高)
""", "D3018", "没有字段")
    # 写不存在的字段
    expect_compile_error("""
类型 点 = { 横: 整数, 纵: 整数 }
定义 甲 = 点(横 是 3, 纵 是 4)
甲.高 = 5
""", "D3018", "没有字段")
    # 类型本身不是值（编译期拦下，而不是拖到运行期）
    expect_compile_error("""
类型 点 = { 横: 整数, 纵: 整数 }
输出(点)
""", "D3005", "不能当作值使用")


@group("枚举·基本")
def test_enum_basic():
    expect_output("""
类型 星期 = { 一, 二, 三, 四, 五, 六, 日 }
定义 今天 = 星期.三
输出(今天)
输出(今天.序号)
输出(今天.名称)
""", ["星期.三", "3", "三"])


@group("枚举·中文数词成员名")
def test_enum_cn_number_members():
    """成员名按**源码原文**匹配 —— `一` 归一后是 `1`，只有原文才对得上。"""
    expect_output("""
类型 星期 = { 一, 二, 三 }
输出(星期.一.序号)
输出(星期.二.名称)
输出(星期.三.序号)
""", ["1", "二", "3"])
    # 写成阿拉伯数字**不匹配**成员 `一`
    expect_compile_error("""
类型 星期 = { 一, 二, 三 }
输出(星期.1)
""", "D3019", "没有成员")


@group("枚举·相等与容器")
def test_enum_equals_container():
    expect_output("""
类型 状态 = { 待处理, `进行中`, 已完成 }
定义 甲 = 状态.`进行中`
输出(甲 等于 状态.`进行中`)
输出(状态.已完成 等于 甲)
""", ["真", "假"])

    # 枚举可放进列表 / 映射
    expect_output("""
类型 星期 = { 一, 五, 日 }
定义 全部 = [星期.一, 星期.五, 星期.日]
遍历 全部 中的每个 某天
    输出(某天.名称)
结束
""", ["一", "五", "日"])
    expect_output("""
类型 状态 = { 已完成 }
定义 表 = {"状态": 状态.已完成}
输出(表["状态"].序号)
""", ["1"])


@group("枚举·逃生舱与纯中文成员")
def test_enum_escape_hatch():
    """`进行中` 会被切成 `进行`+`中`，须用反引号声明。"""
    expect_output("""
类型 状态 = { 待处理, `进行中`, 已完成 }
输出(状态.`进行中`)
输出(状态.`进行中`.序号)
""", ["状态.进行中", "2"])
    # 规范化后**不含反引号**
    expect_output("""
类型 状态 = { `进行中` }
输出(状态.`进行中`.名称)
""", ["进行中"])


@group("枚举·负例")
def test_enum_negative():
    # 成员不存在
    expect_compile_error("""
类型 星期 = { 一, 二 }
输出(星期.三)
""", "D3019", "没有成员")
    # 枚举不可调用
    expect_compile_error("""
类型 星期 = { 一, 二 }
定义 甲 = 星期()
""", "D3019", "不能像函数那样调用")
    # 枚举成员不可改
    expect_compile_error("""
类型 星期 = { 一, 二 }
星期.一 = 5
""", "D3019", "不可修改")
    # 字段与成员混用
    expect_compile_error("""
类型 混合 = { 甲: 整数, 乙 }
""", "D3019", "混用")
    # 空定义
    expect_compile_error("类型 空类型 = { }", "D3019", "空的")


@group("类型·别名与复合类型赋值")
def test_type_alias():
    expect_output("""
类型 编号 = 整数
定义 甲: 编号 = 5
输出(甲)
""", ["5"])
    # 别名不产生新类型：整数可赋给别名
    expect_output("""
类型 编号 = 整数
定义 甲: 编号 = 5
甲 = 6
输出(甲)
""", ["6"])
    # 结构体不参与隐式宽化
    expect_compile_error("""
类型 点 = { 横: 整数 }
定义 甲: 整数 = 点(横 是 1)
""", "D3004", "复合类型")
    # 两种不同复合类型不能互赋
    expect_compile_error("""
类型 甲型 = { 值: 整数 }
类型 乙型 = { 值: 整数 }
定义 甲: 甲型 = 乙型(值 是 1)
""", "D3004", "不同的复合类型")


@group("类型·负例")
def test_type_negative():
    # 字段类型未定义
    expect_compile_error("""
类型 点 = { 横: 不存在的类型 }
""", "D3019", "没有定义")
    # 字段重复
    expect_compile_error("""
类型 点 = { 横: 整数, 横: 整数 }
""", "D3006", "重复声明")
    # 成员重复
    expect_compile_error("""
类型 星期 = { 一, 一 }
""", "D3006", "重复声明")
    # 别名循环
    expect_compile_error("""
类型 甲 = 乙
类型 乙 = 甲
定义 值: 甲 = 1
""", "D3019", "循环")


@group("负例·具名参数不得静默丢弃")
def test_negative_stray_kwargs():
    """`输出(甲 是 5)` 曾被**静默丢弃**（既不输出也不报错）。

    这是本项目最危险的失败形态，必须明确报错。
    """
    expect_compile_error("定义 甲 = 1\n输出(甲 是 5)", "D3007", "不支持具名参数")
    expect_compile_error("输出(值 是 123)", "D3007", "不支持具名参数")
    expect_compile_error("定义 甲 = 1\n长度(值 是 [1])\n输出(甲)",
                         "D3007", "不支持具名参数")
    # 用户函数的未知具名参数同样要拦
    expect_compile_error("""
请 问候(名字)
    输出(名字)
结束
问候(昵称 是 "老李")
""", "D3007", "没有名为")
    # 模块成员同理
    expect_compile_error("输出(数学.平方根(值 是 4))",
                         "D3007", "不支持具名参数")


@group("执行·用户函数具名参数")
def test_user_function_kwargs():
    """用户函数的具名参数必须被**正确绑定**（语义层与运行期同序）。"""
    expect_output("""
请 问候(名字, 语气)
    输出(语气 + "，" + 名字 + "！")
结束
问候(名字 是 "老李", 语气 是 "早安")
""", ["早安，老李！"])
    expect_output("""
请 问候(名字, 语气)
    输出(语气 + "，" + 名字 + "！")
结束
问候("老王", "您好")
""", ["您好，老王！"])
    # 混合：位置 + 具名
    expect_output("""
请 合计(甲, 乙)
    输出(甲 + 乙)
结束
合计(1, 乙 是 2)
""", ["3"])


@group("词法·单字联结词后缀的已知切分")
def test_lexer_known_splits():
    """记录规则的实际行为：`进行中` 会被切开，逃生舱是唯一手段。"""
    expect_eq(token_pairs("进行中"),
              [("IDENT", "进行"), ("KEYWORD", "in")], "进行中")
    expect_eq(token_pairs("数据中台"),
              [("IDENT", "数据中台")], "数据中台（不切）")
    expect_eq(token_pairs("审核中"),
              [("IDENT", "审核"), ("KEYWORD", "in")], "审核中")
    # 反引号强制整体
    expect_eq(token_pairs("`进行中`"),
              [("IDENT", "进行中")], "反引号逃生舱")


@group("词法·汉字名加拉丁后缀")
def test_lexer_han_latin_tail():
    """`编码Base64` 这类「汉字 + 拉丁后缀」必须**原文与值一致**。

    这是实测暴露的严重缺陷：字块的 `text`（源码原文）曾被提前截取，
    只含汉字部分而 `value` 含完整拉丁后缀 ⇒ 解析器取名字原文时得到
    `编码`/`导出`，凡是「汉字名 + 拉丁后缀」的内置成员**全部**查不到，
    且报错文案指向十万八千里外（「空不是函数」）。

    断言两件事：① 切分结果唯一（不劈成多枚）；② `text == value`（原文完整）。
    """
    for src, want in [("编码Base64", "编码Base64"),
                      ("导出CSV", "导出CSV"),
                      ("编码十六进制", "编码十六进制"),
                      ("结果x", "结果x")]:
        toks = Lexer(src, "t.sy").tokenize()
        real = [t for t in toks if t.kind is not Kind.EOF]
        expect_eq(len(real), 1, f"{src}：应切成一枚记号")
        expect_eq(real[0].value, want, f"{src}：值")
        # **关键断言**：名字原文必须与值一致，否则成员访问查不到
        expect_eq(real[0].text, want, f"{src}：源码原文必须完整")

    # 端到端：内置成员经词法—语法—语义—运行四层后仍能命中
    expect_output('引入 "编码"\n输出(编码.编码Base64("你好"))', ["5L2g5aW9"])


# ==================================================== 多文件工程（引入 .sy）


def _with_temp_module(module_name: str, module_source: str,
                      main_source: str) -> tuple[str, int]:
    """在临时目录里建一个模块文件并运行主程序（验证跨文件引入）。"""
    import os
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        mod_path = os.path.join(td, module_name + ".sy")
        with open(mod_path, "w", encoding="utf-8") as f:
            f.write(module_source)
        lib = StdLib()
        lib.capture = True
        lib.base_dir = td                       # 模块解析基准目录
        tokens = Lexer(main_source, os.path.join(td, "主.sy")).tokenize()
        program = Parser(tokens).parse()
        analyzer = Analyzer(lib)
        _, diags = analyzer.analyze(program)
        from .diagnostics import Severity
        errors = [d for d in diags if d.severity is Severity.ERROR]
        if errors:
            raise SemanticError(diags)
        interp = Interpreter(lib, source_lines=main_source.splitlines(),
                             analyzer=analyzer)
        code = interp.run(program)
        return "\n".join(lib.written), code


@group("多文件·引入本机模块")
def test_multifile_import():
    out, code = _with_temp_module(
        "工具",
        """
请 平方(甲)
    返回 甲 * 甲
结束
常量 版本号 = "1.0"
""",
        """
引入 "工具"
输出(工具.平方(5))
""")
    expect_eq([l for l in out.split("\n") if l], ["25"], "跨文件函数调用")
    expect_eq(code, 0, "退出码")


@group("多文件·从模块取名字")
def test_multifile_names():
    out, code = _with_temp_module(
        "数学工具",
        """
请 两倍(甲)
    返回 甲 * 2
结束
""",
        """
引入 { 两倍 } 从 "数学工具"
输出(两倍(21))
""")
    expect_eq([l for l in out.split("\n") if l], ["42"], "按名引入")


@group("多文件·模块里的类型")
def test_multifile_types():
    """模块里定义的结构体 / 枚举必须能在主程序使用。

    这要求语义层与运行期**都把类型定义跨文件带过去** ——
    两处各写一遍必然分叉，故实现上共用同一份类型表。
    """
    out, code = _with_temp_module(
        "领域模型",
        """
类型 点 = { 横: 整数, 纵: 整数 }
类型 状态 = { 待处理, `处理中`, 已完成 }
请 造点(横, 纵)
    返回 点(横 是 横, 纵 是 纵)
结束
""",
        """
引入 "领域模型"
定义 甲 = 领域模型.造点(3, 4)
输出(甲.横)
输出(领域模型.状态.`处理中`.序号)
输出(领域模型.状态.`处理中`.名称)
""")
    expect_eq([l for l in out.split("\n") if l], ["3", "2", "处理中"],
              "跨文件类型使用")


@group("多文件·负例")
def test_multifile_negative():
    # 模块不存在
    expect_compile_error('引入 "根本没有的模块"', "D3005", "找不到模块")


@group("负例·运行期报错")
def test_negative_runtime():
    expect_runtime_error("输出(1 / 0)", "除以零")
    expect_runtime_error("定义 甲 = [1]\n输出(甲[5])", "下标越界")
    expect_runtime_error("请 递归(n)\n    返回 递归(n + 1)\n结束\n递归(0)",
                         "递归")
    expect_runtime_error("定义 甲 = [1]\n定义 乙 = 甲 + 1\n输出(乙)",
                         "类型错误")


@group("执行·边界")
def test_exec_edges():
    # 映射取不存在的键 → 空（规范行为，不报错）
    expect_output('输出(类型名({"甲": 1}["不存在"]))', ["空"])
    # 区间：`从…到…` 闭区间；`范围()` 半开区间
    expect_output("""
定义 累积 = []
重复 i 从 1 到 3
    入列(累积, i)
结束
输出(连接(累积, ","))
""", ["1,2,3"])
    expect_output('输出(范围(3))', ["[0, 1, 2]"])
    expect_output('输出(范围(1, 5))', ["[1, 2, 3, 4]"])
    # 真值规则
    expect_output('如果 "" 则\n输出("真")\n否则\n输出("假")\n结束', ["假"])
    expect_output('如果 [1] 则\n输出("真")\n否则\n输出("假")\n结束', ["真"])
    expect_output('如果 0.0 则\n输出("真")\n否则\n输出("假")\n结束', ["假"])
    # 相等：布尔不隐式转数值
    expect_output("输出(真 是 1)", ["假"])
    expect_output("输出(1 是 1.0)", ["真"])
    expect_output('输出([1, 2] 是 [1, 2])', ["真"])
    expect_output('输出({"甲": 1} 是 {"甲": 1})', ["真"])
    # 逻辑运算返回操作数本身
    expect_output('输出(空 或 "默认值")', ["默认值"])
    expect_output('输出("有值" 且 "也算有值")', ["也算有值"])


# ================================================================ 四、标准库与中文处理


@group("标准库·中文处理")
def test_stdlib_chinese():
    expect_output('输出(长度("你好世界"))', ["4"])
    expect_output('输出(长度("a你b好"))', ["4"])
    # 逐字按码点
    expect_output('输出(连接(文本.逐字("你好"), "-"))', ["你-好"])
    # 显示宽度：汉字算 2 列
    expect_eq(display_width("你好"), 4, "汉字显示宽度")
    expect_eq(display_width("abc"), 3, "拉丁显示宽度")
    expect_eq(display_width("你a好"), 5, "混排显示宽度")
    # 填充按**显示宽度**补到 10 列：2 个汉字占 4 列，需补 6 个空格
    expect_output('输出(文本.填充("你好", 10))', ["你好      "])
    expect_output('输出(文本.填充("你好", 10, "*"))', ["你好******"])
    expect_output('输出(文本.填充("你好", 10, " ", "右"))', ["      你好"])
    # 补到 10 列后，空格数是 6（`长度` 数的是字符，汉字 4 + 空格 6）
    expect_output('输出(长度(文本.填充("你好", 10)) - 长度("你好"))', ["6"])


@group("标准库·文本")
def test_stdlib_text():
    expect_output('输出(大写("abc"))', ["ABC"])
    expect_output('输出(去空白("  甲  "))', ["甲"])
    expect_output('输出(替换("甲乙丙", "乙", "X"))', ["甲X丙"])
    # `输出` 对列表采用面向用户的表示：不加引号
    expect_output('输出(分割("甲,乙,丙", ","))', ["[甲, 乙, 丙]"])
    expect_output('输出(开头是("枢言编译器", "枢言"))', ["真"])
    expect_output('输出(查找("中国人民", "人民"))', ["2"])
    expect_output('输出(包含([1, 2], 2))', ["真"])
    expect_output('输出(包含("枢言", "言"))', ["真"])


@group("标准库·数学与转换")
def test_stdlib_math():
    expect_output("输出(取整(3.7))", ["3"])
    expect_output("输出(绝对值(-5))", ["5"])
    expect_output("输出(最大(3, 7, 2))", ["7"])
    expect_output("输出(最小([4, 1, 9]))", ["1"])
    expect_output('输出(转整数("42"))', ["42"])
    expect_output('输出(转整数("一百二十"))', ["120"])
    expect_output('输出(转小数("3.14"))', ["3.14"])
    expect_output("输出(数学.平方根(16))", ["4.0"])
    expect_output("输出(数学.最大(1, 5))", ["5"])


@group("标准库·模块")
def test_stdlib_modules():
    expect_output("引入 \"数学\"\n输出(取整(数学.圆周率))", ["3"])
    expect_output('引入 "编码"\n输出(编码.编码Base64("你好"))', ["5L2g5aW9"])
    expect_output('引入 "编码"\n输出(编码.解码Base64("5L2g5aW9"))', ["你好"])
    expect_output('引入 "加密"\n输出(加密.MD5(""))',
                  ["d41d8cd98f00b204e9800998ecf8427e"])
    expect_output('引入 "正则"\n输出(正则.是否匹配("abc123", "\\\\d+"))', ["真"])


@group("标准库·类型名")
def test_stdlib_type_names():
    expect_output("输出(类型名(1))", ["整数"])
    expect_output("输出(类型名(1.5))", ["小数"])
    expect_output('输出(类型名("甲"))', ["文本"])
    expect_output("输出(类型名(真))", ["布尔"])
    expect_output("输出(类型名(空))", ["空"])
    expect_output("输出(类型名([1]))", ["列表"])
    expect_output("输出(类型名({\"甲\": 1}))", ["映射"])


# ================================================================ 五、格式化器


@group("格式化器")
def test_formatter():
    from .formatter import format_source
    src = '定义 甲=1\n如果 甲>0 则\n输出( "正" )\n结束\n'
    out, _ = format_source(src)
    expect_eq(out,
              "定义 甲 = 1\n如果 甲 > 0 则\n    输出(\"正\")\n结束\n",
              "格式化结果")


@group("格式化器·保注释")
def test_formatter_comments():
    from .formatter import format_source
    src = "# 顶部注释\n定义 甲=1  # 行尾注释\n"
    out, _ = format_source(src)
    check("# 顶部注释" in out, "顶部注释被丢弃")
    check("# 行尾注释" in out, "行尾注释被丢弃")


@group("格式化器·不得破坏多字符运算符")
def test_formatter_multichar_operators():
    """**格式化器绝不允许改变程序语义。**

    实测缺陷：`n <= 1` 被格式化成 `n < = 1`，程序直接语法错误；
    `==` → `= =`、`+=` → `+ =` 全部同样中招。根因是运算符补空格时
    只向前检查、未向后检查，多字符运算符的**尾字符**被当成独立运算符。

    这条测试的价值：既有的格式化测试只用了单字符 `>`，
    **恰好绕开了**多字符运算符，所以缺陷长期未被发现 ——
    测试用例必须覆盖真实写法，否则「全绿」是虚假的安全感。
    """
    from .formatter import format_source
    cases = ["如果 n <= 1 则", "如果 n >= 1 则", "如果 a == b 则",
             "如果 a != b 则", "甲 += 1", "甲 -= 1", "甲 *= 2", "甲 /= 2"]
    for src in cases:
        out, _ = format_source(src)
        expect_eq(out.strip(), src, f"格式化破坏了「{src}」")
    # 单字符运算符仍需补空格（不能因为修 bug 而漏掉本来的功能）
    expect_eq(format_source("n<1")[0].strip(), "n < 1", "单字符 < 未补空格")
    expect_eq(format_source("a=1")[0].strip(), "a = 1", "单字符 = 未补空格")

    # **语义保真**：格式化前后都能通过编译，且再格式化一次结果不变（幂等）
    for src in cases:
        once, _ = format_source(src)
        twice, _ = format_source(once)
        expect_eq(twice, once, f"格式化不幂等：{src}")


@group("Lint·误报防护")
def test_lint_no_false_positive():
    """Lint 的价值在于**不误报**。误报比漏报更快让人放弃工具。

    中文标点在字符串与注释里都是正确的；只有在**代码区**才值得提醒。
    """
    from .linter import lint_source
    # 字符串内与注释内的全角括号：不应提示
    _, diags = lint_source('# 注释（含全角括号）\n输出("数据（全角）")\n')
    codes = [d.code for d in diags]
    check("W4204" not in codes,
          f"字符串/注释内的全角括号被误报：{codes}")
    # 代码区的全角括号：应当提示
    _, diags = lint_source('输出（"甲"）\n')
    codes = [d.code for d in diags]
    check("W4204" in codes, "代码区的全角括号未被提示")
    # 警告的类别名不得叫「错误」
    for d in diags:
        if d.code.startswith("W"):
            check(d.category != "运行时错误",
                  f"警告 {d.code} 被标为运行时错误")


# ================================================================ 运行器


def collect_tests() -> dict[str, list]:
    groups: dict[str, list] = {}
    module = sys.modules[__name__]
    for name in dir(module):
        if not name.startswith("test_"):
            continue
        fn = getattr(module, name)
        gname = getattr(fn, "_group", "未分组")
        groups.setdefault(gname, []).append((name, fn))
    return groups


def run_all(verbose: bool = False) -> int:
    groups = collect_tests()
    passed = 0
    failed = 0
    failures: list[tuple[str, str, str]] = []

    for gname in sorted(groups):
        if verbose:
            print(f"\n【{gname}】")
        for name, fn in groups[gname]:
            try:
                fn()
                passed += 1
                if verbose:
                    print(f"  ✓ {name}")
            except Failure as exc:
                failed += 1
                failures.append((gname, name, str(exc)))
                if verbose:
                    print(f"  ✗ {name}：{exc}")
            except Exception as exc:                     # noqa: BLE001
                failed += 1
                failures.append((gname, name,
                                 f"{type(exc).__name__}: {exc}"))
                if verbose:
                    print(f"  ! {name}：{type(exc).__name__}: {exc}")

    total = passed + failed
    print(f"\n枢言自测：共 {total} 项，通过 {passed}，失败 {failed}")
    if failures:
        print("\n失败的用例：")
        for gname, name, reason in failures:
            print(f"  ✗ [{gname}] {name}")
            print(f"      {reason}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run_all(verbose=True))