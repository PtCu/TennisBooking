"""Server酱 Turbo 手机提醒；不接触久事会话或支付参数。"""
from __future__ import annotations

import ctypes
import json
import os
import re
import urllib.request
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, new_url):
        raise NotificationError("提醒服务返回跳转，请检查服务状态")


class NotificationError(Exception):
    pass


def _dpapi(value: bytes, decrypt=False) -> bytes:
    if os.name != "nt":
        raise NotificationError("手机提醒密钥仅支持在当前 Windows 用户下保存")
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(value)
    source = Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        fn = crypt.CryptUnprotectData
        fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        ok = fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
    else:
        fn = crypt.CryptProtectData
        fn.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        ok = fn(ctypes.byref(source), "Jiushi phone notification", None, None, None, 1, ctypes.byref(target))
    if not ok:
        raise NotificationError("无法读取或保存手机提醒密钥，请在当前 Windows 用户下重新设置")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(ctypes.cast(target.data, ctypes.c_void_p))


@dataclass(frozen=True)
class PhoneSettings:
    key: str = field(default="", repr=False)

    def validate(self):
        if not re.fullmatch(r"SCT[A-Za-z0-9_-]{10,220}", self.key):
            raise NotificationError("请填写 Server酱 Turbo 的 SCT 开头 SendKey（不是 Server酱³ 密钥）")
        return self

    def save(self, directory):
        import base64
        self.validate()
        Path(directory).mkdir(parents=True, exist_ok=True)
        protected = base64.b64encode(_dpapi(self.key.encode())).decode()
        path = Path(directory) / "phone-settings.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"provider": "serverchan-turbo", "protected_key": protected}), encoding="utf-8")
        temporary.replace(path)

    @classmethod
    def load(cls, directory):
        import base64
        path = Path(directory) / "phone-settings.json"
        if not path.exists():
            return cls()
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if value.get("provider") != "serverchan-turbo":
                raise ValueError()
            return cls(_dpapi(base64.b64decode(value["protected_key"], validate=True), decrypt=True).decode()).validate()
        except Exception:
            raise NotificationError("手机提醒配置无法读取，请在本机重新保存 SendKey") from None


class PhoneNotifier:
    def __init__(self, settings, timeout=8, opener=None):
        self.settings = settings.validate()
        self.timeout = timeout
        self.opener = opener or urllib.request.build_opener(NoRedirect())

    def send(self, title, content):
        """成功只表示服务接受推送，绝不声称用户已收到或阅读。"""
        body = json.dumps({"title": title[:32], "desp": content}, ensure_ascii=False).encode()
        request = urllib.request.Request("https://sctapi.ftqq.com/" + self.settings.key + ".send", data=body,
                                         headers={"Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                if response.status != 200:
                    raise ValueError()
                raw = response.read(65537)
                if len(raw) > 65536:
                    raise ValueError()
                value = json.loads(raw)
                if not isinstance(value, dict) or type(value.get("code")) is not int or value["code"] != 0:
                    raise ValueError()
                detail = value.get("data")
                if isinstance(detail, dict) and detail.get("errno", 0) != 0:
                    raise ValueError()
        except Exception:
            # HTTP异常可能带含密钥的URL，服务错误正文也不回显。
            raise NotificationError("手机提醒服务未确认接受，请检查网络、SendKey或服务额度；订单不会重复提交") from None


def optional_notifier(directory):
    settings = PhoneSettings.load(directory)
    return PhoneNotifier(settings) if settings.key else None

