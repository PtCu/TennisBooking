# nuitka-project: --mode=onefile
# nuitka-project: --windows-icon-from-ico={MAIN_DIRECTORY}/assets/grounder.ico
# nuitka-project: --include-data-files={MAIN_DIRECTORY}/grounder.css=grounder.css
# nuitka-project: --include-data-files={MAIN_DIRECTORY}/assets/trace.json=./assets/trace.json
# nuitka-project: --include-data-files={MAIN_DIRECTORY}/assets/type__1754.js=./assets/type__1754.js
# nuitka-project: --include-package-data=emoji
# nuitka-project: --include-package-data=grapheme
# nuitka-project: --onefile-tempdir-spec={CACHE_DIR}/Grounder/{VERSION}
# nuitka-project: --onefile-cache-mode=cached
# nuitka-project: --output-filename=Grounder.exe
# nuitka-project: --company-name=https://github.com/XIGUAjuice
# nuitka-project: --product-name=Grounder
# nuitka-project: --product-version=1.4.0
# nuitka-project: --copyright=https://github.com/XIGUAjuice
# %%
import logging
import sys
import asyncio
from datetime import datetime
from pathlib import Path
from runtime_env import configure_runtime

configure_runtime()

from app import Grounder
from storage import DATA_DIR

logger = logging.getLogger(__name__)


def config_logger():
    time_str = datetime.now().strftime("%Y_%m_%d_%H%M%S")
    log_path = DATA_DIR / "logs"
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
    if sys.argv[1:] == ["--check"]:
        import execjs
        import json
        try:
            ready = execjs.eval("1 + 1") == 2
            print(json.dumps({"ready": ready, "javascript": execjs.get().name}))
            sys.exit(0 if ready else 1)
        except Exception:
            print(json.dumps({"ready": False, "error": "JavaScript runtime unavailable"}))
            sys.exit(1)
    app = None
    try:
        config_logger()
        app = Grounder()
        app.run()
        return_code = app.return_code
    except Exception as e:
        logger.exception("发生未知错误，程序终止")
        return_code = 1
    finally:
        broswer = getattr(getattr(getattr(app, "js_api", None), "verification", None), "browser", None)
        if broswer:
            asyncio.run(broswer.stop())
        sys.exit(return_code)
