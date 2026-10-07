import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class API:
    def __init__(self):
        self.calls = []

    def call(self, method, **data):
        self.calls.append((method, data))
        return [{'emoji': '✨'}] if method == 'getCustomEmojiStickers' else {'message_id': 1}


class CategoryPricesTests(unittest.TestCase):
    def setUp(self):
        self.bot = importlib.import_module('bot')
        self.s = importlib.import_module('storefront')
        self.temp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(self.s, 'DB_PATH', Path(self.temp.name) / 'state.db')
        self.db_patch.start()
        self.api = API()
        self.admin = self.s.G['ADMIN_ID']
        with self.s.db() as db:
            db.execute("INSERT INTO admin_categories VALUES ('test_cat','Test','now')")
            for pid in ('visible', 'hidden'):
                db.execute("INSERT INTO admin_products(pid,name,description,price_sar,price_usd,category_id,stock,created_at) VALUES (?,?,?,'15','4','test_cat',2,'now')", (pid, pid, 'description'))
            db.execute("INSERT INTO product_visibility VALUES ('hidden',0)")

    def tearDown(self):
        self.db_patch.stop()
        self.temp.cleanup()

    def display(self):
        self.bot.action(self.api, 75, 'product:test_cat')
        return self.api.calls[-1][1]

    def test_live_prices_visibility_and_description(self):
        with self.s.db() as db:
            db.execute("INSERT INTO product_text VALUES ('test_cat','category_description','ar','<b>Keep description</b>')")
        result = self.display()
        self.assertIn('<b>Keep description</b>', result['text'])
        self.assertIn('4.00 USD (15.00 ريال سعودي)', result['text'])
        self.assertNotIn('hidden', result['text'])
        self.assertNotIn('ريال', result['reply_markup']['inline_keyboard'][0][0]['text'])
        with self.s.db() as db:
            db.execute("INSERT INTO product_prices VALUES ('visible','7.50','USD')")
        self.assertIn('7.50 USD (28.13 ريال سعودي)', self.display()['text'])
        with self.s.db() as db:
            db.execute("UPDATE product_prices SET value='14.99',currency='SAR' WHERE pid='visible'")
        self.assertIn('4.00 USD (14.99 ريال سعودي)', self.display()['text'])

    def test_animated_icon_admin_flow_and_cancel(self):
        self.bot.action(self.api, self.admin, 'catdesc:test_cat')
        buttons = self.api.calls[-1][1]['reply_markup']['inline_keyboard']
        self.assertTrue(any(b['callback_data'] == 'infoicon:category_prices:test_cat' for row in buttons for b in row))
        self.bot.action(self.api, self.admin, 'infoicon:category_prices:test_cat')
        self.assertTrue(self.s.handle_info_icon(self.api, {'chat': {'id': self.admin}, 'text': '✨'}))
        self.assertNotIn('tg-emoji', self.display()['text'])
        message = {'chat': {'id': self.admin}, 'text': '✨', 'entities': [{'type': 'custom_emoji', 'offset': 0, 'length': 1, 'custom_emoji_id': '12345'}]}
        self.assertTrue(self.bot.handle_receipt(self.api, message))
        self.assertIn('4.00 USD (<tg-emoji emoji-id="12345">✨</tg-emoji> 15.00 ريال سعودي)', self.display()['text'])
        self.bot.action(self.api, self.admin, 'infoicon:category_prices:test_cat')
        self.bot.action(self.api, self.admin, 'catdesc:test_cat')
        self.assertFalse(self.s.handle_info_icon(self.api, message))
        self.bot.action(self.api, 75, 'infoicon:category_prices:test_cat')
        self.assertFalse(self.s.handle_info_icon(self.api, {'chat': {'id': 75}, 'text': '✨'}))


if __name__ == '__main__':
    unittest.main()
