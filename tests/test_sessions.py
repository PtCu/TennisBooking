import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from TokenGetter import TokenGetter
from app import Grounder


class SessionTests(unittest.TestCase):
    def test_har_ignores_other_domains(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.har"
            entries = [{"request": {"url": url, "headers": [{"name": "token", "value": token}]}}
                       for url, token in [("https://jsapp.jussyun.com/jiushi-core/venue/list", "jiushi-token"),
                                          ("https://example.com/account", "other-token")]]
            path.write_text(json.dumps({"log": {"entries": entries}}), encoding="utf-8")
            self.assertEqual(TokenGetter.token_from_file(path), "jiushi-token")
            path.write_text(json.dumps({"log": {"entries": entries[1:]}}), encoding="utf-8")
            with self.assertRaises(ValueError):
                TokenGetter.token_from_file(path)

    def test_config_clear_and_legacy_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            getter = TokenGetter(Path(directory) / "new.json")
            getter.legacy_path = Path(directory) / "legacy.json"
            getter.legacy_path.write_text('{"token":"legacy","other":1}', encoding="utf-8")
            self.assertEqual(getter.read_token(), "legacy")
            getter.save_token("new")
            self.assertEqual(getter.read_token(), "new")
            getter.clear_token()
            self.assertIsNone(getter.read_token())
            self.assertEqual(json.loads(getter.config_path.read_text())["other"], 1)


class LoginInteractionTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_import_preserves_saved_session(self):
        with tempfile.TemporaryDirectory() as directory:
            getter = TokenGetter(Path(directory) / "config.json")
            getter.legacy_path = Path(directory) / "absent.json"
            getter.save_token("old-token")
            with patch("JSApi.TokenGetter", return_value=getter):
                with patch("JSApi.JSApi.get_user_info", side_effect=ValueError("invalid")):
                    app = Grounder()
                    async with app.run_test(size=(120, 45)) as pilot:
                        await pilot.pause()
                        panel = app.query_one("UserInfoPanel")
                        await panel.verify_session("bad-token", save=True)
                        self.assertEqual(getter.read_token(), "old-token")
                        self.assertFalse(panel.logged_status)
                        await pilot.click("#user-login-button")
                        await pilot.pause()
                        self.assertEqual(type(app.screen).__name__, "SessionScreen")
                        await pilot.click("#session-cancel")
