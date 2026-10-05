import tempfile
import unittest
from pathlib import Path
import bot
from unittest.mock import patch
import storefront as s
from test_product_announcements import API


class StockEditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = s.DB_PATH
        s.DB_PATH = Path(self.tmp.name) / 'store.db'
        self.api = API()
        self.cid = bot.ADMIN_ID
        self.pid = 'custom_stock_test'
        with s.db() as c:
            c.execute('INSERT INTO admin_products(pid,name,description,price_sar,available,created_at,category_id,price_usd,stock) VALUES (?,?,?,?,?,?,?,?,?)', (self.pid,'test','test','4',1,'today','iptv','1',1))

    def tearDown(self):
        s.DB_PATH = self.old
        self.tmp.cleanup()

    def state(self):
        with s.db() as c:
            return c.execute('SELECT action,value FROM admin_state WHERE cid=?', (self.cid,)).fetchone()

    def test_real_button_and_message_save_arabic_digits_and_zero(self):
        bot.action(self.api,self.cid,'stockpick:' + self.pid)
        button = self.api.calls[-1][1]['reply_markup']['inline_keyboard'][1][0]
        bot.action(self.api,self.cid,button['callback_data'])
        self.assertEqual(self.state(), ('stock_quantity',self.pid))
        with patch('chatgpt_extension.is_active', return_value=True):
            self.assertTrue(bot.handle_receipt(self.api, {'chat':{'id':self.cid}, 'text':'١٨'}))
        self.assertEqual(s.product_stock(self.pid),18)
        self.assertIsNone(self.state())
        self.assertIn('18', self.api.calls[-1][1]['text'])
        bot.action(self.api,self.cid,'stockqty:' + self.pid)
        bot.handle_receipt(self.api, {'chat':{'id':self.cid}, 'text':'0'})
        self.assertEqual(s.product_stock(self.pid),0)
        self.assertFalse(s.in_stock(self.pid))

    def test_invalid_cancel_and_unauthorized(self):
        bot.action(self.api,self.cid,'stockqty:' + self.pid)
        bot.handle_receipt(self.api, {'chat':{'id':self.cid}, 'text':'-1'})
        self.assertEqual(s.product_stock(self.pid),1)
        self.assertEqual(self.state(),('stock_quantity',self.pid))
        bot.action(self.api,self.cid,'stockpick:' + self.pid)
        self.assertIsNone(self.state())
        bot.action(self.api,42,'stockqty:' + self.pid)
        with s.db() as c:
            self.assertIsNone(c.execute('SELECT 1 FROM admin_state WHERE cid=42').fetchone())

if __name__ == '__main__': unittest.main()
