"""Subscription dates follow durable order delivery, never payment alone."""
import math
import time
from datetime import datetime, timezone, timedelta


def prepare(s):
    with s.db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS subscription_settings(
          pid TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0,
          days INTEGER NOT NULL DEFAULT 29, remind_days INTEGER NOT NULL DEFAULT 3);
        CREATE TABLE IF NOT EXISTS subscription_intents(
          cid INTEGER PRIMARY KEY, pid TEXT NOT NULL, previous_order TEXT NOT NULL, expires INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS subscription_order_terms(
          order_id TEXT PRIMARY KEY, days INTEGER NOT NULL, remind_days INTEGER NOT NULL, previous_order TEXT);
        CREATE TABLE IF NOT EXISTS customer_subscriptions(
          order_id TEXT PRIMARY KEY, cid INTEGER NOT NULL, pid TEXT NOT NULL,
          started INTEGER NOT NULL, expires INTEGER NOT NULL, days INTEGER NOT NULL, remind_days INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS subscription_customer ON customer_subscriptions(cid,expires);
        CREATE TABLE IF NOT EXISTS subscription_reminders(
          order_id TEXT PRIMARY KEY, sent INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS subscription_reminder_retry(
          order_id TEXT PRIMARY KEY, retry_at INTEGER NOT NULL);
        CREATE TRIGGER IF NOT EXISTS subscription_order_snapshot AFTER INSERT ON orders BEGIN
          INSERT OR IGNORE INTO subscription_order_terms
            SELECT NEW.id, cfg.days, cfg.remind_days,
              (SELECT previous_order FROM subscription_intents WHERE cid=NEW.cid AND pid=NEW.pid
               AND expires>CAST(strftime('%s','now') AS INTEGER))
            FROM subscription_settings cfg WHERE cfg.pid=NEW.pid AND cfg.enabled=1;
          DELETE FROM subscription_intents WHERE cid=NEW.cid AND pid=NEW.pid;
        END;
        CREATE TRIGGER IF NOT EXISTS subscription_delivery AFTER UPDATE OF status ON orders
        WHEN NEW.status='delivered' AND OLD.status!='delivered' BEGIN
          INSERT OR IGNORE INTO customer_subscriptions
            SELECT NEW.id,NEW.cid,NEW.pid,CAST(strftime('%s','now') AS INTEGER),
              MAX(CAST(strftime('%s','now') AS INTEGER),
                COALESCE((SELECT expires FROM customer_subscriptions
                  WHERE order_id=t.previous_order AND cid=NEW.cid AND pid=NEW.pid),0))
                +t.days*86400,t.days,t.remind_days
            FROM subscription_order_terms t WHERE t.order_id=NEW.id;
        END;
        CREATE TRIGGER IF NOT EXISTS subscription_delivered_insert AFTER INSERT ON orders
        WHEN NEW.status='delivered' BEGIN
          INSERT OR IGNORE INTO customer_subscriptions
            SELECT NEW.id,NEW.cid,NEW.pid,CAST(strftime('%s','now') AS INTEGER),
              CAST(strftime('%s','now') AS INTEGER)+cfg.days*86400,cfg.days,cfg.remind_days
            FROM subscription_settings cfg WHERE cfg.pid=NEW.pid AND cfg.enabled=1;
        END;
        """)


def config(s, pid):
    with s.db() as c:
        return c.execute('SELECT enabled,days,remind_days FROM subscription_settings WHERE pid=?',
                         (pid,)).fetchone() or (0,29,3)


def settings(s, api, cid, pid=None, category=None):
    if cid != s.G['ADMIN_ID']:
        return
    with s.db() as c:
        c.execute("DELETE FROM admin_state WHERE cid=? AND action LIKE 'subscription_%'",(cid,))
    if pid:
        enabled,days,lead=config(s,pid)
        rows=[[s.btn('تعطيل' if enabled else 'تفعيل', 'subcfg:toggle:'+pid)],
              [s.btn('مدة الاشتراك: '+str(days)+' يوم','subcfg:days:'+pid)],
              [s.btn('تنبيه قبل: '+str(lead)+' أيام','subcfg:lead:'+pid)],
              [s.btn('رجوع','subadmin')]]
        return s.send(api,cid,'<b>'+s.esc(s.name(pid,cid))+'</b>\n\nإدارة الاشتراك: '+
                      ('مفعّلة' if enabled else 'معطّلة')+
                      '\nحدد مدة الباقة ثم فعّلها. يبدأ الحساب بعد التسليم للطلبات الجديدة فقط.',
                      s.kb(rows))
    with s.db() as c:
        cats=c.execute('SELECT cid,name FROM admin_categories').fetchall()
        direct=c.execute("SELECT pid,name FROM admin_products WHERE COALESCE(category_id,'')=''").fetchall()
    if category:
        rows=[[s.btn(s.name(p,cid),'subproduct:'+p)] for p in s.admin_category_product_ids(category)]
    else:
        labels=dict(cats)
        rows=[[s.btn(labels.get(p) or s.name(p,cid),'subcat:'+p)]
              for p in dict.fromkeys(list(s.G['PRODUCTS'])+list(labels))]
        rows += [[s.btn(n,'subproduct:'+p)] for p,n in direct]
    s.send(api,cid,'<b>إعدادات الاشتراكات والتجديد</b>\nاختر القسم ثم المنتج:',
           s.kb(rows+[[s.btn('لوحة الإدارة','admin')]]))


def owned(s, cid, oid):
    with s.db() as c:
        return c.execute('SELECT order_id,pid,started,expires,days,remind_days FROM customer_subscriptions WHERE order_id=? AND cid=?',
                         (oid,cid)).fetchone()


def date_text(timestamp):
    return datetime.fromtimestamp(timestamp,timezone(timedelta(hours=3))).strftime('%Y-%m-%d')


def page(s, api, cid, page_number=0):
    page_number=max(0,int(page_number))
    with s.db() as c:
        rows=c.execute("""SELECT order_id,pid,started,expires,days,remind_days FROM customer_subscriptions x
          WHERE cid=? AND NOT EXISTS(
            SELECT 1 FROM subscription_order_terms t JOIN orders o ON o.id=t.order_id
            WHERE t.previous_order=x.order_id AND o.status='delivered')
          ORDER BY expires DESC LIMIT 7 OFFSET ?""",(cid,page_number*6)).fetchall()
    text=s.tr(cid,'<b>اشتراكاتي</b>','<b>My subscriptions</b>')
    buttons=[]
    for oid,pid,started,expires,days,lead in rows[:6]:
        remaining=max(0,math.ceil((expires-time.time())/86400))
        text+='\n\n<b>'+s.esc(s.name(pid,cid))+'</b>\n'+s.tr(cid,'الانتهاء: ','Expires: ')+date_text(expires)
        text+='\n'+(s.tr(cid,'المتبقي: ','Remaining: ')+str(remaining)+s.tr(cid,' يوم',' days')
                     if remaining else s.tr(cid,'انتهى الاشتراك','Expired'))
        buttons.append([s.btn(s.tr(cid,'جدّد: ','Renew: ')+s.name(pid,cid)[:35],'subrenew:'+oid,style='success')])
    if not rows:
        text+='\n\n'+s.tr(cid,'ستظهر هنا الاشتراكات المسلّمة للمنتجات المفعّلة في النظام.',
                           'Delivered subscriptions for enabled products will appear here.')
    nav=[]
    if page_number:nav.append(s.btn(s.tr(cid,'السابق','Previous'),'subs:'+str(page_number-1)))
    if len(rows)>6:nav.append(s.btn(s.tr(cid,'التالي','Next'),'subs:'+str(page_number+1)))
    if nav:buttons.append(nav)
    buttons.append([s.btn(s.tr(cid,'حساباتي','My account'),'wallet')])
    s.send(api,cid,text,s.kb(buttons))


def renew(s, namespace, api, cid, oid):
    row=owned(s,cid,oid)
    if not row:
        return s.send(api,cid,s.tr(cid,'هذا الاشتراك غير موجود في حسابك.','This subscription is not in your account.'))
    _,pid,_,_,days,_=row
    enabled,current_days,_=config(s,pid)
    with s.db() as c:
        linked=c.execute("""SELECT o.status FROM subscription_order_terms t JOIN orders o ON o.id=t.order_id
                            WHERE t.previous_order=? AND o.cid=? AND o.status NOT IN ('rejected','cancelled','failed')""",
                         (oid,cid)).fetchone()
    if linked:
        return s.send(api,cid,s.tr(cid,'يوجد طلب تجديد لهذا الاشتراك. راجع طلباتك أو تواصل مع الدعم.',
                                 'A renewal order already exists. Check your orders or contact support.'))
    if not enabled or current_days!=days:
        return s.send(api,cid,s.tr(cid,'تغيّرت إعدادات هذه الباقة. تواصل مع الدعم للتجديد بنفس المدة.',
                                 'This plan has changed. Contact support to renew the same duration.'))
    if not s.can_order(pid):
        return s.send(api,cid,s.tr(cid,'نفدت كمية هذه الباقة. يمكنك تفعيل تنبيه التوفر من صفحة المنتج.',
                                 'This plan is out of stock. Enable the restock alert on its product page.'),
                      s.kb([[s.btn(s.tr(cid,'المنتج وتنبيه التوفر','Product and restock alert'),'options:'+pid)]]))
    with s.db() as c:
        c.execute('INSERT OR REPLACE INTO subscription_intents VALUES (?,?,?,?)',(cid,pid,oid,int(time.time())+3600))
        c.execute('INSERT OR REPLACE INTO selected_quantities VALUES (?,?,?)',(cid,pid,1))
    s.send(api,cid,s.tr(cid,'تجديد بنفس الباقة والمدة. تُضاف المدة بعد التسليم إلى تاريخ الانتهاء الحالي إذا كان الاشتراك ساريًا.',
                       'Renew the same plan and duration. After delivery, the duration is added to the current expiry if still active.'))
    return namespace['action'](api,cid,'buy:'+pid)


def tick(s, api):
    now=int(time.time())
    with s.db() as c:
        rows=c.execute("""SELECT x.order_id,x.cid,x.pid,x.expires FROM customer_subscriptions x
            WHERE x.expires>? AND x.expires<=?+x.remind_days*86400
            AND NOT EXISTS(SELECT 1 FROM subscription_reminders r WHERE r.order_id=x.order_id)
            AND NOT EXISTS(SELECT 1 FROM subscription_reminder_retry r WHERE r.order_id=x.order_id AND r.retry_at>?)
            AND NOT EXISTS(SELECT 1 FROM subscription_order_terms t JOIN orders o ON o.id=t.order_id
              WHERE t.previous_order=x.order_id AND o.status='delivered')
            AND EXISTS(SELECT 1 FROM subscription_settings cfg WHERE cfg.pid=x.pid AND cfg.enabled=1)
            ORDER BY x.expires LIMIT 25""",(now,now,now)).fetchall()
    for oid,cid,pid,expires in rows:
        text=s.tr(cid,'اقترب انتهاء اشتراكك: ','Your subscription is ending soon: ')+s.esc(s.name(pid,cid))
        text+='\n'+s.tr(cid,'تاريخ الانتهاء: ','Expiry date: ')+date_text(expires)
        result=s.send(api,cid,text,s.kb([[s.btn(s.tr(cid,'جدّد اشتراكي','Renew my subscription'),
                                                               'subrenew:'+oid,style='success')]]))
        if result:
            with s.db() as c:
                c.execute('INSERT OR IGNORE INTO subscription_reminders VALUES (?,?)',(oid,now))
                c.execute('DELETE FROM subscription_reminder_retry WHERE order_id=?',(oid,))
        else:
            with s.db() as c:
                c.execute('INSERT OR REPLACE INTO subscription_reminder_retry VALUES (?,?)',(oid,now+3600))


def install(s, namespace):
    prepare(s)
    old_action=namespace['action']
    old_receipt=namespace['handle_receipt']
    old_editor=s.admin_info_editor
    old_admin=s.admin_panel
    old_wallet=s.wallet

    def editor(api,cid,pid):
        old_editor(api,cid,pid)
        if cid==s.G['ADMIN_ID']:
            s.send(api,cid,'إدارة مدة الاشتراك والتجديد:',s.kb([[s.btn('إعدادات الاشتراك','subproduct:'+pid)]]))
    def admin(api,cid):
        old_admin(api,cid)
        if cid==s.G['ADMIN_ID']:
            s.send(api,cid,'إدارة الاشتراكات:',s.kb([[s.btn('الاشتراكات والتجديد','subadmin')]]))
    def wallet(api,cid):
        old_wallet(api,cid)
        s.send(api,cid,s.tr(cid,'اشتراكاتك وتواريخ انتهائها:','Your subscriptions and expiry dates:'),
               s.kb([[s.btn(s.tr(cid,'اشتراكاتي وتجديدها','My subscriptions and renewals'),'subs:0')]]))
    def action(api,cid,value):
        prefix,_,arg=value.partition(':')
        if prefix=='subs':
            if not arg.isdigit():return
            return page(s,api,cid,min(int(arg),10000))
        if prefix=='subrenew':return renew(s,namespace,api,cid,arg)
        if prefix in ('subadmin','subcat','subproduct','subcfg'):
            if cid!=s.G['ADMIN_ID']:return
            if prefix=='subadmin':return settings(s,api,cid)
            if prefix=='subcat':return settings(s,api,cid,category=arg)
            if prefix=='subproduct':return settings(s,api,cid,pid=arg)
            field,_,pid=arg.partition(':')
            if field=='toggle':
                enabled,days,lead=config(s,pid)
                with s.db() as c:
                    c.execute('INSERT OR REPLACE INTO subscription_settings VALUES (?,?,?,?)',(pid,1-enabled,days,lead))
                return settings(s,api,cid,pid=pid)
            if field in ('days','lead'):
                with s.db() as c:
                    c.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)',(cid,'subscription_'+field,pid))
                return s.send(api,cid,'أرسل عدد الأيام (1 إلى 3660).' if field=='days' else 'أرسل عدد أيام التنبيه قبل الانتهاء (1 إلى 30).',
                              s.kb([[s.btn('إلغاء','subproduct:'+pid)]]))
            return
        if value in ('home','products','start') or prefix in ('cancel','options','item','product'):
            with s.db() as c:
                c.execute('DELETE FROM subscription_intents WHERE cid=?',(cid,))
        return old_action(api,cid,value)
    def receipt(api,message):
        cid=message.get('chat',{}).get('id')
        if cid==s.G['ADMIN_ID']:
            with s.db() as c:
                row=c.execute("SELECT action,value FROM admin_state WHERE cid=? AND action IN ('subscription_days','subscription_lead')",(cid,)).fetchone()
            if row:
                raw=(message.get('text') or '').strip().translate(str.maketrans('٠١٢٣٤٥٦٧٨٩','0123456789'))
                if raw.startswith('/') or raw in namespace.get('MENU',{}):
                    with s.db() as c:c.execute('DELETE FROM admin_state WHERE cid=?',(cid,))
                    return old_receipt(api,message)
                maximum=3660 if row[0]=='subscription_days' else 30
                if not raw.isdigit() or not 1<=int(raw)<=maximum:
                    s.send(api,cid,'أرسل عددًا صحيحًا من 1 إلى '+str(maximum)+'.');return True
                enabled,days,lead=config(s,row[1])
                if row[0]=='subscription_days':days=int(raw)
                else:lead=int(raw)
                with s.db() as c:
                    c.execute('INSERT OR REPLACE INTO subscription_settings VALUES (?,?,?,?)',(row[1],enabled,days,lead))
                    c.execute('DELETE FROM admin_state WHERE cid=?',(cid,))
                settings(s,api,cid,pid=row[1]);return True
        return old_receipt(api,message)
    s.admin_info_editor=editor
    s.admin_panel=admin
    s.wallet=wallet
    namespace['action']=namespace['handle_action']=action
    namespace['handle_receipt']=receipt
    namespace['tick_subscriptions']=lambda api:tick(s,api)
