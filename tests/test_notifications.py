import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from phone_notify import PhoneSettings, PhoneNotifier, NotificationError


class Response:
    status = 200

    def __init__(self, result):
        self.result = result

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        return json.dumps(self.result).encode()


class NotificationTests(unittest.TestCase):
    def test_settings_are_encrypted_and_roundtrip(self):
        settings = PhoneSettings("SCTtestcredential123456")
        with tempfile.TemporaryDirectory() as directory:
            settings.save(directory)
            self.assertNotIn(settings.key, (Path(directory) / "phone-settings.json").read_text())
            self.assertEqual(PhoneSettings.load(directory).key, settings.key)

    def test_accepts_service_success_and_rejects_errors(self):
        settings = PhoneSettings("SCTtestcredential123456")
        for result in [{"code": 0}, {"code": 1}, {"code": 0, "data": {"errno": 2}}]:
            opener = SimpleNamespace(open=lambda *args, **kwargs: Response(result))
            notifier = PhoneNotifier(settings, opener=opener)
            if result == {"code": 0}:
                notifier.send("test", "test")
            else:
                with self.assertRaises(NotificationError):
                    notifier.send("test", "test")
