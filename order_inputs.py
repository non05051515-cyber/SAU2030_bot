"""Per-product customer information collected after an order is submitted."""
import re
import time
import threading

_PROMPT_LOCK=threading.RLock()


def prepare(s):
    with s.db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS order_input_config(pid TEXT PRIMARY KEY, mode TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS order_customer_inputs(
          order_id TEXT PRIMARY KEY,cid INTEGER NOT NULL,mode TEXT NOT NULL,
          step TEXT NOT NULL,email TEXT NOT NULL DEFAULT '',password TEXT NOT NULL DEFAULT '',
          prompted INTEGER NOT NULL DEFAULT 0);
        CREATE TRIGGER IF NOT EXISTS customer_input_snapshot AFTER INSERT ON orders
        WHEN NEW.status IN ('review','paid') BEGIN
          INSERT OR IGNORE INTO order_customer_inputs(order_id,cid,mode,step)
            SELECT NEW.id,NEW.cid,mode,CASE WHEN mode='password' THEN 'password' ELSE 'email' END
            FROM order_input_config WHERE pid=NEW.pid AND mode IN ('email','password','both');
        END;
        """)


def ensure(s):
    with s.db() as c:
        ready=c.execute("SELECT 1 FROM sqlite_master WHERE name='customer_input_snapshot' AND type='trigger'").fetchone()
    if not ready:prepare(s)


def pending(s,cid=None,oid=None):
    ensure(s)
    with s.db() as c:
        return c.execute("""SELECT x.order_id,x.cid,x.mode,x.step,x.email,x.password,x.prompted,o.pid
            FROM order_customer_inputs x JOIN orders o ON o.id=x.order_id
            WHERE x.step!='done' AND o.status IN ('review','paid')
              AND (? IS NULL OR x.cid=?) AND (? IS NULL OR x.order_id=?)
            ORDER BY o.rowid LIMIT 1""",(cid,cid,oid,oid)).fetchone()


def prompt(s,api,row,force=False):
    with _PROMPT_LOCK:
        if not row:return
        return _prompt(s,api,pending(s,oid=row[0]),force)


def _prompt(s,api,row,force=False):
    if not row:return
    oid,cid,mode,step,email,password,prompted,pid=row
    # One active order at a time prevents associating a reply with the wrong order.
    first=pending(s,cid=cid)
    if not first or first[0]!=oid:return
    if prompted and not force:return
    text=s.tr(cid,'<b>بيانات تنفيذ الطلب #','<b>Information for order #')+s.esc(oid)+'</b>\n'+s.esc(s.name(pid,cid))
    text+='\n\n'+(s.tr(cid,'أرسل الإيميل المطلوب للتفعيل.','Send the email to activate.')
                   if step=='email' else s.tr(cid,'أرسل كلمة المرور المطلوبة لتنفيذ هذا الطلب.','Send the password required for this order.'))
    text+='\n'+s.tr(cid,'سيبقى تنفيذ الطلب بانتظار تأكيد الدفع.','Fulfillment remains subject to payment approval.')
    if s.send(api,cid,text,s.kb([[s.btn(s.tr(cid,'لاحقًا','Later'),'inputlater:'+oid)]])):
        with s.db() as c:c.execute('UPDATE order_customer_inputs SET prompted=? WHERE order_id=? AND step=?',(int(time.time()),oid,step))


def waiting(s,api,oid):
    row=pending(s,oid=oid)
    if row:
        prompt(s,api,row)
        return True
    return False


def inspect(s,api,cid,oid):
    if cid!=s.G['ADMIN_ID']:return
    ensure(s)
    with s.db() as c:
        row=c.execute('SELECT mode,step,email,password FROM order_customer_inputs WHERE order_id=?',(oid,)).fetchone()
    if not row:return s.send(api,cid,'لا توجد بيانات مطلوبة لهذا الطلب.')
    mode,step,email,password=row
    text='<b>بيانات العميل للطلب #'+s.esc(oid)+'</b>\n'
    text+='الحالة: '+('مكتملة' if step=='done' else 'بانتظار العميل')+'\n'
    if email:text+='الإيميل: <code>'+s.esc(email)+'</code>\n'
    if password:text+='كلمة المرور: <code>'+s.esc(password)+'</code>\n'
    return s.send(api,cid,text,s.kb([[s.btn('الطلبات','admin:orders')]]))


def config_menu(s,api,cid,pid=None,category=None):
    if cid!=s.G['ADMIN_ID']:return
    ensure(s)
    if pid:
        with s.db() as c:
            row=c.execute('SELECT mode FROM order_input_config WHERE pid=?',(pid,)).fetchone()
        mode=row[0] if row else 'off'
        labels={'off':'معطّل','email':'الإيميل فقط','password':'كلمة المرور فقط','both':'الإيميل وكلمة المرور'}
        rows=[[s.btn(('✓ ' if mode==key else '')+label,'inputcfg:'+key+':'+pid)] for key,label in labels.items()]
        return s.send(api,cid,'<b>'+s.esc(s.name(pid,cid))+'</b>\n\nاختر البيانات المطلوبة بعد إرسال الإيصال أو إتمام الدفع. يطلب البوت كل معلومة في رسالة مستقلة.',
                      s.kb(rows+[[s.btn('منتج آخر','inputadmin')],[s.btn('لوحة الإدارة','admin')]]))
    with s.db() as c:
        cats=c.execute('SELECT cid,name FROM admin_categories').fetchall()
        direct=c.execute("SELECT pid,name FROM admin_products WHERE COALESCE(category_id,'')=''").fetchall()
    if category:
        rows=[[s.btn(s.name(p,cid),'inputproduct:'+p)] for p in s.admin_category_product_ids(category)]
    else:
        labels=dict(cats)
        rows=[[s.btn(labels.get(p) or s.name(p,cid),'inputcat:'+p)] for p in dict.fromkeys(list(s.G['PRODUCTS'])+list(labels))]
        rows += [[s.btn(n,'inputproduct:'+p)] for p,n in direct]
    return s.send(api,cid,'<b>طلب بيانات العميل بعد التحويل</b>\nاختر القسم ثم المنتج:',
                  s.kb(rows+[[s.btn('لوحة الإدارة','admin')]]))


def handle(s,api,message):
    cid=message.get('chat',{}).get('id')
    if not cid or message.get('chat',{}).get('type','private')!='private':return False
    row=pending(s,cid=cid)
    if not row:return False
    raw=message.get('text')
    if raw is not None and (raw.startswith('/') or raw in s.G.get('MENU',{})):return False
    oid,_,mode,step,email,password,_,pid=row
    if not raw or len(raw)>500 or '\n' in raw:
        s.send(api,cid,s.tr(cid,'أرسل المعلومة المطلوبة فقط في رسالة نصية واحدة.','Send only the requested information in one text message.'))
        return True
    if step=='email':
        raw=raw.strip()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+",raw):
            s.send(api,cid,s.tr(cid,'أرسل إيميلًا صحيحًا مثل user@example.com.','Send a valid email, e.g. user@example.com.'));return True
        new_step='password' if mode=='both' else 'done'
        column='email'
    else:
        if not raw.strip():
            s.send(api,cid,s.tr(cid,'أرسل كلمة المرور.','Send the password.'));return True
        new_step='done';column='password'
    with s.db() as c:
        changed=c.execute('UPDATE order_customer_inputs SET '+column+'=?,step=?,prompted=0 WHERE order_id=? AND cid=? AND step=?',
                          (raw,new_step,oid,cid,step)).rowcount
    if not changed:return True
    if new_step!='done':
        prompt(s,api,pending(s,cid=cid));return True
    s.send(api,cid,s.tr(cid,'جاري تنفيذ طلبك، وسيتم مشاركة البيانات معك فور الانتهاء.',
                      'Your order is being processed. The details will be shared with you once completed.'))
    s.send(api,s.G['ADMIN_ID'],'اكتملت بيانات العميل للطلب #'+s.esc(oid),
           s.kb([[s.btn('عرض بيانات العميل','inputview:'+oid)],[s.btn('الطلبات','admin:orders')]]))
    with s.db() as c:status=c.execute('SELECT status FROM orders WHERE id=?',(oid,)).fetchone()
    if status and status[0]=='paid':
        if not s.fulfill_paid_order(api,oid):
            s.send(api,s.G['ADMIN_ID'],'الطلب #'+s.esc(oid)+' مدفوع وبياناته مكتملة؛ جاهز للتنفيذ اليدوي.',
                   s.kb([[s.btn('تسليم الطلب','orderdeliver:'+oid)]]))
    prompt(s,api,pending(s,cid=cid))
    return True


def tick(s,api):
    ensure(s)
    with s.db() as c:
        ids=[r[0] for r in c.execute("""SELECT DISTINCT x.cid FROM order_customer_inputs x
          JOIN orders o ON o.id=x.order_id WHERE x.step!='done' AND x.prompted=0 AND o.status IN ('review','paid') LIMIT 20""")]
    for cid in ids:prompt(s,api,pending(s,cid=cid))


def install(s,namespace):
    prepare(s)
    old_action=namespace['action'];old_receipt=namespace['handle_receipt']
    old_editor=s.admin_info_editor;old_admin=s.admin_panel;old_orders=s.admin_orders
    def editor(api,cid,pid):
        old_editor(api,cid,pid)
        if cid==s.G['ADMIN_ID']:
            s.send(api,cid,'بيانات تنفيذ المنتج:',s.kb([[s.btn('طلب بيانات العميل بعد التحويل','inputproduct:'+pid)]]))
    def admin(api,cid):
        old_admin(api,cid)
        if cid==s.G['ADMIN_ID']:
            s.send(api,cid,'إعداد طلب بيانات العميل:',s.kb([[s.btn('طلب بيانات العميل بعد التحويل','inputadmin')]]))
    def orders(api,cid):
        old_orders(api,cid)
        if cid!=s.G['ADMIN_ID']:return
        ensure(s)
        with s.db() as c:
            rows=c.execute('SELECT x.order_id,x.step FROM order_customer_inputs x JOIN orders o ON o.id=x.order_id ORDER BY o.rowid DESC LIMIT 20').fetchall()
        if rows:
            s.send(api,cid,'<b>بيانات تنفيذ الطلبات</b>',s.kb([[s.btn('#'+oid+(' — مكتملة' if step=='done' else ' — بانتظار العميل'),'inputview:'+oid)] for oid,step in rows]))
    def action(api,cid,value):
        ensure(s)
        prefix,_,arg=value.partition(':')
        if prefix in ('inputadmin','inputcat','inputproduct','inputcfg','inputview'):
            if cid!=s.G['ADMIN_ID']:return
            if prefix=='inputadmin':return config_menu(s,api,cid)
            if prefix=='inputcat':return config_menu(s,api,cid,category=arg)
            if prefix=='inputproduct':return config_menu(s,api,cid,pid=arg)
            if prefix=='inputview':return inspect(s,api,cid,arg)
            mode,_,pid=arg.partition(':')
            if mode not in ('off','email','password','both'):return
            with s.db() as c:
                c.execute('INSERT OR REPLACE INTO order_input_config VALUES (?,?)',(pid,mode))
            return config_menu(s,api,cid,pid=pid)
        if prefix in ('inputlater','inputresume'):
            row=pending(s,cid=cid,oid=arg)
            if not row:return
            if prefix=='inputresume':return prompt(s,api,row,True)
            return s.send(api,cid,s.tr(cid,'طلبك محفوظ. أكمل البيانات عندما تكون جاهزًا.',
                                      'Your order is saved. Complete the information when ready.'),
                          s.kb([[s.btn(s.tr(cid,'إكمال البيانات','Complete information'),'inputresume:'+arg)]]))
        if prefix in ('orderdeliver','localretry','supplierretry') and cid==s.G['ADMIN_ID'] and waiting(s,api,arg):
            return s.send(api,cid,'هذا الطلب بانتظار بيانات العميل قبل التنفيذ.')
        return old_action(api,cid,value)
    def receipt(api,message):
        if handle(s,api,message):return True
        result=old_receipt(api,message)
        prompt(s,api,pending(s,cid=message.get('chat',{}).get('id')))
        return result
    s.admin_info_editor=editor;s.admin_panel=admin;s.admin_orders=orders
    s.waiting_customer_input=lambda api,oid:waiting(s,api,oid)
    namespace['action']=namespace['handle_action']=action
    namespace['handle_receipt']=receipt
    namespace['handle_order_input']=lambda api,msg:handle(s,api,msg)
    namespace['tick_order_inputs']=lambda api:tick(s,api)
