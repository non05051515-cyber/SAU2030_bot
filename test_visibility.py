"""Regression checks for hiding products in the owner's storefront."""
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


class VisibilityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        store.DB_PATH = Path(self.directory.name) / 'store.sqlite3'
        self.api = FakeAPI()
        self.admin = bot.ADMIN_ID
        self.customer = 123456

    def test_hidden_variant_disappears_for_owner_and_customer(self):
        bot.action(self.api, self.admin, 'vistoggle:pd_04')
        self.assertFalse(store.product_visible('pd_04'))
        self.assertIn('vistoggle:pd_04', self.api.callbacks())  # Can restore it.

        for cid in (self.admin, self.customer):
            bot.action(self.api, cid, 'product:chatgpt')
            self.assertNotIn('item:pd_04', self.api.callbacks())
            bot.action(self.api, cid, 'item:pd_04')
            self.assertIn('مخفي', self.api.calls[-1][1]['text'])

        bot.action(self.api, self.admin, 'vistoggle:pd_04')
        bot.action(self.api, self.admin, 'product:chatgpt')
        self.assertIn('item:pd_04', self.api.callbacks())

    def test_hidden_iptv_variant_and_simple_product(self):
        iptv_pid = next(pid for pid in store.VARIANTS
                        if store.VARIANTS[pid]['category'] == 'iptv')
        for pid, parent in ((iptv_pid, 'iptv'), ('youtube', None)):
            bot.action(self.api, self.admin, 'vistoggle:' + pid)
            if parent:
                bot.action(self.api, self.admin, 'product:' + parent)
                self.assertNotIn('item:' + pid, self.api.callbacks())
            else:
                bot.action(self.api, self.admin, 'products')
                self.assertNotIn('product:' + pid, self.api.callbacks())
            self.assertFalse(store.can_order(pid))


if __name__ == '__main__':
    unittest.main()
