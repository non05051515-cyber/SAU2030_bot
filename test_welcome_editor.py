import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_button_names import API


class WelcomeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.bot = importlib.import_module('bot')
        self.s = importlib.import_module('storefront')
        self.w = importlib.import_module('welcome_editor')
        patcher = patch.object(self.s, 'DB_PATH', Path(tmp.name) / 'state.sqlite3')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.admin = self.bot.ADMIN_ID
        self.api = API()

    def edit(self, action, **message):
        self.bot.action(self.api, self.admin, action)
        self.assertTrue(self.bot.handle_receipt(self.api, {'chat': {'id': self.admin}, **message}))

    def test_default_and_custom_text_through_start_routes(self):
        self.bot.show_start(self.api, 75)
        self.assertIn('مرحباً بك', self.api.calls[-1][1]['text'])
        self.edit('welcome:text:ar', text='⭐ أهلاً\n<متجر>', entities=[{'type':'custom_emoji','offset':0,'length':1,'custom_emoji_id':'12345'}])
        self.bot.action(self.api, 75, 'start')
        text = self.api.calls[-1][1]['text']
        self.assertIn('<tg-emoji emoji-id="12345">⭐</tg-emoji>', text)
        self.assertIn('\n&lt;متجر&gt;', text)
        self.assertIn('enter_store', [b['callback_data'] for b in self.api.buttons()])
        self.bot.action(self.api, 75, 'enter_store')
        self.assertIn('products', [b['callback_data'] for b in self.api.buttons()])
        with self.s.db() as db:
            db.execute("UPDATE preferences SET lang='en' WHERE cid=75")
        self.bot.show_start(self.api, 75)
        self.assertIn('Welcome to VEXA STORE', self.api.calls[-1][1]['text'])

    def test_photo_caption_delete_and_long_text(self):
        self.edit('welcome:photo', photo=[{'file_id':'small'},{'file_id':'large'}])
        self.bot.show_start(self.api, 75)
        method, data = self.api.calls[-1]
        self.assertEqual(method, 'sendPhoto')
        self.assertEqual(data['photo'], 'large')
        self.assertIn('reply_markup', data)
        self.edit('welcome:text:ar', text='أ' * 1100)
        self.api.calls.clear()
        self.bot.show_start(self.api, 75)
        self.assertEqual([x[0] for x in self.api.calls], ['sendPhoto', 'sendMessage'])
        self.bot.action(self.api, self.admin, 'welcome:remove')
        self.bot.show_start(self.api, 75)
        self.assertEqual(self.api.calls[-1][0], 'sendMessage')
        self.assertEqual(len(self.api.calls[-1][1]['text']), 1100)

    def test_permissions_cancel_and_validation(self):
        self.bot.action(self.api, 75, 'welcome:text:ar')
        self.assertFalse(self.w.receive(self.api, {'chat':{'id':75}, 'text':'No'}))
        self.bot.action(self.api, self.admin, 'welcome:text:ar')
        self.assertTrue(self.w.receive(self.api, {'chat':{'id':self.admin}, 'text':'x'*1501}))
        self.assertNotIn('ar', self.w.settings())
        self.bot.action(self.api, self.admin, 'admin')
        self.assertFalse(self.w.receive(self.api, {'chat':{'id':self.admin}, 'text':'Cancelled'}))
        self.assertIn('admin:welcome', [b['callback_data'] for b in self.api.buttons()])
        self.bot.action(self.api, self.admin, 'admin:icons')
        self.assertIn('seticon:ui_welcome', [b['callback_data'] for b in self.api.buttons()])

    def test_failed_photo_falls_back_to_text(self):
        self.w.save('photo', 'unavailable')
        class BrokenPhoto(API):
            def call(self, method, **data):
                if method == 'sendPhoto':
                    raise RuntimeError('unavailable')
                return super().call(method, **data)
        api = BrokenPhoto()
        self.bot.show_start(api, 75)
        self.assertIn('مرحباً بك', api.calls[-1][1]['text'])


if __name__ == '__main__':
    unittest.main()
