import unittest
from unittest.mock import patch
from test_payment_execution import Payments
import local_delivery as delivery

class LocalDelivery(unittest.TestCase):
    setUpClass=classmethod(Payments.setUpClass.__func__)
    tearDownClass=classmethod(Payments.tearDownClass.__func__)
    setUp=Payments.setUp
    tearDown=Payments.tearDown
    supplier=Payments.supplier
    query=Payments.query
    def stock(self,n=1):
        with self.s.db() as c:
            for i in range(n):c.execute('INSERT INTO local_delivery_stock(pid,email,password,profile) VALUES (?,?,?,?)',('pc_3','test@example.invalid','TEST_PASSWORD',str(i+1)))
            c.execute("DELETE FROM supplier_api WHERE pid='pc_3'")
            c.execute("UPDATE admin_products SET name='Shahid Private Profile',category_id=NULL WHERE pid='pc_3'")
    def order(self,qty=1,status='paid'):
        return self.s.add_order(7,'pc_3','bank',status,usd='10',sar='37.5',quantity=qty)
    def test_button_and_add_flow(self):
        self.s.admin_panel(self.api,self.admin)
        self.assertIn('localstockmenu',str(self.api.calls))
        self.bot.action(self.api,self.admin,'localstockmenu')
        self.bot.action(self.api,self.admin,'localstockcat:pandora_capcut')
        self.assertIn('localstock:pc_3',str(self.api.calls))
        self.bot.action(self.api,self.admin,'localstockadd:pc_3')
        delivery.handle_text(self.s,self.api,{'chat':{'id':self.admin},'text':'a@example.invalid | TEST_PASSWORD | 1'})
        self.assertEqual(delivery.count_available(self.s,'pc_3'),1)
    def test_unpaid_never_delivered(self):
        self.stock();oid=self.order(status='review')
        with patch.object(delivery,'_send_file') as f:self.assertFalse(self.s.fulfill_paid_order(self.api,oid));f.assert_not_called()
        self.assertEqual(delivery.count_available(self.s,'pc_3'),1)
    def test_paid_multiple_and_no_duplicate(self):
        self.stock(3);oid=self.order(qty=2)
        with patch.object(delivery,'_send_file',return_value=True) as f:
            self.assertTrue(self.s.fulfill_paid_order(self.api,oid));self.assertEqual(f.call_count,2)
            before=len(self.api.calls);self.s.fulfill_paid_order(self.api,oid);self.assertEqual(len(self.api.calls),before)
        self.assertEqual(delivery.count_available(self.s,'pc_3'),1)
        self.assertEqual(self.query('SELECT status FROM orders WHERE id=?',(oid,))[0][0],'delivered')
    def test_shortage_keeps_order_and_stock(self):
        self.stock();oid=self.order(qty=2)
        with patch.object(delivery,'_send_file') as f:self.assertTrue(self.s.fulfill_paid_order(self.api,oid));f.assert_not_called()
        self.assertEqual(delivery.count_available(self.s,'pc_3'),1)
        self.assertEqual(self.query('SELECT status FROM orders WHERE id=?',(oid,))[0][0],'paid')
    def test_file_failure_retry_same_account_without_resending_text(self):
        self.stock(2);oid=self.order()
        with patch.object(delivery,'_send_file',side_effect=[False,True]):
            self.s.fulfill_paid_order(self.api,oid)
            self.assertEqual(self.query('SELECT status FROM orders WHERE id=?',(oid,))[0][0],'paid')
            self.bot.action(self.api,self.admin,'localretry:'+oid)
        self.assertEqual(delivery.count_available(self.s,'pc_3'),1)
        customer=[d for m,d in self.api.calls if d.get('chat_id')==7 and 'TEST_PASSWORD' in d.get('text','')]
        self.assertEqual(len(customer),1)
    def test_accept_payment_triggers_delivery(self):
        self.stock();oid=self.order(status='review')
        with patch.object(delivery,'_send_file',return_value=True):self.e.approve(self.s,self.api,self.admin,'accept',oid)
        self.assertEqual(self.query('SELECT status FROM orders WHERE id=?',(oid,))[0][0],'delivered')
    def test_bad_line_not_partially_saved_and_cancel(self):
        delivery.begin_add(self.s,self.api,self.admin,'pc_3')
        delivery.handle_text(self.s,self.api,{'chat':{'id':self.admin},'text':'a | b | 1\ninvalid'})
        self.assertEqual(delivery.count_available(self.s,'pc_3'),0)
        delivery.action(self.s,self.api,self.admin,'localstock:pc_3')
        self.assertFalse(delivery.handle_text(self.s,self.api,{'chat':{'id':self.admin},'text':'a | b | 1'}))

if __name__=='__main__':unittest.main()
