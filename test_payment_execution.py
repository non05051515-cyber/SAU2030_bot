import importlib,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

class API:
 def __init__(self):self.calls=[];self.fail=False
 def call(self,m,**d):
  self.calls.append((m,d))
  if self.fail and m=='sendMessage' and d.get('chat_id')==7 and 'تم تسليم' in d.get('text',''):return None
  return {'message_id':len(self.calls)}

class Payments(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.import_tmp=tempfile.TemporaryDirectory()
  os.environ['STORE_STATE_PATH']=str(Path(cls.import_tmp.name)/'initial.sqlite3')
  cls.net=patch('urllib.request.urlopen',side_effect=AssertionError('Real HTTP forbidden'));cls.net.start()
  cls.s=importlib.import_module('storefront');cls.s.pandora_startup_probe=lambda:None
  cls.bot=importlib.import_module('bot');cls.e=importlib.import_module('payment_execution')
 @classmethod
 def tearDownClass(cls):cls.net.stop();cls.import_tmp.cleanup()
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.s.DB_PATH=Path(self.tmp.name)/'db';self.s._DB_SCHEMA_READY=False
  self.api=API();self.admin=self.bot.ADMIN_ID;self.bot.PENDING_ADMIN_DELIVERY.clear();self.requests=[];self.pending=False
  self.env=patch.dict(os.environ,{'PANDORA_API_KEY':'TEST_ONLY','PANDORA_API_BASE':'https://supplier.invalid'});self.env.start()
  with self.s.db() as c:
   c.execute('INSERT INTO admin_categories VALUES (?,?,?)',('pandora_capcut','CapCut','test'))
   for i,n in enumerate(('CAPCUT PRO 6 M INDIVIDUAL','CAPCUT PRO 1M 1600 CREDITS','Capcut Pro 1 Month FW','Capcut 7D FW')):
    c.execute('INSERT INTO admin_products(pid,name,description,price_sar,available,created_at,category_id,price_usd,stock) VALUES (?,?,?,?,?,?,?,?,?)',('pc_'+str(i),n,'','37.50',1,'test','pandora_capcut','10',20))
    c.execute('INSERT INTO supplier_api(pid,service_id,enabled,provider) VALUES (?,?,?,?)',('pc_'+str(i),'prd_'+str(i),1,'pandora'))
  self.http=patch.object(self.s,'_supplier_json_request',side_effect=self.supplier);self.http.start()
 def tearDown(self):
  self.http.stop();self.env.stop();self.tmp.cleanup()
  self.e.EVENT.message_id=None
 def supplier(self,url,key,method='GET',payload=None,extra_headers=None,**kw):
  self.requests.append((url,method,payload,extra_headers))
  if url.endswith('/quotes'):return {'can_purchase':True,'unit_price':'1','price_version':'v1'}
  r={'id':'supplier-1','status':'pending' if self.pending else 'completed'}
  if not self.pending:r['delivery']={'items':['FAKE DELIVERY']}
  return r
 def order(self,pid='pd_16',qty=2,status='review'):return self.s.add_order(7,pid,'bank',status,usd='20',sar='75',quantity=qty)
 def query(self,q,args=()):
  with self.s.db() as c:return c.execute(q,args).fetchall()
 def purchases(self):return [r for r in self.requests if r[0].endswith('/orders') and r[1]=='POST']
 def test_recover_verified_legacy_prices_once_and_preserve_later_edits(self):
  import pandora_admin
  pid='pc_prd_2ja9dtvu95-mwgfu'
  with self.s.db() as c:
   c.execute('UPDATE admin_products SET pid=?,price_usd=? WHERE pid=?',(pid,'0.15','pc_3'))
   c.execute('INSERT INTO product_prices VALUES (?,?,?)',('pd_17','0.38','USD'))
   c.execute('INSERT INTO product_prices VALUES (?,?,?)',(pid,'0.15','USD'))
  pandora_admin.restore_saved_capcut_prices(self.s)
  self.assertEqual(self.s.amount(pid,'USD'),__import__('decimal').Decimal('0.38'))
  with self.s.db() as c:c.execute('UPDATE product_prices SET value=? WHERE pid=?',('0.15',pid))
  pandora_admin.restore_saved_capcut_prices(self.s)
  self.assertEqual(self.s.amount(pid,'USD'),__import__('decimal').Decimal('0.15'))
 def test_recovery_does_not_replace_new_manual_price(self):
  import pandora_admin
  pid='pc_prd_88qi7o6gb9gdyowp'
  with self.s.db() as c:
   c.execute('UPDATE admin_products SET pid=?,price_usd=? WHERE pid=?',(pid,'1.29','pc_2'))
   c.execute('INSERT INTO product_prices VALUES (?,?,?)',('pd_16','2.00','USD'))
   c.execute('INSERT INTO product_prices VALUES (?,?,?)',(pid,'3.00','USD'))
  pandora_admin.restore_saved_capcut_prices(self.s)
  self.assertEqual(self.s.amount(pid,'USD'),__import__('decimal').Decimal('3.00'))
 def test_actual_receipt_and_final_accept_callback(self):
  with self.s.db() as c:
   c.execute('INSERT INTO receipts VALUES (?,?,?,?,?)',(7,'pd_16','bank','20','75'))
   c.execute('INSERT OR REPLACE INTO quantity_snapshots VALUES (?,?,?)',('receipt','7',2))
  self.assertTrue(self.bot.handle_receipt(self.api,{'chat':{'id':7},'from':{'id':7},'message_id':101,'photo':[{'file_id':'fake'}]}))
  oid,pid,status=self.query('SELECT id,pid,status FROM orders')[0];self.assertEqual((pid,status),('pd_16','review'))
  self.s.admin_orders(self.api,self.admin)
  callbacks=[b['callback_data'] for m,d in self.api.calls if m=='sendMessage' for row in d.get('reply_markup',{}).get('inline_keyboard',[]) for b in row if 'callback_data' in b]
  callback='payreview:accept:'+oid;self.assertIn(callback,callbacks)
  self.bot.action(self.api,self.admin,callback)
  self.assertEqual(self.query('SELECT status,pid FROM orders'),[('delivered','pd_16')])
  r=self.purchases();self.assertEqual(len(r),1);self.assertEqual(r[0][2]['product_id'],'prd_2');self.assertEqual(r[0][2]['quantity'],2)
  self.assertEqual(r[0][3],{'Idempotency-Key':'vexa-'+oid});self.assertEqual(self.bot.PENDING_ADMIN_DELIVERY,{})
  self.assertEqual(self.query('SELECT original_pid,supplier_pid,notified FROM payment_execution'),[('pd_16','pc_2',1)])
  self.bot.action(self.api,self.admin,callback);self.s.fulfill_paid_order(self.api,oid);self.assertEqual(len(self.purchases()),1)
 def test_four_legacy_skus_resolve(self):
  for a,b in [('pd_14','pc_0'),('pd_15','pc_1'),('pd_16','pc_2'),('pd_17','pc_3'),('capcut_7d','pc_3')]:self.assertEqual(self.e.resolve_pid(self.s,a),b)
 def test_final_customer_callback_shows_four(self):
  self.bot.action(self.api,7,'product:capcut')
  callbacks=[b['callback_data'] for m,d in self.api.calls if m=='sendMessage' for row in d.get('reply_markup',{}).get('inline_keyboard',[]) for b in row]
  self.assertEqual([x for x in callbacks if x.startswith('options:')],['options:pc_0','options:pc_1','options:pc_2','options:pc_3'])
 def test_pending_poll_auto_delivery(self):
  self.pending=True;oid=self.order('pc_2');self.bot.action(self.api,self.admin,'payreview:accept:'+oid)
  self.assertEqual(self.query('SELECT status FROM orders'),[('paid',)])
  self.pending=False;self.bot.tick_supplier_orders(self.api);self.assertEqual(self.query('SELECT status FROM orders'),[('delivered',)]);self.assertEqual(len(self.purchases()),1)
 def test_disabled_or_missing_key_never_manual(self):
  for enabled in (0,1):
   with self.s.db() as c:c.execute('UPDATE supplier_api SET enabled=? WHERE pid=?',(enabled,'pc_2'))
   with patch.dict(os.environ,{'PANDORA_API_KEY':''}):oid=self.order('pc_2');self.bot.action(self.api,self.admin,'payreview:accept:'+oid)
   self.bot.action(self.api,self.admin,'orderdeliver:'+oid);self.assertEqual(self.bot.PENDING_ADMIN_DELIVERY,{})
  self.assertFalse(self.requests)
 def test_stale_manual_delivery_blocked(self):
  oid=self.order('pd_16',status='paid');self.bot.PENDING_ADMIN_DELIVERY[self.admin]={'customer':7,'order_id':oid}
  self.assertTrue(self.bot.handle_admin_delivery(self.api,{'chat':{'id':self.admin},'message_id':9}));self.assertFalse(any(m=='copyMessage' for m,d in self.api.calls))
 def test_id_preserved_on_delivery_failure(self):
  self.api.fail=True;oid=self.order('pc_2');self.bot.action(self.api,self.admin,'payreview:accept:'+oid)
  self.assertEqual(self.query('SELECT supplier_order_id FROM supplier_orders'),[('supplier-1',)])
  self.api.fail=False;self.bot.tick_supplier_orders(self.api);self.assertEqual(self.query('SELECT status FROM orders'),[('delivered',)]);self.assertEqual(len(self.purchases()),1)
 def test_ambiguous_network_replays_identical_payload(self):
  oid=self.order('pc_2');calls=[]
  def uncertain(url,key,method='GET',payload=None,extra_headers=None,**kw):
   if url.endswith('/orders'):
    calls.append((payload.copy(),extra_headers.copy()))
    if len(calls)==1:raise TimeoutError()
   return self.supplier(url,key,method,payload,extra_headers,**kw)
  with patch.object(self.s,'_supplier_json_request',side_effect=uncertain):
   self.bot.action(self.api,self.admin,'payreview:accept:'+oid);self.bot.action(self.api,self.admin,'supplierretry:'+oid)
  self.assertEqual(calls[0],calls[1]);self.assertEqual(self.query('SELECT status FROM orders'),[('delivered',)])
 def test_concurrent_accept_one_purchase(self):
  oid=self.order('pc_2')
  with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda _:self.bot.action(self.api,self.admin,'payreview:accept:'+oid),range(2)))
  self.assertEqual(len(self.purchases()),1);self.assertEqual(self.bot.PENDING_ADMIN_DELIVERY,{})
 def test_wallet_double_press_one_debit(self):
  with self.s.db() as c:c.execute('INSERT INTO wallets VALUES (?,?)',(7,'100'))
  self.e.EVENT.message_id=123
  for _ in range(2):self.bot.action(self.api,7,'paywallet:pc_2')
  self.assertEqual(self.query('SELECT count(*) FROM orders'),[(1,)]);self.assertEqual(self.query('SELECT balance_sar FROM wallets'),[('62.50',)])
 def test_unauthorized_callback(self):
  oid=self.order('pc_2');self.bot.action(self.api,7,'payreview:accept:'+oid);self.assertEqual(self.query('SELECT status FROM orders'),[('review',)]);self.assertFalse(self.requests)
 def test_legacy_adminpay_goes_to_supplier(self):
  self.bot.PAYMENT_REVIEWS['old1']={'customer':7,'product':'capcut_1m','method':'bank'}
  self.bot.action(self.api,self.admin,'adminpay:accept:old1');self.assertEqual(self.query('SELECT status FROM orders'),[('delivered',)]);self.assertEqual(self.bot.PENDING_ADMIN_DELIVERY,{})
  self.bot.action(self.api,self.admin,'adminpay:accept:old1');self.assertEqual(self.query('SELECT count(*) FROM orders'),[(1,)])
 def test_receipt_event_idempotency(self):
  first=self.e.receipt_order(self.s,7,'pd_16','bank','20','75',101)
  second=self.e.receipt_order(self.s,7,'pd_16','bank','20','75',101)
  self.assertEqual(first,second);self.assertEqual(self.query('SELECT count(*) FROM orders'),[(1,)])
 def test_stars_and_gifts_fulfill_once(self):
  import telegram_payments as t
  with self.s.db() as c:
   t.setup(c)
   c.execute('INSERT INTO gift_requests VALUES (?,?,?,?,?,?,?)',('gift1',7,'pc_2','20','75',2,'review'))
   c.execute('INSERT INTO star_invoices VALUES (?,?,?,?,?,?,?,?,?)',('stars1',7,'pc_3',10,'20','75',2,None,'pending'))
  for _ in range(2):t.action(self.api,self.admin,'giftreview:accept:gift1')
  m={'chat':{'id':7},'successful_payment':{'invoice_payload':'stars1','telegram_payment_charge_id':'fake-charge','total_amount':10,'currency':'XTR'}}
  for _ in range(2):t.paid(self.api,m)
  self.assertEqual(self.query('SELECT count(*) FROM orders'),[(2,)])
  self.assertEqual(len(self.purchases()),2);self.assertEqual(self.bot.PENDING_ADMIN_DELIVERY,{})
 def test_crypto_invoice_idempotency(self):
  with self.s.db() as c:c.execute('INSERT INTO crypto_orders VALUES (?,?,?,?,?,?)',('crypto1',7,'pc_2','20','fake-invoice','pending'))
  with patch.object(self.s,'crypto_paid',return_value=True):
   for _ in range(2):self.bot.action(self.api,7,'checkorder:crypto1')
  self.assertEqual(self.query('SELECT count(*) FROM orders'),[(1,)]);self.assertEqual(len(self.purchases()),1)
 def test_ambiguous_binding_blocks_purchase(self):
  with self.s.db() as c:
   c.execute('INSERT INTO admin_products(pid,name,description,price_sar,available,created_at,category_id,price_usd,stock) VALUES (?,?,?,?,?,?,?,?,?)',('pc_duplicate','Capcut Pro 1 Month FW','','37.50',1,'test','pandora_capcut','10',20))
   c.execute('INSERT INTO supplier_api(pid,service_id,enabled,provider) VALUES (?,?,?,?)',('pc_duplicate','other-product',1,'pandora'))
  oid=self.order();self.bot.action(self.api,self.admin,'payreview:accept:'+oid)
  self.assertFalse(self.purchases());self.assertEqual(self.bot.PENDING_ADMIN_DELIVERY,{})
 def test_actual_disabled_pd17_alias_matches_active_same_supplier_id(self):
  with self.s.db() as c:
   c.execute('INSERT INTO supplier_api(pid,service_id,enabled,provider) VALUES (?,?,?,?)',('pd_17','prd_3',0,'pandora'))
  oid=self.order('pd_17');self.bot.action(self.api,self.admin,'payreview:accept:'+oid)
  self.assertEqual(self.query('SELECT original_pid,supplier_pid FROM payment_execution'),[('pd_17','pc_3')])
  self.assertEqual(self.query('SELECT status,pid FROM orders'),[('delivered','pd_17')])
  self.assertEqual(self.purchases()[0][2]['product_id'],'prd_3')
  self.assertEqual(self.bot.PENDING_ADMIN_DELIVERY,{})
 def test_admin_link_button_and_binding_preserve_price_and_secret(self):
  import json
  self.bot.action(self.api,self.admin,'admin')
  self.assertIn('admin:pandoralink',str(self.api.calls))
  self.bot.action(self.api,self.admin,'admin:pandoralink')
  self.assertIn('plcat:capcut',str(self.api.calls))
  with self.s.db() as c:
   c.execute('INSERT INTO product_prices VALUES (?,?,?)',('pc_2','12.50','USD'))
   c.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)',(self.admin,'pandora_catalog',json.dumps({'pid':'pc_2','products':[{'id':'new-supplier-id','name':'Test','variants':[]}]})))
  self.bot.action(self.api,self.admin,'pandorap:0')
  self.assertEqual(self.query("SELECT service_id,enabled,api_key FROM supplier_api WHERE pid='pc_2'"),[('new-supplier-id',1,'')])
  self.assertEqual(str(self.s.amount('pc_2','USD')),'12.50')
  self.assertNotIn('TEST_ONLY',str(self.api.calls))
  self.assertFalse(self.purchases())
 def test_admin_link_is_admin_only(self):
  self.bot.action(self.api,7,'admin:pandoralink')
  self.assertFalse(self.api.calls)
 def test_sync_and_refresh_never_overwrite_manual_sale(self):
  import pandora_catalog_sync as sync
  item={'id':'prd_2','name':'Capcut Pro 1 Month FW','stock':10}
  pid='pc_prd_2'
  with patch.object(self.s,'_supplier_json_request',return_value={'items':[item]}),patch.object(sync,'_quote',return_value=__import__('decimal').Decimal('1')):
   sync.sync_capcut()
  with self.s.db() as c:c.execute('INSERT OR REPLACE INTO product_prices VALUES (?,?,?)',(pid,'15.25','USD'))
  with patch.object(self.s,'_supplier_json_request',return_value={'items':[item]}),patch.object(sync,'_quote',return_value=__import__('decimal').Decimal('2')):
   sync.sync_capcut()
  self.s.pandora_refresh_price(pid)
  self.assertEqual(str(self.s.amount(pid,'USD')),'15.25')
 def test_below_cost_product_cannot_be_ordered(self):
  with self.s.db() as c:
   c.execute('INSERT INTO pandora_pricing VALUES (?,?,?,?)',('pc_2','11','0','test'))
   c.execute('INSERT INTO wallets VALUES (?,?)',(7,'100'))
  self.assertFalse(self.s.can_order('pc_2'))
  self.e.EVENT.message_id=888
  self.bot.action(self.api,7,'paywallet:pc_2')
  self.assertEqual(self.query('SELECT balance_sar FROM wallets'),[('100',)])
  self.assertFalse(self.purchases())
 def test_price_edit_targets_customer_sku(self):
  import json
  self.bot.action(self.api,self.admin,'pricepick:pd_16')
  self.assertEqual(json.loads(self.query("SELECT value FROM admin_state WHERE action='price'")[0][0])[0],'pc_2')
if __name__=='__main__':unittest.main()
