# %%
import base64
import ctypes
import hashlib
import json
import logging
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

TARGET_EXE_NAMES = {"wechatappex.exe", "weixin.exe", "wechat.exe"}
APPID_BYTES = b"wxbd4ec54a9e9ce6dd"
HOST_BYTES = b"jsapp.jussyun.com"
APPID_U16 = APPID_BYTES.decode("ascii").encode("utf-16le")
HOST_U16 = HOST_BYTES.decode("ascii").encode("utf-16le")

TH32CS_SNAPPROCESS = 0x00000002
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
MEM_COMMIT = 0x1000
MEM_PRIVATE = 0x20000
PAGE_NOACCESS = 0x01
PAGE_GUARD = 0x100
READABLE_PROTECTS = {0x02, 0x04, 0x08, 0x20, 0x40, 0x80}

RE_CTX_ASCII = re.compile(
    rb'(?:"authToken"|"token"|[\r\n]token)\s*:\s*"?([A-Za-z0-9+/]{140,220}={0,2})(?![A-Za-z0-9+/=])'
)
RE_CTX_U16 = re.compile(
    rb'(?:"\x00a\x00u\x00t\x00h\x00T\x00o\x00k\x00e\x00n\x00"\x00|"\x00t\x00o\x00k\x00e\x00n\x00"\x00)'
    rb'(?:\s\x00)*:\x00(?:\s\x00)*"\x00((?:[A-Za-z0-9+/]\x00){140,220}(?:=\x00){0,2})'
)
RE_B64_ASCII = re.compile(
    rb"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{140,220}={0,2}(?![A-Za-z0-9+/=])"
)
RE_B64_TIGHT = re.compile(
    rb"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{160,185}={0,2}(?![A-Za-z0-9+/=])"
)
RE_B64_U16_TIGHT = re.compile(
    rb"(?:[A-Za-z0-9+/]\x00){160,185}(?:=\x00){0,2}"
)


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


class _MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("PartitionId", wintypes.WORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
_k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
_k32.Process32FirstW.restype = wintypes.BOOL
_k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
_k32.Process32NextW.restype = wintypes.BOOL
_k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
_k32.OpenProcess.restype = wintypes.HANDLE
_k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_k32.VirtualQueryEx.restype = ctypes.c_size_t
_k32.VirtualQueryEx.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.POINTER(_MEMORY_BASIC_INFORMATION),
    ctypes.c_size_t,
]
_k32.ReadProcessMemory.restype = wintypes.BOOL
_k32.ReadProcessMemory.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
_k32.CloseHandle.restype = wintypes.BOOL
_k32.CloseHandle.argtypes = [wintypes.HANDLE]


def _looks_like_token(s: str) -> bool:
    if len(s) < 140 or len(s) > 240:
        return False
    has_upper = any("A" <= c <= "Z" for c in s)
    has_lower = any("a" <= c <= "z" for c in s)
    has_digit = any("0" <= c <= "9" for c in s)
    if not (has_upper and has_lower and has_digit):
        return False
    return len(set(s)) >= 20


class TokenGetter:
    USER_INFO_URL = "https://jsapp.jussyun.com/jiushi-core/user/getUserInfo"
    SIGN_KEY = "527093093C418483029EEC61F70E9DD1"
    SIGN_APP_ID = "0ff444f417de34c1352af3b3ffc30348"

    def __init__(self):
        self.port = 8012
        self.assets_path = Path(__file__).parent / "assets"
        self.src_path = Path(__file__).parent
        self.config_path = self.assets_path / "config.json"

    def _load_config(self) -> dict:
        if not self.config_path.exists():
            return {}
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_config(self, data: dict) -> None:
        self.assets_path.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def save_token(self, token: str) -> None:
        data = self._load_config()
        data["token"] = token
        self._save_config(data)

    def load_serverchan_key(self) -> str:
        return str(self._load_config().get("serverchan_key", "") or "").strip()

    def save_serverchan_key(self, key: str) -> None:
        data = self._load_config()
        data["serverchan_key"] = key.strip()
        self._save_config(data)

    def _list_wechat_processes(self) -> list[tuple[int, str]]:
        snap = _k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snap == wintypes.HANDLE(-1).value:
            return []
        out: list[tuple[int, str]] = []
        try:
            pe = _PROCESSENTRY32W()
            pe.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
            ok = _k32.Process32FirstW(snap, ctypes.byref(pe))
            while ok:
                name = pe.szExeFile
                if name.lower() in TARGET_EXE_NAMES:
                    out.append((int(pe.th32ProcessID), name))
                ok = _k32.Process32NextW(snap, ctypes.byref(pe))
        finally:
            _k32.CloseHandle(snap)
        return out

    def _fetch_cmdlines(self, pids: list[int] | None = None) -> dict[int, str]:
        """纯 Win32 NtQueryInformationProcess 读取命令行，避免派生 powershell 子进程触发 conhost 崩溃。"""
        class _UNICODE_STRING(ctypes.Structure):
            _fields_ = [
                ("Length", wintypes.USHORT),
                ("MaximumLength", wintypes.USHORT),
                ("Buffer", ctypes.c_void_p),
            ]

        ntdll = ctypes.WinDLL("ntdll")
        out: dict[int, str] = {}
        target_pids = pids if pids is not None else [p for p, _ in self._list_wechat_processes()]
        for pid in target_pids:
            h = _k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                continue
            try:
                needed = wintypes.ULONG(0)
                ntdll.NtQueryInformationProcess(h, 60, None, 0, ctypes.byref(needed))
                if needed.value:
                    buf = ctypes.create_string_buffer(needed.value)
                    if (
                        ntdll.NtQueryInformationProcess(
                            h, 60, buf, needed.value, ctypes.byref(needed)
                        )
                        == 0
                    ):
                        us = _UNICODE_STRING.from_buffer_copy(
                            buf.raw[: ctypes.sizeof(_UNICODE_STRING)]
                        )
                        if us.Buffer and us.Length > 0:
                            out[pid] = ctypes.wstring_at(us.Buffer, us.Length // 2)
            except Exception:
                pass
            finally:
                _k32.CloseHandle(h)
        return out

    def _scan_process_memory(self, pid: int, deep: bool = False, max_region: int = 16 * 1024 * 1024):
        h = _k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
        if not h:
            return False, ctypes.get_last_error(), [], [], []

        ctx_seen: dict[str, None] = {}
        anchor_seen: dict[str, None] = {}
        broad_seen: dict[str, None] = {}

        try:
            addr = 0
            mbi = _MEMORY_BASIC_INFORMATION()
            mbi_size = ctypes.sizeof(mbi)
            while (
                _k32.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(mbi), mbi_size)
                == mbi_size
            ):
                base = int(mbi.BaseAddress or 0)
                size = int(mbi.RegionSize or 0)
                next_addr = base + size
                if next_addr <= addr:
                    break
                addr = next_addr

                if mbi.State != MEM_COMMIT:
                    continue
                if mbi.Protect & (PAGE_GUARD | PAGE_NOACCESS):
                    continue
                if (mbi.Protect & 0xFF) not in READABLE_PROTECTS:
                    continue
                if size == 0 or size > 128 * 1024 * 1024:
                    continue

                offset = 0
                overlap = 512
                while offset < size:
                    chunk_len = min(max_region, size - offset)
                    buf = ctypes.create_string_buffer(chunk_len)
                    bytes_read = ctypes.c_size_t(0)
                    ok = _k32.ReadProcessMemory(
                        h,
                        ctypes.c_void_p(base + offset),
                        buf,
                        chunk_len,
                        ctypes.byref(bytes_read),
                    )
                    if ok and bytes_read.value > 0:
                        data = buf.raw[: bytes_read.value]
                        # C 级别高速子串预检，无锚点/字段名则跳过正则（提速 ~20x）
                        if b"authToken" in data or b'"token"' in data or b"\ntoken" in data:
                            for m in RE_CTX_ASCII.finditer(data):
                                tok = m.group(1).decode("ascii", errors="ignore")
                                if _looks_like_token(tok):
                                    ctx_seen.setdefault(tok, None)

                        has_anchor = (
                            APPID_BYTES in data
                            or HOST_BYTES in data
                            or (deep and (APPID_U16 in data or HOST_U16 in data))
                        )
                        if has_anchor:
                            for m in RE_B64_ASCII.finditer(data):
                                tok = m.group(0).decode("ascii", errors="ignore")
                                if _looks_like_token(tok):
                                    anchor_seen.setdefault(tok, None)
                            if deep:
                                for m in RE_B64_U16_TIGHT.finditer(data):
                                    tok = m.group(0)[::2].decode("ascii", errors="ignore")
                                    if _looks_like_token(tok):
                                        anchor_seen.setdefault(tok, None)
                        elif deep and mbi.Type == MEM_PRIVATE:
                            for m in RE_CTX_U16.finditer(data):
                                tok = m.group(1)[::2].decode("ascii", errors="ignore")
                                if _looks_like_token(tok):
                                    ctx_seen.setdefault(tok, None)
                            for m in RE_B64_TIGHT.finditer(data):
                                tok = m.group(0).decode("ascii", errors="ignore")
                                if ("+" in tok or "/" in tok) and _looks_like_token(tok):
                                    broad_seen.setdefault(tok, None)

                    if offset + chunk_len >= size:
                        break
                    offset += chunk_len - overlap
        finally:
            _k32.CloseHandle(h)

        return True, 0, list(ctx_seen.keys()), list(anchor_seen.keys()), list(broad_seen.keys())

    def verify_token(self, token: str, client: httpx.Client | None = None) -> bool:
        body = "{}"
        js_sign = base64.b64encode(
            hashlib.md5((body + self.SIGN_KEY).encode("utf-8")).hexdigest().encode("utf-8")
        ).decode("ascii")
        headers = {
            "Host": "jsapp.jussyun.com",
            "device_type": "microsoft",
            "os_type": "wechat_mini",
            "js_sign": js_sign,
            "app_id": self.SIGN_APP_ID,
            "xweb_xhr": "1",
            "os_version": "Windows 10 x64",
            "gw_channel": "api",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 "
                "MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI MiniProgramEnv/Windows "
                "WindowsWechat/WMPF WindowsWechat(0x63090a13) XWEB/19823"
            ),
            "Content-Type": "application/json",
            "token": token,
            "Referer": "https://servicewechat.com/wxbd4ec54a9e9ce6dd/171/page-frame.html",
        }
        own = client is None
        cli = client if client is not None else httpx.Client(http1=True, verify=True, trust_env=False, timeout=15)
        try:
            r = cli.post(self.USER_INFO_URL, content=body.encode("utf-8"), headers=headers)
            if r.status_code != 200:
                return False
            j = r.json()
            return isinstance(j, dict) and j.get("rtnCode") == "10000"
        except Exception:
            return False
        finally:
            if own:
                cli.close()

    def get_token_from_memory(self) -> bool:
        logger.error("正在从电脑微信内存读取登录态...")
        procs = self._list_wechat_processes()
        if not procs:
            logger.error("未找到微信进程，请先在电脑微信打开「久事体育APP」小程序")
            return False

        cmdlines = self._fetch_cmdlines([p for p, _ in procs])

        def _score(item: tuple[int, str]) -> tuple[int, int]:
            pid, name = item
            cmd = cmdlines.get(pid, "").lower()
            if name.lower() == "wechatappex.exe":
                if "--type=renderer" in cmd:
                    return (0, pid)
                if "--type=" not in cmd:
                    return (1, pid)
                return (2, pid)
            return (3, pid)

        ranked = sorted(procs, key=_score)
        any_open = False
        open_errors: list[int] = []
        seen: set[str] = set()
        ctx_anchor: list[str] = []
        broad: list[str] = []

        with httpx.Client(http1=True, verify=True, trust_env=False, timeout=15) as cli:
            # 仅扫描 WeChatAppEx 渲染进程（不扫 1GB 的 Weixin.exe / gpu-process，1~2 秒内出结果）
            saw_expired_candidate = False
            for pid, name in ranked:
                if name.lower() != "wechatappex.exe":
                    continue
                cmd = cmdlines.get(pid, "").lower()
                if "--type=" in cmd and "--type=renderer" not in cmd:
                    continue
                ok, err, ctx_h, anc_h, _brd_h = self._scan_process_memory(pid, deep=False)
                if not ok:
                    open_errors.append(err)
                    continue
                any_open = True
                for tok in ctx_h + anc_h:
                    if tok not in seen:
                        seen.add(tok)
                        if self.verify_token(tok, client=cli):
                            self.save_token(tok)
                            logger.error("成功从微信内存读取到有效 token！")
                            return True
                        saw_expired_candidate = True

            if not any_open:
                if 5 in open_errors:
                    logger.error("无法读取微信进程内存（权限不足），请尝试以管理员身份运行")
                else:
                    logger.error("未找到小程序渲染进程，请先在电脑微信打开「久事体育APP」→ 点进「场馆」页")
                return False

            if saw_expired_candidate:
                logger.error("微信内存中的登录态已失效（可能曾在手机打开过小程序）。请在电脑微信「久事体育APP」点一下「场馆」页刷新，再点「登录」")
            else:
                logger.error("未在微信内存中发现登录态，请在电脑微信「久事体育APP」点进「场馆」页后再点「登录」")
            return False

    def run_mitmproxy(self):
        subprocess.run(
            args=[
                (self.assets_path / "mitmdump").as_posix(),
                "-s",
                (self.src_path / "RequestLogger.py").as_posix(),
                "-q",
                "-p",
                str(self.port),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def get_token(self, allow_proxy: bool = False):
        if self.get_token_from_memory():
            return True
        if allow_proxy:
            return self.get_token_via_proxy()
        return False

    def get_token_via_proxy(self):
        logger.error("正在启用临时抓包获取 token...")
        proxy = self.back_up_proxy()
        self.set_proxy()

        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(self.run_mitmproxy)
                if not self.check_cert():
                    if not self.install_cert():
                        self.restore_proxy(proxy)
                        executor.shutdown(wait=False)
                        return False

                logger.error("请打开久事体育APP小程序并点进「场馆」页")
                future.result()
                logger.error("成功获取到 token, 可以退出小程序了")
                return True
        except Exception as e:
            logger.error("获取 token 过程中出现错误")
            logger.debug(e, exc_info=True)
            return False
        finally:
            self.restore_proxy(proxy)

    def read_token(self):
        if not self.config_path.exists():
            self._save_config({})
            logger.error("配置文件已初始化，正在获取登录态...")
            return None

        data = self._load_config()
        token = data.get("token")
        if not token or not isinstance(token, str):
            logger.error("配置文件中不存在有效 token，将自动读取微信登录态")
            return None
        logger.debug(f"读取 token: {token}")
        return token

    def clear_token(self):
        if self.config_path.exists():
            logger.error("清除已失效 token")
            data = self._load_config()
            data.pop("token", None)
            self._save_config(data)
        else:
            logger.error("配置文件不存在")

    def check_cert(self):
        try:
            cert_info = subprocess.run(
                ["certutil", "-store", "root"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=True,
                encoding="gb2312",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout
        except Exception as e:
            logger.error("无法自动检测证书，请确保证书已安装")
            logger.debug(e, exc_info=True)
            return True

        if "mitmproxy" in cert_info:
            return True
        logger.error("检测到证书未安装，将自动安装证书。请确保使用管理员权限运行此程序")
        return False

    def install_cert(self):
        logger.error("正在下载证书...")
        cert_url = "http://mitm.it/cert/cer"
        try:
            response = httpx.get(cert_url, trust_env=False)
            response.raise_for_status()
        except Exception as e:
            logger.error("证书下载失败，请检查网络连接")
            logger.debug(e, exc_info=True)
            return False

        (self.assets_path / "mitmproxy-ca-cert.cer").write_bytes(response.content)
        logger.error("证书下载成功，正在安装证书...")

        try:
            subprocess.run(
                [
                    "certutil",
                    "-addstore",
                    "root",
                    (self.assets_path / "mitmproxy-ca-cert.cer").as_posix(),
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as e:
            logger.error("证书安装失败，第一次使用请确保使用管理员权限运行此程序")
            logger.debug(e, exc_info=True)
            return False

        logger.error("证书安装成功")
        return True

    def back_up_proxy(self):
        import winreg

        reg_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        proxy = {}
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path) as key:
            for name in ("ProxyEnable", "ProxyServer", "ProxyOverride"):
                try:
                    val, kind = winreg.QueryValueEx(key, name)
                    proxy[name] = (val, kind)
                except FileNotFoundError:
                    proxy[name] = None
        return proxy

    def _notify_wininet(self):
        try:
            wininet = ctypes.WinDLL("wininet", use_last_error=True)
            wininet.InternetSetOptionW(None, 39, None, 0)
            wininet.InternetSetOptionW(None, 37, None, 0)
        except Exception:
            pass

    def restore_proxy(self, proxy: dict):
        import winreg

        reg_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path, 0, winreg.KEY_SET_VALUE) as key:
            for name, saved in proxy.items():
                if saved is None:
                    try:
                        winreg.DeleteValue(key, name)
                    except FileNotFoundError:
                        pass
                else:
                    val, kind = saved
                    winreg.SetValueEx(key, name, 0, kind, val)
        self._notify_wininet()

    def set_proxy(self):
        import winreg

        reg_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, f"127.0.0.1:{self.port}")
            winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ, "<local>")
            winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1)
        self._notify_wininet()


# %%
