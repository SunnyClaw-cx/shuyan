"""枢言语言 —— 词法分析器（SPEC §2）。

职责：把 UTF-8 中文源码文本流转成记号流（Token）。

中文语言的三个特殊性，本模块逐一处理：
1. **全角归一化**：中文输入法下 `（）`/`()` 混用是高频事故，在词法最前一步归一；
2. **汉字粘连切分**：`定义年龄` 必须切成 `定义` + `年龄`，而 `数据中台` 必须整体保留
   —— 采用 SPEC §2.4 的「前缀 / 后缀 / 中缀」三通道贪心最长匹配；
3. **显示列**：列号按汉字算 2 列计算，保证报错下划线对齐。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from . import keywords as kw
from . import names as reserved
from .diagnostics import (
    Diagnostic, E_AMBIGUOUS_PUNCT, E_BAD_NUMBER, E_IDENT_STARTS_DIGIT,
    E_INTERP_UNCLOSED, E_UNCLOSED_COMMENT, E_UNCLOSED_STRING, E_UNKNOWN_CHAR,
    LexError, Severity,
)
from .source import Span, display_width, is_han

# ------------------------------------------------------------------ 记号定义


class Kind(Enum):
    KEYWORD = auto()
    IDENT = auto()
    NUMBER = auto()
    STRING = auto()
    OP = auto()
    NEWLINE = auto()
    EOF = auto()


@dataclass
class Token:
    kind: Kind
    value: str            # 关键字的规范语义名 / 标识符原文 / 运算符符号
    text: str             # 源码原文（供格式化与报错还原）
    span: Span
    # 字符串专用
    parts: list | None = None      # 插值串：["字面", ("expr", 源码文本), ...]
    quote: str = ""                # 字符串定界符原文

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"Token({self.kind.name}, {self.value!r}@{self.span.line}:{self.span.col})"


# ------------------------------------------------------------ 全角归一化表

FULLWIDTH_MAP: dict[str, str] = {
    "（": "(", "）": ")", "［": "[", "］": "]",
    "｛": "{", "｝": "}", "，": ",", "；": ";",
    "：": ":", "。": ";", "、": ",",
    "＝": "=", "＋": "+", "－": "-", "＊": "*", "／": "/", "％": "%",
    "＜": "<", "＞": ">", "！": "!", "？": "?", "＆": "&", "｜": "|",
    "～": "~", "＾": "^", "＠": "@", "＃": "#", "＿": "_", "＄": "$",
    "　": " ", "＂": '"', "＇": "'",
    "“": '"', "”": '"', "‘": "'", "’": "'",
}

# 意图不明：报了它反而会掩盖用户真实错误，故明确报错
AMBIGUOUS_PUNCT: dict[str, str] = {
    "《": "书名号《》，中文源码里请改用双引号 \" 或单引号 '",
    "》": "书名号》，同上",
    "—": "破折号——，请改用减号 -（运算符）或去掉",
    "…": "省略号……，请改用三个英文句点 ...",
    "【": "方头括号【，请改用方括号 [",
    "】": "方头括号】，请改用方括号 ]",
    "·": "间隔号·，不作为运算符；如需取成员请用 . 或直接写属性名",
}

# 中文字符 → 数值（数词字面量）
CN_DIGITS: dict[str, int] = {
    "零": 0, "〇": 0, "一": 1, "壹": 1, "二": 2, "两": 2, "贰": 2,
    "三": 3, "叁": 3, "四": 4, "肆": 4, "五": 5, "伍": 5,
    "六": 6, "陆": 6, "七": 7, "柒": 7, "八": 8, "捌": 8, "九": 9, "玖": 9,
}
CN_UNITS_SMALL: dict[str, int] = {"十": 10, "拾": 10, "百": 100, "佰": 100, "千": 1000, "仟": 1000}
CN_UNITS_BIG: dict[str, int] = {"万": 10_000, "亿": 100_000_000, "兆": 10_000_000_000_000}

CN_NUM_CHARS: frozenset[str] = frozenset(
    list(CN_DIGITS) + list(CN_UNITS_SMALL) + list(CN_UNITS_BIG)
)

# 多字符运算符（最长优先）
SYMBOL_OPS: tuple[str, ...] = (
    "...", "**", "//", "==", "!=", "<=", ">=", "+=", "-=", "*=", "/=", "%=",
    "->", "=>", "..", "&&", "||",
    "+", "-", "*", "/", "%", "^", "=", "<", ">", "!", "(", ")", "[", "]",
    "{", "}", ",", ";", ":", ".", "&", "|", "~", "@",
)

IDENT_EXTRA: frozenset[str] = frozenset({"_"})

# 保留名整体保护集合（内置函数 / 模块 / 类型名）
PROTECTED_NAMES: frozenset[str] = reserved.PROTECTED_NAMES


def parse_cn_number(text: str) -> int | float | None:
    """解析中文数词。失败返回 None。

    支持 `一百二十`、`十五`、`三百零五`、`两千零一`、`十`、`十亿`、`零`。
    规则：按「单位递降」分段累加；小单位（十/百/千）在同一小节内递降，
    大单位（万/亿/兆）作为小节的乘数。
    """
    if not text:
        return None

    # 纯数字字符序列（无单位）按中文习惯**拼接**：
    # 一九九八 → 1998，一二三 → 123。这是年份、编号的通用写法。
    if all(ch in CN_DIGITS for ch in text):
        if len(text) == 1:
            return CN_DIGITS[text]
        if text.startswith("零"):
            return None                    # `零一` 之类不合法
        digits = "".join(str(CN_DIGITS[ch]) for ch in text)
        return int(digits)

    total = 0          # 已结算的大单位小节之和
    section = 0        # 当前小节的值
    number = 0         # 当前累计的个位数
    seen_any = False
    saw_unit = False

    for ch in text:
        if ch in CN_DIGITS:
            if CN_DIGITS[ch] == 0 and number == 0 and section == 0 \
                    and total == 0 and not saw_unit:
                seen_any = True          # 裸 `零`：占位
                continue
            number = CN_DIGITS[ch]
            seen_any = True
        elif ch in CN_UNITS_SMALL:
            unit = CN_UNITS_SMALL[ch]
            if number == 0:
                # `十五` / `十亿` 情形：前面没有数字，视作 1*单位
                number = 1
            section += number * unit
            number = 0
            saw_unit = True
            seen_any = True              # `十` 单独出现也是合法数词
        elif ch in CN_UNITS_BIG:
            unit = CN_UNITS_BIG[ch]
            section += number
            if section == 0:
                # `十亿` / `万亿` 情形：大单位前没有小节的数字，视作 1 个小节
                section = 1
            total += section * unit
            section = 0
            number = 0
            saw_unit = True
            seen_any = True
        else:
            return None
    section += number

    if not seen_any:
        return None
    result = total + section
    return result


class Lexer:
    """枢言词法分析器。"""

    def __init__(self, source: str, filename: str = "<源码>"):
        self.filename = filename
        self.raw = source
        # 全角归一化：长度不变的多对一映射会破坏列号，故用「规范化副本」+「原文副本」双轨
        self.norm = "".join(FULLWIDTH_MAP.get(ch, ch) for ch in source)
        self.pos = 0
        self.line = 1
        self.col = 1                # 显示列
        self.tokens: list[Token] = []
        self.diagnostics: list[Diagnostic] = []
        # 括号深度：>0 时抑制 NEWLINE（自动续行）
        self.paren_depth = 0
        # 上一有效记号的类型，用于续行判定
        self._prev: Token | None = None

    # ------------------------------------------------------------ 基础工具

    def _peek(self, offset: int = 0) -> str:
        idx = self.pos + offset
        return self.norm[idx] if 0 <= idx < len(self.norm) else "\0"

    def _raw_at(self, offset: int = 0) -> str:
        idx = self.pos + offset
        return self.raw[idx] if 0 <= idx < len(self.raw) else "\0"

    def _advance(self, n: int = 1) -> None:
        for _ in range(n):
            if self.pos >= len(self.norm):
                return
            ch = self.raw[self.pos]
            self.pos += 1
            if ch == "\n":
                self.line += 1
                self.col = 1
            elif ch == "\r":
                pass                      # CR 不占列
            elif ch == "\t":
                self.col += 4
            else:
                self.col += display_width(ch)

    def _here(self) -> tuple[int, int]:
        return self.line, self.col

    def _span_from(self, start: tuple[int, int]) -> Span:
        return Span(start[0], start[1], self.line, self.col)

    def _error(self, code: str, message: str, span: Span,
               hints: list[str] | None = None) -> None:
        self.diagnostics.append(
            Diagnostic(code, message, span, Severity.ERROR, hints or [])
        )

    def _emit_prev_category(self) -> bool:
        """上一记号是否为「需要自动续行」的运算符。"""
        if self._prev is None:
            return False
        if self._prev.kind is Kind.OP and self._prev.value in (
            "+", "-", "*", "/", "%", "==", "!=", "<", ">", "<=", ">=",
            "=", "+=", "-=", "*=", "/=", "%=", "^", "&&", "||", ".", "..",
            "->", ":",
        ):
            return True
        if self._prev.kind is Kind.KEYWORD and self._prev.value in (
            "and", "or", "not", "is", "in", "from", "to", "step", "each",
            "times", "let", "const", "global", "if", "elif", "while", "for",
            "return", "throw", "catch", "type", "use", "assert",
        ):
            return True
        return False

    def _push(self, tok: Token) -> None:
        self.tokens.append(tok)
        if tok.kind not in (Kind.NEWLINE, Kind.EOF):
            self._prev = tok

    # ------------------------------------------------------------ 主循环

    def tokenize(self) -> list[Token]:
        while self.pos < len(self.norm):
            ch = self._peek()

            # 空白
            if ch in " \t\r":
                self._advance()
                continue

            # 行注释
            if ch == "#" or (ch == "/" and self._peek(1) == "/"):
                while self.pos < len(self.norm) and self._peek() != "\n":
                    self._advance()
                continue

            # 块注释
            if ch == "/" and self._peek(1) == "*":
                self._block_comment()
                continue

            # 换行 → NEWLINE（按续行规则决定是否发出）
            if ch == "\n":
                start = self._here()
                self._advance()
                if self.paren_depth > 0 or self._emit_prev_category():
                    continue
                if self.tokens and self.tokens[-1].kind is Kind.NEWLINE:
                    continue
                self._push(Token(Kind.NEWLINE, "\n", "\n",
                                 Span(start[0], start[1], self.line, self.col)))
                continue

            # 字符串
            if ch in ('"', "'", "`"):
                self._string()
                continue

            # 数字（含 3万 这类尾随单位）
            if ch.isdigit():
                self._number()
                continue

            # 拉丁标识符 / 拉丁关键字
            if ch.isascii() and (ch.isalpha() or ch == "_"):
                self._latin_word()
                continue

            # 汉字块
            if is_han(ch):
                self._han_block()
                continue

            # 反引号标识符已在 _string 分支处理（反引号非插值场景）

            # 符号
            if self._symbol():
                continue

            # 全部不匹配 → 非法字符
            start = self._here()
            raw_ch = self._raw_at()
            if raw_ch in AMBIGUOUS_PUNCT:
                self._error(E_AMBIGUOUS_PUNCT,
                            f"无法识别的标点「{raw_ch}」。",
                            Span(start[0], start[1], start[0], start[1] + 1),
                            [AMBIGUOUS_PUNCT[raw_ch]])
            else:
                self._error(E_UNKNOWN_CHAR,
                            f"源码中出现枢言不认识的字符「{raw_ch}」。",
                            Span(start[0], start[1], start[0], start[1] + 1),
                            ["检查是否误用了其他语言或输入法的特殊符号"])
            self._advance()

        self._push(Token(Kind.EOF, "", "", Span(self.line, self.col, self.line, self.col)))
        if self.diagnostics:
            raise LexError(self.diagnostics)
        return self.tokens

    # ------------------------------------------------------------ 各分支

    def _block_comment(self) -> None:
        start = self._here()
        self._advance(2)
        while self.pos < len(self.norm):
            if self._peek() == "*" and self._peek(1) == "/":
                self._advance(2)
                return
            self._advance()
        self._error(E_UNCLOSED_COMMENT, "块注释没有闭合，缺少结尾的 */。",
                    self._span_from(start), ["补上 */"])

    def _string(self) -> None:
        quote = self._peek()
        start = self._here()
        raw_start = self.pos
        self._advance()
        interp = quote == "`"
        is_backtick_ident = quote == "`"

        buf: list[str] = []
        parts: list = []
        seg_start = self.pos

        def flush_literal(upto: int) -> None:
            literal = "".join(buf)
            if literal:
                parts.append(("text", literal))
            buf.clear()

        while self.pos < len(self.norm):
            ch = self._peek()
            # 字符串**内容**一律取自原文，不参与全角归一化
            # —— `"你好，世界！"` 里的标点是数据，不是语法。
            raw_ch = self.raw[self.pos]
            if ch == "\\":
                nxt = self._peek(1)
                escapes = {"n": "\n", "t": "\t", "r": "\r", "0": "\0",
                           "\\": "\\", '"': '"', "'": "'", "`": "`", "{": "{", "}": "}"}
                if nxt in escapes:
                    buf.append(escapes[nxt])
                    self._advance(2)
                    continue
                buf.append("\\")
                self._advance()
                continue
            if ch == quote:
                self._advance()
                text = "".join(buf)
                if text:
                    parts.append(("text", text))
                raw_text = self.raw[raw_start:self.pos]
                # 纯反引号且无插值表达式 → 视为反引号标识符
                if is_backtick_ident and not any(p[0] == "expr" for p in parts):
                    span = self._span_from(start)
                    self._push(Token(Kind.IDENT, text, raw_text, span))
                    return
                span = self._span_from(start)
                self.tokens.append(Token(Kind.STRING, text, raw_text, span,
                                         parts=parts, quote=raw_text[0]))
                self._prev = self.tokens[-1]
                return
            if interp and ch == "{":
                if self._peek(1) == "{":
                    buf.append("{")
                    self._advance(2)
                    continue
                text = "".join(buf)
                if text:
                    parts.append(("text", text))
                buf.clear()
                self._advance()
                expr_start = self.pos
                depth = 1
                while self.pos < len(self.norm) and depth > 0:
                    c = self._peek()
                    if c == "{" :
                        depth += 1
                    elif c == "}":
                        depth -= 1
                        if depth == 0:
                            break
                    elif c == "\n":
                        self._error(E_INTERP_UNCLOSED,
                                    "插值表达式跨行且未闭合。",
                                    self._span_from(start),
                                    ["让 {…} 在同一行内闭合"])
                        return
                    self._advance()
                if depth != 0:
                    self._error(E_INTERP_UNCLOSED,
                                "插值字符串中的 { 没有对应的 }。",
                                self._span_from(start), ["补上 }"])
                    return
                expr_src = self.norm[expr_start:self.pos].strip()
                parts.append(("expr", expr_src))
                self._advance()      # 吃掉 }
                continue
            if ch == "\n" and quote != "`":
                break
            buf.append(raw_ch)
            self._advance()

        # 未闭合
        self._error(E_UNCLOSED_STRING,
                    f"{'插值' if interp else ''}字符串没有闭合，缺少结尾的 {quote}。",
                    self._span_from(start), [f"补上 {quote}"])

    def _number(self) -> None:
        start = self._here()
        raw_start = self.pos
        buf: list[str] = []

        if self._peek() == "0" and self._peek(1) in ("x", "X"):
            self._advance(2)
            while self._peek() in "0123456789abcdefABCDEF_":
                if self._peek() != "_":
                    buf.append(self._peek())
                self._advance()
            text = "".join(buf)
            if not text:
                self._bad_number(start, raw_start, "十六进制字面量缺少数字")
                return
            self._finish_number(start, raw_start, int(text, 16))
            return

        if self._peek() == "0" and self._peek(1) in ("b", "B"):
            self._advance(2)
            while self._peek() in "01_":
                if self._peek() != "_":
                    buf.append(self._peek())
                self._advance()
            text = "".join(buf)
            if not text:
                self._bad_number(start, raw_start, "二进制字面量缺少数字")
                return
            self._finish_number(start, raw_start, int(text, 2))
            return

        if self._peek() == "0" and self._peek(1) in ("o", "O"):
            self._advance(2)
            while self._peek() in "01234567_":
                self._advance()
            self._bad_number(start, raw_start,
                             "枢言 v0.1 暂不支持八进制字面量（0o 前缀）",
                             ["改用十进制，或十六进制 0x"])
            return

        # 十进制
        while self._peek().isdigit() or self._peek() == "_":
            if self._peek() != "_":
                buf.append(self._peek())
            self._advance()
        is_float = False
        if self._peek() == "." and self._peek(1) != "." and self._peek(1).isdigit():
            is_float = True
            buf.append(".")
            self._advance()
            while self._peek().isdigit() or self._peek() == "_":
                if self._peek() != "_":
                    buf.append(self._peek())
                self._advance()
        if self._peek() in ("e", "E") and (
            self._peek(1).isdigit()
            or (self._peek(1) in "+-" and self._peek(2).isdigit())
        ):
            is_float = True
            buf.append(self._peek())
            self._advance()
            if self._peek() in "+-":
                buf.append(self._peek())
                self._advance()
            while self._peek().isdigit():
                buf.append(self._peek())
                self._advance()

        text = "".join(buf)
        # 前导零检查**只针对整数**：`0`、`0.0`、`0.5`、`0e3` 都是合法的，
        # 只有 `0777` 这类「以 0 开头的多位数整数」才是错误写法。
        if not is_float and len(text) > 1 and text[0] == "0":
            self._bad_number(start, raw_start,
                             f"数字字面量 {text} 以 0 开头（不支持前导零）",
                             ["去掉前导零"])
            return
        value: int | float = float(text) if is_float else int(text)

        # 尾随缩写单位：3万 / 20万 / 1.5亿 / 10兆
        ch = self._peek()
        if ch in CN_UNITS_BIG:
            self._advance()
            value = value * CN_UNITS_BIG[ch]
            if isinstance(value, float) and value.is_integer():
                value = int(value)
        self._finish_number(start, raw_start, value)

    def _finish_number(self, start, raw_start, value) -> None:
        raw_text = self.raw[raw_start:self.pos]
        span = self._span_from(start)
        self._push(Token(Kind.NUMBER, str(value), raw_text, span))

    def _bad_number(self, start, raw_start, why: str, hints=None) -> None:
        # 吃掉后续非法数字字符，避免连锁报错
        while self._peek().isdigit() or self._peek() == ".":
            self._advance()
        raw_text = self.raw[raw_start:self.pos]
        span = self._span_from(start)
        self._error(E_BAD_NUMBER, f"数值字面量「{raw_text}」非法：{why}。", span, hints or [])

    def _latin_word(self) -> None:
        start = self._here()
        raw_start = self.pos
        buf = []
        while True:
            ch = self._peek()
            if ch.isascii() and (ch.isalnum() or ch == "_"):
                buf.append(ch)
                self._advance()
            else:
                break
        word = "".join(buf)

        # 拉丁段以 `_` 结尾且紧跟汉字 ⇒ 混合标识符，汉字段整体并入。
        #
        # **必须循环**：混合标识符可以有多段（`A1_账_余额`）。
        # 早期只吸收**一轮**（`_latin_word` 里吸一次汉字段 + 一次拉丁段），
        # 于是 `A1_账_余额` 被切成 `A1_账_` + `余额` —— 第二段汉字段
        # 又走了 `_han_block`，报出「`余额` 两侧没有运算符」这种
        # 与真因毫不相干的错误。判据统一为：**只要 `_` 后面还跟着
        # 字母/数字/汉字，就并进同一个标识符**，直到遇到真正的分隔符。
        while word.endswith("_") and (is_han(self._peek())
                                      or self._peek().isascii()
                                      and self._peek().isalnum()):
            while is_han(self._peek()):
                word += self._peek()
                self._advance()
            while True:
                ch = self._peek()
                if ch.isascii() and (ch.isalnum() or ch == "_"):
                    word += ch
                    self._advance()
                else:
                    break

        raw_text = self.raw[raw_start:self.pos]
        span = self._span_from(start)
        if word in kw.LATIN:
            self._push(Token(Kind.KEYWORD, kw.LATIN[word], raw_text, span))
        else:
            self._push(Token(Kind.IDENT, word, raw_text, span))

    def _han_block(self) -> None:
        """读取最大连续汉字段，执行三通道裁决切分。"""
        start = self._here()
        raw_start = self.pos
        buf = []
        while True:
            ch = self.raw[self.pos] if self.pos < len(self.raw) else "\0"
            if is_han(ch) or (buf and ch in CN_UNITS_BIG):
                buf.append(self.norm[self.pos])
                self._advance()
            else:
                break
        block = "".join(buf)
        # **raw_text 必须在吸收完拉丁尾段之后再取**：`编码Base64`、`导出CSV`
        # 这类「汉字单字块 + 拉丁后缀」的记号，其 `text`（源码原文）若在此处
        # 提前截取，就会只含汉字部分而与 `value` 不一致 —— 解析器取名字原文
        # （`_name_text`）时便会得到 `编码`/`导出`，成员访问全部查不到。
        span = self._span_from(start)

        # ---- 混合标识符：以 `_` 为界 ------------------------------------
        # 出现下划线即声明「这是一个整体标识符」，其后各段不再参与关键字切分。
        # 两种来源：① 下划线在字块内（`定义_私有的`）；② 紧跟在字块之后（`甲_乙`）。
        pre, sep_in, post = block.partition("_")
        sep_from_stream = (
            not sep_in
            and self._peek() == "_"
            and not self._is_keyword_block(block)
        )

        pieces = self._split_han(pre, span) if pre else []

        if sep_in or sep_from_stream:
            absorbed = ("_" + post) if sep_in else ""
            while True:
                ch = self.raw[self.pos] if self.pos < len(self.raw) else "\0"
                if is_han(ch) or (ch.isascii() and (ch.isalnum() or ch == "_")):
                    absorbed += self.norm[self.pos]
                    self._advance()
                else:
                    break
            if len(pieces) == 1 and pieces[0][0] is Kind.IDENT:
                pieces = [(Kind.IDENT, pieces[0][1] + absorbed)]
            else:
                # 前半段被切成了关键字 ⇒ 下划线已表明意图，整体按标识符处理
                pieces = [(Kind.IDENT, pre + absorbed)]

        # 无下划线时，允许末枚 IDENT 续接拉丁字母数字（如 `结果x`）
        if not sep_in and not sep_from_stream and pieces \
                and pieces[-1][0] is Kind.IDENT:
            tail2: list[str] = []
            while True:
                ch = self._peek()
                if ch.isascii() and (ch.isalnum() or ch == "_"):
                    tail2.append(ch)
                    self._advance()
                else:
                    break
            # **尾段里出现 `_` ⇒ 这是一个混合标识符**，其后的汉字必须
            # 继续整体并入（不再切关键字）。
            #
            # 实测真缺陷：`账簿006_借方合计` 曾被切成 `账簿006_` + `借方合计`
            # 两枚记号。原因是本循环只吸收「ascii 字母数字与下划线」，
            # 遇到汉字即停 —— 而下划线出现在**数字之后**，使它没能走进
            # 上面那条 `sep_from_stream`（那条只认「汉字块紧跟 `_`」）。
            # 后果不是报「标识符未定义」，而是报
            # 「`贷方合计` 两侧没有运算符」—— 把用户引向完全不相关的地方。
            # 真实工程里 `服务001_校验`、`账簿006_借方合计` 这类命名极常见，
            # 故危害面很大。此处的判据与 `_latin_word`（拉丁段以 `_` 结尾
            # 则并入汉字段）**同口径**：只要 `_` 出现过，就是整体标识符。
            if tail2 and "_" in "".join(tail2):
                while True:
                    ch = self.raw[self.pos] if self.pos < len(self.raw) else "\0"
                    if is_han(ch) or (ch.isascii()
                                      and (ch.isalnum() or ch == "_")):
                        tail2.append(self.norm[self.pos])
                        self._advance()
                    else:
                        break
            if tail2:
                pieces[-1] = (Kind.IDENT, pieces[-1][1] + "".join(tail2))

        # 此刻已吸收全部尾段，源码原文才完整（见上方关于 `编码Base64` 的说明）。
        raw_text = self.raw[raw_start:self.pos]

        # 位置信息：所有切出的片段共享该块的行列起点（列号在 _advance 中已按显示列累计）
        for idx, (kind, value) in enumerate(pieces):
            piece_span = span if idx == 0 else Span(
                start[0], start[1], self.line, self.col)
            self._push(Token(kind, value, raw_text, piece_span))

    # ---------------------------------------------------- 汉字三通道切分

    def _split_han(self, H: str, span: Span) -> list[tuple[Kind, str]]:
        """实现 SPEC §2.4 的确定性切分。返回 [(Kind, value), ...]。"""
        if not H:
            return []

        # 第 -1 步：保留名整体保护（**优先于一切关键字切分**）。
        # `类型名` 必须以整个标识符出现，否则会被 `类型`（A 类引导词）
        # 前缀匹配切成 `类型` + `名`，标准库函数直接失效。
        if H in PROTECTED_NAMES:
            return [(Kind.IDENT, H)]

        # 第 -0.5 步：内置函数的**口语变体**（`说一声` → `说`）。
        # 中文口语的「动词 + 助词」黏连在此**列举式**收编，不外溢成通用规则，
        # 避免与普通标识符冲突。
        if H in kw.COLLOQUIAL_BUILTINS:
            return [(Kind.IDENT, kw.COLLOQUIAL_BUILTINS[H])]

        # 第 0 步：整体命中关键字
        if H in kw.LEADING:
            return [(Kind.KEYWORD, kw.LEADING[H])]
        if H in kw.STANDALONE:
            return [(Kind.KEYWORD, kw.STANDALONE[H])]
        if H in kw.TRAILING:
            return [(Kind.KEYWORD, kw.TRAILING[H])]
        if H in kw.WORD_OPERATORS:
            return [(Kind.OP, kw.WORD_OPERATORS[H])]

        # 第 0.2 步：保留名长前缀——`类型名X` 这类（保留名 + 后缀）
        # 仍按「保留名为首的标识符」处理，不做关键字切分。
        for reserved in PROTECTED_NAMES:
            if H.startswith(reserved) and len(H) > len(reserved):
                return [(Kind.IDENT, H)]

        # 第 0.5 步：中文数词优先
        num_res = self._try_cn_number(H)
        if num_res is not None:
            return num_res

        # 第 1 步：A 类前缀最长匹配
        prefix = self._longest_prefix(H, kw.LEADING)
        if prefix is not None:
            rest = H[len(prefix):]
            # **单字 A 类词的约束**（实测得出，见 keywords.A_SINGLE）：
            # `当前目录`、`设备清单`、`设置参数`、`请求处理`、`试验记录`、
            # `请假`… 这些高频词若被 `当`/`设`/`请`/`试` 前缀切开会直接废掉。
            #
            # 口径：单字词仅当「剩余部分是**多字**关键字」时才融合切分。
            # 多字关键字（`结束`/`真值`/`不小于`）作剩余时，融合写法是
            # 合理的（如 `试结束` 之类不会出现，但 `若假值` 有可能）；
            # 而剩余是**单字**关键字（`真`/`假`/`空`/`且`）时，
            # 与前面的单字词组合几乎必然是普通词（`请假`、`让步`），不切。
            if prefix in kw.A_SINGLE:
                rest_pieces = self._split_han(rest, span) if rest else []
                ok = (bool(rest_pieces) and len(rest) >= 2
                      and all(k in (Kind.KEYWORD, Kind.OP)
                              for k, _ in rest_pieces))
                if not ok:
                    return self._split_after_a_single_prefix(H, span)
            out = [(Kind.KEYWORD, kw.LEADING[prefix])]
            if rest:
                out.extend(self._split_han(rest, span))
            return out

        # A 类中缀（若 A 类词出现在块中，如 `名字叫张三` 的 `叫` —— 属 C 类，见下）

        # 第 2 步：C 类后缀最长匹配（含中文运算符词）
        suffix_res = self._try_c_suffix(H)
        if suffix_res is not None:
            return suffix_res

        # 第 3 步：C 类中缀（仅 C_MULTI）
        infix = self._longest_infix(H, kw.C_MULTI)
        if infix is not None:
            idx, word = infix
            left = self._split_han(H[:idx], span)
            right = self._split_han(H[idx + len(word):], span)
            return left + [(Kind.OP if word in kw.WORD_OPERATORS
                            else Kind.KEYWORD,
                            kw.WORD_OPERATORS.get(word, kw.TRAILING.get(word, word)))] + right

        # 第 4 步：软助词
        if kw.is_filler_exact(H) or H in kw.FILLER_SOFT:
            return []
        stripped, did = kw.strip_filler_suffix(H)
        if did and stripped:
            return self._split_han(stripped, span)

        # 第 5 步：整体标识符
        if H[0].isdigit():
            self._error(E_IDENT_STARTS_DIGIT,
                        f"标识符「{H}」不能以数字开头。",
                        span, ["以汉字或下划线开头"])
            return []
        return [(Kind.IDENT, H)]

    def _split_after_a_single_prefix(self, H: str,
                                     span: Span) -> list[tuple[Kind, str]]:
        """单字 A 类前缀不能融合时的兜底切分。

        走与正常流程相同的后续通道，但**跳过第 1 步**，避免递归回
        同一个前缀判断而无限循环。
        """
        # C 类后缀
        suffix_res = self._try_c_suffix(H)
        if suffix_res is not None:
            return suffix_res
        # C 类中缀（仅 C_MULTI）
        infix = self._longest_infix(H, kw.C_MULTI)
        if infix is not None:
            idx, word = infix
            left = self._split_han(H[:idx], span)
            right = self._split_han(H[idx + len(word):], span)
            return left + [(Kind.OP if word in kw.WORD_OPERATORS
                            else Kind.KEYWORD,
                            kw.WORD_OPERATORS.get(
                                word, kw.TRAILING.get(word, word)))] + right
        # 软助词
        if kw.is_filler_exact(H) or H in kw.FILLER_SOFT:
            return []
        stripped, did = kw.strip_filler_suffix(H)
        if did and stripped:
            return self._split_han(stripped, span)
        # 整体标识符 —— 单字词开头的标识符走这里
        return [(Kind.IDENT, H)]

    def _try_cn_number(self, H: str) -> list[tuple[Kind, str]] | None:
        """若 H 是（或数词 + 单位词构成的）中文数词，切出数字记号。

        **边界条件（关键）**：只有当「整个字块都是数词符」或者
        「数词前缀之后的剩余部分是纯关键字/填充词（不含标识符）」时，
        才把它当数字。否则整块按标识符处理 —— 这样 `二十一点半`
        不会被裂成 `21` + `点半`，而 `三次` 仍能正确切为 `3` + `次`。
        """
        if H[0] not in CN_NUM_CHARS:
            return None

        # 情形 A：整块都是数词字符（含 `三百零五`、`十二万`）
        if all(ch in CN_NUM_CHARS for ch in H):
            value = parse_cn_number(H)
            if value is not None:
                return [(Kind.NUMBER, str(value))]
            self._error(E_BAD_NUMBER,
                        f"中文数词「{H}」的写法无法解析。",
                        Span(0, 0, 0, 0),
                        ["改用阿拉伯数字，或按 `一百二十` 的规范写法"])
            return [(Kind.IDENT, H)]

        # 情形 B：数词前缀 + 剩余部分**全为关键字/填充词**（如 `三次`）
        n = 0
        while n < len(H) and H[n] in CN_NUM_CHARS:
            n += 1
        if n == 0:
            return None
        for end in range(n, 0, -1):
            value = parse_cn_number(H[:end])
            if value is None:
                continue
            rest = H[end:]
            rest_pieces = self._split_han(rest, Span(0, 0, 0, 0))
            if rest_pieces and all(k is Kind.KEYWORD for k, _ in rest_pieces):
                return [(Kind.NUMBER, str(value))] + rest_pieces
            if not rest_pieces:
                return [(Kind.NUMBER, str(value))]
        # 剩余部分含标识符 ⇒ 整块是标识符
        return [(Kind.IDENT, H)]

    def _try_c_suffix(self, H: str) -> list[tuple[Kind, str]] | None:
        """C 类后缀最长匹配（含中文运算符词）。"""
        candidates: list[str] = []
        for w in kw.TRAILING:
            if H.endswith(w) and len(H) > len(w):
                candidates.append(w)
        for w in kw.WORD_OPERATORS:
            if H.endswith(w) and len(H) > len(w):
                candidates.append(w)
        if not candidates:
            return None
        best = max(candidates, key=len)
        left = H[: len(H) - len(best)]
        if best in kw.C_SINGLE and len(left) < 2:
            return None
        left_pieces = self._split_han(left, Span(0, 0, 0, 0))
        if best in kw.WORD_OPERATORS:
            tail = (Kind.OP, kw.WORD_OPERATORS[best])
        else:
            tail = (Kind.KEYWORD, kw.TRAILING[best])
        return left_pieces + [tail]

    def _is_keyword_block(self, H: str) -> bool:
        """判断字块是否**整体**命中关键字（整体命中时不视作混合标识符）。"""
        return (H in kw.LEADING or H in kw.STANDALONE
                or H in kw.TRAILING or H in kw.WORD_OPERATORS)

    def _longest_prefix(self, H: str, table: dict[str, str]) -> str | None:
        best = None
        for w in table:
            if H.startswith(w) and len(H) > len(w):
                if best is None or len(w) > len(best):
                    best = w
        return best

    def _longest_infix(self, H: str, table: frozenset[str]) -> tuple[int, str] | None:
        best = None
        for w in table:
            idx = H.find(w)
            if idx > 0 and idx + len(w) < len(H):
                if best is None or len(w) > len(best[1]):
                    best = (idx, w)
        return best

    # ------------------------------------------------------------ 符号

    def _symbol(self) -> bool:
        start = self._here()
        for op in SYMBOL_OPS:
            if self.norm.startswith(op, self.pos):
                self._advance(len(op))
                span = self._span_from(start)
                if op in ("(", "[", "{"):
                    self.paren_depth += 1
                elif op in (")", "]", "}"):
                    self.paren_depth = max(0, self.paren_depth - 1)
                self._push(Token(Kind.OP, op, op, span))
                return True
        return False


def tokenize(source: str, filename: str = "<源码>") -> list[Token]:
    return Lexer(source, filename).tokenize()


def describe_tokens(tokens: list[Token]) -> str:
    """供 CLI `词法` 子命令使用的人类可读记号表。"""
    rows = [f"{'种类':<8}{'值':<16}{'原文':<16}{'行:列'}", "-" * 52]
    for t in tokens:
        if t.kind is Kind.EOF:
            continue
        rows.append(
            f"{t.kind.name:<8}{t.value:<16}{t.text:<16}"
            f"{t.span.line}:{t.span.col}"
        )
    return "\n".join(rows)