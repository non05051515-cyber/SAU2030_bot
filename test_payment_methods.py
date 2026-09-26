from test_discounts import DiscountTests
import payment_methods as pm


class PaymentMethodTests(DiscountTests):
    def create_method(self):
        self.bot.action(self.api, self.admin, 'pm:new')
        for text in ('بنك الراجحي', 'محمد <اسم>', 'SA123456789'):
            self.assertTrue(self.msg(self.admin, text))
        self.assertEqual(pm.methods(self.s), [])
        self.assertIn('&lt;اسم&gt;', str(self.api.calls))
        self.bot.action(self.api, self.admin, 'pm:save')
        return pm.methods(self.s)[0][0]

    def test_method_checkout_receipt_and_disable(self):
        key = self.create_method()
        self.bot.action(self.api, self.admin, 'pm:save')
        self.assertEqual(len(pm.methods(self.s)), 1)
        self.bot.action(self.api, self.cid, 'buy:' + self.pid)
        self.assertIn(f'custompay:{key}:{self.pid}', str(self.api.calls))
        self.bot.action(self.api, self.cid, f'custompay:{key}:{self.pid}')
        self.assertIn('SA123456789', str(self.api.calls))
        self.bot.action(self.api, self.cid, f'receipt:custom_{key}:{self.pid}')
        with self.s.db() as c:
            row = c.execute('SELECT pid,method,sar FROM receipts WHERE cid=?', (self.cid,)).fetchone()
        self.assertEqual(row, (self.pid, f'custom_{key}', '30.00'))
        self.bot.action(self.api, self.admin, f'pm:toggle:{key}')
        self.assertEqual(pm.rows(self.s, self.pid), [])
        self.assertFalse(pm.valid(self.s, f'custom_{key}'))
        self.assertTrue(self.bot.handle_receipt(self.api, {'chat': {'id': self.cid}, 'photo': [{'file_id': 'proof'}], 'message_id': 4}))
        with self.s.db() as c:
            self.assertEqual(c.execute('SELECT status FROM orders WHERE cid=?', (self.cid,)).fetchone()[0], 'review')

    def test_permissions_cancel_and_validation(self):
        self.bot.action(self.api, self.cid, 'pm:new')
        with self.s.db() as c:
            self.assertIsNone(c.execute('SELECT * FROM admin_state WHERE cid=?', (self.cid,)).fetchone())
        self.bot.action(self.api, self.admin, 'pm:new')
        self.msg(self.admin, 'x' * 61)
        with self.s.db() as c:
            self.assertEqual(c.execute('SELECT value FROM admin_state WHERE cid=?', (self.admin,)).fetchone()[0], '{}')
        self.bot.action(self.api, self.admin, 'pm:list')
        self.assertEqual(pm.methods(self.s), [])
