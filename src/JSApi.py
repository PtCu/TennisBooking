# %%
import asyncio
import atexit
import base64
import hashlib
import json
import logging
import random
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

from TokenGetter import TokenGetter
from Verification import Verification

# %%
logger = logging.getLogger(__name__)
debug_mode = False

AUTH_ERROR_CODES = (
    "token.device.invalid",
    "token.force.required",
    "token.loss.effectiveness",
    "token.resolve.fail",
)


class TokenExpiredError(Exception):
    pass


class ApiResponseNotExpectedError(Exception):
    pass


class BookFailedError(Exception):
    pass


class _Type1754Runner:
    """常驻 Node 子进程执行 type__1754.js，单次 <1ms，避免 execjs 误选 JScript。"""

    def __init__(self, js_path: Path):
        self.js_path = js_path
        self._proc: subprocess.Popen | None = None
        atexit.register(self.close)

    def _ensure(self):
        if self._proc is not None and self._proc.poll() is None:
            return
        runner = (
            f"const fs = require('fs');\n"
            f"eval(fs.readFileSync({json.dumps(str(self.js_path))}, 'utf8'));\n"
            f"const rl = require('readline').createInterface({{input: process.stdin, crlfDelay: Infinity}});\n"
            f"rl.on('line', (line) => {{\n"
            f"  if (!line) return;\n"
            f"  try {{\n"
            f"    const p = JSON.parse(line);\n"
            f"    process.stdout.write(JSON.stringify({{ok: true, url: type__1754(p)}}) + '\\n');\n"
            f"  }} catch (e) {{\n"
            f"    process.stdout.write(JSON.stringify({{ok: false, err: String(e)}}) + '\\n');\n"
            f"  }}\n"
            f"}});\n"
        )
        self._proc = subprocess.Popen(
            ["node", "-e", runner],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def call(self, payload_str: str) -> str:
        self._ensure()
        assert self._proc and self._proc.stdin and self._proc.stdout
        self._proc.stdin.write(json.dumps(payload_str, ensure_ascii=False) + "\n")
        self._proc.stdin.flush()
        line = self._proc.stdout.readline().strip()
        if not line:
            raise RuntimeError("type__1754 Node 进程异常退出")
        res = json.loads(line)
        if not res.get("ok"):
            raise RuntimeError(f"type__1754 执行失败: {res.get('err')}")
        return res["url"]

    def close(self):
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None


class retry:
    def __init__(self, max_retries=10, delay=200, jitter_percent=10):
        self.max_retries = max_retries
        self.delay = delay
        self.jitter_percent = jitter_percent

    def __call__(self, func):
        async def wrapper(*args, **kwargs):
            for attempt in range(self.max_retries):
                try:
                    success = await func(*args, **kwargs)
                    attempt += 1
                except Exception:
                    logger.error("检测到异常，停止重试")
                    raise

                if success is True:
                    return True
                elif attempt < self.max_retries:
                    logger.error(f"第 {attempt} 次失败，正在重试...")
                    jitter_ms = random.uniform(
                        -self.jitter_percent / 100 * self.delay,
                        self.jitter_percent / 100 * self.delay,
                    )
                    total_delay = max(50.0, self.delay + jitter_ms)
                    await asyncio.sleep(total_delay / 1000)

            logger.error("重试次数已达上限，停止重试。")
            return False

        return wrapper


class JSApi:
    headers = {
        "Connection": "keep-alive",
        "os_type": "wechat_mini",
        "os_version": "Windows 11 x64",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090a13) XWEB/19823",
        "Content-Type": "application/json",
        "xweb_xhr": "1",
        "device_type": "microsoft",
        "gw_channel": "api",
        "Accept": "*/*",
        "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "Referer": "https://servicewechat.com/wxbd4ec54a9e9ce6dd/171/page-frame.html",
        "Accept-Encoding": "gzip, deflate, br",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    appid_get = "80bf61101d10feb1bd0229416ec73a26"
    appid_post = "0ff444f417de34c1352af3b3ffc30348"
    url_book_time = "https://jsapp.jussyun.com/jiushi-core/venue/getVenueBookTime"
    url_venue = "https://jsapp.jussyun.com/jiushi-core/venue/list"
    url_user = "https://jsapp.jussyun.com/jiushi-core/user/getUserInfo"
    url_contact = "https://jsapp.jussyun.com/jiushi-core/user/contact/v2-get/query-list-user-contact"
    url_ground = "https://jsapp.jussyun.com/jiushi-core/venue/getVenueGround"
    url_book = "https://jsapp.jussyun.com/jiushi-core/venue/bookVenue/v2"
    url_orders = "https://jsapp.jussyun.com/jiushi-core/order/v2-get/getOrderList"
    sign_key = "527093093C418483029EEC61F70E9DD1"
    sign_secret = "EB70CBAE71094894EE5BD30A68A3C548"
    aes_key1 = "jsVenueV20240101".encode()
    aes_key2 = "Bangdao01bangdao".encode()
    iv = "1234567890123456".encode()

    if debug_mode is True:
        proxy = "http://192.168.1.4:9999"
        verify = False
    else:
        proxy = None
        verify = True

    def __init__(self):
        self.headers = dict(JSApi.headers)
        self.token_getter = TokenGetter()
        self.assets_path = Path(__file__).parent / "assets"
        self.verification = Verification(self.assets_path / "trace.json")
        self.type_runner = _Type1754Runner(self.assets_path / "type__1754.js")

    def close(self):
        self.type_runner.close()

    def get_token(self, allow_proxy: bool = False):
        if self.token_getter.get_token(allow_proxy=allow_proxy) is True:
            token = self.token_getter.read_token()
            if token is not None:
                self.headers["token"] = token
                return True
        return False

    def get_token_via_proxy(self):
        if self.token_getter.get_token_via_proxy() is True:
            token = self.token_getter.read_token()
            if token is not None:
                self.headers["token"] = token
                return True
        return False

    def read_token(self):
        token = self.token_getter.read_token()
        if token is not None:
            self.headers["token"] = token
            return True
        return False

    def clear_token(self):
        self.token_getter.clear_token()

    def post_sign(self, payload_str):
        sign_str = f"{payload_str}{self.sign_key}"
        payload_bytes = sign_str.encode("utf-8")
        md5_hash = hashlib.md5(payload_bytes).hexdigest()
        return base64.b64encode(md5_hash.encode("utf-8")).decode("utf-8")

    def get_sign(self, params):
        sign_str = "".join([f"&{k}={v}" for k, v in sorted(params.items())])
        sign_str += self.sign_secret
        sign_str = sign_str.lstrip("&")
        params_bytes = sign_str.encode("utf-8")
        md5_hash = hashlib.md5(params_bytes).hexdigest().upper()
        return base64.b64encode(md5_hash.encode("utf-8")).decode("utf-8")

    def get_cipher_text(self, payload):
        payload_str = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        logger.debug(f"payload_str before cipher: {payload_str}")
        return self.aes_encrypt(payload_str)

    def aes_encrypt(self, data, key=aes_key1, iv=iv):
        cipher = AES.new(key=key, mode=AES.MODE_CBC, iv=iv)
        padded_data = pad(data.encode("utf-8"), cipher.block_size)
        encrypted = cipher.encrypt(padded_data)
        return base64.b64encode(encrypted).decode("utf-8")

    def aes_decrypt(self, cipher_text, key=aes_key1, iv=iv):
        cipher = AES.new(key=key, mode=AES.MODE_CBC, iv=iv)
        encrypted_data = base64.b64decode(cipher_text)
        decrypted = cipher.decrypt(encrypted_data).decode("utf-8")
        return decrypted

    def _check_auth_error(self, response: httpx.Response):
        if response.status_code == 401:
            raise TokenExpiredError("token invalid (HTTP 401)")
        for code in AUTH_ERROR_CODES:
            if code in response.text:
                raise TokenExpiredError(code)

    async def js_post(self, url, payload, params=None):
        payload_str = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        js_sign = self.post_sign(payload_str)

        req_headers = dict(self.headers)
        req_headers["gw_channel"] = "api"
        req_headers["js_sign"] = js_sign
        req_headers["app_id"] = self.appid_post
        if hasattr(self, "phone"):
            req_headers["fullMobile"] = self.phone

        async with httpx.AsyncClient(
            proxy=self.proxy, verify=self.verify, trust_env=False, timeout=25
        ) as client:
            if params is not None:
                response = await client.post(
                    url, headers=req_headers, params=params, content=payload_str.encode("utf-8")
                )
            else:
                response = await client.post(
                    url, headers=req_headers, content=payload_str.encode("utf-8")
                )

        self._check_auth_error(response)
        if response.status_code == 200:
            return response
        raise httpx.HTTPStatusError(
            f"HTTP Error: {response.status_code}", request=response.request, response=response
        )

    async def js_get(self, url, params):
        req_params = dict(params)
        req_params["sign"] = self.get_sign(req_params)
        req_headers = dict(self.headers)
        req_headers.pop("gw_channel", None)
        req_headers.pop("js_sign", None)
        req_headers["app_id"] = self.appid_get
        if hasattr(self, "phone"):
            req_headers["fullMobile"] = self.phone

        async with httpx.AsyncClient(
            proxy=self.proxy, verify=self.verify, trust_env=False, timeout=25
        ) as client:
            response = await client.get(url, headers=req_headers, params=req_params)

        self._check_auth_error(response)
        if response.status_code == 200:
            return response
        raise httpx.HTTPStatusError(
            f"HTTP Error: {response.status_code}", request=response.request, response=response
        )

    async def get_ground(self, venue_id, timestamp):
        payload = {"venueId": str(venue_id), "bookTime": str(timestamp)}
        resp = await self.js_post(self.url_ground, payload)
        data = resp.json()
        return data.get("data", {}).get("statusList", []) or []

    async def get_venue_list(self, sports_name):
        payload = {"sportName": sports_name, "userLatitude": "", "userLongitude": ""}
        resp = await self.js_post(self.url_venue, payload)
        return resp.json()["data"]

    async def get_user_info(self):
        resp = await self.js_post(self.url_user, {})
        user_info = resp.json()["data"]
        self.user_info = user_info
        if user_info.get("fullMobile"):
            phone = self.aes_decrypt(user_info["fullMobile"], key=self.aes_key2)
            self.phone = self.remove_non_printable(phone)
        return user_info

    async def get_contact(self):
        params = {
            "inWhite": "false",
            "os_type": "wechat_mini",
            "uid": self.user_info["uid"],
        }
        resp = await self.js_get(self.url_contact, params)
        data = resp.json()
        contacts = []
        for group in data.get("data") or []:
            contacts.extend(group.get("contacts") or [])
        self.contacts = contacts
        return contacts

    async def get_latest_orders(self, page_size: int = 5):
        params = {
            "inWhite": "false",
            "os_type": "wechat_mini",
            "page": "1",
            "pageSize": str(page_size),
            "uid": self.user_info["uid"],
        }
        resp = await self.js_get(self.url_orders, params)
        data = resp.json().get("data") or {}
        return int(data.get("currentTime") or int(datetime.now().timestamp() * 1000)), (
            data.get("orderDetailModelList") or []
        )

    def datetime_to_midnight_timestamp(self, dt: datetime):
        dt_midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        return int(dt_midnight.timestamp()) * 1000

    def get_timestamps_from_now(self, days=8):
        now = datetime.now()
        dts = [now + timedelta(days=_days) for _days in range(days)]
        return [self.datetime_to_midnight_timestamp(dt) for dt in dts]

    async def notify_serverchan(self, title: str, desp: str) -> tuple[bool, str]:
        key = self.token_getter.load_serverchan_key()
        if not key:
            return False, "未配置 Server酱 SendKey"
        url = f"https://sctapi.ftqq.com/{key}.send"
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=12) as client:
                r = await client.post(url, data={"title": title, "desp": desp})
            j = r.json()
            if r.status_code == 200 and j.get("code") == 0:
                return True, "已通过 Server酱发送微信提醒"
            return False, f"Server酱返回失败: {j.get('message') or j}"
        except Exception as e:
            return False, f"Server酱发送异常: {e}"

    async def post_book(
        self,
        contact_id,
        venue_id,
        venue_name,
        agency_id,
        agency_name,
        sports_name,
        ground_list,
        dry_run: bool = False,
    ):
        payload_obj = {
            "contactId": contact_id,
            "venueId": str(venue_id),
            "venueName": venue_name,
            "agencyId": str(agency_id),
            "agencyName": agency_name,
            "sportName": sports_name,
            "venueBookGroundModelList": ground_list,
            "voucherFlowId": None,
            "timestamp": int(datetime.now().timestamp() * 1000),
        }

        payload = {"ciphertext": self.get_cipher_text(payload_obj)}
        payload_str = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        logger.debug(f"payload_str after cipher: {payload_str}")

        if dry_run:
            logger.error(f"[仅演练] 已生成下单密文 (len={len(payload['ciphertext'])})，未实际提交")
            return True

        url_book = self.type_runner.call(payload_str)
        resp = await self.js_post(url_book, payload)

        if resp.headers.get("Content-Type", "").startswith("text/html") or "<html" in resp.text[:200].lower():
            logger.error("触发阿里云 WAF 滑块验证，正在自动处理...")
            html_path = self.save_html(resp.text)
            try:
                url_verified = await self.verification.solve(html_path)
            except Exception as e:
                logger.error(f"滑块验证码处理失败: {e}")
                logger.debug(e, exc_info=True)
                return False
            url_parsed = urlparse(url_verified)
            query_params = parse_qs(url_parsed.query)
            resp = await self.js_post(self.url_book, payload, params=query_params)

        try:
            resp_data = resp.json()
        except Exception as e:
            logger.debug("post_book() 返回数据格式异常")
            logger.debug(resp.text)
            logger.debug(e, exc_info=True)
            raise ApiResponseNotExpectedError("API response not as expected")

        return_code = resp_data.get("rtnCode")
        return_msg = resp_data.get("rtnMessage")
        if return_code != "10000":
            logger.error(f"预约失败: {return_msg} (错误代码: {return_code})")
            return False

        logger.error("🎉 下单成功，请前往微信小程序「待支付」付款！")
        desc_slots = " / ".join(
            f"{g.get('groundName')} {datetime.fromtimestamp(int(g['startTime'])/1000):%m-%d %H:%M}"
            for g in ground_list
        )
        desp = f"场馆: {venue_name}\n场次: {desc_slots}"
        try:
            _, orders = await self.get_latest_orders(5)
            for o in orders:
                if str(o.get("venueId", "")) == str(venue_id) and o.get("payStatus") in ("PAY_WAIT", "CREATED"):
                    ddl = datetime.fromtimestamp(int(o["orderPayMaxTime"]) / 1000).strftime("%Y-%m-%d %H:%M:%S")
                    desp += f"\n订单号: {o.get('orderId')}\n付款截止: {ddl}"
                    logger.error(f"订单号: {o.get('orderId')} | 请于 {ddl} 前付款")
                    break
        except Exception:
            pass

        ok, sc_msg = await self.notify_serverchan(f"久事抢场成功: {venue_name}", desp)
        if ok:
            logger.error(sc_msg)
        else:
            logger.error(f"微信提醒未发送: {sc_msg}")
        return True

    def save_html(self, html_content: str, filename: str = "v2.html"):
        url_pattern = re.compile(r"(src ?= ?['\"])//")
        html_content = url_pattern.sub(r"\1https://", html_content)
        html_content = html_content.replace(",t.submit()", "")
        html_path = self.assets_path / filename
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        return html_path

    def remove_non_printable(self, s: str):
        return "".join(ch for ch in s if ch.isprintable())
