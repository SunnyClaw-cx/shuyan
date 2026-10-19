"""枢言语言 —— 关键字表（SPEC §2.3）。

关键字按**切分行为**分三类，分类即为算法输入：

- A 类 LEADING：引导词。出现在字块开头时，可与后续标识符**紧贴**切分。
      例：`定义年龄` → `定义` + `年龄`
- B 类 STANDALONE：结构词。**必须整块命中**才成立。混在其他字里不切。
      例：`否则` 成立；`否则若` 属 A 类（见下）。
- C 类 TRAILING：联结词。出现在字块**尾部或中间**时切分。
      例：`年龄是20` → `年龄` + `是` + `20`

另有两类辅助集合：
- SAFE_INFIX：C 类中**长度 ≥ 2 且语义唯一**的子集，允许在字块**中间**切分。
      单字联结词（中/从/到/是）**不参与中缀切分**——它们撞词的概率太高
      （`期中成绩` 若允许中缀切分会裂成 `期`+`中`+`成绩`，这是不可接受的）。
- FILLER_SOFT / FILLER_EXACT：软助词，不产生记号，仅用于让白话句式更自然。
      `的话`/`那么` 可作后缀剥离；`则`/`成立`/`时候` 仅整块命中才剥离
      ——`规则` 不能因为末尾有 `则` 就被削成 `规`。
"""

from __future__ import annotations

# ---------------------------------------------------------------- A 类：引导词

LEADING: dict[str, str] = {
    # 声明
    "定义": "let",
    "设": "let",
    "常量": "const",
    "令": "const",
    "全局": "global",
    # 分支
    "如果": "if",
    "若": "if",
    "否则如果": "elif",
    "否则若": "elif",
    "否则": "else",
    # 循环
    "当": "while",
    "只要": "while",
    "遍历": "for",
    "对于": "for",
    "重复": "loop",
    "循环": "loop",
    # 函数
    "请": "fn",
    "函数": "fn",
    "让": "fn",
    # 控制
    "返回": "return",
    "抛开": "throw",
    "抛出": "throw",
    "跳出": "break",
    "继续": "continue",
    # 异常
    "试": "try",
    "尝试": "try",
    "捕获": "catch",
    "出错了": "catch",
    "最终": "finally",
    "最后": "finally",
    # 其他
    "引入": "use",
    "导入": "use",
    "断言": "assert",
    "类型": "type",
}

# ---------------------------------------------------- B 类：结构词（整块命中）

STANDALONE: dict[str, str] = {
    "真": "true",
    "真值": "true",
    "假": "false",
    "假值": "false",
    "空": "nil",
    "空值": "nil",
    "且": "and",
    "并且": "and",
    "或": "or",
    "或者": "or",
    "非": "not",
    "结束": "end",
    "完": "end",
}

# ------------------------------------------------------------ C 类：联结词

TRAILING: dict[str, str] = {
    # 单字联结词（撞词高风险，切分受严格约束，见 C_SINGLE 与 §2.4）
    "是": "is",
    "为": "is",
    "叫": "is",
    "从": "from",
    "到": "to",
    "中": "in",
    "里": "in",
    # 多字联结词（撞词风险低，可无约束切分）
    "步长": "step",
    "属于": "in",
    "不属于": "not_in",
    "中的": "in",
    "里的": "in",
    "中的每个": "each",
    "里的每个": "each",
    "里面的每个": "each",
    "每个": "each",
    "每一个": "each",
    "次": "times",
}

# 单字联结词：**仅在**「整块命中」或「前缀长 ≥ 2 且剩余长 ≥ 2」时切分。
# 这是防止 `数据中台`、`期中成绩` 被误裂的关键约束。
C_SINGLE: frozenset[str] = frozenset({"是", "为", "叫", "从", "到", "中", "里"})

# 单字 A 类引导词：**仅当剩余部分本身是关键字时**才作前缀切分。
#
# 这是由**实测真实项目**得出的约束。原先 A 类前缀无条件最长匹配，
# 于是下列极常见的词会被切坏（实测记录）：
#     当前目录 → `当`(while) + `前目录`      ← 「当前目录」几乎人人要写
#     设备清单 → `设`(let)   + `备清单`
#     设计图纸 → `设`(let)   + `计图纸`
#     设置参数 → `设`(let)   + `置参数`
#     当时     → `当`(while) + `时`
#     若干条   → `若`(if)    + `干条`
#     请求处理 → `请`(fn)    + `求处理`
#     试验记录 → `试`(try)   + `验记录`
#     让步     → `让`(fn)    + `步`
#
# 这与 C_SINGLE 是**同一类问题**：单字词频率太高，与词组撞车不可避免。
# 约束口径也照同一原则 —— 单字词必须**独立成块**（或用空格分开）。
# 两字及以上的 A 类词（定义/如果/重复/遍历…）撞词概率低，
# 仍保留融合写法（`定义年龄=20` 是规范承诺的用法）。
A_SINGLE: frozenset[str] = frozenset(
    {w for w in LEADING if len(w) == 1}
)

# 多字联结词：撞词概率极低，允许无约束切分（含中文运算符词）
C_MULTI: frozenset[str] = frozenset(
    {w for w in TRAILING if len(w) >= 2} | {"加上", "减去", "乘以", "除以", "取余",
                                            "等于", "相等", "不等于", "不是",
                                            "大于", "小于", "不小于", "不大于",
                                            "至少", "至多", "次方"}
)

# 中文运算符词 → 符号（词法期直接产出 OP 记号）
WORD_OPERATORS: dict[str, str] = {
    "加上": "+",
    "加上去": "+",
    "减去": "-",
    "乘以": "*",
    "除以": "/",
    "取余": "%",
    "等于": "==",
    "相等": "==",
    "不等于": "!=",
    "不是": "!=",
    "大于": ">",
    "小于": "<",
    "不小于": ">=",
    "至少": ">=",
    "不大于": "<=",
    "至多": "<=",
    "次方": "^",
}

# 软助词：可作**后缀**剥离（最长优先），以及**整块**命中时剥离
FILLER_SOFT: tuple[str, ...] = ("的时候", "的话", "那么", "以后", "之前")
FILLER_EXACT: frozenset[str] = frozenset({
    "则", "成立", "时候", "都", "就", "开始", "然后",
})

# 内置函数的口语变体 → 规范名。
#
# 中文口语常在动词后加量词/助词（`说一声`、`打一下`），这是最自然的说法。
# 这类「动词 + 助词」的黏连若靠通用切分规则处理，会与标识符严重冲突；
# 故此处**列举式**收编，只承认少数高频口语形式，规则明确、不外溢。
COLLOQUIAL_BUILTINS: dict[str, str] = {
    "说一声": "说",
    "说一下": "说",
    "讲一声": "说",
    "打印出来": "打印",
    "算一下": "计算",
    "看一看": "查看",
}

# ------------------------------------------------------------- 拉丁别名

LATIN: dict[str, str] = {
    "let": "let", "var": "let",
    "const": "const",
    "if": "if", "elif": "elif", "else": "else",
    "while": "while", "for": "for", "loop": "loop",
    "fn": "fn", "func": "fn", "function": "fn", "def": "fn",
    "return": "return", "throw": "throw", "raise": "throw",
    "break": "break", "continue": "continue",
    "try": "try", "catch": "catch", "finally": "finally",
    "true": "true", "false": "false", "nil": "nil", "null": "nil",
    "and": "and", "or": "or", "not": "not",
    "is": "is", "in": "in", "from": "from", "to": "to",
    "step": "step", "each": "each", "times": "times",
    "use": "use", "import": "use",
    "assert": "assert", "type": "type", "global": "global",
    "end": "end",
}


def all_surfaces() -> frozenset[str]:
    """全部关键字表面形式（供标识符合法性检查与格式化器使用）。"""
    return frozenset(
        list(LEADING) + list(STANDALONE) + list(TRAILING)
        + list(WORD_OPERATORS) + list(FILLER_SOFT) + list(FILLER_EXACT)
    )


def is_filler_exact(block: str) -> bool:
    return block in FILLER_EXACT


def strip_filler_suffix(block: str) -> tuple[str, bool]:
    """剥离块尾软助词。返回 (剩余部分, 是否剥离过)。"""
    for f in FILLER_SOFT:
        if block.endswith(f) and len(block) > len(f):
            return block[: -len(f)], True
    return block, False