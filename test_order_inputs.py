import unittest
import importlib
import test_payment_execution as payment_tests


class OrderInputs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        payment_tests.Payments.setUpClass()
        cls.inputs=importlib.import_module('order_inputs')
    @classmethod
    def tearDownClass(cls):
        payment_tests.Payments.tearDownClass()
    def setUp(self):
        self.p=payment_tests.Payments('test_unauthorized_callback')
        self.p.setUp()
        self.s=self.p.s;self.bot=self.p.bot;self.api=self.p.api;self.admin=self.p.admin
        self.bot.action(self.api,self.admin,'inputcfg:both:pc_2')
    def tearDown(self):
        self.p.tearDown()
    def message(self,text,cid=7):
        return {'chat':{'id':cid,'type':'private'},'text':text}
    def submit(self,mode='both',status='review'):
        self.bot.action(self.api,self.admin,'inputcfg:'+mode+':pc_2')
        return self.p.order('pc_2',status=status)
    def test_both_collects_sequentially_and_does_not_execute_unpaid(self):
        oid=self.submit()
        self.inputs.prompt(self.s,self.api,self.inputs.pending(self.s,cid=7))
        self.assertTrue(self.bot.handle_order_input(self.api,self.message('not an email')))
        self.assertEqual(self.inputs.pending(self.s,cid=7)[3],'email')
        self.bot.handle_order_input(self.api,self.message('person@example.com'))
        self.assertEqual(self.inputs.pending(self.s,cid=7)[3],'password')
        self.bot.handle_order_input(self.api,self.message('FakePasswordOnlyForTest'))
        self.assertIsNone(self.inputs.pending(self.s,cid=7))
        self.assertFalse(self.p.purchases())
        self.assertEqual(self.p.query('SELECT email,password,step FROM order_customer_inputs'),[('person@example.com','FakePasswordOnlyForTest','done')])
        self.assertIn('جاري تنفيذ طلبك',str(self.api.calls))
        self.bot.action(self.api,self.admin,'payreview:accept:'+oid)
        self.assertEqual(len(self.p.purchases()),1)
    def test_approval_waits_until_required_fields_arrive(self):
        oid=self.submit()
        self.bot.action(self.api,self.admin,'payreview:accept:'+oid)
        self.assertEqual(self.p.query('SELECT status FROM orders'),[('paid',)])
        self.assertFalse(self.p.purchases())
        self.bot.handle_order_input(self.api,self.message('person@example.com'))
        self.assertFalse(self.p.purchases())
        self.bot.handle_order_input(self.api,self.message('FakePasswordOnlyForTest'))
        self.assertEqual(len(self.p.purchases()),1)
        self.assertEqual(self.p.query('SELECT status FROM orders'),[('delivered',)])
    def test_config_snapshotted_and_rejected_order_stops_collecting(self):
        oid=self.submit(mode='email')
        self.bot.action(self.api,self.admin,'inputcfg:off:pc_2')
        self.assertEqual(self.inputs.pending(self.s,cid=7)[2],'email')
        self.bot.action(self.api,self.admin,'payreview:reject:'+oid)
        self.assertIsNone(self.inputs.pending(self.s,cid=7))
        self.assertFalse(self.bot.handle_order_input(self.api,self.message('person@example.com')))
    def test_password_only_and_access_control(self):
        oid=self.submit(mode='password')
        self.assertFalse(self.bot.handle_order_input(self.api,self.message('wrong user',cid=8)))
        before=len(self.api.calls)
        self.bot.action(self.api,8,'inputview:'+oid)
        self.assertEqual(len(self.api.calls),before)
        self.bot.handle_order_input(self.api,self.message('<FAKE & password>'))
        self.bot.action(self.api,self.admin,'inputview:'+oid)
        self.assertIn('&lt;FAKE &amp; password&gt;',str(self.api.calls[-1]))
        self.assertFalse(self.p.purchases())
    def test_manual_delivery_and_local_retry_guard(self):
        oid=self.submit(status='paid')
        self.bot.action(self.api,self.admin,'orderdeliver:'+oid)
        self.assertFalse(self.bot.PENDING_ADMIN_DELIVERY)
        self.bot.PENDING_ADMIN_DELIVERY[self.admin]={'customer':7,'order_id':oid}
        self.bot.handle_admin_delivery(self.api,{'chat':{'id':self.admin},'message_id':123})
        self.assertFalse(any(m=='copyMessage' for m,d in self.api.calls))
        self.assertEqual(self.p.query('SELECT status FROM orders'),[('paid',)])
    def test_receipt_triggers_prompt_immediately(self):
        with self.s.db() as c:
            c.execute('INSERT INTO receipts VALUES (?,?,?,?,?)',(7,'pc_2','bank','20','75'))
        self.bot.handle_receipt(self.api,{'chat':{'id':7},'from':{'id':7},'message_id':999,'photo':[{'file_id':'fake'}]})
        self.assertIn('أرسل الإيميل المطلوب',str(self.api.calls[-1]))
        self.assertFalse(self.p.purchases())
    def test_multiple_orders_associate_each_reply_with_oldest(self):
        first=self.submit('email');second=self.submit('password')
        self.bot.handle_order_input(self.api,self.message('person@example.com'))
        self.assertEqual(self.inputs.pending(self.s,cid=7)[0],second)
        self.bot.handle_order_input(self.api,self.message('fake password'))
        self.assertEqual(self.p.query("SELECT step FROM order_customer_inputs"),[('done',),('done',)])
    def test_unconfigured_product_never_collects_and_admin_only_configuration(self):
        self.bot.action(self.api,7,'inputcfg:both:pc_3')
        oid=self.p.order('pc_3')
        self.assertIsNone(self.inputs.pending(self.s,oid=oid))
        self.bot.action(self.api,self.admin,'payreview:accept:'+oid)
        self.assertEqual(len(self.p.purchases()),1)


if __name__=='__main__':
    unittest.main()
