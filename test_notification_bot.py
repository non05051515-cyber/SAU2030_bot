import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import notification_bot as n


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [
            patch.object(n, "DATA", self.root),
            patch.object(n, "DATABASE", self.root / "notifications.sqlite3"),
            patch.object(n, "STORE_URL", "https://store.example"),
        ]
        for item in self.patches:
            item.start()
        (self.root / "users.json").write_text(json.dumps([11, 12]))
        (self.root / "languages.json").write_text(json.dumps({"11": "en"}))
        with sqlite3.connect(self.root / "storefront.sqlite3") as db:
            db.execute("CREATE TABLE advertising_preferences(cid INTEGER,enabled INTEGER)")
            db.execute("INSERT INTO advertising_preferences VALUES(12,0)")
        n.setup()
        self.event = {
            "id": "stock-event-1", "product_id": "gpt", "name": ["بلس", "Plus"],
            "added": 3, "stock": 5, "price_cents": 395,
        }

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_legacy_opt_out_is_respected_and_event_is_idempotent(self):
        self.assertEqual(n.preference(11), ("en", True))
        self.assertEqual(n.preference(12), ("ar", False))
        n.queue_event(self.event)
        n.queue_event(self.event)
        with n.db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM events").fetchone()[0], 1)
            self.assertEqual([row[0] for row in db.execute("SELECT cid FROM deliveries")], [11])
        with patch.object(n, "telegram") as send:
            self.assertTrue(n.deliver_one())
            send.assert_called_once()
            self.assertIn("$3.95", send.call_args.kwargs["text"])
            self.assertEqual(send.call_args.kwargs["reply_markup"]["inline_keyboard"][0][0]["web_app"]["url"],
                             "https://store.example/?product=gpt")
            self.assertFalse(n.deliver_one())

    def test_stop_after_queue_suppresses_pending_delivery(self):
        n.queue_event(self.event)
        n.subscribe(11, False)
        with patch.object(n, "telegram") as send:
            n.deliver_one()
            send.assert_not_called()

    def test_failed_delivery_retries_and_conflicting_id_rejected(self):
        n.queue_event(self.event)
        with patch.object(n, "telegram", side_effect=n.TelegramError(429, retry_after=30)):
            n.deliver_one()
        with n.db() as db:
            self.assertEqual(db.execute("SELECT status FROM deliveries").fetchone()[0], "pending")
        with self.assertRaises(ValueError):
            n.queue_event({**self.event, "stock": 10})

    def test_html_escaped_and_invalid_stock_rejected(self):
        card = n.notification({**self.event, "name": ["<test>", "<test>"]}, "ar")
        self.assertIn("&lt;test&gt;", card["text"])
        with self.assertRaises(ValueError):
            n.validate_event({**self.event, "stock": 1})

    def test_old_checkout_callback_redirects_to_store(self):
        with patch.object(n, "telegram") as send:
            n.process_update({"callback_query": {"id": "callback", "from": {"id": 11}, "data": "buy:gpt"}})
        self.assertEqual(send.call_args.kwargs["reply_markup"]["inline_keyboard"][0][0]["web_app"]["url"],
                         "https://store.example/")


if __name__ == "__main__":
    unittest.main()
