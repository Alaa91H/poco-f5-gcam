import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import send_telegram_deliverables as telegram


class TelegramDeliverablesTests(unittest.TestCase):
    def _entry(self, root: Path) -> dict:
        apk = root / "camera.apk"
        apk.write_bytes(b"apk-bytes")
        return {
            "id": "pixel-a13",
            "apk": str(apk),
            "size_bytes": apk.stat().st_size,
            "sha256": hashlib.sha256(b"apk-bytes").hexdigest(),
            "delivery_caption": "camera | android 13+ | test",
            "label": "Pixel Camera 11.1 - Android 13+",
        }

    def test_validate_entry_accepts_matching_file(self):
        with tempfile.TemporaryDirectory() as temp:
            entry = self._entry(Path(temp))
            result = telegram.validate_entry(entry, 2000 * 1024 * 1024)
        self.assertEqual(result.name, "camera.apk")

    def test_validate_entry_rejects_changed_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            entry = self._entry(Path(temp))
            Path(entry["apk"]).write_bytes(b"changed")
            with self.assertRaisesRegex(telegram.DeliveryError, "size changed"):
                telegram.validate_entry(entry, 2000 * 1024 * 1024)

    def test_validate_entry_rejects_oversized_file(self):
        with tempfile.TemporaryDirectory() as temp:
            entry = self._entry(Path(temp))
            with self.assertRaisesRegex(telegram.DeliveryError, "exceeds"):
                telegram.validate_entry(entry, 1)

    def test_send_document_verifies_telegram_metadata(self):
        response = {
            "ok": True,
            "result": {
                "document": {
                    "file_name": "camera.apk",
                    "file_size": len(b"apk-bytes"),
                }
            },
        }
        completed = mock.Mock(
            returncode=0,
            stdout=json.dumps(response),
            stderr="",
        )
        with tempfile.TemporaryDirectory() as temp:
            apk = Path(temp) / "camera.apk"
            apk.write_bytes(b"apk-bytes")
            with mock.patch.object(
                telegram.subprocess,
                "run",
                return_value=completed,
            ) as run_mock:
                document = telegram.send_document(
                    api_url="http://127.0.0.1:8081",
                    bot_token="secret-token",
                    chat_id="123",
                    apk=apk,
                    caption="camera",
                    timeout=60,
                )
        self.assertEqual(document["file_name"], "camera.apk")
        command = run_mock.call_args.args[0]
        self.assertIn("sendDocument", command[command.index("--url") + 1])
        self.assertIn("secret-token", command[command.index("--url") + 1])
        self.assertIn("--form-string", command)

    def test_send_document_rejects_telegram_error(self):
        completed = mock.Mock(
            returncode=0,
            stdout=json.dumps({"ok": False, "description": "bad"}),
            stderr="",
        )
        with tempfile.TemporaryDirectory() as temp:
            apk = Path(temp) / "camera.apk"
            apk.write_bytes(b"apk-bytes")
            with mock.patch.object(
                telegram.subprocess,
                "run",
                return_value=completed,
            ), self.assertRaisesRegex(telegram.DeliveryError, "rejected"):
                telegram.send_document(
                    api_url="http://127.0.0.1:8081",
                    bot_token="secret-token",
                    chat_id="123",
                    apk=apk,
                    caption="camera",
                    timeout=60,
                )


if __name__ == "__main__":
    unittest.main()
