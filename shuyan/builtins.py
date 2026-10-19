"""枢言语言 —— 标准库（SPEC §6）。

中文语言的三个硬性要求，本模块逐一落实：
1. **长度/索引/截取一律按字符（码点）计**，不按字节 —— `长度("你好")` 必须等于 2；
2. **类型名是中文**，报错文案才能读通；
3. **控制台强制 UTF-8**，否则 Windows 下中文输出直接乱码。

模块划分与 SPEC §6.2 一致。核心函数无需引入即可用；模块函数须 `引入` 后经
`模块名.函数名` 调用（如 `文本.填充(...)`）。这也顺带说明：**内置名不占用关键字**，
凡与关键字同形者（如 `重复`）一律改名（此处用 `复制`）。
"""

from __future__ import annotations

import base64
import hashlib
import math
import os
import random as _random
import re
import subprocess
import sys
import time as _time
from datetime import datetime

from .errors import ShuyanThrow, arg_error, error, type_error
from .lexer import parse_cn_number
from .values import (
    NIL, ShuyanMap, display, is_truthy, to_text, type_name,
)

# ------------------------------------------------------------ 参数校验工具


def _want_text(v, fname: str, pos: int = 1) -> str:
    if not isinstance(v, str):
        raise type_error(fname, "文本", type_name(v))
    return v


def _want_int(v, fname: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise type_error(fname, "整数", type_name(v))
    return v


def _want_number(v, fname: str):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise type_error(fname, "数字", type_name(v))
    return v


def _want_list(v, fname: str) -> list:
    if not isinstance(v, list):
        raise type_error(fname, "列表", type_name(v))
    return v


def _want_map(v, fname: str) -> ShuyanMap:
    if not isinstance(v, ShuyanMap):
        raise type_error(fname, "映射", type_name(v))
    return v


def _want_iterable(v, fname: str) -> list:
    """可遍历对象：列表 → 元素；文本 → 逐字；映射 → 键；区间 → 列表。"""
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        return list(v)
    if isinstance(v, ShuyanMap):
        return list(v.data.keys())
    raise type_error(fname, "列表或文本", type_name(v))


def _norm_index(idx: int, n: int) -> int:
    """负索引归一：-1 表示最后一个。"""
    return idx + n if idx < 0 else idx


class StdLib:
    """标准库集合。由解释器实例化并注入运行上下文。"""

    def __init__(self, argv: list[str] | None = None,
                 base_dir: str | None = None,
                 stdout=None):
        self.argv = argv or []
        self.base_dir = base_dir or os.getcwd()
        self.stdout = stdout if stdout is not None else sys.stdout
        self.written: list[str] = []      # 捕获输出（供测试与工具使用）
        self.capture = False
        self.core: dict[str, object] = {}
        self.modules: dict[str, dict[str, object]] = {}
        self._build_core()
        self._build_modules()

    # -------------------------------------------------------- 输出通道

    def emit(self, text: str) -> None:
        self.written.append(text)
        if self.capture:
            return
        try:
            self.stdout.write(text + "\n")
        except UnicodeEncodeError:
            # Windows 老控制台：降级为带替换字符的输出，而不是崩溃
            enc = getattr(self.stdout, "encoding", None) or "utf-8"
            self.stdout.write((text + "\n").encode(enc, "replace").decode(enc))

    # ==================================================== 核心函数

    def _build_core(self) -> None:
        c = self.core

        def reg(zh: str, fn, *aliases: str) -> None:
            for name in (zh, *aliases):
                c[name] = fn

        # --- 输入输出 ---
        def fn_print(*args):
            self.emit(" ".join(display(a) for a in args))
            return NIL
        reg("输出", fn_print, "打印", "说", "print")
        reg("不换行输出", lambda *a: self.emit_nobreak(
            " ".join(display(x) for x in a)))

        def fn_input(prompt=NIL):
            if prompt is not NIL:
                self.stdout.write(display(prompt))
                self.stdout.flush()
            line = sys.stdin.readline()
            return line.rstrip("\n") if line else ""
        reg("输入", fn_input, "input")

        # --- 基本 ---
        reg("长度", lambda v: len(_want_len_target(v)))
        reg("类型名", lambda v: type_name(v))
        reg("是否空", lambda v: not is_truthy(v))
        reg("转文本", lambda v: display(v))
        reg("转整数", self._to_int)
        reg("转小数", self._to_float)
        reg("转布尔", lambda v: is_truthy(v))
        reg("断言失败信息", lambda v: display(v))

        # --- 数学 ---
        reg("取整", lambda v: int(_want_number(v, "取整")))
        reg("四舍五入", lambda v, n=0: round(_want_number(v, "四舍五入"),
                                           _want_int(n, "四舍五入")))
        reg("取小", lambda a, b: min(_want_number(a, "取小"),
                                    _want_number(b, "取小")))
        reg("最大", self._max)
        reg("最小", self._min)
        reg("绝对值", lambda v: abs(_want_number(v, "绝对值")))
        reg("次方", lambda a, b: self._power(a, b), "幂运算")
        reg("求和", lambda lst: sum(_want_number(x, "求和")
                                   for x in _want_list(lst, "求和")))
        reg("平均值", self._avg)
        reg("范围", self._range)

        # --- 列表 / 序列 ---
        reg("排序", self._sort)
        reg("反转", lambda lst: list(reversed(_want_list(lst, "反转"))))
        reg("连接", self._join)
        reg("分割", self._split)
        reg("入列", self._append, "追加元素")
        reg("去重", lambda lst: list(dict.fromkeys(_want_list(lst, "去重"))))
        reg("切片", self._slice)
        reg("枚举", lambda lst: [[i, v] for i, v in
                                enumerate(_want_iterable(lst, "枚举"))])

        # --- 文本 ---
        reg("截取", self._substr)
        reg("查找", self._find)
        reg("替换", self._replace_text)
        reg("去空白", self._strip)
        reg("开头是", lambda t, p: _want_text(t, "开头是").startswith(
            _want_text(p, "开头是")))
        reg("结尾是", lambda t, p: _want_text(t, "结尾是").endswith(
            _want_text(p, "结尾是")))
        reg("大写", lambda t: _want_text(t, "大写").upper())
        reg("小写", lambda t: _want_text(t, "小写").lower())
        reg("包含", self._contains)
        reg("复制", self._repeat_text)

        # --- 映射 ---
        reg("取键", self._keys)
        reg("取值", self._values)
        reg("有键", lambda m, k: k in _want_map(m, "有键").data)

    def emit_nobreak(self, text: str):
        self.emit_no_newline(text)
        return NIL

    def emit_no_newline(self, text: str) -> None:
        self.written.append(text)
        if self.capture:
            return
        self.stdout.write(text)
        self.stdout.flush()

    # -------------------------------------------------------- 核心实现

    def _to_int(self, v):
        if isinstance(v, bool):
            return 1 if v else 0
        if isinstance(v, int):
            return v
        if isinstance(v, float):
            return int(v)
        if isinstance(v, str):
            s = v.strip()
            try:
                return int(s)
            except ValueError:
                pass
            cn = parse_cn_number(s)
            if cn is not None:
                return int(cn)
            try:
                return int(float(s))
            except ValueError:
                raise error("转换错误",
                            f"文本「{v}」不能转成整数。", 值=v)
        raise type_error("转整数", "数字或文本", type_name(v))

    def _to_float(self, v):
        if isinstance(v, bool):
            return 1.0 if v else 0.0
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            try:
                return float(v.strip())
            except ValueError:
                raise error("转换错误", f"文本「{v}」不能转成小数。", 值=v)
        raise type_error("转小数", "数字或文本", type_name(v))

    def _max(self, *args):
        if len(args) == 1 and isinstance(args[0], list):
            lst = args[0]
            if not lst:
                raise error("参数错误", "最大 的列表为空，无法取最大值。")
            return max(lst)
        if not args:
            raise arg_error("最大", "至少一个数字")
        return max(args)

    def _min(self, *args):
        if len(args) == 1 and isinstance(args[0], list):
            lst = args[0]
            if not lst:
                raise error("参数错误", "最小 的列表为空，无法取最小值。")
            return min(lst)
        if not args:
            raise arg_error("最小", "至少一个数字")
        return min(args)

    def _power(self, base, exponent):
        """幂运算（`^` 的函数形式）。"""
        b = _want_number(base, "次方")
        e = _want_number(exponent, "次方")
        try:
            result = b ** e
        except (OverflowError, ZeroDivisionError):
            raise error("运算错误", f"计算 {b} 的 {e} 次方失败。")
        if isinstance(result, complex):
            raise error("运算错误",
                        "负数的小数次幂结果是复数，枢言 v0.1 不支持复数。")
        return result

    def _avg(self, lst):
        items = _want_list(lst, "平均值")
        if not items:
            raise error("参数错误", "平均值 的列表为空。")
        return sum(_want_number(x, "平均值") for x in items) / len(items)

    def _range(self, *args):
        """范围(终点) / 范围(起点, 终点) / 范围(起点, 终点, 步长)。终点不含。"""
        if len(args) == 1:
            lo, hi, st = 0, _want_int(args[0], "范围"), 1
        elif len(args) == 2:
            lo, hi = _want_int(args[0], "范围"), _want_int(args[1], "范围")
            st = 1
        elif len(args) == 3:
            lo = _want_int(args[0], "范围")
            hi = _want_int(args[1], "范围")
            st = _want_int(args[2], "范围")
        else:
            raise arg_error("范围", "1 到 3 个整数")
        if st == 0:
            raise error("参数错误", "范围 的步长不能为 0。")
        return list(range(lo, hi, st))

    def _sort(self, lst, 倒序=False):
        items = _want_list(lst, "排序")
        try:
            return sorted(items, reverse=bool(倒序))
        except TypeError:
            return sorted(items, key=lambda x: (type_name(x), display(x)),
                          reverse=bool(倒序))

    def _join(self, lst, sep="", *_, **__):
        """连接(列表) / 连接(列表, 分隔符) —— 文本列表 → 文本。"""
        items = _want_list(lst, "连接")
        if isinstance(sep, str):
            return sep.join(display(x) for x in items)
        return "".join(display(x) for x in items)

    def _split(self, text, sep=NIL):
        t = _want_text(text, "分割")
        if sep is NIL:
            return t.split()
        return t.split(_want_text(sep, "分割"))

    def _append(self, lst, item):
        target = _want_list(lst, "入列")
        target.append(item)
        return target

    def _slice(self, seq, start=0, stop=NIL):
        """切片(序列, 起) / 切片(序列, 起, 止)。中止不含。"""
        s = _want_int(start, "切片")
        if isinstance(seq, str):
            if stop is NIL:
                return seq[s:]
            return seq[s:_want_int(stop, "切片")]
        items = _want_list(seq, "切片")
        if stop is NIL:
            return items[s:]
        return items[s:_want_int(stop, "切片")]

    def _substr(self, text, start, length=NIL):
        """截取(文本, 起, 长)。按**字符**计。负起表示从末尾。"""
        t = _want_text(text, "截取")
        s = _norm_index(_want_int(start, "截取"), len(t))
        if length is NIL:
            return t[s:]
        n = _want_int(length, "截取")
        if n < 0:
            raise error("参数错误", "截取 的长度不能为负数。")
        return t[s:s + n]

    def _find(self, text, sub):
        t = _want_text(text, "查找")
        s = _want_text(sub, "查找")
        idx = t.find(s)
        return idx if idx >= 0 else -1

    def _replace_text(self, text, old, new):
        return _want_text(text, "替换").replace(
            _want_text(old, "替换"), _want_text(new, "替换"))

    def _strip(self, text, chars=NIL):
        t = _want_text(text, "去空白")
        return t.strip() if chars is NIL else t.strip(display(chars))

    def _contains(self, container, item):
        if isinstance(container, ShuyanMap):
            return item in container.data
        if isinstance(container, str):
            return _want_text(item, "包含") in container
        if isinstance(container, list):
            from .values import equals
            return any(equals(item, x) for x in container)
        raise type_error("包含", "列表 / 映射 / 文本", type_name(container))

    def _repeat_text(self, text, times, sep=""):
        t = _want_text(text, "复制")
        n = _want_int(times, "复制")
        if n < 0:
            raise error("参数错误", "复制 的次数不能为负数。")
        if isinstance(sep, str) and sep:
            return sep.join([t] * n)
        return t * n

    def _keys(self, m):
        return list(_want_map(m, "取键").data.keys())

    def _values(self, m):
        return list(_want_map(m, "取值").data.values())

    # ==================================================== 标准模块

    def _build_modules(self) -> None:
        m = self.modules

        # ---------------- 文本 ----------------
        m["文本"] = {
            "填充": self._pad,
            "重复": self._repeat_text,       # 经 文本.重复 访问，不占用关键字
            "逐字": lambda t: list(_want_text(t, "逐字")),
            "字符": self._chr,
            "码点": self._ord,
            "格式化": self._format_map,
            "正则匹配": self._regex_find,
            "替换正则": self._regex_sub,
            "对齐": self._pad,
            "显示宽度": self._disp_width,
        }

        # ---------------- 列表 ----------------
        m["列表"] = {
            "去重": lambda lst: list(dict.fromkeys(_want_list(lst, "去重"))),
            "扁平": self._flatten,
            "分组": self._chunk,
            "压缩": lambda a, b: list(zip(_want_iterable(a, "压缩"),
                                         _want_iterable(b, "压缩"))),
            "枚举": lambda lst: [[i, v] for i, v in
                                enumerate(_want_iterable(lst, "枚举"))],
            "切片": self._slice,
            "求和": lambda lst: sum(_want_number(x, "求和")
                                   for x in _want_list(lst, "求和")),
            "排序": self._sort,
        }

        # ---------------- 映射 ----------------
        m["映射"] = {
            "合并": self._merge,
            "键列表": self._keys,
            "值列表": self._values,
            "有键": lambda mp, k: k in _want_map(mp, "有键").data,
            "删除": self._map_delete,
        }

        # ---------------- 数学 ----------------
        m["数学"] = {
            "圆周率": math.pi,
            "自然常数": math.e,
            "正弦": lambda x: math.sin(_want_number(x, "正弦")),
            "余弦": lambda x: math.cos(_want_number(x, "余弦")),
            "正切": lambda x: math.tan(_want_number(x, "正切")),
            "平方根": lambda x: math.sqrt(_want_number(x, "平方根")),
            "幂": lambda a, b: _want_number(a, "幂") ** _want_number(b, "幂"),
            "对数": lambda x, base=math.e: math.log(
                _want_number(x, "对数"), base),
            "取整": lambda x: int(_want_number(x, "取整")),
            "向下取整": lambda x: math.floor(_want_number(x, "向下取整")),
            "向上取整": lambda x: math.ceil(_want_number(x, "向上取整")),
            "绝对值": lambda x: abs(_want_number(x, "绝对值")),
            "最大": self._max,
            "最小": self._min,
        }

        # ---------------- 随机 ----------------
        m["随机"] = {
            "随机数": self._randint,
            "随机小数": lambda: _random.random(),
            "选一个": lambda lst: self._choice(lst),
            "打乱": lambda lst: self._shuffle(lst),
            "设随机种子": self._seed,
        }

        # ---------------- 时间 ----------------
        m["时间"] = {
            "现在": lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "时间戳": lambda: _time.time(),
            "格式化": self._strftime,
            "休眠": self._sleep,
            "年": lambda: datetime.now().year,
            "月": lambda: datetime.now().month,
            "日": lambda: datetime.now().day,
            "时": lambda: datetime.now().hour,
            "分": lambda: datetime.now().minute,
            "秒": lambda: datetime.now().second,
        }

        # ---------------- 文件 ----------------
        m["文件"] = {
            "读文本": self._read_text,
            "写文本": self._write_text,
            "追加": self._append_text,
            "存在": self._exists,
            "删除": self._remove,
            "列表": self._listdir,
            "创建目录": self._mkdir,
            "路径合并": lambda *p: os.path.join(*[_want_text(x, "路径合并")
                                                 for x in p]),
            "目录名": lambda p: os.path.dirname(_want_text(p, "目录名")),
            "文件名": lambda p: os.path.basename(_want_text(p, "文件名")),
        }

        # ---------------- 系统 ----------------
        m["系统"] = {
            "参数": lambda: list(self.argv),
            "环境变量": self._getenv,
            "平台": lambda: {"win32": "Windows", "darwin": "macOS",
                             "linux": "Linux"}.get(sys.platform, sys.platform),
            "退出": self._exit,
            "执行": self._run_command,
            "当前目录": lambda: os.getcwd(),
        }

        # ---------------- 编码 ----------------
        m["编码"] = {
            "编码Base64": lambda t: base64.b64encode(
                _want_text(t, "编码Base64").encode("utf-8")).decode("ascii"),
            "解码Base64": self._b64decode,
            "编码十六进制": lambda t: _want_text(t, "编码十六进制")
                .encode("utf-8").hex(),
            "解码十六进制": self._hexdecode,
        }

        # ---------------- 加密（摘要） ----------------
        m["加密"] = {
            "哈希": self._hash,
            "MD5": lambda t: hashlib.md5(
                _want_text(t, "MD5").encode("utf-8")).hexdigest(),
            "SHA256": lambda t: hashlib.sha256(
                _want_text(t, "SHA256").encode("utf-8")).hexdigest(),
        }

        # ---------------- 正则 ----------------
        m["正则"] = {
            "匹配": self._regex_find,
            "替换": self._regex_sub,
            "是否匹配": lambda t, p: bool(re.search(
                _want_text(p, "是否匹配"), _want_text(t, "是否匹配"))),
            "分组": self._regex_groups,
        }

    # -------------------------------------------------------- 模块实现

    def _disp_width(self, t):
        from .source import display_width
        return display_width(_want_text(t, "显示宽度"))

    def _pad(self, text, width, fill=" ", align="左"):
        """填充(文本, 宽度, 填充符?, 对齐?)。**按显示宽度**计，汉字算 2 列。"""
        from .source import display_width
        t = _want_text(text, "填充")
        w = _want_int(width, "填充")
        f = _want_text(fill, "填充") or " "
        gap = w - display_width(t)
        if gap <= 0:
            return t
        if align in ("右", "right"):
            return f * gap + t
        if align in ("中", "center"):
            left = gap // 2
            return f * left + t + f * (gap - left)
        return t + f * gap

    def _chr(self, code):
        n = _want_int(code, "字符")
        try:
            return chr(n)
        except (ValueError, OverflowError):
            raise error("参数错误", f"码点 {n} 不是合法的 Unicode 码点。")

    def _ord(self, ch):
        t = _want_text(ch, "码点")
        if len(t) != 1:
            raise error("参数错误",
                        f"码点 需要恰好一个字符，实际收到 {len(t)} 个字符。")
        return ord(t)

    def _format_map(self, template, mapping):
        """格式化(模板, 映射)：把模板中的 {键} 替换为映射中的值。"""
        tpl = _want_text(template, "格式化")
        mp = _want_map(mapping, "格式化")
        out = []
        i = 0
        while i < len(tpl):
            ch = tpl[i]
            if ch == "{":
                if i + 1 < len(tpl) and tpl[i + 1] == "{":
                    out.append("{")
                    i += 2
                    continue
                j = tpl.find("}", i)
                if j < 0:
                    raise error("格式错误", "格式化模板中的 { 没有对应的 }。")
                key = tpl[i + 1:j]
                if key not in mp.data:
                    raise error("格式错误", f"格式化模板引用了不存在的键「{key}」。")
                out.append(display(mp.data[key]))
                i = j + 1
                continue
            if ch == "}" and i + 1 < len(tpl) and tpl[i + 1] == "}":
                out.append("}")
                i += 2
                continue
            out.append(ch)
            i += 1
        return "".join(out)

    def _flatten(self, lst):
        out: list = []

        def rec(x):
            for item in x:
                if isinstance(item, list):
                    rec(item)
                else:
                    out.append(item)
        rec(_want_list(lst, "扁平"))
        return out

    def _chunk(self, lst, size):
        items = _want_list(lst, "分组")
        n = _want_int(size, "分组")
        if n <= 0:
            raise error("参数错误", "分组 的每组大小必须大于 0。")
        return [items[i:i + n] for i in range(0, len(items), n)]

    def _merge(self, *maps):
        out = ShuyanMap()
        for mp in maps:
            out.data.update(_want_map(mp, "合并").data)
        return out

    def _map_delete(self, mp, key):
        m = _want_map(mp, "删除")
        m.data.pop(key, None)
        return m

    def _randint(self, lo, hi=NIL):
        if hi is NIL:
            lo, hi = 0, _want_int(lo, "随机数")
        return _random.randint(_want_int(lo, "随机数"), _want_int(hi, "随机数"))

    def _choice(self, lst):
        items = _want_list(lst, "选一个")
        if not items:
            raise error("参数错误", "选一个 的列表为空。")
        return _random.choice(items)

    def _shuffle(self, lst):
        items = list(_want_list(lst, "打乱"))
        _random.shuffle(items)
        return items

    def _seed(self, n):
        _random.seed(_want_int(n, "设随机种子"))
        return NIL

    def _strftime(self, ts, fmt="%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.fromtimestamp(
                float(_want_number(ts, "格式化"))).strftime(
                _want_text(fmt, "格式化"))
        except (ValueError, OSError):
            raise error("参数错误", f"时间戳 {ts} 或格式 {fmt!r} 不合法。")

    def _sleep(self, seconds):
        s = _want_number(seconds, "休眠")
        if s < 0:
            raise error("参数错误", "休眠 的秒数不能为负数。")
        _time.sleep(s)
        return NIL

    # ---- 文件（相对路径基于脚本所在目录，而非进程当前目录） ----

    def _resolve(self, path: str) -> str:
        p = os.path.expanduser(path)
        if not os.path.isabs(p):
            p = os.path.join(self.base_dir, p)
        return os.path.normpath(p)

    def _read_text(self, path, encoding="utf-8"):
        p = self._resolve(_want_text(path, "读文本"))
        try:
            with open(p, "r", encoding=encoding, errors="replace") as f:
                return f.read()
        except FileNotFoundError:
            raise error("文件错误", f"文件不存在：{p}", 路径=p, 类型="文件不存在")
        except IsADirectoryError:
            raise error("文件错误", f"{p} 是一个目录，不是文件。", 路径=p)
        except PermissionError:
            raise error("文件错误", f"没有权限读取：{p}", 路径=p)

    def _write_text(self, path, content, encoding="utf-8"):
        p = self._resolve(_want_text(path, "写文本"))
        try:
            os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
            with open(p, "w", encoding=encoding) as f:
                f.write(display(content))
        except PermissionError:
            raise error("文件错误", f"没有权限写入：{p}", 路径=p)
        return NIL

    def _append_text(self, path, content, encoding="utf-8"):
        p = self._resolve(_want_text(path, "追加"))
        try:
            os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
            with open(p, "a", encoding=encoding) as f:
                f.write(display(content))
        except PermissionError:
            raise error("文件错误", f"没有权限写入：{p}", 路径=p)
        return NIL

    def _exists(self, path):
        return os.path.exists(self._resolve(_want_text(path, "存在")))

    def _remove(self, path):
        p = self._resolve(_want_text(path, "删除"))
        if not os.path.exists(p):
            raise error("文件错误", f"要删除的路径不存在：{p}", 路径=p)
        try:
            os.remove(p)
        except IsADirectoryError:
            raise error("文件错误",
                        f"{p} 是目录，`删除` 只能删文件；删目录请用系统命令。",
                        路径=p)
        return NIL

    def _listdir(self, path="."):
        p = self._resolve(_want_text(path, "列表") if path != "." else path)
        try:
            return sorted(os.listdir(p))
        except FileNotFoundError:
            raise error("文件错误", f"目录不存在：{p}", 路径=p)

    def _mkdir(self, path):
        p = self._resolve(_want_text(path, "创建目录"))
        os.makedirs(p, exist_ok=True)
        return NIL

    # ---- 系统 ----

    def _getenv(self, name, default=NIL):
        return os.environ.get(_want_text(name, "环境变量"),
                              default if default is not NIL else NIL)

    def _exit(self, code=0):
        from .errors import ExitSignal
        raise ExitSignal(_want_int(code, "退出"))

    def _run_command(self, cmd, timeout=30):
        """执行外部命令。返回值：{退出码, 输出, 错误}。

        安全提示：这是与宿主系统交互的能力，仅应在可信脚本中使用。
        """
        c = _want_text(cmd, "执行")
        try:
            proc = subprocess.run(
                c, shell=True, capture_output=True, timeout=timeout,
                encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            raise error("执行错误", f"命令超时（{timeout} 秒）：{c}")
        except OSError as exc:
            raise error("执行错误", f"命令无法执行：{exc}")
        return ShuyanMap({"退出码": proc.returncode,
                          "输出": proc.stdout or "",
                          "错误": proc.stderr or ""})

    # ---- 编码 / 哈希 / 正则 ----

    def _b64decode(self, text):
        t = _want_text(text, "解码Base64")
        try:
            return base64.b64decode(t.encode("ascii")).decode("utf-8")
        except Exception:
            raise error("解码错误", "不是合法的 Base64 文本。", 值=t)

    def _hexdecode(self, text):
        t = _want_text(text, "解码十六进制")
        try:
            return bytes.fromhex(t).decode("utf-8")
        except Exception:
            raise error("解码错误", "不是合法的十六进制文本。", 值=t)

    def _hash(self, text, algorithm="sha256"):
        t = _want_text(text, "哈希")
        alg = _want_text(algorithm, "哈希").lower().replace("-", "")
        try:
            return hashlib.new(alg, t.encode("utf-8")).hexdigest()
        except ValueError:
            raise error("参数错误",
                        f"不支持的哈希算法「{algorithm}」，"
                        "可用：md5、sha1、sha256、sha512。")

    def _regex_find(self, text, pattern):
        t = _want_text(text, "正则匹配")
        p = _want_text(pattern, "正则匹配")
        try:
            return [m.group(0) for m in re.finditer(p, t)]
        except re.error as exc:
            raise error("正则错误", f"正则表达式不合法：{exc}", 模式=p)

    def _regex_sub(self, text, pattern, repl):
        t = _want_text(text, "替换")
        p = _want_text(pattern, "替换")
        r = _want_text(repl, "替换")
        try:
            return re.sub(p, r, t)
        except re.error as exc:
            raise error("正则错误", f"正则表达式不合法：{exc}", 模式=p)

    def _regex_groups(self, text, pattern):
        t = _want_text(text, "分组")
        p = _want_text(pattern, "分组")
        try:
            out = []
            for m in re.finditer(p, t):
                out.append(list(m.groups()) if m.groups() else [m.group(0)])
            return out
        except re.error as exc:
            raise error("正则错误", f"正则表达式不合法：{exc}", 模式=p)

    # -------------------------------------------------------- 查询接口

    def has_core(self, name: str) -> bool:
        return name in self.core

    def get_core(self, name: str):
        return self.core.get(name)

    def has_module(self, name: str) -> bool:
        return name in self.modules

    def module_names(self) -> list[str]:
        return sorted(self.modules.keys())

    def core_names(self) -> list[str]:
        return sorted(self.core.keys())


def _want_len_target(v):
    """`长度` 的合法目标：文本 / 列表 / 映射。"""
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        return v
    if isinstance(v, ShuyanMap):
        return v.data
    raise type_error("长度", "文本 / 列表 / 映射", type_name(v))