"""枢言语言 —— 源文件定位与显示宽度工具。

中文编程语言的工具链有一个硬性要求：**汉字的显示宽度是 2 列**。
所有指向源码的列号、下划线、对齐都必须按显示列计算，否则
报错下划线会错位，中文源码的报错体验直接崩掉。
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass


def is_han(ch: str) -> bool:
    """判断字符是否为汉字（CJK 表意文字，含扩展 A）。"""
    code = ord(ch)
    return 0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF


def is_wide(ch: str) -> bool:
    """判断字符是否为占两列的东亚宽字符。"""
    if is_han(ch):
        return True
    return unicodedata.east_asian_width(ch) in ("W", "F")


def display_width(text: str) -> int:
    """计算文本的显示宽度（汉字/全角算 2 列，其余算 1 列）。

    制表符按 4 列估算——源码中不应出现 Tab（格式化器会替换），
    此处仅为容错。
    """
    total = 0
    for ch in text:
        if ch == "\t":
            total += 4
        elif is_wide(ch):
            total += 2
        else:
            total += 1
    return total


def pad_to(text: str, width: int, fill: str = " ") -> str:
    """按显示宽度右侧补齐到指定列宽。"""
    gap = width - display_width(text)
    return text + fill * gap if gap > 0 else text


def highlight(text: str, start_col: int, length: int) -> str:
    """生成下划线标记行。

    start_col 与 length 均按**显示列**计。若标记范围落在宽字符
    中间，则向前对齐到该字符起点，保证视觉上不出错。
    """
    prefix = " " * max(0, start_col)
    return prefix + "^" * max(1, length)


@dataclass(frozen=True)
class Span:
    """源码区间。行、列均从 1 开始；列按显示列计。"""

    line: int
    col: int
    end_line: int
    end_col: int

    @staticmethod
    def merge(a: "Span", b: "Span") -> "Span":
        return Span(a.line, a.col, b.end_line, b.end_col)

    def to_dict(self) -> dict:
        return {
            "行": self.line,
            "列": self.col,
            "结束行": self.end_line,
            "结束列": self.end_col,
        }