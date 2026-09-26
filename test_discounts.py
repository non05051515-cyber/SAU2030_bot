import importlib
import os
from pathlib import Path
import tempfile
import unittest
from decimal import Decimal
from unittest.mock import patch


class API:
    def __init__(self): self.calls = []
    def call(self, method, **data):
        self.calls.append((method, data))
        return {'message_id': 1}


class DiscountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        os.environ['STORE_STATE_PATH'] = str(Path(self.temp.name) / 'state.db')
        self.bot = importlib.import_module('bot')
        self.s = importlib.import_module('storefront')
        self.old_path = self.s.DB_PATH
        self.s.DB_PATH = Path(os.environ['STORE_STATE_PATH'])
        self.api = API()
        self.cid = 7123
        self.admin = self.s.G['ADMIN_ID']
        self.pid = 'pd_02'
        with self.s.db() as c:
            c.execute('INSERT OR REPLACE INTO product_prices VALUES (?,?,?)', (self.pid, '30', 'SAR'))
            c.execute('INSERT OR REPLACE INTO product_availability VALUES (?,1)', (self.pid,))
            c.execute('INSERT OR REPLACE INTO product_visibility VALUES (?,1)', (self.pid,))
    def tearDown(self):
        self.s.DB_PATH = self.old_path
        self.temp.cleanup()
    def msg(self, cid, text):
        return self.bot.handle_receipt(self.api, {'chat': {'id': cid}, 'text': text})
    def create(self, amount='5'):
        self.bot.action(self.api, self.admin, 'couponadmin:new')
        self.assertTrue(self.msg(self.admin, 'vexa5'))
        self.assertTrue(self.msg(self.admin, amount))
    def apply(self):
        self.bot.action(self.api, self.cid, 'coupon:' + self.pid)
        self.assertTrue(self.msg(self.cid, ' vExA5 '))
    def test_admin_and_customer_ui_validation(self):
        self.bot.action(self.api, self.admin, 'admin')
        self.assertIn('couponadmin:list', str(self.api.calls))
        self.create('NaN')
        with self.s.db() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM discount_codes').fetchone()[0], 0)
        self.msg(self.admin, '٥٫٥٠')
        self.apply()
        self.assertEqual(self.s.checkout_totals(self.cid, self.pid)[:2], (Decimal('24.50'), Decimal('6.53')))
        self.assertIn('24.50', str(self.api.calls))
        self.bot.action(self.api, self.cid, 'couponremove:' + self.pid)
        self.assertEqual(self.s.checkout_totals(self.cid, self.pid)[0], Decimal('30'))
    def test_permission_disabled_unknown_and_isolation(self):
        self.bot.action(self.api, self.cid, 'couponadmin:new')
        with self.s.db() as c:
            self.assertIsNone(c.execute('SELECT * FROM admin_state WHERE cid=?', (self.cid,)).fetchone())
        self.create()
        self.apply()
        self.assertEqual(self.s.checkout_totals(self.cid + 1, self.pid)[0], Decimal('30'))
        self.bot.action(self.api, self.cid, 'couponadmin:toggle:VEXA5')
        self.assertEqual(self.s.checkout_totals(self.cid, self.pid)[0], Decimal('25'))
        self.bot.action(self.api, self.admin, 'couponadmin:toggle:VEXA5')
        self.assertEqual(self.s.checkout_totals(self.cid, self.pid)[0], Decimal('30'))
        self.bot.action(self.api, self.cid, 'coupon:' + self.pid)
        self.msg(self.cid, 'UNKNOWN')
        self.assertIn('غير صحيح', str(self.api.calls))
    def test_wallet_charges_and_records_discount(self):
        self.create()
        self.apply()
        self.s.wallet_credit(self.cid, '100')
        self.bot.action(self.api, self.cid, 'paywallet:' + self.pid)
        self.assertEqual(self.s.wallet_balance(self.cid), Decimal('75'))
        with self.s.db() as c:
            row = c.execute('SELECT usd,sar,status FROM orders WHERE cid=?', (self.cid,)).fetchone()
        self.assertEqual(row, ('6.67', '25.00', 'paid'))
    def test_crypto_invoice_snapshot(self):
        self.create()
        self.apply()
        with patch.object(self.s, 'crypto_invoice', return_value=('inv1', 'https://example.com')) as invoice:
            self.s.pay_with_crypto(self.api, self.cid, self.pid)
        self.assertEqual(invoice.call_args.args[0], Decimal('6.67'))
        with self.s.db() as c:
            oid = c.execute('SELECT id FROM crypto_orders').fetchone()[0]
        self.bot.action(self.api, self.admin, 'couponadmin:toggle:VEXA5')
        with patch.object(self.s, 'crypto_paid', return_value=True):
            self.s.check_crypto_order(self.api, self.cid, oid)
            self.s.check_crypto_order(self.api, self.cid, oid)
        with self.s.db() as c:
            self.assertEqual(c.execute('SELECT usd FROM orders').fetchall(), [('6.67',)])
    def test_manual_transfer_keeps_presented_quote(self):
        self.create()
        self.apply()
        with patch.dict(os.environ, {'PAYMENT_BYBIT_PAY_ID': 'test'}):
            self.s.payment(self.api, self.cid, self.pid, 'bybitid')
        self.bot.action(self.api, self.admin, 'couponadmin:toggle:VEXA5')
        self.s.receipt_request(self.api, self.cid, self.pid, 'bybitid')
        self.bot.handle_receipt(self.api, {'chat': {'id': self.cid}, 'photo': [{}], 'message_id': 1})
        with self.s.db() as c:
            self.assertEqual(c.execute('SELECT usd,sar,status FROM orders').fetchone(), ('6.67', '25.00', 'review'))
    def test_home_coupon_applies_at_checkout(self):
        self.create()
        self.bot.action(self.api, self.cid, 'home')
        buttons = self.api.calls[-1][1]['reply_markup']['inline_keyboard']
        button = next(b for row in buttons for b in row if b.get('callback_data') == 'coupon:*')
        self.assertEqual(button['style'], 'primary')
        self.bot.action(self.api, self.cid, 'coupon:*')
        self.assertTrue(self.msg(self.cid, 'vexa5'))
        self.bot.action(self.api, self.cid, 'buy:' + self.pid)
        self.assertEqual(self.s.checkout_totals(self.cid, self.pid)[0], Decimal('25'))
        self.bot.action(self.api, self.cid, 'couponremove:' + self.pid)
        self.assertEqual(self.s.checkout_totals(self.cid, self.pid)[0], Decimal('30'))

    def test_full_discount_and_cancel(self):
        self.create('100')
        self.apply()
        self.assertEqual(self.s.checkout_totals(self.cid, self.pid)[0], 0)
        self.s.pay_with_crypto(self.api, self.cid, self.pid)
        self.assertEqual(self.s.wallet_balance(self.cid), 0)
        with self.s.db() as c:
            self.assertEqual(c.execute('SELECT sar FROM orders').fetchone()[0], '0')
        self.bot.action(self.api, self.cid, 'coupon:' + self.pid)
        self.bot.action(self.api, self.cid, 'buy:' + self.pid)
        self.assertFalse(self.msg(self.cid, 'VEXA5'))


if __name__ == '__main__': unittest.main()
