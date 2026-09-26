"""Administrator-created manual payment methods, stored in the store database."""
import json


def prepare(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS payment_methods (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, holder TEXT NOT NULL, account TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1)')


def methods(s, active=False):
    with s.db() as conn:
        prepare(conn)
        return conn.execute('SELECT id,name,holder,account,active FROM payment_methods' + (' WHERE active=1' if active else '') + ' ORDER BY id').fetchall()


def get(s, key):
    return next((r for r in methods(s, True) if str(r[0]) == key), None)


def valid(s, method):
    return method.startswith('custom_') and get(s, method[7:]) is not None


def rows(s, pid):
    return [[s.btn('🏦 ' + r[1], f'custompay:{r[0]}:{pid}')] for r in methods(s, True)]


def clear(s, cid):
    with s.db() as conn:
        conn.execute("DELETE FROM admin_state WHERE cid=? AND action='payment_method'", (cid,))


def panel(s, api, cid):
    clear(s, cid)
    buttons = [[s.btn('➕ إضافة طريقة دفع', 'pm:new', style='success')]]
    for r in methods(s):
        buttons.append([s.btn(('🟢 ' if r[4] else '🔴 ') + r[1] + (' — إخفاء' if r[4] else ' — إظهار'), f'pm:toggle:{r[0]}')])
    buttons.append([s.btn('↩️ لوحة التحكم', 'admin')])
    s.send(api, cid, '🏦 <b>طرق الدفع</b>\nأضف طريقة دفع أو اضغط على طريقة لإظهارها أو إخفائها.', s.kb(buttons))


def details(s, row):
    return f'<b>{s.esc(row["name"])}</b>\n\nاسم الحساب / Account holder: <b>{s.esc(row["holder"])}</b>\nرقم الحساب أو الآيبان / Account or IBAN:\n<code>{s.esc(row["account"])}</code>'


def action(s, api, cid, value):
    if value.startswith('pm:'):
        if cid != s.G.get('ADMIN_ID'):
            return True
        if value == 'pm:new':
            with s.db() as conn:
                conn.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)', (cid, 'payment_method', '{}'))
            s.send(api, cid, 'أرسل اسم طريقة الدفع، مثل: بنك الراجحي.', s.kb([[s.btn('❌ إلغاء', 'pm:list')]]))
        elif value == 'pm:save':
            with s.db() as conn:
                prepare(conn)
                state = conn.execute("SELECT value FROM admin_state WHERE cid=? AND action='payment_method'", (cid,)).fetchone()
                data = json.loads(state[0]) if state else {}
                if all(data.get(k) for k in ('name', 'holder', 'account')):
                    conn.execute('INSERT INTO payment_methods(name,holder,account) VALUES (?,?,?)', (data['name'], data['holder'], data['account']))
                    conn.execute("DELETE FROM admin_state WHERE cid=? AND action='payment_method'", (cid,))
                    s.send(api, cid, '✅ تم حفظ طريقة الدفع، وستظهر للعملاء عند الدفع.')
            panel(s, api, cid)
        elif value.startswith('pm:toggle:'):
            with s.db() as conn:
                prepare(conn)
                conn.execute('UPDATE payment_methods SET active=1-active WHERE id=?', (value.split(':', 2)[2],))
            panel(s, api, cid)
        else:
            panel(s, api, cid)
        return True
    if value.startswith('custompay:'):
        parts = value.split(':', 2)
        row = get(s, parts[1]) if len(parts) == 3 else None
        if not row:
            s.send(api, cid, s.tr(cid, 'طريقة الدفع غير متاحة حاليًا. افتح خيارات الدفع مجددًا.', 'This payment method is unavailable. Reopen payment options.'))
            return True
        pid = parts[2]
        if not s.can_order(pid):
            s.payments(api, cid, pid)
            return True
        sar, usd, _, _ = s.checkout_totals(cid, pid)
        if sar == 0:
            s.pay_with_wallet(api, cid, pid)
            return True
        method = f'custom_{row[0]}'
        with s.db() as conn:
            conn.execute('INSERT OR REPLACE INTO payment_quotes VALUES (?,?,?,?,?)', (cid, pid, method, str(usd), str(sar)))
        text = details(s, dict(zip(('name','holder','account'), row[1:4])))
        text += '\n\n' + s.summary(cid, pid) + '\n\n' + s.tr(cid, 'بعد الدفع أرسل صورة إثبات التحويل. سيتم إرسال البيانات لك بعد التحقق من الدفع.', 'After paying, send a receipt photo. Your details will be sent after payment verification.')
        s.send(api, cid, text, s.kb([[s.btn(s.tr(cid, '✅ تم التحويل', '✅ Payment sent'), f'receipt:{method}:{pid}')], s.nav(cid, 'buy:' + pid)]))
        return True
    return False


def message(s, api, msg):
    cid = msg['chat']['id']
    if cid != s.G.get('ADMIN_ID'):
        return False
    with s.db() as conn:
        row = conn.execute("SELECT value FROM admin_state WHERE cid=? AND action='payment_method'", (cid,)).fetchone()
    if not row:
        return False
    text = (msg.get('text') or '').strip()
    if text.startswith('/'):
        clear(s, cid)
        return False
    data = json.loads(row[0])
    field = next((k for k in ('name', 'holder', 'account') if k not in data), None)
    if field is None:
        s.send(api, cid, 'اضغط حفظ أو إلغاء في المعاينة.')
        return True
    limit = 60 if field == 'name' else 200
    if not text or len(text) > limit:
        s.send(api, cid, f'أرسل نصًا من 1 إلى {limit} حرفًا.')
        return True
    data[field] = text
    with s.db() as conn:
        conn.execute('UPDATE admin_state SET value=? WHERE cid=? AND action=?', (json.dumps(data, ensure_ascii=False), cid, 'payment_method'))
    if field == 'account':
        s.send(api, cid, 'معاينة طريقة الدفع:\n\n' + details(s, data), s.kb([[s.btn('✅ حفظ', 'pm:save', style='success'), s.btn('❌ إلغاء', 'pm:list')]]))
    else:
        s.send(api, cid, 'أرسل اسم صاحب الحساب.' if field == 'name' else 'أرسل رقم الحساب أو الآيبان (ويمكنك كتابة الاثنين).', s.kb([[s.btn('❌ إلغاء', 'pm:list')]]))
    return True
