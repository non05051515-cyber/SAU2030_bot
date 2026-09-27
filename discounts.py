"""Fixed SAR coupons. Prices are calculated server-side for every checkout."""
import re
import product_options
from decimal import Decimal, ROUND_HALF_UP


def prepare(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS discount_codes (code TEXT PRIMARY KEY, sar TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1)')
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
        row = conn.execute('SELECT d.code,d.sar FROM customer_discounts c JOIN discount_codes d ON c.code=d.code WHERE c.cid=? AND c.pid IN (?, ?) AND d.active=1 ORDER BY (c.pid=?) DESC LIMIT 1', (cid, pid, '*', pid)).fetchone()
    if not row or original is None:
        return original, usd, Decimal('0'), None
    discount = min(original, Decimal(row[1]))
    sar = max(Decimal('0'), original - discount)
    usd = (sar / s.RATE).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    return sar, usd, discount, row[0]


def panel(s, api, cid):
    if cid != s.G['ADMIN_ID']:
        return
    with s.db() as conn:
        conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
        rows = conn.execute('SELECT code,sar,active FROM discount_codes ORDER BY rowid DESC').fetchall()
    buttons = [[s.btn('➕ إنشاء كود خصم', 'couponadmin:new')]]
    for code, sar, active in rows:
        buttons.append([s.btn(f'{code} — {sar} ر.س — ' + ('تعطيل' if active else 'تفعيل'), 'couponadmin:toggle:' + code)])
    buttons.append([s.btn('↩️ لوحة التحكم', 'admin')])
    # Keep large collections within Telegram message/keyboard limits.
    for offset in range(0, len(buttons), 40):
        s.send(api, cid, '🎟 <b>أكواد الخصم</b>\nالخصم مبلغ ثابت بالريال لكل طلب. الكود متاح لجميع العملاء حتى تعطيله.', s.kb(buttons[offset:offset+40]))


def action(s, api, cid, value):
    if value.startswith('couponadmin:'):
        if cid != s.G['ADMIN_ID']:
            return True
        arg = value.split(':', 1)[1]
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
        conn.execute("DELETE FROM admin_state WHERE cid=? AND action IN ('coupon_name','coupon_amount')", (cid,))
    return False


def message(s, api, message):
    cid = message['chat']['id']
    raw = (message.get('text') or '').strip()
    if raw.startswith('/') or raw in s.G.get('MENU', {}):
        with s.db() as conn:
            conn.execute('DELETE FROM discount_input WHERE cid=?', (cid,))
            conn.execute("DELETE FROM admin_state WHERE cid=? AND action IN ('coupon_name','coupon_amount')", (cid,))
        return False
    with s.db() as conn:
        state = conn.execute("SELECT action,value FROM admin_state WHERE cid=? AND action IN ('coupon_name','coupon_amount')", (cid,)).fetchone() if cid == s.G['ADMIN_ID'] else None
        pending = conn.execute('SELECT pid FROM discount_input WHERE cid=?', (cid,)).fetchone()
    if state:
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
                s.send(api, cid, 'أرسل مبلغ الخصم بالريال، مثال: 5 أو 10.50', s.kb([[s.btn('إلغاء', 'couponadmin:list')]]))
        else:
            try:
                value = Decimal(raw.translate(str.maketrans('٠١٢٣٤٥٦٧٨٩٫', '0123456789.')).replace(',', '.'))
                if not value.is_finite() or not 0 < value <= 1000000 or value != value.quantize(Decimal('.01')):
                    raise ValueError()
            except Exception:
                s.send(api, cid, 'أرسل مبلغًا أكبر من صفر وبحد أقصى منزلتين عشريتين، مثل 5.50')
                return True
            with s.db() as conn:
                conn.execute('INSERT INTO discount_codes(code,sar) VALUES (?,?)', (state[1], str(value)))
                conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
            s.send(api, cid, f'✅ تم إنشاء الكود <code>{state[1]}</code> بخصم {value:.2f} ر.س لكل طلب.')
            panel(s, api, cid)
        return True
    if not pending:
        return False
    pid = pending[0]
    with s.db() as conn:
        code = raw.upper()
        found = conn.execute('SELECT 1 FROM discount_codes WHERE code=? AND active=1', (code,)).fetchone()
        if found and (pid == '*' or s.can_order(pid)):
            if pid == '*':
                conn.execute('DELETE FROM customer_discounts WHERE cid=?', (cid,))
            conn.execute('INSERT OR REPLACE INTO customer_discounts VALUES (?,?,?)', (cid, pid, code))
            conn.execute('DELETE FROM discount_input WHERE cid=?', (cid,))
        else:
            found = None
    if not found:
        s.send(api, cid, s.tr(cid, '❌ الكود غير صحيح أو غير فعّال. جرّب كودًا آخر.', '❌ Invalid or inactive code. Try another code.'), s.kb([[s.btn(s.tr(cid, 'إلغاء', 'Cancel'), ('home' if pid == '*' else 'buy:' + pid))]]))
        return True
    s.send(api, cid, s.tr(cid, '✅ تم تطبيق كود الخصم.', '✅ Discount code applied.'))
    (s.home(api, cid) if pid == '*' else s.payments(api, cid, pid))
    return True
