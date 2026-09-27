"""Channel-only catalogue notices and persistent product deep links."""
import hashlib
import json
import time
import urllib.request
import uuid
import storefront as s

CHANNEL = '@SAU2030_k'
BOT = 'SAU2030_bot'
_last_tick = 0


def db():
    c = s.db()
    c.execute('CREATE TABLE IF NOT EXISTS channel_catalog_state (pid TEXT PRIMARY KEY, state TEXT NOT NULL)')
    c.execute('CREATE TABLE IF NOT EXISTS channel_catalog_meta (key TEXT PRIMARY KEY, value TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS channel_catalog_queue (pid TEXT PRIMARY KEY, kind TEXT NOT NULL)')
    c.execute('CREATE TABLE IF NOT EXISTS channel_product_links (token TEXT PRIMARY KEY, pid TEXT NOT NULL)')
    c.execute('CREATE TABLE IF NOT EXISTS channel_pending_links (cid INTEGER PRIMARY KEY, pid TEXT NOT NULL)')
    return c


def product_ids():
    ids = list(s.VARIANTS)
    categories = {v['category'] for v in s.VARIANTS.values()}
    ids += [p for p in s.G['PRODUCTS'] if p not in categories]
    with s.db() as c:
        ids += [r[0] for r in c.execute('SELECT pid FROM admin_products')]
    return list(dict.fromkeys(ids))


def state(pid):
    cp = s.custom_product(pid)
    v = s.VARIANTS.get(pid, {})
    qty = cp[6] if cp else next((v[k] for k in ('stock', 'quantity', 'available_quantity', 'source_stock') if k in v), None)
    try: qty = max(0, int(qty)) if qty is not None else None
    except (ValueError, TypeError): qty = None
    available = bool(s.product_visible(pid) and s.in_stock(pid) and s.can_order(pid))
    if qty == 0: available = False
    return {'available': available, 'quantity': qty}


def link(pid):
    token = hashlib.sha256(pid.encode()).hexdigest()[:24]
    with db() as c:
        c.execute('INSERT OR REPLACE INTO channel_product_links VALUES (?,?)', (token, pid))
    return f'https://t.me/{BOT}?start=product_{token}'


def remember(cid, text):
    parts = text.split(maxsplit=1)
    if len(parts) != 2 or not parts[1].startswith('product_'): return False
    with db() as c:
        row = c.execute('SELECT pid FROM channel_product_links WHERE token=?', (parts[1][8:],)).fetchone()
        if row:
            c.execute('INSERT OR REPLACE INTO channel_pending_links VALUES (?,?)', (cid, row[0]))
    return bool(row)


def resume(api, cid):
    with db() as c:
        row = c.execute('SELECT pid FROM channel_pending_links WHERE cid=?', (cid,)).fetchone()
        c.execute('DELETE FROM channel_pending_links WHERE cid=?', (cid,))
    if not row: return False
    s.reset_navigation_state(cid)
    if row[0] in product_ids() and s.product_visible(row[0]):
        s.G['action'](api, cid, ('item:' if row[0] in s.VARIANTS or s.custom_product(row[0]) else 'product:') + row[0])
    else:
        s.send(api, cid, 'هذا المنتج غير متاح حاليًا. يمكنك تصفح المنتجات الأخرى.')
        s.products(api, cid)
    return True


def local_photo(api, target, image_path, text, markup):
    path = (s.BASE / image_path).resolve()
    api_url = getattr(api, 'u', None)
    if not api_url or not path.is_relative_to(s.BASE) or not path.is_file(): return None
    boundary = 'VEXA' + uuid.uuid4().hex
    body = b''
    for key, value in {'chat_id': str(target), 'caption': text, 'reply_markup': json.dumps(markup)}.items():
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
    body += f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="{path.name}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
    body += path.read_bytes() + f'\r\n--{boundary}--\r\n'.encode()
    try:
        req = urllib.request.Request(api_url + 'sendPhoto', body, {'Content-Type': f'multipart/form-data; boundary={boundary}'})
        with urllib.request.urlopen(req, timeout=40) as response:
            return json.load(response).get('result')
    except Exception:
        return None


def post(api, pid, target=CHANNEL, kind='stock'):
    if pid not in product_ids() or not state(pid)['available']: return None
    st = state(pid)
    heading = '✨ منتج جديد' if kind == 'new' else '🔥 توفر الآن'
    text = heading + ' | VEXA STORE\n\n' + s.name(pid, 0)[:150]
    text += '\n\n💵 السعر: ' + s.price(0, pid, 'SAR')
    text += '\n📦 الكمية المتوفرة: ' + str(st['quantity']) if st['quantity'] is not None else '\n✅ متوفر'
    description = s.product_description(pid, 0)
    if description: text += '\n\n' + description[:250]
    text += '\n\nاضغط الزر لعرض المنتج وشرائه 👇'
    markup = {'inline_keyboard': [[{'text': '🛒 شراء الآن', 'url': link(pid), 'style': 'success'}]]}
    photo = s.saved_product_photo(pid)
    if photo:
        result = api.call('sendPhoto', chat_id=target, photo=photo, caption=text, reply_markup=markup)
        if result: return result
    if photo is None:
        image_path = s.VARIANTS.get(pid, {}).get('image') or ('assets/' + pid + '.png' if pid in s.G['PRODUCTS'] else None)
        if image_path:
            result = local_photo(api, target, image_path, text, markup)
            if result: return result
    return api.call('sendMessage', chat_id=target, text=text, reply_markup=markup)


def scan():
    current = {pid: state(pid) for pid in product_ids()}
    with db() as c:
        initialized = c.execute("SELECT 1 FROM channel_catalog_meta WHERE key='initialized'").fetchone()
        for pid, new in current.items():
            row = c.execute('SELECT state FROM channel_catalog_state WHERE pid=?', (pid,)).fetchone()
            old = json.loads(row[0]) if row else None
            increase = old and new['quantity'] is not None and old['quantity'] is not None and new['quantity'] > old['quantity']
            if initialized and new['available'] and (old is None or not old['available'] or increase):
                c.execute('INSERT OR REPLACE INTO channel_catalog_queue VALUES (?,?)', (pid, 'new' if old is None else 'stock'))
            c.execute('INSERT OR REPLACE INTO channel_catalog_state VALUES (?,?)', (pid, json.dumps(new)))
        c.execute("INSERT OR REPLACE INTO channel_catalog_meta VALUES ('initialized','1')")


def tick(api):
    global _last_tick
    if time.monotonic() - _last_tick < 30: return
    _last_tick = time.monotonic()
    try:
        scan()
        with db() as c:
            jobs = c.execute('SELECT pid,kind FROM channel_catalog_queue ORDER BY rowid LIMIT 1').fetchall()
        for pid, kind in jobs:
            if pid not in product_ids() or not state(pid)['available']:
                with db() as c: c.execute('DELETE FROM channel_catalog_queue WHERE pid=?', (pid,))
                continue
            if post(api, pid, kind=kind):
                with db() as c: c.execute('DELETE FROM channel_catalog_queue WHERE pid=?', (pid,))
            else: print('Channel notice pending: check bot publishing permission for', CHANNEL, flush=True)
    except Exception as exc:
        print('Channel catalogue:', type(exc).__name__, flush=True)


def install(namespace):
    old_action = namespace['action']
    def action(api, cid, value):
        if not value.startswith('channel:'): return old_action(api, cid, value)
        if cid != namespace['ADMIN_ID']: return
        parts = value.split(':', 2)
        verb = parts[1]
        if verb == 'list':
            page = max(0, int(parts[2])) if len(parts) > 2 and parts[2].isdigit() else 0
            ids = [pid for pid in product_ids() if state(pid)['available']]
            rows = [[s.btn(s.name(pid, 0)[:55], 'channel:preview:' + pid)] for pid in ids[page*8:(page+1)*8]]
            nav = []
            if page: nav.append(s.btn('⬅️ السابق', f'channel:list:{page-1}'))
            if (page+1)*8 < len(ids): nav.append(s.btn('التالي ➡️', f'channel:list:{page+1}'))
            if nav: rows.append(nav)
            rows.append([s.btn('↩️ لوحة الإدارة', 'admin')])
            return s.send(api, cid, '📣 النشر في @SAU2030_k\nالإشعارات تلقائية عند إضافة منتج متوفر أو زيادة كميته أو عودته للتوفر.\n\nاختر منتجًا لمعاينته ونشره الآن:', s.kb(rows))
        if len(parts) < 3: return
        pid = parts[2]
        if verb == 'preview':
            if not post(api, pid, target=cid): return s.send(api, cid, 'هذا المنتج غير متوفر للنشر.')
            return s.send(api, cid, 'نشر هذا الإعلان في @SAU2030_k؟', s.kb([[s.btn('✅ نشر في القناة', 'channel:send:' + pid)], [s.btn('↩️ رجوع', 'channel:list')]]))
        if verb == 'send':
            result = post(api, pid)
            if result:
                with db() as c:
                    c.execute('DELETE FROM channel_catalog_queue WHERE pid=?', (pid,))
                    c.execute('INSERT OR REPLACE INTO channel_catalog_state VALUES (?,?)', (pid, json.dumps(state(pid))))
            return s.send(api, cid, '✅ تم نشر المنتج في @SAU2030_k.' if result else '❌ تعذر النشر. تأكد من توفر المنتج وأن البوت مشرف في @SAU2030_k ولديه صلاحية النشر.')
    namespace['action'] = action
    # Automatic catalogue notices belong only in the requested channel.
    s.broadcast_product_alert = lambda api, pid, kind='new': scan()
    namespace['broadcast_new_products'] = lambda api: scan()
    namespace['tick_channel_catalog'] = tick
