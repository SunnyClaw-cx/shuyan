"""枢言语言 —— 运行时错误与异常模型（SPEC §3.7、§6.4）。

关键区分：
- **枢言异常**（`ShuyanThrow`）：程序 `抛出` 的异常，以及标准库失败抛出的异常，
  可被 `捕获` 接住，异常值是**任意枢言值**（标准库统一用映射）。
- **宿主错误**：实现层自身的 bug，不接住，直接冒泡（便于定位实现缺陷）。

把这两类分开很重要——否则「用户程序抛的异常」与「编译器自己的崩溃」会混在
一个 `except` 里，调试时分不清是谁的问题。
"""

from __future__ import annotations

from dataclasses import dataclass

from .source import Span
from .values import ShuyanMap


class ShuyanThrow(Exception):
    """枢言层面的异常。value 可以是任意枢言值。"""

    def __init__(self, value, span: Span | None = None,
                 trace: list[str] | None = None):
        self.value = value
        self.span = span
        self.trace: list[str] = trace or []
        super().__init__(self._describe())

    def _describe(self) -> str:
        if isinstance(self.value, ShuyanMap):
            info = self.value.data.get("信息")
            if info is not None:
                kind = self.value.data.get("类型", "异常")
                return f"{kind}：{info}"
        return str(self.value)


@dataclass
class ExitSignal(Exception):
    """程序主动退出（`退出` 内置函数）。"""

    code: int = 0


@dataclass
class ReturnSignal(Exception):
    """函数返回信号（控制流用异常传递，避免层层判空）。"""

    value: object = None


@dataclass
class BreakSignal(Exception):
    pass


@dataclass
class ContinueSignal(Exception):
    pass


def make_error(kind: str, message: str, **extra) -> ShuyanMap:
    """构造标准库错误值（映射形式，见 SPEC §6.4）。"""
    data = {"类型": kind, "信息": message}
    data.update(extra)
    return ShuyanMap(data)


def error(kind: str, message: str, *_span, **extra) -> ShuyanThrow:
    """构造枢言运行时异常。

    第一个位置参数之后、关键字参数之前，可选传入一个 `Span` 作为定位
    —— 便于运行期错误报出**准确的行列**，而不是笼统的「第 0 行」。
    """
    span = _span[0] if _span else None
    if span is not None and not isinstance(span, Span):
        span = None
    return ShuyanThrow(make_error(kind, message, **extra), span)


def arg_error(func_name: str, expected: str) -> ShuyanThrow:
    return error("参数错误",
                 f"函数「{func_name}」的参数不正确，应该是 {expected}。",
                 函数=func_name)


def type_error(func_name: str, expected: str, got: str) -> ShuyanThrow:
    return error("类型错误",
                 f"函数「{func_name}」需要「{expected}」类型的参数，"
                 f"实际收到「{got}」。",
                 函数=func_name, 期望=expected, 实际=got)