"""Product cards render formatting and custom emoji without exposing user text as HTML."""
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


class ProductInfoHTMLTests(unittest.TestCase):
    def test_custom_emoji_and_bold_fields_render_in_product_card(self):
        with tempfile.TemporaryDirectory() as directory:
            store.DB_PATH = Path(directory) / 'store.sqlite3'
            with store.db() as db:
                db.execute('INSERT INTO admin_categories VALUES (?,?,?)',
                           ('cat_test', 'قسم', 'today'))
                db.execute('INSERT INTO admin_products(pid,name,description,price_sar,available,created_at,category_id,price_usd,stock) VALUES (?,?,?,?,?,?,?,?,?)',
                           ('custom_test', 'Shahid <vip> & X', 'وصف <تجربة> & المزيد', '24.98', 1, 'today', 'cat_test', '6.66', 15))
                db.execute('INSERT INTO product_info_icons(pid,field,custom_emoji_id,fallback_emoji) VALUES (?,?,?,?)',
                           ('custom_test', 'price', '123456789', '💵'))

            api = FakeAPI()
            bot.action(api, 12345, 'item:custom_test')
            messages = [data for method, data in api.calls if method == 'sendMessage']
            self.assertEqual(len(messages), 1)
            message = messages[0]
            self.assertEqual(message['parse_mode'], 'HTML')
            self.assertIn('Shahid &lt;vip&gt; &amp; X', message['text'])
            self.assertIn('وصف &lt;تجربة&gt; &amp; المزيد', message['text'])
            self.assertIn('<tg-emoji emoji-id="123456789">💵</tg-emoji> <b>السعر:</b> 24.98 ر.س', message['text'])
            self.assertIn('📦 <b>الكمية:</b> 15', message['text'])
            self.assertNotIn('&lt;b&gt;', message['text'])


if __name__ == '__main__':
    unittest.main()
