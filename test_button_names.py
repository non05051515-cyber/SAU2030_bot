import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class API:
    def __init__(self):
        self.calls = []

    def call(self, method, **data):
        self.calls.append((method, data))
        return {'message_id': 1}

    def buttons(self):
        return [button for row in self.calls[-1][1]['reply_markup']['inline_keyboard'] for button in row]


class ButtonNamesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        os.environ['STORE_STATE_PATH'] = str(Path(self.temp.name) / 'state.sqlite3')
        self.bot = importlib.import_module('bot')
        self.s = importlib.import_module('storefront')
        self.path = patch.object(self.s, 'DB_PATH', Path(self.temp.name) / 'state.sqlite3')
        self.path.start()
        self.stock = patch.object(self.s, 'product_stock', return_value=20)
        self.stock.start()
        self.api = API()
        self.admin = self.s.G['ADMIN_ID']
        self.pid = 'pd_02'
        with self.s.db() as db:
            db.execute('INSERT INTO product_visibility VALUES (?,1)', (self.pid,))
            db.execute('INSERT INTO product_availability VALUES (?,1)', (self.pid,))
            db.execute('INSERT INTO category_icons VALUES (?,?)', ('ui_quantity', '12345'))

    def tearDown(self):
        self.stock.stop()
        self.path.stop()
        self.temp.cleanup()

    def test_individual_quantity_labels_and_icons(self):
        self.bot.action(self.api, self.admin, 'admin')
        self.assertIn('admin:buttonlabels', [b['callback_data'] for b in self.api.buttons()])
        self.bot.action(self.api, self.admin, 'admin:buttonlabels')
        self.assertIn('buttonlabel:ui_quantity_2', [b['callback_data'] for b in self.api.buttons()])
        self.bot.action(self.api, self.admin, 'buttonlabel:ui_quantity_2')
        self.assertTrue(self.bot.handle_receipt(self.api, {'chat': {'id': self.admin}, 'text': '🛒 2'}))
        self.bot.action(self.api, 75, 'item:' + self.pid)
        choices = {b['callback_data']: b for b in self.api.buttons()}
        self.assertEqual(choices['chooseqty:pd_02:2']['text'], '🛒 2')
        self.assertNotIn('icon_custom_emoji_id', choices['chooseqty:pd_02:2'])
        self.assertEqual(choices['chooseqty:pd_02:1']['text'], '🛍 ×1')
        self.assertEqual(choices['chooseqty:pd_02:1']['icon_custom_emoji_id'], '12345')
        self.bot.action(self.api, self.admin, 'resetbuttonlabel:ui_quantity_2')
        self.bot.action(self.api, 75, 'item:' + self.pid)
        choices = {b['callback_data']: b for b in self.api.buttons()}
        self.assertEqual(choices['chooseqty:pd_02:2']['text'], '🛍 ×2')
        self.assertNotIn('icon_custom_emoji_id', choices['chooseqty:pd_02:2'])

    def test_custom_category_rename_reaches_store(self):
        with self.s.db() as db:
            db.execute("INSERT INTO admin_categories VALUES ('cat_test','Old Category','now')")
            db.execute("INSERT INTO admin_products(pid,name,description,price_sar,created_at,category_id,price_usd,stock) VALUES ('custom_test','Old Product','','15','now','cat_test','4',2)")
        self.bot.action(self.api, self.admin, 'buttonnames:categories')
        self.assertIn('txtpick:name:cat_test', [b['callback_data'] for b in self.api.buttons()])
        self.bot.action(self.api, self.admin, 'txtedit:name:ar:cat_test')
        self.assertTrue(self.s.handle_admin_text(self.api, {'chat': {'id': self.admin}, 'text': 'شاهد 🛍'}))
        self.s.admin_text_editor(self.api, self.admin, 'name', 'custom_test', 'ar')
        self.s.handle_admin_text(self.api, {'chat': {'id': self.admin}, 'text': 'اشتراك جديد'})
        self.s.products(self.api, 75)
        self.assertIn('شاهد 🛍', [b['text'] for b in self.api.buttons()])
        self.s.category(self.api, 75, 'cat_test')
        self.assertIn('اشتراك جديد', str(self.api.calls[-1]))
        self.assertIn('شاهد 🛍', str(self.api.calls[-1]))

    def test_customer_cannot_change_labels(self):
        self.bot.action(self.api, 75, 'buttonlabel:ui_quantity_1')
        self.assertFalse(self.s.handle_button_label(self.api, {'chat': {'id': 75}, 'text': 'changed'}))
        self.bot.action(self.api, 75, 'resetbuttonlabel:ui_quantity_1')
        self.assertEqual(self.s.ui_label('ui_quantity_1', 'default'), 'default')


if __name__ == '__main__':
    unittest.main()
