"""One durable payment approval and Pandora execution path.

Secrets are resolved in the running service only. No supplier errors/payloads
or delivery credentials are logged. Supplier submission uses a persisted payload
and a stable idempotency key, including after an ambiguous network failure.
"""
import json
import io
import urllib.parse
import re
import threading
import time
import uuid
from decimal import Decimal, ROUND_HALF_UP

EVENT = threading.local()
LEASE_SECONDS = 180


def prepare(c):
    c.execute('''CREATE TABLE IF NOT EXISTS payment_execution (
        order_id TEXT PRIMARY KEY, original_pid TEXT NOT NULL, supplier_pid TEXT NOT NULL,
        payload TEXT NOT NULL DEFAULT '', lease_until REAL NOT NULL DEFAULT 0,
        lease_owner TEXT NOT NULL DEFAULT '', notified INTEGER NOT NULL DEFAULT 0,
        attempts INTEGER NOT NULL DEFAULT 0)''')
    c.execute('CREATE TABLE IF NOT EXISTS payment_sources (source TEXT PRIMARY KEY,order_id TEXT NOT NULL UNIQUE)')


def is_capcut(s, pid):
    v = s.VARIANTS.get(pid, {})
    if v.get('category') == 'capcut' or pid.startswith('capcut_'):
        return True
    cp = s.custom_product(pid)
    cat = s.custom_category(cp[5]) if cp and cp[5] else None
    return 'capcut' in re.sub(r'\s+', '', str((cp[1] if cp else '') + ' ' + (cat[1] if cat else '')).lower())


def needs_supplier(s, pid):
    with s.db() as c:
        row = c.execute('SELECT provider FROM supplier_api WHERE pid=?', (pid,)).fetchone()
    return bool(row and row[0] == 'pandora') or is_capcut(s, pid)


def sku_kind(label):
    label = str(label).lower().translate(str.maketrans('٠١٢٣٤٥٦٧٨٩', '0123456789'))
    if '1600' in label:
        return '1m1600'
    if re.search(r'6\s*(m\b|months?\b|أشهر|اشهر|شهور)', label):
        return '6m'
    if re.search(r'7\s*(d\b|days?\b|أيام|ايام)', label):
        return '7d'
    if re.search(r'1\s*(m\b|months?\b)', label) or 'شهر' in label:
        return '1m'
    return None


def resolve_pid(s, pid):
    with s.db() as c:
        direct = c.execute('SELECT provider,service_id,enabled,variant_id FROM supplier_api WHERE pid=?', (pid,)).fetchone()
    # Resolve disabled legacy aliases by identical supplier identity only.
    legacy = s.VARIANTS.get(pid,{}).get('category') == 'capcut' or pid.startswith('capcut_')
    if direct and direct[0] == 'pandora':
        if direct[2] or not legacy:
            return pid
        with s.db() as c:
            same = c.execute('''SELECT p.pid FROM admin_products p JOIN supplier_api a ON a.pid=p.pid
                WHERE a.provider='pandora' AND a.enabled=1 AND a.service_id=? AND a.variant_id=? ORDER BY p.rowid''',
                (direct[1],direct[3])).fetchall()
        if same:
            return same[0][0]
        return pid
    if not is_capcut(s, pid):
        return pid
    cp = s.custom_product(pid)
    v = s.VARIANTS.get(pid, {})
    names = [cp[1]] if cp else list(v.get('name', {}).values())
    legacy_kinds = {'capcut_6m':'6m','capcut_1m_1600':'1m1600','capcut_1m':'1m','capcut_7d':'7d'}
    kind = legacy_kinds.get(pid) or next((sku_kind(n) for n in names if sku_kind(n)), None)
    normalized = {re.sub(r'[^a-z0-9]+', '', n.lower()) for n in names}
    with s.db() as c:
        rows = c.execute('''SELECT p.pid,p.name,a.service_id,a.variant_id FROM admin_products p
            JOIN supplier_api a ON a.pid=p.pid WHERE a.provider='pandora' AND a.service_id<>'' ''').fetchall()
    rows = [r for r in rows if is_capcut(s, r[0])]
    exact = [r for r in rows if re.sub(r'[^a-z0-9]+', '', r[1].lower()) in normalized]
    matches = exact or ([r for r in rows if sku_kind(r[1]) == kind] if kind else [])
    # Distinct SKUs/variants with the same label are ambiguous and must fail closed.
    identities = {(r[2],r[3]) for r in matches}
    if len(identities) == 1:
        return matches[0][0]
    return pid


def approve(s, api, actor, decision, oid):
    if actor != s.G['ADMIN_ID'] or decision not in ('accept', 'reject'):
        return
    # Commit payment BEFORE any supplier connection. The old route read 'review'
    # via a second connection while its 'paid' update was still uncommitted.
    with s.db() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT cid,pid,status FROM orders WHERE id=?', (oid,)).fetchone()
        if not row:
            return s.send(api, actor, '⚠️ الطلب غير موجود.')
        customer, pid, status = row
        if status != 'review':
            return s.send(api, actor, '⚠️ تمت معالجة الطلب مسبقًا.')
        changed = c.execute('UPDATE orders SET status=? WHERE id=? AND status=?',
                            ('paid' if decision == 'accept' else 'rejected', oid, 'review')).rowcount
    if not changed:
        return
    if decision == 'reject':
        s.send(api, customer, '❌ تم رفض إثبات الدفع.')
        return s.send(api, actor, '❌ تم رفض الطلب #' + s.esc(oid))
    print('VEXA approval route: '+json.dumps({'order_id':oid,'saved_pid':pid,'route':'pandora' if needs_supplier(s,pid) else 'manual'}),flush=True)
    if s.fulfill_paid_order(api, oid):
        return s.send(api, actor, '✅ تم قبول الطلب #' + s.esc(oid) + ' ومتابعته عبر التنفيذ التلقائي.')
    s.G['PENDING_ADMIN_DELIVERY'][actor] = {'customer':customer,'order_id':oid}
    s.send(api, customer, '✅ تم قبول الدفع. سيتم إرسال طلبك قريبًا.')
    return s.send(api, actor, '📤 أرسل بيانات تسليم الطلب #' + s.esc(oid))


def claim(s, oid, original, resolved):
    owner = uuid.uuid4().hex
    with s.db() as c:
        c.execute('BEGIN IMMEDIATE')
        prepare(c)
        c.execute('INSERT OR IGNORE INTO payment_execution(order_id,original_pid,supplier_pid) VALUES (?,?,?)', (oid,original,resolved))
        c.execute('''UPDATE payment_execution SET supplier_pid=? WHERE order_id=? AND payload=''
            AND lease_until<? AND NOT EXISTS(SELECT 1 FROM supplier_orders WHERE order_id=? AND supplier_order_id<>'')''',
            (resolved,oid,time.time(),oid))
        job = c.execute('SELECT supplier_pid,payload,notified FROM payment_execution WHERE order_id=?', (oid,)).fetchone()
        changed = c.execute('''UPDATE payment_execution SET lease_until=?,lease_owner=?,attempts=attempts+1
            WHERE order_id=? AND lease_until<? AND notified=0''', (time.time()+LEASE_SECONDS,owner,oid,time.time())).rowcount
    return (owner, job) if changed else (None, job)


def _send_delivery_file(api, cid, oid, product_name, items):
    clean=[str(x).strip() for x in (items or []) if str(x).strip()]
    if not clean:return None
    content=('\n'.join(clean)+'\n').encode('utf-8')
    base=getattr(api,'u',None)
    if not base:return False
    boundary='----VEXADeliveryBoundary'
    filename='order-'+str(oid)+'-'+''.join(ch if ch.isalnum() or ch in '-_' else '-' for ch in str(product_name))[:45]+'.txt'
    fields={'chat_id':str(cid),'caption':'📄 1 item → '+str(product_name)+'\nKeep this file private and store it somewhere safe.'}
    body=b''
    for k,v in fields.items():
        body+=('--'+boundary+'\r\nContent-Disposition: form-data; name="'+k+'"\r\n\r\n'+v+'\r\n').encode('utf-8')
    body+=('--'+boundary+'\r\nContent-Disposition: form-data; name="document"; filename="'+filename+'"\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n').encode('utf-8')+content+b'\r\n'
    body+=('--'+boundary+'--\r\n').encode('utf-8')
    req=urllib.request.Request(base+'sendDocument',data=body,headers={'Content-Type':'multipart/form-data; boundary='+boundary},method='POST')
    try:
        with urllib.request.urlopen(req,timeout=40) as r:return r.status<300
    except Exception as exc:
        print('Delivery file send failed:',type(exc).__name__,flush=True);return False

def fulfill(s, api, oid):
    import local_delivery
    if local_delivery.fulfill(s, api, oid):
        return True
    with s.db() as c:
        order = c.execute('SELECT cid,pid,status,usd FROM orders WHERE id=?', (oid,)).fetchone()
    if not order:
        return False
    cid, original, order_status, paid_usd = order
    if not needs_supplier(s, original):
        return False
    if order_status != 'paid':
        return True
    resolved = resolve_pid(s, original)
    owner, job = claim(s, oid, original, resolved)
    if not owner:
        return True
    pid, saved_payload, notified = job
    supplier_id = ''
    try:
        endpoint,key,product_id,enabled,provider,variant_id = s.supplier_api_row(pid)
        if not (provider == 'pandora' and enabled and endpoint and key and product_id):
            raise ValueError('supplier_binding_incomplete')
        with s.db() as c:
            existing = c.execute('SELECT supplier_order_id,status,delivery FROM supplier_orders WHERE order_id=?', (oid,)).fetchone()
            qty_row = c.execute("SELECT qty FROM quantity_snapshots WHERE kind='order' AND key=?", (oid,)).fetchone()
        qty = int(qty_row[0]) if qty_row else 1
        supplier_id = existing[0] if existing else ''
        cached = json.loads(existing[2]) if existing and existing[2] else []
        if cached:
            result = {'status':existing[1], 'delivery':{'items':cached}}
        elif supplier_id:
            result = s._supplier_json_request(endpoint.rstrip('/')+'/orders/'+s.urllib.parse.quote(supplier_id),key)
        else:
            if saved_payload:
                payload = json.loads(saved_payload)
            else:
                quote_payload = {'product_id':product_id,'quantity':qty}
                if variant_id:
                    quote_payload['variant_id'] = variant_id
                quote = s._supplier_json_request(endpoint.rstrip('/')+'/quotes',key,'POST',quote_payload)
                if not quote.get('can_purchase') or quote.get('unit_price') is None or not quote.get('price_version'):
                    raise ValueError('supplier_quote_unavailable')
                cost = Decimal(str(quote['unit_price'])).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)
                margin = Decimal(str(s.pandora_pricing_row(pid)[1] or '0'))
                if Decimal(str(paid_usd or '0')) < (cost+margin)*qty:
                    raise ValueError('supplier_price_changed')
                payload = dict(quote_payload,expected_unit_price=float(cost),price_version=quote['price_version'],client_order_reference=oid)
                # Save BEFORE network submission. A retry replays identical request.
                with s.db() as c:
                    c.execute('UPDATE payment_execution SET payload=? WHERE order_id=? AND lease_owner=?', (json.dumps(payload),oid,owner))
            result = s._supplier_json_request(endpoint.rstrip('/')+'/orders',key,'POST',payload,{'Idempotency-Key':'vexa-'+oid})
            supplier_id = str(result.get('id') or '')
            if not supplier_id:
                raise ValueError('supplier_response_missing_id')
        items = (result.get('delivery') or {}).get('items') or []
        status = str(result.get('status') or 'pending')
        with s.db() as c:
            c.execute('''INSERT INTO supplier_orders(order_id,supplier_order_id,status,delivery,last_error,updated_at)
                VALUES (?,?,?,?,?,?) ON CONFLICT(order_id) DO UPDATE SET
                supplier_order_id=CASE WHEN excluded.supplier_order_id<>'' THEN excluded.supplier_order_id ELSE supplier_orders.supplier_order_id END,
                status=excluded.status,delivery=excluded.delivery,last_error=excluded.last_error,updated_at=excluded.updated_at''',
                (oid,supplier_id,status,json.dumps(items) if items else '', '',s.now_saudi()))
        if items:
            product_name=s.name(original,cid)
            clean=[str(x).strip() for x in items if str(x).strip()]
            body='\n'.join('<code>'+s.esc(x)+'</code>' for x in clean)
            text=('🛒 <b>Thank you for your purchase!</b>\nYour order is complete and ready below.\n\n'
                  '✨ <b>Order:</b> #'+s.esc(str(oid))+'\n'
                  '➕ <b>'+s.esc(product_name)+' ×1</b>\n\n'
                  '📩 <b>Your item</b>\nTap and hold any delivered line below to copy it.\n\n'+body+
                  '\n\n📎 <b>Instructions:</b>\nDelivered automatically after payment.')
            sent=s.send(api,cid,text,s.menu(cid))
            if not sent: raise RuntimeError('telegram_delivery_failed')
            _send_delivery_file(api,cid,oid,product_name,items)
            with s.db() as c:
                c.execute('UPDATE orders SET status=? WHERE id=?',('delivered',oid))
                c.execute('UPDATE payment_execution SET notified=1 WHERE order_id=? AND lease_owner=?',(oid,owner))
            s.send(api,s.G['ADMIN_ID'],'✅ تسليم تلقائي عبر Pandora للطلب <code>'+s.esc(oid)+'</code>')
        elif not existing:
            s.send(api,cid,'⏳ تم قبول الدفع. طلبك قيد التنفيذ والتسليم التلقائي عبر Pandora، ولا تحتاج لإعادة الدفع.')
        return True
    except Exception as exc:
        with s.db() as c:
            # Preserve the supplier ID and delivery on any later polling/send error.
            c.execute('''INSERT INTO supplier_orders(order_id,supplier_order_id,status,last_error,updated_at)
                VALUES (?,?,?, ?,?) ON CONFLICT(order_id) DO UPDATE SET
                last_error=excluded.last_error,updated_at=excluded.updated_at''',
                (oid,supplier_id,'error',type(exc).__name__,s.now_saudi()))
        s.send(api,s.G['ADMIN_ID'],'⚠️ تعذر التنفيذ التلقائي للطلب <code>'+s.esc(oid)+'</code>. راجع ربط المورد أو أعد المحاولة. التسليم اليدوي محظور.')
        return True
    finally:
        with s.db() as c:
            c.execute('UPDATE payment_execution SET lease_until=0,lease_owner=? WHERE order_id=? AND lease_owner=?',('',oid,owner))


def tick(s, api):
    # Poll only already-created orders. Never purchase backlog orders at startup.
    with s.db() as c:
        prepare(c)
        ids = [r[0] for r in c.execute('''SELECT so.order_id FROM supplier_orders so
            JOIN payment_execution e ON e.order_id=so.order_id JOIN orders o ON o.id=so.order_id
            WHERE so.supplier_order_id<>'' AND e.notified=0 AND o.status='paid'
            AND lower(so.status) NOT IN ('failed','rejected','cancelled') ORDER BY so.updated_at LIMIT 10''')]
    for oid in ids:
        fulfill(s,api,oid)


def capcut_category(s, api, cid, category_id):
    cat = s.custom_category(category_id)
    if category_id not in ('capcut','pandora_capcut') and not (cat and 'capcut' in cat[1].lower().replace(' ','')):
        return False
    with s.db() as c:
        rows = c.execute('''SELECT p.pid,p.name,a.service_id,a.variant_id FROM admin_products p
            JOIN supplier_api a ON a.pid=p.pid LEFT JOIN admin_categories cat ON cat.cid=p.category_id
            WHERE a.provider='pandora' AND a.service_id<>'' AND
            (lower(p.name) LIKE '%capcut%' OR lower(COALESCE(cat.name,'')) LIKE '%capcut%') ORDER BY p.rowid''').fetchall()
    if not rows:
        return False
    buttons, seen = [], set()
    for pid,label,product_id,variant_id in rows:
        identity = (product_id,variant_id)
        if identity in seen or not s.product_visible(pid):
            continue
        seen.add(identity)
        qty = s.product_stock(pid)
        buttons.append([s.btn(s.compact_name(pid,cid)+' | 💵 '+s.price(cid,pid,'USD')+' | '+s.compact_stock(qty),'options:'+pid,style='success' if s.in_stock(pid) else 'danger')])
    s.send(api,cid,s.category_heading('capcut',cid),s.kb(buttons+[s.nav(cid)]))
    return True


def legacy_review(s, api, actor, decision, rid):
    if actor != s.G['ADMIN_ID']:
        return
    with s.db() as c:
        prepare(c)
        prior = c.execute('SELECT order_id FROM payment_sources WHERE source=?',('adminpay:'+rid,)).fetchone()
    if prior:
        return approve(s,api,actor,decision,prior[0])
    old = s.G.get('PAYMENT_REVIEWS',{}).get(rid)
    if not old:
        return s.send(api,actor,'⚠️ الطلب القديم غير متاح. راجع الطلبات المحفوظة؛ لن يتم تسليمه يدويًا.')
    pid = old['product']
    qty = 1
    usd,sar = s.amount(pid,'USD'),s.amount(pid,'SAR')
    if pid in s.G.get('CAPCUT',{}):
        sar = Decimal(s.G['CAPCUT'][pid][2])
        usd = (sar / s.RATE).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)
    if usd is None or sar is None:
        return s.send(api,actor,'⚠️ تعذر تحديد سعر الطلب القديم. راجع بيانات الدفع.')
    oid = 'L'+rid
    with s.db() as c:
        c.execute('BEGIN IMMEDIATE')
        prepare(c)
        c.execute('INSERT OR IGNORE INTO orders VALUES (?,?,?,?,?,?,?,?)',(oid,old['customer'],pid,old['method'],str(usd),str(sar),'review',s.now_saudi()))
        c.execute('INSERT OR IGNORE INTO payment_sources VALUES (?,?)',('adminpay:'+rid,oid))
        c.execute('INSERT OR IGNORE INTO quantity_snapshots VALUES (?,?,?)',('order',oid,qty))
    s.G['PAYMENT_REVIEWS'].pop(rid,None)
    return approve(s,api,actor,decision,oid)


def wallet_pay(s, api, cid, pid):
    if not s.can_order(pid):
        return s.payments(api,cid,pid)
    message_id = getattr(EVENT,'message_id',None)
    if message_id is None and not needs_supplier(s,pid):
        return s._legacy_wallet_pay(api,cid,pid)
    if message_id is None:
        return s.send(api,cid,'افتح صفحة الدفع واضغط زر المحفظة لإتمام الطلب.')
    source = 'wallet:'+str(cid)+':'+str(message_id)+':'+pid
    cost,usd,_,_ = s.checkout_totals(cid,pid)
    qty = s.product_options.selected(s,cid,pid)
    oid = uuid.uuid4().hex[:10].upper()
    with s.db() as c:
        c.execute('BEGIN IMMEDIATE')
        prepare(c)
        old = c.execute('SELECT order_id FROM payment_sources WHERE source=?',(source,)).fetchone()
        if old:
            oid = old[0]
        else:
            row = c.execute('SELECT balance_sar FROM wallets WHERE cid=?',(cid,)).fetchone()
            balance = Decimal(row[0]) if row else Decimal('0')
            if balance < cost:
                return s.send(api,cid,'رصيد المحفظة غير كافٍ.')
            c.execute('INSERT OR REPLACE INTO wallets VALUES (?,?)',(cid,str(balance-cost)))
            c.execute('INSERT INTO orders VALUES (?,?,?,?,?,?,?,?)',(oid,cid,pid,'wallet',str(usd),str(cost),'paid',s.now_saudi()))
            c.execute('INSERT INTO quantity_snapshots VALUES (?,?,?)',('order',oid,qty))
            c.execute('INSERT INTO payment_sources VALUES (?,?)',(source,oid))
    if s.fulfill_paid_order(api,oid):
        return
    s.send(api,cid,'✅ تم الدفع من المحفظة. رقم الطلب: '+oid)
    if not old:
        s.send(api,s.G['ADMIN_ID'],'🛒 طلب محفظة مدفوع #'+oid)


def guard_delivery(s, oid):
    with s.db() as c:
        row = c.execute('SELECT pid FROM orders WHERE id=?',(oid,)).fetchone()
    return bool(row and needs_supplier(s,row[0]))


def install(s, namespace):
    import pandora_admin
    pandora_admin.install(s)
    old_action = namespace['action']
    def action(api,cid,value):
        import local_delivery
        if local_delivery.action(s,api,cid,value):
            return
        if pandora_admin.action(s,api,cid,value):
            return
        if cid == s.G['ADMIN_ID']:
            value = pandora_admin.price_action(s,value)
        prefix,_,arg = value.partition(':')
        if prefix in ('payreview','adminpay'):
            decision,_,oid = arg.partition(':')
            if prefix == 'adminpay':
                return legacy_review(s,api,cid,decision,oid)
            return approve(s,api,cid,decision,oid)
        if prefix == 'orderdeliver' and cid == s.G['ADMIN_ID'] and guard_delivery(s,arg):
            return s.send(api,cid,'⚠️ طلب Pandora: التسليم اليدوي محظور. استخدم إعادة محاولة التنفيذ التلقائي.',s.kb([[s.btn('🔄 إعادة المحاولة','supplierretry:'+arg)]]))
        if prefix == 'supplierretry' and cid == s.G['ADMIN_ID']:
            return fulfill(s,api,arg)
        if prefix == 'product' and capcut_category(s,api,cid,arg):
            return
        return old_action(api,cid,value)
    namespace['action'] = namespace['handle_action'] = action
    namespace['tick_supplier_orders'] = lambda api: tick(s,api)
    s._legacy_wallet_pay = s.pay_with_wallet
    s.pay_with_wallet = lambda api,cid,pid: wallet_pay(s,api,cid,pid)


def create_order(s, c, source, cid, pid, method, status, usd, sar, qty):
    """Insert order and its payment event in the SAME caller transaction."""
    prepare(c)
    previous = c.execute('SELECT order_id FROM payment_sources WHERE source=?',(source,)).fetchone()
    if previous:
        return previous[0]
    oid = uuid.uuid4().hex[:10].upper()
    c.execute('INSERT INTO orders VALUES (?,?,?,?,?,?,?,?)',(oid,cid,pid,method,str(usd),str(sar),status,s.now_saudi()))
    c.execute('INSERT INTO quantity_snapshots VALUES (?,?,?)',('order',oid,qty))
    c.execute('INSERT INTO payment_sources VALUES (?,?)',(source,oid))
    return oid


def receipt_order(s, cid, pid, method, usd, sar, message_id):
    qty = s.product_options.snapshot(s,'receipt',cid)
    with s.db() as c:
        c.execute('BEGIN IMMEDIATE')
        oid = create_order(s,c,'receipt:'+str(cid)+':'+str(message_id),cid,pid,method,'review',usd,sar,qty)
        c.execute('DELETE FROM receipts WHERE cid=?',(cid,))
        c.execute('DELETE FROM payment_quotes WHERE cid=? AND pid=? AND method=?',(cid,pid,method))
    return oid


def crypto_check(s, api, cid, invoice_ref):
    with s.db() as c:
        row = c.execute('SELECT pid,external_id,status,amount_usd FROM crypto_orders WHERE id=? AND cid=?',(invoice_ref,cid)).fetchone()
    if not row:
        return s.products(api,cid)
    pid,external,status,usd = row
    if status != 'paid' and not s.crypto_paid(external):
        return s.send(api,cid,'لم يصل الدفع بعد. أعد التحقق لاحقًا.')
    with s.db() as c:
        c.execute('BEGIN IMMEDIATE')
        prepare(c)
        if status == 'paid' and not c.execute('SELECT 1 FROM payment_sources WHERE source=?',('cryptopay:'+invoice_ref,)).fetchone():
            return s.send(api,cid,'✅ تمت معالجة هذه الفاتورة سابقًا. راجع الطلب المحفوظ لدى الإدارة.')
        qty = c.execute("SELECT qty FROM quantity_snapshots WHERE kind='crypto' AND key=?",(invoice_ref,)).fetchone()
        oid = create_order(s,c,'cryptopay:'+invoice_ref,cid,pid,'cryptopay','paid',usd,
                           (Decimal(usd)*s.RATE).quantize(Decimal('0.01')),qty[0] if qty else 1)
        c.execute('UPDATE crypto_orders SET status=? WHERE id=?',('paid',invoice_ref))
    if s.fulfill_paid_order(api,oid):
        return
    return s.send(api,cid,'✅ تم تأكيد الدفع. رقم الطلب: '+oid)

