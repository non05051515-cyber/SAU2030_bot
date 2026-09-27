import importlib
import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
import product_options as q

class API:
    def __init__(self):self.calls=[]
    def call(self,m,**d):self.calls.append((m,d));return {'message_id':1}

class OptionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        os.environ['STORE_STATE_PATH']=str(Path(self.tmp.name)/'db')
        self.b=importlib.import_module('bot');self.s=importlib.import_module('storefront')
        self.p=patch.object(self.s,'DB_PATH',Path(self.tmp.name)/'db');self.p.start()
        self.api=API();self.cid=123;self.pid='pd_02'
        self.stock=patch.object(self.s,'product_stock',return_value=24);self.stock.start()
        with self.s.db() as c:
            c.execute('INSERT INTO product_visibility VALUES (?,1)',(self.pid,))
            c.execute('INSERT INTO product_prices VALUES (?,?,?)',(self.pid,'10','SAR'))
            c.execute('INSERT INTO product_availability VALUES (?,1)',(self.pid,))
    def tearDown(self):self.stock.stop();self.p.stop();self.tmp.cleanup()
    def test_ui_and_wallet_quantity(self):
        self.b.action(self.api,self.cid,'item:'+self.pid)
        text=str(self.api.calls)
        self.assertIn('كمية مخصصة',text);self.assertIn('chooseqty:pd_02:10',text)
        self.b.action(self.api,self.cid,'chooseqty:pd_02:3')
        self.assertEqual(self.s.checkout_totals(self.cid,self.pid)[0],Decimal('30'))
        self.s.wallet_credit(self.cid,Decimal('100'))
        self.b.action(self.api,self.cid,'paywallet:'+self.pid)
        self.assertEqual(self.s.wallet_balance(self.cid),Decimal('70'))
        with self.s.db() as c:oid=c.execute('SELECT id FROM orders').fetchone()[0]
        self.assertEqual(q.snapshot(self.s,'order',oid),3)
    def test_custom_and_bounds(self):
        self.b.action(self.api,self.cid,'customqty:'+self.pid)
        self.b.handle_receipt(self.api,{'chat':{'id':self.cid},'text':'٢'})
        self.assertEqual(q.selected(self.s,self.cid,self.pid),2)
        self.b.action(self.api,self.cid,'chooseqty:pd_02:25')
        self.assertEqual(q.selected(self.s,self.cid,self.pid),2)
        with patch.object(self.s,'product_stock',return_value=1):
            self.b.action(self.api,self.cid,'paywallet:'+self.pid)
        with self.s.db() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM orders').fetchone()[0],0)
    def test_price_and_quantity_snapshots_survive_selection_change(self):
        with self.s.db() as c:
            c.execute('INSERT INTO selected_quantities VALUES (?,?,3)',(self.cid,self.pid))
            c.execute('INSERT INTO payment_quotes VALUES (?,?,?,?,?)',(self.cid,self.pid,'bank','8','30'))
            c.execute('INSERT INTO crypto_orders VALUES (?,?,?,?,?,?)',('invoice',self.cid,self.pid,'8','ext','pending'))
            c.execute('UPDATE selected_quantities SET qty=1')
            c.execute('INSERT INTO receipts VALUES (?,?,?,?,?)',(self.cid,self.pid,'bank','8','30'))
        oid=self.s.add_order(self.cid,self.pid,'bank','review',usd='8',sar='30')
        self.assertEqual(q.snapshot(self.s,'order',oid),3)
        with patch.object(self.s,'crypto_paid',return_value=True):
            self.s.check_crypto_order(self.api,self.cid,'invoice')
        with self.s.db() as c:
            oid=c.execute("SELECT id FROM orders WHERE method='cryptopay'").fetchone()[0]
        self.assertEqual(q.snapshot(self.s,'order',oid),3)
    def test_coupon_once_per_order(self):
        with self.s.db() as c:
            c.execute('INSERT INTO selected_quantities VALUES (?,?,3)',(self.cid,self.pid))
            c.execute("INSERT INTO discount_codes VALUES ('SAVE','5',1)")
            c.execute("INSERT INTO customer_discounts VALUES (?,?,'SAVE')",(self.cid,self.pid))
        self.assertEqual(self.s.checkout_totals(self.cid,self.pid)[0],Decimal('25'))
    def test_alert_transition_and_unsubscribe(self):
        self.b.action(self.api,self.cid,'stockalert:'+self.pid)
        with self.s.db() as c:c.execute('UPDATE stock_alerts SET was_available=0')
        with patch('product_options.time.monotonic',return_value=100000000):self.b.tick_stock_alerts(self.api)
        self.assertIn('عاد المنتج للتوفر',str(self.api.calls))
        self.b.action(self.api,self.cid,'stockalert:'+self.pid)
        with self.s.db() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM stock_alerts').fetchone()[0],0)

if __name__=='__main__':unittest.main()
