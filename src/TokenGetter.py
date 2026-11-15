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
        logger.error("未检测到本地登录凭证")
        logger.error("为保护系统网络，程序不会修改 Windows 代理、安装证书或拦截微信流量")
        logger.error("请准备有效的本地 token 后再点击登录")
        return False

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
