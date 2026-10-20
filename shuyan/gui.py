"""枢言语言 —— 桌面工作台（图形界面）。

设计约束
--------
- **只用标准库 `tkinter`**：本项目承诺零第三方依赖，界面层也不例外。
  注意托管 Python 发行版可能不含 tkinter，启动器会挑一个可用的解释器
  （见 `shuyan/launcher.py`）。
- **六种操作全部走 `workbench.perform`**：界面层**不得**自行调用
  `Lexer` / `Parser` / `Interpreter`，否则与命令行必然分叉
  （项目铁律「同一语义两处实现必然分叉」）。
- **耗时的操作放后台线程**：ERP 套件跑一遍约两分钟，若在 UI 线程执行，
  窗口会整个卡住。完成后经 `after(0, ...)` 回主线程刷界面。

界面结构
--------
    ┌ 菜单栏 ────────────────────────────────────────┐
    ├ 工具栏（新建 打开 保存 │ 运行 检查 格式化 词法 语法树 测试 │ 主题）
    ├──────────┬──────────────────────┬─────────────┤
    │ 文件树    │ 编辑区（行号 + 高亮） │ 输出区（多标签）│
    ├──────────┴──────────────────────┴─────────────┤
    └ 状态栏（光标位置 · 选中摘要 · 操作结果）        ┘

关于「中文列号」
--------------
编辑器内部按**字符**索引（tkinter 的索引口径），而诊断里的列号是
**显示列**（汉字算 2 列）。两者不同，故定位报错行时只用**行号**，
不拿显示列去设光标列 —— 混用会让中文行的光标位置整段偏移。
"""

from __future__ import annotations

import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import __version__
from . import workbench as wb

# ------------------------------------------------------------------ 主题

THEMES: dict[str, dict[str, str]] = {
    "暖": {
        "bg": "#FBF8F3",          # 窗口底
        "panel": "#F3EFE8",       # 面板 / 工具栏
        "border": "#DFD8CC",
        "fg": "#2B2622",
        "muted": "#8A8378",
        "editor_bg": "#FFFFFF",
        "gutter_bg": "#F3EFE8",
        "gutter_fg": "#A79E90",
        "select": "#D8E8F5",
        "cursor": "#2B2622",
        "关键字": "#A6265C",
        "标识符": "#2B2622",
        "数字": "#1F6FB2",
        "文本": "#2E7D32",
        "运算符": "#7A5C2E",
        "注释": "#8A8378",
        "模块": "#6A4CA8",
        "内置函数": "#B5651D",
        "类型": "#0E7490",
        "普通": "#2B2622",
        "错误": "#C0392B",
        "警告": "#B7791F",
        "成功": "#2E7D32",
        "高亮行": "#F7F1E3",
    },
    "暗": {
        "bg": "#1E1F22",
        "panel": "#26282C",
        "border": "#37393E",
        "fg": "#E6E3DE",
        "muted": "#8B929E",
        "editor_bg": "#1B1C1F",
        "gutter_bg": "#26282C",
        "gutter_fg": "#6B7280",
        "select": "#2F4A63",
        "cursor": "#E6E3DE",
        "关键字": "#FF7BA9",
        "标识符": "#E6E3DE",
        "数字": "#79C0FF",
        "文本": "#A5D6A7",
        "运算符": "#D6C08A",
        "注释": "#7A828E",
        "模块": "#C4A7FF",
        "内置函数": "#FFB86B",
        "类型": "#7FDBCA",
        "普通": "#E6E3DE",
        "错误": "#FF6B6B",
        "警告": "#FFD166",
        "成功": "#7BE38A",
        "高亮行": "#24262A",
    },
}

# 高亮样式名 → 主题键（同名，集中在此便于核对两张表不漏键）
HIGHLIGHT_STYLES = ("关键字", "标识符", "数字", "文本", "运算符",
                    "注释", "模块", "内置函数", "类型", "普通")

UI_FONT = ("Microsoft YaHei UI", 9)
MONO_CANDIDATES = ("Cascadia Mono", "Consolas", "Courier New")
# 超过这个行数就不做高亮：全量重扫在大文件上会拖慢输入。
# 阈值偏小是刻意的 —— 宁可少一点颜色，也不要打字卡顿。
HIGHLIGHT_MAX_LINES = 3000


def _pick_mono() -> str:
    """挑一个系统里真实存在的等宽字体（不硬编码，避免机器差异）。"""
    try:
        import tkinter.font as tkfont
        fams = set(tkfont.families())
    except Exception:
        return "TkFixedFont"
    for name in MONO_CANDIDATES:
        if name in fams:
            return name
    return "TkFixedFont"


class Workbench:
    """枢言桌面工作台主窗口。"""

    def __init__(self, path: str | None = None):
        self.root = tk.Tk()
        self.root.title(f"枢言工作台 · v{__version__}")
        self.root.geometry("1280x820")
        self.root.minsize(900, 560)

        self.theme_name = "暖"
        self.theme = THEMES[self.theme_name]
        self.mono = _pick_mono()

        self.current_path: str | None = None
        self.dirty = False
        self.running = False             # 有后台任务在跑
        self._hl_job = None              # 高亮防抖句柄
        self._last_outcome = None

        self._setup_fonts()
        self._build_menu()
        self._build_toolbar()
        self._build_body()
        self._build_status()
        self._bind_keys()
        self._apply_theme()
        self._update_title()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        if path:
            self.open_file(path)
        else:
            self._load_sample()

    # ------------------------------------------------------------- 字体

    def _setup_fonts(self) -> None:
        """把默认字体换掉。

        tkinter 在 Windows 上的默认界面字体有时是「宋体」这类过时选择，
        尺寸也偏小。显式指定不仅好看，也让**汉字的行高**统一
        —— 中文源码的行高不一致会显得很脏。
        """
        import tkinter.font as tkfont
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            try:
                tkfont.nametofont(name).configure(family=UI_FONT[0],
                                                  size=UI_FONT[1])
            except Exception:
                pass
        self.code_font = tkfont.Font(family=self.mono, size=11)
        self.gutter_font = tkfont.Font(family=self.mono, size=11)
        self.out_font = tkfont.Font(family=self.mono, size=10)

    # ------------------------------------------------------------- 菜单

    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)

        m_file = tk.Menu(menubar, tearoff=0)
        m_file.add_command(label="新建", accelerator="Ctrl+N", command=self.new_file)
        m_file.add_command(label="打开…", accelerator="Ctrl+O", command=self.open_dialog)
        m_file.add_command(label="保存", accelerator="Ctrl+S", command=self.save_file)
        m_file.add_command(label="另存为…", command=self.save_as)
        m_file.add_separator()
        m_file.add_command(label="打开示例", command=self._open_example_menu)
        m_file.add_separator()
        m_file.add_command(label="退出", accelerator="Ctrl+Q", command=self._on_close)
        menubar.add_cascade(label="文件", menu=m_file)

        m_edit = tk.Menu(menubar, tearoff=0)
        m_edit.add_command(label="撤销", accelerator="Ctrl+Z",
                           command=lambda: self.editor.event_generate("<<Undo>>"))
        m_edit.add_command(label="重做", accelerator="Ctrl+Y",
                           command=lambda: self.editor.event_generate("<<Redo>>"))
        m_edit.add_separator()
        m_edit.add_command(label="全选", accelerator="Ctrl+A", command=self._select_all)
        m_edit.add_command(label="查找…", accelerator="Ctrl+F", command=self.find_dialog)
        m_edit.add_command(label="跳到行…", accelerator="Ctrl+G", command=self.goto_line)
        menubar.add_cascade(label="编辑", menu=m_edit)

        m_view = tk.Menu(menubar, tearoff=0)
        m_view.add_command(label="切换主题（暖 / 暗）", command=self.toggle_theme)
        m_view.add_separator()
        m_view.add_command(label="放大字号", accelerator="Ctrl++",
                           command=lambda: self._zoom(1))
        m_view.add_command(label="缩小字号", accelerator="Ctrl+-",
                           command=lambda: self._zoom(-1))
        menubar.add_cascade(label="视图", menu=m_view)

        m_run = tk.Menu(menubar, tearoff=0)
        m_run.add_command(label="运行", accelerator="F5", command=lambda: self.operate(wb.RUN))
        m_run.add_command(label="检查（不执行）", accelerator="F6",
                          command=lambda: self.operate(wb.CHECK))
        m_run.add_separator()
        m_run.add_command(label="格式化", command=lambda: self.operate(wb.FORMAT))
        m_run.add_command(label="查看记号流", command=lambda: self.operate(wb.LEX))
        m_run.add_command(label="查看语法树", command=lambda: self.operate(wb.AST))
        m_run.add_separator()
        m_run.add_command(label="运行内置自测", command=lambda: self.operate(wb.TEST))
        menubar.add_cascade(label="运行", menu=m_run)

        m_help = tk.Menu(menubar, tearoff=0)
        m_help.add_command(label="快捷键与说明", command=self.show_help)
        m_help.add_command(label="关于枢言", command=self.show_about)
        menubar.add_cascade(label="帮助", menu=m_help)

        self.root.configure(menu=menubar)

    # ------------------------------------------------------------- 工具栏

    def _build_toolbar(self) -> None:
        bar = tk.Frame(self.root, height=38)
        bar.pack(side="top", fill="x")
        self.toolbar = bar

        def btn(text, cmd, tip=""):
            b = tk.Button(bar, text=text, command=cmd, relief="flat",
                          padx=10, pady=4, cursor="hand2",
                          activebackground=self.theme["select"])
            b.pack(side="left", padx=2, pady=5)
            if tip:
                _Tooltip(b, tip)
            self._toolbar_buttons.append(b)
            return b

        self._toolbar_buttons: list[tk.Button] = []
        btn("新建", self.new_file, "新建源文件（Ctrl+N）")
        btn("打开", self.open_dialog, "打开 .sy 文件（Ctrl+O）")
        btn("保存", self.save_file, "保存（Ctrl+S）")
        tk.Frame(bar, width=1, bg=self.theme["border"]).pack(
            side="left", fill="y", padx=8, pady=8)
        self.btn_run = btn("▶ 运行", lambda: self.operate(wb.RUN),
                           "运行当前程序（F5）")
        btn("检查", lambda: self.operate(wb.CHECK), "静态检查，不执行（F6）")
        btn("格式化", lambda: self.operate(wb.FORMAT), "按规范重排缩进与空格")
        btn("词法", lambda: self.operate(wb.LEX), "查看记号流")
        btn("语法树", lambda: self.operate(wb.AST), "查看抽象语法树")
        btn("自测", lambda: self.operate(wb.TEST), "运行内置测试集")

        self.btn_theme = tk.Button(bar, text="主题", command=self.toggle_theme,
                                   relief="flat", padx=10, pady=4,
                                   cursor="hand2")
        self.btn_theme.pack(side="right", padx=6, pady=5)
        _Tooltip(self.btn_theme, "暖色 / 暗色 主题切换")

    # ------------------------------------------------------------- 主体

    def _build_body(self) -> None:
        outer = tk.PanedWindow(self.root, orient="horizontal",
                               sashwidth=5, bd=0)
        outer.pack(fill="both", expand=True, padx=6, pady=(0, 0))
        self.outer_pane = outer

        # ---- 左：文件树
        left = tk.Frame(outer, width=210)
        tk.Label(left, text="工程文件", anchor="w").pack(fill="x", padx=8, pady=(6, 2))
        self.tree = ttk.Treeview(left, show="tree", selectmode="browse")
        self.tree.pack(fill="both", expand=True, padx=4, pady=(0, 6))
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        outer.add(left, minsize=150, width=210)

        # ---- 中：编辑区（行号 gutter + 文本）
        mid = tk.Frame(outer)
        self.editor_wrap = mid
        self.gutter = tk.Text(mid, width=5, padx=6, takefocus=0,
                              relief="flat", state="disabled", wrap="none",
                              font=self.gutter_font)
        self.gutter.pack(side="left", fill="y")
        self.editor = tk.Text(mid, undo=True, maxundo=-1, wrap="none",
                              relief="flat", padx=8, pady=4,
                              insertwidth=2, font=self.code_font,
                              tabs=("1c",))
        vsb = ttk.Scrollbar(mid, orient="vertical", command=self._on_scroll)
        hsb = ttk.Scrollbar(mid, orient="horizontal", command=self.editor.xview)
        self.editor.configure(yscrollcommand=lambda a, b: self._sync_scroll(a, b, vsb),
                              xscrollcommand=hsb.set)
        outer.add(mid, minsize=380)
        self.editor.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")

        # ---- 右：输出区（多标签）
        right = tk.Frame(outer)
        self.right_pane = right
        self.tabs = ttk.Notebook(right)
        self.tabs.pack(fill="both", expand=True, padx=(4, 0), pady=(0, 0))
        self.out_texts: dict[str, tk.Text] = {}
        for name in ("输出", "诊断", "报告"):
            frame = tk.Frame(self.tabs)
            t = tk.Text(frame, wrap="word", relief="flat", padx=8, pady=6,
                        font=self.out_font, height=10)
            sb = ttk.Scrollbar(frame, orient="vertical", command=t.yview)
            t.configure(yscrollcommand=sb.set)
            t.pack(side="left", fill="both", expand=True)
            sb.pack(side="right", fill="y")
            t.configure(state="disabled")
            self.tabs.add(frame, text=name)
            self.out_texts[name] = t
        outer.add(right, minsize=280, width=430)

        # 输入区（`输入()` 需要标准输入；GUI 下由用户预填）
        stdin_frame = tk.Frame(self.root)
        stdin_frame.pack(fill="x", padx=6, pady=(2, 0))
        tk.Label(stdin_frame, text="标准输入（供 `输入()` 逐行读取，可留空）",
                 anchor="w").pack(side="left")
        self.stdin_entry = tk.Entry(stdin_frame)
        self.stdin_entry.pack(side="right", fill="x", expand=True, padx=(8, 0))

        self._setup_editor_tags()

    def _setup_editor_tags(self) -> None:
        """注册高亮用的文本标签（颜色在 `_apply_theme` 里统一刷）。"""
        for style in HIGHLIGHT_STYLES:
            self.editor.tag_configure(style)
        # 报错行底纹：诊断定位时横向整行标色
        self.editor.tag_configure("错误行", background=self.theme["高亮行"])
        self.editor.tag_configure("当前行", background=self.theme["高亮行"])
        # 文本框只读时也要看得清
        for t in self.out_texts.values():
            for style in ("错误", "警告", "成功", "普通"):
                t.tag_configure(style)

    # ------------------------------------------------------------- 状态栏

    def _build_status(self) -> None:
        bar = tk.Frame(self.root, height=26)
        bar.pack(side="bottom", fill="x")
        self.statusbar = bar
        self.lbl_pos = tk.Label(bar, text="第 1 行，第 1 列", anchor="w")
        self.lbl_pos.pack(side="left", padx=8)
        tk.Frame(bar, width=1, bg="#999").pack(side="left", fill="y", padx=4, pady=5)
        self.lbl_state = tk.Label(bar, text="就绪", anchor="w")
        self.lbl_state.pack(side="left", padx=4)
        self.lbl_path = tk.Label(bar, text="", anchor="e")
        self.lbl_path.pack(side="right", padx=8)

    def _bind_keys(self) -> None:
        e = self.editor
        e.bind("<KeyRelease>", self._on_key)
        e.bind("<ButtonRelease-1>", self._on_cursor)
        e.bind("<<Modified>>", self._on_modified)
        self.root.bind("<Control-n>", lambda ev: self.new_file())
        self.root.bind("<Control-o>", lambda ev: self.open_dialog())
        self.root.bind("<Control-s>", lambda ev: self.save_file())
        self.root.bind("<Control-q>", lambda ev: self._on_close())
        self.root.bind("<Control-f>", lambda ev: self.find_dialog())
        self.root.bind("<Control-g>", lambda ev: self.goto_line())
        self.root.bind("<F5>", lambda ev: self.operate(wb.RUN))
        self.root.bind("<F6>", lambda ev: self.operate(wb.CHECK))
        self.root.bind("<Control-plus>", lambda ev: self._zoom(1))
        self.root.bind("<Control-equal>", lambda ev: self._zoom(1))
        self.root.bind("<Control-minus>", lambda ev: self._zoom(-1))

    # ------------------------------------------------------------- 主题

    def _apply_theme(self) -> None:
        t = self.theme
        root = self.root
        root.configure(bg=t["bg"])
        for w in (self.toolbar, self.statusbar, self.editor_wrap,
                  self.right_pane, self.outer_pane):
            w.configure(bg=t["bg"] if w is not self.outer_pane else t["bg"])

        self.editor.configure(bg=t["editor_bg"], fg=t["fg"],
                              insertbackground=t["cursor"],
                              selectbackground=t["select"],
                              selectforeground=t["fg"])
        self.gutter.configure(bg=t["gutter_bg"], fg=t["gutter_fg"])
        self.stdout_style()
        self.stdin_entry.configure(bg=t["editor_bg"], fg=t["fg"],
                                   insertbackground=t["cursor"])

        self.lbl_pos.configure(bg=t["bg"], fg=t["muted"])
        self.lbl_state.configure(bg=t["bg"], fg=t["fg"])
        self.lbl_path.configure(bg=t["bg"], fg=t["muted"])

        for b in getattr(self, "_toolbar_buttons", []):
            b.configure(bg=t["panel"], fg=t["fg"],
                        activebackground=t["select"], activeforeground=t["fg"])
        self.btn_theme.configure(bg=t["panel"], fg=t["fg"],
                                 activebackground=t["select"])

        for style in HIGHLIGHT_STYLES:
            self.editor.tag_configure(style, foreground=t[style])
        self.editor.tag_configure("错误行", background=t["高亮行"])
        self.editor.tag_configure("当前行", background=t["高亮行"])
        for w in self.tree.winfo_children():
            pass

        # 输出区配色
        for name, text in self.out_texts.items():
            text.configure(bg=t["editor_bg"], fg=t["fg"],
                           insertbackground=t["cursor"],
                           selectbackground=t["select"])
            text.tag_configure("错误", foreground=t["错误"])
            text.tag_configure("警告", foreground=t["警告"])
            text.tag_configure("成功", foreground=t["成功"])
            text.tag_configure("普通", foreground=t["fg"])

        # ttk 控件跟随（Treeview / Notebook / Scrollbar）
        try:
            st = ttk.Style()
            st.theme_use("clam")
            st.configure("Treeview", background=t["editor_bg"],
                         fieldbackground=t["editor_bg"], foreground=t["fg"],
                         bordercolor=t["border"], lightcolor=t["panel"],
                         darkcolor=t["panel"])
            st.map("Treeview", background=[("selected", t["select"])],
                   foreground=[("selected", t["fg"])])
            st.configure("TNotebook", background=t["bg"], bordercolor=t["border"])
            st.configure("TNotebook.Tab", background=t["panel"],
                         foreground=t["fg"], padding=(12, 4))
            st.map("TNotebook.Tab",
                   background=[("selected", t["editor_bg"])])
            st.configure("TScrollbar", background=t["panel"],
                         troughcolor=t["bg"], bordercolor=t["border"],
                         arrowcolor=t["fg"])
        except Exception:
            pass

        self._refresh_highlight()

    def stdout_style(self) -> None:
        """输出区的等宽字体与行距（切主题时一并刷新）。"""

    def toggle_theme(self) -> None:
        self.theme_name = "暗" if self.theme_name == "暖" else "暖"
        self.theme = THEMES[self.theme_name]
        self._apply_theme()
        self._set_state(f"已切换到「{self.theme_name}色」主题")

    def _zoom(self, delta: int) -> None:
        for f in (self.code_font, self.gutter_font):
            size = max(8, min(24, f.cget("size") + delta))
            f.configure(size=size)
        self._set_state(f"字号 {self.code_font.cget('size')}")

    # ------------------------------------------------------------- 文件

    def _load_sample(self) -> None:
        """首次打开放一段短示例，避免面对空窗口无从下手。"""
        sample = (
            "# 欢迎使用枢言工作台\n"
            "# 按 F5 运行，F6 检查\n\n"
            "定义 问候 = \"你好，世界\"\n"
            "输出(问候)\n\n"
            "定义 数 = [1, 2, 3, 4, 5]\n"
            "定义 平方 = 映射列表(数, 函数(x) 返回 x * x 结束)\n"
            "输出(\"各数的平方：\" + 转文本(平方))\n"
        )
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", sample)
        self.current_path = None
        self._mark_clean()
        self._refresh_tree()

    def _refresh_tree(self) -> None:
        """重建左侧文件树：项目根 + examples + projects。"""
        for item in self.tree.get_children():
            self.tree.delete(item)
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self._tree_paths: dict[str, str] = {}

        def add_dir(parent, dirpath, depth=0):
            if depth > 3 or not os.path.isdir(dirpath):
                return
            try:
                names = sorted(os.listdir(dirpath))
            except OSError:
                return
            for name in names:
                if name.startswith(".") or name in ("__pycache__", "node_modules"):
                    continue
                full = os.path.join(dirpath, name)
                if os.path.isdir(full):
                    node = self.tree.insert(parent, "end", text="📁 " + name,
                                            open=(depth == 0))
                    add_dir(node, full, depth + 1)
                elif name.endswith(".sy"):
                    node = self.tree.insert(parent, "end", text="📄 " + name)
                    self._tree_paths[node] = full

        for sub in ("examples", "projects"):
            d = os.path.join(root_dir, sub)
            if os.path.isdir(d):
                node = self.tree.insert("", "end", text="📁 " + sub, open=True)
                add_dir(node, d, 1)

    def _on_tree_select(self, _evt) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        path = getattr(self, "_tree_paths", {}).get(sel[0])
        if path:
            self.open_file(path)

    def new_file(self) -> None:
        if not self._confirm_discard():
            return
        self.editor.delete("1.0", "end")
        self.current_path = None
        self._mark_clean()
        self._clear_outputs()
        self._set_state("已新建空文件")

    def open_dialog(self) -> None:
        if not self._confirm_discard():
            return
        path = filedialog.askopenfilename(
            title="打开枢言源文件",
            filetypes=[("枢言源码", "*.sy"), ("所有文件", "*.*")])
        if path:
            self.open_file(path)

    def _open_example_menu(self) -> None:
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        examples = os.path.join(root_dir, "examples")
        if not os.path.isdir(examples):
            messagebox.showinfo("提示", "未找到 examples 目录。")
            return
        names = sorted(n for n in os.listdir(examples) if n.endswith(".sy"))
        if not names:
            messagebox.showinfo("提示", "examples 目录里没有 .sy 文件。")
            return
        win = tk.Toplevel(self.root)
        win.title("打开示例")
        win.configure(bg=self.theme["bg"])
        tk.Label(win, text="选择一个示例：", bg=self.theme["bg"],
                 fg=self.theme["fg"]).pack(anchor="w", padx=12, pady=(10, 4))
        lb = tk.Listbox(win, width=44, height=min(14, len(names)),
                        bg=self.theme["editor_bg"], fg=self.theme["fg"],
                        relief="flat", highlightthickness=0)
        lb.pack(fill="both", expand=True, padx=12)
        for n in names:
            lb.insert("end", n)

        def choose(_e=None):
            sel = lb.curselection()
            if sel and self._confirm_discard():
                self.open_file(os.path.join(examples, names[sel[0]]))
                win.destroy()
        lb.bind("<Double-Button-1>", choose)
        lb.bind("<Return>", choose)
        tk.Button(win, text="打开", command=choose, relief="flat",
                  padx=14, pady=4).pack(pady=10)
        win.transient(self.root)

    def open_file(self, path: str) -> None:
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError as exc:
            messagebox.showerror("打不开文件", f"{path}\n{exc}")
            return
        except UnicodeDecodeError:
            messagebox.showerror(
                "编码不支持",
                f"{path}\n枢言源码必须是 UTF-8 编码。\n"
                f"请先转成 UTF-8 再打开。")
            return
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", content)
        self.current_path = os.path.abspath(path)
        self._mark_clean()
        self._clear_outputs()
        self._refresh_highlight()
        self._set_state(f"已打开 {os.path.basename(path)}")

    def save_file(self) -> bool:
        if not self.current_path:
            return self.save_as()
        return self._write(self.current_path)

    def save_as(self) -> bool:
        path = filedialog.asksaveasfilename(
            title="保存枢言源文件", defaultextension=".sy",
            filetypes=[("枢言源码", "*.sy"), ("所有文件", "*.*")])
        if not path:
            return False
        return self._write(path)

    def _write(self, path: str) -> bool:
        content = self.editor.get("1.0", "end-1c")
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(content if content.endswith("\n") else content + "\n")
        except OSError as exc:
            messagebox.showerror("保存失败", f"{path}\n{exc}")
            return False
        self.current_path = os.path.abspath(path)
        self._mark_clean()
        self._set_state(f"已保存 {os.path.basename(path)}")
        return True

    def _confirm_discard(self) -> bool:
        """有未保存改动时先问一声 —— 静默丢改动是最不能接受的。"""
        if not self.dirty:
            return True
        name = os.path.basename(self.current_path) if self.current_path else "未命名"
        ans = messagebox.askyesnocancel(
            "尚未保存", f"「{name}」有未保存的改动。\n是否先保存？")
        if ans is None:
            return False
        if ans:
            return self.save_file()
        return True

    def _on_close(self) -> None:
        if self.running:
            if not messagebox.askyesno("正在运行", "有任务正在运行，仍要退出？"):
                return
        if self._confirm_discard():
            self.root.destroy()

    # ------------------------------------------------------------- 编辑辅助

    def _select_all(self) -> None:
        self.editor.tag_add("sel", "1.0", "end-1c")
        self.editor.mark_set("insert", "1.0")

    def goto_line(self) -> None:
        win = _prompt(self.root, self.theme, "跳到行", "行号：")
        if not win:
            return
        try:
            n = int(win)
        except ValueError:
            self._set_state("行号必须是整数")
            return
        n = max(1, min(n, int(self.editor.index("end-1c").split(".")[0])))
        self.editor.mark_set("insert", f"{n}.0")
        self.editor.see(f"{n}.0")
        self.editor.focus_set()
        self._on_cursor()

    def find_dialog(self) -> None:
        win = _prompt(self.root, self.theme, "查找", "查找内容：")
        if not win:
            return
        # 从当前光标处向后找，绕回开头再找一次
        start = self.editor.index("insert")
        pos = self.editor.search(win, start, stopindex="end")
        if not pos:
            pos = self.editor.search(win, "1.0", stopindex="end")
        if not pos:
            self._set_state(f"未找到「{win}」")
            return
        end = f"{pos}+{len(win)}c"
        self.editor.tag_remove("sel", "1.0", "end")
        self.editor.tag_add("sel", pos, end)
        self.editor.mark_set("insert", pos)
        self.editor.see(pos)
        self._set_state(f"找到「{win}」于 {pos}")

    def _on_key(self, evt=None) -> None:
        # 高亮防抖：连续输入时只在停手后扫一次，避免打字卡顿
        if self._hl_job:
            self.root.after_cancel(self._hl_job)
        self._hl_job = self.root.after(180, self._refresh_highlight)
        self._on_cursor()

    def _on_modified(self, _evt=None) -> None:
        try:
            if self.editor.edit_modified():
                self.dirty = True
                self._update_title()
                self.editor.edit_modified(False)
        except tk.TclError:
            pass

    def _on_cursor(self, _evt=None) -> None:
        try:
            line, col = self.editor.index("insert").split(".")
            self.lbl_pos.configure(text=f"第 {int(line)} 行，第 {int(col) + 1} 列")
        except Exception:
            pass

    def _mark_clean(self) -> None:
        self.dirty = False
        try:
            self.editor.edit_modified(False)
        except tk.TclError:
            pass
        self._update_title()
        self._refresh_highlight()

    def _update_title(self) -> None:
        name = os.path.basename(self.current_path) if self.current_path else "未命名"
        star = " •" if self.dirty else ""
        self.root.title(f"枢言工作台 · {name}{star} · v{__version__}")
        self.lbl_path.configure(
            text=self.current_path or "（未保存）")

    # ------------------------------------------------------- 滚动 / 行号

    def _on_scroll(self, *args) -> None:
        self.editor.yview(*args)
        self.gutter.yview(*args)

    def _sync_scroll(self, a, b, vsb) -> None:
        vsb.set(a, b)
        self.gutter.yview_moveto(a)

    def _refresh_gutter(self) -> None:
        """重画行号。

        行号栏是**独立**的 Text 组件，内容只有数字 —— 用等宽字体对齐，
        不受中文宽度影响。若把行号混在正文里（很多简易编辑器这么做），
        中文行的行号就会错位。
        """
        total = int(self.editor.index("end-1c").split(".")[0])
        width = max(3, len(str(total)))
        self.gutter.configure(width=width + 2)
        self.gutter.configure(state="normal")
        self.gutter.delete("1.0", "end")
        self.gutter.insert("1.0", "\n".join(str(i) for i in range(1, total + 1)))
        self.gutter.configure(state="disabled")
        self.gutter.yview_moveto(self.editor.yview()[0])

    # ------------------------------------------------------------- 高亮

    def _refresh_highlight(self) -> None:
        self._hl_job = None
        self._refresh_gutter()
        for style in HIGHLIGHT_STYLES:
            self.editor.tag_remove(style, "1.0", "end")
        self.editor.tag_remove("错误行", "1.0", "end")

        source = self.editor.get("1.0", "end-1c")
        lines = source.count("\n") + 1
        if lines > HIGHLIGHT_MAX_LINES:
            self.lbl_state.configure(
                text=f"文件较大（{lines} 行），已暂停语法着色以保证输入流畅")
            return

        path = self.current_path or "<未保存>"
        for (line, start, end, style) in wb.highlight_ranges(source, path):
            self.editor.tag_add(style, f"{line}.{start}", f"{line}.{end}")

    # ------------------------------------------------------------- 输出

    def _clear_outputs(self) -> None:
        for t in self.out_texts.values():
            t.configure(state="normal")
            t.delete("1.0", "end")
            t.configure(state="disabled")

    def _write_out(self, tab: str, text: str, tag: str = "普通") -> None:
        t = self.out_texts[tab]
        t.configure(state="normal")
        t.insert("end", text, tag)
        t.see("end")
        t.configure(state="disabled")

    def _show_tab(self, tab: str) -> None:
        idx = list(self.out_texts).index(tab)
        self.tabs.select(idx)

    def _set_state(self, msg: str, kind: str = "普通") -> None:
        color = {"普通": self.theme["fg"], "成功": self.theme["成功"],
                 "警告": self.theme["警告"], "错误": self.theme["错误"]}[kind]
        self.lbl_state.configure(text=msg, fg=color)

    # ------------------------------------------------------------- 六操作

    def operate(self, operation: str) -> None:
        """执行一种工作台操作。**运行与自测放后台线程**，其余即时。

        为什么只有这两类要后台：运行可能耗时数分钟（ERP 套件约两分钟），
        自测要跑六十项 —— 在 UI 线程里做，窗口会白屏假死。
        检查 / 格式化 / 词法 / 语法树都是毫秒级，直接做更简单，
        也免去「结果到达时用户已改了源码」的困惑。
        """
        if self.running:
            self._set_state("有任务正在运行，请稍候", "警告")
            return
        source = self.editor.get("1.0", "end-1c")
        path = self.current_path or "<未保存>"

        if operation in (wb.RUN, wb.TEST):
            if operation == wb.RUN and not self.current_path \
                    and not self._ensure_saved_for_run():
                return
            self._start_background(operation, source, path)
            return

        self._clear_outputs()
        try:
            outcome = wb.perform(operation, source, path)
        except Exception as exc:                     # noqa: BLE001
            self._report_internal(exc)
            return
        self._present(outcome, source)

    def _ensure_saved_for_run(self) -> bool:
        """运行一个未保存的文件时，先落盘。

        因为 `引入 "模块"` 的路径基准是**主脚本所在目录**（SPEC §3.8.1）。
        未保存时没有目录可言，模块解析必然对不上 —— 与其让用户看到
        「找不到模块」，不如先请他保存。
        """
        return messagebox.askyesno(
            "尚未保存",
            "当前文件还没有保存。\n\n"
            "运行时，引入模块的查找基准是主脚本所在目录，"
            "未保存的文件没有目录 —— 请先保存再运行。\n\n现在保存吗？"
        ) and self.save_file()

    def _start_background(self, operation: str, source: str, path: str) -> None:
        self.running = True
        self._set_state(f"{operation}中…")
        self.btn_run.configure(state="disabled", text="运行中…")
        self._clear_outputs()

        # 标准输入换成一个能把输入区内容逐行喂进去的流。
        # `输入()` 内部用的是 `sys.stdin.readline()`（宿主全局），
        # 与 StdLib 无关，故必须在**宿主层**替换 —— 这是唯一的接入点。
        feed = self.stdin_entry.get()
        old_stdin = sys.stdin
        sys.stdin = _FeedStdin(feed)

        def work():
            try:
                outcome = wb.perform(operation, source, path, capture=True)
            except Exception as exc:                 # noqa: BLE001
                self.root.after(0, lambda: self._report_internal(exc))
                return
            finally:
                sys.stdin = old_stdin
            self.root.after(0, lambda: self._present(outcome, source))

        threading.Thread(target=work, daemon=True).start()

    def _report_internal(self, exc: Exception) -> None:
        """宿主异常兜底。

        界面层若把 Python 堆栈直接展示给用户，等于诊断体系失效
        —— 但完全不显示又让人无从下手。折中：明说这是**工具自身**的
        问题（而非用户代码的问题），并附上异常类型与信息。
        """
        self.running = False
        self.btn_run.configure(state="normal", text="▶ 运行")
        self._show_tab("诊断")
        self._write_out("诊断",
                        f"工具内部错误：{type(exc).__name__}\n  {exc}\n\n"
                        f"这不是你的源码的问题。请把这段信息报告给工具维护者。\n",
                        "错误")
        self._set_state(f"工具内部错误：{type(exc).__name__}", "错误")

    def _present(self, outcome, source: str) -> None:
        """把操作结果刷到界面（**主线程**调用）。"""
        self.running = False
        self.btn_run.configure(state="normal", text="▶ 运行")
        self._last_outcome = outcome

        # 报告 / 词法 / 语法树 / 格式化 走「报告」页；运行与检查看输出与诊断
        if outcome.operation in (wb.LEX, wb.AST):
            self._show_tab("报告")
            self._write_out("报告", outcome.text or "（无内容）")
            self._set_state(f"{outcome.operation}完成", "成功" if outcome.ok else "错误")
        elif outcome.operation == wb.TEST:
            self._show_tab("报告")
            self._write_out("报告", outcome.text or "（无内容）",
                            "成功" if outcome.ok else "错误")
            self._set_state("自测完成", "成功" if outcome.ok else "错误")
        elif outcome.operation == wb.FORMAT:
            self.editor.delete("1.0", "end")
            self.editor.insert("1.0", outcome.text)
            self.dirty = True
            self._update_title()
            self._refresh_highlight()
            self._show_tab("报告")
            self._write_out("报告",
                            "已按规范格式化（编辑器内容已更新）。\n"
                            "如需撤销，按 Ctrl+Z；如需落盘，按 Ctrl+S。\n"
                            + ("\n提示：\n  " + "\n  ".join(
                                d.message for d in outcome.diagnostics)
                               if outcome.diagnostics else ""))
            self._set_state("格式化完成", "成功")
        else:
            # 运行 / 检查
            if outcome.text:
                self._show_tab("输出")
                self._write_out("输出", outcome.text)
            if outcome.diagnostics:
                self._show_tab("诊断" if not outcome.ok else "诊断")
                self._write_out("诊断", outcome.render(source),
                                "错误" if outcome.errors else "警告")
            if outcome.ok:
                self._set_state(
                    f"{outcome.operation}完成"
                    + (f"（{len(outcome.warnings)} 条提醒）"
                       if outcome.warnings else ""), "成功")
            else:
                self._set_state(
                    f"{outcome.operation}失败：{len(outcome.errors)} 个错误", "错误")
                self._highlight_error_lines(outcome)

    def _highlight_error_lines(self, outcome) -> None:
        """把报错所在行标上底纹。

        只用**行号**定位，不拿诊断里的**显示列**设光标列 —— 两者口径不同
        （显示列把汉字算 2 列，编辑器按字符数），混用会让中文行的光标偏位。
        """
        self.editor.tag_remove("错误行", "1.0", "end")
        total = int(self.editor.index("end-1c").split(".")[0])
        for d in outcome.errors:
            ln = d.span.line
            if 1 <= ln <= total:
                self.editor.tag_add("错误行", f"{ln}.0", f"{ln}.end")
        if outcome.errors:
            first = outcome.errors[0].span.line
            if 1 <= first <= total:
                self.editor.see(f"{first}.0")

    # ------------------------------------------------------------- 帮助

    def show_help(self) -> None:
        text = (
            "快捷键\n"
            "  Ctrl+N  新建        Ctrl+O  打开        Ctrl+S  保存\n"
            "  Ctrl+Z  撤销        Ctrl+A  全选        Ctrl+F  查找\n"
            "  Ctrl+G  跳到行      Ctrl+Q  退出\n"
            "  F5      运行        F6      检查\n"
            "  Ctrl+加号 / 减号    字号缩放\n\n"
            "工具栏\n"
            "  运行    执行当前源码，输出落在「输出」页\n"
            "  检查    静态检查（语义 + 代码提醒），不执行\n"
            "  格式化  按规范重排缩进与空格（改编辑器内容，Ctrl+S 才落盘）\n"
            "  词法    查看记号流（调试词法层）\n"
            "  语法树  查看抽象语法树（调试语法层）\n"
            "  自测    运行编译器内置的测试集\n\n"
            "标准输入\n"
            "  源码里用到 `输入()` 时，先在最下方的输入框里按行填写，\n"
            "  运行时会逐行喂给程序。\n\n"
            "提示\n"
            "  打开多文件工程时，用左侧文件树直接点选；运行前请先保存，\n"
            "  因为 `引入` 的查找基准是主脚本所在目录。\n"
        )
        _text_window(self.root, self.theme, "快捷键与说明", text, self.mono)

    def show_about(self) -> None:
        text = (
            f"枢言（Shuyan）—— 中文原生编程语言\n"
            f"版本 {__version__}\n\n"
            "纯 Python 标准库实现，不依赖任何第三方库、语法文件\n"
            "或代码生成器。\n\n"
            "双端口语 / 书面语归一为同一棵语法树；\n"
            "诊断码分四档：D1 词法 / D2 语法 / D3 语义 / D4 运行时，\n"
            "警告一律称「代码提醒」。\n\n"
            "本工作台把命令行工具链的六种操作收进图形界面，\n"
            "与 `sy` 命令共用同一套实现。\n"
        )
        _text_window(self.root, self.theme, "关于枢言", text, self.mono,
                     width=62, height=16)

    # ------------------------------------------------------------- 入口

    def run(self) -> int:
        self.root.mainloop()
        return 0


# ------------------------------------------------------------------ 小部件

class _FeedStdin:
    """把一段文本当作标准输入，供 `输入()` 逐行读取。

    只需实现 `readline`：枢言内置的 `输入()` 只调这一个方法。
    刻意**不**实现完整的文件接口 —— 少实现一点，就少一处可能与
    `io` 行为不一致的地方。
    """

    def __init__(self, text: str):
        self._lines = text.splitlines(keepends=True) if text else []
        self._i = 0

    def readline(self, *_a) -> str:
        if self._i < len(self._lines):
            line = self._lines[self._i]
            self._i += 1
            return line if line.endswith("\n") else line + "\n"
        return ""

    def read(self, *_a) -> str:
        rest = "".join(self._lines[self._i:])
        self._i = len(self._lines)
        return rest

    def isatty(self) -> bool:
        return False


class _Tooltip:
    """悬停提示。tkinter 没有内建 tooltip，这里给一个极简实现。"""

    def __init__(self, widget, text: str, delay: int = 500):
        self.widget = widget
        self.text = text
        self.delay = delay
        self._id = None
        self._win = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<Button>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._cancel()
        self._id = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self._id:
            self.widget.after_cancel(self._id)
            self._id = None

    def _show(self):
        if self._win or not self.text:
            return
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self._win = tk.Toplevel(self.widget)
        self._win.wm_overrideredirect(True)
        self._win.wm_geometry(f"+{x}+{y}")
        tk.Label(self._win, text=self.text, justify="left",
                 bg="#33383F", fg="#F2F2F2", relief="solid", bd=0,
                 padx=8, pady=4).pack()

    def _hide(self, _e=None):
        self._cancel()
        if self._win:
            self._win.destroy()
            self._win = None


def _prompt(parent, theme, title: str, label: str) -> str | None:
    """单行输入对话框。返回输入内容；取消返回 None。"""
    win = tk.Toplevel(parent)
    win.title(title)
    win.configure(bg=theme["bg"])
    win.transient(parent)
    tk.Label(win, text=label, bg=theme["bg"], fg=theme["fg"]).pack(
        anchor="w", padx=14, pady=(12, 4))
    entry = tk.Entry(win, width=34, bg=theme["editor_bg"], fg=theme["fg"],
                     insertbackground=theme["cursor"], relief="flat")
    entry.pack(padx=14)
    entry.focus_set()
    result: dict[str, str | None] = {"v": None}

    def ok(_e=None):
        result["v"] = entry.get()
        win.destroy()

    def cancel(_e=None):
        result["v"] = None
        win.destroy()

    row = tk.Frame(win, bg=theme["bg"])
    row.pack(pady=12)
    tk.Button(row, text="确定", command=ok, relief="flat", padx=14).pack(
        side="left", padx=4)
    tk.Button(row, text="取消", command=cancel, relief="flat", padx=14).pack(
        side="left", padx=4)
    win.bind("<Return>", ok)
    win.bind("<Escape>", cancel)
    win.grab_set()
    parent.wait_window(win)
    return result["v"]


def _text_window(parent, theme, title: str, text: str, mono: str,
                 width: int = 66, height: int = 24) -> None:
    """只读文本窗口（帮助 / 关于）。"""
    win = tk.Toplevel(parent)
    win.title(title)
    win.configure(bg=theme["bg"])
    win.transient(parent)
    t = tk.Text(win, wrap="word", width=width, height=height, relief="flat",
                bg=theme["editor_bg"], fg=theme["fg"], padx=12, pady=10,
                font=(mono, 10))
    sb = ttk.Scrollbar(win, orient="vertical", command=t.yview)
    t.configure(yscrollcommand=sb.set)
    t.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")
    t.insert("1.0", text)
    t.configure(state="disabled")


def main(argv: list[str] | None = None) -> int:
    """图形界面入口。`sy 界面 [文件]` 调到这里。"""
    argv = list(sys.argv[1:] if argv is None else argv)
    path = None
    for a in argv:
        if not a.startswith("-") and os.path.exists(a):
            path = a
            break
    try:
        import tkinter  # noqa: F401
    except ImportError:
        print("当前 Python 没有 tkinter，无法启动图形界面。\n"
              "请改用带 tkinter 的 Python（Windows 官方版自带）。\n"
              "命令行工具仍可用：sy 运行 / 检查 / 格式化 …",
              file=sys.stderr)
        return 2
    app = Workbench(path)
    return app.run()


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.exit(main())