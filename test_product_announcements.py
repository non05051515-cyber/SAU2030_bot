import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class API:
    def __init__(self): self.calls = []
    def call(self, method, **data):
        self.calls.append((method, data))
        return {'message_id': len(self.calls)}


class AnnouncementTests(unittest.TestCase):
    def setUp(self):
        self.bot = importlib.import_module('bot')
        self.ad = importlib.import_module('product_announcements')
        self.s = importlib.import_module('storefront')
        self.temp = tempfile.TemporaryDirectory()
        self.old = self.s.DB_PATH
        self.s.DB_PATH = Path(self.temp.name) / 'test.db'
        self.cid = self.bot.ADMIN_ID
        self.pid = 'custom_ad_test'
        self.api = API()
        with self.s.db() as c:
            c.execute('INSERT INTO admin_products(pid,name,description,price_sar,available,created_at,category_id,price_usd,stock) VALUES (?,?,?,?,?,?,?,?,?)', (self.pid, 'JetBrains 🧑‍💻 <1 Year>', 'test', '15', 1, 'today', 'iptv', '4', 29))

    def tearDown(self):
        self.s.DB_PATH = self.old
        self.temp.cleanup()

    def select(self):
        self.bot.action(self.api, self.cid, 'ad:home')
        self.bot.action(self.api, self.cid, 'ad:pick:' + self.pid)
        self.bot.handle_admin_delivery(self.api, {'chat': {'id': self.cid}, 'text': '20'})

    def test_preview_native_entities_and_deep_link(self):
        with self.ad.db() as c:
            c.execute('INSERT INTO pandora_notification_icons VALUES (?,?)', ('product', '123456'))
            c.execute('INSERT INTO pandora_notification_icons VALUES (?,?)', ('buy', '654321'))
        self.select()
        card = next(d for _, d in self.api.calls if 'entities' in d)
        self.assertIn('Added: 20', card['text'])
        self.assertIn('Current stock: 29', card['text'])
        self.assertNotIn('parse_mode', card)
        for e in card['entities']:
            raw = card['text'].encode('utf-16-le')[e['offset']*2:(e['offset']+e['length'])*2]
            self.assertTrue(raw.decode('utf-16-le'))
        button = card['reply_markup']['inline_keyboard'][0][0]
        self.assertEqual(button['style'], 'success')
        self.assertEqual(button['icon_custom_emoji_id'], '654321')
        payload = button['url'].split('?start=')[1]
        self.assertTrue(self.ad.catalog.remember(42, '/start ' + payload))
        with patch.dict(self.s.G, {'action': lambda a,c,x: self.assertEqual(x, 'item:' + self.pid)}):
            self.assertTrue(self.ad.catalog.resume(self.api, 42))
        self.assertFalse(any(d.get('chat_id') in self.ad.TARGETS.values() for _,d in self.api.calls))
        self.assertEqual(self.ad.catalog.state(self.pid)['quantity'], 29)

    def test_confirmation_once_admin_only_and_unavailable(self):
        self.select()
        token = self.ad.draft(self.cid)[0]
        value = 'ad:send:channel:' + token
        self.bot.action(self.api, 42, value)
        self.bot.action(self.api, self.cid, value)
        self.bot.action(self.api, self.cid, value)
        self.assertEqual(sum(d.get('chat_id') == self.ad.catalog.CHANNEL for _,d in self.api.calls), 1)
        self.select()
        token = self.ad.draft(self.cid)[0]
        with self.s.db() as c: c.execute('UPDATE admin_products SET stock=0 WHERE pid=?', (self.pid,))
        self.bot.action(self.api, self.cid, 'ad:send:group:' + token)
        self.assertFalse(any(d.get('chat_id') == '@SAU2030_k' for _,d in self.api.calls))

    def test_broadcast_flood_wait_and_batch_resume(self):
        import broadcast_admin as broadcast
        self.select()
        payload = self.ad.card(self.ad.draft(self.cid)[1])
        with patch.object(broadcast, '_users', return_value=list(range(1000, 1101))):
            self.bot.queue_product_announcement('batch_test', self.pid, payload)
        api = API()
        original = api.call
        def limited(method, **data):
            api.last_error = {'code': 429, 'retry_after': 90}
            return None
        api.call = limited
        with patch.object(broadcast.time, 'sleep'):
            broadcast.DELIVERY_WORKER(api)
        with self.s.db() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM product_broadcast_recipients WHERE status="pending"').fetchone()[0], 101)
            self.assertGreater(c.execute('SELECT until FROM broadcast_cooldown').fetchone()[0], broadcast.time.time() + 85)
        api.call = original
        api.last_error = None
        broadcast.DELIVERY_WORKER(api)
        self.assertEqual(api.calls, [])
        with self.s.db() as c: c.execute('DELETE FROM broadcast_cooldown')
        with patch.object(broadcast.time, 'sleep'):
            broadcast.DELIVERY_WORKER(api)
            self.assertEqual(len(api.calls), 100)
            broadcast.DELIVERY_WORKER(api)
        self.assertEqual(sum(d.get('chat_id') in range(1000,1101) for _,d in api.calls), 101)

    def test_bot_broadcast_preserves_card_and_sends_once(self):
        import broadcast_admin as broadcast
        self.select()
        self.bot.action(self.api, self.cid, 'ad:stock')
        self.bot.handle_admin_delivery(self.api, {'chat': {'id': self.cid}, 'text': '18'})
        with self.ad.db() as c:
            c.execute('INSERT INTO pandora_notification_icons VALUES (?,?)', ('stock', '777'))
        expected = self.ad.card(self.ad.draft(self.cid)[1])
        token = self.ad.draft(self.cid)[0]
        with patch.object(broadcast, '_users', return_value=[42, 42, 43, self.cid, -100]):
            self.bot.action(self.api, 42, 'ad:send:bot:' + token)
            self.assertIsNotNone(self.ad.draft(self.cid))
            self.bot.action(self.api, self.cid, 'ad:send:bot:' + token)
            self.bot.action(self.api, self.cid, 'ad:send:bot:' + token)
        # Queueing never blocks the callback or sends before the worker runs.
        self.assertFalse(any(d.get('chat_id') in (42, 43) for _,d in self.api.calls))
        with self.s.db() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM product_broadcast_jobs').fetchone()[0], 1)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM product_broadcast_recipients').fetchone()[0], 2)
        broadcast.DELIVERY_WORKER(self.api)
        broadcast.DELIVERY_WORKER(self.api)
        deliveries = [d for _,d in self.api.calls if d.get('chat_id') in (42, 43)]
        self.assertEqual(len(deliveries), 2)
        for delivery in deliveries:
            self.assertEqual({k:v for k,v in delivery.items() if k != 'chat_id'}, expected)
        self.assertEqual(self.ad.catalog.state(self.pid)['quantity'], 29)

    def test_stock_edit_publish_and_restore_automatic(self):
        self.select()
        self.bot.action(self.api, self.cid, 'ad:stock')
        self.bot.handle_admin_delivery(self.api, {'chat': {'id': self.cid}, 'text': '-2'})
        self.assertEqual(self.ad.draft(self.cid)[2], 'stock')
        self.bot.handle_admin_delivery(self.api, {'chat': {'id': self.cid}, 'text': '18'})
        data = self.ad.draft(self.cid)[1]
        self.assertIn('Current stock: 18', self.ad.card(data)['text'])
        self.assertEqual(data['added'], 20)
        self.assertEqual(self.ad.catalog.state(self.pid)['quantity'], 29)
        token = self.ad.draft(self.cid)[0]
        self.bot.action(self.api, self.cid, 'ad:send:channel:' + token)
        posted = [d for _,d in self.api.calls if d.get('chat_id') == self.ad.catalog.CHANNEL]
        self.assertIn('Current stock: 18', posted[-1]['text'])
        self.select()
        self.bot.action(self.api, self.cid, 'ad:stock')
        self.bot.handle_admin_delivery(self.api, {'chat': {'id': self.cid}, 'text': '0'})
        self.assertIn('Current stock: 0', self.ad.card(self.ad.draft(self.cid)[1])['text'])
        self.bot.action(self.api, self.cid, 'ad:stock_auto')
        self.assertIn('Current stock: 29', self.ad.card(self.ad.draft(self.cid)[1])['text'])

    def test_icon_persistence_skip_invalid_and_cancel(self):
        self.select()
        self.bot.action(self.api, self.cid, 'ad:icon:stock')
        self.bot.handle_admin_delivery(self.api, {'chat': {'id': self.cid}, 'text': '📦', 'entities': [{'type': 'custom_emoji', 'custom_emoji_id': '777', 'offset': 0, 'length': 2}]})
        with self.ad.db() as c:
            self.assertEqual(c.execute("SELECT custom_emoji_id FROM pandora_notification_icons WHERE key='stock'").fetchone()[0], '777')
        self.bot.action(self.api, self.cid, 'ad:added')
        self.bot.handle_admin_delivery(self.api, {'chat': {'id': self.cid}, 'text': '-1'})
        self.assertEqual(self.ad.draft(self.cid)[2], 'added')
        self.bot.action(self.api, self.cid, 'ad:skip')
        self.assertNotIn('Added:', self.ad.card(self.ad.draft(self.cid)[1])['text'])
        self.bot.action(self.api, self.cid, 'ad:home')
        self.assertIsNone(self.ad.draft(self.cid))


if __name__ == '__main__': unittest.main()
