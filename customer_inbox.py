"""Private admin inbox. Store only new customer messages; no bot token or payment credentials."""
import sqlite3,time,html
from pathlib import Path
DB=Path('/data/customer_inbox.sqlite3')
ADMIN=8386371522
STATE={}
def connect():
 c=sqlite3.connect(str(DB),timeout=10)
 c.execute('CREATE TABLE IF NOT EXISTS inbox (id INTEGER PRIMARY KEY, cid INTEGER NOT NULL, username TEXT, kind TEXT NOT NULL, body TEXT, message_id INTEGER NOT NULL, created INTEGER NOT NULL)')
 c.execute('CREATE INDEX IF NOT EXISTS inbox_by_customer ON inbox(cid,id)')
 return c
def capture(m):
 c=m.get('chat',{}).get('id')
 if not c or c==ADMIN or m.get('chat',{}).get('type')!='private':return
 kind='photo' if m.get('photo') else 'text' if m.get('text') else 'document' if m.get('document') else 'video' if m.get('video') else 'voice' if m.get('voice') else 'other'
 body=(m.get('text') or m.get('caption') or '')[:3000]
 # Do not duplicate /start and other commands in the customer inbox.
 if kind=='text' and body.startswith('/'):return
 try:
  with connect() as db:
   db.execute('INSERT INTO inbox(cid,username,kind,body,message_id,created) VALUES(?,?,?,?,?,?)',(c,(m.get('from') or {}).get('username',''),kind,body,m['message_id'],int(time.time())))
 except Exception as e:print('Inbox save error',type(e).__name__,flush=True)
def button(t,v):return {'text':t,'callback_data':v}
def menu(a):
 a.call('sendMessage',chat_id=ADMIN,text='📨 <b>مراسلات العملاء</b>\nاختر المطلوب:',parse_mode='HTML',reply_markup={'inline_keyboard':[[button('📥 الوارد','inbox:list')],[button('✉️ إرسال رسالة خاصة','inbox:send')],[button('↩️ لوحة الإدارة','admin')]]})
def send(a,t,rows=None):
 d={'chat_id':ADMIN,'text':t,'parse_mode':'HTML'}
 if rows:d['reply_markup']={'inline_keyboard':rows}
 return a.call('sendMessage',**d)
def action(a,c,v):
 if c!=ADMIN or not v.startswith('inbox:'):return False
 if v=='inbox:menu':menu(a)
 elif v=='inbox:list':
  with connect() as db:items=db.execute('SELECT cid,MAX(id),MAX(username) FROM inbox GROUP BY cid ORDER BY MAX(id) DESC LIMIT 20').fetchall()
  rows=[[button(('@'+u if u else str(cid))+' • '+str(cid),'inbox:view:'+str(cid))] for cid,_,u in items]
  send(a,'📥 <b>آخر العملاء الذين أرسلوا رسائل</b>' if items else 'لا توجد رسائل جديدة مسجلة بعد.',rows+[[button('↩️ رجوع','inbox:menu')]])
 elif v.startswith('inbox:view:'):
  target=int(v.rsplit(':',1)[1])
  with connect() as db:items=db.execute('SELECT kind,body,message_id,created FROM inbox WHERE cid=? ORDER BY id DESC LIMIT 10',(target,)).fetchall()
  send(a,'📥 رسائل العميل <code>'+str(target)+'</code>\nأحدث 10 رسائل:',[[button('✉️ مراسلة العميل','inbox:reply:'+str(target))],[button('↩️ الوارد','inbox:list')]])
  for kind,body,mid,created in reversed(items):
   label={'photo':'🖼 صورة','text':'💬 رسالة','document':'📎 ملف','video':'🎬 فيديو','voice':'🎤 صوت'}.get(kind,'📨 محتوى')
   send(a,label+' • '+time.strftime('%Y-%m-%d %H:%M UTC',time.gmtime(created))+'\n'+html.escape(body[:500]))
   if kind!='text':a.call('copyMessage',chat_id=ADMIN,from_chat_id=target,message_id=mid)
 elif v=='inbox:send':
  STATE[ADMIN]='target'
  send(a,'✉️ أرسل الآن المعرّف الرقمي للعميل، أو اسم المستخدم @username إذا سبق له بدء البوت.\nيمكنك أيضًا اختيار العميل من الوارد.')
 elif v.startswith('inbox:reply:'):
  STATE[ADMIN]=('compose',int(v.rsplit(':',1)[1]))
  send(a,'✉️ أرسل الآن الرسالة أو الصورة التي تريد إرسالها لهذا العميل.\nللإلغاء: /cancel')
 return True
def handle(a,m):
 if m.get('chat',{}).get('id')!=ADMIN or ADMIN not in STATE:return False
 state=STATE[ADMIN];value=(m.get('text') or '').strip()
 if value=='/cancel':
  STATE.pop(ADMIN,None);menu(a);return True
 if state=='target':
  target=None
  if value.lstrip('-').isdigit():target=int(value)
  elif value.startswith('@'):
   with connect() as db:row=db.execute('SELECT cid FROM inbox WHERE lower(username)=lower(?) ORDER BY id DESC LIMIT 1',(value[1:],)).fetchone()
   if row:target=row[0]
  if not target or target==ADMIN:
   send(a,'⚠️ لم أجد العميل. استخدم المعرّف الرقمي أو اسم مستخدم سبق تسجيله في الوارد.');return True
  STATE[ADMIN]=('compose',target)
  send(a,'✅ العميل: <code>'+str(target)+'</code>\nأرسل الآن النص أو الصورة. للإلغاء: /cancel');return True
 if isinstance(state,tuple) and state[0]=='compose':
  target=state[1]
  if not m.get('text') and not m.get('photo') and not m.get('document') and not m.get('video') and not m.get('voice'):
   send(a,'أرسل نصًا أو صورة أو ملفًا، أو /cancel.');return True
  result=a.call('copyMessage',chat_id=target,from_chat_id=ADMIN,message_id=m['message_id'])
  if result:
   STATE.pop(ADMIN,None);send(a,'✅ تم إرسال الرسالة إلى <code>'+str(target)+'</code>.')
  else:send(a,'❌ تعذر الإرسال. قد يكون العميل حظر البوت أو لم يبدأه. حاول مجددًا أو /cancel.')
  return True
 return False
