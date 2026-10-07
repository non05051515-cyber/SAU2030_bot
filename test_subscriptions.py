import sqlite3
import tempfile
import time
import unittest
import ast
from pathlib import Path
from types import SimpleNamespace
import subscriptions as sub


class SubscriptionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.NamedTemporaryFile()
        self.messages=[]
        self.ok=True
        self.s=SimpleNamespace(
            db=lambda:sqlite3.connect(self.temp.name),G={'ADMIN_ID':1,'PRODUCTS':{}},
            send=lambda api,cid,text,kb=None:self.send(cid,text,kb),
            btn=lambda text,data,*args,**kw:dict(text=text,callback_data=data),
            kb=lambda rows:rows,tr=lambda cid,ar,en:ar,esc=str,name=lambda pid,cid:pid,
            can_order=lambda pid:True,VARIANTS={})
        with self.s.db() as c:
            c.executescript("""
            CREATE TABLE orders(id TEXT PRIMARY KEY,cid INTEGER,pid TEXT,method TEXT,usd TEXT,sar TEXT,status TEXT,created_at TEXT);
            CREATE TABLE admin_state(cid INTEGER PRIMARY KEY,action TEXT,value TEXT);
            CREATE TABLE selected_quantities(cid INTEGER,pid TEXT,qty INTEGER,PRIMARY KEY(cid,pid));
            CREATE TABLE admin_categories(cid TEXT,name TEXT);
            CREATE TABLE admin_products(pid TEXT,name TEXT,category_id TEXT);
            INSERT INTO orders VALUES ('history',8,'p','wallet','1','3.75','delivered','2020-01-01');
            """)
        sub.prepare(self.s)
        with self.s.db() as c:
            c.execute("INSERT INTO subscription_settings VALUES ('p',1,29,3)")
        self.calls=[]
        self.s.admin_info_editor=lambda *a:None
        self.s.admin_panel=lambda *a:None
        self.s.wallet=lambda *a:None
        self.ns={'action':lambda api,cid,value:self.calls.append(value),'handle_receipt':lambda *a:False}
        sub.install(self.s,self.ns)
    def tearDown(self):
        self.temp.close()
    def send(self,cid,text,kb):
        self.messages.append((cid,text,kb))
        return {'message_id':1} if self.ok else None
    def order(self,oid,status='review',cid=8,pid='p'):
        with self.s.db() as c:
            c.execute('INSERT INTO orders VALUES (?,?,?,?,?,?,?,?)',(oid,cid,pid,'wallet','1','3.75',status,'now'))
    def deliver(self,oid):
        with self.s.db() as c:c.execute("UPDATE orders SET status='delivered' WHERE id=?",(oid,))
    def test_delivery_and_snapshot(self):
        self.assertIsNone(sub.owned(self.s,8,'history'))
        self.order('new')
        with self.s.db() as c:c.execute("UPDATE subscription_settings SET days=60 WHERE pid='p'")
        self.assertIsNone(sub.owned(self.s,8,'new'))
        with self.s.db() as c:c.execute("UPDATE orders SET status='paid' WHERE id='new'")
        self.assertIsNone(sub.owned(self.s,8,'new'))
        self.deliver('new')
        row=sub.owned(self.s,8,'new')
        self.assertEqual(row[4],29)
        self.assertEqual(row[3]-row[2],29*86400)
        self.deliver('new')
        self.assertEqual(sub.owned(self.s,8,'new'),row)
        self.order('untracked',pid='other')
        self.deliver('untracked')
        self.assertIsNone(sub.owned(self.s,8,'untracked'))
    def test_renewal_carries_remaining_only_on_delivery(self):
        self.order('old');self.deliver('old')
        previous=sub.owned(self.s,8,'old')
        sub.renew(self.s,self.ns,None,8,'old')
        self.assertEqual(self.calls,['buy:p'])
        self.order('renewal')
        self.assertIsNone(sub.owned(self.s,8,'renewal'))
        self.deliver('renewal')
        self.assertEqual(sub.owned(self.s,8,'renewal')[3],previous[3]+29*86400)
        sub.page(self.s,None,8)
        self.assertEqual(self.messages[-1][1].count('<b>p</b>'),1)
        self.calls.clear()
        sub.renew(self.s,self.ns,None,8,'old')
        self.assertFalse(self.calls)
    def test_expired_and_cancelled_renewal(self):
        self.order('old');self.deliver('old')
        with self.s.db() as c:c.execute("UPDATE customer_subscriptions SET expires=? WHERE order_id='old'",(int(time.time())-100,))
        sub.renew(self.s,self.ns,None,8,'old')
        self.order('cancelled','rejected')
        self.assertIsNone(sub.owned(self.s,8,'cancelled'))
        sub.renew(self.s,self.ns,None,8,'old')
        self.order('replacement')
        self.deliver('replacement')
        row=sub.owned(self.s,8,'replacement')
        self.assertEqual(row[3]-row[2],29*86400)
    def test_owner_stock_duration_and_admin_guards(self):
        self.order('old');self.deliver('old')
        sub.renew(self.s,self.ns,None,9,'old')
        self.assertFalse(self.calls)
        self.s.can_order=lambda pid:False
        sub.renew(self.s,self.ns,None,8,'old')
        self.assertFalse(self.calls)
        self.assertEqual(self.messages[-1][2][0][0]['callback_data'],'options:p')
        self.s.can_order=lambda pid:True
        with self.s.db() as c:c.execute("UPDATE subscription_settings SET days=7 WHERE pid='p'")
        sub.renew(self.s,self.ns,None,8,'old')
        self.assertFalse(self.calls)
        self.ns['action'](None,8,'subcfg:toggle:p')
        self.assertEqual(sub.config(self.s,'p'),(1,7,3))
    def test_reminder_retry_and_deduplication(self):
        self.order('old');self.deliver('old')
        with self.s.db() as c:c.execute("UPDATE customer_subscriptions SET expires=? WHERE order_id='old'",(int(time.time())+86400,))
        self.ok=False
        sub.tick(self.s,None)
        with self.s.db() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM subscription_reminders').fetchone()[0],0)
        self.ok=True
        with self.s.db() as c:c.execute('UPDATE subscription_reminder_retry SET retry_at=0')
        sub.tick(self.s,None)
        count=len(self.messages)
        sub.tick(self.s,None)
        self.assertEqual(len(self.messages),count)
        self.assertEqual(self.messages[-1][2][0][0]['callback_data'],'subrenew:old')
    def test_navigation_clears_intent_and_admin_input(self):
        self.order('old');self.deliver('old')
        sub.renew(self.s,self.ns,None,8,'old')
        self.ns['action'](None,8,'options:p')
        self.order('separate')
        self.deliver('separate')
        row=sub.owned(self.s,8,'separate')
        self.assertEqual(row[3]-row[2],29*86400)
        self.ns['action'](None,1,'subcfg:days:p')
        self.ns['handle_receipt'](None,{'chat':{'id':1},'text':'0'})
        self.assertEqual(sub.config(self.s,'p')[1],29)
        self.ns['handle_receipt'](None,{'chat':{'id':1},'text':'٣٠'})
        self.assertEqual(sub.config(self.s,'p')[1],30)
    def test_direct_delivered_insert(self):
        self.order('instant','delivered')
        self.assertEqual(sub.owned(self.s,8,'instant')[4],29)

    def test_manual_delivery_requires_telegram_success(self):
        self.order('manual','paid')
        parsed=ast.parse(Path(__file__).with_name('bot.py').read_text())
        node=next(n for n in parsed.body if isinstance(n,ast.FunctionDef) and n.name=='handle_admin_delivery')
        pending={1:{'customer':8,'order_id':'manual'}}
        ns={'ADMIN_ID':1,'PENDING_ADMIN_DELIVERY':pending,'storefront':self.s,
            'payment_execution':SimpleNamespace(guard_delivery=lambda *a:False),
            'send':lambda *a:None}
        exec(compile(ast.Module(body=[node],type_ignores=[]),'manual_delivery','exec'),ns)
        message={'chat':{'id':1},'message_id':77}
        ns['handle_admin_delivery'](SimpleNamespace(call=lambda *a,**kw:None),message)
        self.assertIsNone(sub.owned(self.s,8,'manual'))
        self.assertIn(1,pending)
        ns['handle_admin_delivery'](SimpleNamespace(call=lambda *a,**kw:{'message_id':88}),message)
        self.assertIsNotNone(sub.owned(self.s,8,'manual'))
        self.assertNotIn(1,pending)


if __name__=='__main__':
    unittest.main()
