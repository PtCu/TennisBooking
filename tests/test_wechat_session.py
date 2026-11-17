import asyncio
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from wechat_session import APP_ID, HOST, find_candidates
from TokenGetter import TokenGetter
from SessionScreen import SessionScreen
from app import Grounder
from JSApi import JSApi, ApiResponseNotExpectedError
from unittest.mock import AsyncMock, Mock


class CandidateTests(unittest.TestCase):
    def test_requires_jiushi_app_and_host_in_same_renderer(self):
        token = b"A" * 171
        self.assertEqual(find_candidates([APP_ID + b"\0" + token]), [])
        self.assertEqual(find_candidates([HOST + b"\0" + token]), [])
        self.assertEqual(find_candidates([APP_ID, HOST, b"\0" + token + b"\0"]), [token.decode()])
        self.assertEqual(find_candidates([APP_ID, HOST, b"A" * 500]), [])


class ConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_inventory_and_server_error_are_distinct(self):
        api = JSApi()
        api.js_post = AsyncMock(return_value=Mock(json=lambda: {"rtnCode": "10000", "data": {}}))
        self.assertEqual(await api.get_ground("venue", 0), [])
        api.js_post = AsyncMock(return_value=Mock(json=lambda: {"rtnCode": "failure", "data": {}}))
        with self.assertRaises(ApiResponseNotExpectedError):
            await api.get_ground("venue", 0)

    async def test_connection_returns_only_officially_validated_token(self):
        with tempfile.TemporaryDirectory() as directory:
            getter = TokenGetter(Path(directory) / "config.json")
            getter.legacy_path = Path(directory) / "missing.json"
            with patch("JSApi.TokenGetter", return_value=getter), patch("SessionScreen.read_wechat_candidates", return_value=["candidate"]), patch("SessionScreen.JSApi.get_user_info", return_value={"uid": "test"}):
                app = Grounder()
                received = []
                async with app.run_test(size=(80, 25)) as pilot:
                    app.push_screen(SessionScreen(), received.append)
                    await pilot.pause()
                    await pilot.click("#session-connect")
                    await pilot.pause()
                    self.assertEqual(received, ["candidate"])

    async def test_cancel_stops_reader_and_does_not_log_in(self):
        started = threading.Event()
        stopped = threading.Event()
        def reader(cancelled):
            started.set()
            cancelled.wait(3)
            stopped.set()
            return []
        with tempfile.TemporaryDirectory() as directory:
            getter = TokenGetter(Path(directory) / "config.json")
            getter.legacy_path = Path(directory) / "missing.json"
            with patch("JSApi.TokenGetter", return_value=getter), patch("SessionScreen.read_wechat_candidates", side_effect=reader):
                app = Grounder()
                received = []
                async with app.run_test(size=(80, 25)) as pilot:
                    app.push_screen(SessionScreen(), received.append)
                    await pilot.pause()
                    await pilot.click("#session-connect")
                    await pilot.pause()
                    self.assertTrue(started.is_set())
                    await pilot.click("#session-cancel")
                    await asyncio.to_thread(stopped.wait, 2)
                    self.assertTrue(stopped.is_set())
                    self.assertEqual(received, [None])
