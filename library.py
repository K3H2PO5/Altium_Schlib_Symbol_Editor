"""打开和保存 SchLib。"""

from __future__ import annotations

import re
import shutil
import stat
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import olefile

from ole_storage import OleCompoundFile
from issues import has_pin_audit_issue
from records import SchlibFormatError, Symbol

_SKIP = {"fileheader", "root entry", "storage", "sectionkeys"}
_SIDE_STREAMS = {"pinverticalmargindata", "pinfunctiondata", "pinfrac"}


class LibraryError(ValueError):
    """库文件打不开或无法保存。"""


@dataclass
class Library:
    path: Path
    symbols: list[Symbol] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)

    def save(self) -> Path | None:
        dirty = [symbol for symbol in self.symbols if symbol.dirty]
        if not dirty:
            return None
        backup = history_backup_path(self.path)
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.path, backup)
        try:
            _writable(self.path)
            with OleCompoundFile(self.path) as document:
                for symbol in dirty:
                    document.replace_stream(symbol.name, symbol.data_stream, symbol.serialize_data())
                    pintext = symbol.serialize_pintext()
                    if symbol.pintext_dirty and pintext is not None:
                        document.replace_stream(symbol.name, symbol.pin_stream, pintext)
                    for stream_name, blob in symbol.dirty_side_streams():
                        document.replace_stream(symbol.name, stream_name, blob)
        except Exception:
            shutil.copy2(backup, self.path)
            raise
        for symbol in dirty:
            symbol.mark_saved()
        return backup


def history_backup_path(library_path: Path, when: datetime | None = None) -> Path:
    """库文件同级 History 目录下的备份路径，形如 DB_IC_20261111_165930.SchLib。"""

    library_path = Path(library_path)
    stamp = (when or datetime.now()).strftime("%Y%m%d_%H%M%S")
    history = library_path.parent / "History"
    candidate = history / f"{library_path.stem}_{stamp}{library_path.suffix}"
    index = 2
    while candidate.exists():
        candidate = history / f"{library_path.stem}_{stamp}_{index}{library_path.suffix}"
        index += 1
    return candidate


def schlib_files(folder: Path) -> list[Path]:
    """只列出文件夹里的 SchLib，不进入子文件夹。"""

    folder = Path(folder)
    if not folder.is_dir():
        raise LibraryError(f"找不到文件夹: {folder}")
    files = [path for path in folder.iterdir() if path.is_file() and path.suffix.lower() == ".schlib"]
    return sorted(files, key=lambda path: natural_key(path.name))


def load_library(path: Path, progress=None) -> Library:
    path = Path(path)
    if not path.is_file():
        raise LibraryError(f"找不到文件: {path}")
    if not olefile.isOleFile(str(path)):
        raise LibraryError(f"不是 OLE 复合文档: {path}")

    symbols: list[Symbol] = []
    skipped: list[tuple[str, str]] = []
    try:
        ole = olefile.OleFileIO(str(path))
    except OSError as exc:
        raise LibraryError(f"无法打开库: {exc}") from exc
    try:
        streams: dict[str, dict[str, str]] = {}
        for entry in ole.listdir():
            if len(entry) == 2:
                streams.setdefault(entry[0], {})[entry[1].lower()] = entry[1]
        names = sorted(
            (name for name in streams if "data" in streams[name] and name.casefold() not in _SKIP),
            key=natural_key,
        )
        total = len(names)
        for index, name in enumerate(names):
            if progress is not None and index % 20 == 0:
                progress(index, total)
            data_stream = streams[name]["data"]
            pin_stream = streams[name].get("pintextdata", "PinTextData")
            try:
                data = ole.openstream([name, data_stream]).read()
                pintext = (
                    ole.openstream([name, pin_stream]).read() if "pintextdata" in streams[name] else None
                )
                extra = {
                    actual: ole.openstream([name, actual]).read()
                    for key, actual in streams[name].items()
                    if key in _SIDE_STREAMS
                }
                symbol = Symbol.parse(name, data, pintext, extra)
            except (SchlibFormatError, OSError, RuntimeError, ValueError) as exc:
                skipped.append((name, str(exc)))
                continue
            symbol.data_stream = data_stream
            symbol.pin_stream = pin_stream
            if symbol.mode_count > 1 or has_pin_audit_issue(symbol):
                symbols.append(symbol)
    finally:
        ole.close()
    if progress is not None:
        progress(total, total)
    return Library(path=path, symbols=symbols, skipped=skipped)


def natural_key(name: str) -> list[tuple[int, int] | tuple[int, str]]:
    key: list[tuple[int, int] | tuple[int, str]] = []
    for piece in re.split(r"(\d+)", name):
        if piece.isdigit():
            key.append((0, int(piece)))
        else:
            key.append((1, piece.casefold()))
    return key


def _writable(path: Path) -> None:
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IWRITE)
