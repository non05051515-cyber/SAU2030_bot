import importlib
import json
import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

class API:
    def __init__(self): self.calls=[]
    def call(self, method, **data):
        self.calls.append((method,data))
        return {'message_id': 1}

class StoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        os.environ['STORE_STATE_PATH']=str(Path(cls.temp.name)/'state.sqlite3')
        cls.bot=importlib.import_module('bot')
        cls.s=importlib.import_module('storefront')
    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()
    def setUp(self): self.api=API();self.cid=7
    def test_prices_and_markup(self):
        s=self.s
        self.assertEqual(s.amount('pd_04','USD'),Decimal('7.50'))
        self.assertEqual(s.amount('pd_04','SAR'),Decimal('28.13'))
        self.assertEqual(s.amount('pd_02','SAR'),Decimal('76.84'))
        self.assertEqual(s.amount('pd_22','SAR'),Decimal('10.50'))
        self.assertEqual(s.amount('pd_23','USD','4.99'),Decimal('6.49'))
        self.assertEqual(s.amount('youtube','USD'),Decimal('4.00'))
        self.assertIsNone(s.amount('netflix'))
    def test_all_products_in_both_languages(self):
        for lang in ('ar','en'):
            self.s.action(self.api,self.cid,'setlang:'+lang)
            for v in self.s.VARIANTS.values():
                self.api.calls=[]
                self.s.item(self.api,self.cid,v['id'])
                messages=[d for m,d in self.api.calls if m=='sendMessage']
                self.assertTrue(messages)
                for d in messages:
                    self.assertLessEqual(len(d['text'].encode('utf-16-le'))//2,4096)
                buttons=messages[-1]['reply_markup']['inline_keyboard']
                for row in buttons:
                    for b in row:self.assertLessEqual(len(b['callback_data'].encode()),64)
                buy=any(b['callback_data'].startswith('buy:') for row in buttons for b in row)
                self.assertEqual(buy,self.s.can_order(v['id']))
    def test_preferences_survive_reload(self):
        self.s.action(self.api,55,'setlang:en')
        self.s.action(self.api,55,'setcurrency:USD')
        self.assertEqual(self.s.prefs(55),('en','USD'))
        self.assertEqual(self.s.price(55,'pd_04'),'7.50 USD')
        with self.s.db() as conn:
            self.assertEqual(conn.execute('SELECT lang,currency FROM preferences WHERE cid=55').fetchone(),('en','USD'))
    def test_blocked_items_and_old_callbacks(self):
        for pid in ('pd_05','pd_10','pd_04','claude_api_500m','unknown'):
            self.assertFalse(self.s.can_order(pid))
            self.api.calls=[]
            self.s.action(self.api,7,'paybank:'+pid)
            self.assertNotIn('PAYMENT_BANK_IBAN',str(self.api.calls))
            self.assertFalse(any('receipt:' in str(d) for _,d in self.api.calls))
        self.s.action(self.api,7,'claude:claude_pro')
        self.assertIn('Claude Pro',str(self.api.calls))
        self.assertIn('product:claude',str(self.api.calls[-1]))
    def test_receipt_cancel_and_success(self):
        self.s.receipt_request(self.api,7,'pd_02','bank')
        self.s.action(self.api,7,'cancel:pd_02')
        self.assertFalse(self.s.receipt(self.api,{'chat':{'id':7},'photo':[{}],'message_id':1}))
        self.s.receipt_request(self.api,7,'pd_02','bank')
        self.api.calls=[]
        self.assertTrue(self.s.receipt(self.api,{'chat':{'id':7},'photo':[{}],'message_id':1,'from':{'username':'test'}}))
        forwards=[d for m,d in self.api.calls if m=='forwardMessage']
        self.assertEqual(len(forwards),1)
        self.assertEqual(forwards[0]['chat_id'],8386371522)
        self.assertIn('76.84 SAR / 20.49 USD',str(self.api.calls))
    def test_preserve_categories_icons_and_admin(self):
        self.assertEqual(len(self.bot.PRODUCTS),9)
        self.assertEqual(self.bot.PRODUCTS['gemini']['custom_emoji_id'],'5312057964494874871')
        self.assertNotIn('spotify',self.bot.PRODUCTS)
        self.assertTrue(callable(self.bot.broadcast_product))
        self.assertEqual(len(self.s.VARIANTS),23)
    def test_unconfigured_payments(self):
        with patch.dict(os.environ,{},clear=True):
            self.s.payment(self.api,7,'pd_04','bank')
        self.assertNotIn('receipt:bank',str(self.api.calls))

if __name__=='__main__': unittest.main()
