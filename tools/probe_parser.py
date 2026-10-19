"""语法分析实测（开发期）。"""
import sys
sys.path.insert(0, r"E:/360MoveData/Users/lenovo/Desktop/语言编译")

from shuyan.lexer import Lexer
from shuyan.parser import Parser
from shuyan.diagnostics import render_all, ParseError

SRC = """# 形式中文
定义 年龄 = 20
常量 名字 = "小明"
如果 年龄 >= 18 则
    输出("成年：" + 名字)
否则
    输出("未成年")
结束

遍历 名字 中 名字列表
    输出(名字)
结束

重复 3 次
    输出("第 " + 转文本(1))
结束

请 相加(甲, 乙)
    返回 甲 + 乙
结束
"""

SRC2 = """# 白话文
设 年龄 是 20
令 名字 是 "小明"
若 年龄 不小于 18，那么
    输出("成年")
否则的话
    输出("未成年")
完

遍历 名单 中的每个 名字
    输出(名字)
完

重复三次
    输出("嗨")
完
"""

for label, src in (("形式中文", SRC), ("白话文", SRC2)):
    print(f"===== {label} =====")
    try:
        toks = Lexer(src).tokenize()
        tree = Parser(toks).parse()
        for st in tree.body:
            print(" ", type(st).__name__, getattr(st, "name", ""),
                  getattr(st, "var_name", ""))
    except Exception as e:
        print(render_all(e.diagnostics, src))