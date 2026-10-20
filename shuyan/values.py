"""枢言语言 —— 运行时值模型与类型判定（SPEC §5）。

设计约定：
1. **类型名是中文的**：`类型名(值)` 返回 `"整数"`、`"文本"`，不是 `"int"`。
   这是中文语言的基本体面——报错里说「不能把 文本 赋给 整数」才读得通。
2. **谜题」假值表集中在此**：条件判定只有一处实现，散落各处必然出现不一致。
3. **结构相等**（列表 / 映射按值比较）在此实现，供 `==` 使用。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 枢言类型名 → 拉丁别名
TYPE_ALIASES: dict[str, str] = {
    "整数": "int", "小数": "float", "文本": "text", "布尔": "bool",
    "空": "nil", "列表": "list", "映射": "map", "函数": "fn",
    "任意": "any",
}
TYPE_BY_LATIN = {v: k for k, v in TYPE_ALIASES.items()}

# 类型加宽关系（左可隐式转为右）
WIDENING: dict[str, set[str]] = {
    "整数": {"小数", "文本", "任意"},
    "小数": {"文本", "任意"},
    "文本": {"任意"},
    "布尔": {"文本", "任意"},
    "空": {"任意"},
    "列表": {"任意"},
    "映射": {"任意"},
    "函数": {"任意"},
    "任意": {"任意"},
}


class NilValue:
    """枢言 `空`。用单例避免 `空 == 空` 因对象身份而误判。"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "空"

    def __bool__(self) -> bool:
        return False


NIL = NilValue()


@dataclass
class ShuyanMap:
    """枢言 `映射`。包一层而非直接用 dict：保证键顺序稳定、比较语义可控。"""

    data: dict = field(default_factory=dict)

    def get(self, key, default=NIL):
        return self.data.get(key, default)

    def set(self, key, value) -> None:
        self.data[_ensure_hashable(key)] = value

    def __len__(self) -> int:
        return len(self.data)

    def __contains__(self, key) -> bool:
        try:
            return _ensure_hashable(key) in self.data
        except TypeError:
            return False

    def __bool__(self) -> bool:
        return len(self.data) > 0

    def __repr__(self) -> str:
        # 走容器渲染的唯一实现，不在这里另写一份。
        return _render(self, _show_repr)


def _ensure_hashable(key):
    """映射键必须可哈希（SPEC §5.1：键须可哈希）。

    列表 / 映射 / 结构体作为键在多数语言里都不允许（可变对象做键会让
    「取回来」变得无意义）。此处**不是**要禁止它们 —— 而是要**报中文**：
    此前直接落 Python dict，用户看到的是英文的
    `unhashable type: 'list'`，既看不懂也关联不到自己写的那一行。

    用「类型名字典 + 容器类型」判定，不依赖 Python 的 `hash()` 试探 ——
    试探对某些类型有副作用，且报错信息无法给出枢言类型名。

    `error` 在函数内**延迟导入**：`errors.py` 顶层导入了本模块的
    `ShuyanMap`，若本模块顶层再导入 `errors` 会形成循环导入。
    """
    if isinstance(key, (ShuyanMap, ShuyanStruct, list)):
        from .errors import error
        raise error("类型错误",
                    f"映射的键不能是「{type_name(key)}」。"
                    "键必须可哈希，请改用整数、文本、布尔或小数"
                    "（需要以复合值为键时，可先转成文本）。",
                    实际=type_name(key))
    return key


@dataclass
class ShuyanStruct:
    """枢言 `结构体` 的值（SPEC §3.9.2）。

    字段**有序**（定义顺序），值可变。类型名即声明时的标识符，
    因此 `类型名(某点)` 返回 `"点"` 而不是 `"结构体"` ——
    报错里说「不能把 点 赋给 整数」才读得通。
    """

    type_name: str
    fields: dict = field(default_factory=dict)

    def get(self, key, default=NIL):
        return self.fields.get(key, default)

    def set(self, key, value) -> None:
        self.fields[key] = value

    def __len__(self) -> int:
        return len(self.fields)

    def __contains__(self, key) -> bool:
        return key in self.fields

    def __bool__(self) -> bool:
        # 结构体恒为真值：它不是一个「集合」，空字段的结构体仍是有效值。
        return True

    def __repr__(self) -> str:
        # 走容器渲染的唯一实现，不在这里另写一份。
        return _render(self, _show_repr)


@dataclass(frozen=True)
class EnumMember:
    """枢言 `枚举` 的一个成员（SPEC §3.9.3）。

    成员是**不可变值**，相等即「同类型且同成员」。
    """

    enum_name: str
    member_name: str
    ordinal: int                       # 序号，从 1 起

    def __repr__(self) -> str:
        return f"{self.enum_name}.{self.member_name}"


@dataclass
class ShuyanType:
    """运行期的**类型对象**（结构体 / 枚举的构造器与成员容器）。

    它只出现在 `_eval_Call` 与 `_eval_Member` 的中间过程，不会作为
    普通值被用户持有 —— 因此 `type_name` 不必为它给出名字。
    """

    name: str
    kind: str                          # struct | enum

    def __repr__(self) -> str:
        return f"<类型 {self.name}>"


def type_name(value) -> str:
    """返回枢言类型名（中文）。"""
    if value is NIL or value is None:
        return "空"
    if isinstance(value, bool):
        return "布尔"
    if isinstance(value, int):
        return "整数"
    if isinstance(value, float):
        return "小数"
    if isinstance(value, str):
        return "文本"
    if isinstance(value, list):
        return "列表"
    if isinstance(value, ShuyanStruct):
        return value.type_name
    if isinstance(value, EnumMember):
        return value.enum_name
    if isinstance(value, ShuyanMap):
        return "映射"
    if callable(value):
        return "函数"
    return "任意"


def normalized_type(ann: str | None) -> str | None:
    """把类型标注（可能是拉丁别名）归一为中文类型名。"""
    if ann is None:
        return None
    if ann in TYPE_ALIASES:
        return ann
    if ann in TYPE_BY_LATIN:
        return TYPE_BY_LATIN[ann]
    return ann                       # 自定义类型名，原样保留


def can_assign(value_type: str, target_type: str) -> bool:
    """判断 value_type 能否隐式赋给 target_type（SPEC §5.4）。"""
    if target_type in (None, "任意"):
        return True
    if value_type == target_type:
        return True
    return target_type in WIDENING.get(value_type, set())


def is_truthy(value) -> bool:
    """枢言真值判定（SPEC §4.5）。

    假值：假 / 空 / 0 / 0.0 / "" / [] / {}。
    注意 `bool` 是 `int` 的子类，须先判布尔，否则 `假` 会走 int 分支。
    """
    if value is NIL or value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return len(value) > 0
    if isinstance(value, (list, ShuyanMap)):
        return len(value) > 0
    return True


def equals(a, b) -> bool:
    """枢言相等语义（SPEC §4.6）。"""
    if a is NIL or a is None:
        return b is NIL or b is None
    if b is NIL or b is None:
        return False
    # 布尔只与布尔相等（`真 == 1` 为假）
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if isinstance(a, str) and isinstance(b, str):
        return a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(equals(x, y) for x, y in zip(a, b))
    if isinstance(a, ShuyanStruct) and isinstance(b, ShuyanStruct):
        # 结构相等：同类型、同字段集合、逐字段相等（SPEC §3.9.2）
        if a.type_name != b.type_name:
            return False
        if set(a.fields.keys()) != set(b.fields.keys()):
            return False
        return all(equals(a.fields[k], b.fields[k]) for k in a.fields)
    if isinstance(a, EnumMember) and isinstance(b, EnumMember):
        return a.enum_name == b.enum_name and a.member_name == b.member_name
    if isinstance(a, ShuyanMap) and isinstance(b, ShuyanMap):
        if set(a.data.keys()) != set(b.data.keys()):
            return False
        return all(equals(a.data[k], b.data[k]) for k in a.data)
    return a is b                    # 函数等按引用比较


def format_number(value: float) -> str:
    """小数的展示格式。

    整数值得显示为 `20.0`（明确它是小数）；长尾小数按有效数字截断到
    12 位，避免 `86.33333333333333` 这类输出淹没用户。**这仅是展示层
    的处理，数值本身不做任何精度损失** —— 参与运算的仍是原始值。

    截断后要**补回 `.0`**：`0.01` 累加 100 次得到的是
    `1.0000000000000007`，它 `is_integer()` 为假（有尾差），但截断到
    12 位有效数字后就是 `1`。若直接返回 `1`，就与 `输出(1.0)` → `1.0`
    不一致 —— 同一个数学结果出现两种写法，是展示层的缺陷而非数值问题。
    """
    if value != value:                      # NaN
        return "非数"
    if value in (float("inf"), float("-inf")):
        return "正无穷" if value > 0 else "负无穷"
    if value.is_integer() and abs(value) < 1e16:
        return f"{value:.1f}"
    text = repr(value)
    if len(text) > 15 and "e" not in text and "E" not in text:
        text = f"{value:.12g}"
        # 截断后若已是「整数值」，补 `.0` 保持与整数小数一致的写法。
        if "e" not in text and "E" not in text and "." not in text:
            text += ".0"
    return text


def _repr(value) -> str:
    """值 → 显示文本（中文语境的自然表示，文本**加引号**）。"""
    if value is NIL or value is None:
        return "空"
    if isinstance(value, bool):
        return "真" if value else "假"
    if isinstance(value, float):
        return format_number(value)
    if isinstance(value, str):
        return '"' + value + '"'
    if isinstance(value, (list, ShuyanMap, ShuyanStruct)):
        return _render(value, _show_repr)
    if isinstance(value, EnumMember):
        return repr(value)
    if isinstance(value, ShuyanType):
        return repr(value)
    if callable(value):
        name = getattr(value, "__name__", "匿名函数")
        return f"<函数 {name}>"
    return str(value)


def display(value) -> str:
    """值 → 面向用户的文本（`输出` 使用）。

    文本值**不加引号**——这是输出，不是调试表示。
    """
    if value is NIL or value is None:
        return "空"
    if isinstance(value, bool):
        return "真" if value else "假"
    if isinstance(value, float):
        return format_number(value)
    if isinstance(value, str):
        return value
    if isinstance(value, (list, ShuyanMap, ShuyanStruct)):
        return _render(value, _show_text)
    if isinstance(value, EnumMember):
        return repr(value)
    if isinstance(value, ShuyanType):
        return repr(value)
    if callable(value):
        name = getattr(value, "__name__", "匿名函数")
        return f"<函数 {name}>"
    return str(value)


def _show_text(value) -> str:
    """容器内元素的**输出口径**渲染：文本不加引号。"""
    return display(value)


def _show_repr(value) -> str:
    """容器内元素的**调试口径**渲染：文本加引号。"""
    return _repr(value)


def _render(value, elem) -> str:
    """**容器渲染的唯一实现**（列表 / 映射 / 结构体）。

    `elem` 决定元素用哪种口径渲染 —— `display` 传不加引号的，
    `_repr` 传加引号的。容器结构本身只写一份，两种口径共用。

    为什么必须收敛成一份：此前列表在 `display` 里手写一份（元素不加
    引号）、映射在 `ShuyanMap.__repr__` 里又写一份（键用 Python 的
    `{k!r}`，带单引号）、结构体再写一份。结果同一个文本值在列表里是
    `[甲, 乙]`、在映射里是 `{'甲': 1}` —— 用户直接就能看到的不一致。
    """
    if isinstance(value, list):
        return "[" + ", ".join(elem(v) for v in value) + "]"
    if isinstance(value, ShuyanMap):
        return "{" + ", ".join(f"{elem(k)}: {elem(v)}"
                               for k, v in value.data.items()) + "}"
    if isinstance(value, ShuyanStruct):
        return value.type_name + "{" + ", ".join(
            f"{k}: {elem(v)}" for k, v in value.fields.items()) + "}"
    raise TypeError("_render 只处理容器类型")


def to_text(value) -> str:
    """隐式/显式转文本（拼接与 `转文本` 使用，与 display 同口径）。"""
    return display(value)