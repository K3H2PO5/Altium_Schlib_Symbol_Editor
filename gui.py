"""SchLib 多显示模式符号的检查、引脚编辑和模式排序窗口。"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox, ttk

import draw as draw_mod
from i18n import localize_error, localize_note, tr
from issues import check_symbol
from library import Library, LibraryError, load_library, schlib_files
from ole_storage import OleStorageError
from records import PinInfo, SchlibFormatError, Symbol, default_mode_name
from version import VERSION

APP_TITLE = f"Altium_Schlib_Symbol_Editor {VERSION}"


class EditorApp(tk.Tk):
    """打开一个 SchLib。列出多种显示模式的符号，以及引脚重复或不连续的符号。"""

    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1280x820")
        self.minsize(1020, 680)
        self._apply_font()
        self.libraries: list[Library] = []
        self._symbols: dict[str, Symbol] = {}
        self._pin_rows: dict[str, list[PinInfo]] = {}
        self._picked_record: int | None = None
        self._syncing_pin_selection = False
        self._file_column = False
        self.issues: dict[str, list] = {}
        self._mode_baselines: dict[str, object] = {}
        self._current_iid: str | None = None
        self._loading = False
        self._pin_name_width = 0
        self._pin_name_resized = False
        self._syncing_pin_widths = False
        self._heading_font: tkfont.Font | None = None
        self.lang = "zh"
        self.path_var = tk.StringVar(value=self.t("not_open"))
        self.summary_var = tk.StringVar(value="")
        self._status_spec: tuple[str, dict] = ("status_ready", {})
        self._status_text = self.t("status_ready")
        self.status_var = tk.StringVar(value=self._status_text)
        self.filter_var = tk.StringVar()
        self.only_issues_var = tk.BooleanVar(value=False)
        self._issue_filter: set[str] | None = None
        self.check_gaps_var = tk.BooleanVar(value=True)
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Control-o>", lambda _event: self.open_library())
        self.bind("<Control-s>", lambda _event: self.save_library())

    def t(self, key: str, **kwargs: object) -> str:
        return tr(self.lang, key, **kwargs)

    def _toggle_language(self) -> None:
        self.lang = "en" if self.lang == "zh" else "zh"
        self._apply_language()

    def _apply_language(self) -> None:
        self._lang_button.configure(text=self.t("lang_button"))
        self._open_button.configure(text=self.t("open_schlib"))
        self._folder_button.configure(text=self.t("open_folder"))
        self._save_button.configure(text=self.t("save"))
        self._close_button.configure(text=self.t("close_file"))
        self._symbol_caption.configure(text=self.t("symbol_list"))
        self._only_issues.configure(text=self.t("only_issues"))
        self._check_gaps.configure(text=self.t("check_gaps"))
        self._filter_label.configure(text=self.t("filter"))
        for key, title_key in (
            ("mode", "col_mode"),
            ("kind", "col_kind"),
            ("message", "col_detail"),
        ):
            self.issue_tree.heading(key, text=self.t(title_key))
        if self._preview_missing is not None:
            self._preview_missing.configure(text=self.t("no_matplotlib"))
        if not self.libraries:
            self.path_var.set(self.t("not_open"))
        self._retranslate_status()
        self._update_summary()
        if self.libraries:
            self._recheck()
            self._fill_symbols()
        else:
            self._configure_symbol_tree()
            self._configure_pin_tree(0)
            self._draw(self.edit_preview, None, [])

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
        self._open_button = ttk.Button(bar, text=self.t("open_schlib"), command=self.open_library)
        self._open_button.pack(side="left")
        self._folder_button = ttk.Button(bar, text=self.t("open_folder"), command=self.open_folder)
        self._folder_button.pack(side="left", padx=(6, 0))
        self._save_button = ttk.Button(bar, text=self.t("save"), command=self.save_library)
        self._save_button.pack(side="left", padx=(6, 0))
        self._close_button = ttk.Button(bar, text=self.t("close_file"), command=self.close_library)
        self._close_button.pack(side="left", padx=(6, 0))
        self._lang_button = ttk.Button(bar, text=self.t("lang_button"), command=self._toggle_language)
        self._lang_button.pack(side="right")
        ttk.Label(bar, textvariable=self.path_var).pack(side="left", padx=12)

        paned = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        paned.grid(row=1, column=0, sticky="nsew", padx=8, pady=4)
        paned.add(self._build_symbol_pane(paned), weight=2)
        paned.add(self._build_editor(paned), weight=5)
        ttk.Label(self, textvariable=self.status_var, padding=(8, 4)).grid(row=2, column=0, sticky="ew")

    def _build_symbol_pane(self, parent: ttk.Panedwindow) -> ttk.Frame:
        pane = ttk.Frame(parent, padding=(0, 0, 8, 0))
        pane.columnconfigure(0, weight=1)
        pane.rowconfigure(3, weight=1)
        self._symbol_caption = ttk.Label(pane, text=self.t("symbol_list"))
        self._symbol_caption.grid(row=0, column=0, sticky="w")
        checks = ttk.Frame(pane)
        checks.grid(row=1, column=0, sticky="w")
        self._only_issues = ttk.Checkbutton(
            checks,
            text=self.t("only_issues"),
            variable=self.only_issues_var,
            command=self._on_only_issues,
        )
        self._only_issues.pack(side="left")
        self._check_gaps = ttk.Checkbutton(
            checks,
            text=self.t("check_gaps"),
            variable=self.check_gaps_var,
            command=self._on_gap_check,
        )
        self._check_gaps.pack(side="left", padx=(12, 0))
        filter_row = ttk.Frame(pane)
        filter_row.grid(row=2, column=0, sticky="ew", pady=4)
        filter_row.columnconfigure(1, weight=1)
        self._filter_label = ttk.Label(filter_row, text=self.t("filter"))
        self._filter_label.grid(row=0, column=0, sticky="w")
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

    def _build_editor(self, parent: ttk.Panedwindow) -> ttk.Frame:
        self.edit_tab = ttk.Frame(parent, padding=8)
        self._build_edit_tab()
        return self.edit_tab

    def _build_edit_tab(self) -> None:
        self.edit_tab.columnconfigure(0, weight=1)
        self.edit_tab.rowconfigure(1, weight=1)
        issues = ttk.Frame(self.edit_tab)
        issues.grid(row=0, column=0, sticky="ew")
        issues.columnconfigure(0, weight=1)
        self.issue_tree = ttk.Treeview(
            issues,
            columns=("mode", "kind", "message"),
            show="headings",
            selectmode="browse",
            height=5,
        )
        for key, title_key, width, stretch in (
            ("mode", "col_mode", 160, False),
            ("kind", "col_kind", 100, False),
            ("message", "col_detail", 480, True),
        ):
            title = self.t(title_key)
            self.issue_tree.heading(key, text=title)
            self.issue_tree.column(key, width=width, anchor="w", stretch=stretch)
        self.issue_tree.grid(row=0, column=0, sticky="ew")
        scroll = ttk.Scrollbar(issues, orient="vertical", command=self.issue_tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.issue_tree.configure(yscrollcommand=scroll.set)
        middle = ttk.Panedwindow(self.edit_tab, orient=tk.HORIZONTAL)
        middle.grid(row=1, column=0, sticky="nsew", pady=6)
        self._edit_split = middle
        pin_pane = ttk.Frame(middle)
        pin_pane.columnconfigure(0, weight=1)
        pin_pane.rowconfigure(0, weight=1)
        self.pin_tree = ttk.Treeview(pin_pane, columns=("designator",), show="headings", selectmode="browse")
        self.pin_tree.heading("designator", text=self.t("col_designator"))
        designator_width = self._heading_width(self.t("col_designator"))
        self.pin_tree.column("designator", width=designator_width, minwidth=designator_width, stretch=False, anchor="w")
        self.pin_tree.tag_configure("mismatch", foreground="#a32020")
        self.pin_tree.grid(row=0, column=0, sticky="nsew")
        pin_scroll = ttk.Scrollbar(pin_pane, orient="vertical", command=self.pin_tree.yview)
        pin_scroll.grid(row=0, column=1, sticky="ns")
        self._pin_scroll = pin_scroll
        pin_xscroll = ttk.Scrollbar(pin_pane, orient="horizontal", command=self.pin_tree.xview)
        pin_xscroll.grid(row=1, column=0, sticky="ew")
        self.pin_tree.configure(yscrollcommand=pin_scroll.set, xscrollcommand=pin_xscroll.set)
        self.pin_tree.bind("<<TreeviewSelect>>", self._on_pin_tree_select)
        self.pin_tree.bind("<Button-3>", self._on_pin_tree_right_click)
        self._preview_missing: ttk.Label | None = None
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
            label = ttk.Label(parent, text=self.t("no_matplotlib"), wraplength=420, justify="left")
            label.grid(row=0, column=0, sticky="nsew")
            self._preview_missing = label
            return None
        figure = draw_mod.ScrollFigure(parent)
        figure.set_pin_handler(self._on_drawing_pin)
        figure.grid(row=0, column=0, sticky="nsew")
        return figure

    def open_library(self) -> None:
        if not self._confirm_discard():
            return
        chosen = filedialog.askopenfilename(
            parent=self,
            title=self.t("open_schlib"),
            filetypes=[("Altium SchLib", "*.SchLib"), (self.t("all_files"), "*.*")],
        )
        if not chosen:
            return
        self.load_library(Path(chosen))

    def open_folder(self) -> None:
        if not self._confirm_discard():
            return
        chosen = filedialog.askdirectory(parent=self, title=self.t("open_folder"))
        if not chosen:
            return
        self.load_folder(Path(chosen))

    def close_library(self) -> None:
        if not self.libraries:
            self._set_status_key("no_library")
            return
        had_changes = any(symbol.dirty for symbol in self._all_symbols())
        if not self._confirm_discard():
            return
        saved = had_changes and not any(symbol.dirty for symbol in self._all_symbols())
        self.libraries = []
        self._symbols = {}
        self._current_iid = None
        self._picked_record = None
        self.issues = {}
        self._mode_baselines = {}
        self.filter_var.set("")
        self.path_var.set(self.t("not_open"))
        self._fill_symbols()
        self._recheck()
        self._set_status_key("closed_saved" if saved else "closed")

    def load_folder(self, folder: Path) -> None:
        try:
            files = schlib_files(folder)
        except LibraryError as exc:
            messagebox.showerror(self.t("open_failed"), localize_error(self.lang, str(exc)), parent=self)
            return
        if not files:
            messagebox.showwarning(self.t("open_folder"), self.t("folder_empty"), parent=self)
            return
        self.configure(cursor="watch")
        libraries: list[Library] = []
        failed: list[str] = []
        try:
            for index, file in enumerate(files, start=1):
                self._set_status_key("reading_named", index=index, total=len(files), name=file.name)
                self.update_idletasks()
                try:
                    libraries.append(load_library(file))
                except (LibraryError, OSError) as exc:
                    failed.append(f"{file.name}: {exc}")
        finally:
            self.configure(cursor="")
        if not libraries:
            messagebox.showerror(self.t("open_failed"), "\n".join(failed) or self.t("nothing_read"), parent=self)
            return
        symbol_count = sum(len(library.symbols) for library in libraries)
        self._use_libraries(
            libraries,
            self.t("folder_label", folder=folder, count=len(libraries)),
            "opened_folder",
        )
        skipped = [(library.path.name, name, err) for library in libraries for name, err in library.skipped]
        if failed or skipped or symbol_count == 0:
            lines: list[str] = []
            if symbol_count == 0:
                lines.append(self.t("no_multi_mode"))
            if failed:
                lines.append(self.t("files_failed", count=len(failed)))
                lines.extend(failed[:6])
            if skipped:
                lines.append(self.t("symbols_unreadable", count=len(skipped)))
                lines.extend(f"{file_name}/{name}: {err}" for file_name, name, err in skipped[:6])
            messagebox.showwarning(self.t("open_folder"), "\n".join(lines), parent=self)

    def load_library(self, path: Path) -> None:
        self._set_status_key("reading_library")
        self.configure(cursor="watch")
        self.update_idletasks()

        def progress(index: int, total: int) -> None:
            self._set_status_key("reading_progress", index=index, total=total)
            self.update_idletasks()

        try:
            library = load_library(path, progress)
        except (LibraryError, OSError) as exc:
            self.configure(cursor="")
            messagebox.showerror(self.t("open_failed"), localize_error(self.lang, str(exc)), parent=self)
            return
        self.configure(cursor="")
        self._use_libraries([library], str(path), "opened")
        if library.skipped or not library.symbols:
            lines: list[str] = []
            if not library.symbols:
                lines.append(self.t("no_multi_mode"))
            if library.skipped:
                lines.append(self.t("symbols_unreadable", count=len(library.skipped)))
                lines.extend(f"{name}: {err}" for name, err in library.skipped[:6])
            messagebox.showwarning(self.t("open_library_title"), "\n".join(lines), parent=self)

    def save_library(self, *, confirm: bool = True) -> bool:
        dirty_libraries = [library for library in self.libraries if any(symbol.dirty for symbol in library.symbols)]
        if not self.libraries:
            self._set_status_key("no_library")
            return False
        if not dirty_libraries:
            self._set_status_key("no_save_needed")
            return True
        if confirm:
            lines = []
            for library in dirty_libraries:
                names = [symbol.name for symbol in library.symbols if symbol.dirty]
                lines.append(library.path.name)
                lines.extend(f"  {name}" for name in names[:8])
                if len(names) > 8:
                    lines.append(self.t("more_symbols", count=len(names)))
            agreed = messagebox.askyesno(
                self.t("save"),
                self.t("save_prompt", lines="\n".join(lines)),
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
                self.t("save_failed"),
                self.t("save_failed_body", error=localize_error(self.lang, str(exc))),
                parent=self,
            )
            self._recheck()
            self._fill_symbols()
            return False
        self._recheck()
        self._fill_symbols()
        self._set_status_key("saved", count=len(backups), names=self.t("list_sep").join(backups))
        return True

    def _use_libraries(self, libraries: list[Library], label: str, status: str) -> None:
        self.libraries = libraries
        self._mode_baselines = {}
        self._index_symbols()
        self._current_iid = None
        self._picked_record = None
        self.path_var.set(label)
        self._recheck()
        if self.only_issues_var.get():
            self._capture_issue_filter()
        self._fill_symbols()
        self._set_status_key(status)

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
        answer = messagebox.askyesnocancel(self.t("unsaved_title"), self.t("unsaved_body"), parent=self)
        if answer is None:
            return False
        if answer:
            return self.save_library(confirm=False)
        return True

    def _on_close(self) -> None:
        if self._confirm_discard():
            self.destroy()

    def _on_gap_check(self) -> None:
        self._loading = True
        try:
            self._recheck()
        finally:
            self._loading = False
        self._fill_symbols()

    def _recheck(self) -> None:
        self.issues = {}
        check_gaps = self.check_gaps_var.get()
        for symbol in self._all_symbols():
            self.issues[symbol.editor_iid] = check_symbol(symbol, self.lang, check_gaps=check_gaps)
            self._refresh_symbol_row(symbol)
        self._fill_issue_view()
        self._update_summary()

    def _on_only_issues(self) -> None:
        if self.only_issues_var.get():
            self._capture_issue_filter()
        else:
            self._issue_filter = None
        self._fill_symbols()

    def _capture_issue_filter(self) -> None:
        self._issue_filter = {
            symbol.editor_iid for symbol in self._all_symbols() if self.issues.get(symbol.editor_iid)
        }

    def _visible_symbols(self) -> list[Symbol]:
        text = self.filter_var.get().strip().casefold()
        pinned = self._issue_filter
        matched = []
        for symbol in self._all_symbols():
            if pinned is not None and symbol.editor_iid not in pinned:
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
            "name": ("col_symbol", 180, True, "w"),
            "file": ("col_file", 180, True, "w"),
            "modes": ("col_modes", 64, False, "center"),
            "parts": ("col_part", 48, False, "center"),
            "issues": ("col_issues", 64, False, "center"),
        }
        for key in columns:
            title_key, width, stretch, anchor = headings[key]
            title = self.t(title_key)
            self.symbol_tree.heading(key, text=title)
            self.symbol_tree.column(key, width=max(width, self._heading_width(title)), stretch=stretch, anchor=anchor)

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
        iid = None if symbol is None else symbol.editor_iid
        if iid != self._current_iid:
            self._picked_record = None
        self._current_iid = iid
        if symbol is None:
            self._fill_pins(None)
            self._draw(self.edit_preview, None, [])
            self._fill_issue_view()
            return
        keep = self._picked_designator(symbol)
        self._fill_pins(symbol, keep=keep)
        self._redraw(symbol)
        self._fill_issue_view()

    def _picked_designator(self, symbol: Symbol) -> str | None:
        if self._picked_record is None:
            return None
        pin = self._pin_by_record(symbol, self._picked_record)
        if pin is None:
            self._picked_record = None
            return None
        return pin.designator

    def _pin_by_record(self, symbol: Symbol, record: int) -> PinInfo | None:
        for pin in symbol.pins():
            if pin.record_index == record and not pin.broken:
                return pin
        return None

    def _configure_pin_tree(self, mode_count: int, names: list[str] | None = None) -> None:
        columns = ["designator"] + [f"m{index}" for index in range(mode_count)]
        if mode_count:
            columns.append("pad")
        self.pin_tree["columns"] = columns
        self.pin_tree.heading("designator", text=self.t("col_designator"))
        designator_width = self._heading_width(self.t("col_designator"))
        self.pin_tree.column(
            "designator",
            width=designator_width,
            minwidth=designator_width,
            stretch=False,
            anchor="w",
        )
        headings = list(names) if names is not None else [default_mode_name(index) for index in range(mode_count)]
        needed = max((self._heading_width(text) for text in headings), default=self._heading_width("Normal"))
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
        self._configure_pin_tree(symbol.mode_count, [symbol.mode_name(index) for index in range(symbol.mode_count)])
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
        self._syncing_pin_selection = True
        try:
            if target:
                self.pin_tree.selection_set(target)
                self.pin_tree.focus(target)
                self.pin_tree.see(target)
        finally:
            self._syncing_pin_selection = False

    def _selected_pin_group(self) -> list[PinInfo]:
        selection = self.pin_tree.selection()
        if not selection:
            return []
        return list(self._pin_rows.get(selection[0], []))

    def _on_pin_tree_select(self, _event=None) -> None:
        if self._syncing_pin_selection:
            return
        self._picked_record = None
        self._redraw(self._selected_symbol())

    def _on_drawing_pin(self, pin: PinInfo | None, button: int, x_root: int | None = None, y_root: int | None = None) -> None:
        symbol = self._selected_symbol()
        if symbol is None:
            return
        if pin is None:
            if button == 1 and self._picked_record is not None:
                self._picked_record = None
                self._redraw(symbol)
            return
        self._picked_record = pin.record_index
        self._fill_pins(symbol, keep=pin.designator)
        self._redraw(symbol)
        if button != 3:
            return
        chosen = pin
        x = self.winfo_pointerx() if x_root is None else int(x_root)
        y = self.winfo_pointery() if y_root is None else int(y_root)
        self._schedule_choice_popup(
            x,
            y,
            [(self.t("edit_pin_num"), True, lambda target=chosen: self._ask_pin_designator(target))],
        )

    def _ask_pin_designator(self, pin: PinInfo) -> None:
        symbol = self._selected_symbol()
        if symbol is None:
            return
        current = self._pin_by_record(symbol, pin.record_index)
        if current is None:
            return
        value = self._ask_text(
            self.t("edit_designator"),
            self.t("edit_designator_prompt"),
            initial=current.designator,
        )
        if value is None:
            return
        try:
            result = symbol.rename_pin(current, value, current.name, all_modes=False)
        except SchlibFormatError as exc:
            messagebox.showerror(self.t("cannot_edit"), localize_error(self.lang, str(exc)), parent=self)
            return
        if result.updated == 0:
            self._set_status_key("no_change")
            return
        self._picked_record = current.record_index
        notes = [note for note in result.notes if note != self.t("unchanged_note")]
        self._recheck()
        self._refresh_symbol_row(symbol)
        self._update_summary()
        self._show_symbol()
        self._set_status_key("pins_updated", count=result.updated)
        if notes:
            messagebox.showwarning(
                self.t("pins_changed"),
                "\n".join(localize_note(self.lang, note) for note in notes),
                parent=self,
            )

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
            (self.t("move_left"), mode_index > 0, lambda index=mode_index: self._shift_mode(index, -1)),
            (self.t("move_right"), mode_index < symbol.mode_count - 1, lambda index=mode_index: self._shift_mode(index, 1)),
            (self.t("delete"), symbol.mode_count > 1, lambda index=mode_index: self._delete_mode_at(index)),
            None,
            (self.t("custom_mode_name"), True, lambda index=mode_index: self._ask_mode_name(index)),
            (
                self.t("reset_mode_name"),
                symbol.has_custom_mode_name(mode_index),
                lambda index=mode_index: self._reset_mode_name(index),
            ),
            None,
            (self.t("restore"), symbol.editor_iid in self._mode_baselines, self._restore_modes),
        ]
        self._schedule_choice_popup(event.x_root, event.y_root, choices)
        return "break"

    def _popup_pin_menu(self, event) -> str:
        row = self.pin_tree.identify_row(event.y)
        if not row:
            return "break"
        already = row in self.pin_tree.selection()
        self._picked_record = None
        self.pin_tree.selection_set(row)
        self.pin_tree.focus(row)
        if already:
            self._redraw(self._selected_symbol())
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
                        name if name else self.t("empty_name"),
                        True,
                        lambda chosen=name, group=list(pins): self._apply_pin_name(group, chosen),
                    )
                )
        else:
            choices.append((self.t("no_existing_name"), False, None))
        choices.append(None)
        choices.append((self.t("custom_name"), True, lambda group=list(pins): self._ask_pin_name(group)))
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
        name = self._ask_text(self.t("custom_name_title"), self.t("custom_name_prompt"))
        if name is None:
            return
        self._apply_pin_name(pins, name)

    def _ask_mode_name(self, index: int) -> None:
        symbol = self._selected_symbol()
        if symbol is None or not 0 <= index < symbol.mode_count:
            return
        name = self._ask_text(
            self.t("custom_mode_name"),
            self.t("custom_mode_prompt"),
            initial=symbol.mode_name(index),
        )
        if name is None:
            return
        self._rename_mode(index, name, "mode_renamed")

    def _reset_mode_name(self, index: int) -> None:
        self._rename_mode(index, None, "mode_name_reset")

    def _rename_mode(self, index: int, name: str | None, status_key: str) -> None:
        symbol = self._selected_symbol()
        if symbol is None or not 0 <= index < symbol.mode_count:
            return
        created = self._remember_mode_baseline(symbol)
        try:
            changed = symbol.set_mode_name(index, name)
        except SchlibFormatError as exc:
            if created:
                self._mode_baselines.pop(symbol.editor_iid, None)
            messagebox.showerror(self.t("cannot_edit"), localize_error(self.lang, str(exc)), parent=self)
            return
        if not changed:
            if created:
                self._mode_baselines.pop(symbol.editor_iid, None)
            self._set_status_key("no_change")
            return
        self._finish_mode_edit(symbol, status_key, name=symbol.mode_name(index))

    def _ask_text(self, title: str, prompt: str, initial: str = "") -> str | None:
        dialog = tk.Toplevel(self)
        dialog.title(title)
        dialog.transient(self)
        dialog.resizable(False, False)
        font = ("Microsoft YaHei UI", 12)
        frame = tk.Frame(dialog, padx=18, pady=16)
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text=prompt, font=font, justify="left", wraplength=460).pack(anchor="w")
        value = tk.StringVar(value=initial)
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
        tk.Button(buttons, text=self.t("ok"), font=font, width=8, padx=8, pady=3, command=accept).pack(side="left", padx=(0, 8))
        tk.Button(buttons, text=self.t("cancel"), font=font, width=8, padx=8, pady=3, command=cancel).pack(side="left")
        dialog.bind("<Return>", accept)
        dialog.bind("<Escape>", cancel)
        dialog.protocol("WM_DELETE_WINDOW", cancel)
        dialog.update_idletasks()
        dialog.geometry(f"+{self.winfo_rootx() + 80}+{self.winfo_rooty() + 120}")
        dialog.grab_set()
        entry.focus_set()
        entry.select_range(0, "end")
        entry.icursor("end")
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
            messagebox.showerror(self.t("cannot_edit"), localize_error(self.lang, str(exc)), parent=self)
            return
        if updated == 0:
            self._set_status_key("no_change")
            return
        notes = [note for note in notes if note != self.t("unchanged_note")]
        keep = pins[0].designator
        self._recheck()
        self._refresh_symbol_row(symbol)
        self._update_summary()
        self._fill_pins(symbol, keep=keep)
        self._redraw(symbol)
        self._set_status_key("pins_updated", count=updated)
        if notes:
            messagebox.showwarning(self.t("pins_changed"), "\n".join(localize_note(self.lang, note) for note in notes), parent=self)

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
        moved_name = symbol.mode_name(index)
        created = self._remember_mode_baseline(symbol)
        try:
            symbol.apply_order(order)
        except SchlibFormatError as exc:
            if created:
                self._mode_baselines.pop(symbol.editor_iid, None)
            messagebox.showerror(self.t("cannot_reorder"), localize_error(self.lang, str(exc)), parent=self)
            return
        direction = self.t("dir_left") if delta < 0 else self.t("dir_right")
        self._finish_mode_edit(symbol, "moved", name=moved_name, direction=direction)

    def _delete_mode_at(self, index: int) -> None:
        symbol = self._selected_symbol()
        if symbol is None:
            return
        if not 0 <= index < symbol.mode_count:
            return
        if symbol.mode_count <= 1:
            messagebox.showinfo(self.t("cannot_delete"), self.t("keep_one_mode"), parent=self)
            return
        name = symbol.mode_name(index)
        agreed = messagebox.askyesno(
            self.t("delete_mode_title"),
            self.t("delete_mode_body", name=name),
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
            messagebox.showerror(self.t("cannot_delete"), localize_error(self.lang, str(exc)), parent=self)
            return
        status_key = "deleted_mode_last" if symbol.mode_count == 1 else "deleted_mode"
        self._finish_mode_edit(symbol, status_key, name=name)

    def _restore_modes(self) -> None:
        symbol = self._selected_symbol()
        if symbol is None:
            return
        snapshot = self._mode_baselines.get(symbol.editor_iid)
        if snapshot is None:
            self._set_status_key("nothing_to_restore")
            return
        agreed = messagebox.askyesno(
            self.t("restore_title"),
            self.t("restore_body"),
            parent=self,
        )
        if not agreed:
            return
        symbol._restore(snapshot)
        self._mode_baselines.pop(symbol.editor_iid, None)
        status_key = "restored_dirty" if symbol.dirty else "restored"
        self._finish_mode_edit(symbol, status_key)

    def _finish_mode_edit(self, symbol: Symbol, status_key: str, **kwargs: object) -> None:
        self._recheck()
        self._refresh_symbol_row(symbol)
        self._update_summary()
        self._show_symbol()
        self._set_status_key(status_key, **kwargs)

    def _fill_issue_view(self) -> None:
        self.issue_tree.delete(*self.issue_tree.get_children())
        symbol_iid = self._selected_iid()
        if not symbol_iid:
            return
        symbol = self._symbols.get(symbol_iid)
        for index, issue in enumerate(self.issues.get(symbol_iid, [])):
            mode = "" if issue.mode is None or symbol is None else symbol.mode_name(issue.mode)
            self.issue_tree.insert(
                "",
                "end",
                iid=f"issue-{index}",
                values=(mode, self.t(f"kind_{issue.kind}"), issue.message),
            )

    def _drawing_highlight(self, symbol: Symbol, group: list[PinInfo]):
        if self._picked_record is not None:
            pin = self._pin_by_record(symbol, self._picked_record)
            if pin is None:
                self._picked_record = None
            else:
                return (pin.mode, pin.part_id, pin.designator, pin.record_index)
        if not group:
            return None
        return (None, None, group[0].designator)

    def _redraw(self, symbol: Symbol | None) -> None:
        if symbol is None:
            self._draw(self.edit_preview, None, [])
            return
        group = self._selected_pin_group()
        highlight = self._drawing_highlight(symbol, group)
        self._draw(
            self.edit_preview,
            symbol,
            list(range(symbol.mode_count)),
            highlight=highlight,
            label_as_target=False,
            lang=self.lang,
        )

    def _draw(self, preview, symbol: Symbol | None, modes: list[int], **kwargs) -> None:
        if preview is None:
            return
        kwargs.setdefault("lang", self.lang)
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
        parts = [self.t("summary", total=total, bad=bad)]
        if len(self.libraries) > 1:
            parts.append(self.t("summary_libs", count=len(self.libraries)))
        if dirty:
            parts.append(self.t("summary_dirty", count=dirty))
        text = self.t("footer_sep").join(parts)
        self.summary_var.set(text)
        self.title(APP_TITLE + (" *" if dirty else ""))
        self._refresh_footer()

    def _set_status_key(self, key: str, **kwargs: object) -> None:
        self._status_spec = (key, kwargs)
        self._retranslate_status()

    def _retranslate_status(self) -> None:
        key, kwargs = self._status_spec
        self._status_text = self.t(key, **kwargs)
        self._refresh_footer()

    def _refresh_footer(self) -> None:
        summary = self.summary_var.get()
        if self._status_text and summary:
            self.status_var.set(self._status_text + self.t("footer_sep") + summary)
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
