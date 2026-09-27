import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

class API:
    def __init__(self, success=True): self.calls=[]; self.success=success
    def call(self, method, **data):
        self.calls.append((method,data))
        return {'message_id': 1} if self.success else None

class ChannelTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        os.environ['STORE_STATE_PATH']=str(Path(self.temp.name)/'db')
        self.bot=importlib.import_module('bot')
        self.ch=importlib.import_module('channel_catalog')
        self.s=importlib.import_module('storefront')
        self.old=self.s.DB_PATH; self.s.DB_PATH=Path(os.environ['STORE_STATE_PATH'])
        self.pid='custom_channel_test';self.api=API()
        with self.s.db() as c:
            c.execute('INSERT INTO admin_products(pid,name,description,price_sar,available,created_at,category_id,price_usd,stock) VALUES (?,?,?,?,?,?,?,?,?)',(self.pid,'منتج الاختبار','وصف','15',1,'today','iptv','4',2))
    def tearDown(self): self.s.DB_PATH=self.old;self.temp.cleanup()
    def jobs(self):
        with self.ch.db() as c:return c.execute('SELECT pid FROM channel_catalog_queue').fetchall()
    def test_baseline_restock_and_no_duplicate(self):
        self.ch.scan();self.assertEqual(self.jobs(),[])
        with self.s.db() as c:c.execute('UPDATE admin_products SET stock=5 WHERE pid=?',(self.pid,))
        self.ch.scan();self.assertEqual(self.jobs(),[(self.pid,)])
        self.ch._last_tick=0;self.ch.tick(self.api)
        self.assertEqual(self.jobs(),[])
        self.ch.scan();self.assertEqual(self.jobs(),[])
        posts=[d for m,d in self.api.calls if d.get('chat_id')==self.ch.CHANNEL]
        self.assertEqual(len(posts),1);self.assertIn('الكمية المتوفرة: 5',posts[0]['text'])
        self.assertIn('?start=product_',posts[0]['reply_markup']['inline_keyboard'][0][0]['url'])
        with self.s.db() as c:c.execute('UPDATE admin_products SET stock=3 WHERE pid=?',(self.pid,))
        self.ch.scan();self.assertEqual(self.jobs(),[])
    def test_failure_retains_queue(self):
        self.ch.scan()
        with self.s.db() as c:c.execute('UPDATE admin_products SET stock=4 WHERE pid=?',(self.pid,))
        self.ch._last_tick=0;self.ch.tick(API(False));self.assertEqual(self.jobs(),[(self.pid,)])
    def test_deep_link_survives_pending_join(self):
        payload=self.ch.link(self.pid).split('?start=')[1]
        self.assertTrue(self.ch.remember(42,'/start '+payload))
        with patch.dict(self.s.G,{'action':lambda a,c,x:self.assertEqual(x,'item:'+self.pid)}):
            self.assertTrue(self.ch.resume(self.api,42))
        self.assertFalse(self.ch.resume(self.api,42))
        self.assertFalse(self.ch.remember(42,'/start product_fake'))
    def test_admin_only_and_photo(self):
        self.bot.action(self.api,42,'channel:send:'+self.pid)
        self.assertEqual(self.api.calls,[])
        with self.s.db() as c:c.execute('INSERT OR REPLACE INTO product_photos VALUES (?,?)',(self.pid,'photo-file'))
        self.bot.action(self.api,self.bot.ADMIN_ID,'channel:preview:'+self.pid)
        self.assertTrue(any(m=='sendPhoto' for m,d in self.api.calls))
        self.assertFalse(any(d.get('chat_id')==self.ch.CHANNEL for m,d in self.api.calls))
        self.bot.action(self.api,self.bot.ADMIN_ID,'channel:send:'+self.pid)
        self.assertTrue(any(d.get('chat_id')==self.ch.CHANNEL for m,d in self.api.calls))
    def test_new_product_and_unavailable_skip(self):
        self.ch.scan()
        with self.ch.db() as c:c.execute('DELETE FROM channel_catalog_state WHERE pid=?',(self.pid,))
        self.ch.scan();self.assertEqual(self.jobs(),[(self.pid,)])
        with self.s.db() as c:c.execute('UPDATE admin_products SET stock=0 WHERE pid=?',(self.pid,))
        self.ch._last_tick=0;self.ch.tick(self.api)
        self.assertEqual(self.jobs(),[]);self.assertEqual(self.api.calls,[])

if __name__=='__main__': unittest.main()
