import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class API:
    def __init__(self):
        self.calls = []
        self.ok = True

    def call(self, method, **data):
        self.calls.append((method, data))
        return {'message_id': 42} if self.ok else None


class CustomerActivityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        import storefront as s
        self.s = s
        self.db_patch = patch.object(s, 'DB_PATH', Path(self.temp.name) / 'state.sqlite3')
        self.db_patch.start()
        self.g_patch = patch.object(s, 'G', {'ADMIN_ID': 99, 'PRODUCTS': {}, 'MENU': {}})
        self.g_patch.start()
        s.ACTIVITY_VIEW.clear()
        self.api = API()

    def tearDown(self):
        self.s.ACTIVITY_VIEW.clear()
        self.g_patch.stop()
        self.db_patch.stop()
        self.temp.cleanup()

    def test_empty_page_updates_on_first_event_and_only_on_change(self):
        s = self.s
        s.admin_activity(self.api, 99)
        self.assertIn('admin:activity', str(self.api.calls[-1]))
        s.track_customer_activity(1, value='start')
        s.tick_customer_activity(self.api)
        method, data = self.api.calls[-1]
        self.assertEqual(method, 'editMessageText')
        self.assertEqual(data['message_id'], 42)
        self.assertIn('بدء البوت', data['text'])
        count = len(self.api.calls)
        s.tick_customer_activity(self.api)
        self.assertEqual(len(self.api.calls), count)

    def test_all_product_callbacks_and_private_messages(self):
        s = self.s
        for value in ('product:iptv', 'item:iptv_test', 'claude:claude_pro', 'buy:pd_02'):
            s.track_customer_activity(1, value=value)
        s.track_customer_activity(1, message={'text': 'private password'})
        with s.db() as conn:
            rows = conn.execute('SELECT action,pid FROM activity ORDER BY id').fetchall()
        self.assertEqual(rows[:2], [('category', 'iptv'), ('item', 'iptv_test')])
        self.assertEqual(rows[-1], ('message', ''))
        self.assertNotIn('private password', str(rows))

    def test_latest_first_and_length_budget(self):
        s = self.s
        for i in range(30):
            s.log_activity(i, 'item', 'x' * 200)
        with patch.object(s, 'name', return_value='😀&<>' * 100):
            text, latest = s.activity_page(99)
        self.assertEqual(latest, 30)
        self.assertIn('id=29', text)
        self.assertLess(len(text.encode('utf-16-le')) // 2, 4096)
        self.assertNotIn('id=0"', text)

    def test_admin_navigation_expiry_and_failed_edit_stop_refresh(self):
        s = self.s
        s.admin_activity(self.api, 99)
        s.track_customer_activity(99, value='admin')
        self.assertFalse(s.ACTIVITY_VIEW)
        with s.db() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM activity').fetchone()[0], 0)
        s.admin_activity(self.api, 99)
        s.ACTIVITY_VIEW['expires'] = 0
        s.tick_customer_activity(self.api)
        self.assertFalse(s.ACTIVITY_VIEW)
        s.admin_activity(self.api, 99)
        s.log_activity(1, 'home', '')
        self.api.ok = False
        s.tick_customer_activity(self.api)
        self.assertFalse(s.ACTIVITY_VIEW)


if __name__ == '__main__':
    unittest.main()
