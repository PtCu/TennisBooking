# nuitka-project: --mode=onefile
# nuitka-project: --windows-icon-from-ico={MAIN_DIRECTORY}/assets/grounder.ico
# nuitka-project: --include-data-files={MAIN_DIRECTORY}/grounder.css=grounder.css
# nuitka-project: --include-data-files={MAIN_DIRECTORY}/assets/trace.json=./assets/trace.json
# nuitka-project: --include-data-files={MAIN_DIRECTORY}/assets/type__1754.js=./assets/type__1754.js
# nuitka-project: --include-data-files={MAIN_DIRECTORY}/RequestLogger.py=./RequestLogger.py
# nuitka-project: --include-data-files={MAIN_DIRECTORY}/assets/mitmdump.exe=./assets/mitmdump.exe
# nuitka-project: --include-package-data=emoji
# nuitka-project: --include-package-data=grapheme
# nuitka-project: --onefile-tempdir-spec={CACHE_DIR}/Grounder/{VERSION}
# nuitka-project: --onefile-cache-mode=cached
# nuitka-project: --output-filename=Grounder.exe
# nuitka-project: --company-name=https://github.com/XIGUAjuice
# nuitka-project: --product-name=Grounder
# nuitka-project: --product-version=1.0.0
# nuitka-project: --copyright=https://github.com/XIGUAjuice
# %%
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from app import Grounder

logger = logging.getLogger(__name__)


def config_logger():
    time_str = datetime.now().strftime("%Y_%m_%d_%H%M%S")
    log_path = SRC_DIR / "logs"
    log_path.mkdir(parents=True, exist_ok=True)
    log_file = log_path / f"Grounder_{time_str}.log"
    logging.basicConfig(
        handlers=[
            logging.FileHandler(log_file, mode="w", encoding="utf-8"),
        ],
        level=logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


if __name__ == "__main__":
    app = None
    return_code = 0
    try:
        config_logger()
        app = Grounder()
        app.run()
        return_code = app.return_code or 0
    except Exception:
        logger.exception("发生未知错误，程序终止")
        return_code = 1
    finally:
        js_api = getattr(app, "js_api", None)
        ver = getattr(js_api, "verification", None)
        browser = getattr(ver, "browser", None)
        if browser:
            asyncio.run(browser.stop())
        if js_api is not None:
            js_api.close()
        sys.exit(return_code)
