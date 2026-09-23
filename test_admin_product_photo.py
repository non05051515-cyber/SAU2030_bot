"""A custom product's photo can be edited from its category."""
import tempfile
import unittest
from pathlib import Path

import bot
import storefront as store


class FakeAPI:
    def __init__(self):
        self.calls = []

    def call(self, method, **data):
        self.calls.append((method, data))
        return True

    def callbacks(self):
        return [button['callback_data']
                for row in self.calls[-1][1]['reply_markup']['inline_keyboard']
                for button in row if 'callback_data' in button]


class ProductPhotoTests(unittest.TestCase):
    def test_edit_custom_product_photo_from_category(self):
        with tempfile.TemporaryDirectory() as directory:
            store.DB_PATH = Path(directory) / 'store.sqlite3'
            api = FakeAPI()
            admin = bot.ADMIN_ID
            with store.db() as db:
                db.execute('INSERT INTO admin_categories VALUES (?,?,?)',
                           ('cat_test', 'قسم تجريبي', 'today'))
                db.execute('INSERT INTO admin_products(pid,name,description,price_sar,available,created_at,category_id,price_usd,stock) VALUES (?,?,?,?,?,?,?,?,?)',
                           ('custom_test', 'منتج تجربة', 'وصف', '10', 1, 'today', 'cat_test', '2.67', 2))

            bot.action(api, admin, 'mycategory:cat_test')
            self.assertIn('photopick:custom_test', api.callbacks())
            bot.action(api, admin, 'myproduct:custom_test')
            self.assertIn('photopick:custom_test', api.callbacks())

            bot.action(api, admin, 'photopick:custom_test')
            self.assertIn('myproduct:custom_test', api.callbacks())
            self.assertTrue(bot.handle_receipt(api, {
                'chat': {'id': admin}, 'photo': [{'file_id': 'photo-id'}], 'message_id': 1,
            }))
            self.assertEqual(store.saved_product_photo('custom_test'), 'photo-id')
            self.assertIn('myproduct:custom_test', api.callbacks())

            bot.action(api, admin, 'item:custom_test')
            self.assertTrue(any(method == 'sendPhoto' and data.get('photo') == 'photo-id'
                                for method, data in api.calls))


if __name__ == '__main__':
    unittest.main()
