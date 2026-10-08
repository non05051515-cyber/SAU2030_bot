"""Telegram Stars invoices and manually reviewed gift requests."""
import uuid,html
from decimal import Decimal,ROUND_CEILING
import storefront as s
import payment_execution as execution

def _notify_order(oid):
 try:
  import whatsapp_alerts
  whatsapp_alerts.notify(s,oid)
 except Exception as exc:
  print('WhatsApp order alert unavailable:',type(exc).__name__,flush=True)
def stars(usd):
 return max(1,int((Decimal(str(usd))*Decimal(320)/Decimal('3.50')).to_integral_value(rounding=ROUND_CEILING)))
def setup(db):
 db.execute('CREATE TABLE IF NOT EXISTS star_invoices (payload TEXT PRIMARY KEY,cid INTEGER NOT NULL,pid TEXT NOT NULL,stars INTEGER NOT NULL,usd TEXT NOT NULL,sar TEXT NOT NULL,qty INTEGER NOT NULL,charge_id TEXT UNIQUE,status TEXT NOT NULL)')
 db.execute('CREATE TABLE IF NOT EXISTS gift_requests (id TEXT PRIMARY KEY,cid INTEGER NOT NULL,pid TEXT NOT NULL,usd TEXT NOT NULL,sar TEXT NOT NULL,qty INTEGER NOT NULL,status TEXT NOT NULL)')
def action(api,cid,value):
 if value.startswith('paystars:'):
  pid=value.split(':',1)[1]
  if not s.can_order(pid):s.payments(api,cid,pid);return True
  sar,usd,_,_=s.checkout_totals(cid,pid)
  if usd<=0:s.pay_with_wallet(api,cid,pid);return True
  n=stars(usd);payload=uuid.uuid4().hex
  qty=s.product_options.selected(s,cid,pid)
  with s.db() as db:
   setup(db)
   db.execute('INSERT INTO star_invoices VALUES (?,?,?,?,?,?,?,?,?)',(payload,cid,pid,n,str(usd),str(sar),qty,None,'pending'))
  result=api.call('sendInvoice',chat_id=cid,title=s.name(pid,cid)[:32],description=('VEXA STORE — '+s.name(pid,cid))[:255],payload=payload,currency='XTR',prices=[{'label':'VEXA STORE', 'amount':n}])
  if not result:s.send(api,cid,'⚠️ تعذر فتح فاتورة النجوم. حاول لاحقًا.')
  return True
 if value.startswith('paygifts:'):
  pid=value.split(':',1)[1]
  if not s.can_order(pid):s.payments(api,cid,pid);return True
  sar,usd,_,_=s.checkout_totals(cid,pid)
  if usd<=0:s.pay_with_wallet(api,cid,pid);return True
  qty=s.product_options.selected(s,cid,pid)
  ref=uuid.uuid4().hex[:10].upper()
  with s.db() as db:
   setup(db)
   db.execute('INSERT INTO gift_requests VALUES (?,?,?,?,?,?,?)',(ref,cid,pid,str(usd),str(sar),qty,'awaiting_gift'))
  s.send(api,cid,'🎁 <b>الدفع بهدايا تيليجرام</b>\n\n'+s.summary(cid,pid)+'\n\nأرسل الهدية إلى حساب الدعم <b>@m7mmd2030_1</b> بعد الاتفاق على نوع الهدية وقيمتها معه.\nرقم الطلب: <code>'+ref+'</code>\n\n⚠️ الهدية لا تُعتبر مدفوعة تلقائيًا؛ سيُراجع وصولها قبل تنفيذ الطلب.',s.kb([[s.btn('🎁 أرسلت الهدية','giftclaim:'+ref)],[s.btn('↩️ رجوع','buy:'+pid)]]))
  return True
 if value.startswith('giftclaim:'):
  ref=value.split(':',1)[1]
  with s.db() as db:
   setup(db)
   row=db.execute('SELECT cid,pid,usd,sar,qty,status FROM gift_requests WHERE id=?',(ref,)).fetchone()
   if not row or row[0]!=cid:return True
   if row[5]!='awaiting_gift':s.send(api,cid,'تم تسجيل إشعار الهدية مسبقًا.');return True
   db.execute('UPDATE gift_requests SET status=? WHERE id=?',('review',ref))
  s.send(api,s.G['ADMIN_ID'],'🎁 <b>طلب هدية بانتظار التحقق</b>\nرقم: <code>'+ref+'</code>\nالعميل: <code>'+str(cid)+'</code>\nالمنتج: '+s.esc(s.name(row[1],cid))+'\nالكمية: '+str(row[4])+'\nالمبلغ المرجعي: '+s.esc(row[2])+' USD\n⚠️ تحقق من وصول الهدية بنفسك قبل القبول.',s.kb([[s.btn('✅ تأكيد وصول الهدية','giftreview:accept:'+ref),s.btn('❌ رفض','giftreview:reject:'+ref)]]))
  s.send(api,cid,'⏳ تم إرسال إشعار الهدية للإدارة للمراجعة. لا يعتبر الطلب مدفوعًا بعد.')
  return True
 if value.startswith('giftreview:') and cid==s.G['ADMIN_ID']:
  _,decision,ref=value.split(':',2)
  with s.db() as db:
   setup(db)
   row=db.execute('SELECT cid,pid,usd,sar,qty,status FROM gift_requests WHERE id=?',(ref,)).fetchone()
   if not row or row[5]!='review':s.send(api,cid,'تمت معالجة الطلب أو لم يعد متاحًا.');return True
   if decision not in ('accept','reject'):return True
   db.execute('UPDATE gift_requests SET status=? WHERE id=? AND status=?',('paid' if decision=='accept' else 'rejected',ref,'review'))
   if decision=='accept':
    oid=execution.create_order(s,db,'gift:'+ref,row[0],row[1],'telegram_gift','paid',row[2],row[3],row[4])
  if decision=='accept':
   _notify_order(oid)
   s.fulfill_paid_order(api,oid)
   s.send(api,row[0],'✅ تم التحقق من وصول الهدية. رقم طلبك: <code>'+oid+'</code>')
   s.send(api,cid,'✅ تم اعتماد الهدية. رقم الطلب: <code>'+oid+'</code>')
  else:
   s.send(api,row[0],'❌ لم يتم تأكيد وصول الهدية. تواصل مع الدعم واذكر رقم الطلب <code>'+ref+'</code>.')
   s.send(api,cid,'تم رفض إشعار الهدية.')
  return True
 return False
def precheckout(api,q):
 payload=q.get('invoice_payload','')
 with s.db() as db:
  setup(db)
  row=db.execute('SELECT cid,stars,status FROM star_invoices WHERE payload=?',(payload,)).fetchone()
 valid=bool(row and row[0]==q.get('from',{}).get('id') and row[1]==q.get('total_amount') and row[2]=='pending' and q.get('currency')=='XTR')
 api.call('answerPreCheckoutQuery',pre_checkout_query_id=q['id'],ok=valid,**({} if valid else {'error_message':'Invoice unavailable. Please create a new invoice.'}))
def paid(api,m):
 p=m.get('successful_payment')
 if not p:return False
 payload=p.get('invoice_payload','');charge=p.get('telegram_payment_charge_id','')
 if not charge:return True
 cid=m['chat']['id']
 with s.db() as db:
  setup(db)
  row=db.execute('SELECT cid,pid,stars,usd,sar,qty,status FROM star_invoices WHERE payload=?',(payload,)).fetchone()
  if not row or row[0]!=cid or row[2]!=p.get('total_amount') or p.get('currency')!='XTR':
   s.send(api,s.G['ADMIN_ID'],'⚠️ دفعة نجوم غير مطابقة؛ راجعها يدويًا. Charge: <code>'+html.escape(charge)+'</code>');return True
  if row[6]=='paid':return True
  changed=db.execute('UPDATE star_invoices SET charge_id=?,status=? WHERE payload=? AND status=?',(charge,'paid',payload,'pending')).rowcount
  if not changed:return True
  oid=execution.create_order(s,db,'stars:'+payload,cid,row[1],'telegram_stars','paid',row[3],row[4],row[5])
 _notify_order(oid)
 s.fulfill_paid_order(api,oid)
 s.send(api,cid,'✅ تم تأكيد دفع '+str(row[2])+' ⭐. رقم طلبك: <code>'+oid+'</code>.')
 s.send(api,s.G['ADMIN_ID'],'⭐ <b>طلب مدفوع بنجوم تيليجرام</b>\nرقم: <code>'+oid+'</code>\nالعميل: <code>'+str(cid)+'</code>\nالمنتج: '+s.esc(s.name(row[1],cid))+'\nالكمية: '+str(row[5])+'\nالنجوم: '+str(row[2])+'\nCharge: <code>'+html.escape(charge)+'</code>')
 return True

