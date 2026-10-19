"""枢言语言 —— 源码格式化工具（SPEC §7）。

设计原则：格式化必须**无损**——绝不改变语义，绝不丢注释。

实现路线：基于**行**而非基于 AST 重排。理由：AST 重排会丢失注释与
原作者的段落意图（空行、分节注释），而中文源码中注释密度通常很高。
故本实现的做法是：按块结构重算缩进、规范化空白与符号间距，
**保留注释原位置**。

规范要点（SPEC §7）：
- 缩进 4 空格，禁用 Tab；
- 二元运算符两侧一空格；
- 汉字与拉丁标识符之间保留一空格；
- 最大行宽 100 显示列（汉字算 2 列）——超宽时给出提示而非强拆。
"""

from __future__ import annotations

import re

from .lexer import FULLWIDTH_MAP, Lexer, Kind
from .source import display_width

# 使缩进增深的行首关键字（语义名）
OPENERS = {
    "if", "elif", "else", "while", "for", "loop", "fn", "try", "catch",
    "finally",
}
# 缩进回退
CLOSERS = {"end"}

INDENT = "    "
MAX_WIDTH = 100


class Formatter:
    def __init__(self, source: str, filename: str = "<源码>"):
        self.source = source
        self.filename = filename
        self.issues: list[str] = []

    def format(self) -> str:
        lines = self.source.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        out: list[str] = []
        depth = 0
        for raw in lines:
            stripped = raw.strip()
            if not stripped:
                out.append("")
                continue

            # 注释行：随当前缩进即可，内容原样保留
            code, comment = self._split_comment(stripped)

            if not code:
                out.append(INDENT * depth + stripped)
                continue

            # `结束` / `否则` / `捕获` / `最终` 先回退一级
            lead = self._leading_keyword(code)
            if lead in CLOSERS or lead in ("else", "elif", "catch", "finally"):
                depth = max(0, depth - 1)

            normalized = self._normalize_code(code)
            line = INDENT * depth + normalized
            if comment:
                line += "  " + comment

            if display_width(line) > MAX_WIDTH:
                self.issues.append(
                    f"第 {len(out) + 1} 行超过 {MAX_WIDTH} 显示列"
                    f"（实为 {display_width(line)}），建议拆成多行")

            out.append(line)

            # 本行使后续增深
            if self._opens_block(normalized):
                depth += 1

        text = "\n".join(out)
        if not text.endswith("\n"):
            text += "\n"
        return text

    # ------------------------------------------------------------ 辅助

    def _split_comment(self, line: str) -> tuple[str, str]:
        """切出（代码, 注释）。字符串内部的 `#` 不算注释起始。"""
        in_str = None
        i = 0
        while i < len(line):
            ch = line[i]
            if in_str:
                if ch == "\\":
                    i += 2
                    continue
                if ch == in_str:
                    in_str = None
                i += 1
                continue
            if ch in ('"', "'", "`"):
                in_str = ch
                i += 1
                continue
            if ch == "#":
                return line[:i].rstrip(), line[i:].rstrip()
            if ch == "/" and i + 1 < len(line) and line[i + 1] == "/":
                return line[:i].rstrip(), "#" + line[i + 2:].rstrip()
            i += 1
        return line.rstrip(), ""

    def _leading_keyword(self, code: str) -> str | None:
        try:
            tokens = Lexer(code).tokenize()
        except Exception:
            return None
        for t in tokens:
            if t.kind is Kind.KEYWORD:
                return t.value
            if t.kind in (Kind.IDENT, Kind.NUMBER, Kind.STRING, Kind.OP):
                return None
        return None

    def _opens_block(self, code: str) -> bool:
        """判断本行是否开启一个以 `结束` 收尾的块。

        行内已经写出 `结束` 时不增深（如 `如果 甲 则 输出(1) 结束`）。
        """
        try:
            tokens = [t for t in Lexer(code).tokenize() if t.kind is not Kind.EOF]
        except Exception:
            return False
        if not tokens:
            return False
        # 行尾已是 end 或右花括号 ⇒ 不增深
        if tokens[-1].value in ("end", "}", ";"):
            return False
        first = tokens[0]
        if first.kind is not Kind.KEYWORD:
            return False
        if first.value not in OPENERS:
            return False
        # 单行完整块（含 end）→ 不增深
        if any(t.value == "end" for t in tokens):
            return False
        return True

    def _normalize_code(self, code: str) -> str:
        """规范化空白与符号间距。"""
        code = code.replace("\t", INDENT)
        # 折叠多余空格
        code = re.sub(r"[ \u3000]{2,}", " ", code)

        # 全角 → 半角（除字符串内容）
        code = self._halfwidth_outside_strings(code)

        # 运算符两侧留一空格
        for op in ("==", "!=", "<=", ">=", "+=", "-=", "*=", "/=", "%=",
                   "=", "<", ">", "+", "-", "*", "/", "%", "^", ".."):
            code = self._space_around(code, op)

        # 逗号后一空格、冒号后一空格
        code = re.sub(r",\s*", ", ", code)
        code = re.sub(r":\s*", ": ", code)
        code = re.sub(r"\s+\)", ")", code)
        code = re.sub(r"\(\s+", "(", code)
        code = re.sub(r"\[\s+", "[", code)
        code = re.sub(r"\s+\]", "]", code)

        # 折叠
        code = re.sub(r" {2,}", " ", code).strip()
        code = re.sub(r", $", ",", code)
        return code

    def _halfwidth_outside_strings(self, code: str) -> str:
        out = []
        in_str = None
        for ch in code:
            if in_str:
                out.append(ch)
                if ch == in_str:
                    in_str = None
                continue
            if ch in ('"', "'", "`"):
                in_str = ch
                out.append(ch)
                continue
            out.append(FULLWIDTH_MAP.get(ch, ch))
        return "".join(out)

    def _space_around(self, code: str, op: str) -> str:
        """给运算符两侧补空格（不动字符串内部，不破坏 `+=` 等多字符运算符）。"""
        out = []
        i = 0
        n = len(code)
        in_str = None
        while i < n:
            ch = code[i]
            if in_str:
                out.append(ch)
                if ch == "\\" and i + 1 < n:
                    out.append(code[i + 1])
                    i += 2
                    continue
                if ch == in_str:
                    in_str = None
                i += 1
                continue
            if ch in ('"', "'", "`"):
                in_str = ch
                out.append(ch)
                i += 1
                continue
            if code.startswith(op, i):
                # 多字符运算符需完整匹配：`+` 不应命中 `+=` 的内部
                if self._is_complete_operator(code, i, op):
                    # 一元负号 / 正号不加前空格
                    prev = out[-1] if out else ""
                    is_unary = (op in ("-", "+")
                                and (not prev or prev in "(,=+-*/%^<>!:"))
                    if is_unary:
                        out.append(op)
                    else:
                        while out and out[-1] == " ":
                            out.pop()
                        if out:
                            out.append(" ")
                        out.append(op)
                        out.append(" ")
                        # 跳过后续空格
                        j = i + len(op)
                        while j < n and code[j] == " ":
                            j += 1
                        i = j
                        continue
                    i += len(op)
                    continue
            out.append(ch)
            i += 1
        return "".join(out)

    def _is_complete_operator(self, code: str, i: int, op: str) -> bool:
        """确认此处不是更长运算符的一部分（`+` 不得命中 `+=`，`=` 不得命中 `==`）。

        **必须双向检查**。曾经的缺陷：只检查「后面还有没有字符」，
        于是 `==` 的首个 `=` 因后面是 `=` 被正确跳过，**第二个 `=`**
        却因后面是空格而被当成独立运算符 ⇒ 被补上空格变成 `= =`，
        程序直接语法错误（`<=` → `< =`、`+=` → `+ =` 同理）。

        格式化器**绝不允许改变程序语义** —— 把能跑的程序格式化坏，
        比不格式化严重得多。故此处对多字符运算符双向设防。
        """
        longer = ("==", "!=", "<=", ">=", "+=", "-=", "*=", "/=", "%=", "..")
        for other in longer:
            if other == op:
                continue
            if code.startswith(other, i):
                return False
        # `=` 不得命中 `==`、`<=` 等
        if op == "=" and i + 1 < len(code) and code[i + 1] == "=":
            return False
        if op in ("<", ">") and i + 1 < len(code) and code[i + 1] == "=":
            return False
        if op == "." and (i + 1 < len(code) and code[i + 1] == "."):
            return False
        # **向后检查**：本字符可能是多字符运算符的**后半部分**。
        # `==` / `<=` / `+=` 等已在扫描到首字符时作为整体处理，若此处
        # 它们的尾字符会被误判为独立运算符而被拆开。
        if op == "=" and i > 0 and code[i - 1] in "=!<>+-*/%":
            return False
        if op == ">" and i > 0 and code[i - 1] == "-":
            return False
        return True


def format_source(source: str, filename: str = "<源码>") -> tuple[str, list[str]]:
    f = Formatter(source, filename)
    return f.format(), f.issues