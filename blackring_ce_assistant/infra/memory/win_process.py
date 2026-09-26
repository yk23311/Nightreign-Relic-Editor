# -*- coding: utf-8 -*-
"""Windows 进程内存读写后端。"""
from __future__ import annotations

import logging
import struct
from typing import Optional

from ..errors import (
    AddressInvalid,
    AttachDenied,
    NotAttached,
    ProcessNotFound,
    WriteFailed,
)
from .aob import parse_aob_pattern, scan_regions, scan_regions_multi
from .base import IMemoryBackend, ProcessInfo

try:  # pragma: no cover
    import pymem
    import pymem.process

    _HAS_PYMEM = True
except Exception:  # pragma: no cover
    pymem = None  # type: ignore
    _HAS_PYMEM = False

log = logging.getLogger(__name__)

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_VM_OPERATION = 0x0008
MEM_COMMIT = 0x1000
MEM_RESERVE = 0x2000
MEM_RELEASE = 0x8000
PAGE_READWRITE = 0x04
PAGE_EXECUTE_READWRITE = 0x40
PAGE_EXECUTE = 0x10
PAGE_EXECUTE_READ = 0x20
PAGE_EXECUTE_WRITECOPY = 0x80
PAGE_GUARD = 0x100
PAGE_NOACCESS = 0x01


def _configure_ctypes(kernel32, psapi, ctypes, wintypes) -> None:
    """声明 Win32 函数签名。

    **不声明时 ctypes 默认把返回值当 32 位 c_int**，HANDLE 会被截断成低 32 位。
    当前 Windows 句柄值普遍较小所以「看起来能跑」，但在 pymem 缺失、走 ctypes 兜底
    路径时这是实打实的 64 位截断隐患。
    """
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.ReadProcessMemory.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel32.ReadProcessMemory.restype = wintypes.BOOL
    kernel32.WriteProcessMemory.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel32.WriteProcessMemory.restype = wintypes.BOOL
    kernel32.VirtualProtectEx.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t,
        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.VirtualProtectEx.restype = wintypes.BOOL
    kernel32.VirtualAllocEx.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD,
    ]
    kernel32.VirtualAllocEx.restype = ctypes.c_void_p
    kernel32.VirtualFreeEx.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD,
    ]
    kernel32.VirtualFreeEx.restype = wintypes.BOOL

    psapi.EnumProcesses.argtypes = [
        ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    ]
    psapi.EnumProcesses.restype = wintypes.BOOL
    psapi.EnumProcessModulesEx.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p), wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), wintypes.DWORD,
    ]
    psapi.EnumProcessModulesEx.restype = wintypes.BOOL
    psapi.GetModuleBaseNameW.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, wintypes.LPWSTR, wintypes.DWORD,
    ]
    psapi.GetModuleBaseNameW.restype = wintypes.DWORD
    psapi.GetModuleInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
    ]
    psapi.GetModuleInformation.restype = wintypes.BOOL


class WinProcessBackend(IMemoryBackend):
    def __init__(self) -> None:
        self._pm = None
        self._handle = None
        self._pid = 0
        self._name = ""
        self._module_base = 0
        self._ctypes = None
        self._kernel32 = None
        self._psapi = None
        self._wintypes = None

    def attach(self, process_name: str) -> ProcessInfo:
        self.detach()
        if _HAS_PYMEM:
            try:
                pm = pymem.Pymem(process_name)
            except Exception as exc:
                raise ProcessNotFound(f"未找到进程 {process_name}") from exc
            self._pm = pm
            self._pid = pm.process_id
            self._name = process_name
            try:
                self._module_base = pymem.process.module_from_name(pm.process_handle, process_name).lpBaseOfDll
            except Exception:
                self._module_base = 0
            return self.process_info()
        return self._attach_ctypes(process_name)

    def _attach_ctypes(self, process_name: str) -> ProcessInfo:
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        self._kernel32 = kernel32
        self._psapi = psapi
        self._wintypes = wintypes
        _configure_ctypes(kernel32, psapi, ctypes, wintypes)

        arr = (wintypes.DWORD * 4096)()
        needed = wintypes.DWORD()
        if not psapi.EnumProcesses(ctypes.byref(arr), ctypes.sizeof(arr), ctypes.byref(needed)):
            raise ProcessNotFound("EnumProcesses 失败")
        count = needed.value // ctypes.sizeof(wintypes.DWORD)
        target = process_name.lower()
        pid = 0
        for i in range(count):
            p = arr[i]
            if not p:
                continue
            h = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, p)
            if not h:
                continue
            try:
                buf = ctypes.create_unicode_buffer(260)
                if psapi.GetModuleBaseNameW(h, None, buf, 260):
                    if buf.value.lower() == target:
                        pid = p
                        break
            finally:
                kernel32.CloseHandle(h)
        if not pid:
            raise ProcessNotFound(f"未找到进程 {process_name}")

        access = (
            PROCESS_QUERY_INFORMATION
            | PROCESS_VM_READ
            | PROCESS_VM_WRITE
            | PROCESS_VM_OPERATION
        )
        handle = kernel32.OpenProcess(access, False, pid)
        if not handle:
            raise AttachDenied(f"无法打开进程 {process_name}（权限不足？）请以管理员运行。")
        self._handle = handle
        self._pid = pid
        self._name = process_name
        self._module_base = self._module_base_ctypes(process_name)
        return self.process_info()

    def _module_base_ctypes(self, module_name: str) -> int:
        if not self._handle:
            return 0
        ctypes = self._ctypes
        wintypes = self._wintypes
        psapi = self._psapi
        hmods = (ctypes.c_void_p * 1024)()
        needed = wintypes.DWORD()
        if not psapi.EnumProcessModulesEx(
            self._handle, ctypes.byref(hmods), ctypes.sizeof(hmods), ctypes.byref(needed), 0x03
        ):
            return 0
        count = needed.value // ctypes.sizeof(ctypes.c_void_p)
        name_l = module_name.lower()
        for i in range(count):
            mod = hmods[i]
            if not mod:
                continue
            buf = ctypes.create_unicode_buffer(260)
            if psapi.GetModuleBaseNameW(self._handle, mod, buf, 260):
                if buf.value.lower() == name_l:
                    return int(mod or 0)
        return 0

    def detach(self) -> None:
        if self._pm is not None:
            try:
                self._pm.close_process()
            except Exception:
                log.debug("pymem close_process 失败（句柄可能已随进程退出）", exc_info=True)
            self._pm = None
        if getattr(self, "_handle", None):
            try:
                self._kernel32.CloseHandle(self._handle)
            except Exception:
                log.debug("CloseHandle 失败", exc_info=True)
            self._handle = None
        self._pid = 0
        self._name = ""
        self._module_base = 0

    def is_attached(self) -> bool:
        return self._pm is not None or self._handle is not None

    def process_info(self) -> ProcessInfo:
        return ProcessInfo(self._pid, self._name, self._module_base, self.is_attached())

    def _ensure(self) -> None:
        if not self.is_attached():
            raise NotAttached("尚未附加进程")

    def read_bytes(self, address: int, size: int) -> bytes:
        self._ensure()
        if size <= 0:
            raise AddressInvalid("读取长度必须 > 0")
        if self._pm is not None:
            try:
                return self._pm.read_bytes(address, size)
            except Exception as exc:
                raise AddressInvalid(f"读取失败 @0x{address:X}: {exc}") from exc
        return self._read_bytes_ctypes(address, size)

    def _read_bytes_ctypes(self, address: int, size: int) -> bytes:
        ctypes = self._ctypes
        wintypes = self._wintypes
        buf = (ctypes.c_ubyte * size)()
        read = wintypes.SIZE_T(0)
        ok = self._kernel32.ReadProcessMemory(
            self._handle, ctypes.c_void_p(address), buf, size, ctypes.byref(read)
        )
        if not ok or read.value != size:
            raise AddressInvalid(f"读取失败 @0x{address:X}")
        return bytes(buf)

    def write_bytes(self, address: int, data: bytes) -> None:
        self._ensure()
        if not data:
            raise WriteFailed("写入数据为空")
        if self._pm is not None:
            try:
                self._pm.write_bytes(address, data, len(data))
                return
            except Exception as exc:
                raise WriteFailed(f"写入失败 @0x{address:X}: {exc}") from exc
        self._write_bytes_ctypes(address, data)

    def _write_bytes_ctypes(self, address: int, data: bytes) -> None:
        """先直接写；只有失败时才临时放开页保护。

        旧实现无条件把目标页改成 PAGE_EXECUTE_READWRITE（数据页也被加上执行权限），
        且不检查 VirtualProtectEx 的返回值。遗物字段是普通数据页，没有理由给它执行权限。
        """
        ctypes = self._ctypes
        wintypes = self._wintypes
        buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        written = wintypes.SIZE_T(0)
        ok = self._kernel32.WriteProcessMemory(
            self._handle, ctypes.c_void_p(address), buf, len(data), ctypes.byref(written)
        )
        if ok and written.value == len(data):
            return

        old = wintypes.DWORD(0)
        if not self._kernel32.VirtualProtectEx(
            self._handle, ctypes.c_void_p(address), len(data), PAGE_READWRITE, ctypes.byref(old)
        ):
            raise WriteFailed(f"写入失败 @0x{address:X}：地址不可写且无法修改页保护（地址可能无效）")
        try:
            written = wintypes.SIZE_T(0)
            ok = self._kernel32.WriteProcessMemory(
                self._handle, ctypes.c_void_p(address), buf, len(data), ctypes.byref(written)
            )
        finally:
            if old.value:
                tmp = wintypes.DWORD(0)
                self._kernel32.VirtualProtectEx(
                    self._handle, ctypes.c_void_p(address), len(data), old.value, ctypes.byref(tmp)
                )
        if not ok or written.value != len(data):
            raise WriteFailed(f"写入失败 @0x{address:X}")

    def resolve_module_base(self, module_name: str) -> int:
        self._ensure()
        if self._pm is not None:
            try:
                mod = pymem.process.module_from_name(self._pm.process_handle, module_name)
                return int(mod.lpBaseOfDll)
            except Exception:
                return 0
        return self._module_base_ctypes(module_name)

    def module_size(self, module_name: str) -> int:
        self._ensure()
        base = self.resolve_module_base(module_name)
        if not base:
            return 0
        if self._pm is not None:
            try:
                mod = pymem.process.module_from_name(self._pm.process_handle, module_name)
                return int(getattr(mod, "SizeOfImage", 0) or 0)
            except Exception:
                return 0
        try:
            import ctypes
            from ctypes import wintypes

            hmods = (ctypes.c_void_p * 1)()
            needed = wintypes.DWORD()
            self._psapi.EnumProcessModulesEx(
                self._handle, ctypes.byref(hmods), ctypes.sizeof(hmods), ctypes.byref(needed), 0x03
            )

            class MODULEINFO(ctypes.Structure):
                _fields_ = [
                    ("lpBaseOfDll", ctypes.c_void_p),
                    ("SizeOfImage", wintypes.DWORD),
                    ("EntryPoint", ctypes.c_void_p),
                ]

            mi = MODULEINFO()
            if self._psapi.GetModuleInformation(self._handle, hmods[0], ctypes.byref(mi), ctypes.sizeof(mi)):
                return int(mi.SizeOfImage or 0)
        except Exception:
            pass
        return 0

    def aob_scan(self, module_name: str | None, pattern: str) -> list[int]:
        self._ensure()
        pat = parse_aob_pattern(pattern)
        mod = module_name or self._name
        # pymem 自带扫描更快
        if self._pm is not None:
            try:
                from pymem.pattern import pattern_scan_module

                mod_h = pymem.process.module_from_name(self._pm.process_handle, mod)
                sig = "".join("?? " if b is None else f"{b:02X} " for b in pat).strip()
                addr = pattern_scan_module(self._pm.process_handle, mod_h, sig, return_multiple=True)
                if addr:
                    return list(addr) if isinstance(addr, (list, tuple)) else [int(addr)]
            except Exception:
                pass
        regions = self._code_regions_for_module(mod)
        if not regions:
            base = self.resolve_module_base(mod) or self._module_base
            size = self.module_size(mod) or (64 * 1024 * 1024)
            if base:
                regions = [(base, size)]
        return scan_regions(self.read_bytes, regions, pat, max_hits=8, chunk=8 * 1024 * 1024)

    def aob_scan_multi(self, module_name: str | None, patterns: dict[str, str]) -> dict[str, list[int]]:
        """一次扫描多个特征码。"""
        self._ensure()
        mod = module_name or self._name
        pats = [(name, parse_aob_pattern(p)) for name, p in patterns.items()]
        regions = self._code_regions_for_module(mod)
        if not regions:
            base = self.resolve_module_base(mod) or self._module_base
            size = self.module_size(mod) or (64 * 1024 * 1024)
            if base:
                regions = [(base, size)]
        return scan_regions_multi(self.read_bytes, regions, pats, max_hits=8, chunk=8 * 1024 * 1024)

    def _code_regions_for_module(self, module_name: str) -> list[tuple[int, int]]:
        """解析 PE 节表，只取可执行节（IMAGE_SCN_MEM_EXECUTE）。"""
        self._ensure()
        base = self.resolve_module_base(module_name) or self._module_base
        if not base:
            return []
        try:
            # DOS header e_lfanew
            e_lfanew = struct.unpack("<I", self.read_bytes(base + 0x3C, 4))[0]
            pe = base + e_lfanew
            coff = pe + 4
            num_sections = struct.unpack("<H", self.read_bytes(coff + 2, 2))[0]
            size_opt = struct.unpack("<H", self.read_bytes(coff + 16, 2))[0]
            section_table = coff + 20 + size_opt
            out: list[tuple[int, int]] = []
            for i in range(num_sections):
                off = section_table + i * 40
                raw = self.read_bytes(off, 40)
                name = raw[:8].split(b"\x00", 1)[0]
                vsize = struct.unpack("<I", raw[8:12])[0]
                va = struct.unpack("<I", raw[12:16])[0]
                raw_size = struct.unpack("<I", raw[16:20])[0]
                chars = struct.unpack("<I", raw[36:40])[0]
                size = max(vsize, raw_size)
                if not size:
                    continue
                # EXECUTE 位 0x20000000
                if chars & 0x20000000:
                    out.append((base + va, size))
            if out:
                return out
        except Exception:
            # 解析 PE 节表失败会退化成「扫整个模块」，只影响耗时不影响正确性，但要留痕
            log.warning("解析 %s 的 PE 节表失败，退化为整模块扫描", module_name, exc_info=True)
        return []

    def allocate(self, size: int) -> int:
        self._ensure()
        if self._pm is not None:
            return int(self._pm.allocate(size))
        ctypes = self._ctypes
        addr = self._kernel32.VirtualAllocEx(
            self._handle, None, size, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE
        )
        if not addr:
            raise WriteFailed("VirtualAllocEx 失败")
        return int(addr)

    def free(self, address: int) -> None:
        if not self.is_attached():
            return
        if self._pm is not None:
            try:
                self._pm.free(address)
            except Exception:
                log.debug("释放 Gaitem 缓存失败 @0x%X（通常无害）", address, exc_info=True)
            return
        try:
            self._kernel32.VirtualFreeEx(self._handle, address, 0, MEM_RELEASE)
        except Exception:
            log.debug("VirtualFreeEx 失败 @0x%X（通常无害）", address, exc_info=True)

    def read_u16(self, address: int) -> int:
        return struct.unpack("<H", self.read_bytes(address, 2))[0]

    def read_i32(self, address: int) -> int:
        return struct.unpack("<i", self.read_bytes(address, 4))[0]


class MockMemoryBackend(IMemoryBackend):
    def __init__(self) -> None:
        self.mem: dict[int, int] = {}
        self.attached = False
        self.pid = 1
        self.name = "mock.exe"
        self.module_base = 0x10000000
        self.writes: list[tuple[int, bytes]] = []
        self.fail_write_at: Optional[int] = None
        self._next_alloc = 0x70000000

    def attach(self, process_name: str) -> ProcessInfo:
        self.attached = True
        self.name = process_name
        return self.process_info()

    def detach(self) -> None:
        self.attached = False

    def is_attached(self) -> bool:
        return self.attached

    def process_info(self) -> ProcessInfo:
        return ProcessInfo(self.pid, self.name, self.module_base, self.attached)

    def read_bytes(self, address: int, size: int) -> bytes:
        return bytes(self.mem.get(address + i, 0) & 0xFF for i in range(size))

    def write_bytes(self, address: int, data: bytes) -> None:
        if self.fail_write_at is not None and address == self.fail_write_at:
            raise WriteFailed(f"模拟写入失败 @0x{address:X}")
        self.writes.append((address, data))
        for i, b in enumerate(data):
            self.mem[address + i] = b

    def resolve_module_base(self, module_name: str) -> int:
        return self.module_base

    def module_size(self, module_name: str) -> int:
        return 0x1000

    def aob_scan(self, module_name: str | None, pattern: str) -> list[int]:
        return []

    def allocate(self, size: int) -> int:
        addr = self._next_alloc
        self._next_alloc += size
        return addr

    def free(self, address: int) -> None:
        return None

    def poke_u32(self, address: int, value: int) -> None:
        for i in range(4):
            self.mem[address + i] = (value >> (8 * i)) & 0xFF

    def poke_u64(self, address: int, value: int) -> None:
        for i in range(8):
            self.mem[address + i] = (value >> (8 * i)) & 0xFF

    def poke_u16(self, address: int, value: int) -> None:
        self.mem[address] = value & 0xFF
        self.mem[address + 1] = (value >> 8) & 0xFF
