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
 body=(m.get('text') or m.get('caption') or '')[:4096]
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
 if c!=ADMIN:return False
 # Leaving this flow always cancels the pending recipient/message.
 STATE.pop(ADMIN,None)
 if not v.startswith('inbox:'):return False
 if v=='inbox:menu':menu(a)
 elif v=='inbox:list' or v.startswith('inbox:list:'):
  page=int(v.rsplit(':',1)[1]) if v.rsplit(':',1)[1].isdigit() else 0
  with connect() as db:items=db.execute('SELECT i.cid,i.id,i.username FROM inbox i JOIN (SELECT cid,MAX(id) AS latest FROM inbox GROUP BY cid) x ON i.id=x.latest ORDER BY i.id DESC LIMIT 21 OFFSET ?',(page*20,)).fetchall()
  more=len(items)>20;items=items[:20]
  rows=[[button(('@'+u if u else str(cid))+' • '+str(cid),'inbox:view:'+str(cid))] for cid,_,u in items]
  if page:rows.append([button('العملاء الأحدث','inbox:list:'+str(page-1))])
  if more:rows.append([button('العملاء الأقدم','inbox:list:'+str(page+1))])
  send(a,'📥 <b>آخر العملاء الذين أرسلوا رسائل</b>' if items else 'لا توجد رسائل جديدة مسجلة بعد.',rows+[[button('↩️ رجوع','inbox:menu')]])
 elif v.startswith('inbox:view:'):
  parts=v.split(':')
  if not parts[2].isdigit():return True
  target=int(parts[2]);page=int(parts[3]) if len(parts)>3 and parts[3].isdigit() else 0
  with connect() as db:items=db.execute('SELECT kind,body,message_id,created FROM inbox WHERE cid=? ORDER BY id DESC LIMIT 11 OFFSET ?',(target,page*10)).fetchall()
  more=len(items)>10;items=items[:10]
  rows=[[button('✉️ مراسلة العميل','inbox:reply:'+str(target))]]
  if page:rows.append([button('الرسائل الأحدث',f'inbox:view:{target}:{page-1}')])
  if more:rows.append([button('الرسائل الأقدم',f'inbox:view:{target}:{page+1}')])
  rows.append([button('↩️ الوارد','inbox:list')])
  send(a,'📥 رسائل العميل <code>'+str(target)+'</code>\nالصفحة '+str(page+1),rows)
  for kind,body,mid,created in reversed(items):
   label={'photo':'🖼 صورة','text':'💬 رسالة','document':'📎 ملف','video':'🎬 فيديو','voice':'🎤 صوت'}.get(kind,'📨 محتوى')
   send(a,label+' • '+time.strftime('%Y-%m-%d %H:%M',time.gmtime(created+10800))+' (الرياض)')
   if not a.call('copyMessage',chat_id=ADMIN,from_chat_id=target,message_id=mid):
    if body:
     for start in range(0,len(body),1000):send(a,html.escape(body[start:start+1000]))
    else:send(a,'تعذر عرض المرفق الأصلي.')
 elif v=='inbox:send':
  STATE[ADMIN]='target'
  send(a,'✉️ أرسل الآن ID العميل الرقمي. يجب أن يكون قد بدأ البوت مسبقًا.\nيمكنك أيضًا اختيار العميل من الوارد.',[[button('إلغاء','inbox:menu')]])
 elif v.startswith('inbox:reply:'):
  value=v.rsplit(':',1)[1]
  if not value.isdigit() or int(value)<=0 or int(value)==ADMIN:return True
  STATE[ADMIN]=('compose',int(value))
  send(a,'✉️ العميل: <code>'+value+'</code>\nأرسل الرسالة أو الصورة أو الملف الذي تريد إرساله له.',[[button('إلغاء','inbox:menu')]])
 return True
def handle(a,m):
 if m.get('chat',{}).get('id')!=ADMIN or ADMIN not in STATE:return False
 state=STATE[ADMIN];value=(m.get('text') or '').strip()
 if value=='/cancel':
  STATE.pop(ADMIN,None);menu(a);return True
 if value.startswith('/'):
  STATE.pop(ADMIN,None);return False
 if state=='target':
  target=None
  if value.isdigit():target=int(value)
  if not target or target==ADMIN:
   send(a,'أرسل ID عميل رقمي صحيح، أو اختر العميل من الوارد.',[[button('إلغاء','inbox:menu')]]);return True
  STATE[ADMIN]=('compose',target)
  send(a,'✅ العميل: <code>'+str(target)+'</code>\nأرسل الآن الرسالة التي تريد إيصالها له.',[[button('إلغاء','inbox:menu')]]);return True
 if isinstance(state,tuple) and state[0]=='compose':
  target=state[1]
  if not m.get('text') and not m.get('photo') and not m.get('document') and not m.get('video') and not m.get('voice'):
   send(a,'أرسل نصًا أو صورة أو ملفًا، أو /cancel.');return True
  result=a.call('copyMessage',chat_id=target,from_chat_id=ADMIN,message_id=m['message_id'])
  if result:
   STATE.pop(ADMIN,None);send(a,'✅ تم إرسال الرسالة إلى <code>'+str(target)+'</code>.',[[button('رسالة أخرى للعميل','inbox:reply:'+str(target))],[button('📥 الوارد','inbox:list')],[button('↩️ رجوع','inbox:menu')]])
  else:send(a,'❌ تعذر الإرسال. قد يكون العميل حظر البوت أو لم يبدأه. حاول مجددًا أو /cancel.')
  return True
 return False
