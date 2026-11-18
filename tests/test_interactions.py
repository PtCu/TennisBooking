import json
import sys
import unittest
import tempfile
from functools import partial
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from app import Grounder
from TokenGetter import TokenGetter
from GroundSelectScreen import GroundSelectScreen, GroundButton
from FilePickerScreen import FilePickerScreen
from orders import track_payment
from textual.widgets import DirectoryTree, Collapsible


class InteractionTests(unittest.IsolatedAsyncioTestCase):
    async def test_booking_confirmation_follows_fallback_order_through_payment(self):
        day = (datetime.now() + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        start = int((day + timedelta(hours=12)).timestamp() * 1000)
        end = start + 3600000
        blocks = [
            {"groundId": str(number), "groundName": f"{number}号", "sportsType": "网球", "price": "100", "status": "1"}
            for number in (1, 2)
        ]
        inventory = [{"startTime": start, "endTime": end, "blockModel": blocks}]
        venue = {"venueId": "v", "venueName": "网球馆", "agencyId": "a", "agencyName": "体育中心"}
        order_list = {"totalNum": 1, "orderDetailModelList": [{"orderId": "o1", "venueId": "v"}]}
        waiting_order = {
            "orderDetailModel": {"orderId": "o1", "venueId": "v", "payAmount": 10000,
                                 "payStatus": "PAY_WAIT", "orderPayMaxTime": start, "currentTime": start - 900000},
            "venueBookGroundModelList": [{"groundId": "1", "groundName": "1号", "startTime": start, "endTime": end}],
        }
        paid_order = dict(waiting_order, orderDetailModel=dict(waiting_order["orderDetailModel"], payStatus="PAY_SUCCESS"))
        notifier = Mock()
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(TokenGetter, "read_token", return_value=None),
            patch("GroundInfoPanel.DATA_DIR", Path(directory)),
            patch("GroundInfoPanel.track_payment", new=partial(track_payment, interval=0, checks=3)),
            patch("orders.optional_notifier", return_value=notifier),
        ):
            app = Grounder()
            async with app.run_test(size=(140, 45)) as pilot:
                await pilot.pause()
                api = app.js_api
                api.session_copy = lambda: api
                api.get_venue_list = AsyncMock(return_value=[venue])
                api.get_open_dates = AsyncMock(return_value={day.date()})
                api.get_ground = AsyncMock(return_value=inventory)
                api.get_timestamps_from_now = lambda: [int(day.timestamp() * 1000)]
                api.post_book = AsyncMock(return_value=True)
                api.get_order_data = AsyncMock(side_effect=[
                    {"totalNum": 0, "orderDetailModelList": []},
                    order_list, waiting_order, waiting_order, waiting_order, paid_order,
                ])
                contact = app.query_one("#user-contact-select")
                contact.set_options([("测试包场人", "contact")])
                contact.value = "contact"
                app.query_one("#ground-sports-select").value = "网球"
                await pilot.pause()
                venue_select = app.query_one("#ground-venue-select")
                self.assertTrue(await pilot.click("#ground-button"))
                await app.workers.wait_for_complete()
                self.assertEqual(type(app.screen).__name__, "Screen")
                self.assertEqual(api.get_ground.await_count, 0)
                venue_select.focus()
                await pilot.press("enter", "down", "enter")
                await pilot.pause()
                self.assertIsNotNone(venue_select.selection)
                await pilot.click("#ground-button")
                await pilot.pause()
                for button in app.screen.query(GroundButton):
                    await pilot.click(button)
                await pilot.click("#ground-confirm-button")
                await pilot.pause()
                app.query_one("#fallback-one-type").value = "single_slot"
                await pilot.pause()
                app.query_one("#order-button").focus()
                await pilot.press("enter")
                await pilot.pause()
                self.assertEqual(type(app.screen).__name__, "ConfirmScreen")
                api.post_book.assert_not_awaited()
                await pilot.click("#confirm-start")
                await app.workers.wait_for_complete()
                api.post_book.assert_awaited_once()
                self.assertEqual([slot["groundId"] for slot in api.post_book.await_args.kwargs["ground_list"]], ["1"])
                notifier.send.assert_called_once()
                record_path, = (Path(directory) / "orders").glob("*.json")
                record = json.loads(record_path.read_text(encoding="utf-8"))
                self.assertEqual(record["state"], "payment-success")

    async def test_session_file_picker_returns_path_to_login(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(TokenGetter, "read_token", return_value=None):
            file = Path(directory) / "session.json"
            file.write_text('{"token":"example"}', encoding="utf-8")
            with patch("SessionScreen.FilePickerScreen", side_effect=lambda: FilePickerScreen(directory)):
                app = Grounder()
                async with app.run_test(size=(80, 25)) as pilot:
                    await pilot.pause()
                    await pilot.click("#user-login-button")
                    await pilot.pause()
                    app.screen.query_one(Collapsible).collapsed = False
                    await pilot.pause()
                    app.screen.query_one("#session-browse").focus()
                    await pilot.pause()
                    await pilot.press("enter")
                    await pilot.pause()
                    tree = app.screen.query_one(DirectoryTree)
                    tree.post_message(DirectoryTree.FileSelected(tree.root, file))
                    await pilot.pause()
                    self.assertEqual(app.screen.query_one("#session-path").value, str(file))
                    app.screen.query_one("#session-cancel").focus()
                    await pilot.pause()
                    await pilot.press("enter")

    async def test_narrow_window_actions_and_dialogs_are_reachable(self):
        with patch.object(TokenGetter, "read_token", return_value=None), patch("OrdersScreen.trackable_records", return_value=[]):
            app = Grounder()
            async with app.run_test(size=(80, 25)) as pilot:
                await pilot.pause()
                for selector in ("#ground-venue-select", "#ground-button", "#preview-button", "#order-button", "#stop-button"):
                    widget = app.query_one(selector)
                    self.assertLessEqual(widget.region.right, 79)
                button = app.query_one("#preview-button")
                button.focus(scroll_visible=False)
                button.scroll_visible(animate=False, force=True, top=True)
                await pilot.pause()
                self.assertTrue(await pilot.click(button))
                button = app.query_one("#stop-button")
                self.assertTrue(await pilot.click(button))
                app.query_one("#user-login-button").scroll_visible(animate=False, force=True)
                await pilot.pause()
                self.assertTrue(await pilot.click("#user-login-button"))
                await pilot.pause()
                dialog = app.screen.query_one("#session-dialog")
                self.assertLessEqual(dialog.region.right, 80)
                self.assertLessEqual(dialog.region.bottom, 25)
                self.assertTrue(await pilot.click("#session-cancel"))
                app.query_one("#orders-button").scroll_visible(animate=False, force=True)
                await pilot.pause()
                self.assertTrue(await pilot.click("#orders-button"))
                await pilot.pause()
                self.assertLessEqual(app.screen.query_one("#orders-dialog").region.right, 80)
                self.assertTrue(await pilot.click("#orders-close"))

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
                screen = GroundSelectScreen([selected], venue_id="v")
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
