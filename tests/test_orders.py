import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from orders import verify_detail, OrderReviewError, review_payment, reconcile
from storage import write_json


def fixture():
    start = "1900000000000"
    end = "1900003600000"
    slot = {"groundId": "1", "groundName": "1号", "startTime": start, "endTime": end}
    record = {"order_id": "o1", "target": {"venue_id": "v", "slots": [slot], "amount": "100", "budget": "150"}}
    detail = {"orderDetailModel": {"orderId": "o1", "venueId": "v", "payAmount": 10000, "payStatus": "PAY_WAIT",
                                   "orderPayMaxTime": "1900000900000", "currentTime": "1900000000000"},
              "venueBookGroundModelList": [slot.copy()]}
    return record, detail


class DetailTests(unittest.TestCase):
    def test_checks_official_amount_times_and_venue(self):
        record, detail = fixture()
        status, amount, remaining, deadline = verify_detail(detail, record)
        self.assertEqual((status, str(amount), remaining), ("PAY_WAIT", "100", 900))
        for key, value in [("venueId", "different"), ("orderId", "different"), ("payAmount", 20000)]:
            record, detail = fixture()
            detail["orderDetailModel"][key] = value
            with self.assertRaises(OrderReviewError):
                verify_detail(detail, record)

    def test_rejects_different_court_and_time(self):
        for key, value in [("groundId", "2"), ("startTime", "1900003600000")]:
            record, detail = fixture()
            detail["venueBookGroundModelList"][0][key] = value
            with self.assertRaises(OrderReviewError):
                verify_detail(detail, record)


class FollowupTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_notification_sent_once_and_payment_ends_tracking(self):
        record, detail = fixture()
        api = type("Api", (), {})()
        api.get_order_data = AsyncMock(return_value=detail)
        class Notifier:
            calls = 0
            def send(self, title, body):
                self.calls += 1
        notifier = Notifier()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"order.json"
            write_json(path, record)
            self.assertTrue((await review_payment(api, path, notifier=notifier))[0])
            await review_payment(api, path, notifier=notifier)
            self.assertEqual(notifier.calls, 1)
            detail["orderDetailModel"]["payStatus"] = "PAY_SUCCESS"
            self.assertFalse((await review_payment(api, path, notifier=notifier))[0])
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["state"], "payment-success")

    async def test_mismatch_never_sends_notification(self):
        record, detail = fixture()
        detail["orderDetailModel"]["payAmount"] = 20000
        api = type("Api", (), {})()
        api.get_order_data = AsyncMock(return_value=detail)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"order.json"
            write_json(path, record)
            with self.assertRaises(OrderReviewError):
                await review_payment(api, path)

    async def test_unresolved_submission_keeps_record(self):
        record, _ = fixture()
        record.pop("order_id")
        record["baseline_orders"] = []
        api = type("Api", (), {})()
        api.get_order_data = AsyncMock(return_value={"totalNum": 0, "orderDetailModelList": []})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"order.json"
            write_json(path, record)
            with self.assertRaises(OrderReviewError):
                await reconcile(api, path, attempts=1)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["state"], "requires-official-check")
