"""Per-product login assistance requests with admin review; no automatic OTP forwarding."""
import email
import base64
from email.policy import default
from email.utils import getaddresses, parsedate_to_datetime
import html
import imaplib
import json
import os
import re
import threading
import time
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen

_LOCK=threading.RLock()
_WORKER_LOCK=threading.Lock()
SENDERS={'noreply@tm.openai.com','otp@tm1.openai.com'}
EMAIL_RE=r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"


def ensure(s):
    with s.db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS email_code_products(pid TEXT PRIMARY KEY,enabled INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS email_code_accounts(order_id TEXT PRIMARY KEY,email TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS email_code_buttons(order_id TEXT PRIMARY KEY,sent INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS email_code_requests(
          order_id TEXT PRIMARY KEY,cid INTEGER NOT NULL,email TEXT NOT NULL,status TEXT NOT NULL,
          requested INTEGER NOT NULL,code TEXT NOT NULL DEFAULT '',message_key TEXT NOT NULL DEFAULT '',
          admin_notified INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS email_code_used(message_key TEXT PRIMARY KEY,order_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS email_code_extra(order_id TEXT PRIMARY KEY,notified INTEGER NOT NULL DEFAULT 0);
        """)


def enabled(s,pid):
    with s.db() as c:
        row=c.execute('SELECT enabled FROM email_code_products WHERE pid=?',(pid,)).fetchone()
    return bool(row and row[0])


def bind_delivery(s,oid,text):
    ensure(s)
    addresses=set(x.lower() for x in re.findall(EMAIL_RE,text))
    if len(addresses)==1:
        with s.db() as c:
            c.execute('INSERT OR IGNORE INTO email_code_accounts VALUES (?,?)',(oid,addresses.pop()))


def account(s,oid):
    with s.db() as c:
        explicit=c.execute('SELECT email FROM email_code_accounts WHERE order_id=?',(oid,)).fetchone()
        if explicit:return explicit[0]
        local=c.execute('SELECT email FROM local_delivery_stock WHERE order_id=?',(oid,)).fetchone()
    return local[0].lower() if local else None


def eligible(s,cid,oid):
    ensure(s)
    with s.db() as c:
        row=c.execute('SELECT pid,status FROM orders WHERE id=? AND cid=?',(oid,cid)).fetchone()
        has_subscriptions=c.execute("SELECT 1 FROM sqlite_master WHERE name='customer_subscriptions'").fetchone()
        expired=c.execute('SELECT 1 FROM customer_subscriptions WHERE order_id=? AND expires<=?',(oid,int(time.time()))).fetchone() if has_subscriptions else None
    return bool(row and row[1]=='delivered' and enabled(s,row[0]) and not expired)


def button(s,cid,oid):
    return s.btn(s.tr(cid,'طلب كود الدخول','Request login code'),'emailcode:'+oid,
                 s.ui_icon('ui_email_code'),style='success')


def show_button(s,api,cid,oid):
    if not eligible(s,cid,oid):return
    with s.db() as c:
        if c.execute('SELECT 1 FROM email_code_buttons WHERE order_id=?',(oid,)).fetchone():return
    text=s.tr(cid,'إذا ظهرت لك شاشة تطلب رمز تسجيل الدخول، اضغط «طلب الكود» عندما تكون جاهزًا. أدخل الرمز فور استلامه لأن صلاحيته قد تكون قصيرة. طلبك يُراجع من الإدارة حفاظًا على أمان الحساب.',
              'When the login code screen appears, request help only when ready. Enter any code promptly as it may expire quickly. Support will review your request to protect the account.')
    if s.send(api,cid,text,s.kb([[button(s,cid,oid)]])):
        with s.db() as c:c.execute('INSERT OR IGNORE INTO email_code_buttons VALUES (?,?)',(oid,int(time.time())))


def mailbox_configs():
    # One shared inbox for the store; OAuth credentials take precedence.
    user=os.getenv('GMAIL_OTP_USER','').strip()
    oauth={key:os.getenv('GMAIL_OTP_'+key.upper(),'').strip()
           for key in ('client_id','client_secret','refresh_token')}
    if any(oauth.values()):
        if not user or not all(oauth.values()):raise ValueError('incomplete_gmail_oauth')
        return [dict(oauth,user=user)]
    raw=os.getenv('GMAIL_OTP_ACCOUNTS','')
    if raw:
        rows=json.loads(raw)
        if not isinstance(rows,list):raise ValueError('invalid_mailbox_configuration')
    else:
        user=os.getenv('GMAIL_OTP_USER','').strip()
        secret=os.getenv('GMAIL_OTP_APP_PASSWORD','').replace(' ','')
        rows=[{'user':user,'app_password':secret}] if user and secret else []
    return [r for r in rows if isinstance(r,dict) and r.get('user') and r.get('app_password')]


def google_json(url,token=None,form=None):
    headers={'Authorization':'Bearer '+token} if token else {}
    data=urlencode(form).encode() if form is not None else None
    if data is not None:headers['Content-Type']='application/x-www-form-urlencoded'
    with urlopen(Request(url,data=data,headers=headers),timeout=15) as response:
        return json.load(response)


def fetch_oauth_code(cfg,target,requested,used):
    """Use Gmail API GET operations only, with gmail.readonly authorization."""
    token_data=google_json('https://oauth2.googleapis.com/token',form={
        'client_id':cfg['client_id'],'client_secret':cfg['client_secret'],
        'refresh_token':cfg['refresh_token'],'grant_type':'refresh_token'})
    token=token_data['access_token']
    scopes=token_data.get('scope','').split()
    if scopes and scopes!=['https://www.googleapis.com/auth/gmail.readonly']:
        raise ValueError('gmail_readonly_scope_required')
    root='https://gmail.googleapis.com/gmail/v1/users/me'
    profile=google_json(root+'/profile',token)
    if profile.get('emailAddress','').lower()!=cfg['user'].lower():
        raise ValueError('gmail_mailbox_mismatch')
    query='after:'+str(int(requested-120))+' {'+' '.join('from:'+x for x in sorted(SENDERS))+'}'
    messages=google_json(root+'/messages?'+urlencode({'q':query,'maxResults':50}),token)
    for item in messages.get('messages',[]):
        mid=item['id'];key=cfg['user'].lower()+':gmail:'+mid
        if key in used:continue
        message=google_json(root+'/messages/'+quote(mid,safe='')+'?format=raw',token)
        # Gmail's receipt time provides an independent freshness bound.
        received=int(message.get('internalDate',0))/1000
        if received<max(requested-120,time.time()-300) or received>time.time()+60:continue
        raw=message.get('raw','')
        parsed=parse_code(base64.urlsafe_b64decode(raw+'='*(-len(raw)%4)),target,requested)
        if parsed:return parsed[0],key
    return None


def parse_code(raw,target,requested,now=None):
    """Accept fresh, recipient-bound login messages from authenticated senders."""
    now=time.time() if now is None else now
    msg=email.message_from_bytes(raw,policy=default)
    sender=email.utils.parseaddr(str(msg.get('From','')))[1].lower()
    if sender not in SENDERS:return None
    recipients={addr.lower() for _,addr in getaddresses(
        [str(v) for h in ('To','Delivered-To','X-Original-To','X-Forwarded-To') for v in msg.get_all(h,[])])}
    if target.lower() not in recipients:return None
    # Authentication must have been evaluated by Gmail for the OpenAI sender.
    auth=' '.join(str(v) for v in msg.get_all('Authentication-Results',[]) if str(v).lstrip().startswith('mx.google.com;'))
    if not re.search(r'dkim=pass\b[^;]*header\.(?:i|d)=[^;\s]*(?:tm1?\.)?openai\.com\b',auth,re.I):return None
    try:stamp=parsedate_to_datetime(str(msg.get('Date',''))).timestamp()
    except (TypeError,ValueError,OverflowError):return None
    if stamp<max(requested-120,now-300) or stamp>now+60:return None
    subject=str(msg.get('Subject',''))
    if not re.search(r'code|verification|verify|رمز|كود|تحقق',subject,re.I):return None
    parts=[]
    for part in msg.walk():
        if part.get_content_type() not in ('text/plain','text/html') or part.get_content_disposition()=='attachment':continue
        try:parts.append(str(part.get_content()))
        except (UnicodeError,LookupError):continue
    body=html.unescape(re.sub(r'<[^>]+>',' ', '\n'.join(parts)))
    matches=set(re.findall(r'(?<!\d)(\d{6})(?!\d)',subject+'\n'+body))
    if len(matches)!=1:return None
    return matches.pop(),stamp


def fetch_code(target,requested,used=()):
    for cfg in mailbox_configs():
        if cfg.get('refresh_token'):
            found=fetch_oauth_code(cfg,target,requested,used)
            if found:return found
            continue
        with imaplib.IMAP4_SSL('imap.gmail.com',993,timeout=15) as client:
            client.login(cfg['user'],str(cfg['app_password']).replace(' ',''))
            client.select('INBOX',readonly=True)
            since=time.strftime('%d-%b-%Y',time.gmtime(requested-86400))
            status,data=client.uid('search',None,'SINCE',since)
            if status!='OK' or not data or not data[0]:continue
            validity=client.response('UIDVALIDITY')[1]
            epoch=str(validity[0]) if validity else ''
            for uid in reversed(data[0].split()[-50:]):
                message_key=str(cfg['user']).lower()+':'+epoch+':'+uid.decode()
                if message_key in used:continue
                status,parts=client.uid('fetch',uid,'(BODY.PEEK[])')
                if status!='OK':continue
                raw=next((p[1] for p in parts if isinstance(p,tuple)),None)
                if not raw:continue
                parsed=parse_code(raw,target,requested)
                if parsed:
                    code,stamp=parsed
                    return code,message_key
    return None


def notify_admin(s,api,oid,extra=False):
    table='email_code_extra' if extra else 'email_code_requests'
    column='notified' if extra else 'admin_notified'
    with s.db() as c:
        row=c.execute('SELECT '+column+' FROM '+table+' WHERE order_id=?',(oid,)).fetchone()
    if not row or row[0]:return
    text=('طلب كود إضافي' if extra else 'طلب كود يحتاج متابعة')+' للطلب #'+s.esc(oid)
    if s.send(api,s.G['ADMIN_ID'],text,s.kb([[s.btn('إرسال كود للعميل','emailcodeadmin:'+oid)],
                                            [s.btn('تحديد بريد الحساب','emailcodemail:'+oid)]])):
        with s.db() as c:c.execute('UPDATE '+table+' SET '+column+'=1 WHERE order_id=?',(oid,))


def request(s,api,cid,oid):
    """Notify store administration only; never extract or forward an email OTP."""
    with _LOCK:
        if not eligible(s,cid,oid):
            return s.send(api,cid,s.tr(cid,
                'هذا الطلب غير متاح لطلب المساعدة في تسجيل الدخول.',
                'This order is not eligible for login assistance.'))
        with s.db() as c:
            previous=c.execute('SELECT status FROM email_code_requests WHERE order_id=?',(oid,)).fetchone()
            if previous and previous[0] in ('pending','ready','escalated'):
                return s.send(api,cid,s.tr(cid,
                    'طلبك قيد مراجعة الإدارة. يرجى الانتظار.',
                    'Your request is being reviewed by support.'))
            if previous:
                c.execute("UPDATE email_code_requests SET status='escalated',code='',requested=?,admin_notified=0 WHERE order_id=?",
                          (int(time.time()),oid))
            else:
                c.execute('INSERT INTO email_code_requests(order_id,cid,email,status,requested) VALUES (?,?,?,?,?)',
                          (oid,cid,account(s,oid) or '','escalated',int(time.time())))
        notify_admin(s,api,oid)
        return s.send(api,cid,s.tr(cid,
            'تم إرسال طلب المساعدة في تسجيل الدخول للإدارة. لا تشارك رموز التحقق مع أي شخص.',
            'Your login assistance request was sent to support. Do not share verification codes.'))


def tick(s,api):
    """Display eligible buttons and notify admin; do not read Gmail or send codes."""
    ensure(s)
    with s.db() as c:
        delivered=c.execute("""SELECT o.id,o.cid FROM orders o JOIN email_code_products p ON p.pid=o.pid
            WHERE o.status='delivered' AND p.enabled=1
            AND NOT EXISTS(SELECT 1 FROM email_code_buttons b WHERE b.order_id=o.id)
            ORDER BY o.rowid DESC LIMIT 20""").fetchall()
    for oid,cid in delivered:show_button(s,api,cid,oid)
    with s.db() as c:
        # Migrate unfinished requests to manual review, without retaining OTPs.
        c.execute("UPDATE email_code_requests SET status='escalated',code='',admin_notified=0 WHERE status IN ('pending','ready')")
        requests=[r[0] for r in c.execute("SELECT order_id FROM email_code_requests WHERE status='escalated' AND admin_notified=0 LIMIT 10")]
    for oid in requests:notify_admin(s,api,oid)


def deliver_ready(s,api,oid):
    with _LOCK:
        with s.db() as c:
            row=c.execute("SELECT cid,code FROM email_code_requests WHERE order_id=? AND status='ready'",(oid,)).fetchone()
        if not row:return
        cid,code=row
        if s.send(api,cid,s.tr(cid,'كود الدخول: ','Login code: ')+'<code>'+s.esc(code)+'</code>'):
            with s.db() as c:c.execute("UPDATE email_code_requests SET status='sent',code='' WHERE order_id=?",(oid,))


def manual_send(s,api,cid,oid,code,customer):
    with _LOCK:
        if s.send(api,customer,s.tr(customer,'كود الدخول من الإدارة: ','Login code from support: ')+'<code>'+code+'</code>'):
            with s.db() as c:
                c.execute("INSERT OR IGNORE INTO email_code_requests(order_id,cid,email,status,requested) VALUES (?,?,?,'sent',?)",
                          (oid,customer,account(s,oid) or '',int(time.time())))
                c.execute("UPDATE email_code_requests SET status='sent',code='' WHERE order_id=?",(oid,))
                c.execute('DELETE FROM admin_state WHERE cid=?',(cid,))
            s.send(api,cid,'تم إرسال الكود للعميل.')
        else:s.send(api,cid,'تعذر إرسال الكود؛ أعد المحاولة.')


def start_tick(s,api):
    if not _WORKER_LOCK.acquire(blocking=False):return
    def work():
        try:tick(s,api)
        except Exception as exc:print('Email code worker:',type(exc).__name__,flush=True)
        finally:_WORKER_LOCK.release()
    threading.Thread(target=work,name='email-code-worker',daemon=True).start()


def settings(s,api,cid,pid):
    if cid!=s.G['ADMIN_ID']:return
    ensure(s)
    rows=[[s.btn('تعطيل' if enabled(s,pid) else 'تفعيل','emailcodetoggle:'+pid)],
          [s.btn('أيقونة متحركة لزر طلب الكود','seticon:ui_email_code')],
          [s.btn('حالة ربط Gmail','emailcodestatus')],[s.btn('رجوع','infopick:'+pid)]]
    s.send(api,cid,'<b>'+s.esc(s.name(pid,cid))+'</b>\n\nالحالة: '+('مفعّل' if enabled(s,pid) else 'معطّل')+'\nتفعيل هذا المنتج فقط؛ باقي المنتجات لا تتغير. جميع المنتجات المحددة تستخدم بريد Gmail المشترك. طلبات المساعدة تُرسل للإدارة للمراجعة فقط. لا تُقرأ أكواد Gmail ولا تُرسل تلقائيًا للعملاء.',
           s.kb(rows))


def install(s,namespace):
    ensure(s)
    old_action=namespace['action'];old_receipt=namespace['handle_receipt']
    old_editor=s.admin_info_editor;old_wallet=s.wallet;old_orders=s.admin_orders
    s.UI_ICON_LABELS['ui_email_code']='أيقونة زر طلب كود الدخول'
    def editor(api,cid,pid):
        old_editor(api,cid,pid)
        if cid==s.G['ADMIN_ID']:
            s.send(api,cid,'إعداد كود الدخول:',s.kb([[s.btn('كود الدخول عبر Gmail','emailcodeproduct:'+pid)]]))
    def wallet(api,cid):
        old_wallet(api,cid);ensure(s)
        with s.db() as c:
            rows=c.execute("""SELECT o.id,o.pid FROM orders o JOIN email_code_products p ON p.pid=o.pid
              WHERE o.cid=? AND o.status='delivered' AND p.enabled=1 ORDER BY o.rowid DESC LIMIT 10""",(cid,)).fetchall()
        if rows:
            s.send(api,cid,s.tr(cid,'طلب كود لحساباتك:','Request an account code:'),
                   s.kb([[button(s,cid,oid)] for oid,pid in rows]))
    def orders(api,cid):
        old_orders(api,cid)
        if cid!=s.G['ADMIN_ID']:return
        ensure(s)
        with s.db() as c:
            rows=c.execute("""SELECT o.id FROM orders o JOIN email_code_products p ON p.pid=o.pid
                WHERE o.status='delivered' AND p.enabled=1 ORDER BY o.rowid DESC LIMIT 10""").fetchall()
        if rows:s.send(api,cid,'ربط بريد الكود بالطلبات:',s.kb([[s.btn('#'+oid,'emailcodemail:'+oid)] for oid, in rows]))
    def action(api,cid,value):
        ensure(s)
        prefix,_,arg=value.partition(':')
        if prefix=='emailcode':return request(s,api,cid,arg)
        if prefix.startswith('emailcode'):
            if cid!=s.G['ADMIN_ID']:return
            if prefix=='emailcodeproduct':return settings(s,api,cid,arg)
            if prefix=='emailcodetoggle':
                with s.db() as c:c.execute('INSERT OR REPLACE INTO email_code_products VALUES (?,?)',(arg,not enabled(s,arg)))
                return settings(s,api,cid,arg)
            if prefix=='emailcodestatus':
                try:ready=bool(mailbox_configs())
                except Exception:ready=False
                return s.send(api,cid,'خدمة Gmail مستقلة؛ طلبات العملاء تصل للإدارة فقط ولا تُقرأ أو تُرسل الأكواد تلقائيًا.')
            if prefix in ('emailcodemail','emailcodeadmin'):
                with s.db() as c:
                    order=c.execute("SELECT cid FROM orders WHERE id=? AND status='delivered'",(arg,)).fetchone()
                    if not order:return s.send(api,cid,'الطلب غير مسلّم أو غير موجود.')
                    c.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)',(cid,'otp_mail' if prefix=='emailcodemail' else 'otp_manual',arg))
                return s.send(api,cid,'أرسل بريد الحساب الذي تسلّمه هذا العميل.' if prefix=='emailcodemail' else 'أرسل كود الدخول للعميل (6 أرقام).',
                              s.kb([[s.btn('إلغاء','emailcodecancel')]]))
            if prefix=='emailcodecancel':
                with s.db() as c:c.execute("DELETE FROM admin_state WHERE cid=? AND action IN ('otp_mail','otp_manual')",(cid,))
                return s.admin_orders(api,cid)
            return
        return old_action(api,cid,value)
    def receipt(api,message):
        cid=message.get('chat',{}).get('id');ensure(s)
        if cid==s.G['ADMIN_ID']:
            with s.db() as c:state=c.execute("SELECT action,value FROM admin_state WHERE cid=? AND action IN ('otp_mail','otp_manual')",(cid,)).fetchone()
            if state:
                raw=(message.get('text') or '').strip()
                if raw.startswith('/'):
                    with s.db() as c:c.execute('DELETE FROM admin_state WHERE cid=?',(cid,))
                    return old_receipt(api,message)
                kind,oid=state
                if kind=='otp_mail':
                    if not re.fullmatch(EMAIL_RE,raw):
                        s.send(api,cid,'أرسل بريدًا صحيحًا.');return True
                    with s.db() as c:
                        c.execute('INSERT OR REPLACE INTO email_code_accounts VALUES (?,?)',(oid,raw.lower()))
                        c.execute('DELETE FROM admin_state WHERE cid=?',(cid,))
                    s.send(api,cid,'تم ربط بريد الحساب بهذا الطلب.');return True
                if not re.fullmatch(r'\d{6}',raw):
                    s.send(api,cid,'أرسل كودًا من 6 أرقام.');return True
                with s.db() as c:
                    row=c.execute("SELECT cid FROM orders WHERE id=? AND status='delivered'",(oid,)).fetchone()
                if not row:return True
                manual_send(s,api,cid,oid,raw,row[0])
                return True
        return old_receipt(api,message)
    s.admin_info_editor=editor;s.wallet=wallet;s.admin_orders=orders
    s.bind_code_delivery=lambda oid,text:bind_delivery(s,oid,text)
    s.show_code_button=lambda api,cid,oid:show_button(s,api,cid,oid)
    namespace['action']=namespace['handle_action']=action
    namespace['handle_receipt']=receipt
    namespace['tick_email_codes']=lambda api:start_tick(s,api)
    import otp_bridge
    otp_bridge.install(s)
