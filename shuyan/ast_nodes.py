"""枢言语言 —— 抽象语法树节点定义（SPEC §3、§9）。

设计约定：
1. 每个节点携带 `span`，用于报错定位；
2. 双语端口（形式中文 / 白话文）**归一为同一组节点**——这是「白话可编程」
   与「工程严谨性」能够共存的前提：白话不是另一门语言，只是同一 AST 的另一种输入皮肤；
3. 节点极简，不携带类型信息（类型挂在语义分析产物的旁表上，保持 AST 纯净）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .source import Span


@dataclass
class Node:
    span: Span = field(default_factory=lambda: Span(0, 0, 0, 0))


# ------------------------------------------------------------------ 表达式


@dataclass
class Literal(Node):
    value: Any = None
    literal_kind: str = "number"      # number | text | bool | nil


@dataclass
class InterpolatedString(Node):
    parts: list = field(default_factory=list)   # [("text", str) | ("expr", Node)]


@dataclass
class Identifier(Node):
    name: str = ""


@dataclass
class ListLiteral(Node):
    items: list = field(default_factory=list)


@dataclass
class MapLiteral(Node):
    pairs: list = field(default_factory=list)   # [(keyNode, valueNode)]


@dataclass
class Unary(Node):
    op: str = ""
    operand: Node | None = None


@dataclass
class Binary(Node):
    op: str = ""
    left: Node | None = None
    right: Node | None = None


@dataclass
class Logical(Node):
    op: str = ""                      # and | or
    left: Node | None = None
    right: Node | None = None


@dataclass
class Compare(Node):
    op: str = ""
    left: Node | None = None
    right: Node | None = None


@dataclass
class Ternary(Node):
    cond: Node | None = None
    then: Node | None = None
    otherwise: Node | None = None


@dataclass
class Call(Node):
    callee: Node | None = None
    args: list = field(default_factory=list)
    kwargs: dict = field(default_factory=dict)


@dataclass
class Index(Node):
    target: Node | None = None
    index: Node | None = None
    is_slice: bool = False
    start: Node | None = None
    stop: Node | None = None


@dataclass
class Member(Node):
    target: Node | None = None
    name: str = ""
    # 属性名的**源码原文**。
    # 枚举成员名必须按原文匹配：`星期.一` 的 `一` 在词法层是中文数词
    # （`name` 已被归一成 "1"），只有原文才对得上声明 `{ 一, 二, ... }`。
    name_text: str = ""


@dataclass
class RangeExpr(Node):
    start: Node | None = None
    stop: Node | None = None


@dataclass
class FunctionExpr(Node):
    params: list = field(default_factory=list)
    body: list = field(default_factory=list)
    name: str = ""                    # 匿名函数为空


# ------------------------------------------------------------------ 语句


@dataclass
class Program(Node):
    body: list = field(default_factory=list)


@dataclass
class VarDecl(Node):
    name: str = ""
    type_ann: str | None = None
    init: Node | None = None
    is_const: bool = False
    is_global: bool = False


@dataclass
class Assign(Node):
    target: Node | None = None
    op: str = "="
    value: Node | None = None


@dataclass
class ExprStmt(Node):
    expr: Node | None = None


@dataclass
class If(Node):
    branches: list = field(default_factory=list)   # [(condNode|None, [stmts])]
    # branches[-1] 的 cond 为 None 表示 `否则` 分支


@dataclass
class While(Node):
    cond: Node | None = None
    body: list = field(default_factory=list)


@dataclass
class RepeatTimes(Node):
    times: Node | None = None
    body: list = field(default_factory=list)


@dataclass
class RepeatRange(Node):
    var_name: str = ""
    start: Node | None = None
    stop: Node | None = None
    step: Node | None = None
    body: list = field(default_factory=list)


@dataclass
class ForEach(Node):
    var_name: str = ""
    iterable: Node | None = None
    body: list = field(default_factory=list)


@dataclass
class FuncDef(Node):
    name: str = ""
    params: list = field(default_factory=list)
    body: list = field(default_factory=list)
    return_type: str | None = None
    doc: str = ""
    examples: list = field(default_factory=list)
    is_generator_hint: bool = False


@dataclass
class Param:
    name: str = ""
    type_ann: str | None = None
    default: Node | None = None
    is_variadic: bool = False
    span: Span = field(default_factory=lambda: Span(0, 0, 0, 0))


@dataclass
class Return(Node):
    value: Node | None = None


@dataclass
class Throw(Node):
    value: Node | None = None


@dataclass
class Try(Node):
    body: list = field(default_factory=list)
    handlers: list = field(default_factory=list)   # [(varName|None, [stmts])]
    finalizer: list | None = None


@dataclass
class Break(Node):
    pass


@dataclass
class Continue(Node):
    pass


@dataclass
class Use(Node):
    module: str = ""
    names: list | None = None         # None 表示整体导入


@dataclass
class TypeDecl(Node):
    """类型声明（SPEC §3.9）。

    `kind` 三态：
      - `alias`  类型别名，`definition` 为被别名者
      - `struct` 结构体，`fields` 为 [(字段名, 类型表达式, Span), ...]
      - `enum`   枚举，`members` 为 [(成员名, Span), ...]
    """

    name: str = ""
    definition: str = ""
    kind: str = "alias"
    fields: list = field(default_factory=list)
    members: list = field(default_factory=list)


@dataclass
class Assert(Node):
    cond: Node | None = None
    message: Node | None = None


@dataclass
class Block(Node):
    body: list = field(default_factory=list)


# ------------------------------------------------------------ 遍历工具

def iter_child_nodes(node: Node):
    """产出节点的直接子节点（供语义分析与格式化器统一遍历）。"""
    import dataclasses
    if not isinstance(node, Node):
        return
    for f in dataclasses.fields(node):
        if f.name == "span":
            continue
        value = getattr(node, f.name)
        if isinstance(value, Node):
            yield value
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, Node):
                    yield item
                elif isinstance(item, tuple):
                    for sub in item:
                        if isinstance(sub, Node):
                            yield sub
                        elif isinstance(sub, list):
                            for s in sub:
                                if isinstance(s, Node):
                                    yield s
        elif isinstance(value, dict):
            for v in value.values():
                if isinstance(v, Node):
                    yield v


def walk(node: Node):
    """深度优先遍历整棵子树（含自身）。"""
    yield node
    for child in iter_child_nodes(node):
        yield from walk(child)