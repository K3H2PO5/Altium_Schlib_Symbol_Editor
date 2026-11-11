"""通过 Windows 复合文档接口改写 SchLib 里的流。"""

from __future__ import annotations

import ctypes
import sys
from ctypes import POINTER, byref, c_long, c_uint, c_ulong, c_void_p, c_wchar_p
from pathlib import Path


class OleStorageError(OSError):
    """打开或改写 OLE 复合文档失败。"""


STGM_READWRITE = 0x00000002
STGM_SHARE_EXCLUSIVE = 0x00000010
STGM_CREATE = 0x00001000
STGM_TRANSACTED = 0x00010000
STGC_DEFAULT = 0

_OPEN_MODE = STGM_READWRITE | STGM_SHARE_EXCLUSIVE | STGM_TRANSACTED
# 复合文档里的流不支持事务模式，只能以直接模式创建。
_STREAM_MODE = STGM_READWRITE | STGM_SHARE_EXCLUSIVE | STGM_CREATE


def _check(hr: int, action: str) -> None:
    if hr < 0:
        raise OleStorageError(f"{action} 失败, HRESULT=0x{hr & 0xFFFFFFFF:08X}")


def _method(unknown: int, index: int, argtypes: tuple):
    vtable = ctypes.cast(unknown, POINTER(c_void_p))[0]
    address = ctypes.cast(vtable, POINTER(c_void_p))[index]
    prototype = ctypes.WINFUNCTYPE(c_long, c_void_p, *argtypes)
    return prototype(address)


def _release(unknown: int | None) -> None:
    if unknown:
        _method(unknown, 2, ())(unknown)


class OleCompoundFile:
    """独占打开一个 OLE 文件，替换其中的流，或重命名顶层存储。"""

    def __init__(self, path: Path):
        if sys.platform != "win32":
            raise OleStorageError("写回 SchLib 需要 Windows 的 ole32 复合文档接口")
        self.path = Path(path)
        self._ole32 = ctypes.WinDLL("ole32")
        self._root: int | None = None
        self._initialized = False

    def __enter__(self) -> "OleCompoundFile":
        self._ole32.CoInitialize.argtypes = (c_void_p,)
        self._ole32.CoInitialize.restype = c_long
        hr = self._ole32.CoInitialize(None)
        if hr < 0:
            raise OleStorageError(f"CoInitialize 失败, HRESULT=0x{hr & 0xFFFFFFFF:08X}")
        self._initialized = True

        self._ole32.StgOpenStorage.argtypes = (
            c_wchar_p,
            c_void_p,
            c_uint,
            c_void_p,
            c_uint,
            POINTER(c_void_p),
        )
        self._ole32.StgOpenStorage.restype = c_long
        root = c_void_p()
        hr = self._ole32.StgOpenStorage(
            str(self.path),
            None,
            _OPEN_MODE,
            None,
            0,
            byref(root),
        )
        _check(hr, f"打开复合文档 {self.path}")
        self._root = root.value
        return self

    def replace_stream(self, storage_name: str, stream_name: str, data: bytes) -> None:
        if self._root is None:
            raise OleStorageError("复合文档尚未打开")
        child = c_void_p()
        open_storage = _method(
            self._root,
            6,
            (c_wchar_p, c_void_p, c_uint, c_void_p, c_uint, POINTER(c_void_p)),
        )
        hr = open_storage(
            self._root,
            storage_name,
            None,
            _OPEN_MODE,
            None,
            0,
            byref(child),
        )
        _check(hr, f"打开符号存储 {storage_name}")
        child_ptr = child.value
        stream_ptr: int | None = None
        try:
            stream = c_void_p()
            create_stream = _method(
                child_ptr,
                3,
                (c_wchar_p, c_uint, c_uint, c_uint, POINTER(c_void_p)),
            )
            hr = create_stream(
                child_ptr,
                stream_name,
                _STREAM_MODE,
                0,
                0,
                byref(stream),
            )
            _check(hr, f"重建流 {storage_name}/{stream_name}")
            stream_ptr = stream.value
            self._write_all(stream_ptr, data)
            self._commit(child_ptr, f"提交符号 {storage_name}")
        finally:
            _release(stream_ptr)
            _release(child_ptr)

    def rename_storage(self, old_name: str, new_name: str) -> None:
        """重命名根目录下的存储。调用方需要已避免同名冲突。"""

        if self._root is None:
            raise OleStorageError("复合文档尚未打开")
        rename = _method(self._root, 13, (c_wchar_p, c_wchar_p))
        hr = rename(self._root, old_name, new_name)
        _check(hr, f"重命名存储 {old_name} -> {new_name}")

    def replace_nested_stream(self, path: list[str], data: bytes) -> None:
        """按存储路径重建流。path 最后一项是流名称，前面是逐级存储。"""

        if self._root is None:
            raise OleStorageError("复合文档尚未打开")
        if len(path) < 2:
            raise OleStorageError("流路径至少包含一级存储和流名称")
        opened: list[int] = []
        stream_ptr: int | None = None
        try:
            current = self._root
            for name in path[:-1]:
                current = self._open_child(current, name)
                opened.append(current)
            stream = c_void_p()
            create_stream = _method(
                current,
                3,
                (c_wchar_p, c_uint, c_uint, c_uint, POINTER(c_void_p)),
            )
            hr = create_stream(current, path[-1], _STREAM_MODE, 0, 0, byref(stream))
            _check(hr, f"重建流 {'/'.join(path)}")
            stream_ptr = stream.value
            self._write_all(stream_ptr, data)
            for storage in reversed(opened):
                self._commit(storage, f"提交存储 {'/'.join(path)}")
        finally:
            _release(stream_ptr)
            for storage in reversed(opened):
                _release(storage)

    def _open_child(self, parent: int, name: str) -> int:
        child = c_void_p()
        open_storage = _method(
            parent,
            6,
            (c_wchar_p, c_void_p, c_uint, c_void_p, c_uint, POINTER(c_void_p)),
        )
        hr = open_storage(parent, name, None, _OPEN_MODE, None, 0, byref(child))
        _check(hr, f"打开存储 {name}")
        if not child.value:
            raise OleStorageError(f"打开存储 {name} 没有返回指针")
        return int(child.value)

    def _write_all(self, stream: int, data: bytes) -> None:
        write = _method(stream, 4, (c_void_p, c_ulong, POINTER(c_ulong)))
        view = memoryview(data)
        offset = 0
        chunk_size = 1024 * 1024
        while offset < len(view):
            chunk = bytes(view[offset : offset + chunk_size])
            buffer = ctypes.create_string_buffer(chunk)
            written = c_ulong()
            hr = write(stream, buffer, len(chunk), byref(written))
            _check(hr, "写入流")
            if written.value != len(chunk):
                raise OleStorageError(
                    f"写入流不完整: 期望 {len(chunk)} 字节, 实际 {written.value}"
                )
            offset += written.value

    def _commit(self, storage: int, action: str) -> None:
        commit = _method(storage, 9, (c_uint,))
        _check(commit(storage, STGC_DEFAULT), action)

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self._root is not None:
                if exc_type is None:
                    self._commit(self._root, "提交复合文档")
                else:
                    revert = _method(self._root, 10, ())
                    revert(self._root)
        finally:
            _release(self._root)
            self._root = None
            if self._initialized:
                self._ole32.CoUninitialize()
                self._initialized = False
