"""SchLib 多显示模式符号的检查、引脚编辑和模式排序窗口。"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox, ttk

import draw as draw_mod
from issues import check_symbol
from library import Library, LibraryError, load_library, schlib_files
from ole_storage import OleStorageError
from records import PinInfo, SchlibFormatError, Symbol, mode_letter
from version import VERSION

APP_TITLE = f"Altium_Schlib_Symbol_Editor {VERSION}"


class EditorApp(tk.Tk):
    """打开一个 SchLib，只列出有多种显示模式的符号。"""

    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1280x820")
        self.minsize(1020, 680)
        self._apply_font()
        self.libraries: list[Library] = []
        self._symbols: dict[str, Symbol] = {}
        self._pin_rows: dict[str, list[PinInfo]] = {}
        self._issue_owner: dict[str, str] = {}
        self._file_column = False
        self.issues: dict[str, list] = {}
        self._mode_baselines: dict[str, object] = {}
        self._current_iid: str | None = None
        self._loading = False
        self._pin_name_width = 0
        self._pin_name_resized = False
        self._syncing_pin_widths = False
        self._heading_font: tkfont.Font | None = None
        self.path_var = tk.StringVar(value="尚未打开库文件")
        self.summary_var = tk.StringVar(value="")
        self._status_text = "打开一个 SchLib 或文件夹。左侧只列出有多种显示模式的符号。"
        self.status_var = tk.StringVar(value=self._status_text)
        self.filter_var = tk.StringVar()
        self.only_current_var = tk.BooleanVar(value=True)
        self.only_issues_var = tk.BooleanVar(value=False)
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Control-o>", lambda _event: self.open_library())
        self.bind("<Control-s>", lambda _event: self.save_library())

    def _apply_font(self) -> None:
        style = ttk.Style(self)
        style.configure(".", font=("Microsoft YaHei UI", 10))
        style.configure("Treeview", font=("Microsoft YaHei UI", 10), rowheight=24)
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 10))

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        bar = ttk.Frame(self, padding=(8, 8, 8, 4))
        bar.grid(row=0, column=0, sticky="ew")
        ttk.Button(bar, text="打开 SchLib", command=self.open_library).pack(side="left")
        ttk.Button(bar, text="打开文件夹", command=self.open_folder).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="保存", command=self.save_library).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="关闭文件", command=self.close_library).pack(side="left", padx=(6, 0))
        ttk.Label(bar, textvariable=self.path_var).pack(side="left", padx=12)

        paned = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        paned.grid(row=1, column=0, sticky="nsew", padx=8, pady=4)
        paned.add(self._build_symbol_pane(paned), weight=2)
        paned.add(self._build_notebook(paned), weight=5)
        ttk.Label(self, textvariable=self.status_var, padding=(8, 4)).grid(row=2, column=0, sticky="ew")

    def _build_symbol_pane(self, parent: ttk.Panedwindow) -> ttk.Frame:
        pane = ttk.Frame(parent, padding=(0, 0, 8, 0))
        pane.columnconfigure(0, weight=1)
        pane.rowconfigure(3, weight=1)
        ttk.Label(pane, text="多种显示模式的符号").grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(
            pane,
            text="仅显示有问题的符号",
            variable=self.only_issues_var,
            command=self._fill_symbols,
        ).grid(row=1, column=0, sticky="w")
        filter_row = ttk.Frame(pane)
        filter_row.grid(row=2, column=0, sticky="ew", pady=4)
        filter_row.columnconfigure(1, weight=1)
        ttk.Label(filter_row, text="筛选").grid(row=0, column=0, sticky="w")
        entry = ttk.Entry(filter_row, textvariable=self.filter_var)
        entry.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        self.filter_var.trace_add("write", lambda *_args: self._fill_symbols())
        self.symbol_tree = ttk.Treeview(
            pane,
            columns=("name", "modes", "parts", "issues"),
            show="headings",
            selectmode="browse",
        )
        self.symbol_tree.heading("name", text="符号")
        self.symbol_tree.heading("modes", text="模式")
        self.symbol_tree.heading("parts", text="Part")
        self.symbol_tree.heading("issues", text="问题")
        self.symbol_tree.column("name", width=180, anchor="w")
        self.symbol_tree.column("modes", width=48, anchor="center", stretch=False)
        self.symbol_tree.column("parts", width=48, anchor="center", stretch=False)
        self.symbol_tree.column("issues", width=48, anchor="center", stretch=False)
        self.symbol_tree.tag_configure("bad", foreground="#a32020")
        self.symbol_tree.grid(row=3, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(pane, orient="vertical", command=self.symbol_tree.yview)
        scroll.grid(row=3, column=1, sticky="ns")
        self.symbol_tree.configure(yscrollcommand=scroll.set)
        self.symbol_tree.bind("<<TreeviewSelect>>", self._on_symbol_event)
        return pane

    def _build_notebook(self, parent: ttk.Panedwindow) -> ttk.Notebook:
        self.notebook = ttk.Notebook(parent)
        self.check_tab = ttk.Frame(self.notebook, padding=8)
        self.edit_tab = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(self.check_tab, text="引脚检查")
        self.notebook.add(self.edit_tab, text="编辑")
        self._build_check_tab()
        self._build_edit_tab()
        self.notebook.select(self.edit_tab)
        return self.notebook

    def _build_check_tab(self) -> None:
        self.check_tab.columnconfigure(0, weight=1)
        self.check_tab.rowconfigure(1, weight=1)
        ttk.Checkbutton(
            self.check_tab,
            text="只看当前符号",
            variable=self.only_current_var,
            command=self._fill_issue_view,
        ).grid(row=0, column=0, sticky="w")
        self.issue_tree = ttk.Treeview(
            self.check_tab,
            columns=("symbol", "mode", "kind", "message"),
            show="headings",
            selectmode="browse",
        )
        for key, title, width, stretch in (
            ("symbol", "符号", 180, True),
            ("mode", "模式", 60, False),
            ("kind", "类别", 88, False),
            ("message", "说明", 480, True),
        ):
            self.issue_tree.heading(key, text=title)
            self.issue_tree.column(key, width=width, anchor="w", stretch=stretch)
        self.issue_tree.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        scroll = ttk.Scrollbar(self.check_tab, orient="vertical", command=self.issue_tree.yview)
        scroll.grid(row=1, column=1, sticky="ns", pady=(6, 0))
        self.issue_tree.configure(yscrollcommand=scroll.set)
        self.issue_tree.bind("<Double-1>", self._open_issue)

    def _build_edit_tab(self) -> None:
        self.edit_tab.columnconfigure(0, weight=1)
        self.edit_tab.rowconfigure(1, weight=1)
        ttk.Label(
            self.edit_tab,
            text="右键编辑名称，默认应用到全部显示模式。名称不一致的行显示为红色。在模式标题上右键可以左移、右移、删除或恢复。",
        ).grid(row=0, column=0, sticky="w")
        middle = ttk.Panedwindow(self.edit_tab, orient=tk.HORIZONTAL)
        middle.grid(row=1, column=0, sticky="nsew", pady=6)
        self._edit_split = middle
        pin_pane = ttk.Frame(middle)
        pin_pane.columnconfigure(0, weight=1)
        pin_pane.rowconfigure(0, weight=1)
        self.pin_tree = ttk.Treeview(pin_pane, columns=("designator",), show="headings", selectmode="browse")
        self.pin_tree.heading("designator", text="位号")
        designator_width = self._heading_width("位号")
        self.pin_tree.column("designator", width=designator_width, minwidth=designator_width, stretch=False, anchor="w")
        self.pin_tree.tag_configure("mismatch", foreground="#a32020")
        self.pin_tree.grid(row=0, column=0, sticky="nsew")
        pin_scroll = ttk.Scrollbar(pin_pane, orient="vertical", command=self.pin_tree.yview)
        pin_scroll.grid(row=0, column=1, sticky="ns")
        self._pin_scroll = pin_scroll
        pin_xscroll = ttk.Scrollbar(pin_pane, orient="horizontal", command=self.pin_tree.xview)
        pin_xscroll.grid(row=1, column=0, sticky="ew")
        self.pin_tree.configure(yscrollcommand=pin_scroll.set, xscrollcommand=pin_xscroll.set)
        self.pin_tree.bind("<<TreeviewSelect>>", lambda _event: self._redraw(self._selected_symbol()))
        self.pin_tree.bind("<Button-3>", self._on_pin_tree_right_click)
        self._pin_menu: tk.Toplevel | None = None
        self._popup_token: object | None = None
        self.pin_tree.bind("<B1-Motion>", self._sync_pin_column_widths)
        self.pin_tree.bind("<ButtonRelease-1>", self._sync_pin_column_widths)
        middle.add(pin_pane, weight=0)
        preview = ttk.Frame(middle)
        preview.columnconfigure(0, weight=1)
        preview.rowconfigure(0, weight=1)
        self.edit_preview = self._make_preview(preview)
        middle.add(preview, weight=1)

    def _make_preview(self, parent: ttk.Frame):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        if not draw_mod.HAS_MATPLOTLIB:
            label = ttk.Label(parent, text="未安装 matplotlib，不能预览符号。检查和编辑仍可使用。")
            label.grid(row=0, column=0, sticky="nsew")
            return None
        figure = draw_mod.ScrollFigure(parent)
        figure.grid(row=0, column=0, sticky="nsew")
        return figure

    def open_library(self) -> None:
        if not self._confirm_discard():
            return
        chosen = filedialog.askopenfilename(
            parent=self,
            title="打开 SchLib",
            filetypes=[("Altium SchLib", "*.SchLib"), ("所有文件", "*.*")],
        )
        if not chosen:
            return
        self.load_library(Path(chosen))

    def open_folder(self) -> None:
        if not self._confirm_discard():
            return
        chosen = filedialog.askdirectory(parent=self, title="打开文件夹")
        if not chosen:
            return
        self.load_folder(Path(chosen))

    def close_library(self) -> None:
        if not self.libraries:
            self._set_status("还没有打开库文件")
            return
        had_changes = any(symbol.dirty for symbol in self._all_symbols())
        if not self._confirm_discard():
            return
        saved = had_changes and not any(symbol.dirty for symbol in self._all_symbols())
        self.libraries = []
        self._symbols = {}
        self._current_iid = None
        self.issues = {}
        self._mode_baselines = {}
        self.filter_var.set("")
        self.path_var.set("尚未打开库文件")
        self._fill_symbols()
        self._recheck()
        self._set_status("已保存并关闭" if saved else "已关闭")

    def load_folder(self, folder: Path) -> None:
        try:
            files = schlib_files(folder)
        except LibraryError as exc:
            messagebox.showerror("无法打开", str(exc), parent=self)
            return
        if not files:
            messagebox.showwarning("打开文件夹", "这个文件夹里没有 SchLib 文件。", parent=self)
            return
        self.configure(cursor="watch")
        libraries: list[Library] = []
        failed: list[str] = []
        try:
            for index, file in enumerate(files, start=1):
                self.status_var.set(f"正在读取 {index}/{len(files)} {file.name}")
                self.update_idletasks()
                try:
                    libraries.append(load_library(file))
                except (LibraryError, OSError) as exc:
                    failed.append(f"{file.name}: {exc}")
        finally:
            self.configure(cursor="")
        if not libraries:
            messagebox.showerror("无法打开", "\n".join(failed) or "没有读到 SchLib。", parent=self)
            return
        symbol_count = sum(len(library.symbols) for library in libraries)
        self._use_libraries(
            libraries,
            f"{folder}    {len(libraries)} 个 SchLib",
            "已打开文件夹",
        )
        skipped = [(library.path.name, name, err) for library in libraries for name, err in library.skipped]
        if failed or skipped or symbol_count == 0:
            lines: list[str] = []
            if symbol_count == 0:
                lines.append("没有找到具备多种显示模式的符号。")
            if failed:
                lines.append(f"有 {len(failed)} 个文件无法打开。")
                lines.extend(failed[:6])
            if skipped:
                lines.append(f"有 {len(skipped)} 个符号无法读取。")
                lines.extend(f"{file_name}/{name}: {err}" for file_name, name, err in skipped[:6])
            messagebox.showwarning("打开文件夹", "\n".join(lines), parent=self)

    def load_library(self, path: Path) -> None:
        self.status_var.set("正在读取库文件…")
        self.configure(cursor="watch")
        self.update_idletasks()

        def progress(index: int, total: int) -> None:
            self.status_var.set(f"正在读取 {index}/{total} …")
            self.update_idletasks()

        try:
            library = load_library(path, progress)
        except (LibraryError, OSError) as exc:
            self.configure(cursor="")
            messagebox.showerror("无法打开", str(exc), parent=self)
            return
        self.configure(cursor="")
        self._use_libraries([library], str(path), "已打开")
        if library.skipped or not library.symbols:
            lines: list[str] = []
            if not library.symbols:
                lines.append("没有找到具备多种显示模式的符号。")
            if library.skipped:
                lines.append(f"有 {len(library.skipped)} 个符号无法读取。")
                lines.extend(f"{name}: {err}" for name, err in library.skipped[:6])
            messagebox.showwarning("打开库", "\n".join(lines), parent=self)

    def save_library(self, *, confirm: bool = True) -> bool:
        dirty_libraries = [library for library in self.libraries if any(symbol.dirty for symbol in library.symbols)]
        if not self.libraries:
            self._set_status("还没有打开库文件")
            return False
        if not dirty_libraries:
            self._set_status("没有需要保存的修改")
            return True
        if confirm:
            lines = []
            for library in dirty_libraries:
                names = [symbol.name for symbol in library.symbols if symbol.dirty]
                lines.append(library.path.name)
                lines.extend(f"  {name}" for name in names[:8])
                if len(names) > 8:
                    lines.append(f"  …等 {len(names)} 个")
            agreed = messagebox.askyesno(
                "保存",
                "将写回原文件，并先在同目录的 History 文件夹里生成带时间戳的备份。\n"
                "请先关闭正在使用这些库的 Altium Designer。\n\n"
                + "\n".join(lines),
                parent=self,
            )
            if not agreed:
                return False
        backups: list[str] = []
        try:
            for library in dirty_libraries:
                backup = library.save()
                if backup is not None:
                    backups.append(str(Path("History") / backup.name))
        except (LibraryError, SchlibFormatError, OleStorageError, OSError) as exc:
            messagebox.showerror(
                "保存失败",
                f"{exc}\n\n如果已经生成备份，失败的那个文件已从备份恢复。窗口里的修改还在。",
                parent=self,
            )
            self._recheck()
            self._fill_symbols()
            return False
        self._recheck()
        self._fill_symbols()
        self._set_status(f"已保存 {len(backups)} 个库：" + "、".join(backups))
        return True

    def _use_libraries(self, libraries: list[Library], label: str, status: str) -> None:
        self.libraries = libraries
        self._mode_baselines = {}
        self._index_symbols()
        self._current_iid = None
        self.path_var.set(label)
        self._recheck()
        self._fill_symbols()
        self._set_status(status)

    def _index_symbols(self) -> None:
        self._symbols = {}
        for library in self.libraries:
            for symbol in library.symbols:
                iid = str(len(self._symbols))
                symbol.editor_iid = iid
                self._symbols[iid] = symbol

    def _all_symbols(self) -> list[Symbol]:
        return [symbol for library in self.libraries for symbol in library.symbols]

    def _library_for(self, symbol: Symbol) -> Library | None:
        for library in self.libraries:
            if any(item is symbol for item in library.symbols):
                return library
        return None

    def _confirm_discard(self) -> bool:
        if not any(symbol.dirty for symbol in self._all_symbols()):
            return True
        answer = messagebox.askyesnocancel("未保存的修改", "有修改还没有写入文件。要保存吗？", parent=self)
        if answer is None:
            return False
        if answer:
            return self.save_library(confirm=False)
        return True

    def _on_close(self) -> None:
        if self._confirm_discard():
            self.destroy()

    def _recheck(self) -> None:
        self.issues = {}
        for symbol in self._all_symbols():
            self.issues[symbol.editor_iid] = check_symbol(symbol)
            self._refresh_symbol_row(symbol)
        self._fill_issue_view()
        self._update_summary()

    def _visible_symbols(self) -> list[Symbol]:
        text = self.filter_var.get().strip().casefold()
        only_issues = self.only_issues_var.get()
        matched = []
        for symbol in self._all_symbols():
            if only_issues and not self.issues.get(symbol.editor_iid):
                continue
            if text:
                library = self._library_for(symbol)
                file_name = "" if library is None else library.path.name.casefold()
                if text not in symbol.name.casefold() and text not in file_name:
                    continue
            matched.append(symbol)
        return matched

    def _fill_symbols(self) -> None:
        if not self.symbol_tree.winfo_exists():
            return
        previous = self._selected_iid()
        self._loading = True
        try:
            self._configure_symbol_tree()
            self.symbol_tree.delete(*self.symbol_tree.get_children())
            for symbol in self._visible_symbols():
                self._insert_symbol_row(symbol)
            target = previous if previous and self.symbol_tree.exists(previous) else ""
            children = self.symbol_tree.get_children()
            if not target and children:
                target = children[0]
            if target:
                self.symbol_tree.selection_set(target)
                self.symbol_tree.focus(target)
                self.symbol_tree.see(target)
        finally:
            self._loading = False
        self._show_symbol()

    def _configure_symbol_tree(self) -> None:
        self._file_column = len(self.libraries) > 1
        columns = ["name", "modes", "parts", "issues"]
        if self._file_column:
            columns.insert(1, "file")
        self.symbol_tree["columns"] = columns
        headings = {
            "name": ("符号", 180, True, "w"),
            "file": ("文件", 160, True, "w"),
            "modes": ("模式", 48, False, "center"),
            "parts": ("Part", 48, False, "center"),
            "issues": ("问题", 48, False, "center"),
        }
        for key in columns:
            title, width, stretch, anchor = headings[key]
            self.symbol_tree.heading(key, text=title)
            self.symbol_tree.column(key, width=width, stretch=stretch, anchor=anchor)

    def _symbol_values(self, symbol: Symbol) -> tuple:
        issues = self.issues.get(getattr(symbol, "editor_iid", ""), [])
        label = symbol.name + (" *" if symbol.dirty else "")
        values: list = [label]
        if self._file_column:
            library = self._library_for(symbol)
            values.append("" if library is None else library.path.name)
        values.extend((symbol.mode_count, len(symbol.parts()), len(issues)))
        return tuple(values)

    def _insert_symbol_row(self, symbol: Symbol) -> None:
        issues = self.issues.get(symbol.editor_iid, [])
        self.symbol_tree.insert(
            "",
            "end",
            iid=symbol.editor_iid,
            values=self._symbol_values(symbol),
            tags=("bad",) if issues else (),
        )

    def _refresh_symbol_row(self, symbol: Symbol) -> None:
        iid = getattr(symbol, "editor_iid", "")
        if not iid:
            return
        visible = [item.editor_iid for item in self._visible_symbols()]
        exists = self.symbol_tree.exists(iid)
        if iid not in visible:
            if exists:
                self.symbol_tree.delete(iid)
            return
        if not exists:
            self.symbol_tree.insert(
                "",
                visible.index(iid),
                iid=symbol.editor_iid,
                values=self._symbol_values(symbol),
                tags=("bad",) if self.issues.get(iid) else (),
            )
            return
        issues = self.issues.get(iid, [])
        self.symbol_tree.item(iid, values=self._symbol_values(symbol), tags=("bad",) if issues else ())

    def _selected_iid(self) -> str | None:
        selection = self.symbol_tree.selection()
        if not selection:
            return None
        return selection[0]

    def _selected_symbol(self) -> Symbol | None:
        iid = self._selected_iid()
        if iid is None:
            return None
        return self._symbols.get(iid)

    def _on_symbol_event(self, _event=None) -> None:
        if self._loading:
            return
        self._show_symbol()

    def _show_symbol(self) -> None:
        symbol = self._selected_symbol()
        self._current_iid = None if symbol is None else symbol.editor_iid
        if symbol is None:
            self._fill_pins(None)
            self._draw(self.edit_preview, None, [])
            self._fill_issue_view()
            return
        self._fill_pins(symbol)
        self._redraw(symbol)
        self._fill_issue_view()

    def _configure_pin_tree(self, mode_count: int) -> None:
        columns = ["designator"] + [f"m{index}" for index in range(mode_count)]
        if mode_count:
            columns.append("pad")
        self.pin_tree["columns"] = columns
        self.pin_tree.heading("designator", text="位号")
        designator_width = self._heading_width("位号")
        self.pin_tree.column(
            "designator",
            width=designator_width,
            minwidth=designator_width,
            stretch=False,
            anchor="w",
        )
        headings = [f"模式 {mode_letter(index)}" for index in range(mode_count)]
        needed = max((self._heading_width(text) for text in headings), default=self._heading_width("模式 A"))
        if self._pin_name_resized:
            self._pin_name_width = max(self._pin_name_width, needed)
        else:
            self._pin_name_width = needed
        for index, title in enumerate(headings):
            key = f"m{index}"
            self.pin_tree.heading(key, text=title)
            self.pin_tree.column(key, width=self._pin_name_width, minwidth=needed, stretch=False, anchor="w")
        if mode_count:
            self.pin_tree.heading("pad", text="")
            self.pin_tree.column("pad", width=self._pin_name_width, minwidth=needed, stretch=False, anchor="w")
        self._fit_pin_pane()

    def _pin_list_width(self) -> int:
        total = sum(int(self.pin_tree.column(column, "width")) for column in self.pin_tree["columns"])
        scroll = self._pin_scroll.winfo_reqwidth()
        if scroll < 8:
            scroll = 18
        return total + scroll + 4

    def _fit_pin_pane(self) -> None:
        split = getattr(self, "_edit_split", None)
        if split is None or not split.winfo_exists():
            return
        width = self._pin_list_width()

        def apply() -> None:
            if not split.winfo_exists():
                return
            try:
                split.sashpos(0, width)
            except tk.TclError:
                return

        self.after_idle(apply)

    def _heading_width(self, text: str) -> int:
        if self._heading_font is None:
            spec = ttk.Style(self).lookup("Treeview.Heading", "font") or ("Microsoft YaHei UI", 10)
            self._heading_font = tkfont.Font(self, font=spec)
        return self._heading_font.measure(text) + 16

    def _sync_pin_column_widths(self, _event=None) -> None:
        if self._syncing_pin_widths:
            return
        columns = [
            column
            for column in self.pin_tree["columns"]
            if str(column).startswith("m") or column == "pad"
        ]
        if not columns:
            return
        widths = [int(self.pin_tree.column(column, "width")) for column in columns]
        changed = next((width for width in widths if width != self._pin_name_width), None)
        if len(columns) == 1:
            if widths[0] != self._pin_name_width:
                self._pin_name_resized = True
                self._pin_name_width = widths[0]
                self._fit_pin_pane()
            return
        if changed is None:
            return
        self._syncing_pin_widths = True
        try:
            self._pin_name_resized = True
            self._pin_name_width = changed
            for column in columns:
                if int(self.pin_tree.column(column, "width")) != changed:
                    self.pin_tree.column(column, width=changed)
        finally:
            self._syncing_pin_widths = False
            self._fit_pin_pane()

    def _fill_pins(self, symbol: Symbol | None, keep: str | None = None) -> None:
        self._pin_rows = {}
        self.pin_tree.delete(*self.pin_tree.get_children())
        if symbol is None:
            self._configure_pin_tree(0)
            return
        self._configure_pin_tree(symbol.mode_count)
        groups: dict[str, list[PinInfo]] = {}
        for pin in symbol.pins():
            if pin.broken:
                continue
            groups.setdefault(pin.designator, []).append(pin)
        target = ""
        for designator in sorted(groups, key=_natural_text):
            pins = groups[designator]
            cells: list[str] = []
            signatures: list[frozenset[str]] = []
            for mode in range(symbol.mode_count):
                names: list[str] = []
                seen: set[str] = set()
                mode_pins = sorted(
                    (pin for pin in pins if pin.mode == mode),
                    key=lambda pin: (pin.part_id, pin.ordinal),
                )
                for pin in mode_pins:
                    if pin.name not in seen:
                        seen.add(pin.name)
                        names.append(pin.name)
                cells.append("、".join(names))
                signatures.append(frozenset(names))
            iid = f"row-{len(self._pin_rows)}"
            self._pin_rows[iid] = pins
            self.pin_tree.insert(
                "",
                "end",
                iid=iid,
                values=(designator, *cells),
                tags=("mismatch",) if len(set(signatures)) > 1 else (),
            )
            if keep == designator:
                target = iid
        children = self.pin_tree.get_children()
        if target not in children:
            target = children[0] if children else ""
        if target:
            self.pin_tree.selection_set(target)
            self.pin_tree.focus(target)

    def _selected_pin_group(self) -> list[PinInfo]:
        selection = self.pin_tree.selection()
        if not selection:
            return []
        return list(self._pin_rows.get(selection[0], []))

    def _on_pin_tree_right_click(self, event) -> str:
        mode_index = self._mode_index_at(event)
        if mode_index is not None:
            return self._popup_mode_menu(event, mode_index)
        return self._popup_pin_menu(event)

    def _mode_index_at(self, event) -> int | None:
        if self.pin_tree.identify_region(event.x, event.y) != "heading":
            return None
        column = self.pin_tree.identify_column(event.x)
        if not str(column).startswith("#"):
            return None
        number = int(column[1:]) - 1
        columns = list(self.pin_tree["columns"])
        if not 0 <= number < len(columns):
            return None
        name = str(columns[number])
        if not name.startswith("m"):
            return None
        return int(name[1:])

    def _popup_mode_menu(self, event, mode_index: int) -> str:
        symbol = self._selected_symbol()
        if symbol is None or not 0 <= mode_index < symbol.mode_count:
            return "break"
        choices: list[tuple[str, bool, object] | None] = [
            ("左移", mode_index > 0, lambda index=mode_index: self._shift_mode(index, -1)),
            ("右移", mode_index < symbol.mode_count - 1, lambda index=mode_index: self._shift_mode(index, 1)),
            ("删除", symbol.mode_count > 1, lambda index=mode_index: self._delete_mode_at(index)),
            ("恢复", symbol.editor_iid in self._mode_baselines, self._restore_modes),
        ]
        self._schedule_choice_popup(event.x_root, event.y_root, choices)
        return "break"

    def _popup_pin_menu(self, event) -> str:
        row = self.pin_tree.identify_row(event.y)
        if not row:
            return "break"
        self.pin_tree.selection_set(row)
        self.pin_tree.focus(row)
        pins = list(self._pin_rows.get(row, []))
        symbol = self._selected_symbol()
        if symbol is None or not pins:
            return "break"
        names: list[str] = []
        seen: set[str] = set()
        for mode in range(symbol.mode_count):
            for pin in pins:
                if pin.mode == mode and pin.name not in seen:
                    seen.add(pin.name)
                    names.append(pin.name)
        choices: list[tuple[str, bool, object] | None] = []
        if names:
            for name in names:
                choices.append(
                    (
                        name if name else "（空名称）",
                        True,
                        lambda chosen=name, group=list(pins): self._apply_pin_name(group, chosen),
                    )
                )
        else:
            choices.append(("（没有已有名称）", False, None))
        choices.append(None)
        choices.append(("自定义名称…", True, lambda group=list(pins): self._ask_pin_name(group)))
        self._schedule_choice_popup(event.x_root, event.y_root, choices)
        return "break"

    def _schedule_choice_popup(self, x_root: int, y_root: int, choices: list[tuple[str, bool, object] | None]) -> None:
        self._discard_pin_menu()
        token = object()
        self._popup_token = token
        self.after_idle(lambda: self._show_choice_popup(token, x_root, y_root, choices))

    def _show_choice_popup(
        self,
        token: object,
        x_root: int,
        y_root: int,
        choices: list[tuple[str, bool, object] | None],
    ) -> None:
        if token is not self._popup_token:
            return
        popup = tk.Toplevel(self)
        popup.withdraw()
        popup.overrideredirect(True)
        popup.configure(bg="#b5b5b5")
        body = tk.Frame(popup, bg="white", padx=3, pady=3)
        body.pack(fill="both", expand=True, padx=1, pady=1)
        font = ("Microsoft YaHei UI", 12)
        for item in choices:
            if item is None:
                tk.Frame(body, height=1, bg="#d5d5d5").pack(fill="x", pady=4)
                continue
            label, enabled, action = item
            button = tk.Button(
                body,
                text=label,
                font=font,
                anchor="w",
                relief="flat",
                bg="white",
                activebackground="#d7e6f8",
                disabledforeground="#9a9a9a",
                padx=16,
                pady=6,
                state="normal" if enabled else "disabled",
                command=lambda fn=action: self._choose_popup_item(fn),
            )
            button.pack(fill="x")
        self._pin_menu = popup
        popup.update_idletasks()
        popup.geometry(f"+{int(x_root)}+{int(y_root)}")
        popup.deiconify()
        popup.lift()
        try:
            popup.grab_set()
        except tk.TclError:
            pass
        popup.bind("<Escape>", lambda _event: self._discard_pin_menu())
        popup.bind("<Button-1>", self._close_popup_on_outside_click)
        popup.focus_set()

    def _close_popup_on_outside_click(self, event) -> None:
        popup = self._pin_menu
        if popup is not None and event.widget is popup:
            self._discard_pin_menu()

    def _choose_popup_item(self, action) -> None:
        self._discard_pin_menu()
        if action is not None:
            self.after_idle(action)

    def _discard_pin_menu(self) -> None:
        self._popup_token = None
        popup = self._pin_menu
        self._pin_menu = None
        if popup is None:
            return
        try:
            popup.grab_release()
        except tk.TclError:
            pass
        try:
            popup.destroy()
        except tk.TclError:
            pass

    def _ask_pin_name(self, pins: list[PinInfo]) -> None:
        name = self._ask_text("自定义名称", "新的引脚名称。将应用到这个位号的全部显示模式。")
        if name is None:
            return
        self._apply_pin_name(pins, name)

    def _ask_text(self, title: str, prompt: str) -> str | None:
        dialog = tk.Toplevel(self)
        dialog.title(title)
        dialog.transient(self)
        dialog.resizable(False, False)
        font = ("Microsoft YaHei UI", 12)
        frame = tk.Frame(dialog, padx=18, pady=16)
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text=prompt, font=font, justify="left", wraplength=460).pack(anchor="w")
        value = tk.StringVar()
        entry = tk.Entry(frame, textvariable=value, font=font, width=32)
        entry.pack(fill="x", pady=(12, 16), ipady=4)
        result: dict[str, str | None] = {"value": None}

        def accept(_event=None) -> None:
            result["value"] = value.get()
            dialog.destroy()

        def cancel(_event=None) -> None:
            dialog.destroy()

        buttons = tk.Frame(frame)
        buttons.pack(anchor="e")
        tk.Button(buttons, text="确定", font=font, width=8, padx=8, pady=3, command=accept).pack(side="left", padx=(0, 8))
        tk.Button(buttons, text="取消", font=font, width=8, padx=8, pady=3, command=cancel).pack(side="left")
        dialog.bind("<Return>", accept)
        dialog.bind("<Escape>", cancel)
        dialog.protocol("WM_DELETE_WINDOW", cancel)
        dialog.update_idletasks()
        dialog.geometry(f"+{self.winfo_rootx() + 80}+{self.winfo_rooty() + 120}")
        dialog.grab_set()
        entry.focus_set()
        self.wait_window(dialog)
        return result["value"]

    def _apply_pin_name(self, pins: list[PinInfo], name: str) -> None:
        symbol = self._selected_symbol()
        if symbol is None or not pins:
            return
        updated = 0
        notes: list[str] = []
        seen_notes: set[str] = set()
        try:
            for pin in pins:
                result = symbol.rename_pin(pin, pin.designator, name, all_modes=False)
                updated += result.updated
                for note in result.notes:
                    if note not in seen_notes:
                        seen_notes.add(note)
                        notes.append(note)
        except SchlibFormatError as exc:
            messagebox.showerror("不能修改", str(exc), parent=self)
            return
        if updated == 0:
            self._set_status(notes[0] if notes else "没有修改")
            return
        notes = [note for note in notes if note != "位号和名称没有变化"]
        keep = pins[0].designator
        self._recheck()
        self._refresh_symbol_row(symbol)
        self._update_summary()
        self._fill_pins(symbol, keep=keep)
        self._redraw(symbol)
        self._set_status(f"已修改 {updated} 个引脚，尚未写入文件")
        if notes:
            messagebox.showwarning("引脚已修改", "\n".join(notes), parent=self)

    def _remember_mode_baseline(self, symbol: Symbol) -> bool:
        iid = getattr(symbol, "editor_iid", "")
        if not iid or iid in self._mode_baselines:
            return False
        self._mode_baselines[iid] = symbol._capture()
        return True

    def _shift_mode(self, index: int, delta: int) -> None:
        symbol = self._selected_symbol()
        if symbol is None:
            return
        target = index + delta
        if not 0 <= index < symbol.mode_count or not 0 <= target < symbol.mode_count:
            return
        order = list(range(symbol.mode_count))
        order[index], order[target] = order[target], order[index]
        created = self._remember_mode_baseline(symbol)
        try:
            symbol.apply_order(order)
        except SchlibFormatError as exc:
            if created:
                self._mode_baselines.pop(symbol.editor_iid, None)
            messagebox.showerror("不能调整", str(exc), parent=self)
            return
        direction = "左" if delta < 0 else "右"
        self._finish_mode_edit(symbol, f"已将模式 {mode_letter(index)} 向{direction}移动，尚未写入文件")

    def _delete_mode_at(self, index: int) -> None:
        symbol = self._selected_symbol()
        if symbol is None:
            return
        if not 0 <= index < symbol.mode_count:
            return
        if symbol.mode_count <= 1:
            messagebox.showinfo("不能删除", "至少需要保留一个显示模式。", parent=self)
            return
        letter = mode_letter(index)
        agreed = messagebox.askyesno(
            "删除显示模式",
            f"删除模式 {letter}。这个符号的每个 Part 都会去掉该模式，后面的模式依次前移。\n"
            "改动还在内存里，保存后才会写入文件。",
            parent=self,
        )
        if not agreed:
            return
        created = self._remember_mode_baseline(symbol)
        try:
            symbol.delete_mode(index)
        except SchlibFormatError as exc:
            if created:
                self._mode_baselines.pop(symbol.editor_iid, None)
            messagebox.showerror("不能删除", str(exc), parent=self)
            return
        status = f"已删除模式 {letter}，尚未写入文件"
        if symbol.mode_count == 1:
            status += "。这个符号现在只剩一个显示模式"
        self._finish_mode_edit(symbol, status)

    def _restore_modes(self) -> None:
        symbol = self._selected_symbol()
        if symbol is None:
            return
        snapshot = self._mode_baselines.get(symbol.editor_iid)
        if snapshot is None:
            self._set_status("没有需要恢复的显示模式调整")
            return
        agreed = messagebox.askyesno(
            "恢复显示模式",
            "恢复到开始调整显示模式之前。这之后对这个符号的修改会一起撤消。",
            parent=self,
        )
        if not agreed:
            return
        symbol._restore(snapshot)
        self._mode_baselines.pop(symbol.editor_iid, None)
        status = "已恢复显示模式"
        if symbol.dirty:
            status += "，尚未写入文件"
        self._finish_mode_edit(symbol, status)

    def _finish_mode_edit(self, symbol: Symbol, status: str) -> None:
        self._recheck()
        self._refresh_symbol_row(symbol)
        self._update_summary()
        self._show_symbol()
        self._set_status(status)

    def _fill_issue_view(self) -> None:
        self.issue_tree.delete(*self.issue_tree.get_children())
        self._issue_owner = {}
        current = self._selected_iid()
        index = 0
        for symbol_iid, issues in self.issues.items():
            if self.only_current_var.get() and symbol_iid != current:
                continue
            symbol = self._symbols.get(symbol_iid)
            symbol_name = symbol.name if symbol is not None else ""
            for issue in issues:
                item = f"issue-{index}"
                mode = "" if issue.mode is None else mode_letter(issue.mode)
                self._issue_owner[item] = symbol_iid
                self.issue_tree.insert("", "end", iid=item, values=(symbol_name, mode, issue.kind, issue.message))
                index += 1

    def _open_issue(self, _event=None) -> None:
        selection = self.issue_tree.selection()
        if not selection:
            return
        symbol_iid = self._issue_owner.get(selection[0])
        if not symbol_iid:
            return
        if not self.symbol_tree.exists(symbol_iid):
            self.filter_var.set("")
        if self.symbol_tree.exists(symbol_iid):
            self.symbol_tree.selection_set(symbol_iid)
            self.symbol_tree.see(symbol_iid)
            self._show_symbol()
        self.notebook.select(self.edit_tab)

    def _redraw(self, symbol: Symbol | None) -> None:
        if symbol is None:
            self._draw(self.edit_preview, None, [])
            return
        group = self._selected_pin_group()
        highlight = None if not group else (None, None, group[0].designator)
        self._draw(
            self.edit_preview,
            symbol,
            list(range(symbol.mode_count)),
            highlight=highlight,
            label_as_target=False,
        )

    def _draw(self, preview, symbol: Symbol | None, modes: list[int], **kwargs) -> None:
        if preview is None:
            return
        preview.show(symbol, modes, **kwargs)

    def _update_summary(self) -> None:
        if not self.libraries:
            self.summary_var.set("")
            self.title(APP_TITLE)
            self._refresh_footer()
            return
        total = len(self._all_symbols())
        bad = sum(1 for items in self.issues.values() if items)
        dirty = sum(1 for symbol in self._all_symbols() if symbol.dirty)
        text = f"多显示模式符号 {total} 个，有问题 {bad} 个"
        if len(self.libraries) > 1:
            text += f"，来自 {len(self.libraries)} 个库"
        if dirty:
            text += f"，未保存 {dirty} 个"
        self.summary_var.set(text)
        self.title(APP_TITLE + (" *" if dirty else ""))
        self._refresh_footer()

    def _set_status(self, text: str) -> None:
        self._status_text = text
        self._refresh_footer()

    def _refresh_footer(self) -> None:
        summary = self.summary_var.get()
        if self._status_text and summary:
            self.status_var.set(f"{self._status_text}，{summary}")
        else:
            self.status_var.set(self._status_text or summary)


def _natural_text(text: str):
    import re

    key = []
    for piece in re.split(r"(\d+)", text):
        if piece.isdigit():
            key.append((0, int(piece)))
        else:
            key.append((1, piece.casefold()))
    return key


def main() -> None:
    app = EditorApp()
    app.mainloop()


if __name__ == "__main__":
    main()
