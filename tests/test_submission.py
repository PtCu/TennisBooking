import json
import sys
import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from GroundInfoPanel import GroundInfoPanel
from booking_flow import BookingRequest, Plan
from orders import OrderReviewError


class SubmissionTests(unittest.IsolatedAsyncioTestCase):
    def setup_task(self):
        stamp = 1_900_000_000_000
        request = BookingRequest((("1", str(stamp), str(stamp + 3600000)),), (Plan("single_slot", 1),), Decimal("200"))
        rows = [{"startTime": str(stamp), "endTime": str(stamp + 3600000), "blockModel": [
            {"groundId": "1", "groundName": "1号", "sportsType": "网球", "price": "100", "status": "1"}]}]
        api = SimpleNamespace(get_ground=AsyncMock(return_value=rows), post_book=AsyncMock(return_value=True),
                              get_open_dates=AsyncMock(return_value={datetime.fromtimestamp(stamp / 1000).date()}))
        async def order_data(path, params):
            if path.endswith('getOrderList'):
                posted = bool(api.post_book.await_count)
                return {"totalNum": int(posted), "orderDetailModelList": [{"orderId": "new", "venueId": "v"}] if posted else []}
            return {"orderDetailModel": {"orderId": "new", "venueId": "v", "payAmount": 10000, "payStatus": "PAY_SUCCESS"},
                    "venueBookGroundModelList": [{"groundId": "1", "groundName": "1号", "startTime": str(stamp), "endTime": str(stamp+3600000)}]}
        api.get_order_data = AsyncMock(side_effect=order_data)
        panel = SimpleNamespace(js_api=api, _day_timestamp=GroundInfoPanel._day_timestamp)
        venue = dict(venue_id="v", venue_name="场馆", agency_id="a", agency_name="机构", sports_name="网球")
        return request, panel, venue

    async def test_same_request_is_submitted_once_across_runs(self):
        request, panel, venue = self.setup_task()
        with tempfile.TemporaryDirectory() as directory, patch("GroundInfoPanel.DATA_DIR", Path(directory)), patch("orders.optional_notifier", return_value=None):
            self.assertTrue(await GroundInfoPanel._submit_request(panel, request, venue, "c", 3, 2000, 0))
            with self.assertRaises(OrderReviewError):
                await GroundInfoPanel._submit_request(panel, request, venue, "c", 3, 2000, 0)
            self.assertEqual(panel.js_api.post_book.await_count, 1)

    async def test_timeout_keeps_record_and_does_not_resubmit(self):
        request, panel, venue = self.setup_task()
        panel.js_api.post_book.side_effect = TimeoutError()
        with tempfile.TemporaryDirectory() as directory, patch("GroundInfoPanel.DATA_DIR", Path(directory)), patch("orders.optional_notifier", return_value=None):
            self.assertTrue(await GroundInfoPanel._submit_request(panel, request, venue, "c", 3, 2000, 0))
            self.assertEqual(panel.js_api.post_book.await_count, 1)
            record = json.loads(next((Path(directory)/"orders").glob("*.json")).read_text(encoding="utf-8"))
            self.assertEqual(record["state"], "payment-success")

    async def test_unopened_date_never_submits_preview_inventory(self):
        request, panel, venue = self.setup_task()
        panel.js_api.get_open_dates.return_value = set()
        with tempfile.TemporaryDirectory() as directory, patch("GroundInfoPanel.DATA_DIR", Path(directory)):
            self.assertFalse(await GroundInfoPanel._submit_request(panel, request, venue, "c", 1, 2000, 0))
            panel.js_api.get_ground.assert_not_awaited()
            panel.js_api.post_book.assert_not_awaited()
