"""Fixed SAR coupons. Prices are calculated server-side for every checkout."""
import re
import json
import product_options
from decimal import Decimal, ROUND_HALF_UP


def prepare(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS discount_codes (code TEXT PRIMARY KEY, sar TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1)')
    if 'scope' not in {r[1] for r in conn.execute('PRAGMA table_info(discount_codes)')}:
        conn.execute("ALTER TABLE discount_codes ADD COLUMN scope TEXT NOT NULL DEFAULT 'all'")
    conn.execute('CREATE TABLE IF NOT EXISTS discount_products (code TEXT NOT NULL, pid TEXT NOT NULL, PRIMARY KEY(code,pid))')
    if 'sar' not in {r[1] for r in conn.execute('PRAGMA table_info(discount_products)')}:
        conn.execute('ALTER TABLE discount_products ADD COLUMN sar TEXT')
    conn.execute('CREATE TABLE IF NOT EXISTS customer_discounts (cid INTEGER NOT NULL, pid TEXT NOT NULL, code TEXT NOT NULL, PRIMARY KEY(cid,pid))')
    conn.execute('CREATE TABLE IF NOT EXISTS discount_input (cid INTEGER PRIMARY KEY, pid TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS payment_quotes (cid INTEGER NOT NULL, pid TEXT NOT NULL, method TEXT NOT NULL, usd TEXT NOT NULL, sar TEXT NOT NULL, PRIMARY KEY(cid,pid,method))')


def totals(s, cid, pid):
    qty = product_options.selected(s, cid, pid)
    original = s.amount(pid, 'SAR')
    usd = s.amount(pid, 'USD')
    if original is not None: original *= qty
    if usd is not None: usd *= qty
    with s.db() as conn:
        row = conn.execute("SELECT d.code,COALESCE(p.sar,d.sar) FROM customer_discounts c JOIN discount_codes d ON c.code=d.code LEFT JOIN discount_products p ON p.code=d.code AND p.pid=? WHERE c.cid=? AND c.pid IN (?, ?) AND d.active=1 AND (d.scope='all' OR p.pid IS NOT NULL) ORDER BY (c.pid=?) DESC LIMIT 1", (pid, cid, pid, '*', pid)).fetchone()
    if not row or original is None:
        return original, usd, Decimal('0'), None
    discount = min(original, Decimal(row[1]))
    sar = max(Decimal('0'), original - discount)
    usd = (sar / s.RATE).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    return sar, usd, discount, row[0]


def product_ids(s):
    with s.db() as conn:
        categories = list(s.G['PRODUCTS']) + [r[0] for r in conn.execute('SELECT cid FROM admin_categories')]
        ids = [r[0] for r in conn.execute('SELECT pid FROM admin_products ORDER BY rowid')]
    for category in categories:
        ids.extend(s.admin_category_product_ids(category))
    return list(dict.fromkeys(ids))


def scope_panel(s, api, cid, draft, page=0):
    ids = draft.setdefault('available', product_ids(s))
    page = max(0, min(page, max(0, (len(ids)-1)//15)))
    variable = draft.get('mode') == 'variable'
    rows = [] if variable else [[s.btn('كل المنتجات', 'couponadmin:all'), s.btn('منتجات محددة', 'couponadmin:select')], [s.btn('خصم مختلف لكل منتج', 'couponadmin:variable')]]
    if draft.get('scope') == 'selected':
        selected = set(draft.get('products', []))
        for index in range(page*15, min(len(ids), (page+1)*15)):
            pid = ids[index]
            label = ('✓ ' if pid in selected else '') + s.name(pid, cid)
            if variable and pid in selected: label += ' — خصم ' + draft['amounts'][pid] + ' ر.س'
            rows.append([s.btn(label, 'couponadmin:pick:' + str(index) + ':' + str(page))])
        nav = []
        if page: nav.append(s.btn('السابق', 'couponadmin:page:' + str(page-1)))
        if (page+1)*15 < len(ids): nav.append(s.btn('التالي', 'couponadmin:page:' + str(page+1)))
        if nav: rows.append(nav)
        rows.append([s.btn('حفظ الكود (' + str(len(selected)) + ' منتجات)', 'couponadmin:save')])
    rows.append([s.btn('إلغاء', 'couponadmin:list')])
    prompt = 'اختر منتجًا واكتب مبلغ خصمه بالريال، ثم كرر لبقية المنتجات. اضغط المنتج المحدد لتعديل خصمه أو إزالته، ثم احفظ الكود.' if variable else 'اختر نطاق كود الخصم: جميع المنتجات، أو حدد منتجًا واحدًا أو أكثر ثم اضغط حفظ.'
    s.send(api, cid, prompt, s.kb(rows))


def save_coupon(s, api, cid, draft):
    scope = draft.get('scope', 'all')
    selected = list(dict.fromkeys(draft.get('products', [])))
    if scope == 'selected' and (not selected or any(pid not in product_ids(s) for pid in selected)):
        s.send(api, cid, 'حدد منتجًا واحدًا على الأقل من المنتجات الموجودة قبل الحفظ.')
        scope_panel(s, api, cid, draft)
        return
    amounts = draft.get('amounts', {}) if draft.get('mode') == 'variable' else {}
    if draft.get('mode') == 'variable' and any(pid not in amounts for pid in selected):
        s.send(api, cid, 'حدد مبلغ الخصم لكل منتج قبل الحفظ.')
        return
    with s.db() as conn:
        if not draft.get('edit_code') and conn.execute('SELECT 1 FROM discount_codes WHERE code=?', (draft['code'],)).fetchone():
            s.send(api, cid, 'هذا الكود موجود مسبقًا. أنشئ كودًا باسم آخر.')
            return
        if draft.get('edit_code'):
            conn.execute('UPDATE discount_codes SET scope=? WHERE code=?', (scope, draft['code']))
            conn.execute('DELETE FROM discount_products WHERE code=?', (draft['code'],))
        else:
            conn.execute('INSERT INTO discount_codes(code,sar,scope) VALUES (?,?,?)', (draft['code'], draft['sar'], scope))
        conn.executemany('INSERT INTO discount_products(code,pid,sar) VALUES (?,?,?)', [(draft['code'], pid, amounts.get(pid)) for pid in selected] if scope == 'selected' else [])
        conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
    label = 'كل المنتجات' if scope == 'all' else str(len(selected)) + ' منتجات محددة'
    amount_label = 'بخصم مختلف لكل منتج' if draft.get('mode') == 'variable' else f"بخصم {draft['sar']} ر.س"
    verb = 'تم تحديث' if draft.get('edit_code') else 'تم إنشاء'
    s.send(api, cid, f"✅ {verb} الكود <code>{draft['code']}</code> {amount_label} — {label}.")
    panel(s, api, cid)


def panel(s, api, cid):
    if cid != s.G['ADMIN_ID']:
        return
    with s.db() as conn:
        conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
        rows = conn.execute('SELECT code,sar,active,scope,EXISTS(SELECT 1 FROM discount_products p WHERE p.code=d.code AND p.sar IS NOT NULL) FROM discount_codes d ORDER BY rowid DESC').fetchall()
    buttons = [[s.btn('➕ إنشاء كود خصم', 'couponadmin:new')]]
    for code, sar, active, scope, variable in rows:
        label = 'الكل' if scope == 'all' else 'منتجات محددة'
        amount_label = 'خصم مختلف لكل منتج' if variable else f'{sar} ر.س'
        buttons.append([s.btn(f'{code} — {amount_label} — {label}', 'couponadmin:manage:' + code),
                        s.btn('تعطيل' if active else 'تفعيل', 'couponadmin:toggle:' + code)])
    buttons.append([s.btn('↩️ لوحة التحكم', 'admin')])
    # Keep large collections within Telegram message/keyboard limits.
    for offset in range(0, len(buttons), 40):
        s.send(api, cid, '🎟 <b>أكواد الخصم</b>\nاختر مبلغ خصم موحدًا أو مبلغًا مختلفًا لكل منتج. الخصم بالريال لكل طلب، والكود متاح لجميع العملاء حتى تعطيله.', s.kb(buttons[offset:offset+40]))


def action(s, api, cid, value):
    if value.startswith('couponadmin:'):
        if cid != s.G['ADMIN_ID']:
            return True
        arg = value.split(':', 1)[1]
        if arg.startswith('manage:'):
            code = arg[7:]
            with s.db() as conn:
                row = conn.execute('SELECT sar,active,scope FROM discount_codes WHERE code=?', (code,)).fetchone()
                count = conn.execute('SELECT COUNT(*) FROM discount_products WHERE code=?', (code,)).fetchone()[0]
            if not row:
                panel(s, api, cid)
                return True
            buttons = []
            if row[2] == 'selected' or count:
                buttons.append([s.btn('➕ إضافة/تعديل منتجات الكود', 'couponadmin:extend:' + code)])
            buttons.extend([[s.btn('تعطيل الكود' if row[1] else 'تفعيل الكود', 'couponadmin:toggle:' + code)],
                            [s.btn('↩️ رجوع للأكواد', 'couponadmin:list')]])
            s.send(api, cid, f'🎟 <b>الكود {s.esc(code)}</b>\nالنطاق: {"منتجات محددة" if row[2] == "selected" else "كل المنتجات"}\nعدد المنتجات المحددة: {count}\nيمكنك إضافة منتجات جديدة أو تعديل خصم منتج موجود، وستبقى خصومات المنتجات الحالية محفوظة.', s.kb(buttons))
            return True
        if arg.startswith('extend:'):
            code = arg[7:]
            with s.db() as conn:
                row = conn.execute('SELECT sar,scope FROM discount_codes WHERE code=?', (code,)).fetchone()
                products = conn.execute('SELECT pid,sar FROM discount_products WHERE code=?', (code,)).fetchall()
            if not row:
                panel(s, api, cid)
                return True
            draft = {'code': code, 'edit_code': code, 'sar': row[0], 'scope': 'selected',
                     'mode': 'variable', 'products': [p[0] for p in products],
                     'amounts': {p[0]: p[1] for p in products if p[1] is not None},
                     'available': product_ids(s)}
            with s.db() as conn:
                conn.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)', (cid, 'coupon_scope', json.dumps(draft)))
            scope_panel(s, api, cid, draft)
            return True
        if arg == 'variable':
            with s.db() as conn:
                state = conn.execute("SELECT action,value FROM admin_state WHERE cid=? AND action IN ('coupon_amount','coupon_scope')", (cid,)).fetchone()
            if not state:
                panel(s, api, cid)
                return True
            draft = json.loads(state[1]) if state[0] == 'coupon_scope' else {'code': state[1], 'available': product_ids(s)}
            if not draft.get('edit_code'):
                draft.update(scope='selected', mode='variable', sar='0', products=[], amounts={})
            with s.db() as conn:
                conn.execute('UPDATE admin_state SET action=?,value=? WHERE cid=?', ('coupon_scope', json.dumps(draft), cid))
            scope_panel(s, api, cid, draft)
            return True
        if arg in ('backproducts', 'removeproduct'):
            with s.db() as conn:
                state = conn.execute("SELECT value FROM admin_state WHERE cid=? AND action='coupon_product_amount'", (cid,)).fetchone()
                if state:
                    draft = json.loads(state[0])
                    pid = draft.pop('pending_product')
                    page = draft.pop('pending_page', 0)
                    if arg == 'removeproduct':
                        if pid in draft['products']: draft['products'].remove(pid)
                        draft['amounts'].pop(pid, None)
                    conn.execute('UPDATE admin_state SET action=?,value=? WHERE cid=?', ('coupon_scope', json.dumps(draft), cid))
            if state: scope_panel(s, api, cid, draft, page)
            else: panel(s, api, cid)
            return True
        if arg in ('all', 'select', 'save') or arg.startswith(('pick:', 'page:')):
            with s.db() as conn:
                state = conn.execute("SELECT value FROM admin_state WHERE cid=? AND action='coupon_scope'", (cid,)).fetchone()
            if not state:
                panel(s, api, cid)
                return True
            draft = json.loads(state[0])
            page = 0
            if draft.get('mode') == 'variable' and arg in ('all', 'select'):
                scope_panel(s, api, cid, draft)
                return True
            if arg == 'all':
                draft['scope'] = 'all'
                save_coupon(s, api, cid, draft)
                return True
            if arg == 'save':
                if draft.get('scope') == 'selected': save_coupon(s, api, cid, draft)
                else: scope_panel(s, api, cid, draft)
                return True
            if arg == 'select': draft['scope'] = 'selected'
            elif arg.startswith('page:'):
                page = int(arg.split(':')[1])
            else:
                _, index, page = arg.split(':')
                page = int(page)
                ids = draft.get('available', [])
                index = int(index)
                if draft.get('scope') == 'selected' and 0 <= index < len(ids):
                    selected = draft.setdefault('products', [])
                    pid = ids[index]
                    if draft.get('mode') == 'variable':
                        draft.update(pending_product=pid, pending_page=page)
                        with s.db() as conn:
                            conn.execute('UPDATE admin_state SET action=?,value=? WHERE cid=?', ('coupon_product_amount', json.dumps(draft), cid))
                        rows = [[s.btn('رجوع للمنتجات', 'couponadmin:backproducts')]]
                        if pid in selected: rows.insert(0, [s.btn('إزالة المنتج من الكود', 'couponadmin:removeproduct')])
                        s.send(api, cid, 'أرسل مبلغ الخصم بالريال لهذا المنتج: <b>' + s.esc(s.name(pid, cid)) + '</b>\nمثال: 3 أو 4.50', s.kb(rows))
                        return True
                    if pid in selected: selected.remove(pid)
                    else: selected.append(pid)
            with s.db() as conn:
                conn.execute('UPDATE admin_state SET value=? WHERE cid=?', (json.dumps(draft), cid))
            scope_panel(s, api, cid, draft, page)
            return True
        if arg == 'new':
            with s.db() as conn:
                conn.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)', (cid, 'coupon_name', ''))
            s.send(api, cid, 'أرسل اسم الكود بالإنجليزي أو الأرقام، مثل VEXA10 (من 1 إلى 24 حرفًا).', s.kb([[s.btn('إلغاء', 'couponadmin:list')]]))
        else:
            if arg.startswith('toggle:'):
                with s.db() as conn:
                    conn.execute('UPDATE discount_codes SET active=1-active WHERE code=?', (arg[7:],))
            panel(s, api, cid)
        return True
    if value.startswith(('coupon:', 'couponremove:')):
        prefix, pid = value.split(':', 1)
        pid = s.LEGACY.get(pid, pid)
        if pid != '*' and not s.can_order(pid):
            (s.home(api, cid) if pid == '*' else s.payments(api, cid, pid))
            return True
        with s.db() as conn:
            conn.execute('DELETE FROM receipts WHERE cid=?', (cid,))
            conn.execute('DELETE FROM payment_quotes WHERE cid=?', (cid,))
            if prefix == 'couponremove':
                conn.execute('DELETE FROM customer_discounts WHERE cid=?', (cid,))
                conn.execute('DELETE FROM discount_input WHERE cid=?', (cid,))
            else:
                conn.execute('INSERT OR REPLACE INTO discount_input VALUES (?,?)', (cid, pid))
        if prefix == 'couponremove':
            (s.home(api, cid) if pid == '*' else s.payments(api, cid, pid))
        else:
            s.send(api, cid, s.tr(cid, '🎟 أرسل كود الخصم الآن:', '🎟 Enter your discount code:'), s.kb([[s.btn(s.tr(cid, 'إلغاء', 'Cancel'), ('home' if pid == '*' else 'buy:' + pid))]]))
        return True
    # Navigating away cancels text input, but keeps the selected code for this product.
    with s.db() as conn:
        conn.execute('DELETE FROM discount_input WHERE cid=?', (cid,))
        conn.execute("DELETE FROM admin_state WHERE cid=? AND action IN ('coupon_name','coupon_amount','coupon_scope','coupon_product_amount')", (cid,))
    return False


def message(s, api, message):
    cid = message['chat']['id']
    raw = (message.get('text') or '').strip()
    if raw.startswith('/') or raw in s.G.get('MENU', {}):
        with s.db() as conn:
            conn.execute('DELETE FROM discount_input WHERE cid=?', (cid,))
            conn.execute("DELETE FROM admin_state WHERE cid=? AND action IN ('coupon_name','coupon_amount','coupon_scope','coupon_product_amount')", (cid,))
        return False
    with s.db() as conn:
        state = conn.execute("SELECT action,value FROM admin_state WHERE cid=? AND action IN ('coupon_name','coupon_amount','coupon_scope','coupon_product_amount')", (cid,)).fetchone() if cid == s.G['ADMIN_ID'] else None
        pending = conn.execute('SELECT pid FROM discount_input WHERE cid=?', (cid,)).fetchone()
    if state:
        if state[0] == 'coupon_scope':
            scope_panel(s, api, cid, json.loads(state[1]))
            return True
        if state[0] == 'coupon_name':
            code = raw.upper()
            if not re.fullmatch(r'[A-Z0-9_-]{1,24}', code):
                s.send(api, cid, 'استخدم حروفًا إنجليزية وأرقامًا فقط، ويمكن استخدام - أو _، بحد أقصى 24 حرفًا.')
                return True
            with s.db() as conn:
                exists = conn.execute('SELECT 1 FROM discount_codes WHERE code=?', (code,)).fetchone()
                if not exists:
                    conn.execute('UPDATE admin_state SET action=?,value=? WHERE cid=?', ('coupon_amount', code, cid))
            if exists:
                s.send(api, cid, 'هذا الكود موجود مسبقًا. أرسل اسمًا آخر.')
            else:
                s.send(api, cid, 'أرسل مبلغ الخصم الموحد بالريال، مثال: 5 أو 10.50، أو اضغط «خصم مختلف لكل منتج».', s.kb([[s.btn('خصم مختلف لكل منتج', 'couponadmin:variable')], [s.btn('إلغاء', 'couponadmin:list')]]))
        else:
            try:
                value = Decimal(raw.translate(str.maketrans('٠١٢٣٤٥٦٧٨٩٫', '0123456789.')).replace(',', '.'))
                if not value.is_finite() or not 0 < value <= 1000000 or value != value.quantize(Decimal('.01')):
                    raise ValueError()
            except Exception:
                s.send(api, cid, 'أرسل مبلغًا أكبر من صفر وبحد أقصى منزلتين عشريتين، مثل 5.50')
                return True
            if state[0] == 'coupon_product_amount':
                draft = json.loads(state[1])
                pid = draft.pop('pending_product')
                page = draft.pop('pending_page', 0)
                if pid not in draft['products']: draft['products'].append(pid)
                draft['amounts'][pid] = str(value)
            else:
                draft = {'code': state[1], 'sar': str(value), 'products': [], 'available': product_ids(s)}
                page = 0
            with s.db() as conn:
                conn.execute('UPDATE admin_state SET action=?,value=? WHERE cid=?', ('coupon_scope', json.dumps(draft), cid))
            scope_panel(s, api, cid, draft, page)
        return True
    if not pending:
        return False
    pid = pending[0]
    with s.db() as conn:
        code = raw.upper()
        found = conn.execute("SELECT 1 FROM discount_codes d WHERE code=? AND active=1 AND (?='*' OR d.scope='all' OR EXISTS (SELECT 1 FROM discount_products p WHERE p.code=d.code AND p.pid=?))", (code, pid, pid)).fetchone()
        if found and (pid == '*' or s.can_order(pid)):
            if pid == '*':
                conn.execute('DELETE FROM customer_discounts WHERE cid=?', (cid,))
            conn.execute('INSERT OR REPLACE INTO customer_discounts VALUES (?,?,?)', (cid, pid, code))
            conn.execute('DELETE FROM discount_input WHERE cid=?', (cid,))
        else:
            found = None
    if not found:
        s.send(api, cid, s.tr(cid, '❌ الكود غير صحيح أو غير فعّال أو لا يشمل هذا المنتج. جرّب كودًا آخر.', '❌ Invalid, inactive, or not valid for this product. Try another code.'), s.kb([[s.btn(s.tr(cid, 'إلغاء', 'Cancel'), ('home' if pid == '*' else 'buy:' + pid))]]))
        return True
    s.send(api, cid, s.tr(cid, '✅ تم حفظ كود الخصم وسيطبق على المنتجات المشمولة به عند الشراء.', '✅ Discount code saved. It applies to eligible products at checkout.'))
    (s.home(api, cid) if pid == '*' else s.payments(api, cid, pid))
    return True


