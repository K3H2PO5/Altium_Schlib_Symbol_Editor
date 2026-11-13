"""多显示模式符号的引脚一致性检查。"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from i18n import tr
from records import PinInfo, Symbol, default_mode_name

KIND_COUNT = "引脚数量"
KIND_NAME = "引脚名称"
KIND_DUP = "重复引脚"
KIND_HIDDEN = "隐藏引脚"
# 允许设为不可见的引脚名称。后续增减只改这里。
HIDDEN_PIN_NAMES = ("NC", "nc", ".n.c")


@dataclass(frozen=True)
class PinIssue:
    symbol: str
    part_id: int | None
    mode: int | None
    kind: str
    message: str


def check_symbol(symbol: Symbol, lang: str = "zh") -> list[PinIssue]:
    """按显示模式检查引脚数量、名称、重复位号，以及隐藏引脚的名称。"""

    pins = [pin for pin in symbol.pins() if not pin.broken]
    modes = list(range(symbol.mode_count))
    names = {index: symbol.mode_name(index) for index in modes}
    issues: list[PinIssue] = []
    issues.extend(_empty_and_duplicates(symbol.name, modes, pins, lang, names))
    issues.extend(_compare_modes(symbol.name, modes, pins, lang, names))
    issues.extend(_hidden_names(symbol.name, pins, lang, names))
    return issues


def pin_row_note(pins: list[PinInfo], mode_count: int, pin: PinInfo, lang: str = "zh") -> str:
    """引脚表里给当前行用的短标记。同一模式内不区分 Part。"""

    if pin.broken:
        return "无法解析"
    notes: list[str] = []
    if pin.designator == "":
        notes.append("位号为空")
    else:
        same = [item for item in pins if item.mode == pin.mode and item.designator == pin.designator]
        if len(same) > 1:
            notes.append("重复")
        bundles = [
            tuple(item.name for item in pins if item.mode == mode and item.designator == pin.designator)
            for mode in range(mode_count)
        ]
        if any(not bundle for bundle in bundles):
            notes.append("模式间缺失")
        elif len(set(bundles)) > 1:
            notes.append("名称不一致")
    if pin.hidden and pin.name not in HIDDEN_PIN_NAMES:
        notes.append(hidden_pin_rule(lang))
    return tr(lang, "detail_sep").join(notes) if lang == "en" else "、".join(notes)


def _mode_label(mode: int, names: dict[int, str]) -> str:
    return names.get(mode) or default_mode_name(mode)


def _empty_and_duplicates(
    symbol: str,
    modes: list[int],
    pins: list[PinInfo],
    lang: str,
    names: dict[int, str],
) -> list[PinIssue]:
    issues: list[PinIssue] = []
    for mode in modes:
        in_mode = [pin for pin in pins if pin.mode == mode]
        empty = sum(1 for pin in in_mode if pin.designator == "")
        if empty:
            issues.append(
                PinIssue(
                    symbol=symbol,
                    part_id=None,
                    mode=mode,
                    kind=KIND_NAME,
                    message=tr(lang, "empty_designators", name=_mode_label(mode, names), count=empty),
                )
            )
        counts = Counter(pin.designator for pin in in_mode if pin.designator != "")
        for designator, count in sorted(counts.items(), key=lambda item: item[0]):
            if count > 1:
                issues.append(
                    PinIssue(
                        symbol=symbol,
                        part_id=None,
                        mode=mode,
                        kind=KIND_DUP,
                        message=tr(lang, "duplicate_designator", name=_mode_label(mode, names), designator=designator, count=count),
                    )
                )
    return issues


def _compare_modes(
    symbol: str,
    modes: list[int],
    pins: list[PinInfo],
    lang: str,
    names: dict[int, str],
) -> list[PinIssue]:
    issues: list[PinIssue] = []
    counts = [sum(1 for pin in pins if pin.mode == mode) for mode in modes]
    if len(set(counts)) > 1:
        detail = tr(lang, "detail_sep").join(
            tr(lang, "mode_count_item", name=_mode_label(mode, names), count=count) for mode, count in zip(modes, counts)
        )
        issues.append(
            PinIssue(
                symbol=symbol,
                part_id=None,
                mode=None,
                kind=KIND_COUNT,
                message=tr(lang, "count_mismatch", detail=detail),
            )
        )

    designators = sorted({pin.designator for pin in pins if pin.designator != ""}, key=_text_key)
    for designator in designators:
        bundles = [
            tuple(pin.name for pin in pins if pin.mode == mode and pin.designator == designator)
            for mode in modes
        ]
        present = [mode for mode, bundle in zip(modes, bundles) if bundle]
        missing = [mode for mode, bundle in zip(modes, bundles) if not bundle]
        if present and missing:
            issues.append(
                PinIssue(
                    symbol=symbol,
                    part_id=None,
                    mode=None,
                    kind=KIND_NAME,
                    message=tr(
                        lang,
                        "designator_missing",
                        designator=designator,
                        present=_mode_list(present, lang, names),
                        missing=_mode_list(missing, lang, names),
                    ),
                )
            )
        differing = {bundle for bundle in bundles if bundle}
        if len(differing) > 1:
            detail = tr(lang, "detail_sep").join(
                tr(lang, "mode_names", name=_mode_label(mode, names), names=_show_names(bundle, lang))
                for mode, bundle in zip(modes, bundles)
                if bundle
            )
            issues.append(
                PinIssue(
                    symbol=symbol,
                    part_id=None,
                    mode=None,
                    kind=KIND_NAME,
                    message=tr(lang, "name_mismatch", designator=designator, detail=detail),
                )
            )
    return issues


def _hidden_names(symbol: str, pins: list[PinInfo], lang: str, names: dict[int, str]) -> list[PinIssue]:
    issues: list[PinIssue] = []
    for pin in pins:
        if not pin.hidden or pin.name in HIDDEN_PIN_NAMES:
            continue
        if pin.designator:
            place = tr(lang, "hidden_place_pin", name=_mode_label(pin.mode, names), designator=pin.designator)
        else:
            place = tr(lang, "hidden_place", name=_mode_label(pin.mode, names))
        shown = pin.name if pin.name else tr(lang, "empty_pin_name")
        issues.append(
            PinIssue(
                symbol=symbol,
                part_id=None,
                mode=pin.mode,
                kind=KIND_HIDDEN,
                message=tr(lang, "hidden_message", place=place, shown=shown, rule=hidden_pin_rule(lang)),
            )
        )
    return issues


def hidden_pin_rule(lang: str = "zh") -> str:
    """根据 HIDDEN_PIN_NAMES 生成隐藏引脚的说明。"""

    return tr(lang, "hidden_rule", names=" ".join(HIDDEN_PIN_NAMES))


def _mode_list(modes: list[int], lang: str, names: dict[int, str]) -> str:
    return tr(lang, "list_sep").join(tr(lang, "mode_word", name=_mode_label(mode, names)) for mode in modes)


def _show_names(names: tuple[str, ...], lang: str) -> str:
    return tr(lang, "list_sep").join(tr(lang, "quote", name=name) for name in names)


def _text_key(text: str) -> list[tuple[int, int] | tuple[int, str]]:
    import re

    key: list[tuple[int, int] | tuple[int, str]] = []
    for piece in re.split(r"(\d+)", text):
        if piece.isdigit():
            key.append((0, int(piece)))
        else:
            key.append((1, piece.casefold()))
    return key
