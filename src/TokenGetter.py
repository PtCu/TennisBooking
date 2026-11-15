# %%
import json
import ctypes
import logging
import os
import subprocess
import sys
import time
import winreg
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)


class TokenGetter:
    def __init__(self):
        self.port = 8012
        self.assets_path = Path(__file__).parent / "assets"
        self.src_path = Path(__file__).parent
        self.proxy_state_path = self.assets_path / "proxy-state.json"
        self.recover_proxy()

    def recover_proxy(self):
        if not self.proxy_state_path.exists():
            return
        try:
            proxy = json.loads(self.proxy_state_path.read_text(encoding="utf-8"))
            if isinstance(proxy, dict):
                if str(proxy.get("ProxyEnable")) == "1" and not proxy.get("ProxyServer"):
                    logger.error("上次代理恢复记录无效，保留当前系统代理设置")
                else:
                    logger.warning("检测到上次登录未完成，正在恢复系统代理设置")
                    self.restore_proxy(proxy)
        except Exception as error:
            logger.error("恢复系统代理设置失败，请检查网络代理")
            logger.debug(error, exc_info=True)
        finally:
            self.proxy_state_path.unlink(missing_ok=True)

    def run_mitmproxy(self):
        subprocess.run(
            args=[
                (self.assets_path / "mitmdump").as_posix(),
                "-s",
                (self.src_path / "RequestLogger.py").as_posix(),
                "-q",
                "-p",
                str(self.port),
            ]
        )

    def wait_for_proxy(self, timeout=8):
        proxy_url = f"http://127.0.0.1:{self.port}"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                response = httpx.get(
                    "http://mitm.it/",
                    proxy=proxy_url,
                    trust_env=False,
                    timeout=1,
                )
                if response.status_code < 500:
                    return True
            except Exception:
                pass
            time.sleep(0.25)
        logger.error("mitmproxy 启动失败，请检查本机代理端口")
        return False

    def get_token(self):
        logger.error("准备获取 token, 请稍等...")
        proxy = self.back_up_proxy()
        self.proxy_state_path.write_text(json.dumps(proxy), encoding="utf-8")
        self.set_proxy()

        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(self.run_mitmproxy)
                if not self.check_cert():
                    if not self.wait_for_proxy() or not self.install_cert():
                        self.restore_proxy(proxy)
                        executor.shutdown(wait=False)
                        return False

                logger.error("请打开久事体育APP小程序并登录")
                future.result()
                logger.error("成功获取到 token, 可以退出小程序了")
                return True
        except Exception as e:
            logger.error("获取 token 过程中出现错误")
            logger.debug(e, exc_info=True)
            return False
        finally:
            self.restore_proxy(proxy)
            self.proxy_state_path.unlink(missing_ok=True)

    def read_token(self):
        config_path = self.assets_path / "config.json"
        if not config_path.exists():
            with open(config_path, "w") as f:
                pass
            logger.error("配置文件不存在，请先登录")
            return None

        with open(config_path, "r+") as f:
            try:
                data: dict = json.load(f)
            except json.JSONDecodeError:
                logger.error("配置文件格式错误，初始化配置文件")
                data = {}
                f.truncate(0)
                f.seek(0)
                json.dump(data, f)
                return None

            token = data.get("token", {})
            if token == {}:
                logger.error("配置文件中不存在 token，请先登录")
                return None
            logger.debug(f"读取 token: {token}")
            return token

    def clear_token(self):
        config_path = self.assets_path / "config.json"
        if config_path.exists():
            logger.error("清除 token")
            with open(config_path, "r+") as f:
                try:
                    json_dict = json.load(f)
                except json.JSONDecodeError:
                    logger.error("配置文件格式错误，初始化配置文件")
                    json_dict = {}

                if "token" in json_dict:
                    del json_dict["token"]
                f.truncate(0)
                f.seek(0)
                json.dump(json_dict, f)
        else:
            logger.error("配置文件不存在")

    def check_cert(self):
        for args in (["certutil", "-user", "-store", "root"], ["certutil", "-store", "root"]):
            try:
                cert_info = subprocess.run(
                    args,
                    capture_output=True,
                    check=True,
                    encoding="gb2312",
                    errors="replace",
                ).stdout
            except Exception as error:
                logger.debug(error, exc_info=True)
                continue
            if "mitmproxy" in cert_info:
                return True
        logger.error("未检测到证书，将自动安装到当前用户证书库")
        return False

    def install_cert(self):
        logger.error("正在下载证书...")
        cert_url = "http://mitm.it/cert/cer"
        proxy_url = f"http://127.0.0.1:{self.port}"

        try:
            response = httpx.get(
                cert_url,
                proxy=proxy_url,
                trust_env=False,
                timeout=5,
            )
            response.raise_for_status()
        except Exception as e:
            logger.error("证书下载失败，请检查网络连接")
            logger.debug(e, exc_info=True)
            return False

        with open(self.assets_path / "mitmproxy-ca-cert.cer", "wb") as f:
            f.write(response.content)

        logger.error("证书下载成功，正在安装证书...")

        cert_path = self.assets_path / "mitmproxy-ca-cert.cer"
        if self.install_cert_file(cert_path, user=True):
            logger.error("证书已安装到当前用户，继续登录")
            return True

        logger.error("当前用户证书库安装失败，正在请求 Windows 权限")
        return self.install_cert_with_elevation(cert_path)

    @staticmethod
    def install_cert_file(cert_path: Path, *, user: bool) -> bool:
        args = ["certutil"]
        if user:
            args.append("-user")
        args.extend(["-addstore", "root", cert_path.as_posix()])
        try:
            subprocess.run(args, capture_output=True, check=True)
        except Exception as error:
            logger.debug(error, exc_info=True)
            return False
        return True

    def install_cert_with_elevation(self, cert_path: Path) -> bool:
        if os.name != "nt":
            return False

        result_path = cert_path.with_suffix(".install-result")
        result_path.unlink(missing_ok=True)
        if getattr(sys, "frozen", False):
            executable = sys.executable
            arguments = ["--install-cert", cert_path.as_posix(), result_path.as_posix()]
        else:
            executable = sys.executable
            arguments = [str(Path(__file__).resolve()), "--install-cert", cert_path.as_posix(), result_path.as_posix()]

        params = subprocess.list2cmdline(arguments)
        result = ctypes.windll.shell32.ShellExecuteW(None, "runas", executable, params, None, 1)
        if result <= 32:
            logger.error("用户取消了证书权限确认")
            return False

        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if result_path.exists():
                success = result_path.read_text(encoding="ascii").strip() == "ok"
                result_path.unlink(missing_ok=True)
                if success:
                    logger.error("证书安装成功，继续登录")
                return success
            time.sleep(0.25)
        logger.error("证书权限确认超时")
        return False

    def back_up_proxy(self):
        path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            values = {}
            for name, default in (("ProxyEnable", 0), ("ProxyServer", ""), ("ProxyOverride", "")):
                try:
                    values[name] = winreg.QueryValueEx(key, name)[0]
                except FileNotFoundError:
                    values[name] = default
        return values

    def restore_proxy(self, proxy: dict):
        path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, int(proxy.get("ProxyEnable", 0)))
            winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, str(proxy.get("ProxyServer", "")))
            winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ, str(proxy.get("ProxyOverride", "")))

    def set_proxy(self):
        path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, f"127.0.0.1:{self.port}")
            winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ, "<local>")


# %%
