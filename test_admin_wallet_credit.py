import importlib
import os
import tempfile
import unittest
from pathlib import Path


class API:
    def __init__(self): self.calls=[]
    def call(self, method, **data):
        self.calls.append((method,data)); return {'message_id':len(self.calls)}


class AdminWalletCreditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.initial=tempfile.TemporaryDirectory()
        os.environ['STORE_STATE_PATH']=str(Path(cls.initial.name)/'initial.db')
        cls.bot=importlib.import_module('bot')
        cls.store=importlib.import_module('storefront')
    @classmethod
    def tearDownClass(cls): cls.initial.cleanup()
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.old=self.store.DB_PATH
        self.store.DB_PATH=Path(self.tmp.name)/'store.db'; self.store._DB_SCHEMA_READY=False
        self.admin=self.bot.ADMIN_ID; self.api=API()
    def tearDown(self): self.store.DB_PATH=self.old; self.tmp.cleanup()
    def msg(self,text): return self.bot.handle_receipt(self.api,{'chat':{'id':self.admin},'text':text})
    def test_credit_requires_confirmation_logs_and_notifies(self):
        self.bot.action(self.api,self.admin,'admin:wallet_credit'); self.msg('123456'); self.msg('25.50')
        with self.store.db() as c: self.assertIsNone(c.execute('SELECT 1 FROM wallets WHERE cid=123456').fetchone())
        self.bot.action(self.api,self.admin,'admin:wallet_credit_confirm')
        with self.store.db() as c:
            self.assertEqual(c.execute('SELECT balance_sar FROM wallets WHERE cid=123456').fetchone(),('25.50',))
            self.assertEqual(c.execute('SELECT COUNT(*) FROM admin_wallet_adjustments').fetchone(),(1,))
        self.assertTrue(any(d.get('chat_id')==123456 for m,d in self.api.calls if m=='sendMessage'))
        self.bot.action(self.api,self.admin,'admin:wallet_credit_confirm')
        with self.store.db() as c: self.assertEqual(c.execute('SELECT balance_sar FROM wallets WHERE cid=123456').fetchone(),('25.50',))
    def test_invalid_amount_cancel_and_admin_button(self):
        self.bot.action(self.api,self.admin,'admin')
        buttons=[b for m,d in self.api.calls for row in d.get('reply_markup',{}).get('inline_keyboard',[]) for b in row]
        self.assertTrue(any(b.get('callback_data')=='admin:wallet_credit' for b in buttons))
        self.bot.action(self.api,self.admin,'admin:wallet_credit'); self.msg('123456')
        self.msg('-1'); self.msg('1.234'); self.bot.action(self.api,self.admin,'admin:wallet_credit_cancel')
        self.bot.action(self.api,self.admin,'admin:wallet_credit_confirm')
        with self.store.db() as c: self.assertIsNone(c.execute('SELECT 1 FROM wallets WHERE cid=123456').fetchone())


if __name__=='__main__': unittest.main()
