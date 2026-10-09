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
        self.s._DB_SCHEMA_READY = False
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
        if amount != 'NaN': self.bot.action(self.api, self.admin, 'couponadmin:all')
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
        self.bot.action(self.api, self.admin, 'couponadmin:all')
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
            self.assertIsNone(c.execute('SELECT id FROM orders').fetchone())
        self.bot.action(self.api, self.cid, 'receipt:bybitid:' + self.pid)
        with self.s.db() as c:
            self.assertEqual(c.execute('SELECT usd,sar,status FROM orders').fetchone(), ('6.67', '25.00', 'review'))
    def test_home_coupon_applies_at_checkout(self):
        self.create()
        self.s.payments(self.api, self.cid, self.pid)
        buttons = self.api.calls[-1][1]['reply_markup']['inline_keyboard']
        next(b for row in buttons for b in row if b.get('callback_data') == 'coupon:' + self.pid)
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

    def test_selected_scope_requires_selection_and_rejects_other_product(self):
        import discounts
        self.bot.action(self.api, self.admin, 'couponadmin:new')
        self.msg(self.admin, 'ONLYONE')
        self.msg(self.admin, '5')
        self.bot.action(self.api, self.admin, 'couponadmin:select')
        self.bot.action(self.api, self.admin, 'couponadmin:save')
        with self.s.db() as c:
            self.assertIsNone(c.execute('SELECT code FROM discount_codes').fetchone())
        ids = discounts.product_ids(self.s)
        self.bot.action(self.api, self.admin, 'couponadmin:pick:' + str(ids.index(self.pid)) + ':0')
        self.bot.action(self.api, self.admin, 'couponadmin:save')
        with self.s.db() as c:
            self.assertEqual(c.execute('SELECT scope FROM discount_codes').fetchone()[0], 'selected')
            self.assertEqual(c.execute('SELECT pid FROM discount_products').fetchall(), [(self.pid,)])
        self.bot.action(self.api, self.cid, 'coupon:*')
        self.msg(self.cid, 'ONLYONE')
        self.assertEqual(self.s.checkout_totals(self.cid, self.pid)[0], Decimal('25'))
        other = 'pd_03'
        with self.s.db() as c:
            c.execute('INSERT OR REPLACE INTO product_prices VALUES (?,?,?)', (other, '30', 'SAR'))
            c.execute('INSERT OR REPLACE INTO product_availability VALUES (?,1)', (other,))
            c.execute('INSERT OR REPLACE INTO product_visibility VALUES (?,1)', (other,))
        self.assertEqual(self.s.checkout_totals(self.cid, other)[0], Decimal('30'))
        self.bot.action(self.api, self.cid+1, 'coupon:' + other)
        self.msg(self.cid+1, 'ONLYONE')
        with self.s.db() as c:
            self.assertIsNone(c.execute('SELECT code FROM customer_discounts WHERE cid=?', (self.cid+1,)).fetchone())

    def test_legacy_coupon_migration(self):
        import sqlite3
        import discounts
        with sqlite3.connect(':memory:') as c:
            c.execute('CREATE TABLE discount_codes (code TEXT PRIMARY KEY, sar TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1)')
            c.execute("INSERT INTO discount_codes VALUES ('OLD','5',1)")
            discounts.prepare(c)
            discounts.prepare(c)
            self.assertEqual(c.execute('SELECT code,scope FROM discount_codes').fetchone(), ('OLD', 'all'))

    def start_variable(self, code='DIFFERENT'):
        import discounts
        self.bot.action(self.api, self.admin, 'couponadmin:new')
        self.msg(self.admin, code)
        self.bot.action(self.api, self.admin, 'couponadmin:variable')
        return discounts.product_ids(self.s)

    def set_product_discount(self, ids, pid, amount):
        index = ids.index(pid)
        self.bot.action(self.api, self.admin, f'couponadmin:pick:{index}:{index//15}')
        self.msg(self.admin, amount)

    def test_different_amounts_for_one_code_at_checkout(self):
        ids = self.start_variable()
        other, excluded = 'pd_03', 'pd_04'
        with self.s.db() as c:
            for pid, price in ((self.pid, '7'), (other, '14'), (excluded, '20')):
                c.execute('INSERT OR REPLACE INTO product_prices VALUES (?,?,?)', (pid, price, 'SAR'))
                c.execute('INSERT OR REPLACE INTO product_availability VALUES (?,1)', (pid,))
                c.execute('INSERT OR REPLACE INTO product_visibility VALUES (?,1)', (pid,))
        self.set_product_discount(ids, self.pid, '٣')
        self.set_product_discount(ids, other, '4')
        self.bot.action(self.api, self.admin, 'couponadmin:save')
        with self.s.db() as c:
            self.assertEqual(c.execute('SELECT pid,sar FROM discount_products ORDER BY pid').fetchall(), [(self.pid, '3'), (other, '4')])
        self.bot.action(self.api, self.cid, 'coupon:*')
        self.msg(self.cid, 'DIFFERENT')
        self.assertEqual(self.s.checkout_totals(self.cid, self.pid)[:3], (Decimal('4'), Decimal('1.07'), Decimal('3')))
        self.assertEqual(self.s.checkout_totals(self.cid, other)[:3], (Decimal('10'), Decimal('2.67'), Decimal('4')))
        self.assertEqual(self.s.checkout_totals(self.cid, excluded)[0], Decimal('20'))
        self.s.wallet_credit(self.cid, '20')
        self.bot.action(self.api, self.cid, 'paywallet:' + self.pid)
        self.assertEqual(self.s.wallet_balance(self.cid), Decimal('16'))
        self.bot.action(self.api, self.admin, 'couponadmin:toggle:DIFFERENT')
        self.assertEqual(self.s.checkout_totals(self.cid, other)[0], Decimal('14'))

    def test_product_amount_validation_edit_removal_and_cancel(self):
        ids = self.start_variable()
        self.set_product_discount(ids, self.pid, 'NaN')
        with self.s.db() as c:
            self.assertEqual(c.execute('SELECT action FROM admin_state WHERE cid=?', (self.admin,)).fetchone()[0], 'coupon_product_amount')
        for bad in ('-1', '0', '1.234', 'Infinity'):
            self.msg(self.admin, bad)
        self.msg(self.admin, '3')
        self.set_product_discount(ids, self.pid, '4.50')
        with self.s.db() as c:
            import json
            draft = json.loads(c.execute('SELECT value FROM admin_state WHERE cid=?', (self.admin,)).fetchone()[0])
            self.assertEqual(draft['amounts'][self.pid], '4.50')
        self.bot.action(self.api, self.admin, f'couponadmin:pick:{ids.index(self.pid)}:0')
        self.bot.action(self.api, self.admin, 'couponadmin:removeproduct')
        self.bot.action(self.api, self.admin, 'couponadmin:save')
        with self.s.db() as c:
            self.assertIsNone(c.execute('SELECT code FROM discount_codes').fetchone())
        self.set_product_discount(ids, self.pid, '100')
        self.bot.action(self.api, self.admin, 'couponadmin:save')
        self.bot.action(self.api, self.cid, 'coupon:' + self.pid)
        self.msg(self.cid, 'DIFFERENT')
        self.assertEqual(self.s.checkout_totals(self.cid, self.pid)[0], 0)

    def test_selected_coupon_migration_keeps_fixed_discount(self):
        import sqlite3
        import discounts
        with sqlite3.connect(':memory:') as c:
            c.execute('CREATE TABLE discount_codes (code TEXT PRIMARY KEY,sar TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,scope TEXT NOT NULL)')
            c.execute('CREATE TABLE discount_products (code TEXT NOT NULL,pid TEXT NOT NULL,PRIMARY KEY(code,pid))')
            c.execute("INSERT INTO discount_codes VALUES ('OLD','5',1,'selected')")
            c.execute("INSERT INTO discount_products VALUES ('OLD','pd_02')")
            discounts.prepare(c)
            discounts.prepare(c)
            self.assertEqual(c.execute('SELECT code,pid,sar FROM discount_products').fetchone(), ('OLD', 'pd_02', None))


if __name__ == '__main__': unittest.main()


