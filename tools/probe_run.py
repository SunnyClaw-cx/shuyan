"""端到端执行实测（开发期）。"""
import io
import sys
sys.path.insert(0, r"E:/360MoveData/Users/lenovo/Desktop/语言编译")

from shuyan.lexer import Lexer
from shuyan.parser import Parser
from shuyan.semantic import Analyzer
from shuyan.interpreter import Interpreter
from shuyan.builtins import StdLib
from shuyan.diagnostics import render_all

SRC = '''# 枢言示例：斐波那契、列表、字符串
请 斐波那契(n)
    如果 n <= 1 则
        返回 n
    结束
    返回 斐波那契(n - 1) + 斐波那契(n - 2)
结束

定义 结果表 = []
重复 10 次
    入列(结果表, 斐波那契(长度(结果表)))
结束
输出("斐波那契前10项：" + 连接(结果表, ", "))

定义 名字 = "小明"
定义 年龄 = 20
输出(`你好，{名字}！你今年 {年龄} 岁。`)

定义 名单 = ["张三", "李四", "王五"]
遍历 名字 中 名单
    输出("你好，" + 名字)
结束

定义 总分 = 0
遍历 分数 中 [88, 95, 76]
    总分 += 分数
结束
输出("总分 " + 转文本(总分) + "，平均 " + 转文本(总分 / 3))

试
    抛出 {"类型": "业务错误", "信息": "库存不足"}
捕获 异常
    输出("捕获到：" + 异常["信息"])
最终
    输出("收尾")
结束
'''

src_lines = SRC.splitlines()
lib = StdLib()
lib.capture = True
try:
    toks = Lexer(SRC).tokenize()
    tree = Parser(toks).parse()
    an = Analyzer(lib)
    types, diags = an.analyze(tree)
    errs = [d for d in diags if d.severity.value == "错误"]
    warns = [d for d in diags if d.severity.value == "警告"]
    if errs:
        print(render_all(diags, SRC))
    else:
        if warns:
            print("[警告]", len(warns))
            print(render_all(warns, SRC))
        interp = Interpreter(lib, source_lines=src_lines)
        code = interp.run(tree)
        print("--- 程序输出 ---")
        print("\n".join(lib.written))
        print("--- 退出码 ---", code)
except Exception as e:
    import traceback; traceback.print_exc()