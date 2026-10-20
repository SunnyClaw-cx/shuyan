"""枢言语言 —— 语法分析器（SPEC §3、§4）。

两条路线在本模块**收敛**：形式中文（书面语）与白话文（口语）产出**同一棵 AST**。
白话端不是另一门语言，而是同一语法树的另一种表面皮肤。实现方式：
词法层已把两种表面形式归一到同一批关键字语义名（`let` / `if` / `for` / `is` …），
因此解析器只需按语义名分派，不必为白话再写一套语法。

表达式采用 **Pratt 解析**（优先级表见 SPEC §4.1），语句采用递归下降。
"""

from __future__ import annotations

from .ast_nodes import (
    Assert, Assign, Binary, Block, Break, Call, Compare, Continue, ExprStmt,
    ForEach, FuncDef, FunctionExpr, Identifier, If, Index, InterpolatedString,
    ListLiteral, Literal, Logical, MapLiteral, Member, Node, Param, Program,
    RangeExpr, RepeatRange, RepeatTimes, Return, Ternary, Throw, Try, TypeDecl,
    Unary, Use, VarDecl, While,
)
from .diagnostics import (
    Diagnostic, E_COMPARE_CHAIN, E_DISCARDED_EXPR, E_EXPECTED_EXPRESSION,
    E_EXPECTED_TOKEN, E_TOO_DEEP,
    E_NO_MEMBER, E_NO_OVERLOAD, E_REDEFINED, E_STRAY_END, E_UNCLOSED_BLOCK,
    E_UNEXPECTED_TOKEN, ParseError, Severity,
)
from .lexer import Kind, Lexer, Token
from .source import Span

# 受支持的嵌套深度上限（表达式 / 语句块共用）。
#
# 取值 180 —— **实测得到，不是拍脑袋**（`tools/probe_extreme.py` 可复现）：
#
#   在 Python 默认递归上限 1000 下，实测「解析 + 语义」全链路撞爆宿主栈
#   的层数约：
#       括号 `((((...))))`      约 320 层   ≈ 3.1 Python 帧/层
#       列表 `[[[[...]]]]`      约 240 层   ≈ 4.2 Python 帧/层
#       `如果` 块嵌套           约 240 层   ≈ 4.2 Python 帧/层
#
#   本守卫对**所有形态取同一个上限**，故须按**最紧的那个**（240）留余量：
#   180 / 240 = 75%，即最坏形态下也留出 25% 的栈余量。
#   手写代码的嵌套深度几乎不会超过 10 层，180 已极其宽松。
#
# 另有一道**兜底**（`cli._report_too_deep` / `linter._too_deep_diag`）：
# 万一仍有未被覆盖的形态漏网并抛 RecursionError，也要转成枢言诊断。
# 两道并存的原因是：递归深度预算是**结构相关**的，单靠一个常数无法
# 覆盖所有语法形态；守卫负责「给出好建议」，兜底负责「绝不崩宿主」。
MAX_NEST_DEPTH = 180

# 中文运算符词在词法层已转为符号，此处仅需符号优先级表
BINARY_BP: dict[str, tuple[int, int]] = {
    "or":  (10, 11),
    "and": (20, 21),
    # 比较（禁止链式，见 §4.1）
    "==": (30, 31), "!=": (30, 31),
    "<": (30, 31), ">": (30, 31), "<=": (30, 31), ">=": (30, 31),
    "in": (30, 31), "not_in": (30, 31),
    "..": (40, 41),
    "+": (50, 51), "-": (50, 51),
    "*": (60, 61), "/": (60, 61), "%": (60, 61),
    "^": (70, 70),          # 右结合
}

COMPARE_OPS = {"==", "!=", "<", ">", "<=", ">=", "in", "not_in"}

KEYWORD_OPS: dict[str, str] = {
    "and": "and", "or": "or", "in": "in", "not_in": "not_in",
    "is": "==", "times": "times",
}

ASSIGN_OPS = {"=", "+=", "-=", "*=", "/=", "%="}

# 允许**无括号调用**的内置函数（白话写法需求）。
#
# `说一声 "你好"` 比 `说("你好")` 更接近口语，是白话端的核心体验。
# 但无括号调用若无限开放，会与「两条相邻的表达式语句」产生歧义
# （`甲\n乙` 到底是两句还是一个调用？）。故**限定在白名单内**，
# 规则明确、不外溢。
PARENLESS_CALLABLE: frozenset[str] = frozenset({"说", "输出", "打印"})

# 语句起始关键字（用于判断块是否继续）
STMT_STARTERS = {
    "let", "const", "global", "if", "elif", "else", "while", "for", "loop",
    "fn", "return", "throw", "break", "continue", "try", "catch", "finally",
    "use", "type", "assert",
}


def _name_text(tok: Token) -> str:
    """取名字的**源码原文**，并剥掉反引号逃生舱的外壳。

    反引号形式的记号（`` `进行中` ``）其 `text` 含反引号本身，`value`
    则是内部内容。规范化后的成员名应当**不含**反引号 —— 这样打印、
    比较、报错提示都干净，且声明与访问两侧自动同口径。

    这是名字原文的**唯一入口**：声明端与访问端共用它，
    避免两处各剥一次而分叉。
    """
    if tok.text[:1] == "`":
        return tok.value
    return tok.text


class Parser:
    def __init__(self, tokens: list[Token], filename: str = "<源码>"):
        # 剔除 NEWLINE 前先打标记；解析器按需跳过
        self.toks = tokens
        self.filename = filename
        self.i = 0
        self.diagnostics: list[Diagnostic] = []
        self._loop_depth = 0
        self._func_depth = 0
        self._error_count = 0        # 恐慌模式恢复用（见 _sync_to_next_line）
        self._stmt_mark = 0          # 进入当前语句时的错误计数
        # 嵌套深度计数（表达式 / 语句块共用一个上限）。
        # `_nest_reported` 保证**只报一次**：深层嵌套会触发成百上千次
        # 超限检查，若每次都报，用户会被同一根因的重复报错淹没
        # （违反 §8.5「一行错只报一条」的精神）。
        self._nest_depth = 0
        self._nest_reported = False
        # 同一根因已报错 ⇒ 抑制随之而来的「衍生」报错（如缺操作数之后的
        # 「括号未闭合」）。每条语句开始时重置。
        self._suppress_followup = False

    # ------------------------------------------------------------ 记号工具

    def _cur(self) -> Token:
        return self.toks[self.i]

    def _at(self, offset: int = 0) -> Token:
        idx = min(self.i + offset, len(self.toks) - 1)
        return self.toks[idx]

    def _advance(self) -> Token:
        tok = self.toks[self.i]
        if self.i < len(self.toks) - 1:
            self.i += 1
        return tok

    def _check(self, value: str, kind: Kind | None = None) -> bool:
        tok = self._cur()
        if kind is not None and tok.kind is not kind:
            return False
        return tok.value == value

    def _check_kind(self, kind: Kind) -> bool:
        return self._cur().kind is kind

    def _match(self, value: str) -> Token | None:
        if self._check(value):
            return self._advance()
        return None

    def _expect(self, value: str, message: str = "",
                hint: str = "") -> Token | None:
        if self._check(value):
            return self._advance()
        self._error(E_EXPECTED_TOKEN,
                    message or f"这里应该是「{value}」，但看到了「{self._cur().text}」。",
                    self._cur().span, [hint] if hint else [])
        return None

    def _skip_newlines(self) -> None:
        while self._check_kind(Kind.NEWLINE):
            self._advance()

    def _skip_pause(self) -> None:
        """跳过**语句层**的句读逗号（SPEC §2.7 中文特有规则）。

        白话写法 `若 年龄 不小于 18，那么` 中的 `，` 只是自然汉语的停顿，
        不承担分隔语义。该规则**不**适用于括号 / 列表 / 映射 / 参数列表内部
        —— 那些位置由各自的解析函数直接消费 `,`，不会走到这里。
        """
        while self._check(","):
            self._advance()

    def _skip_terminators(self) -> None:
        """跳过语句结束符（换行 / 分号）与**句读逗号**。

        语句层的 `,` 只可能是自然汉语的停顿（如 `设 甲 是 5，`），
        真正承担分隔语义的 `,` 全部出现在括号/列表/参数列表内部，
        由各自的解析函数直接消费，不会走到这里。
        """
        while True:
            if self._check_kind(Kind.NEWLINE) or self._check(";") \
                    or self._check(","):
                self._advance()
                continue
            break

    def _error(self, code: str, message: str, span: Span,
               hints: list[str] | None = None,
               severity: Severity = Severity.ERROR) -> None:
        self.diagnostics.append(Diagnostic(code, message, span, severity, hints or []))
        if severity is Severity.ERROR:
            self._error_count += 1

    def _mark(self) -> int:
        """记录当前已产生的**错误**条数，供语句层判断「本条是否出错」。"""
        return self._error_count

    def _sync_to_next_line(self, mark: int) -> None:
        """恐慌模式恢复：本条语句期间产生过错误 ⇒ 跳到下一行行首。

        **为什么必须这样做**：解析器一旦在语句中途出错，后续记号已经
        失去可靠的上下文，继续按原语法硬解析会产生**级联假错误**
        （实测：`定义 乙 == 2` 一行会报出 2 条，并进一步污染下一行，
        最终 1 个错变 4 个错）。真正的编译器不会这样 ——
        用户写错一行，就应该只看到那**一行**的问题。

        做法：若本条语句期间新增了错误，则丢弃本行剩余记号，直接把游标
        移到下一个换行之后。这牺牲了「同一行多错」的报告能力，
        换来的是**报错数量与用户真实的错误数量成正比** —— 这才是对的取舍。

        若本条**没有**出错则原地不动（正常路径零开销）。
        """
        if self._error_count == mark:
            return
        depth = 0
        while not self._check_kind(Kind.EOF):
            tok = self._cur()
            if tok.kind is Kind.NEWLINE and depth == 0:
                break
            if tok.kind is Kind.OP:
                if tok.value in ("(", "[", "{"):
                    depth += 1
                elif tok.value in (")", "]", "}"):
                    depth = max(0, depth - 1)
            self._advance()
        # 吃掉换行本身，停在下一条语句的行首
        if self._check_kind(Kind.NEWLINE):
            self._advance()

    def _synchronize(self) -> None:
        """跳到下一个语句起点，保证一次报告多个错误。"""
        while not self._check_kind(Kind.EOF):
            if self._check_kind(Kind.NEWLINE):
                self._advance()
                return
            if self._cur().kind is Kind.KEYWORD and self._cur().value in STMT_STARTERS:
                return
            self._advance()

    # ------------------------------------------------------------ 入口

    def parse(self) -> Program:
        body = self._parse_statements(stop_values={"end", "else", "elif",
                                                  "catch", "finally"})
        if not self._check_kind(Kind.EOF):
            if self._check("end"):
                self._error(E_STRAY_END, "多余的「结束」——没有与之配对的块开启语句。",
                            self._cur().span,
                            ["删除这一行，或检查上层是否漏写 `如果` / `遍历` 等开启语句"])
            else:
                self._error(E_UNEXPECTED_TOKEN,
                            f"意料之外的记号「{self._cur().text}」。",
                            self._cur().span)
        if self.diagnostics:
            raise ParseError(self.diagnostics)
        return Program(span=body[0].span if body else Span(1, 1, 1, 1), body=body)

    # ------------------------------------------------------------ 语句序列

    def _parse_statements(self, stop_values: set[str],
                          stop_on_end_kind: bool = False) -> list[Node]:
        """解析语句序列，直到遇到 stop_values 中的关键字或 EOF。

        块终止条件：`结束`/`完`(end)、`否则`(else)、`否则如果`(elif)、
        `捕获`/`出错了`(catch)、`最终`(finally)、右花括号、EOF。
        """
        stmts: list[Node] = []
        while True:
            # 交替清理「句读逗号」与「语句终止符」，直到稳定。
            # 必须交替——跳过逗号后可能正好停在换行上（`设 甲 是 5，` + 换行）。
            while True:
                before = self.i
                self._skip_pause()
                self._skip_terminators()
                if self.i == before:
                    break
            if self._check_kind(Kind.EOF):
                break
            if self._check("}"):
                break
            tok = self._cur()
            if tok.kind is Kind.KEYWORD and tok.value in stop_values:
                break
            before = self.i
            mark = self._mark()
            self._stmt_mark = mark
            self._suppress_followup = False
            stmt = self._parse_statement()
            if stmt is not None:
                stmts.append(stmt)
            if self.i == before:
                # 未推进 ⇒ 强制跳过一枚记号，避免死循环
                self._error(E_UNEXPECTED_TOKEN,
                            f"无法解析的记号「{tok.text}」。", tok.span)
                self._advance()
                self._synchronize()
            else:
                # 本条语句出错 ⇒ 丢弃本行剩余记号，防止级联假错误
                self._sync_to_next_line(mark)
        return stmts

    # ------------------------------------------------------------ 单条语句

    def _parse_statement(self) -> Node | None:
        tok = self._cur()

        if tok.kind is Kind.KEYWORD:
            v = tok.value
            if v == "let":
                return self._parse_var_decl(is_const=False)
            if v == "const":
                return self._parse_var_decl(is_const=True)
            if v == "global":
                return self._parse_global_decl()
            if v == "if":
                return self._parse_if()
            if v == "while":
                return self._parse_while()
            if v == "for":
                return self._parse_for()
            if v == "loop":
                return self._parse_loop()
            if v == "fn":
                return self._parse_func_def()
            if v == "return":
                return self._parse_return()
            if v == "throw":
                return self._parse_throw()
            if v == "break":
                self._advance()
                return Break(span=tok.span)
            if v == "continue":
                self._advance()
                return Continue(span=tok.span)
            if v == "try":
                return self._parse_try()
            if v == "use":
                return self._parse_use()
            if v == "type":
                return self._parse_type_decl()
            if v == "assert":
                return self._parse_assert()
            if v in ("true", "false", "nil", "not"):
                return ExprStmt(span=tok.span, expr=self._parse_expression())
            # 段 6：函数式定义 `定义 一个 函数 叫 名字 ...`
            if v in ("elif", "else", "catch", "finally", "end"):
                self._error(E_UNEXPECTED_TOKEN,
                            f"意料之外的「{tok.text}」——没有与之配对的开启语句。",
                            tok.span)
                self._advance()
                return None

        if tok.kind is Kind.OP and tok.value == "{":
            return self._parse_brace_block()

        # 赋值 / 表达式语句
        return self._parse_assign_or_expr()

    # --------------------------------------------------------- 声明与赋值

    def _parse_var_decl(self, is_const: bool) -> Node:
        start = self._advance()          # let / const
        span = start.span
        name = self._parse_ident_name("变量名")
        type_ann = self._parse_type_annotation() if self._check(":") else None

        # 白话写法：`定义 甲 是 3` —— `是` 在此为赋值，不是比较
        init = None
        if self._match("="):
            init = self._parse_expression()
        elif self._check("is"):
            self._advance()
            init = self._parse_expression()

        if is_const and init is None:
            self._error("D3001", "常量声明必须给出初始值。", span,
                        ["写成 `常量 名字 = 值`，或改用 `定义`"])
        self._guard_trailing_tokens(span)
        self._skip_terminators()
        return VarDecl(span=span, name=name, type_ann=type_ann,
                       init=init, is_const=is_const)

    def _parse_global_decl(self) -> Node:
        start = self._advance()
        name = self._parse_ident_name("变量名")
        if self._match("=") or self._match("is"):
            value = self._parse_expression()
        else:
            value = None
        self._skip_terminators()
        return VarDecl(span=start.span, name=name, init=value,
                       is_const=False, is_global=True)

    def _parse_ident_name(self, what: str) -> str:
        tok = self._cur()
        if tok.kind is Kind.IDENT:
            self._advance()
            return tok.value
        if tok.kind is Kind.KEYWORD:
            # 关键字不可作标识符——给出明确指引
            self._error("D3006",
                        f"{what}不能使用关键字「{tok.text}」。",
                        tok.span,
                        [f"换一个名字，或用反引号形式 `{tok.text}`（整体作为标识符）"])
            self._advance()
            return tok.value
        self._error(E_EXPECTED_TOKEN, f"这里应该是一个{what}。", tok.span)
        self._advance()
        return "<错误>"

    def _parse_type_annotation(self) -> str | None:
        if not self._match(":"):
            return None
        tok = self._cur()
        if tok.kind is Kind.IDENT:
            self._advance()
            return tok.value
        if tok.kind is Kind.KEYWORD:
            self._advance()
            return tok.value
        if self._check("["):
            self._advance()
            inner = self._parse_type_annotation_name()
            self._expect("]")
            return f"[{inner}]"
        self._error(E_EXPECTED_TOKEN, "类型标注后应该跟类型名（如 整数、文本、列表）。",
                    tok.span)
        return None

    def _parse_type_annotation_name(self) -> str:
        tok = self._cur()
        if tok.kind in (Kind.IDENT, Kind.KEYWORD):
            self._advance()
            return tok.value
        return "任意"

    def _parse_assign_or_expr(self) -> Node:
        start_span = self._cur().span
        expr = self._parse_expression()

        if self._cur().kind is Kind.OP and self._cur().value in ASSIGN_OPS:
            op = self._advance().value
            value = self._parse_expression()
            self._skip_terminators()
            if not isinstance(expr, (Identifier, Index, Member)):
                self._error("D2011",
                            "赋值号左侧必须是变量、列表元素或对象属性。",
                            start_span, ["检查左侧表达式"])
            return Assign(span=start_span, target=expr, op=op, value=value)

        # 白话赋值：`甲 是 3` —— 解析器已在表达式层把 `是` 转为 `==`，
        # 此处按「语句起始 IDENT + 单个 == + 右侧」的形态判回赋值。
        if (isinstance(expr, Compare) and expr.op == "=="
                and isinstance(expr.left, Identifier)):
            return Assign(span=start_span, target=expr.left, op="=",
                          value=expr.right)

        # 无括号调用（白话写法）：`说 "你好"` / `说 名字`
        # 仅对白名单内的标识符开放，且右侧必须紧跟一个值记号。
        if (isinstance(expr, Identifier)
                and expr.name in PARENLESS_CALLABLE
                and self._starts_value()):
            arg = self._parse_expression()
            self._skip_terminators()
            return ExprStmt(span=start_span,
                            expr=Call(span=start_span, callee=expr,
                                      args=[arg], kwargs={}))

        self._guard_trailing_tokens(start_span)
        self._skip_terminators()
        self._guard_discarded_expr(expr, start_span)
        return ExprStmt(span=start_span, expr=expr)

    def _guard_discarded_expr(self, expr: Node, span: Span) -> None:
        """表达式语句「算完就丢」⇒ 报错，而不是静默丢弃。

        **实测踩到的真实缺陷**（企业级工程 `projects/应收对账/报表.sy`）：

            请 表头()
                返回 右对齐("序号", 4) + "  "
                    + 左对齐("单号", 14) + "  "     // 行首 `+`
                    ...
            结束

        枢言的续行规则是**行尾运算符**续行（SPEC §2.x）。行首的 `+`
        不会续行，于是它被当成**新语句**的开始：一元正号表达式。
        整段拼接语句就此断裂，`+ 左对齐("单号", 14) + "  "` 被当成
        「算出来就扔掉」的语句 —— **不报错、不提醒、结果少了一整列**。

        报表标题行、明细行因此少掉全部左对齐列；用户看到的是「结果不对」，
        却找不到任何报错。这正是本项目最危险的一类缺陷
        （错误结果 + 无人察觉），必须把它变成一句可读的提示。

        **为什么是报错而非自动续行**：自动把行首运算符接回上一行，会改变
        语言语义（`-x` 独立成句就不再合法），也违背「规则确定」的承诺。
        规范已冻结，故只把「沉默」改为「响亮」，并给出改法。
        """
        if isinstance(expr, Call):
            return                      # 函数调用有副作用，是正常语句
        if isinstance(expr, (Identifier, Member, Index)):
            # `甲` / `甲.字段` / `甲[0]` 独立成句虽无效果，但常是**调试残留**
            # 或从别的语言带过来的习惯，不算「拼接断裂」，保留宽容。
            return
        # 到这里只剩「纯计算」结果：算术 / 比较 / 逻辑 / 一元 / 字面量。
        # 它们没有任何副作用，值一算出来就没了。
        if isinstance(expr, (Binary, Compare, Logical, Unary, Ternary,
                             Literal)):
            self._error(
                E_DISCARDED_EXPR,
                "这条语句算出结果就丢掉了，没有任何效果"
                "（可能是上一行的拼接被这里的运算符截断了）。",
                span,
                ["枢言的续行规则要求运算符放在上一行行尾，"
                 "行首运算符不会接续上一行",
                 "把运算符挪到上一行行尾，或用圆括号把整个表达式括起来",
                 "若确实想丢弃结果，请显式写成 `_ = 表达式`"])

    def _guard_trailing_tokens(self, span: Span) -> None:
        """语句解析完却还有剩余记号 ⇒ 明确报错，而不是静默丢弃。

        **为什么必须加这道守卫**：静默丢弃比报错危险得多。`2 的 10 次方`
        若被解析成 `2` 且不报错，用户会以为语言算错了——这是最坏的一类
        缺陷（错误结果 + 无人察觉）。守卫把它变成一句可读的提示。

        **守卫对象是「任何残余记号」**，不限于像值的记号。曾经只拦「像值」
        的记号，于是 `定义 乙 == 2` 里剩下的 `==`（不是值起始）被静默放过，
        再由下一轮把它当成新语句报错 —— 一行错被拆成两条、指向两个位置，
        用户会以为写错了两个地方。**一行错只该报一条**。

        提示指向**根因**（紧邻前一个记号的非法字），而非紧跟其后的记号，
        这样用户一眼就能看到是哪个字出了问题。
        """
        tok = self._cur()
        if tok.kind in (Kind.EOF, Kind.NEWLINE):
            return
        if tok.value in (";", ",", "}", "end", "else", "elif", "catch",
                         "finally", ")"):
            return
        # **本条语句已经报过错 ⇒ 不再补刀。**
        # 例：`定义 乙 = (1 + 2` 的括号未闭合，报错发生在换行之后
        # （游标已跨行），此时守卫若再报一次「这里不该出现『定义』」，
        # 用户会看到同一行的两条错误、且第二条指向了下一行的关键字。
        # 一行错只报一条，第一条（更接近根因的那条）已足够。
        if self._error_count > self._stmt_mark:
            self._skip_to_line_end()
            return
        if self._starts_value():
            # 像值的残余：多为 `2 的 10 次方` 这类「中文词当运算符」的误用
            self._error(E_UNEXPECTED_TOKEN,
                        f"「{tok.text}」两侧没有运算符，枢言无法理解这里的意图。",
                        tok.span,
                        ["中文里像「的」这样的常用字不是运算符",
                         "如果要求幂，请写 `次方(底, 指数)` 或 `底 ^ 指数`",
                         "如果是要拼接文本，请写 `+`"])
        else:
            # 其他残余（运算符、意外的关键字等）：同样是语句没写完
            self._error(E_UNEXPECTED_TOKEN,
                        f"这里不该出现「{tok.text}」。",
                        tok.span,
                        ["检查这一行是否漏写、或写错了符号",
                         "一条语句应当在一个概念内写完"])
        # 丢弃本行剩余记号，避免同一行被拆成多条错误
        self._skip_to_line_end()

    def _skip_to_line_end(self) -> None:
        """丢弃本行剩余记号（括号内的也一并跳过），停在行尾（不含换行）。"""
        depth = 0
        while not self._check_kind(Kind.EOF):
            tok = self._cur()
            if tok.kind is Kind.NEWLINE and depth == 0:
                return
            if tok.kind is Kind.OP:
                if tok.value in ("(", "[", "{"):
                    depth += 1
                elif tok.value in (")", "]", "}"):
                    if depth == 0:
                        return
                    depth -= 1
            self._advance()

    def _starts_value(self) -> bool:
        """当前记号是否可以作为「值」的开始（用于无括号调用的判定）。"""
        tok = self._cur()
        if tok.kind in (Kind.NUMBER, Kind.STRING):
            return True
        if tok.kind is Kind.IDENT:
            return True
        if tok.kind is Kind.OP and tok.value in ("(", "[", "{", "-", "+", "!"):
            return True
        if tok.kind is Kind.KEYWORD and tok.value in ("true", "false", "nil",
                                                     "not", "fn"):
            return True
        return False

    # ------------------------------------------------------------ 分支

    def _parse_if(self) -> Node:
        start = self._advance()          # if
        branches = []
        cond = self._parse_expression()
        self._skip_pause()               # 句读逗号：`若 条件，那么`
        self._skip_inline_filler("then")
        body = self._parse_block(stop={"elif", "else", "end"})
        branches.append((cond, body))

        while self._check("elif"):
            self._advance()
            c = self._parse_expression()
            self._skip_pause()
            self._skip_inline_filler("then")
            b = self._parse_block(stop={"elif", "else", "end"})
            branches.append((c, b))

        if self._check("else"):
            self._advance()
            self._skip_pause()
            b = self._parse_block(stop={"end"})
            branches.append((None, b))

        self._expect_end("如果", start.span)
        return If(span=start.span, branches=branches)

    def _skip_inline_filler(self, name: str) -> None:
        """跳过可选的软分隔词（`则` / `那么`，词法层已剥离为无记号）。

        保留接口以便后续接入显式分隔词。
        """
        return

    # ------------------------------------------------------------ 循环

    def _parse_while(self) -> Node:
        start = self._advance()
        cond = self._parse_expression()
        body = self._parse_block(stop={"end"})
        self._expect_end("当", start.span)
        return While(span=start.span, cond=cond, body=body)

    def _parse_for(self) -> Node:
        start = self._advance()          # for
        # 白话与形式两种形态必须都支持：
        #   形式：`遍历 变量 中 表达式`        → 先读变量名
        #   白话：`遍历 表达式 中的每个 变量名`  → 先读表达式
        # 判定方式：若第二个记号就是 `each`/`in`，按形式读；否则按白话读。
        if self._check("each"):
            # 形式写法漏了变量名？`遍历 中的每个 x` 属语法错误
            pass

        if (self._cur().kind is Kind.IDENT
                and self._at(1).value in ("each", "in")):
            var = self._parse_ident_name("循环变量")
            if self._check("each"):
                self._advance()
                iterable = Identifier(span=start.span, name=var)
                var = self._parse_ident_name("循环变量")
            else:
                self._advance()          # in
                iterable = self._parse_expression()
        else:
            # 白话写法：先读表达式，再等 `中的`/`中`/`each`
            iterable = self._parse_expression()
            if self._check("each"):
                self._advance()
            elif self._check("in"):
                self._advance()
            else:
                self._error(E_EXPECTED_TOKEN,
                            "遍历语句缺少「中的每个」或「中」。",
                            self._cur().span,
                            ["形式写法：`遍历 变量 中 列表`；"
                             "白话写法：`遍历 列表 中的每个 变量`"])
            var = self._parse_ident_name("循环变量")
        body = self._parse_block(stop={"end"})
        self._expect_end("遍历", start.span)
        return ForEach(span=start.span, var_name=var, iterable=iterable, body=body)

    def _parse_loop(self) -> Node:
        start = self._advance()          # loop
        # `重复 3 次` / `重复三次` / `重复 甲 从 1 到 5 步长 2` / `循环 ...`
        if self._check_kind(Kind.IDENT) and self._check_ahead_is_from():
            var = self._parse_ident_name("循环变量")
            self._expect("from", "这里应该是「从」。")
            lo = self._parse_expression()
            self._expect("to", "这里应该是「到」。")
            hi = self._parse_expression()
            step = None
            if self._check("step"):
                self._advance()
                step = self._parse_expression()
            body = self._parse_block(stop={"end"})
            self._expect_end("重复", start.span)
            return RepeatRange(span=start.span, var_name=var, start=lo,
                               stop=hi, step=step, body=body)

        if self._check_kind(Kind.EOF) or self._check_kind(Kind.NEWLINE):
            body = self._parse_block(stop={"end"})
            self._expect_end("循环", start.span)
            return While(span=start.span,
                         cond=Literal(span=start.span, value=True,
                                      literal_kind="bool"),
                         body=body)

        times = self._parse_expression()
        if self._check("times"):
            self._advance()
        body = self._parse_block(stop={"end"})
        self._expect_end("重复", start.span)
        return RepeatTimes(span=start.span, times=times, body=body)

    def _check_ahead_is_from(self) -> bool:
        return self._at(1).value == "from"

    # ------------------------------------------------------------ 函数

    def _parse_func_def(self) -> Node:
        start = self._advance()          # fn / 请 / 让 / 定义 一个 函数 叫
        # 白话写法 `让 名字 来做`：`让` 后跟名字、可选 `来做`（词法层已剥离）
        name = self._parse_ident_name("函数名")

        if not self._check("("):
            # 白话写法：`定义 一个 函数 叫 名字 参数是 a 和 b 做的事`
            self._parse_oral_params()
            body = self._parse_block(stop={"end"})
            self._expect_end("函数", start.span)
            return FuncDef(span=start.span, name=name, params=[], body=body)

        self._advance()                   # (
        params = self._parse_params()
        self._expect(")", "参数列表未闭合，缺少 )")
        return_type = None
        if self._check("return"):
            self._advance()
            return_type = self._parse_type_annotation_name()
        body = self._parse_block(stop={"end"})
        self._expect_end("函数", start.span)
        return FuncDef(span=start.span, name=name, params=params,
                       body=body, return_type=return_type)

    def _parse_oral_params(self) -> list[Param]:
        return []

    def _parse_params(self) -> list[Param]:
        params: list[Param] = []
        if self._check(")"):
            return params
        while True:
            variadic = bool(self._match("..."))
            tok = self._cur()
            if tok.kind is not Kind.IDENT:
                self._error(E_EXPECTED_TOKEN, "这里应该是参数名。", tok.span)
                break
            self._advance()
            p = Param(span=tok.span, name=tok.value, is_variadic=variadic)
            if self._check(":"):
                p.type_ann = self._parse_type_annotation()
            if self._match("="):
                p.default = self._parse_expression()
            params.append(p)
            if not self._match(","):
                break

        # 默认参数顺序校验（SPEC §3.6，D3002）
        seen_default = False
        for p in params:
            if p.is_variadic:
                continue
            if p.default is not None:
                seen_default = True
            elif seen_default:
                self._error("D3002",
                            f"参数「{p.name}」没有默认值，但它后面的参数有默认值。",
                            p.span,
                            ["把带默认值的参数全部移到参数列表末尾"])
        return params

    def _parse_return(self) -> Node:
        start = self._advance()
        if self._check_kind(Kind.NEWLINE) or self._check(";") \
                or self._check_kind(Kind.EOF) or self._check("}"):
            self._skip_terminators()
            return Return(span=start.span, value=None)
        if self._cur().kind is Kind.KEYWORD and self._cur().value in ("end",):
            return Return(span=start.span, value=None)
        value = self._parse_expression()
        self._skip_terminators()
        return Return(span=start.span, value=value)

    def _parse_throw(self) -> Node:
        start = self._advance()
        value = self._parse_expression()
        self._skip_terminators()
        return Throw(span=start.span, value=value)

    def _parse_try(self) -> Node:
        start = self._advance()          # try
        body = self._parse_block(stop={"catch", "finally", "end"})
        handlers = []
        while self._check("catch"):
            self._advance()
            var = None
            if self._cur().kind is Kind.IDENT:
                var = self._advance().value
            hbody = self._parse_block(stop={"catch", "finally", "end"})
            handlers.append((var, hbody))
        finalizer = None
        if self._check("finally"):
            self._advance()
            finalizer = self._parse_block(stop={"end"})
        self._expect_end("试", start.span)
        return Try(span=start.span, body=body, handlers=handlers,
                   finalizer=finalizer)

    def _parse_use(self) -> Node:
        start = self._advance()
        names = None
        if self._check("{"):
            self._advance()
            names = []
            while not self._check("}") and not self._check_kind(Kind.EOF):
                names.append(self._parse_ident_name("模块成员名"))
                if not self._match(","):
                    break
            self._expect("}")
            if self._check("from"):
                self._advance()
        tok = self._cur()
        if tok.kind is Kind.STRING:
            self._advance()
            module = tok.value
        elif tok.kind is Kind.IDENT:
            self._advance()
            module = tok.value
        else:
            self._error(E_EXPECTED_TOKEN, "引入语句需要一个模块名（如 字符串形式 \"数学\"）。",
                        tok.span)
            module = "<未知>"
        self._skip_terminators()
        return Use(span=start.span, module=module, names=names)

    def _parse_type_decl(self) -> Node:
        """类型声明（SPEC §3.9）：别名 / 结构体 / 枚举。

        判定规则（单趟、无回溯）：
          - 右侧为 `{ ... }` 且**存在含 `:` 的条目** → 结构体
          - 右侧为 `{ ... }` 且**全部条目不含 `:`**   → 枚举
          - 两种条目混用 → 报错 D3019
          - 其余 → 类型别名
        """
        start = self._advance()
        name = self._parse_ident_name("类型名")
        self._match("=")
        tok = self._cur()

        # ---- 结构体 / 枚举 ----
        if tok.kind is Kind.OP and tok.value == "{":
            return self._parse_bracket_type_body(start, name)

        # ---- 类型别名 ----
        definition = tok.text
        if tok.kind in (Kind.IDENT, Kind.KEYWORD):
            self._advance()
        else:
            self._error(E_EXPECTED_TOKEN, "类型声明缺少右侧定义。", tok.span)
        self._skip_terminators()
        return TypeDecl(span=start.span, name=name, definition=definition,
                        kind="alias")

    def _parse_bracket_type_body(self, start: Token, name: str) -> Node:
        """解析 `{ ... }` 形态的类型定义（结构体或枚举）。"""
        self._advance()                                  # {
        fields: list = []                                # (名, 类型, Span)
        members: list = []                               # (名, Span)

        self._skip_newlines()
        while not self._check("}") and not self._check_kind(Kind.EOF):
            member_tok = self._parse_type_entry_name()
            entry_value = member_tok.value      # 归一后（与具名参数键同口径）
            entry_text = _name_text(member_tok)  # 源码原文（剥反引号外壳）
            self._skip_newlines()
            if self._check(":"):
                self._advance()
                ftype = self._parse_type_annotation_name()
                fields.append((entry_value, ftype, member_tok.span))
            else:
                members.append((entry_text, member_tok.span))
            self._skip_newlines()
            # `}` 前允许尾随逗号
            if not self._match(","):
                break
            self._skip_newlines()

        self._expect("}", "类型定义的 } 未闭合")
        self._skip_terminators()

        if fields and members:
            self._error(E_NO_MEMBER,
                        f"类型「{name}」的定义里混用了两种写法："
                        f"带冒号的当字段（结构体），不带冒号的当成员（枚举）。",
                        start.span,
                        ["结构体写成 `类型 X = { 字段: 类型, ... }`",
                         "枚举写成 `类型 X = { 成员1, 成员2, ... }`"])
            return TypeDecl(span=start.span, name=name, kind="alias",
                            definition="{}")

        if not fields and not members:
            self._error(E_NO_MEMBER,
                        f"类型「{name}」的定义是空的。",
                        start.span,
                        ["结构体缺字段：`类型 X = { 字段: 类型 }`",
                         "枚举缺成员：`类型 X = { 成员1, 成员2 }`"])
            return TypeDecl(span=start.span, name=name, kind="alias",
                            definition="{}")

        if fields:
            seen: dict[str, Span] = {}
            for fname, _ftype, fspan in fields:
                if fname in seen:
                    self._error(E_REDEFINED,
                                f"结构体「{name}」的字段「{fname}」重复声明。",
                                fspan,
                                [f"它在第 {seen[fname].line} 行已经声明过了"])
                seen[fname] = fspan
            return TypeDecl(span=start.span, name=name, kind="struct",
                            fields=fields,
                            definition="{" + ", ".join(
                                f"{f}: {t}" for f, t, _ in fields) + "}")

        seen_m: dict[str, Span] = {}
        for mname, mspan in members:
            if mname in seen_m:
                self._error(E_REDEFINED,
                            f"枚举「{name}」的成员「{mname}」重复声明。",
                            mspan,
                            [f"它在第 {seen_m[mname].line} 行已经声明过了"])
            seen_m[mname] = mspan
        return TypeDecl(span=start.span, name=name, kind="enum",
                        members=members,
                        definition="{" + ", ".join(m for m, _ in members) + "}")

    def _parse_type_entry_name(self) -> Token:
        """读取类型定义里的字段名 / 成员名，返回**记号本身**。

        与普通标识符不同：此处的名字**允许是关键字与中文数词**
        （`类型 季节 = { 一, 二 }`、`类型 选项 = { 是, 否 }`），
        因为类型定义是**受限上下文**，不存在切分歧义。

        调用方需按用途各取其字段：
          - 结构体字段名取 `value`（归一后，与具名参数键同口径）
          - 枚举成员名取 `text`（源码原文，`一` 不会被归一成 `1`）
        """
        tok = self._cur()
        if tok.kind is Kind.EOF:
            self._error(E_EXPECTED_TOKEN, "类型定义里需要一个字段名或成员名。",
                        tok.span)
            return tok
        self._advance()
        return tok

    def _parse_assert(self) -> Node:
        start = self._advance()
        cond = self._parse_expression()
        msg = None
        if self._match(","):
            msg = self._parse_expression()
        self._skip_terminators()
        return Assert(span=start.span, cond=cond, message=msg)

    # ------------------------------------------------------------ 块

    def _parse_brace_block(self) -> Block:
        start = self._advance()          # {
        body = []
        while not self._check("}") and not self._check_kind(Kind.EOF):
            self._skip_terminators()
            if self._check("}"):
                break
            stmt = self._parse_statement()
            if stmt is not None:
                body.append(stmt)
        self._expect("}", "块未闭合，缺少 }")
        return Block(span=start.span, body=body)

    def _parse_block(self, stop: set[str]) -> list[Node]:
        """解析一个块体：支持 `{ }`、`结束` 结尾、或缩进式（v0.1 按语句序列处理）。"""
        # **语句块嵌套也要守卫**：`如果 ... 如果 ... 如果 ...` 每层都会
        # 递归调用 `_parse_statements`，1000 层同样会撞爆 Python 栈
        # （实测宿主 RecursionError）。与表达式共用同一个上限与同一个
        # 「只报一次」标记。
        self._nest_depth += 1
        try:
            if self._nest_depth > MAX_NEST_DEPTH:
                if not self._nest_reported:
                    self._nest_reported = True
                    self._error(E_TOO_DEEP,
                                f"语句块嵌套太深（超过 {MAX_NEST_DEPTH} 层），"
                                f"已停止解析以免栈溢出。",
                                self._cur().span,
                                ["把内层逻辑抽成一个函数再调用",
                                 "深层嵌套通常可以用循环改写"])
                return []
            if self._check("{"):
                blk = self._parse_brace_block()
                return blk.body
            # 允许 `则` 后的同一行语句
            self._skip_terminators()
            return self._parse_statements(stop_values=stop | {"end"})
        finally:
            self._nest_depth -= 1

    def _expect_end(self, owner: str, start_span: Span) -> None:
        if self._check("end"):
            self._advance()
            return
        if self._check("}"):
            return
        if self._check_kind(Kind.EOF):
            self._error(E_UNCLOSED_BLOCK,
                        f"第 {start_span.line} 行开始的「{owner}」块没有闭合。",
                        start_span,
                        [f"在该块末尾补一个「结束」（或白话的「完」）"])
            return
        self._error(E_EXPECTED_TOKEN,
                    f"「{owner}」块应以「结束」收尾，但看到了「{self._cur().text}」。",
                    self._cur().span,
                    [f"补上「结束」"])

    # ------------------------------------------------------------ 表达式

    def _parse_expression(self, min_bp: int = 0) -> Node:
        # **嵌套深度守卫**（唯一入口，故一处覆盖所有嵌套路径）。
        #
        # 递归下降解析器每进入一层嵌套就多一层 Python 调用栈。实测：
        #   · 1000 层括号 `((((...1...))))`  ⇒ 宿主 RecursionError
        #   · 500 层列表 `[[[[...1...]]]]`   ⇒ 宿主 RecursionError
        #   · 1000 层 `如果` 嵌套              ⇒ 宿主 RecursionError
        # 用户看到的是一段 **Python 堆栈**，而不是枢言诊断 —— 这是
        # 「宿主崩溃」，比普通报错严重得多（工具链崩了，用户无从下手）。
        #
        # 阈值取 300：远低于 Python 默认递归上限（1000），留足余量给
        # 解析器自身的调用层次；同时远高于正常人会写的嵌套深度
        # （手写代码几乎不会超过 10 层）。超限即报**可行动**的诊断，
        # 而不是硬撑到栈溢出。
        self._nest_depth += 1
        try:
            if self._nest_depth > MAX_NEST_DEPTH:
                if not self._nest_reported:
                    self._nest_reported = True
                    self._error(E_TOO_DEEP,
                                f"表达式嵌套太深（超过 {MAX_NEST_DEPTH} 层），"
                                f"已停止解析以免栈溢出。",
                                self._cur().span,
                                ["把表达式拆成多个中间变量（`定义 甲 = ...`）",
                                 "深层嵌套通常可以用循环或递归函数改写"])
                return Literal(span=self._cur().span, value=None,
                               literal_kind="nil")
            return self._parse_expression_inner(min_bp)
        finally:
            self._nest_depth -= 1

    def _parse_expression_inner(self, min_bp: int = 0) -> Node:
        left = self._parse_prefix()
        if left is None:
            return Literal(span=self._cur().span, value=None, literal_kind="nil")

        while True:
            tok = self._cur()

            # 后缀：调用 / 索引 / 成员
            if tok.kind is Kind.OP and tok.value == "(":
                left = self._parse_call(left)
                continue
            if tok.kind is Kind.OP and tok.value == "[":
                left = self._parse_index(left)
                continue
            if tok.kind is Kind.OP and tok.value == ".":
                dot = self._advance()
                name_tok = self._cur()
                if name_tok.kind in (Kind.IDENT, Kind.KEYWORD, Kind.NUMBER):
                    self._advance()
                    left = Member(span=dot.span, target=left,
                                  name=name_tok.value,
                                  name_text=_name_text(name_tok))
                else:
                    self._error(E_EXPECTED_TOKEN, "成员访问符 . 后面应该是属性名。",
                                name_tok.span)
                continue

            # 二元
            op, lbp, rbp = self._binary_op(tok)
            if op is None or lbp < min_bp:
                break

            op_tok = self._advance()
            # **二元运算符的右操作数不得跨行到「语句起始关键字」。**
            #
            # `输出(甲 +` 换行后紧跟 `定义 丙 = 3` —— 若照常去下一行取操作数，
            # 会把 `定义` 当成表达式的开始，于是报出「这里需要一个表达式，
            # 但看到了『定义』」，再叠加一条「参数『丙』两侧没有运算符」，
            # **一条错误（漏了右括号）被放大成两条**，且两条的位置都指向
            # 下一行的关键字，与真正的根因（第 1 行末尾）相距甚远。
            #
            # 直接在这一行收尾：让上层的 `_expect(")")` 报出「括号未闭合」，
            # 那条提示才指向正确的行。多行运算符续写本就不应跨语句边界。
            if self._at_stmt_start():
                self._error(E_EXPECTED_EXPRESSION,
                            f"「{op_tok.text}」右边缺少操作数。",
                            op_tok.span,
                            ["检查这一行的运算符是否漏写了右边的值",
                             "若上一行有未闭合的 ( 或 [ ，先补齐它"])
                # 本条语句已出错 ⇒ 后续的「括号未闭合」只是**同一个根因的
                # 另一面**，不再重复报（否则用户看到两条，以为错了两处）。
                self._suppress_followup = True
                break
            # `in` / `not_in` 是关键字
            right = self._parse_expression(rbp)

            if op in COMPARE_OPS:
                left = Compare(span=left.span, op=op, left=left, right=right)
            elif op in ("and", "or"):
                left = Logical(span=left.span, op=op, left=left, right=right)
            elif op == "..":
                left = RangeExpr(span=left.span, start=left, stop=right)
            else:
                left = Binary(span=left.span, op=op, left=left, right=right)

        return left

    def _binary_op(self, tok: Token) -> tuple[str | None, int, int]:
        if tok.kind is Kind.OP and tok.value in BINARY_BP:
            lbp, rbp = BINARY_BP[tok.value]
            return tok.value, lbp, rbp
        if tok.kind is Kind.KEYWORD:
            v = tok.value
            if v in ("and", "or"):
                return v, *BINARY_BP[v]
            if v == "in":
                return "in", *BINARY_BP["in"]
            if v == "not_in":
                return "not_in", *BINARY_BP["not_in"]
            if v == "is":
                # 表达式中间位置的 `是` → 相等比较（SPEC §4.3）
                return "==", *BINARY_BP["=="]
        return None, 0, 0

    def _parse_prefix(self) -> Node | None:
        tok = self._cur()

        if tok.kind is Kind.OP:
            if tok.value == "-":
                self._advance()
                return Unary(span=tok.span, op="-", operand=self._parse_expression(80))
            if tok.value == "+":
                self._advance()
                return Unary(span=tok.span, op="+", operand=self._parse_expression(80))
            if tok.value == "!":
                self._advance()
                return Unary(span=tok.span, op="not", operand=self._parse_expression(80))
            if tok.value == "(":
                self._advance()
                expr = self._parse_expression()
                self._expect(")", "括号未闭合，缺少 )")
                return expr
            if tok.value == "[":
                return self._parse_list_literal()
            if tok.value == "{":
                return self._parse_map_literal()
            if tok.value == "...":
                self._advance()
                return self._parse_expression(80)

        if tok.kind is Kind.KEYWORD:
            v = tok.value
            if v == "not":
                self._advance()
                return Unary(span=tok.span, op="not", operand=self._parse_expression(80))
            if v == "true":
                self._advance()
                return Literal(span=tok.span, value=True, literal_kind="bool")
            if v == "false":
                self._advance()
                return Literal(span=tok.span, value=False, literal_kind="bool")
            if v == "nil":
                self._advance()
                return Literal(span=tok.span, value=None, literal_kind="nil")
            if v == "fn":
                return self._parse_function_expr()

        if tok.kind is Kind.NUMBER:
            self._advance()
            text = tok.value
            value: int | float = float(text) if ("." in text or "e" in text.lower()) else int(text)
            return Literal(span=tok.span, value=value, literal_kind="number")

        if tok.kind is Kind.STRING:
            self._advance()
            if tok.parts and any(p[0] == "expr" for p in tok.parts):
                return self._parse_interpolated(tok)
            return Literal(span=tok.span, value=tok.value, literal_kind="text")

        if tok.kind is Kind.IDENT:
            self._advance()
            return Identifier(span=tok.span, name=tok.value)

        self._error(E_EXPECTED_EXPRESSION,
                    f"这里需要一个表达式，但看到了「{tok.text}」。", tok.span,
                    ["检查是否漏写了操作数，或误用了不支持的写法"])
        self._advance()
        return None

    def _parse_interpolated(self, tok: Token) -> Node:
        """把插值字符串的表达式片段就地重新词法/语法分析。"""
        parts: list = []
        for kind, content in tok.parts:
            if kind == "text":
                parts.append(("text", content))
            else:
                sub_tokens = Lexer(content, self.filename).tokenize()
                sub_parser = Parser(sub_tokens, self.filename)
                try:
                    expr = sub_parser._parse_expression()
                except ParseError as exc:
                    self.diagnostics.extend(exc.diagnostics)
                    expr = Literal(span=tok.span, value=None, literal_kind="nil")
                parts.append(("expr", expr))
        return InterpolatedString(span=tok.span, parts=parts)

    def _parse_call(self, callee: Node) -> Node:
        self._advance()                  # (
        args: list[Node] = []
        kwargs: dict[str, Node] = {}
        self._skip_newlines()
        unclosed = False
        while not self._check(")") and not self._check_kind(Kind.EOF):
            # **参数位置出现「语句起始关键字」⇒ 多半是上一行漏了右括号。**
            #
            # 多行参数列表本身合法（样例工程里就有 `造流水(...多行...)`），
            # 故不能一律禁止跨行。但 `定义`/`如果`/`遍历` 这类关键字
            # **永远不能作表达式前缀**，它们出现在参数位置只能是
            # 「括号没闭合」。若不拦，参数列表会**吞掉下一整条语句**，
            # 把一条错误放大成两三条（实测症状），且报错位置全错。
            if self._at_stmt_start():
                unclosed = True
                break
            # 具名参数：标识符 `是` 值
            if (self._at().kind is Kind.IDENT and self._at(1).value == "is"
                    and self._at(2).value != "is"):
                name = self._advance().value
                self._advance()          # is
                kwargs[name] = self._parse_expression()
            else:
                args.append(self._parse_expression())
            self._skip_newlines()
            # 参数后面多出一个值记号（如 `输出(2 的 10)`）—— 明确报错，
            # 避免「少算了参数却浑然不知」。
            nxt = self._cur()
            if (self._starts_value()
                    and nxt.value not in (",", ")")):
                self._error(E_UNEXPECTED_TOKEN,
                            f"函数参数「{nxt.text}」两侧没有运算符。",
                            nxt.span,
                            ["检查是否漏写运算符",
                             "中文的「的」「之」等字不是运算符"])
                while not self._check(",") and not self._check(")") \
                        and not self._check_kind(Kind.EOF):
                    self._advance()
            if not self._match(","):
                break
            self._skip_newlines()
        if unclosed:
            if not self._suppress_followup:
                self._error(E_EXPECTED_TOKEN,
                            "函数调用的参数列表未闭合，缺少 )",
                            self._cur().span,
                            ["检查这一行是否漏写了右括号 )",
                             "若参数要写多行，续行不要以 `定义` 等语句关键字开头"])
        elif not self._suppress_followup:
            self._expect(")", "函数调用的参数列表未闭合，缺少 )")
        return Call(span=callee.span, callee=callee, args=args, kwargs=kwargs)

    def _at_stmt_start(self) -> bool:
        """当前记号是否是「语句起始关键字」。

        这类关键字**不能作表达式前缀**（唯一的例外是 `fn`，
        它既开启函数定义、也能作匿名函数表达式，故排除）。
        参数列表 / 索引 / 字面量里遇到它们，即意味着括号未闭合。
        """
        tok = self._cur()
        return (tok.kind is Kind.KEYWORD
                and tok.value in STMT_STARTERS
                and tok.value != "fn")

    def _parse_index(self, target: Node) -> Node:
        start = self._advance()          # [
        if self._check(":"):
            self._advance()
            stop = None if self._check("]") else self._parse_expression()
            self._expect("]")
            return Index(span=start.span, target=target, is_slice=True,
                         start=None, stop=stop)
        first = self._parse_expression()
        if self._check(":"):
            self._advance()
            stop = None if self._check("]") else self._parse_expression()
            self._expect("]")
            return Index(span=start.span, target=target, is_slice=True,
                         start=first, stop=stop)
        self._expect("]", "下标未闭合，缺少 ]")
        return Index(span=start.span, target=target, index=first)

    def _parse_list_literal(self) -> Node:
        start = self._advance()          # [
        items = []
        self._skip_newlines()
        while not self._check("]") and not self._check_kind(Kind.EOF):
            items.append(self._parse_expression())
            self._skip_newlines()
            if not self._match(","):
                break
            self._skip_newlines()
        self._expect("]", "列表字面量未闭合，缺少 ]")
        return ListLiteral(span=start.span, items=items)

    def _parse_map_literal(self) -> Node:
        start = self._advance()          # {
        pairs = []
        self._skip_newlines()
        while not self._check("}") and not self._check_kind(Kind.EOF):
            key = self._parse_expression()
            self._expect(":", "映射的键值之间应该是冒号 :")
            value = self._parse_expression()
            pairs.append((key, value))
            self._skip_newlines()
            if not self._match(","):
                break
            self._skip_newlines()
        self._expect("}", "映射字面量未闭合，缺少 }")
        return MapLiteral(span=start.span, pairs=pairs)

    def _parse_function_expr(self) -> Node:
        start = self._advance()          # fn
        name = ""
        if self._cur().kind is Kind.IDENT and self._at(1).value == "(":
            name = self._advance().value
        params = []
        if self._match("("):
            params = self._parse_params()
            self._expect(")")
        body = self._parse_block(stop={"end"})
        self._expect_end("匿名函数", start.span)
        return FunctionExpr(span=start.span, params=params, body=body, name=name)


def parse(tokens: list[Token], filename: str = "<源码>") -> Program:
    return Parser(tokens, filename).parse()