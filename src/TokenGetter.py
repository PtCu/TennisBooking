import json
from pathlib import Path
from urllib.parse import urlsplit

from storage import DATA_DIR, write_json


def validate_token(token):
    if not isinstance(token, str) or not token.strip() or len(token) > 16384:
        raise ValueError("请填写有效的登录凭证")
    if any(character.isspace() or ord(character) < 32 for character in token):
        raise ValueError("登录凭证含有空白或控制字符")
    return token


class TokenGetter:
    def __init__(self, config_path=None):
        self.config_path = Path(config_path) if config_path else DATA_DIR / "config.json"
        self.legacy_path = Path(__file__).parent / "assets" / "config.json"

    def get_token(self):
        return False

    @staticmethod
    def token_from_file(path):
        source = Path(path)
        if source.stat().st_size > 100_000_000:
            raise ValueError("会话文件过大，请只导出久事请求")
        try:
            data = json.loads(source.read_text(encoding="utf-8-sig"))
        except (ValueError, UnicodeError):
            raise ValueError("请选择有效的 HAR 或 JSON 文件") from None
        if not isinstance(data, dict):
            raise ValueError("会话文件格式无效")
        if "token" in data:
            return validate_token(data["token"])
        if data.get("host") == "jsapp.jussyun.com" and isinstance(data.get("headers"), dict):
            return validate_token(data["headers"].get("token"))
        log = data.get("log", {})
        entries = log.get("entries", []) if isinstance(log, dict) else []
        token = None
        for entry in entries if isinstance(entries, list) else []:
            request = entry.get("request", {}) if isinstance(entry, dict) else {}
            if not isinstance(request, dict):
                continue
            url = urlsplit(request.get("url", ""))
            if (url.scheme != "https" or url.hostname != "jsapp.jussyun.com"
                    or url.port not in (None, 443) or not url.path.startswith("/jiushi-core/")):
                continue
            for header in request.get("headers", []):
                if isinstance(header, dict) and str(header.get("name", "")).lower() == "token":
                    token = validate_token(header.get("value"))
        if token is None:
            raise ValueError("文件中没有久事登录凭证，请选择本人登录后的久事请求")
        return token

    def _read_config(self):
        path = self.config_path if self.config_path.exists() else self.legacy_path
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (ValueError, UnicodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def save_token(self, token):
        config = self._read_config()
        config["token"] = validate_token(token)
        write_json(self.config_path, config)

    def read_token(self):
        try:
            return validate_token(self._read_config().get("token"))
        except ValueError:
            return None

    def clear_token(self):
        config = self._read_config()
        config.pop("token", None)
        write_json(self.config_path, config)
