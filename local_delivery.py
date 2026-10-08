"""Local automatic stock delivery: email + password + profile, text + TXT."""
import html
import json
import threading

_DELIVERY_LOCK = threading.RLock()
import urllib.request

def prepare(c):
    c.execute("""CREATE TABLE IF NOT EXISTS local_delivery_stock (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pid TEXT NOT NULL,
        email TEXT NOT NULL,
        password TEXT NOT NULL,
        profile TEXT NOT NULL,
        order_id TEXT UNIQUE,
        delivered_at TEXT NOT NULL DEFAULT ''
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_local_delivery_stock_pid ON local_delivery_stock(pid,id)")

def count_available(s,pid):
    with s.db() as c:
        prepare(c)
        return c.execute("SELECT COUNT(*) FROM local_delivery_stock WHERE pid=? AND order_id IS NULL",(pid,)).fetchone()[0]

def has_stock(s,pid):
    return count_available(s,pid)>0

def _send_file(api,cid,oid,email,password,profile):
    base=getattr(api,'u',None) or getattr(api,'base_url',None)
    if not base:return False
    content=("Email: "+email+"\nPassword: "+password+"\nProfile: "+profile+"\n").encode("utf-8")
    boundary="----VEXALocalDelivery"
    body=("--"+boundary+"\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n"+str(cid)+"\r\n").encode()
    body+=("--"+boundary+"\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n📄 Your account details\r\n").encode()
    body+=("--"+boundary+"\r\nContent-Disposition: form-data; name=\"document\"; filename=\"order-"+str(oid).replace("/","-")+".txt\"\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n").encode()+content+b"\r\n"
    body+=("--"+boundary+"--\r\n").encode()
    try:
        req=urllib.request.Request(base+"sendDocument",data=body,headers={"Content-Type":"multipart/form-data; boundary="+boundary},method="POST")
        with urllib.request.urlopen(req,timeout=40) as r:return r.status<300 and bool(json.loads(r.read()).get("ok"))
    except Exception:return False

def _update_stock(s,c,pid):
    left=c.execute("SELECT COUNT(*) FROM local_delivery_stock WHERE pid=? AND order_id IS NULL",(pid,)).fetchone()[0]
    if s.custom_product(pid):
        c.execute("UPDATE admin_products SET stock=?,available=? WHERE pid=?",(left,bool(left),pid))
    else:
        c.execute("INSERT OR REPLACE INTO product_stock_overrides(pid,stock) VALUES (?,?)",(pid,left))
        c.execute("INSERT OR REPLACE INTO product_availability(pid,available) VALUES (?,?)",(pid,bool(left)))

def fulfill(s,api,oid):
    with _DELIVERY_LOCK:
        return _fulfill(s,api,oid)

def _fulfill(s,api,oid):
    with s.db() as c:
        prepare(c)
        order=c.execute("SELECT cid,pid,status FROM orders WHERE id=?",(oid,)).fetchone()
        if not order or order[2] not in ('paid','delivered'):return False
        cid,pid,status=order
        row=c.execute("SELECT provider,enabled FROM supplier_api WHERE pid=?",(pid,)).fetchone()
        if row and row[0]=='pandora' and row[1]:return False
        if not c.execute("SELECT 1 FROM local_delivery_stock WHERE pid=? LIMIT 1",(pid,)).fetchone():return False
        if status=='delivered':return True
        qty_row=c.execute("SELECT qty FROM quantity_snapshots WHERE kind='order' AND key=?",(oid,)).fetchone()
        qty=max(1,int(qty_row[0])) if qty_row else 1
        keys=[str(oid) if i==0 else str(oid)+'/'+str(i+1) for i in range(qty)]
        c.execute("BEGIN IMMEDIATE")
        existing=[c.execute("SELECT id,email,password,profile,delivered_at FROM local_delivery_stock WHERE order_id=?",(key,)).fetchone() for key in keys]
        missing=sum(row is None for row in existing)
        stock=c.execute("SELECT id,email,password,profile,delivered_at FROM local_delivery_stock WHERE pid=? AND order_id IS NULL ORDER BY id LIMIT ?",(pid,missing)).fetchall()
        if len(stock)<missing:
            s.send(api,s.G['ADMIN_ID'],'⚠️ الطلب المدفوع #'+s.esc(oid)+' ينتظر إضافة بيانات تسليم كافية.',s.kb([[s.btn('إضافة بيانات الحسابات','localstockadd:'+pid)],[s.btn('إعادة محاولة التسليم','localretry:'+str(oid))]]))
            return True
        for i,row in enumerate(existing):
            if row is None:
                row=stock.pop(0)
                c.execute("UPDATE local_delivery_stock SET order_id=? WHERE id=? AND order_id IS NULL",(keys[i],row[0]))
                existing[i]=row
        _update_stock(s,c,pid)
    for key,row in zip(keys,existing):
        item_id,email,password,profile,stage=row
        if stage and stage!='text_sent':continue
        if not stage:
            text=s.tr(cid,'✅ <b>بيانات حسابك</b>','✅ <b>Your Account Details</b>')+'\n\n'+s.tr(cid,'الإيميل: ','Email: ')+'<code>'+html.escape(email)+'</code>\n'+s.tr(cid,'كلمة المرور: ','Password: ')+'<code>'+html.escape(password)+'</code>\n'+s.tr(cid,'رقم الملف: ','Profile: ')+'<code>'+html.escape(profile)+'</code>'
            if not s.send(api,cid,text):
                s.send(api,s.G['ADMIN_ID'],'⚠️ تعذر إرسال بيانات الطلب #'+s.esc(oid),s.kb([[s.btn('إعادة محاولة التسليم','localretry:'+str(oid))]]))
                return True
            with s.db() as c:c.execute("UPDATE local_delivery_stock SET delivered_at='text_sent' WHERE id=?",(item_id,))
        if not _send_file(api,cid,key,email,password,profile):
            s.send(api,s.G['ADMIN_ID'],'⚠️ أُرسل نص الطلب #'+s.esc(oid)+' وتعذر إرسال الملف.',s.kb([[s.btn('إعادة محاولة إرسال الملف','localretry:'+str(oid))]]))
            return True
        with s.db() as c:c.execute("UPDATE local_delivery_stock SET delivered_at=? WHERE id=?",(s.now_saudi(),item_id))
    with s.db() as c:c.execute("UPDATE orders SET status='delivered' WHERE id=? AND status='paid'",(oid,))
    if hasattr(s,'show_code_button'):
        s.show_code_button(api,cid,oid)
    s.send(api,s.G['ADMIN_ID'],'✅ تم التسليم التلقائي للطلب <code>'+html.escape(str(oid))+'</code>')
    return True


def admin_menu(s,api,cid,pid):
    if cid!=s.G["ADMIN_ID"]:return
    with s.db() as c:c.execute("DELETE FROM admin_state WHERE cid=?",(cid,))
    n=count_available(s,pid)
    s.send(api,cid,"📦 <b>مخزون التسليم التلقائي</b>\n\n"+html.escape(s.name(pid,cid))+"\nالمتاح: <b>"+str(n)+"</b>\n\nأضف كل حساب في سطر بهذا الشكل:\n<code>email | password | profile</code>",s.kb([[s.btn("➕ إضافة مخزون","localstockadd:"+pid,style="success")],[s.btn("🗑 مسح المخزون غير المباع","localstockclear:"+pid,style="danger")],[s.btn("اختيار منتج آخر","localstockmenu")],[s.btn("لوحة الإدارة","admin")]]))

def begin_add(s,api,cid,pid):
    if cid!=s.G["ADMIN_ID"]:return
    s.G.get("PENDING_ADMIN_DELIVERY",{}).pop(cid,None)
    with s.db() as c:
        prepare(c);c.execute("INSERT OR REPLACE INTO admin_state VALUES (?,?,?)",(cid,"local_delivery_add",pid))
    s.send(api,cid,"أرسل الحسابات الآن. كل حساب في سطر:\n<code>email | password | profile</code>\n\nمثال:\n<code>user@example.com | Pass123 | 1</code>",s.kb([[s.btn("❌ إلغاء","localstock:"+pid)]]))

def handle_text(s,api,message):
    cid=message.get("chat",{}).get("id")
    if cid!=s.G.get("ADMIN_ID"):return False
    with s.db() as c:
        prepare(c);row=c.execute("SELECT value FROM admin_state WHERE cid=? AND action='local_delivery_add'",(cid,)).fetchone()
    if not row:return False
    raw=(message.get("text") or "").strip()
    if raw.startswith("/"):return False
    parsed=[]
    for line in raw.splitlines():
        parts=[x.strip() for x in line.split("|")]
        if len(parts)==3 and all(parts):parsed.append(tuple(parts))
    if not parsed or len(parsed)!=len(raw.splitlines()):
        s.send(api,cid,"⚠️ الصيغة غير صحيحة. استخدم:\n<code>email | password | profile</code>")
        return True
    pid=row[0]
    with s.db() as c:
        prepare(c);c.executemany("INSERT INTO local_delivery_stock(pid,email,password,profile) VALUES (?,?,?,?)",[(pid,*x) for x in parsed]);c.execute("DELETE FROM admin_state WHERE cid=?",(cid,))
        left=c.execute("SELECT COUNT(*) FROM local_delivery_stock WHERE pid=? AND order_id IS NULL",(pid,)).fetchone()[0]
        if s.custom_product(pid):c.execute("UPDATE admin_products SET stock=?,available=1 WHERE pid=?",(left,pid))
        else:
            c.execute("INSERT OR REPLACE INTO product_stock_overrides VALUES (?,?)",(pid,left));c.execute("INSERT OR REPLACE INTO product_availability VALUES (?,1)",(pid,))
    s.send(api,cid,"✅ تمت إضافة <b>"+str(len(parsed))+"</b> حساب.\n📦 المخزون الحالي: <b>"+str(left)+"</b>")
    admin_menu(s,api,cid,pid);return True

def clear(s,api,cid,pid):
    if cid!=s.G["ADMIN_ID"]:return
    with s.db() as c:
        prepare(c);c.execute("DELETE FROM local_delivery_stock WHERE pid=? AND order_id IS NULL",(pid,))
        if s.custom_product(pid):c.execute("UPDATE admin_products SET stock=0,available=0 WHERE pid=?",(pid,))
        else:
            c.execute("INSERT OR REPLACE INTO product_stock_overrides VALUES (?,0)",(pid,));c.execute("INSERT OR REPLACE INTO product_availability VALUES (?,0)",(pid,))
    admin_menu(s,api,cid,pid)



def action(s,api,cid,value):
    prefix,_,arg=value.partition(':')
    if prefix not in ('localstockmenu','localstockcat','localstock','localstockadd','localstockclear','localretry'):return False
    if cid!=s.G['ADMIN_ID']:return True
    if prefix=='localstockmenu':
        with s.db() as c:
            c.execute('DELETE FROM admin_state WHERE cid=?',(cid,))
            cats=c.execute('SELECT cid,name FROM admin_categories').fetchall()
            direct=c.execute("SELECT pid,name FROM admin_products WHERE COALESCE(category_id,'')='' ORDER BY rowid").fetchall()
        categories=list(dict.fromkeys(list(s.G['PRODUCTS'])+[r[0] for r in cats]))
        labels=dict(cats)
        rows=[[s.btn(labels.get(pid) or s.name(pid,cid),'localstockcat:'+pid)] for pid in categories]
        rows += [[s.btn(name,'localstock:'+pid)] for pid,name in direct]
        s.send(api,cid,'<b>إضافة بيانات تسليم المنتجات</b>\nاختر القسم ثم المنتج. تُرسل البيانات بعد تأكيد الدفع فقط.',s.kb(rows+[[s.btn('لوحة الإدارة','admin')]]))
    elif prefix=='localstockcat':
        rows=[[s.btn(s.name(pid,cid),'localstock:'+pid)] for pid in s.admin_category_product_ids(arg)]
        s.send(api,cid,'اختر المنتج لإضافة إيميل وكلمة مرور ورقم الملف:',s.kb(rows+[[s.btn('رجوع','localstockmenu')]]))
    elif prefix=='localstock':admin_menu(s,api,cid,arg)
    elif prefix=='localstockadd':begin_add(s,api,cid,arg)
    elif prefix=='localstockclear':clear(s,api,cid,arg)
    elif prefix=='localretry':fulfill(s,api,arg)
    return True
