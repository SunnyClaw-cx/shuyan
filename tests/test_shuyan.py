"""枢言 —— 标准化测试套件（可被 `python -m unittest discover` 发现）。

**设计取舍（重要）**：本目录**不重复实现**测试用例，而是把 `shuyan.tests`
里的用例包装成标准 `unittest.TestCase`。

理由是本项目已被实测证实的铁律：**同一语义两处实现必然分叉**。
若在此处再写一套断言，两套测试迟早会给出不同结论（而且没人知道
哪一套是对的）。因此这里只做「适配」，真相源仍在 `shuyan/tests.py`
—— 那个文件里的用例是**唯一**的。

三处入口共用同一批用例：
    python sy.py 测试                          （命令行，最常用）
    python shuyan/tests.py -v                  （直接运行，带分组输出）
    python -m unittest discover -s tests -v    （标准工具，CI 用）

除包装外，本套件另加若干**独立的结构性检查**（不依赖 `shuyan.tests`）：
    - 保留名清单与标准库实际注册名的一致性（防两处清单漂移）
    - 诊断码唯一性与分档正确性
    - 文档中引用的示例文件确实存在
"""

from __future__ import annotations

import os
import sys
import unittest

# 让 `python -m unittest discover -s tests` 在仓库根目录下能导入 shuyan
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from shuyan import tests as core_tests          # noqa: E402


def _make_case(name: str, fn):
    """把 `shuyan.tests` 的一个用例包装成 TestCase 的单个测试方法。"""

    def run(self):
        try:
            fn()
        except core_tests.Failure as exc:
            self.fail(str(exc))

    run.__name__ = f"test_{name}"
    run.__doc__ = getattr(fn, "__doc__", None) or f"用例 {name}"
    return run


# ---------------------------------------------------------------- 包装用例

class 枢言用例(unittest.TestCase):
    """`shuyan/tests.py` 全部用例的 unittest 包装（真相源唯一）。"""


def _attach_core_cases() -> None:
    collected = core_tests.collect_tests()
    for gname, items in sorted(collected.items()):
        for fname, fn in items:
            unique = f"{gname}_{fname}" if gname else fname
            method_name = f"test_{unique}"
            idx = 2
            while hasattr(枢言用例, method_name):
                method_name = f"test_{unique}_{idx}"
                idx += 1
            attr = _make_case(unique, fn)
            attr.__doc__ = f"[{gname}] {getattr(fn, '__doc__', '') or fname}"
            setattr(枢言用例, method_name, attr)


_attach_core_cases()


# ------------------------------------------------------ 结构性检查（独立）

class 保留名一致性(unittest.TestCase):
    """`names.py` 与 `builtins.py` 的实际注册名必须一致。

    两处清单漂移是本项目的高发风险：`names.py` 负责词法层「整体保护」，
    `builtins.py` 负责运行期注册。若只改一处，会出现「函数存在但名字
    被切坏」这种极难定位的故障。
    """

    def test_核心函数名一致(self):
        """汉字名必须登记（否则会被关键字前缀切坏）。

        注意：拉丁别名（`print`/`input`）走的是**另一条词法路径**
        （`_latin_word` + `LATIN` 关键字表），不经汉字块切分，
        因此**不需要** `names.py` 保护。两类名字的机制不同，
        这里分开校验。
        """
        from shuyan.builtins import StdLib
        from shuyan import names
        lib = StdLib()
        actual = set(lib.core_names())
        han_actual = {n for n in actual if any("\u4e00" <= c <= "\u9fff"
                                              for c in n)}
        declared = set(names.CORE_FUNCTION_NAMES)
        missing = declared - han_actual
        extra = han_actual - declared
        self.assertFalse(
            missing, f"names.py 声明了但标准库没有实现：{sorted(missing)}")
        self.assertFalse(
            extra,
            f"标准库实现了汉字函数名但 names.py 未登记"
            f"（会导致名字被切坏）：{sorted(extra)}")

    def test_拉丁别名可解析(self):
        """拉丁别名不走汉字切分，但必须真的能解析到函数。"""
        from shuyan.lexer import Kind, Lexer
        for alias in ("print", "input"):
            toks = [t for t in Lexer(f"{alias}(1)").tokenize()
                    if t.kind is not Kind.EOF]
            self.assertEqual(
                toks[0].kind, Kind.IDENT,
                f"拉丁别名「{alias}」应解析为标识符")
            self.assertEqual(toks[0].value, alias, f"拉丁别名「{alias}」被改写")

    def test_模块名一致(self):
        from shuyan.builtins import StdLib
        from shuyan import names
        lib = StdLib()
        actual = set(lib.modules.keys())
        declared = set(names.MODULE_NAMES)
        self.assertEqual(
            sorted(declared), sorted(actual),
            f"模块名不一致：names.py={sorted(declared)} 实装={sorted(actual)}")

    def test_类型名覆盖标准类型(self):
        from shuyan import names
        from shuyan.values import TYPE_ALIASES
        # 除刻意排除的关键字同形名外，所有类型名都应被保护
        for tname in TYPE_ALIASES:
            if tname in names.KEYWORD_COLLIDING_TYPE_NAMES:
                continue
            self.assertIn(
                tname, names.PROTECTED_NAMES,
                f"类型名「{tname}」未进入保留名清单，可能被关键字前缀切坏")


class 诊断码规范(unittest.TestCase):
    """诊断码必须唯一、分档正确（D1xx 词法 / D2xx 语法 / D3xx 语义 / W4xx 警告）。"""

    def _all_codes(self):
        import shuyan.diagnostics as d
        codes = {}
        for attr in dir(d):
            if attr[:2] in ("E_", "W_") and attr.isupper():
                val = getattr(d, attr)
                if isinstance(val, str) and val[:1] in ("D", "W"):
                    codes[attr] = val
        return codes

    def test_码唯一(self):
        codes = self._all_codes()
        seen: dict[str, str] = {}
        for name, code in codes.items():
            self.assertNotIn(
                code, seen,
                f"诊断码 {code} 被 {seen.get(code)} 与 {name} 重复使用")
            seen[code] = name

    def test_分档正确(self):
        codes = self._all_codes()
        for name, code in codes.items():
            if code.startswith("W"):
                continue
            self.assertEqual(code[0], "D", f"{name} 的错误码应以 D 开头")
            self.assertIn(
                code[1], "1234",
                f"{name}={code} 的档位不在 1/2/3/4 内")

    def test_警告类别为代码提醒(self):
        from shuyan.diagnostics import Diagnostic, Severity
        from shuyan.source import Span
        codes = {v for k, v in self._all_codes().items() if v.startswith("W")}
        for code in codes:
            d = Diagnostic(code, "测试", Span(1, 1, 1, 2), Severity.WARNING)
            self.assertEqual(
                d.category, "代码提醒",
                f"警告码 {code} 的类别必须是「代码提醒」，不能叫「错误」")

    def test_结构体枚举码存在(self):
        import shuyan.diagnostics as d
        for attr in ("E_STRUCT_FIELD", "E_NO_FIELD", "E_NO_MEMBER"):
            self.assertTrue(hasattr(d, attr), f"缺少诊断码常量 {attr}")


class 文档一致性(unittest.TestCase):
    """文档里引用的示例文件必须真实存在。"""

    def test_示例文件存在(self):
        import pathlib
        ex = pathlib.Path(_ROOT) / "examples"
        found = sorted(p.name for p in ex.glob("*.sy"))
        self.assertTrue(found, "examples/ 下没有任何示例")
        # README 提到的每个 examples/xxx.sy 都应存在
        readme = (pathlib.Path(_ROOT) / "README.md").read_text(encoding="utf-8")
        import re
        for m in re.finditer(r"examples/([^\s`\)\"']+\.sy)", readme):
            name = m.group(1)
            self.assertIn(
                name, found,
                f"README 引用了 examples/{name}，但该文件不存在")

    def test_文档文件齐备(self):
        import pathlib
        docs = pathlib.Path(_ROOT) / "docs"
        for name in ("SPEC.md", "WHITEPAPER.md", "ROADMAP.md",
                     "USERMANUAL.md", "STDLIB.md", "DEVLOG.md"):
            self.assertTrue((docs / name).exists(), f"缺少文档 docs/{name}")

    def test_工程骨架齐备(self):
        import pathlib
        root = pathlib.Path(_ROOT)
        for name in ("README.md", "LICENSE", "CHANGELOG.md",
                     "CONTRIBUTING.md", ".gitignore", ".editorconfig",
                     ".gitattributes", "pyproject.toml", "Makefile"):
            self.assertTrue((root / name).exists(), f"缺少工程文件 {name}")


class 标准库文档同源(unittest.TestCase):
    """`docs/STDLIB.md` 必须与实现同源（防文档漂移）。"""

    def test_文档含全部核心函数(self):
        import pathlib
        from shuyan.builtins import StdLib
        doc = (pathlib.Path(_ROOT) / "docs" / "STDLIB.md").read_text(
            encoding="utf-8")
        lib = StdLib()
        missing = [n for n in lib.core_names() if f"`{n}`" not in doc]
        self.assertFalse(
            missing,
            f"docs/STDLIB.md 未收录这些核心函数：{sorted(missing)}"
            f"（请重跑 tools/gen_stdlib_doc.py）")


class 命名空间卫生(unittest.TestCase):
    """`shuyan` 包必须零第三方依赖（这是项目前提，不是偏好）。"""

    def test_零第三方依赖(self):
        import ast
        import pathlib
        std = set(sys.stdlib_module_names) | {"shuyan"}
        bad: list[str] = []
        for p in sorted((pathlib.Path(_ROOT) / "shuyan").glob("*.py")):
            tree = ast.parse(p.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for a in node.names:
                        root = a.name.split(".")[0]
                        if root not in std:
                            bad.append(f"{p.name}: import {a.name}")
                elif isinstance(node, ast.ImportFrom):
                    if node.level == 0 and node.module:
                        root = node.module.split(".")[0]
                        if root not in std:
                            bad.append(
                                f"{p.name}: from {node.module} import ...")
        self.assertFalse(bad, "发现第三方依赖：\n" + "\n".join(bad))


if __name__ == "__main__":
    unittest.main(verbosity=2)