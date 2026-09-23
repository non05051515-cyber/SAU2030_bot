"""Price edits use the same value in administration and checkout."""
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

import bot
import chatgpt_extension
import storefront as store


class FakeAPI:
    def __init__(self):
        self.calls = []

    def call(self, method, **data):
        self.calls.append((method, data))
        return True

    def buttons(self):
        return [button for row in self.calls[-1][1]['reply_markup']['inline_keyboard']
                for button in row]


class PriceEditTests(unittest.TestCase):
    def test_custom_product_price_updates_every_view(self):
        with tempfile.TemporaryDirectory() as directory:
            store.DB_PATH = Path(directory) / 'store.sqlite3'
            chatgpt_extension.DB_PATH = Path(directory) / 'chat.sqlite3'
            api, admin = FakeAPI(), bot.ADMIN_ID
            with store.db() as db:
                db.execute('INSERT INTO admin_categories VALUES (?,?,?)',
                           ('cat_test', 'قسم تجريبي', 'today'))
                db.execute('INSERT INTO admin_products(pid,name,description,price_sar,available,created_at,category_id,price_usd,stock) VALUES (?,?,?,?,?,?,?,?,?)',
                           ('custom_test', 'منتج', 'وصف', '37.50', 1, 'today', 'cat_test', '10.00', 5))

            bot.action(api, admin, 'admin:prices')
            self.assertIn('pricecat:cat_test', [b['callback_data'] for b in api.buttons()])
            bot.action(api, admin, 'myproduct:custom_test')
            self.assertIn('pricepick:custom_test', [b['callback_data'] for b in api.buttons()])
            bot.action(api, admin, 'pricepick:custom_test')
            bot.action(api, admin, 'priceedit:SAR:custom_test')
            with chatgpt_extension.db() as db:
                db.execute('INSERT INTO sessions(cid,active) VALUES (?,1)', (admin,))
            self.assertTrue(bot.handle_receipt(api, {
                'chat': {'id': admin}, 'text': '75', 'message_id': 1,
            }))

            self.assertEqual(store.amount('custom_test', 'SAR'), Decimal('75.00'))
            self.assertEqual(store.amount('custom_test', 'USD'), Decimal('20.00'))
            bot.action(api, admin, 'mycategory:cat_test')
            self.assertTrue(any('20.00 USD' in b['text'] for b in api.buttons()))
            bot.action(api, admin, 'myproduct:custom_test')
            self.assertIn('20.00 USD', api.calls[-1][1]['text'])
            bot.action(api, 12345, 'product:cat_test')
            self.assertTrue(any('75.00' in b['text'] for b in api.buttons()))


if __name__ == '__main__':
    unittest.main()
