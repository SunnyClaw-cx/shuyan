"""中文切分算法实测脚本（开发期用，不属于交付物）。"""
import sys
sys.path.insert(0, r"E:/360MoveData/Users/lenovo/Desktop/语言编译")

from shuyan.lexer import Lexer, Kind

CASES = [
    "定义年龄=20",
    "苹果个数=5",
    "年龄 是 20",
    "年龄不小于18",
    "数据中台 是 1",
    "期中成绩 是 90",
    "规则 是 1",
    "无线网络 是 真",
    "重复三次",
    "一百二十",
    "十五",
    "三百零五",
    "3万",
    "遍历名单中的每个名字",
    "否则若条件",
    "返回结果",
    "如果 条件成立",
    "订单_ID 是 1",
    "`定义年龄` 是 1",
    "名为张三",
]

for src in CASES:
    lx = Lexer(src)
    try:
        toks = lx.tokenize()
        desc = " | ".join(f"{t.kind.name}:{t.value}" for t in toks if t.kind is not Kind.EOF)
        print(f"{src:<24} => {desc}")
    except Exception as e:
        print(f"{src:<24} => 错误: {e}")