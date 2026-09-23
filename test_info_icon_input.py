"""Both standard and Telegram custom emoji work as product field icons."""
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
        if method == 'getCustomEmojiStickers':
            return [{'emoji': '✨'}]
        return True


class IconInputTests(unittest.TestCase):
    def test_standard_plus_and_custom_emoji(self):
        with tempfile.TemporaryDirectory() as directory:
            store.DB_PATH = Path(directory) / 'store.sqlite3'
            api, admin = FakeAPI(), bot.ADMIN_ID

            bot.action(api, admin, 'infoicon:price:pd_04')
            self.assertTrue(bot.handle_info_icon(api, {'chat': {'id': admin}, 'text': '➕'}))
            self.assertEqual(store.info_icon('pd_04', 'price', '💵'), '➕')
            self.assertIn('أيقونة عادية', api.calls[-1][1]['text'])

            bot.action(api, admin, 'infoicon:stock:pd_04')
            self.assertTrue(bot.handle_info_icon(api, {
                'chat': {'id': admin}, 'text': '✨',
                'entities': [{'type': 'custom_emoji', 'custom_emoji_id': '1234'}],
            }))
            self.assertEqual(store.info_icon('pd_04', 'stock', '📦'),
                             '<tg-emoji emoji-id="1234">✨</tg-emoji>')


if __name__ == '__main__':
    unittest.main()
