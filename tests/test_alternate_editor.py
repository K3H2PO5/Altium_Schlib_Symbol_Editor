"""显示模式复制/删除、引脚改名和一致性检查。"""

from __future__ import annotations

import itertools
import struct
import tempfile
import unittest
import zlib
from datetime import datetime
from pathlib import Path

from issues import KIND_COUNT, KIND_DUP, KIND_HIDDEN, KIND_NAME, check_symbol
from library import history_backup_path, load_library, schlib_files
from records import (
    Record,
    SchlibFormatError,
    Symbol,
    decode_mode,
    describe_moves,
    is_pin,
    entry_pin_index,
    mode_letter,
    parse_pin_fields,
    pintext_fingerprint,
    plan_moves,
    set_pin_mode,
    set_text_mode,
)

_TEST_SCHLIB = Path(r"E:\Python\Altium_SchLib_Version_Management\DB_IC_Test.SchLib")
_PIN = bytes.fromhex(
    "0200000000010001000000000001043a14000000fcfeff00000005555342442d0138000000"
)
_RECT = (
    b"|RECORD=14|IsNotAccesible=T|IndexInSheet=5|OwnerPartId=1|OwnerPartDisplayMode=1"
    b"|Location.Y=-350|Corner.X=120|UniqueID=SSXBHVYE\x00"
)


def _pascal(text: str) -> bytes:
    raw = text.encode("ascii")
    return bytes((len(raw),)) + raw


def _make_pin(mode: int, part: int, designator: str, name: str, y: int = 0) -> bytes:
    payload = bytearray(26)
    payload[0] = 2
    payload[5] = part
    struct.pack_into("<H", payload, 6, mode << 8)
    payload[15] = 0x38
    struct.pack_into("<h", payload, 16, 20)
    struct.pack_into("<h", payload, 20, y // 10)
    return bytes(payload) + _pascal(name) + _pascal(designator) + b"\x00\x00\x00"


def _make_rect(mode: int, part: int, uid: str, sheet: int, corner: int) -> bytes:
    mode_field = f"|OwnerPartDisplayMode={mode}" if mode else ""
    text = (
        f"|RECORD=14|IndexInSheet={sheet}|OwnerPartId={part}{mode_field}"
        f"|Location.X=0|Location.Y=0|Corner.X={corner}|Corner.Y=10|UniqueID={uid}\x00"
    )
    return text.encode("ascii")


def _make_entry(label: str, marker: bytes) -> bytes:
    blob = zlib.compress(marker)
    payload = bytes((0xD0, len(label))) + label.encode("ascii") + struct.pack("<I", len(blob)) + blob
    return struct.pack("<HH", len(payload), 0x0100) + payload


def _demo(modes: int = 3, parts: int = 2) -> Symbol:
    records = [
        Record(
            0,
            (
                f"|RECORD=1|LibReference=DEMO|PartCount={parts + 1}|DisplayModeCount={modes}"
                f"|OwnerPartId=-1|AllPinCount={parts}\x00"
            ).encode("ascii"),
        )
    ]
    entries: list[bytes] = []
    labels = "ABCDE"
    sheet = 1
    for mode in range(modes):
        for part in range(1, parts + 1):
            records.append(Record(0, _make_rect(mode, part, f"{labels[mode]}{part}RECT00"[:8], sheet, 10 + mode)))
            sheet += 1
            name = f"{labels[mode]}{part}"
            records.append(Record(256, _make_pin(mode, part, str(part), name, y=-100 * part)))
            entries.append(_make_entry(str(len(entries)), name.encode("ascii")))
    header = f"|HEADER=PinTextData|Weight={len(entries)}\x00".encode("ascii")
    symbol = Symbol("DEMO", records, header, entries)
    symbol.mode_count = modes
    symbol.mark_saved()
    return symbol


def _corners(symbol: Symbol) -> dict[int, list[float]]:
    return {
        mode: sorted(
            item.corner_x
            for item in symbol.graphics(mode)
            if item.kind == "rectangle" and item.corner_x is not None
        )
        for mode in range(symbol.mode_count)
    }


def _texts(symbol: Symbol) -> dict[int, list[str]]:
    return {
        mode: sorted(item.text for item in symbol.graphics(mode) if item.text)
        for mode in range(symbol.mode_count)
    }


def _pin_bodies(symbol: Symbol) -> dict[int, list[bytes]]:
    grouped: dict[int, list[bytes]] = {mode: [] for mode in range(symbol.mode_count)}
    for record in symbol.records:
        if not is_pin(record):
            continue
        body = bytearray(record.payload)
        body[6:8] = b"\x00\x00"
        grouped[decode_mode(record.payload)].append(bytes(body))
    return {mode: sorted(items) for mode, items in grouped.items()}


def _names(symbol: Symbol) -> dict[int, list[tuple[int, str, str]]]:
    return {
        mode: sorted((pin.part_id, pin.designator, pin.name) for pin in symbol.pins() if pin.mode == mode)
        for mode in range(symbol.mode_count)
    }


class FolderTests(unittest.TestCase):
    def test_schlib_files_stay_in_the_folder(self) -> None:
        import tempfile

        folder = Path(tempfile.mkdtemp())
        (folder / "A.SchLib").write_bytes(b"x")
        (folder / "b.schlib").write_bytes(b"x")
        (folder / "notes.txt").write_bytes(b"x")
        nested = folder / "sub"
        nested.mkdir()
        (nested / "C.SchLib").write_bytes(b"x")
        self.assertEqual([path.name for path in schlib_files(folder)], ["A.SchLib", "b.schlib"])


class BackupPathTests(unittest.TestCase):
    def test_backup_goes_to_history_with_second_timestamp(self) -> None:
        when = datetime(2026, 11, 11, 16, 59, 30)
        with tempfile.TemporaryDirectory() as folder:
            library = Path(folder) / "Demo.SchLib"
            backup = history_backup_path(library, when)
            self.assertEqual(backup.parent, library.parent / "History")
            self.assertEqual(backup.name, "Demo_20261111_165930.SchLib")
            backup.parent.mkdir()
            backup.write_bytes(b"x")
            again = history_backup_path(library, when)
            self.assertEqual(again.name, "Demo_20261111_165930_2.SchLib")


class FormatTests(unittest.TestCase):
    def test_mode_letters(self) -> None:
        self.assertEqual([mode_letter(index) for index in range(4)], ["A", "B", "C", "D"])
        self.assertEqual(mode_letter(26), "AA")

    def test_real_pin_bytes(self) -> None:
        fields = parse_pin_fields(_PIN)
        self.assertEqual(fields.designator, b"8")
        self.assertEqual(fields.name, b"USBD-")
        self.assertEqual(fields.x, 0)
        self.assertEqual(fields.y, -2600)
        self.assertEqual(fields.rotation, 180)
        self.assertEqual(fields.length, 200)
        updated = set_pin_mode(_PIN, 2)
        self.assertEqual(updated[6:8], b"\x00\x02")
        self.assertEqual(parse_pin_fields(updated).name, b"USBD-")

    def test_clear_display_mode_field(self) -> None:
        cleared = set_text_mode(_RECT, 0)
        self.assertNotIn(b"OwnerPartDisplayMode", cleared)
        self.assertIn(b"|OwnerPartId=1|Location.Y=-350|", cleared)
        restored = set_text_mode(cleared, 2)
        self.assertIn(b"|OwnerPartId=1|OwnerPartDisplayMode=2|Location.Y=-350|", restored)


class OrderTests(unittest.TestCase):
    def test_short_swap_matches_copy_then_delete(self) -> None:
        self.assertEqual(plan_moves([0, 2, 1]), [1])
        text = "\n".join(describe_moves(3, [1]))
        self.assertIn("新建模式 D，复制模式 B", text)
        self.assertIn("删除模式 B", text)
        self.assertIn("B（原来的 C）", text)
        self.assertIn("C（原来的 B）", text)

    def test_every_small_permutation_is_reachable(self) -> None:
        for count in (1, 2, 3, 4):
            for perm in itertools.permutations(range(count)):
                current = list(range(count))
                for index in plan_moves(list(perm)):
                    current.append(current.pop(index))
                self.assertEqual(current, list(perm))

    def test_swap_keeps_parts_together_and_pintext(self) -> None:
        symbol = _demo()
        before_names = _names(symbol)
        before_corners = _corners(symbol)
        before_prints = [pintext_fingerprint(entry) for entry in symbol.pintext_entries or []]
        symbol.apply_order([0, 2, 1])
        self.assertEqual(symbol.mode_count, 3)
        self.assertEqual(_names(symbol)[0], before_names[0])
        self.assertEqual(_names(symbol)[1], before_names[2])
        self.assertEqual(_names(symbol)[2], before_names[1])
        self.assertEqual(_corners(symbol)[0], before_corners[0])
        self.assertEqual(_corners(symbol)[1], before_corners[2])
        self.assertEqual(_corners(symbol)[2], before_corners[1])
        self.assertEqual(len(symbol.pintext_entries or []), symbol.pin_record_count())
        after = symbol.pins()
        prints = {
            (pin.mode, pin.part_id, pin.designator): pintext_fingerprint(symbol.pintext_entries[index])
            for index, pin in enumerate(after)
        }
        # 原来的模式 0 是前两个引脚，模式 2 是最后两个，对调后模式 1 应带着原来模式 2 的文本。
        self.assertEqual(prints[(0, 1, "1")], before_prints[0])
        self.assertEqual(prints[(1, 1, "1")], before_prints[4])
        self.assertEqual(prints[(2, 1, "1")], before_prints[2])
        again = Symbol.parse("DEMO", symbol.serialize_data(), symbol.serialize_pintext())
        self.assertEqual(_names(again), _names(symbol))
        self.assertTrue(symbol.dirty)

    def test_identity_order_does_not_rewrite(self) -> None:
        symbol = _demo(modes=2, parts=1)
        before = symbol.serialize_data()
        self.assertEqual(symbol.apply_order([0, 1]), [])
        self.assertEqual(symbol.serialize_data(), before)
        self.assertFalse(symbol.dirty)


class EditAndCheckTests(unittest.TestCase):
    def test_rename_one_mode_or_all_modes(self) -> None:
        symbol = _demo(modes=3, parts=1)
        pin = next(item for item in symbol.pins() if item.mode == 0)
        one = symbol.rename_pin(pin, "9", "NEW", all_modes=False)
        self.assertEqual(one.updated, 1)
        self.assertEqual(_names(symbol)[0], [(1, "9", "NEW")])
        self.assertEqual(_names(symbol)[1], [(1, "1", "B1")])
        again = next(item for item in symbol.pins() if item.mode == 1 and item.designator == "1")
        all_modes = symbol.rename_pin(again, "9", "ALL", all_modes=True)
        self.assertEqual(all_modes.updated, 2)
        self.assertEqual(_names(symbol)[0], [(1, "9", "NEW")])
        self.assertEqual(_names(symbol)[1], [(1, "9", "ALL")])
        self.assertEqual(_names(symbol)[2], [(1, "9", "ALL")])

    def test_reports_count_name_and_duplicates(self) -> None:
        symbol = _demo(modes=2, parts=1)
        first = next(item for item in symbol.pins() if item.mode == 1)
        symbol.rename_pin(first, "1", "OTHER", all_modes=False)
        extra = Record(256, _make_pin(0, 1, "1", "DUP", y=-400))
        symbol.records.append(extra)
        issues = check_symbol(symbol)
        kinds = {issue.kind for issue in issues}
        self.assertIn(KIND_COUNT, kinds)
        self.assertIn(KIND_NAME, kinds)
        self.assertIn(KIND_DUP, kinds)
        self.assertTrue(any("名称不一致" in issue.message for issue in issues))

    def test_checks_ignore_part_and_flag_hidden_names(self) -> None:
        symbol = _demo(modes=2, parts=2)
        symbol.records.append(Record(256, _make_pin(0, 2, "1", "OTHER", y=-400)))
        issues = check_symbol(symbol)
        self.assertTrue(all(issue.part_id is None for issue in issues))
        self.assertFalse(any("Part" in issue.message for issue in issues))
        self.assertTrue(any(issue.kind == KIND_DUP and "模式 A 的位号 1 重复" in issue.message for issue in issues))
        self.assertTrue(any(issue.kind == KIND_COUNT and issue.message.startswith("引脚数量不一致") for issue in issues))

        hidden = _demo(modes=1, parts=1)
        pin = hidden.pins()[0]
        record = hidden.records[pin.record_index]
        payload = bytearray(record.payload)
        payload[15] |= 0x04
        record.payload = bytes(payload)
        flagged = [issue for issue in check_symbol(hidden) if issue.kind == KIND_HIDDEN]
        self.assertEqual(len(flagged), 1)
        self.assertIn("仅允许名称为NC nc .n.c的引脚设置为不可见", flagged[0].message)
        for allowed in ("NC", "nc", ".n.c"):
            current = next(item for item in hidden.pins() if item.record_index == pin.record_index)
            hidden.rename_pin(current, current.designator, allowed, all_modes=False)
            self.assertFalse(any(issue.kind == KIND_HIDDEN for issue in check_symbol(hidden)))
        current = next(item for item in hidden.pins() if item.record_index == pin.record_index)
        hidden.rename_pin(current, current.designator, "Nc", all_modes=False)
        self.assertTrue(any(issue.kind == KIND_HIDDEN for issue in check_symbol(hidden)))

    def test_reject_empty_designator(self) -> None:
        symbol = _demo(modes=2, parts=1)
        pin = symbol.pins()[0]
        with self.assertRaises(SchlibFormatError):
            symbol.rename_pin(pin, "  ", "X", all_modes=False)


class RealLibraryTests(unittest.TestCase):
    def setUp(self) -> None:
        if not _TEST_SCHLIB.is_file():
            self.skipTest("没有 DB_IC_Test.SchLib")

    def test_ch346_roundtrip_and_swap(self) -> None:
        library = load_library(_TEST_SCHLIB)
        self.assertEqual([symbol.name for symbol in library.symbols], ["I_USB_CH346"])
        symbol = library.symbols[0]
        self.assertEqual(symbol.mode_count, 3)
        self.assertFalse(symbol.dirty)
        before_names = _names(symbol)
        before_bodies = _pin_bodies(symbol)
        before_text = _texts(symbol)
        assert symbol.pintext_entries is not None
        before_prints = [pintext_fingerprint(entry) for entry in symbol.pintext_entries]
        by_pin = {
            (pin.mode, pin.part_id, pin.designator, pin.ordinal): before_prints[index]
            for index, pin in enumerate(symbol.pins())
        }
        symbol.apply_order([0, 2, 1])
        self.assertEqual(_names(symbol)[0], before_names[0])
        self.assertEqual(_names(symbol)[1], before_names[2])
        self.assertEqual(_names(symbol)[2], before_names[1])
        self.assertEqual(_pin_bodies(symbol)[0], before_bodies[0])
        self.assertEqual(_pin_bodies(symbol)[1], before_bodies[2])
        self.assertEqual(_pin_bodies(symbol)[2], before_bodies[1])
        self.assertEqual(_texts(symbol)[0], before_text[0])
        self.assertEqual(_texts(symbol)[1], before_text[2])
        self.assertEqual(_texts(symbol)[2], before_text[1])
        self.assertNotEqual(before_bodies[1], before_bodies[2])
        self.assertEqual(len(symbol.pintext_entries), symbol.pin_record_count())
        for index, pin in enumerate(symbol.pins()):
            source_mode = {0: 0, 1: 2, 2: 1}[pin.mode]
            expected = by_pin[(source_mode, pin.part_id, pin.designator, pin.ordinal)]
            self.assertEqual(pintext_fingerprint(symbol.pintext_entries[index]), expected)
        reparsed = Symbol.parse(symbol.name, symbol.serialize_data(), symbol.serialize_pintext())
        self.assertEqual(_names(reparsed), _names(symbol))
        self.assertFalse(library.path.with_suffix(library.path.suffix + ".bak").exists())


class DrawTests(unittest.TestCase):
    def test_modes_are_horizontal_and_parts_are_vertical(self) -> None:
        from matplotlib.figure import Figure

        from draw import render_symbol

        figure = Figure(figsize=(8, 4))
        render_symbol(figure, _demo(), [0, 1, 2], label_as_target=True)
        self.assertEqual(len(figure.axes), 1)
        texts = figure.axes[0].texts
        titles = sorted(
            (item for item in texts if item.get_text().startswith("模式 ")),
            key=lambda item: item.get_position()[0],
        )
        self.assertEqual(len(titles), 3)
        self.assertTrue(titles[0].get_text().startswith("模式 A"))
        self.assertTrue(titles[1].get_text().startswith("模式 B"))
        self.assertTrue(titles[2].get_text().startswith("模式 C"))
        self.assertLess(titles[0].get_position()[0], titles[1].get_position()[0])
        self.assertLess(titles[1].get_position()[0], titles[2].get_position()[0])
        self.assertAlmostEqual(titles[0].get_position()[1], titles[1].get_position()[1], delta=1)
        self.assertTrue(all(item.get_ha() == "center" for item in titles))
        self.assertFalse(any(item.get_text().startswith("Part ") for item in texts))
        boundary = (titles[0].get_position()[0] + titles[1].get_position()[0]) / 2
        left = [item for item in texts if item.get_position()[0] < boundary]
        upper = next(item for item in left if item.get_text() == "A1")
        lower = next(item for item in left if item.get_text() == "A2")
        self.assertGreater(upper.get_position()[1], lower.get_position()[1])

    def test_delete_mode_shifts_later_modes(self) -> None:
        symbol = _demo()
        before_names = _names(symbol)
        before_corners = _corners(symbol)
        assert symbol.pintext_entries is not None
        prints = [pintext_fingerprint(entry) for entry in symbol.pintext_entries]
        by_pin = {
            (pin.mode, pin.part_id, pin.designator, pin.ordinal): prints[index]
            for index, pin in enumerate(symbol.pins())
        }
        symbol.delete_mode(1)
        self.assertEqual(symbol.mode_count, 2)
        self.assertEqual(_names(symbol)[0], before_names[0])
        self.assertEqual(_names(symbol)[1], before_names[2])
        self.assertEqual(_corners(symbol)[0], before_corners[0])
        self.assertEqual(_corners(symbol)[1], before_corners[2])
        self.assertEqual(len(symbol.pintext_entries), symbol.pin_record_count())
        for index, pin in enumerate(symbol.pins()):
            source_mode = {0: 0, 1: 2}[pin.mode]
            expected = by_pin[(source_mode, pin.part_id, pin.designator, pin.ordinal)]
            self.assertEqual(pintext_fingerprint(symbol.pintext_entries[index]), expected)
        self.assertTrue(symbol.dirty)
        symbol.delete_mode(0)
        self.assertEqual(symbol.mode_count, 1)
        with self.assertRaises(SchlibFormatError):
            symbol.delete_mode(0)

    def test_sparse_pintext_follows_deleted_mode(self) -> None:
        path = Path(r"e:\Python\Altium_Python\DB_Connector.SchLib")
        if not path.is_file():
            self.skipTest("没有 DB_Connector.SchLib")
        library = load_library(path)
        symbol = next((item for item in library.symbols if item.name == "Circular_Pin_2P"), None)
        if symbol is None:
            self.skipTest("Circular_Pin_2P 已不是多显示模式符号")
        data = symbol.serialize_data()
        pintext = symbol.serialize_pintext()
        extra = {stream.name: stream.serialize() for stream in symbol.side_streams}
        self.assertEqual(sorted(extra), ["PinFunctionData", "PinVerticalMarginData"])
        self.assertEqual(_entry_indexes(symbol.pintext_entries), [4, 5])

        removed_alternate = Symbol.parse(symbol.name, data, pintext, extra)
        removed_alternate.delete_mode(1)
        self.assertEqual(removed_alternate.mode_count, 1)
        self.assertEqual([(pin.designator, pin.x, pin.y) for pin in removed_alternate.pins()], [("1", 600, -100), ("3", 0, -500)])
        self.assertEqual(_entry_indexes(removed_alternate.pintext_entries), [0, 1])
        margin = next(stream for stream in removed_alternate.side_streams if stream.name == "PinVerticalMarginData")
        function = next(stream for stream in removed_alternate.side_streams if stream.name == "PinFunctionData")
        self.assertEqual(_entry_indexes(margin.entries), [0, 1])
        self.assertEqual(_entry_indexes(function.entries), [1])

        removed_normal = Symbol.parse(symbol.name, data, pintext, extra)
        removed_normal.delete_mode(0)
        self.assertEqual(removed_normal.mode_count, 1)
        self.assertEqual([pin.designator for pin in removed_normal.pins()], ["1", "2", "3", "4"])
        self.assertEqual(removed_normal.pintext_entries, [])
        for stream in removed_normal.side_streams:
            self.assertEqual(stream.entries, [])

        reordered = Symbol.parse(symbol.name, data, pintext, extra)
        reordered.apply_order([1, 0])
        moved = [index for index, pin in enumerate(reordered.pins()) if pin.mode == 1]
        self.assertEqual(_entry_indexes(reordered.pintext_entries), moved)


def _entry_indexes(entries) -> list[int]:
    return [entry_pin_index(entry) for entry in entries or []]


if __name__ == "__main__":
    unittest.main()
