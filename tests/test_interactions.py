import json
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from app import Grounder
from TokenGetter import TokenGetter
from GroundSelectScreen import GroundSelectScreen, GroundButton


class InteractionTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_plan_and_orders_dialog(self):
        with patch.object(TokenGetter, "read_token", return_value=None), patch("OrdersScreen.trackable_records", return_value=[]):
            app = Grounder()
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                app.query_one("#primary-plan-type").value = "single_slot"
                await pilot.pause()
                self.assertEqual(app.query_one("#primary-plan-count").value, "1")
                self.assertTrue(app.query_one("#primary-plan-count").disabled)
                self.assertTrue(await pilot.click("#orders-button"))
                await pilot.pause()
                self.assertEqual(type(app.screen).__name__, "OrdersScreen")
                self.assertTrue(app.screen.query_one("#order-resume").disabled)
                await pilot.click("#orders-close")

    async def test_candidate_selection_survives_tab_change_and_reopen(self):
        day = (datetime.now() + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        stamp = int((day + timedelta(hours=12)).timestamp() * 1000)
        midnight = int(day.timestamp() * 1000)
        selected = {"groundId": "1", "groundName": "1号", "startTime": stamp, "endTime": stamp+3600000,
                    "sportsType": "网球", "price": "100", "status": "1"}
        row = {"startTime": stamp, "endTime": stamp+3600000, "blockModel": [
            {key: selected[key] for key in ("groundId", "groundName", "sportsType", "price", "status")} ]}
        with patch.object(TokenGetter, "read_token", return_value=None):
            app = Grounder()
            async with app.run_test(size=(140, 45)) as pilot:
                await pilot.pause()
                api = app.js_api
                api.get_ground = AsyncMock(return_value=[row])
                api.get_open_dates = AsyncMock(return_value={day.date()})
                api.get_timestamps_from_now = lambda: [midnight, midnight+86400000]
                app.query_one("#ground-venue-select").set_options([("场馆", json.dumps({"venue_id": "v"}))])
                app.query_one("#ground-venue-select").value = json.dumps({"venue_id": "v"})
                screen = GroundSelectScreen([selected])
                await app.push_screen(screen)
                await pilot.pause()
                self.assertTrue(screen.query_one(GroundButton).selected)
                tabs = screen.query_one("#ground-tabs")
                tabs.active = f"ground-tab-{midnight+86400000}"
                await pilot.pause()
                tabs.active = f"ground-tab-{midnight}"
                await pilot.pause()
                active_button = screen.query_one(f"#ground-table-{midnight}").query_one(GroundButton)
                self.assertTrue(active_button.selected)
                await pilot.click(active_button)
                await pilot.pause()
                self.assertEqual(screen.candidate_selection, {})
                await pilot.click(active_button)
                await pilot.pause()
                self.assertEqual(len(screen.candidate_selection), 1)
                await pilot.click("#ground-cancel-button")
