"""Mixed built-in and owner-added products must remain independently editable."""
import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_button_names import API


class YouTubeAdminTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / 'state.sqlite3'
        env = patch.dict(os.environ, {'STORE_STATE_PATH': str(path)})
        env.start()
        self.addCleanup(env.stop)
        self.bot = importlib.import_module('bot')
        self.s = importlib.import_module('storefront')
        db_path = patch.object(self.s, 'DB_PATH', path)
        db_path.start()
        self.addCleanup(db_path.stop)
        self.api = API()
        self.admin = self.bot.ADMIN_ID
        with self.s.db() as db:
            db.execute("INSERT INTO admin_products(pid,name,price_sar,created_at,category_id,price_usd,stock) VALUES ('added_yt','Family account','15','now','youtube','4',0)")

    def callbacks(self):
        return [b['callback_data'] for b in self.api.buttons()]

    def test_name_and_description_include_both_products(self):
        for field in ('name', 'description'):
            self.bot.action(self.api, self.admin, 'txtcat:' + field + ':youtube')
            for pid in ('youtube', 'added_yt'):
                self.assertEqual(self.callbacks().count('txtpick:' + field + ':' + pid), 1)

    def test_prices_include_original_and_added_even_when_hidden(self):
        with self.s.db() as db:
            db.execute("INSERT INTO product_visibility VALUES ('youtube',0)")
        self.bot.action(self.api, self.admin, 'pricecat:youtube')
        for pid in ('youtube', 'added_yt'):
            self.assertEqual(self.callbacks().count('pricepick:' + pid), 1)
        other_price = self.s.amount('added_yt', 'SAR')
        self.bot.action(self.api, self.admin, 'pricepick:youtube')
        self.assertIn('priceedit:SAR:youtube', self.callbacks())
        self.bot.action(self.api, self.admin, 'priceedit:SAR:youtube')
        self.assertTrue(self.bot.handle_receipt(self.api, {
            'chat': {'id': self.admin}, 'text': '14.99'}))
        self.assertEqual(str(self.s.amount('youtube', 'SAR')), '14.99')
        self.assertEqual(self.s.amount('added_yt', 'SAR'), other_price)

    def test_price_and_description_lists_match_legacy_youtube_categories(self):
        with self.s.db() as db:
            db.execute("INSERT INTO admin_categories VALUES ('legacy_yt','YouTube Family','now')")
            db.execute("UPDATE admin_products SET category_id='legacy_yt' WHERE pid='added_yt'")
        self.bot.action(self.api, self.admin, 'txtcat:description:youtube')
        described = {value.removeprefix('txtpick:description:') for value in self.callbacks() if value.startswith('txtpick:description:')}
        self.bot.action(self.api, self.admin, 'pricecat:youtube')
        priced = {value.removeprefix('pricepick:') for value in self.callbacks() if value.startswith('pricepick:')}
        self.assertEqual(priced, described)
        self.assertEqual(priced, {'youtube', 'added_yt'})

    def test_hide_restore_each_product_independently(self):
        for pid, other in (('youtube', 'added_yt'), ('added_yt', 'youtube')):
            self.bot.action(self.api, self.admin, 'vistoggle:' + pid)
            self.assertFalse(self.s.product_visible(pid))
            self.assertTrue(self.s.product_visible(other))
            for item in ('youtube', 'added_yt'):
                self.assertIn('vistoggle:' + item, self.callbacks())
            self.bot.action(self.api, 75, 'product:youtube')
            self.assertNotIn('options:' + pid, self.callbacks())
            self.assertIn('options:' + other, self.callbacks())
            self.bot.action(self.api, self.admin, 'txtcat:name:youtube')
            self.assertIn('txtpick:name:' + pid, self.callbacks())
            self.bot.action(self.api, self.admin, 'vistoggle:' + pid)
            self.assertTrue(self.s.product_visible(pid))

    def test_rename_original_is_displayed_without_renaming_added_product(self):
        self.bot.action(self.api, self.admin, 'txtedit:name:ar:youtube')
        self.assertTrue(self.bot.handle_receipt(self.api, {
            'chat': {'id': self.admin}, 'text': 'دعوة يوتيوب شهر'}))
        self.assertEqual(self.s.name('youtube', self.admin), 'دعوة يوتيوب شهر')
        self.assertEqual(self.s.name('added_yt', self.admin), 'Family account')


if __name__ == '__main__':
    unittest.main()
