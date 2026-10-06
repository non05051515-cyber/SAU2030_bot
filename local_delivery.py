"""Local automatic stock delivery: email + password + profile, text + TXT."""
import html
import io
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
    body+=("--"+boundary+"\r\nContent-Disposition: form-data; name=\"document\"; filename=\"order-"+str(oid)+".txt\"\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n").encode()+content+b"\r\n"
    body+=("--"+boundary+"--\r\n").encode()
    try:
        req=urllib.request.Request(base+"sendDocument",data=body,headers={"Content-Type":"multipart/form-data; boundary="+boundary},method="POST")
        with urllib.request.urlopen(req,timeout=40) as r:return r.status<300
    except Exception:return False

def fulfill(s,api,oid):
    with s.db() as c:
        prepare(c)
        order=c.execute("SELECT cid,pid,status FROM orders WHERE id=?",(oid,)).fetchone()
        if not order or order[2]!="paid": return False
        cid,pid,_=order
        # Pandora always owns its own fulfillment path.
        row=c.execute("SELECT provider,enabled FROM supplier_api WHERE pid=?",(pid,)).fetchone()
        if row and row[0]=="pandora" and row[1]: return False
        c.execute("BEGIN IMMEDIATE")
        existing=c.execute("SELECT email,password,profile FROM local_delivery_stock WHERE order_id=?",(oid,)).fetchone()
        if existing:
            item=existing
        else:
            item=c.execute("SELECT email,password,profile FROM local_delivery_stock WHERE pid=? AND order_id IS NULL ORDER BY id LIMIT 1",(pid,)).fetchone()
            if not item:return False
            changed=c.execute("UPDATE local_delivery_stock SET order_id=? WHERE id=(SELECT id FROM local_delivery_stock WHERE pid=? AND order_id IS NULL ORDER BY id LIMIT 1) AND order_id IS NULL",(oid,pid)).rowcount
            if not changed:return False
    email,password,profile=item
    text=("✅ <b>Your Account Details</b>\n\n"
          "📧 <b>Email:</b> <code>"+html.escape(email)+"</code>\n"
          "🔑 <b>Password:</b> <code>"+html.escape(password)+"</code>\n"
          "👤 <b>Profile:</b> <code>"+html.escape(profile)+"</code>")
    if not s.send(api,cid,text):
        return True
    _send_file(api,cid,oid,email,password,profile)
    with s.db() as c:
        c.execute("UPDATE local_delivery_stock SET delivered_at=? WHERE order_id=?",(s.now_saudi(),oid))
        c.execute("UPDATE orders SET status='delivered' WHERE id=? AND status='paid'",(oid,))
        left=c.execute("SELECT COUNT(*) FROM local_delivery_stock WHERE pid=? AND order_id IS NULL",(pid,)).fetchone()[0]
        if s.custom_product(pid):
            c.execute("UPDATE admin_products SET stock=?,available=? WHERE pid=?",(left,1 if left else 0,pid))
        else:
            c.execute("INSERT OR REPLACE INTO product_stock_overrides(pid,stock) VALUES (?,?)",(pid,left))
            c.execute("INSERT OR REPLACE INTO product_availability(pid,available) VALUES (?,?)",(pid,1 if left else 0))
    s.send(api,s.G["ADMIN_ID"],"✅ تم التسليم التلقائي للطلب <code>"+html.escape(str(oid))+"</code>")
    return True

def admin_menu(s,api,cid,pid):
    if cid!=s.G["ADMIN_ID"]:return
    n=count_available(s,pid)
    s.send(api,cid,"📦 <b>مخزون التسليم التلقائي</b>\n\n"+html.escape(s.name(pid,cid))+"\nالمتاح: <b>"+str(n)+"</b>\n\nأضف كل حساب في سطر بهذا الشكل:\n<code>email | password | profile</code>",s.kb([[s.btn("➕ إضافة مخزون","localstockadd:"+pid,style="success")],[s.btn("🗑 مسح المخزون غير المباع","localstockclear:"+pid,style="danger")],[s.btn("↩️ المنتج","myproduct:"+pid)]]))

def begin_add(s,api,cid,pid):
    if cid!=s.G["ADMIN_ID"]:return
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
    if not parsed:
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
