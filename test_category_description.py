import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_button_names import API


class CategoryDescriptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bot = importlib.import_module('bot')
        self.s = importlib.import_module('storefront')
        self.patch = patch.object(self.s, 'DB_PATH', Path(self.temp.name) / 'state.sqlite3')
        self.patch.start()
        self.api = API()
        self.admin = self.s.G['ADMIN_ID']
        with self.s.db() as db:
            db.execute("INSERT INTO admin_categories VALUES ('cat_test','Test','now')")
            db.execute("INSERT INTO admin_products(pid,name,description,price_sar,created_at,category_id,price_usd,stock) VALUES ('custom_test','Product','Keep me','15','now','cat_test','4',2)")

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def save(self, pid, lang='ar'):
        self.bot.action(self.api, self.admin, 'catdesclang:' + lang + ':' + pid)
        message = {'chat': {'id': self.admin}, 'text': '⭐ Welcome\n<hello>', 'entities': [{'type':'custom_emoji','offset':0,'length':1,'custom_emoji_id':'12345'}]}
        self.assertTrue(self.bot.handle_receipt(self.api, message))

    def test_admin_menu_save_and_custom_category_render(self):
        self.bot.action(self.api, self.admin, 'admin')
        self.assertIn('admin:categorydesc', [b['callback_data'] for b in self.api.buttons()])
        self.bot.action(self.api, self.admin, 'admin:categorydesc')
        self.assertIn('catdesc:cat_test', [b['callback_data'] for b in self.api.buttons()])
        self.save('cat_test')
        self.bot.action(self.api, 75, 'product:cat_test')
        text = self.api.calls[-1][1]['text']
        self.assertIn('<tg-emoji emoji-id="12345">⭐</tg-emoji>', text)
        self.assertIn('\n&lt;hello&gt;', text)
        self.assertEqual(self.s.product_description('custom_test', 75), 'Keep me')
        with self.s.db() as db:
            self.assertIsNone(db.execute('SELECT 1 FROM admin_state WHERE cid=?', (self.admin,)).fetchone())

    def test_builtin_extended_and_iptv_render(self):
        for pid in ('chatgpt', 'youtube', 'iptv'):
            self.save(pid)
            self.api.calls.clear()
            with patch.object(self.s, 'grok_cards', return_value=None):
                self.bot.action(self.api, 75, 'product:' + pid)
            self.assertTrue(any('Welcome' in data.get('text', '') for _, data in self.api.calls), pid)
        with self.s.db() as db:
            db.execute("UPDATE admin_products SET category_id='youtube' WHERE pid='custom_test'")
        self.bot.action(self.api, 75, 'product:youtube')
        self.assertIn('Welcome', self.api.calls[-1][1]['text'])

    def test_cancel_authorization_and_languages(self):
        self.bot.action(self.api, 75, 'catdesclang:ar:cat_test')
        self.assertFalse(self.s.handle_category_description(self.api, {'chat':{'id':75},'text':'No'}))
        self.save('cat_test', 'en')
        self.assertIsNone(self.s.category_description_html('cat_test', 75))
        self.bot.action(self.api, self.admin, 'catdesclang:ar:cat_test')
        self.bot.action(self.api, self.admin, 'admin')
        self.assertFalse(self.s.handle_category_description(self.api, {'chat':{'id':self.admin},'text':'Cancelled'}))
        self.bot.action(self.api, self.admin, 'admin:icons')
        self.assertIn('seticon:ui_category_description', [b['callback_data'] for b in self.api.buttons()])


if __name__ == '__main__':
    unittest.main()
