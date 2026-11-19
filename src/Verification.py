# %%
import json
import logging
from pathlib import Path

import zendriver as zd

# %%
logger = logging.getLogger(__name__)


class Verification:
    def __init__(self, trace_path):
        with open(trace_path, "r", encoding="utf-8") as f:
            trace = json.load(f)["trace"]
        trace = [
            (x2 - x1, y2 - y1) for (x1, y1), (x2, y2) in zip(trace[:-1], trace[1:])
        ]
        self.trace = trace
        self.browser = None

    async def move_slider(self, slider: zd.Element):
        for dx, dy in self.trace:
            await slider.mouse_drag((dx, dy), relative=True, steps=3)

    async def start_browser(self):
        if self.browser is None:
            self.browser = await zd.start(headless=True)

    async def solve(self, html_path):
        if self.browser is None:
            await self.start_browser()
        page = await self.browser.get(f"file://{Path(html_path).resolve()}")
        slider = await page.find("div[id='aliyunCaptcha-sliding-slider']")
        await self.move_slider(slider)
        form = (await page.xpath("//form[@action]"))[0]
        return form.attrs["action"]


if __name__ == "__main__":
    import asyncio

    assets_path = Path(__file__).parent / "assets"
    verification = Verification(assets_path / "trace.json")
    asyncio.run(verification.start_browser())
