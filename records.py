"""SchLib Data 流里的显示模式、图元和引脚。

显示模式不是单独的目录，而是写在每条图元和引脚上的序号。
Altium 不能直接交换两个模式，只能把一个模式复制到末尾，再删除旧模式，
序号比它大的模式会自动减一。对调 A B C 里的 B 和 C，就是新建 D（复制 B）再删除 B。
"""

from __future__ import annotations

import random
import string
import struct
from collections import deque
from dataclasses import dataclass

_PIN_KIND = 256
_TEXT_KIND = 0


class SchlibFormatError(ValueError):
    """SchLib 记录无法按当前格式读写。"""


class Record:
    def __init__(self, kind: int, payload: bytes) -> None:
        self.kind = kind
        self.payload = payload


@dataclass(frozen=True)
class PinFields:
    name: bytes
    designator: bytes
    name_at: int
    designator_end: int
    x: int
    y: int
    rotation: int
    length: int
    hidden: bool


@dataclass(frozen=True)
class PinInfo:
    mode: int
    part_id: int
    designator: str
    name: str
    x: int
    y: int
    rotation: int
    length: int
    hidden: bool
    record_index: int
    ordinal: int
    broken: bool


@dataclass(frozen=True)
class Graphic:
    part_id: int
    kind: str
    x: float
    y: float
    corner_x: float | None = None
    corner_y: float | None = None
    radius: float | None = None
    secondary_radius: float | None = None
    start_angle: float | None = None
    end_angle: float | None = None
    points: tuple[tuple[float, float], ...] = ()
    text: str = ""


@dataclass(frozen=True)
class EditResult:
    updated: int
    notes: tuple[str, ...]


def mode_letter(index: int) -> str:
    """0→A，1→B，26→AA。和界面里说的模式 A/B/C 一致。"""

    number = index + 1
    chars: list[str] = []
    while number:
        number, remainder = divmod(number - 1, 26)
        chars.append(chr(ord("A") + remainder))
    return "".join(reversed(chars))


def iter_stream(data: bytes):
    offset = 0
    total = len(data)
    while offset + 4 <= total:
        length, kind = struct.unpack_from("<HH", data, offset)
        end = offset + 4 + length
        if length == 0 or end > total:
            raise SchlibFormatError(
                f"记录长度无效: offset={offset} header=0x{int.from_bytes(data[offset:offset + 4], 'little'):08X}"
            )
        yield kind, data[offset + 4 : end]
        offset = end
    if offset != total:
        raise SchlibFormatError(f"记录流末尾有 {total - offset} 字节无法解析")


def serialize_records(records: list[Record]) -> bytes:
    output = bytearray()
    for record in records:
        if len(record.payload) > 0xFFFF:
            raise SchlibFormatError("记录超过 65535 字节")
        output += struct.pack("<HH", len(record.payload), record.kind)
        output += record.payload
    return bytes(output)


def is_pin(record: Record) -> bool:
    return record.kind == _PIN_KIND and len(record.payload) >= 8 and record.payload[0] == 2


def record_number(payload: bytes) -> int | None:
    marker = b"|RECORD="
    if not payload.startswith(marker):
        return None
    end = payload.find(b"|", len(marker))
    if end < 0:
        return None
    try:
        return int(payload[len(marker) : end])
    except ValueError:
        return None


def field_span(payload: bytes, key: str) -> tuple[int, int, int] | None:
    """返回字段的起始竖线、值起点、值终点。终点落在下一个竖线或 NUL 上。"""

    marker = b"|" + key.encode("ascii") + b"="
    start = payload.find(marker)
    if start < 0:
        return None
    value_start = start + len(marker)
    value_end = value_start
    while value_end < len(payload) and payload[value_end] not in (ord("|"), 0):
        value_end += 1
    return start, value_start, value_end


def get_field(payload: bytes, key: str) -> str | None:
    span = field_span(payload, key)
    if span is None:
        return None
    raw = payload[span[1] : span[2]]
    try:
        return raw.decode("ascii")
    except UnicodeDecodeError:
        return None


def set_field(
    payload: bytes,
    key: str,
    value: str | None,
    *,
    insert_after: str | None = None,
) -> bytes:
    """写入或删除 `|key=value`。value 为 None 时删除该字段。"""

    span = field_span(payload, key)
    if value is None:
        if span is None:
            return payload
        start, _value_start, value_end = span
        return payload[:start] + payload[value_end:]
    encoded = value.encode("ascii")
    if span is not None:
        _start, value_start, value_end = span
        if payload[value_start:value_end] == encoded:
            return payload
        return payload[:value_start] + encoded + payload[value_end:]
    addition = b"|" + key.encode("ascii") + b"=" + encoded
    if insert_after:
        after = field_span(payload, insert_after)
        if after is not None:
            position = after[2]
            return payload[:position] + addition + payload[position:]
    if payload.endswith(b"\x00"):
        return payload[:-1] + addition + b"\x00"
    return payload + addition


def owner_part_id(payload: bytes) -> int | None:
    value = get_field(payload, "OwnerPartId")
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def text_mode(payload: bytes) -> int | None:
    """图元所属显示模式。元件级参数返回 None。省略模式字段时，Part>=1 视为模式 0。"""

    if record_number(payload) == 1:
        return None
    mode_text = get_field(payload, "OwnerPartDisplayMode")
    if mode_text is not None:
        try:
            return int(mode_text)
        except ValueError:
            return None
    part = owner_part_id(payload)
    if part is not None and part >= 1:
        return 0
    return None


def set_text_mode(payload: bytes, mode: int) -> bytes:
    if mode <= 0:
        return set_field(payload, "OwnerPartDisplayMode", None)
    return set_field(
        payload,
        "OwnerPartDisplayMode",
        str(mode),
        insert_after="OwnerPartId",
    )


def decode_mode(payload: bytes) -> int:
    if len(payload) < 8:
        raise SchlibFormatError("引脚记录过短，读不到显示模式")
    value = struct.unpack_from("<H", payload, 6)[0]
    low = payload[6]
    if low == 0 and value % 256 == 0:
        return value // 256
    if value <= 3:
        return value
    if value == 256:
        high = payload[7]
        return high if high <= 3 else 1
    return 0


def set_pin_mode(payload: bytes, mode: int) -> bytes:
    if mode < 0 or mode > 255:
        raise SchlibFormatError(f"显示模式 {mode} 超出范围")
    current = decode_mode(payload)
    if current == mode:
        return payload
    data = bytearray(payload)
    value = struct.unpack_from("<H", data, 6)[0]
    low = data[6]
    if low == 0 and value % 256 == 0:
        struct.pack_into("<H", data, 6, mode << 8)
    elif value <= 3:
        struct.pack_into("<H", data, 6, mode)
    else:
        struct.pack_into("<H", data, 6, mode << 8)
    return bytes(data)


def owned_mode(record: Record) -> int | None:
    if is_pin(record):
        return decode_mode(record.payload)
    if record.kind != _TEXT_KIND:
        return None
    return text_mode(record.payload)


def _read_pascal(buf: bytes, index: int) -> tuple[bytes, int]:
    if index >= len(buf):
        raise SchlibFormatError("Pascal 字符串超出引脚记录末尾")
    size = buf[index]
    end = index + 1 + size
    if end > len(buf):
        raise SchlibFormatError(f"Pascal 字符串需要 {size} 字节，剩余 {len(buf) - index - 1}")
    return buf[index + 1 : end], end


def parse_pin_fields(payload: bytes) -> PinFields:
    """引脚 name / designator 在 description 之后，坐标也随 description 长度后移。"""

    if len(payload) < 13 or payload[0] != 2:
        raise SchlibFormatError("不是二进制引脚记录")
    index = 12
    _description, index = _read_pascal(payload, index)
    if index + 13 > len(payload):
        raise SchlibFormatError("引脚固定字段被截断")
    flags = payload[index + 2]
    length = struct.unpack_from("<h", payload, index + 3)[0] * 10
    x = struct.unpack_from("<h", payload, index + 5)[0] * 10
    y = struct.unpack_from("<h", payload, index + 7)[0] * 10
    index += 13
    name_at = index
    name, index = _read_pascal(payload, index)
    designator, designator_end = _read_pascal(payload, index)
    return PinFields(
        name=name,
        designator=designator,
        name_at=name_at,
        designator_end=designator_end,
        x=x,
        y=y,
        rotation=(flags & 0x03) * 90,
        length=length,
        hidden=bool(flags & 0x04),
    )


def encode_pascal(text: bytes) -> bytes:
    if len(text) > 255:
        raise SchlibFormatError(f"引脚字符串有 {len(text)} 字节，超过 255")
    return bytes((len(text),)) + text


def replace_pin_strings(payload: bytes, name: bytes, designator: bytes) -> bytes:
    fields = parse_pin_fields(payload)
    updated = (
        payload[: fields.name_at]
        + encode_pascal(name)
        + encode_pascal(designator)
        + payload[fields.designator_end :]
    )
    checked = parse_pin_fields(updated)
    if checked.name != name or checked.designator != designator:
        raise SchlibFormatError("写回引脚名称后校验失败")
    if decode_mode(updated) != decode_mode(payload) or updated[5] != payload[5]:
        raise SchlibFormatError("写回引脚名称时改动了模式或 Part")
    return updated


def decode_pin_text(raw: bytes) -> str:
    if raw.isascii():
        return raw.decode("ascii")
    for encoding in ("utf-8", "gbk"):
        try:
            text = raw.decode(encoding)
        except UnicodeError:
            continue
        if text.encode(encoding) == raw:
            return text
    return raw.decode("latin-1")


def encode_pin_text(text: str, original: bytes) -> bytes:
    if text.isascii():
        return text.encode("ascii")
    for encoding in ("utf-8", "gbk"):
        try:
            if not original.isascii() and original.decode(encoding).encode(encoding) == original:
                return text.encode(encoding)
        except UnicodeError:
            continue
    try:
        return text.encode("gbk")
    except UnicodeError as exc:
        raise SchlibFormatError("位号或名称包含无法用 GBK 保存的字符") from exc


def parse_pintext(raw: bytes) -> tuple[bytes, list[bytes]]:
    if len(raw) < 4:
        raise SchlibFormatError("PinTextData 太短")
    header_len = struct.unpack_from("<I", raw, 0)[0]
    header_end = 4 + header_len
    if header_len <= 0 or header_end > len(raw):
        raise SchlibFormatError("PinTextData 文件头长度无效")
    header = raw[4:header_end]
    entries: list[bytes] = []
    offset = header_end
    while offset + 4 <= len(raw):
        length, _kind = struct.unpack_from("<HH", raw, offset)
        end = offset + 4 + length
        if length == 0 or end > len(raw):
            raise SchlibFormatError(f"PinTextData 项长度无效: offset={offset}")
        entries.append(raw[offset:end])
        offset = end
    if offset != len(raw):
        raise SchlibFormatError(f"PinTextData 末尾有 {len(raw) - offset} 字节无法解析")
    return header, entries


def pintext_fingerprint(entry: bytes) -> bytes:
    """去掉序号标签后的内容，用来确认复制引脚时文本样式还跟着原来的引脚。"""

    length, _kind = struct.unpack_from("<HH", entry, 0)
    payload = entry[4 : 4 + length]
    if len(payload) < 2 or payload[0] != 0xD0:
        raise SchlibFormatError("PinTextData 项不是预期的引脚文本")
    label_len = payload[1]
    if 2 + label_len > len(payload):
        raise SchlibFormatError("PinTextData 标签超出范围")
    return payload[2 + label_len :]


def set_entry_label(entry: bytes, label: str) -> bytes:
    length, kind = struct.unpack_from("<HH", entry, 0)
    if 4 + length != len(entry):
        raise SchlibFormatError("PinTextData 项长度不符")
    payload = entry[4:]
    if not payload or payload[0] != 0xD0:
        raise SchlibFormatError("PinTextData 项不是预期的引脚文本")
    old_len = payload[1]
    if 2 + old_len > len(payload):
        raise SchlibFormatError("PinTextData 标签超出范围")
    new = label.encode("ascii")
    if payload[2 : 2 + old_len] == new:
        return entry
    if len(new) > 255:
        raise SchlibFormatError("引脚序号过长")
    updated = bytes([0xD0, len(new)]) + new + payload[2 + old_len :]
    return struct.pack("<HH", len(updated), kind) + updated


def renumber_pintext(entries: list[bytes]) -> list[bytes]:
    return [set_entry_label(entry, str(index)) for index, entry in enumerate(entries)]


def entry_pin_index(entry: bytes) -> int:
    """附带引脚数据用序号指向 Data 流里的第几个引脚，可以只记录其中一部分。"""

    length, _kind = struct.unpack_from("<HH", entry, 0)
    payload = entry[4 : 4 + length]
    if len(payload) < 2 or payload[0] != 0xD0:
        raise SchlibFormatError("附带引脚数据项不是预期格式")
    label_len = payload[1]
    if 2 + label_len > len(payload):
        raise SchlibFormatError("附带引脚数据的序号超出范围")
    label = payload[2 : 2 + label_len]
    if not label.isdigit():
        raise SchlibFormatError("附带引脚数据的序号不是数字")
    return int(label)


def validate_pin_entries(entries: list[bytes], pin_count: int, stream_name: str) -> None:
    seen: set[int] = set()
    for entry in entries:
        try:
            index = entry_pin_index(entry)
        except SchlibFormatError as exc:
            raise SchlibFormatError(f"{stream_name}：{exc}") from exc
        if index in seen or not 0 <= index < pin_count:
            raise SchlibFormatError(f"{stream_name} 的引脚序号 {index} 超出 0 到 {max(pin_count - 1, 0)}")
        seen.add(index)


def remap_pin_entries(entries: list[bytes], mapping: dict[int, int]) -> list[bytes]:
    updated: list[bytes] = []
    for entry in entries:
        old = entry_pin_index(entry)
        if old not in mapping:
            continue
        updated.append(set_entry_label(entry, str(mapping[old])))
    return updated


def copy_indexed_entries(entries: list[bytes], source_indices: list[int], new_start: int) -> list[bytes]:
    by_index = {entry_pin_index(entry): entry for entry in entries}
    cloned = [
        set_entry_label(by_index[old], str(new_start + offset))
        for offset, old in enumerate(source_indices)
        if old in by_index
    ]
    return list(entries) + cloned


def set_weight(header: bytes, count: int) -> bytes:
    core = header[:-1] if header.endswith(b"\x00") else header
    tail = b"\x00" if header.endswith(b"\x00") else b""
    key = b"Weight="
    start = core.find(key)
    if start < 0:
        raise SchlibFormatError("PinTextData 缺少 Weight")
    value_start = start + len(key)
    value_end = value_start
    while value_end < len(core) and 48 <= core[value_end] <= 57:
        value_end += 1
    if value_end == value_start:
        raise SchlibFormatError("PinTextData 的 Weight 没有数字")
    updated = core[:value_start] + str(count).encode("ascii") + core[value_end:] + tail
    return header if updated == header else updated


def serialize_pintext(header: bytes, entries: list[bytes]) -> bytes:
    body = set_weight(header, len(entries))
    return struct.pack("<I", len(body)) + body + b"".join(entries)


def _parse_side_streams(blobs: dict[str, bytes]) -> list[SideStream]:
    streams: list[SideStream] = []
    for name, blob in blobs.items():
        try:
            header, entries = parse_pintext(blob)
        except SchlibFormatError as exc:
            streams.append(SideStream(name, error=str(exc), raw=blob))
            continue
        stream = SideStream(name, header, entries)
        if stream.serialize() != blob:
            streams.append(SideStream(name, error=f"{name} 重组后与原文件不一致", raw=blob))
            continue
        streams.append(stream)
    return streams


def parse_kv(payload: bytes) -> dict[str, str]:
    raw = payload.split(b"\x00", 1)[0]
    fields: dict[str, str] = {}
    for segment in raw.decode("latin-1").split("|"):
        if "=" not in segment:
            continue
        key, value = segment.split("=", 1)
        if key:
            fields[key] = value
    return fields


def _mil(fields: dict[str, str], key: str) -> float:
    def one(name: str) -> int:
        raw = fields.get(name, "")
        if not raw:
            return 0
        try:
            return int(float(raw))
        except ValueError:
            return 0

    return one(key) * 10 + one(f"{key}_Frac") / 10000.0


def _number(fields: dict[str, str], key: str, default: float = 0.0) -> float:
    raw = fields.get(key, "")
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _points(fields: dict[str, str]) -> tuple[tuple[float, float], ...]:
    try:
        count = int(fields.get("LocationCount", "0") or "0")
    except ValueError:
        count = 0
    return tuple((_mil(fields, f"X{index}"), _mil(fields, f"Y{index}")) for index in range(1, count + 1))


def _display_text(fields: dict[str, str]) -> str:
    if "%UTF8%Text" in fields:
        return decode_pin_text(fields["%UTF8%Text"].encode("latin-1"))
    if "Text" not in fields:
        return ""
    return decode_pin_text(fields["Text"].encode("latin-1"))


def graphic_from_payload(payload: bytes) -> Graphic | None:
    number = record_number(payload)
    if number not in (4, 6, 7, 8, 10, 11, 12, 13, 14):
        return None
    fields = parse_kv(payload)
    part = owner_part_id(payload)
    part_id = part if part is not None and part >= 1 else 1
    if number in (6, 7):
        points = _points(fields)
        if len(points) < 2:
            return None
        return Graphic(
            part_id=part_id,
            kind="polygon" if number == 7 else "polyline",
            x=points[0][0],
            y=points[0][1],
            points=points,
        )
    if number == 13:
        return Graphic(
            part_id=part_id,
            kind="line",
            x=_mil(fields, "Location.X"),
            y=_mil(fields, "Location.Y"),
            corner_x=_mil(fields, "Corner.X"),
            corner_y=_mil(fields, "Corner.Y"),
        )
    if number == 14:
        return Graphic(
            part_id=part_id,
            kind="rectangle",
            x=_mil(fields, "Location.X"),
            y=_mil(fields, "Location.Y"),
            corner_x=_mil(fields, "Corner.X"),
            corner_y=_mil(fields, "Corner.Y"),
        )
    if number == 10:
        return Graphic(
            part_id=part_id,
            kind="roundrect",
            x=_mil(fields, "Location.X"),
            y=_mil(fields, "Location.Y"),
            corner_x=_mil(fields, "Corner.X"),
            corner_y=_mil(fields, "Corner.Y"),
            radius=_mil(fields, "CornerXRadius"),
            secondary_radius=_mil(fields, "CornerYRadius"),
        )
    if number == 8:
        return Graphic(
            part_id=part_id,
            kind="ellipse",
            x=_mil(fields, "Location.X"),
            y=_mil(fields, "Location.Y"),
            radius=_mil(fields, "Radius"),
            secondary_radius=_mil(fields, "SecondaryRadius"),
        )
    if number == 12:
        return Graphic(
            part_id=part_id,
            kind="arc",
            x=_mil(fields, "Location.X"),
            y=_mil(fields, "Location.Y"),
            radius=_mil(fields, "Radius"),
            start_angle=_number(fields, "StartAngle"),
            end_angle=_number(fields, "EndAngle"),
        )
    if number == 11:
        secondary = _mil(fields, "SecondaryRadius") if (
            "SecondaryRadius" in fields or "SecondaryRadius_Frac" in fields
        ) else None
        radius = _mil(fields, "Radius")
        return Graphic(
            part_id=part_id,
            kind="elliptical_arc" if secondary is not None else "arc",
            x=_mil(fields, "Location.X"),
            y=_mil(fields, "Location.Y"),
            radius=radius,
            secondary_radius=secondary if secondary is not None else radius,
            start_angle=_number(fields, "StartAngle"),
            end_angle=_number(fields, "EndAngle"),
        )
    return Graphic(
        part_id=part_id,
        kind="text",
        x=_mil(fields, "Location.X"),
        y=_mil(fields, "Location.Y"),
        text=_display_text(fields),
    )


def _collect_unique_ids(records: list[Record]) -> set[str]:
    found: set[str] = set()
    for record in records:
        if record.kind != _TEXT_KIND:
            continue
        value = get_field(record.payload, "UniqueID")
        if value:
            found.add(value)
    return found


def _new_unique_id(used: set[str]) -> str:
    alphabet = string.ascii_uppercase
    for _ in range(10000):
        token = "".join(random.choices(alphabet, k=8))
        if token not in used:
            used.add(token)
            return token
    raise SchlibFormatError("无法生成唯一的 UniqueID")


def _max_sheet_index(records: list[Record]) -> int:
    best = 0
    for record in records:
        if record.kind != _TEXT_KIND:
            continue
        value = get_field(record.payload, "IndexInSheet")
        if value is None:
            continue
        try:
            best = max(best, int(value))
        except ValueError:
            continue
    return best


def _clone_graphic(payload: bytes, mode: int, used: set[str], sheet_index: int) -> bytes:
    updated = set_text_mode(payload, mode)
    updated = set_field(updated, "IndexInSheet", str(sheet_index), insert_after="OwnerPartId")
    updated = set_field(updated, "UniqueID", _new_unique_id(used), insert_after="IndexInSheet")
    return updated


def _validate_pin_text(label: str, text: str, *, allow_empty: bool) -> str:
    cleaned = text.strip()
    if not allow_empty and cleaned == "":
        raise SchlibFormatError(f"{label}不能为空")
    if any(ord(char) < 32 for char in cleaned):
        raise SchlibFormatError(f"{label}不能包含控制字符")
    return cleaned


def plan_moves(order: list[int]) -> list[int]:
    """把当前 0..n-1 变成 order 所需的「移到末尾」步骤。每一步都是复制后再删除。"""

    count = len(order)
    if sorted(order) != list(range(count)):
        raise SchlibFormatError("显示模式顺序必须包含每个现有模式恰好一次")
    if list(order) == list(range(count)):
        return []
    if count > 8:
        return _linear_moves(order)
    start = tuple(range(count))
    goal = tuple(order)
    parent: dict[tuple[int, ...], tuple[tuple[int, ...], int] | None] = {start: None}
    queue: deque[tuple[int, ...]] = deque([start])
    found = False
    while queue:
        state = queue.popleft()
        if state == goal:
            found = True
            break
        last = len(state) - 1
        for index in range(last):
            nxt = state[:index] + state[index + 1 :] + (state[index],)
            if nxt in parent:
                continue
            parent[nxt] = (state, index)
            queue.append(nxt)
    if not found:
        return _linear_moves(order)
    moves: list[int] = []
    state = goal
    while parent[state] is not None:
        previous, index = parent[state]  # type: ignore[misc]
        moves.append(index)
        state = previous
    moves.reverse()
    return moves


def _linear_moves(order: list[int]) -> list[int]:
    current = list(range(len(order)))
    moves: list[int] = []
    for wanted in order:
        index = current.index(wanted)
        if index == len(current) - 1:
            continue
        moves.append(index)
        current.append(current.pop(index))
    return moves


def describe_moves(mode_count: int, moves: list[int]) -> list[str]:
    """用模式 A/B/C 说明即将执行的复制和删除。"""

    if not moves:
        return ["顺序没有变化。"]
    content = [mode_letter(index) for index in range(mode_count)]
    names = content[:]
    lines: list[str] = []
    for index in moves:
        new_name = mode_letter(len(names))
        lines.append(f"新建模式 {new_name}，复制模式 {names[index]} 的图形和引脚（包含全部 Part）")
        lines.append(f"删除模式 {names[index]}，其后的模式依次前移")
        content = content[:index] + content[index + 1 :] + [content[index]]
        names = [mode_letter(item) for item in range(len(content))]
    final = "、".join(f"{names[index]}（原来的 {content[index]}）" for index in range(len(content)))
    lines.append(f"完成后：{final}")
    return lines


@dataclass
class SideStream:
    """PinFrac、引脚边距、引脚功能这类按引脚序号存放的附带流。"""

    name: str
    header: bytes | None = None
    entries: list[bytes] | None = None
    raw: bytes | None = None
    error: str | None = None

    def serialize(self) -> bytes | None:
        if self.error or self.header is None or self.entries is None:
            return self.raw
        return serialize_pintext(self.header, self.entries)


class Symbol:
    """一个符号的 Data / PinTextData，以及还没写入文件的修改。"""

    def __init__(
        self,
        name: str,
        records: list[Record],
        pintext_header: bytes | None,
        pintext_entries: list[bytes] | None,
        *,
        pintext_raw: bytes | None = None,
        pintext_error: str | None = None,
        side_streams: list[SideStream] | None = None,
    ) -> None:
        self.name = name
        self.records = records
        self.pintext_header = pintext_header
        self.pintext_entries = pintext_entries
        self.pintext_raw = pintext_raw
        self.pintext_error = pintext_error
        self.side_streams = list(side_streams or [])
        self.data_stream = "Data"
        self.pin_stream = "PinTextData"
        self.mode_count = 1
        self._data_snapshot = b""
        self._pintext_snapshot: bytes | None = None
        self._side_snapshot: tuple[tuple[str, bytes | None], ...] = ()

    @classmethod
    def parse(
        cls,
        name: str,
        data: bytes,
        pintext: bytes | None,
        extra_streams: dict[str, bytes] | None = None,
    ) -> Symbol:
        records = [Record(kind, payload) for kind, payload in iter_stream(data)]
        header: bytes | None = None
        entries: list[bytes] | None = None
        error: str | None = None
        raw = pintext
        if pintext:
            try:
                header, entries = parse_pintext(pintext)
            except SchlibFormatError as exc:
                error = str(exc)
                header = None
                entries = None
        symbol = cls(
            name,
            records,
            header,
            entries,
            pintext_raw=raw if error else None,
            pintext_error=error,
        )
        symbol.side_streams = _parse_side_streams(extra_streams or {})
        symbol.mode_count = symbol._infer_mode_count()
        rebuilt = symbol.serialize_data()
        if rebuilt != data:
            raise SchlibFormatError("Data 流重组后与原文件不一致")
        if pintext is not None and error is None and symbol.serialize_pintext() != pintext:
            raise SchlibFormatError("PinTextData 重组后与原文件不一致")
        symbol.mark_saved()
        return symbol

    def serialize_data(self) -> bytes:
        return serialize_records(self.records)

    def serialize_pintext(self) -> bytes | None:
        if self.pintext_entries is None or self.pintext_header is None:
            return self.pintext_raw
        return serialize_pintext(self.pintext_header, self.pintext_entries)

    def mark_saved(self) -> None:
        self._data_snapshot = self.serialize_data()
        self._pintext_snapshot = self.serialize_pintext()
        self._side_snapshot = tuple((stream.name, stream.serialize()) for stream in self.side_streams)

    @property
    def dirty(self) -> bool:
        return (
            self.serialize_data() != self._data_snapshot
            or self.serialize_pintext() != self._pintext_snapshot
            or tuple((stream.name, stream.serialize()) for stream in self.side_streams) != self._side_snapshot
        )

    def dirty_side_streams(self) -> list[tuple[str, bytes]]:
        saved = dict(self._side_snapshot)
        changed: list[tuple[str, bytes]] = []
        for stream in self.side_streams:
            blob = stream.serialize()
            if blob is not None and blob != saved.get(stream.name):
                changed.append((stream.name, blob))
        return changed

    @property
    def pintext_dirty(self) -> bool:
        return self.serialize_pintext() != self._pintext_snapshot

    def _header(self) -> Record | None:
        for record in self.records:
            if record.kind == _TEXT_KIND and record_number(record.payload) == 1:
                return record
        return None

    def _infer_mode_count(self) -> int:
        declared = 0
        header = self._header()
        if header is not None:
            value = get_field(header.payload, "DisplayModeCount")
            if value is not None:
                try:
                    declared = int(value)
                except ValueError:
                    declared = 0
        observed = 0
        for record in self.records:
            mode = owned_mode(record)
            if mode is not None:
                observed = max(observed, mode + 1)
        count = max(declared, observed)
        return count if count >= 1 else 1

    def _set_mode_count(self, count: int) -> None:
        self.mode_count = count
        header = self._header()
        if header is None:
            return
        header.payload = set_field(
            header.payload,
            "DisplayModeCount",
            str(count),
            insert_after="PartCount",
        )

    def pin_record_count(self) -> int:
        return sum(1 for record in self.records if is_pin(record))

    def pins(self) -> list[PinInfo]:
        result: list[PinInfo] = []
        seen: dict[tuple[int, int, str], int] = {}
        for record_index, record in enumerate(self.records):
            if not is_pin(record):
                continue
            mode = decode_mode(record.payload)
            part_id = record.payload[5]
            try:
                fields = parse_pin_fields(record.payload)
            except SchlibFormatError:
                info = PinInfo(
                    mode=mode,
                    part_id=part_id,
                    designator="",
                    name="",
                    x=0,
                    y=0,
                    rotation=0,
                    length=0,
                    hidden=False,
                    record_index=record_index,
                    ordinal=0,
                    broken=True,
                )
                result.append(info)
                continue
            designator = decode_pin_text(fields.designator)
            key = (mode, part_id, designator)
            ordinal = seen.get(key, 0)
            seen[key] = ordinal + 1
            result.append(
                PinInfo(
                    mode=mode,
                    part_id=part_id,
                    designator=designator,
                    name=decode_pin_text(fields.name),
                    x=fields.x,
                    y=fields.y,
                    rotation=fields.rotation,
                    length=fields.length,
                    hidden=fields.hidden,
                    record_index=record_index,
                    ordinal=ordinal,
                    broken=False,
                )
            )
        return result

    def parts(self) -> list[int]:
        found: set[int] = set()
        for record in self.records:
            mode = owned_mode(record)
            if mode is None:
                continue
            if is_pin(record):
                found.add(record.payload[5])
                continue
            part = owner_part_id(record.payload)
            if part is not None and part >= 1:
                found.add(part)
        return sorted(found)

    def parts_for_mode(self, mode: int) -> list[int]:
        found: set[int] = set()
        for record in self.records:
            if owned_mode(record) != mode:
                continue
            if is_pin(record):
                found.add(record.payload[5])
                continue
            part = owner_part_id(record.payload)
            if part is not None and part >= 1:
                found.add(part)
        return sorted(found)

    def graphics(self, mode: int) -> list[Graphic]:
        rows: list[Graphic] = []
        for record in self.records:
            if record.kind != _TEXT_KIND or owned_mode(record) != mode:
                continue
            try:
                graphic = graphic_from_payload(record.payload)
            except SchlibFormatError:
                continue
            if graphic is not None:
                rows.append(graphic)
        return rows

    def rename_pin(self, pin: PinInfo, designator: str, name: str, *, all_modes: bool) -> EditResult:
        designator = _validate_pin_text("位号", designator, allow_empty=False)
        name = _validate_pin_text("名称", name, allow_empty=True)
        if pin.broken:
            raise SchlibFormatError("这个引脚无法解析，不能编辑")
        pins = self.pins()
        notes: list[str] = []
        if all_modes:
            grouped: dict[int, list[PinInfo]] = {}
            for item in pins:
                if item.part_id == pin.part_id and item.designator == pin.designator:
                    grouped.setdefault(item.mode, []).append(item)
            targets: list[PinInfo] = []
            for mode in range(self.mode_count):
                group = grouped.get(mode, [])
                if pin.ordinal < len(group):
                    chosen = group[pin.ordinal]
                    if chosen.broken:
                        notes.append(f"模式 {mode_letter(mode)} 的对应引脚无法解析，已跳过")
                        continue
                    targets.append(chosen)
                else:
                    notes.append(
                        f"模式 {mode_letter(mode)} 没有对应的第 {pin.ordinal + 1} 个位号 {pin.designator}"
                    )
            if not targets:
                raise SchlibFormatError("没有可修改的引脚")
        else:
            targets = [pin]
        if all(item.designator == designator and item.name == name for item in targets):
            return EditResult(0, ("位号和名称没有变化",))
        target_ids = {item.record_index for item in targets}
        for item in targets:
            for other in pins:
                if other.record_index in target_ids:
                    continue
                if (
                    other.mode == item.mode
                    and other.part_id == item.part_id
                    and other.designator == designator
                ):
                    notes.append(
                        f"模式 {mode_letter(item.mode)} Part {item.part_id} 已经有位号 {designator}"
                    )
                    break
        for item in targets:
            record = self.records[item.record_index]
            if not is_pin(record):
                raise SchlibFormatError("引脚记录已经变化，请重新选择")
            fields = parse_pin_fields(record.payload)
            record.payload = replace_pin_strings(
                record.payload,
                encode_pin_text(name, fields.name),
                encode_pin_text(designator, fields.designator),
            )
        return EditResult(len(targets), tuple(notes))

    def delete_mode(self, index: int) -> None:
        """删除一个显示模式。编号更大的模式依次前移，每个 Part 都一起去掉。"""
        self._ensure_pintext()
        snapshot = self._capture()
        try:
            self._delete_mode(index)
            self._check_modes()
        except Exception:
            self._restore(snapshot)
            raise

    def apply_order(self, order: list[int]) -> list[str]:
        moves = plan_moves(order)
        if not moves:
            return []
        self._ensure_pintext()
        snapshot = self._capture()
        try:
            for index in moves:
                self._copy_mode(index)
                self._delete_mode(index)
            self._check_modes()
        except Exception:
            self._restore(snapshot)
            raise
        return describe_moves(len(order), moves)

    def _ensure_pintext(self) -> None:
        pin_count = self.pin_record_count()
        if self.pintext_error:
            raise SchlibFormatError(f"PinTextData 无法解析，不能调整显示模式：{self.pintext_error}")
        if self.pintext_entries is not None:
            validate_pin_entries(self.pintext_entries, pin_count, "PinTextData")
        for stream in self.side_streams:
            if stream.error:
                raise SchlibFormatError(f"{stream.name} 无法解析，不能调整显示模式：{stream.error}")
            if stream.entries is not None:
                validate_pin_entries(stream.entries, pin_count, stream.name)

    def _capture(self):
        entries = None if self.pintext_entries is None else list(self.pintext_entries)
        sides = [
            SideStream(
                stream.name,
                stream.header,
                None if stream.entries is None else list(stream.entries),
                stream.raw,
                stream.error,
            )
            for stream in self.side_streams
        ]
        return (
            [Record(record.kind, record.payload) for record in self.records],
            self.mode_count,
            self.pintext_header,
            entries,
            sides,
        )

    def _restore(self, snapshot) -> None:
        records, count, header, entries, sides = snapshot
        self.records = records
        self.mode_count = count
        self.pintext_header = header
        self.pintext_entries = entries
        self.side_streams = sides

    def _insert_at(self) -> int:
        position = len(self.records)
        for index, record in enumerate(self.records):
            if owned_mode(record) is not None:
                position = index + 1
        return position

    def _copy_mode(self, source: int) -> None:
        if not 0 <= source < self.mode_count:
            raise SchlibFormatError(f"没有显示模式 {source}")
        new_mode = self.mode_count
        used = _collect_unique_ids(self.records)
        sheet_index = _max_sheet_index(self.records) + 1
        clones: list[Record] = []
        source_indices: list[int] = []
        pin_cursor = 0
        for record in self.records:
            mode = owned_mode(record)
            if is_pin(record):
                if mode == source:
                    clones.append(Record(record.kind, set_pin_mode(record.payload, new_mode)))
                    source_indices.append(pin_cursor)
                pin_cursor += 1
            elif mode == source:
                clones.append(
                    Record(record.kind, _clone_graphic(record.payload, new_mode, used, sheet_index))
                )
                sheet_index += 1
        insert_at = self._insert_at()
        self.records[insert_at:insert_at] = clones
        self._transform_pin_entries(lambda entries: copy_indexed_entries(entries, source_indices, pin_cursor))
        self._set_mode_count(new_mode + 1)

    def _delete_mode(self, index: int) -> None:
        if not 0 <= index < self.mode_count:
            raise SchlibFormatError(f"没有显示模式 {index}")
        if self.mode_count <= 1:
            raise SchlibFormatError("至少需要保留一个显示模式")
        kept: list[Record] = []
        pin_modes: list[int | None] = []
        for record in self.records:
            mode = owned_mode(record)
            if is_pin(record):
                pin_modes.append(mode)
                if mode == index:
                    continue
                payload = record.payload
                if mode is not None and mode > index:
                    payload = set_pin_mode(payload, mode - 1)
                kept.append(Record(record.kind, payload))
                continue
            if mode == index:
                continue
            payload = record.payload
            if mode is not None and mode > index:
                payload = set_text_mode(payload, mode - 1)
            kept.append(Record(record.kind, payload))
        mapping: dict[int, int] = {}
        new_index = 0
        for old_index, mode in enumerate(pin_modes):
            if mode == index:
                continue
            mapping[old_index] = new_index
            new_index += 1
        self.records = kept
        self._transform_pin_entries(lambda entries: remap_pin_entries(entries, mapping))
        self._set_mode_count(self.mode_count - 1)

    def _transform_pin_entries(self, transform) -> None:
        if self.pintext_entries is not None:
            self.pintext_entries = transform(self.pintext_entries)
        for stream in self.side_streams:
            if stream.entries is not None:
                stream.entries = transform(stream.entries)

    def _check_modes(self) -> None:
        for record in self.records:
            mode = owned_mode(record)
            if mode is not None and not 0 <= mode < self.mode_count:
                raise SchlibFormatError(f"显示模式 {mode} 超出 0 到 {self.mode_count - 1}")
        header = self._header()
        if header is not None:
            value = get_field(header.payload, "DisplayModeCount")
            if value is not None and int(value) != self.mode_count:
                raise SchlibFormatError("DisplayModeCount 与实际模式数量不一致")
        pin_count = self.pin_record_count()
        if self.pintext_entries is not None:
            validate_pin_entries(self.pintext_entries, pin_count, "PinTextData")
        for stream in self.side_streams:
            if stream.entries is not None:
                validate_pin_entries(stream.entries, pin_count, stream.name)
