"""Separate order-bound Telegram OTP bot. Mailbox access must be authorized."""
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
import email_codes

DB = Path(os.getenv('OTP_STATE_PATH', '/data/otp.sqlite3'))
ADMIN = int(os.getenv('OTP_ADMIN_ID', '8386371522'))
LOCK = threading.RLock()


def db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=10)
    c.execute('PRAGMA busy_timeout=10000')
    return c


def prepare():
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS tickets(
          oid TEXT PRIMARY KEY,cid INTEGER NOT NULL,email TEXT NOT NULL,
          token TEXT UNIQUE NOT NULL,expires INTEGER NOT NULL,quota INTEGER NOT NULL,
          used INTEGER NOT NULL DEFAULT 0,requested INTEGER NOT NULL DEFAULT 0,
          status TEXT NOT NULL DEFAULT 'idle',code TEXT NOT NULL DEFAULT '',
          message_key TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS consumed(message_key TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        ''')


def issue(data):
    oid = str(data['order_id'])
    cid = int(data['customer_id'])
    email = str(data['email']).strip().lower()
    expires = int(data['expires'])
    quota = int(data.get('limit', 2))
    if not oid or len(oid) > 100 or cid <= 0 or not email_codes.re.fullmatch(email_codes.EMAIL_RE, email):
        raise ValueError('invalid_order')
    if not 1 <= quota <= 10 or not time.time() < expires <= time.time()+366*86400:
        raise ValueError('invalid_limit_or_expiry')
    with LOCK, db() as c:
        c.execute('BEGIN IMMEDIATE')
        old = c.execute('SELECT cid,email,token FROM tickets WHERE oid=?', (oid,)).fetchone()
        if old:
            if old[:2] != (cid, email): raise ValueError('order_binding_conflict')
            # Reopening a purchase link never resets quota or extends its expiry.
            return old[2]
        token = secrets.token_urlsafe(24)
        c.execute('INSERT INTO tickets(oid,cid,email,token,expires,quota) VALUES (?,?,?,?,?,?)',
                  (oid,cid,email,token,expires,quota))
        return token


def telegram(method, **data):
    token = os.environ['OTP_BOT_TOKEN']
    raw = json.dumps(data).encode()
    with urlopen(Request('https://api.telegram.org/bot'+token+'/'+method, data=raw,
                         headers={'Content-Type':'application/json'}), timeout=40) as r:
        result = json.load(r)
    if not result.get('ok'): raise RuntimeError('telegram_request_failed')
    return result['result']


def send(cid, text, token=None):
    data = {'chat_id':cid, 'text':text}
    if token:
        data['reply_markup'] = {'inline_keyboard':[[{'text':'طلب كود الدخول | Request code','callback_data':'get:'+token}]]}
    return telegram('sendMessage', **data)


def ticket(cid, token):
    with db() as c:
        c.row_factory = sqlite3.Row
        return c.execute('SELECT * FROM tickets WHERE token=? AND cid=?', (token,cid)).fetchone()


def request_code(cid, token):
    with LOCK:
        row = ticket(cid, token)
        if not row or row['expires'] <= time.time():
            return send(cid, 'الرابط غير صالح أو لا يخص حسابك. افتحه من طلبك في فيكسا.\nInvalid or expired order link.')
        if row['used'] >= row['quota']:
            return send(cid, 'وصلت إلى الحد المسموح للأكواد لهذا الطلب. تواصل مع الإدارة.\nCode limit reached for this order.')
        if row['status'] in ('pending','ready','sending','uncertain'):
            return send(cid, 'طلبك قيد المتابعة، انتظر أو تواصل مع الإدارة.\nYour request is already being processed.')
        if not email_codes.mailbox_configs():
            return send(cid, 'استلام الأكواد التلقائي لم يُربط بالبريد بعد. تواصل مع الإدارة.\nAutomatic email retrieval is not configured yet.')
        with db() as c:
            c.execute("UPDATE tickets SET status='pending',requested=?,code='',message_key='' WHERE token=?",
                      (int(time.time()),token))
        return send(cid, 'الآن اطلب رمز الدخول من ChatGPT. سأرسله هنا عند وصوله.\nRequest your ChatGPT login code now; it will appear here when received.')


def handle(update):
    callback = update.get('callback_query')
    if callback:
        telegram('answerCallbackQuery', callback_query_id=callback['id'])
        cid = callback['from']['id']
        value = callback.get('data','')
        if value.startswith('get:'): request_code(cid,value[4:])
        return
    message = update.get('message',{})
    if message.get('chat',{}).get('type') != 'private': return
    cid = message['from']['id']
    text = message.get('text','')
    if text.startswith('/start '):
        token = text.split(maxsplit=1)[1].strip()
        row = ticket(cid,token)
        if not row or row['expires'] <= time.time():
            return send(cid,'افتح رابط الأكواد الخاص بطلبك من بوت فيكسا.\nOpen the code link from your VEXA order.')
        return send(cid, 'أهلًا بك في بوت أكواد فيكسا.\nالمتبقي لهذا الطلب: '+str(max(0,row['quota']-row['used']))+
                    '\nاضغط «طلب كود الدخول»، ثم اطلب الرمز من ChatGPT.\nWelcome. Press Request code, then request the code from ChatGPT.', token)
    if cid == ADMIN and text == '/status':
        try: ready = bool(email_codes.mailbox_configs())
        except Exception: ready = False
        return send(cid, 'ربط البريد: '+('جاهز' if ready else 'غير مكتمل')+'\nالحد الافتراضي: مرتان لكل طلب.')
    send(cid,'للحصول على رمز الدخول افتح الزر الخاص بطلبك في فيكسا.\nUse your VEXA order link to receive a code.')


def tick():
    with db() as c:
        c.row_factory = sqlite3.Row
        rows = c.execute("SELECT * FROM tickets WHERE status IN ('pending','ready') LIMIT 20").fetchall()
    for row in rows:
        if row['expires'] <= time.time() or row['requested'] < time.time()-180:
            with db() as c: c.execute("UPDATE tickets SET status='idle',code='' WHERE oid=?",(row['oid'],))
            send(row['cid'],'لم يصل كود جديد؛ لم تُحسب محاولة. يمكنك إعادة الطلب.\nNo new code arrived. Your allowance was not reduced.',row['token'])
            continue
        if row['status'] == 'pending':
            with db() as c: used = {r[0] for r in c.execute('SELECT message_key FROM consumed')}
            found = email_codes.fetch_code(row['email'],row['requested'],used)
            if not found: continue
            code,key = found
            with LOCK, db() as c:
                c.execute('BEGIN IMMEDIATE')
                current=c.execute('SELECT status,used,quota FROM tickets WHERE oid=?',(row['oid'],)).fetchone()
                if not current or current[0]!='pending' or current[1]>=current[2]:continue
                if c.execute('SELECT 1 FROM consumed WHERE message_key=?',(key,)).fetchone(): continue
                c.execute('INSERT INTO consumed VALUES (?)',(key,))
                c.execute("UPDATE tickets SET status='ready',code=?,message_key=? WHERE oid=?",(code,key,row['oid']))
        with LOCK, db() as c:
            c.execute('BEGIN IMMEDIATE')
            current=c.execute("SELECT code,used,quota FROM tickets WHERE oid=? AND status='ready'",(row['oid'],)).fetchone()
            if not current or current[1]>=current[2]:continue
            c.execute("UPDATE tickets SET status='sending',used=used+1 WHERE oid=?",(row['oid'],))
            code=current[0]
        # Reserve before sending: ambiguous network failures never resend an OTP.
        try:
            send(row['cid'],'كود الدخول | Login code: '+code+'\nاستخدمه فورًا. | Use it promptly.')
        except Exception:
            with db() as c: c.execute("UPDATE tickets SET status='uncertain',code='' WHERE oid=?",(row['oid'],))
            continue
        with db() as c: c.execute("UPDATE tickets SET status='idle',code='' WHERE oid=?",(row['oid'],))


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass

    def reply(self,status,data):
        raw=json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(raw)))
        self.end_headers();self.wfile.write(raw)

    def do_GET(self):
        if self.path != '/health': return self.reply(404,{'error':'not_found'})
        try: mail=bool(email_codes.mailbox_configs())
        except Exception: mail=False
        self.reply(200,{'status':'running','telegram_configured':bool(os.getenv('OTP_BOT_TOKEN')),'mailbox_configured':mail})

    def do_POST(self):
        if self.path != '/tickets': return self.reply(404,{'error':'not_found'})
        secret=os.getenv('OTP_BRIDGE_SECRET','')
        supplied=self.headers.get('Authorization','')
        if len(secret)<32 or not hmac.compare_digest(supplied,'Bearer '+secret):return self.reply(401,{'error':'unauthorized'})
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=4096:raise ValueError()
            token=issue(json.loads(self.rfile.read(size)))
            self.reply(200,{'token':token})
        except (ValueError,KeyError,TypeError): self.reply(400,{'error':'invalid_order'})


def worker():
    with db() as c:
        c.execute("UPDATE tickets SET status='uncertain',code='' WHERE status='sending'")
        row=c.execute("SELECT value FROM metadata WHERE key='offset'").fetchone()
        offset=int(row[0]) if row else 0
    while True:
        try:
            if not os.getenv('OTP_BOT_TOKEN'):
                time.sleep(5);continue
            updates=telegram('getUpdates',offset=offset,timeout=5,allowed_updates=['message','callback_query'])
            for update in updates:
                handle(update)
                offset=update['update_id']+1
                with db() as c:c.execute("INSERT OR REPLACE INTO metadata VALUES ('offset',?)",(str(offset),))
            tick()
        except Exception as exc:
            print('OTP worker error:',type(exc).__name__,flush=True)
            time.sleep(5)


if __name__=='__main__':
    prepare()
    threading.Thread(target=worker,daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0',int(os.getenv('PORT','8080'))),Handler).serve_forever()
