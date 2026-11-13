"""把一个符号的显示模式画到 matplotlib 图上。坐标系是 mil，Y 轴向上。"""

from __future__ import annotations

import math

try:
    import matplotlib
    from matplotlib import patches
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
    from matplotlib.figure import Figure

    matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    matplotlib.rcParams["axes.unicode_minus"] = False
    HAS_MATPLOTLIB = True

    def _preview_toolitems():
        drop = {"Back", "Forward", "Subplots", "Save"}
        kept = []
        for item in NavigationToolbar2Tk.toolitems:
            if item[0] in drop:
                continue
            if item[0] is None:
                if kept and kept[-1][0] is not None:
                    kept.append(item)
                continue
            kept.append(item)
        while kept and kept[-1][0] is None:
            kept.pop()
        return tuple(kept)

    class _PreviewToolbar(NavigationToolbar2Tk):
        """绘图窗口只保留复位、平移和框选缩放。"""

        toolitems = _preview_toolitems()

except ImportError:  # pragma: no cover - 没装绘图库时只是不能预览
    patches = None
    Figure = None
    FigureCanvasTkAgg = None
    NavigationToolbar2Tk = None
    HAS_MATPLOTLIB = False

from i18n import tr
from records import Graphic, PinInfo, Symbol

_BODY = "#3584e4"
_PIN = "#c01c28"
_HIGHLIGHT = "#e5a50a"
_TITLE_ABOVE = 10.0
_TITLE_BELOW = 30.0
# 名称锚点从外框往符号里面退这么多 mil，避免贴着边线。
_NAME_INSET = 50.0
# 文字高度按 mil 固定，放大缩小时和图形用同一个比例。
_SYMBOL_TEXT_MIL = 100.0
_PIN_NAME_MIL = 85.0
_TITLE_TEXT_MIL = 114.0


class _ModeColumn:
    def __init__(self, mode: int, title: str, width: float, pins, graphics, offsets, labels) -> None:
        self.mode = mode
        self.title = title
        self.width = width
        self.pins = pins
        self.graphics = graphics
        self.offsets = offsets
        self.labels = labels
        self.title_gap = 0.0
        self.origin_x = 0.0


def render_symbol(
    fig,
    symbol: Symbol,
    modes: list[int],
    *,
    highlight: tuple | None = None,
    label_as_target: bool = False,
    lang: str = "zh",
) -> None:
    fig.clear()
    axis = fig.add_subplot(111)
    if not modes:
        axis.axis("off")
        axis.text(0.5, 0.5, tr(lang, "no_modes"), ha="center", va="center")
        return
    columns = [
        _column_layout(symbol, mode, position, label_as_target=label_as_target, lang=lang)
        for position, mode in enumerate(modes)
    ]
    _place_columns(columns)
    axis.set_aspect("equal", adjustable="box")
    axis.grid(False)
    axis.tick_params(labelsize=8)
    axis.set_xlabel("mil")
    axis.set_ylabel("mil")
    for column in columns:
        _draw_column(axis, column, highlight)
    axis.relim()
    axis.autoscale_view()
    x0, x1 = axis.get_xlim()
    y0, y1 = axis.get_ylim()
    span_x = max(x1 - x0, 1.0)
    span_y = max(y1 - y0, 1.0)
    axis.set_xlim(x0 - span_x * 0.012, x1 + span_x * 0.012)
    axis.set_ylim(y0 - span_y * 0.02, y1 + span_y * 0.012)
    _scale_symbol_text(axis)
    _bind_text_scale(axis)


def _column_layout(symbol: Symbol, mode: int, position: int, *, label_as_target: bool, lang: str = "zh") -> _ModeColumn:
    pins = [pin for pin in symbol.pins() if pin.mode == mode and not pin.broken]
    graphics = symbol.graphics(mode)
    parts = _parts_in_mode(symbol, mode, pins, graphics)
    current = symbol.mode_name(mode)
    future = symbol.mode_name(position)
    if label_as_target and current != future:
        title = tr(lang, "mode_heading_arrow", future=future, current=current)
    else:
        title = current
    boxes = {part: _part_box(part, graphics, pins) for part in parts}
    widths = [box[2] - box[0] for box in boxes.values()]
    heights = [box[3] - box[1] for box in boxes.values()]
    typical_w = _median(widths) if widths else 200.0
    typical_h = _median(heights) if heights else 200.0
    part_gap = max(28.0, typical_h * 0.08)
    offsets: dict[int, tuple[float, float]] = {}
    labels: list[tuple[float, float, str]] = []
    cursor = -_TITLE_BELOW
    for part in parts:
        min_x, min_y, _max_x, max_y = boxes[part]
        offsets[part] = (-min_x, cursor - max_y)
        cursor -= max_y - min_y
        cursor -= part_gap
    width = max(widths) if widths else max(180.0, typical_w)
    if not parts:
        labels.append((0.0, cursor, tr(lang, "no_graphics")))
    column = _ModeColumn(mode, title, width, pins, graphics, offsets, labels)
    column.title_gap = _TITLE_ABOVE
    return column


def _parts_in_mode(symbol: Symbol, mode: int, pins: list[PinInfo], graphics: list[Graphic]) -> list[int]:
    parts = list(symbol.parts_for_mode(mode))
    known = set(parts)
    for part in sorted({pin.part_id for pin in pins} | {item.part_id for item in graphics}):
        if part not in known:
            parts.append(part)
            known.add(part)
    return parts


def _place_columns(columns: list[_ModeColumn]) -> None:
    widths = [column.width for column in columns if column.width > 0]
    gap = max(16.0, _median(widths) * 0.08) if widths else 24.0
    cursor = 0.0
    for index, column in enumerate(columns):
        column.origin_x = cursor
        cursor += column.width
        if index < len(columns) - 1:
            cursor += gap


def _draw_column(axis, column: _ModeColumn, highlight) -> None:
    title_x = column.origin_x + column.width / 2
    _mil_text(
        axis,
        title_x,
        0.0,
        column.title,
        _TITLE_TEXT_MIL,
        ha="center",
        va="bottom",
        color="#1a1a1a",
        clip_on=False,
    )
    axis.plot([title_x], [column.title_gap], linewidth=0, marker="None")
    for graphic in column.graphics:
        dx, dy = column.offsets.get(graphic.part_id, (0.0, 0.0))
        _draw_graphic(axis, graphic, column.origin_x + dx, dy)
    for pin in column.pins:
        dx, dy = column.offsets.get(pin.part_id, (0.0, 0.0))
        selected = _pin_highlighted(highlight, pin, column.mode)
        _draw_pin(axis, pin, column.origin_x + dx, dy, selected)
    for x, y, text in column.labels:
        _mil_text(
            axis,
            column.origin_x + x,
            y,
            text,
            _SYMBOL_TEXT_MIL,
            color="#666666",
            ha="left",
            va="top",
            clip_on=False,
        )
        axis.plot(column.origin_x + x, y, markersize=0)


def _pin_highlighted(highlight, pin: PinInfo, mode: int) -> bool:
    if not highlight or len(highlight) < 3:
        return False
    record = highlight[3] if len(highlight) > 3 else None
    if record is not None:
        return pin.record_index == record
    return (
        highlight[2] == pin.designator
        and (highlight[1] is None or highlight[1] == pin.part_id)
        and (highlight[0] is None or highlight[0] == mode)
    )


def pin_at(symbol: Symbol, modes: list[int], x: float, y: float, *, tolerance: float = 100.0) -> PinInfo | None:
    """返回离数据坐标最近、且在容差内的引脚。点在引脚线段或位号文字上都算命中。"""
    columns = [
        _column_layout(symbol, mode, position, label_as_target=False)
        for position, mode in enumerate(modes)
    ]
    _place_columns(columns)
    best: PinInfo | None = None
    best_distance = tolerance
    for column in columns:
        for pin in column.pins:
            dx, dy = column.offsets.get(pin.part_id, (0.0, 0.0))
            body_x = pin.x + column.origin_x + dx
            body_y = pin.y + dy
            end_x, end_y = _pin_end(pin)
            end_x += column.origin_x + dx
            end_y += dy
            distance = _distance_to_segment(x, y, body_x, body_y, end_x, end_y)
            if pin.designator:
                perp_x, perp_y = _perp(end_x - body_x, end_y - body_y)
                text_x = (body_x + end_x) / 2 + perp_x * 36
                text_y = (body_y + end_y) / 2 + perp_y * 36
                distance = min(distance, math.hypot(x - text_x, y - text_y))
            if distance < best_distance:
                best = pin
                best_distance = distance
    return best


def _distance_to_segment(px: float, py: float, x0: float, y0: float, x1: float, y1: float) -> float:
    dx = x1 - x0
    dy = y1 - y0
    length_sq = dx * dx + dy * dy
    if length_sq < 1e-9:
        return math.hypot(px - x0, py - y0)
    ratio = max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / length_sq))
    return math.hypot(px - (x0 + ratio * dx), py - (y0 + ratio * dy))


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def _part_box(part: int, graphics: list[Graphic], pins: list[PinInfo]):
    box = None
    for graphic in graphics:
        if graphic.part_id != part:
            continue
        box = _merge(box, *_graphic_box(graphic))
    for pin in pins:
        if pin.part_id != part:
            continue
        end_x, end_y = _pin_end(pin)
        box = _merge(box, pin.x, pin.y, end_x, end_y)
    if box is None:
        return (0.0, 0.0, 0.0, 0.0)
    return box


def _merge(box, x0: float, y0: float, x1: float, y1: float):
    left, right = min(x0, x1), max(x0, x1)
    bottom, top = min(y0, y1), max(y0, y1)
    if box is None:
        return (left, bottom, right, top)
    return (min(box[0], left), min(box[1], bottom), max(box[2], right), max(box[3], top))


def _graphic_box(graphic: Graphic):
    if graphic.points:
        box = None
        for x, y in graphic.points:
            box = _merge(box, x, y, x, y)
        return box
    radius = graphic.radius or 0.0
    secondary = graphic.secondary_radius if graphic.secondary_radius is not None else radius
    if graphic.kind in ("ellipse", "arc", "elliptical_arc"):
        return (graphic.x - radius, graphic.y - secondary, graphic.x + radius, graphic.y + secondary)
    corner_x = graphic.x if graphic.corner_x is None else graphic.corner_x
    corner_y = graphic.y if graphic.corner_y is None else graphic.corner_y
    return (graphic.x, graphic.y, corner_x, corner_y)


def _draw_graphic(axis, graphic: Graphic, dx: float, dy: float) -> None:
    x = graphic.x + dx
    y = graphic.y + dy
    if graphic.kind in ("polyline", "polygon") and len(graphic.points) >= 2:
        xs = [point[0] + dx for point in graphic.points]
        ys = [point[1] + dy for point in graphic.points]
        if graphic.kind == "polygon":
            axis.fill(xs, ys, facecolor="none", edgecolor=_BODY, linewidth=1.2, closed=True)
        else:
            axis.plot(xs, ys, color=_BODY, linewidth=1.2, solid_capstyle="round")
        return
    if graphic.kind == "line" and graphic.corner_x is not None and graphic.corner_y is not None:
        axis.plot([x, graphic.corner_x + dx], [y, graphic.corner_y + dy], color=_BODY, linewidth=1.2)
        return
    if graphic.kind in ("rectangle", "roundrect") and graphic.corner_x is not None and graphic.corner_y is not None:
        corner_x = graphic.corner_x + dx
        corner_y = graphic.corner_y + dy
        x0, x1 = (x, corner_x) if x <= corner_x else (corner_x, x)
        y0, y1 = (y, corner_y) if y <= corner_y else (corner_y, y)
        width, height = x1 - x0, y1 - y0
        if graphic.kind == "roundrect":
            radius_x = graphic.radius or 0.0
            radius_y = graphic.secondary_radius if graphic.secondary_radius is not None else radius_x
            rounding = max(0.0, min(radius_x, radius_y, width / 2 if width else 0, height / 2 if height else 0))
            axis.add_patch(
                patches.FancyBboxPatch(
                    (x0, y0),
                    width,
                    height,
                    boxstyle=patches.BoxStyle.Round(pad=0.0, rounding_size=rounding),
                    fill=False,
                    edgecolor=_BODY,
                    linewidth=1.2,
                )
            )
        else:
            axis.add_patch(
                patches.Rectangle((x0, y0), width, height, fill=False, edgecolor=_BODY, linewidth=1.2)
            )
        return
    if graphic.kind == "ellipse" and graphic.radius is not None and graphic.secondary_radius is not None:
        axis.add_patch(
            patches.Ellipse(
                (x, y),
                width=2 * graphic.radius,
                height=2 * graphic.secondary_radius,
                fill=False,
                edgecolor=_BODY,
                linewidth=1.2,
            )
        )
        return
    if graphic.kind in ("arc", "elliptical_arc") and graphic.radius is not None:
        secondary = graphic.secondary_radius if graphic.secondary_radius is not None else graphic.radius
        axis.add_patch(
            patches.Arc(
                (x, y),
                2 * graphic.radius,
                2 * secondary,
                angle=0.0,
                theta1=graphic.start_angle or 0.0,
                theta2=graphic.end_angle if graphic.end_angle is not None else (graphic.start_angle or 0.0) + 360.0,
                fill=False,
                edgecolor=_BODY,
                linewidth=1.2,
            )
        )
        return
    if graphic.kind == "text" and graphic.text:
        _mil_text(axis, x, y, graphic.text, _SYMBOL_TEXT_MIL, color=_BODY, ha="center", va="center")
        axis.plot(x, y, markersize=0)


def _draw_pin(axis, pin: PinInfo, dx: float, dy: float, selected: bool) -> None:
    color = _HIGHLIGHT if selected else _PIN
    width = 2.4 if selected else 1.3
    body_x = pin.x + dx
    body_y = pin.y + dy
    end_x, end_y = _pin_end(pin)
    end_x += dx
    end_y += dy
    axis.plot([body_x, end_x], [body_y, end_y], color=color, linewidth=width, solid_capstyle="round")
    if pin.designator:
        perp_x, perp_y = _perp(end_x - body_x, end_y - body_y)
        text_x = (body_x + end_x) / 2 + perp_x * 36
        text_y = (body_y + end_y) / 2 + perp_y * 36
        _mil_text(axis, text_x, text_y, pin.designator, _SYMBOL_TEXT_MIL, color="#222222", ha="center", va="center")
        axis.plot(text_x, text_y, markersize=0)
    if pin.name:
        name_x, name_y, ha, va = _name_layout(body_x, body_y, end_x, end_y)
        _mil_text(
            axis,
            name_x,
            name_y,
            pin.name,
            _PIN_NAME_MIL,
            color=_HIGHLIGHT if selected else _BODY,
            ha=ha,
            va=va,
        )
        axis.plot(name_x, name_y, markersize=0)


def _pin_end(pin: PinInfo) -> tuple[float, float]:
    delta_x, delta_y = _delta(pin.rotation, pin.length)
    return pin.x + delta_x, pin.y + delta_y


def _delta(rotation: int, length: float) -> tuple[float, float]:
    table = {
        0: (length, 0.0),
        90: (0.0, length),
        180: (-length, 0.0),
        270: (0.0, -length),
    }
    return table.get(int(rotation) % 360, (length, 0.0))


def _perp(delta_x: float, delta_y: float) -> tuple[float, float]:
    length = (delta_x * delta_x + delta_y * delta_y) ** 0.5
    if length < 1e-9:
        return 0.0, 1.0
    normal_x, normal_y = -delta_y / length, delta_x / length
    if normal_y < 0:
        normal_x, normal_y = -normal_x, -normal_y
    return normal_x, normal_y


def _name_layout(body_x: float, body_y: float, end_x: float, end_y: float):
    """引脚名称放在外框内侧，和边线留出固定空隙。空隙不随引脚长短变化。"""

    delta_x, delta_y = end_x - body_x, end_y - body_y
    length = (delta_x * delta_x + delta_y * delta_y) ** 0.5
    if length < 1e-9:
        return body_x, body_y, "center", "center"
    toward_x, toward_y = -delta_x / length, -delta_y / length
    name_x = body_x + toward_x * _NAME_INSET
    name_y = body_y + toward_y * _NAME_INSET
    if toward_x < -0.2:
        align = ("right", "center")
    elif toward_x > 0.2:
        align = ("left", "center")
    elif toward_y < -0.2:
        align = ("center", "top")
    elif toward_y > 0.2:
        align = ("center", "bottom")
    else:
        align = ("center", "center")
    return name_x, name_y, align[0], align[1]


def _mil_text(axis, x: float, y: float, text: str, size_mil: float, **kwargs):
    artist = axis.text(x, y, text, fontsize=8, **kwargs)
    artist._size_mil = size_mil
    return artist


def _scale_symbol_text(axis) -> None:
    """按当前视图的 mil→像素比例重设文字字号，使文字和图形一起缩放。"""

    figure = axis.figure
    if figure is None:
        return
    try:
        origin = axis.transData.transform((0.0, 0.0))
        unit = axis.transData.transform((0.0, 100.0))
    except (ValueError, RuntimeError):
        return
    pixels_per_mil = abs(unit[1] - origin[1]) / 100.0
    if pixels_per_mil < 1e-9:
        return
    dpi = float(figure.dpi or 100.0)
    for artist in axis.texts:
        size_mil = getattr(artist, "_size_mil", None)
        if not size_mil:
            continue
        artist.set_fontsize(max(1.0, size_mil * pixels_per_mil * 72.0 / dpi))


def _bind_text_scale(axis) -> None:
    def on_limit_change(changed) -> None:
        _scale_symbol_text(changed)

    axis.callbacks.connect("xlim_changed", on_limit_change)
    axis.callbacks.connect("ylim_changed", on_limit_change)


if HAS_MATPLOTLIB:
    import tkinter as tk
    from tkinter import ttk

    class ScrollFigure(ttk.Frame):
        """按符号实际大小显示。模式左右排列，超出窗口时可以滚动。"""

        _MIN_PX_PER_MIL = 0.11
        _MARGIN_LEFT = 0.48
        _MARGIN_RIGHT = 0.08
        _MARGIN_TOP = 0.08
        _MARGIN_BOTTOM = 0.34

        def __init__(self, master) -> None:
            super().__init__(master)
            self.fig = Figure(figsize=(7.6, 4.0), dpi=100)
            self._symbol: Symbol | None = None
            self._modes: list[int] = []
            self._kwargs: dict = {}
            self._shown = False
            self._resize_job = None
            self.canvas = tk.Canvas(self, highlightthickness=0, background="white")
            self.scroll = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
            self.vscroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
            self.canvas.configure(xscrollcommand=self.scroll.set, yscrollcommand=self.vscroll.set)
            self.inner = ttk.Frame(self.canvas)
            self.window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
            self.mpl = FigureCanvasTkAgg(self.fig, master=self.inner)
            widget = self.mpl.get_tk_widget()
            widget.pack(side="left", anchor="nw")
            widget.bind("<MouseWheel>", self._wheel)
            try:
                self.toolbar = _PreviewToolbar(self.mpl, self, pack_toolbar=False)
            except TypeError:
                self.toolbar = _PreviewToolbar(self.mpl, self)
            else:
                self.toolbar.pack(side="top", fill="x")
            message = getattr(self.toolbar, "_message_label", None)
            if message is not None:
                message.pack_forget()
            self.toolbar.set_message = lambda _text: None
            self.vscroll.pack(side="right", fill="y")
            self.scroll.pack(side="bottom", fill="x")
            self.canvas.pack(fill="both", expand=True)
            self.inner.bind("<Configure>", self._fit)
            self.canvas.bind("<Configure>", self._on_canvas_configure)
            self.canvas.bind("<MouseWheel>", self._wheel)
            self._pin_handler = None
            self.mpl.mpl_connect("button_press_event", self._on_pick)

        def set_pin_handler(self, handler) -> None:
            self._pin_handler = handler

        def _on_pick(self, event) -> None:
            if self._pin_handler is None or event.xdata is None or event.ydata is None:
                return
            if event.button not in (1, 3):
                return
            if event.button == 1 and getattr(self.toolbar, "mode", ""):
                return
            pin = None
            if self._symbol is not None and self._modes:
                pin = pin_at(self._symbol, self._modes, event.xdata, event.ydata)
            gui = getattr(event, "guiEvent", None)
            self._pin_handler(
                pin,
                event.button,
                getattr(gui, "x_root", None),
                getattr(gui, "y_root", None),
            )

        def show(self, symbol: Symbol | None, modes: list[int], **kwargs) -> None:
            self._shown = True
            self._symbol = symbol
            self._modes = list(modes)
            self._kwargs = kwargs
            self._render()

        def _render(self) -> None:
            self._paint()
            span = self._content_span()
            width, height = self._canvas_inches() if span is None else self._size_for_span(*span)
            self.fig.set_size_inches(width, height)
            self._paint()
            if span is not None:
                self._apply_margins()
            self._scale_drawn_text()
            self.mpl.draw()
            widget = self.mpl.get_tk_widget()
            widget.configure(
                height=int(self.fig.get_figheight() * self.fig.dpi),
                width=int(self.fig.get_figwidth() * self.fig.dpi),
            )
            self.after_idle(self._fit)

        def _paint(self) -> None:
            if self._symbol is None:
                self.fig.clear()
                axis = self.fig.add_subplot(111)
                axis.axis("off")
                axis.text(0.5, 0.5, tr(self._kwargs.get("lang", "zh"), "select_symbol"), ha="center", va="center")
                return
            try:
                render_symbol(self.fig, self._symbol, self._modes, **self._kwargs)
            except Exception as exc:  # noqa: BLE001 - 预览失败不应挡住编辑
                self.fig.clear()
                axis = self.fig.add_subplot(111)
                axis.axis("off")
                axis.text(0.5, 0.5, tr(self._kwargs.get("lang", "zh"), "cannot_draw", error=exc), ha="center", va="center", wrap=True)

        def _content_span(self) -> tuple[float, float] | None:
            if not self.fig.axes:
                return None
            axis = self.fig.axes[0]
            if not axis.axison:
                return None
            x0, x1 = axis.get_xlim()
            y0, y1 = axis.get_ylim()
            if x1 <= x0 or y1 <= y0:
                return None
            return (x1 - x0, y1 - y0)

        def _canvas_inches(self) -> tuple[float, float]:
            dpi = float(self.fig.dpi)
            canvas_w = self.canvas.winfo_width()
            canvas_h = self.canvas.winfo_height()
            if canvas_w < 80 or canvas_h < 80:
                return (7.6, 4.2)
            return (max(canvas_w - 4, 80) / dpi, max(canvas_h - 4, 80) / dpi)

        def _size_for_span(self, data_w: float, data_h: float) -> tuple[float, float]:
            dpi = float(self.fig.dpi)
            canvas_w = self.canvas.winfo_width()
            canvas_h = self.canvas.winfo_height()
            margin_w = (self._MARGIN_LEFT + self._MARGIN_RIGHT) * dpi
            margin_h = (self._MARGIN_TOP + self._MARGIN_BOTTOM) * dpi
            if canvas_w < 80 or canvas_h < 80:
                avail_w, avail_h = 720.0, 360.0
            else:
                avail_w = max(80.0, canvas_w - margin_w)
                avail_h = max(80.0, canvas_h - margin_h)
            fit = min(avail_w / data_w, avail_h / data_h)
            scale = max(self._MIN_PX_PER_MIL, fit)
            width = self._MARGIN_LEFT + self._MARGIN_RIGHT + data_w * scale / dpi
            height = self._MARGIN_TOP + self._MARGIN_BOTTOM + data_h * scale / dpi
            return width, height

        def _scale_drawn_text(self) -> None:
            if self.fig.axes:
                _scale_symbol_text(self.fig.axes[0])

        def _apply_margins(self) -> None:
            width = self.fig.get_figwidth()
            height = self.fig.get_figheight()
            if width <= 0 or height <= 0:
                return
            self.fig.subplots_adjust(
                left=self._MARGIN_LEFT / width,
                right=1.0 - self._MARGIN_RIGHT / width,
                top=1.0 - self._MARGIN_TOP / height,
                bottom=self._MARGIN_BOTTOM / height,
            )

        def _fit(self, _event=None) -> None:
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))

        def _on_canvas_configure(self, event) -> None:
            if not self._shown or event.width < 80 or event.height < 80:
                return
            if self._resize_job is not None:
                self.after_cancel(self._resize_job)
            self._resize_job = self.after(60, self._apply_resize)

        def _apply_resize(self) -> None:
            self._resize_job = None
            if not self._shown:
                return
            span = self._content_span()
            width, height = self._canvas_inches() if span is None else self._size_for_span(*span)
            if abs(width - self.fig.get_figwidth()) < 0.12 and abs(height - self.fig.get_figheight()) < 0.12:
                self._fit()
                return
            self._render()

        def _wheel(self, event) -> None:
            steps = int(-event.delta / 120) if event.delta else 0
            if steps == 0:
                return
            if event.state & 0x0001 or not self._overflows_vertically():
                self.canvas.xview_scroll(steps, "units")
            else:
                self.canvas.yview_scroll(steps, "units")

        def _overflows_vertically(self) -> bool:
            bbox = self.canvas.bbox("all")
            if bbox is None:
                return False
            return bbox[3] - bbox[1] > self.canvas.winfo_height() + 4
