"""Pandora linked-stock notifications + one-message custom emoji importer."""
from decimal import Decimal
import html
import storefront as s

def _tg(key, fallback):
    try:
        with s.db() as c:
            c.execute("CREATE TABLE IF NOT EXISTS pandora_notification_icons(key TEXT PRIMARY KEY, custom_emoji_id TEXT NOT NULL)")
            r=c.execute("SELECT custom_emoji_id FROM pandora_notification_icons WHERE key=?",(key,)).fetchone()
        if r and str(r[0]).isdecimal():
            return '<tg-emoji emoji-id="'+str(r[0])+'">'+fallback+'</tg-emoji>'
    except Exception: pass
    return fallback

def _entities(message):
    return message.get('entities') or message.get('caption_entities') or []

def import_message(api,message):
    cid=message.get('chat',{}).get('id')
    if cid!=s.G.get('ADMIN_ID'): return False
    with s.db() as c:
        c.execute("CREATE TABLE IF NOT EXISTS pandora_notification_icons(key TEXT PRIMARY KEY, custom_emoji_id TEXT NOT NULL)")
        state=c.execute("SELECT value FROM admin_state WHERE cid=? AND action='pandora_icon_import'",(cid,)).fetchone()
    if not state:return False
    raw=message.get('text') or message.get('caption') or ''
    found=[]
    for e in _entities(message):
        eid=str(e.get('custom_emoji_id') or '')
        if e.get('type')=='custom_emoji' and eid.isdecimal(): found.append(eid)
    # Pandora sample order: product, added, stock, price (when all are custom emojis).
    keys=('product','added','stock','price')
    if not found:
        s.send(api,cid,'❌ لم أجد أيقونات Telegram متحركة في الرسالة. حوّل رسالة Pandora الأصلية نفسها، وليس صورة شاشة.')
        return True
    with s.db() as c:
        for key,eid in zip(keys,found[:4]):
            c.execute("INSERT OR REPLACE INTO pandora_notification_icons(key,custom_emoji_id) VALUES (?,?)",(key,eid))
        c.execute("DELETE FROM admin_state WHERE cid=? AND action='pandora_icon_import'",(cid,))
    s.send(api,cid,'✅ تم استيراد '+str(min(4,len(found)))+' أيقونات متحركة من رسالة Pandora وحفظها لتنبيهات المخزون.')
    return True

def begin_import(api,cid):
    if cid!=s.G.get('ADMIN_ID'):return
    with s.db() as c:c.execute("INSERT OR REPLACE INTO admin_state VALUES (?,?,?)",(cid,'pandora_icon_import','1'))
    s.send(api,cid,'🎨 <b>استيراد أيقونات Pandora</b>\n\nحوّل الآن رسالة المخزون الأصلية من Pandora إلى هذا البوت.\n⚠️ لا ترسل لقطة شاشة؛ يجب Forward للرسالة نفسها.')

def tick(api):
    admin=s.G.get('ADMIN_ID')
    if not admin:return
    with s.db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS pandora_stock_notifications(pid TEXT PRIMARY KEY,stock INTEGER,available INTEGER,initialized INTEGER DEFAULT 0,updated_at TEXT)""")
        rows=c.execute("""SELECT pid FROM supplier_api WHERE provider='pandora' AND enabled=1 AND COALESCE(service_id,'')<>''""").fetchall()
    for (pid,) in rows:
        with s.db() as c:
            old=c.execute("SELECT stock,initialized FROM pandora_stock_notifications WHERE pid=?",(pid,)).fetchone()
        cur=s.pandora_sync_product(pid)
        if not cur:continue
        stock=cur.get('stock'); available=1 if cur.get('available') else 0
        added=(int(stock)-int(old[0])) if old and old[1] and stock is not None and old[0] is not None and int(stock)>int(old[0]) else 0
        with s.db() as c:c.execute("INSERT OR REPLACE INTO pandora_stock_notifications VALUES (?,?,?,1,?)",(pid,stock,available,s.now_saudi()))
        # Keep supplier stock synchronized, but announcements are manual only.
        continue
        if not added:continue
        try:price=s.amount(pid,'USD')
        except Exception:price=None
        text=(_tg('product','🎬')+' <b>'+html.escape(str(s.name(pid,admin)))+'</b>\n'+
              _tg('added','➕')+' <b>Added:</b> '+str(added)+'\n'+
              _tg('stock','📦')+' <b>Current stock:</b> '+str(stock)+'\n'+
              _tg('price','💵')+' <b>Price:</b> $'+(str(price) if price is not None else '—'))
        s.send(api,admin,text,s.kb([[s.btn('🛒 Buy now','item:'+pid,style='success')]]))
