"""多显示模式符号的引脚一致性检查。"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from records import PinInfo, Symbol, mode_letter

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


def check_symbol(symbol: Symbol) -> list[PinIssue]:
    """按显示模式检查引脚数量、名称、重复位号，以及隐藏引脚的名称。"""

    pins = [pin for pin in symbol.pins() if not pin.broken]
    modes = list(range(symbol.mode_count))
    issues: list[PinIssue] = []
    issues.extend(_empty_and_duplicates(symbol.name, modes, pins))
    issues.extend(_compare_modes(symbol.name, modes, pins))
    issues.extend(_hidden_names(symbol.name, pins))
    return issues


def pin_row_note(pins: list[PinInfo], mode_count: int, pin: PinInfo) -> str:
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
        notes.append(hidden_pin_rule())
    return "、".join(notes)


def _empty_and_duplicates(symbol: str, modes: list[int], pins: list[PinInfo]) -> list[PinIssue]:
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
                    message=f"模式 {mode_letter(mode)} 有 {empty} 个位号为空的引脚",
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
                        message=f"模式 {mode_letter(mode)} 的位号 {designator} 重复 {count} 次",
                    )
                )
    return issues


def _compare_modes(symbol: str, modes: list[int], pins: list[PinInfo]) -> list[PinIssue]:
    issues: list[PinIssue] = []
    counts = [sum(1 for pin in pins if pin.mode == mode) for mode in modes]
    if len(set(counts)) > 1:
        detail = "，".join(f"模式 {mode_letter(mode)} {count} 个" for mode, count in zip(modes, counts))
        issues.append(
            PinIssue(
                symbol=symbol,
                part_id=None,
                mode=None,
                kind=KIND_COUNT,
                message=f"引脚数量不一致：{detail}",
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
                    message=f"位号 {designator} 在{_mode_list(present)}有，在{_mode_list(missing)}没有",
                )
            )
        differing = {bundle for bundle in bundles if bundle}
        if len(differing) > 1:
            detail = "，".join(
                f"模式 {mode_letter(mode)} 为 {_show_names(bundle)}"
                for mode, bundle in zip(modes, bundles)
                if bundle
            )
            issues.append(
                PinIssue(
                    symbol=symbol,
                    part_id=None,
                    mode=None,
                    kind=KIND_NAME,
                    message=f"位号 {designator} 的名称不一致：{detail}",
                )
            )
    return issues


def _hidden_names(symbol: str, pins: list[PinInfo]) -> list[PinIssue]:
    issues: list[PinIssue] = []
    for pin in pins:
        if not pin.hidden or pin.name in HIDDEN_PIN_NAMES:
            continue
        place = f"模式 {mode_letter(pin.mode)}"
        if pin.designator:
            place += f" 位号 {pin.designator}"
        shown = pin.name if pin.name else "（空）"
        issues.append(
            PinIssue(
                symbol=symbol,
                part_id=None,
                mode=pin.mode,
                kind=KIND_HIDDEN,
                message=f"{place} 已隐藏，名称是「{shown}」，{hidden_pin_rule()}",
            )
        )
    return issues


def hidden_pin_rule() -> str:
    """根据 HIDDEN_PIN_NAMES 生成隐藏引脚的说明。"""

    return f"仅允许名称为{' '.join(HIDDEN_PIN_NAMES)}的引脚设置为不可见"


def _mode_list(modes: list[int]) -> str:
    return "、".join(f"模式 {mode_letter(mode)}" for mode in modes)


def _show_names(names: tuple[str, ...]) -> str:
    return "、".join(f"「{name}」" for name in names)


def _text_key(text: str) -> list[tuple[int, int] | tuple[int, str]]:
    import re

    key: list[tuple[int, int] | tuple[int, str]] = []
    for piece in re.split(r"(\d+)", text):
        if piece.isdigit():
            key.append((0, int(piece)))
        else:
            key.append((1, piece.casefold()))
    return key
