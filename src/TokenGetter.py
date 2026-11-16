# %%
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class TokenGetter:
    """Read and clear a token stored in the local application config.

    Login capture is intentionally not implemented here. This class must not
    change Windows proxy settings, install certificates, intercept traffic, or
    request administrator privileges.
    """

    def __init__(self):
        self.assets_path = Path(__file__).parent / "assets"

    def get_token(self):
        logger.error("未检测到本地登录凭证，请先导入本人会话文件")
        return False

    def import_token_file(self, path: str | Path):
        source = Path(path)
        data = json.loads(source.read_text(encoding="utf-8-sig"))
        token = data.get("token") if isinstance(data, dict) else None
        if not token and isinstance(data, dict) and isinstance(data.get("log"), dict):
            for entry in data["log"].get("entries", []):
                request = entry.get("request", {}) if isinstance(entry, dict) else {}
                headers = request.get("headers", []) if isinstance(request, dict) else []
                for header in headers:
                    if str(header.get("name", "")).lower() == "token" and header.get("value"):
                        token = header["value"]
        if not token:
            raise ValueError("文件中没有找到本人久事 token")
        config_path = self.assets_path / "config.json"
        config = {}
        if config_path.exists() and config_path.stat().st_size:
            try:
                config = json.loads(config_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                config = {}
        config["token"] = token
        config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        return token

    def read_token(self):
        config_path = self.assets_path / "config.json"
        if not config_path.exists():
            config_path.touch()
            logger.error("配置文件不存在，请准备本地 token")
            return None

        with config_path.open("r+", encoding="utf-8") as config_file:
            try:
                data: dict = json.load(config_file)
            except json.JSONDecodeError:
                logger.error("配置文件格式错误，初始化配置文件")
                data = {}
                config_file.truncate(0)
                config_file.seek(0)
                json.dump(data, config_file)
                return None

            token = data.get("token", {})
            if token == {}:
                logger.error("配置文件中不存在 token，请准备本地 token")
                return None
            logger.debug("读取 token: %s", token)
            return token

    def clear_token(self):
        config_path = self.assets_path / "config.json"
        if not config_path.exists():
            logger.error("配置文件不存在")
            return

        logger.error("清除 token")
        with config_path.open("r+", encoding="utf-8") as config_file:
            try:
                json_dict = json.load(config_file)
            except json.JSONDecodeError:
                logger.error("配置文件格式错误，初始化配置文件")
                json_dict = {}

            json_dict.pop("token", None)
            config_file.truncate(0)
            config_file.seek(0)
            json.dump(json_dict, config_file)
