# -*- coding: utf-8 -*-
"""内存后端抽象接口。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import struct


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    name: str
    module_base: int
    is_attached: bool


class IMemoryBackend(ABC):
    """统一内存后端：WinProcessBackend 实现；CeBackend 二期可选。"""

    @abstractmethod
    def attach(self, process_name: str) -> ProcessInfo: ...

    @abstractmethod
    def detach(self) -> None: ...

    @abstractmethod
    def is_attached(self) -> bool: ...

    @abstractmethod
    def process_info(self) -> ProcessInfo: ...

    @abstractmethod
    def read_bytes(self, address: int, size: int) -> bytes: ...

    @abstractmethod
    def write_bytes(self, address: int, data: bytes) -> None: ...

    def read_u8(self, address: int) -> int:
        return self.read_bytes(address, 1)[0]

    def read_u16(self, address: int) -> int:
        return struct.unpack("<H", self.read_bytes(address, 2))[0]

    def read_u32(self, address: int) -> int:
        return struct.unpack("<I", self.read_bytes(address, 4))[0]

    def read_i32(self, address: int) -> int:
        return struct.unpack("<i", self.read_bytes(address, 4))[0]

    def read_u64(self, address: int) -> int:
        return struct.unpack("<Q", self.read_bytes(address, 8))[0]

    def read_f32(self, address: int) -> float:
        return struct.unpack("<f", self.read_bytes(address, 4))[0]

    def read_f64(self, address: int) -> float:
        return struct.unpack("<d", self.read_bytes(address, 8))[0]

    def read_string(self, address: int, length: int, *, unicode: bool = False) -> str:
        raw = self.read_bytes(address, length * (2 if unicode else 1))
        if unicode:
            return raw.decode("utf-16-le", errors="replace").split("\x00", 1)[0]
        return raw.decode("utf-8", errors="replace").split("\x00", 1)[0]

    def write_u32(self, address: int, value: int) -> None:
        self.write_bytes(address, struct.pack("<I", value & 0xFFFFFFFF))

    def write_u64(self, address: int, value: int) -> None:
        self.write_bytes(address, struct.pack("<Q", value & 0xFFFFFFFFFFFFFFFF))

    @abstractmethod
    def resolve_module_base(self, module_name: str) -> int: ...

    @abstractmethod
    def aob_scan(self, module_name: str | None, pattern: str) -> list[int]: ...

    def read_pointer(self, address: int) -> int:
        return self.read_u64(address)
