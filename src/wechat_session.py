"""Read a session from the user's running Jiushi mini-program on Windows."""

import ctypes
from ctypes import wintypes
import json
import re
import subprocess
import sys
import threading
import time


APP_ID = b"wxbd4ec54a9e9ce6dd"
HOST = b"jsapp.jussyun.com"
# Current Jiushi sessions encode 128 bytes as unpadded Base64.
TOKEN_PATTERN = re.compile(rb"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{171}(?![A-Za-z0-9+/=])")


class SessionConnectionError(Exception):
    pass


class MemoryInfo(ctypes.Structure):
    _fields_ = [
        ("base", ctypes.c_void_p), ("allocation", ctypes.c_void_p),
        ("allocation_protect", wintypes.DWORD), ("partition", wintypes.WORD),
        ("size", ctypes.c_size_t), ("state", wintypes.DWORD),
        ("protect", wintypes.DWORD), ("kind", wintypes.DWORD),
    ]


def find_candidates(chunks):
    """Accept candidates only from a renderer containing both Jiushi markers."""
    has_app = False
    has_host = False
    candidates = set()
    for data in chunks:
        has_app |= APP_ID in data or APP_ID.decode().encode("utf-16-le") in data
        has_host |= HOST in data or HOST.decode().encode("utf-16-le") in data
        for match in TOKEN_PATTERN.finditer(data):
            candidates.add(match.group().decode("ascii"))
            if len(candidates) > 16:
                raise SessionConnectionError("登录数据较多，请关闭其他久事窗口后重试。")
    return sorted(candidates) if has_app and has_host else []


def _memory_chunks(kernel, handle, cancelled, deadline):
    address = 0
    info = MemoryInfo()
    while kernel.VirtualQueryEx(handle, address, ctypes.byref(info), ctypes.sizeof(info)):
        if cancelled.is_set():
            return
        if time.monotonic() > deadline:
            raise SessionConnectionError("连接超时，请在久事打开场馆页后重试。")
        base = info.base or 0
        if info.state == 0x1000 and info.kind in (0x20000, 0x40000) and info.protect & 0xEE and not info.protect & 0x101:
            tail = b""
            for offset in range(0, info.size, 1024 * 1024):
                if cancelled.is_set():
                    return
                if time.monotonic() > deadline:
                    raise SessionConnectionError("连接超时，请在久事打开场馆页后重试。")
                size = min(1024 * 1024, info.size - offset)
                buffer = ctypes.create_string_buffer(size)
                read = ctypes.c_size_t()
                if kernel.ReadProcessMemory(handle, base + offset, buffer, size, ctypes.byref(read)):
                    data = tail + buffer.raw[:read.value]
                    yield data
                    tail = data[-512:]
                else:
                    tail = b""
        address = base + info.size
        if address <= base:
            return


def read_wechat_candidates(cancelled: threading.Event):
    if sys.platform != "win32":
        raise SessionConnectionError("连接微信需要 Windows 电脑。")
    command = "Get-CimInstance Win32_Process -Filter \"Name='WeChatAppEx.exe'\" | Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW, timeout=10, check=True,
        )
        processes = json.loads(result.stdout or "[]")
    except (OSError, ValueError, subprocess.SubprocessError):
        raise SessionConnectionError("无法读取微信运行状态，请打开电脑微信后重试。") from None
    if isinstance(processes, dict):
        processes = [processes]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.VirtualQueryEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(MemoryInfo), ctypes.c_size_t]
    kernel.VirtualQueryEx.restype = ctypes.c_size_t
    kernel.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    kernel.ReadProcessMemory.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    candidates = set()
    deadline = time.monotonic() + 30
    for process in processes:
        command_line = process.get("CommandLine") or ""
        if not re.search(r"--wmpf-render-type=1(?:\s|$)", command_line):
            continue
        if cancelled.is_set():
            return []
        handle = kernel.OpenProcess(0x410, False, process["ProcessId"])
        if not handle:
            continue
        try:
            candidates.update(find_candidates(_memory_chunks(kernel, handle, cancelled, deadline)))
        finally:
            kernel.CloseHandle(handle)
    if cancelled.is_set():
        return []
    if not candidates:
        raise SessionConnectionError("请在电脑微信打开久事体育，登录后进入场馆页，再点连接。")
    if len(candidates) > 16:
        raise SessionConnectionError("检测到多个久事窗口，请保留本次使用的账号后重试。")
    return sorted(candidates)
