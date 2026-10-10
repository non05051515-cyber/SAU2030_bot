"""Manual VEXA customer-points awards with per-order duplicate protection."""
import json
from datetime import datetime, timezone


def install(store, namespace):
    db = store.db
    G = store.G

    def ensure_schema():
        with db() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS customer_points (
                cid INTEGER PRIMARY KEY, balance INTEGER NOT NULL DEFAULT 0)''')
            conn.execute('''CREATE TABLE IF NOT EXISTS points_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cid INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                order_id TEXT UNIQUE,
                reason TEXT NOT NULL,
                admin_id INTEGER NOT NULL,
                created_at TEXT NOT NULL)''')

    def points_balance(cid):
        ensure_schema()
        with db() as conn:
            row = conn.execute('SELECT balance FROM customer_points WHERE cid=?', (cid,)).fetchone()
        return int(row[0]) if row else 0

    def known_users():
        try:
            return {int(x) for x in json.loads(store.USERS_PATH.read_text(encoding='utf-8'))}
        except Exception:
            return set()

    def set_state(cid, action, value=''):
        with db() as conn:
            conn.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)', (cid, action, value))

    def clear_state(cid):
        with db() as conn:
            conn.execute("DELETE FROM admin_state WHERE cid=? AND action IN ('loyalty_search','loyalty_amount')", (cid,))

    def admin_points(api, cid):
        if cid != G['ADMIN_ID']:
            return store.home(api, cid)
        clear_state(cid)
        store.send(api, cid, '⭐ <b>نقاط العملاء</b>\n\nابحث عن العميل برقم العضوية، أو اختر طلبًا مدفوعًا لإضافة النقاط لصاحبه.',
                   store.kb([[store.btn('🔎 البحث برقم العميل', 'loyalty:search', style='primary')],
                             [store.btn('🧾 اختيار طلب مدفوع', 'loyalty:orders', style='success')],
                             [store.btn('↩️ لوحة الإدارة', 'admin')]]))

    def customer_page(api, cid, customer_id, note=''):
        if cid != G['ADMIN_ID']:
            return
        balance = points_balance(customer_id)
        with db() as conn:
            recent = conn.execute('SELECT amount,reason,created_at FROM points_ledger WHERE cid=? ORDER BY id DESC LIMIT 5', (customer_id,)).fetchall()
        text = f'👤 <b>ملف نقاط العميل</b>\n\nرقم العضوية: <code>{customer_id}</code>\n⭐ رصيد النقاط: <b>{balance}</b>'
        if note:
            text += '\n\n' + note
        if recent:
            text += '\n\n<b>آخر الإضافات:</b>'
            text += ''.join(f'\n➕ {amount} نقطة — {store.esc(reason)}' for amount, reason, _ in recent)
        store.send(api, cid, text,
                   store.kb([[store.btn('➕ إضافة نقاط', f'loyalty:add:user:{customer_id}', style='success')],
                             [store.btn('↩️ نقاط العملاء', 'admin:points')]]))

    def order_list(api, cid):
        if cid != G['ADMIN_ID']:
            return
        with db() as conn:
            rows = conn.execute('''SELECT id,cid,pid,status FROM orders
                WHERE status IN ('paid','delivered') ORDER BY rowid DESC LIMIT 20''').fetchall()
        if not rows:
            return store.send(api, cid, 'لا توجد طلبات مدفوعة يمكن إضافة النقاط عليها الآن.',
                              store.kb([[store.btn('↩️ رجوع', 'admin:points')]]))
        buttons = []
        for order_id, customer_id, pid, status in rows:
            with db() as conn:
                awarded = conn.execute('SELECT 1 FROM points_ledger WHERE order_id=?', (order_id,)).fetchone()
            label = f"{'✅ ' if awarded else '⭐ '}{store.name(pid, cid)} — #{order_id} — {customer_id}"
            buttons.append([store.btn(label[:60], 'loyalty:order:' + order_id,
                                      style='primary' if awarded else 'success')])
        buttons.append([store.btn('↩️ رجوع', 'admin:points')])
        store.send(api, cid, '🧾 <b>اختر طلبًا مدفوعًا</b>\n\n✅ تعني أن نقاط هذا الطلب أُضيفت مسبقًا.', store.kb(buttons))

    def order_page(api, cid, order_id):
        if cid != G['ADMIN_ID']:
            return
        with db() as conn:
            row = conn.execute('SELECT cid,pid,status FROM orders WHERE id=?', (order_id,)).fetchone()
            awarded = conn.execute('SELECT amount FROM points_ledger WHERE order_id=?', (order_id,)).fetchone()
        if not row:
            return store.send(api, cid, '⚠️ الطلب غير موجود.', store.kb([[store.btn('↩️ رجوع', 'admin:points')]]))
        customer_id, pid, status = row
        if status not in ('paid', 'delivered'):
            return store.send(api, cid, '⚠️ تقدر تضيف النقاط بعد تأكيد الدفع فقط.',
                              store.kb([[store.btn('↩️ الطلبات', 'loyalty:orders')]]))
        text = f'🧾 <b>طلب #{store.esc(order_id)}</b>\n{store.esc(store.name(pid, cid))}\nالعميل: {store.customer_link(customer_id)}\n⭐ رصيد العميل: <b>{points_balance(customer_id)}</b>'
        rows = []
        if awarded:
            text += f'\n\n✅ أُضيفت <b>{awarded[0]}</b> نقطة لهذا الطلب مسبقًا.'
        else:
            rows.append([store.btn('➕ إضافة نقاط لهذا الطلب', f'loyalty:add:order:{order_id}', style='success')])
        rows += [[store.btn('👤 ملف نقاط العميل', f'loyalty:customer:{customer_id}')],
                 [store.btn('↩️ الطلبات', 'loyalty:orders')]]
        store.send(api, cid, text, store.kb(rows))

    def begin_add(api, cid, kind, target):
        if cid != G['ADMIN_ID']:
            return
        if kind == 'order':
            with db() as conn:
                row = conn.execute('SELECT cid,status FROM orders WHERE id=?', (target,)).fetchone()
                already = conn.execute('SELECT 1 FROM points_ledger WHERE order_id=?', (target,)).fetchone()
            if not row or row[1] not in ('paid', 'delivered'):
                return store.send(api, cid, '⚠️ الطلب غير موجود أو لم يُؤكد دفعه بعد.')
            if already:
                return order_page(api, cid, target)
            customer_id = row[0]
        else:
            customer_id = int(target)
        payload = json.dumps({'kind': kind, 'target': target, 'customer_id': customer_id})
        set_state(cid, 'loyalty_amount', payload)
        store.send(api, cid, f'أرسل عدد النقاط لإضافتها للعميل <code>{customer_id}</code>.\n\nأرسل رقمًا صحيحًا أكبر من صفر، أو اضغط إلغاء.',
                   store.kb([[store.btn('❌ إلغاء', 'admin:points')]]))

    def award(customer_id, amount, admin_id, reason, order_id=None):
        ensure_schema()
        with db() as conn:
            if order_id:
                if conn.execute('SELECT 1 FROM points_ledger WHERE order_id=?', (order_id,)).fetchone():
                    return False
            conn.execute('INSERT OR IGNORE INTO customer_points(cid,balance) VALUES (?,0)', (customer_id,))
            conn.execute('INSERT INTO points_ledger(cid,amount,order_id,reason,admin_id,created_at) VALUES (?,?,?,?,?,?)',
                         (customer_id, amount, order_id, reason, admin_id, datetime.now(timezone.utc).isoformat()))
            conn.execute('UPDATE customer_points SET balance=balance+? WHERE cid=?', (amount, customer_id))
        return True

    def receive(api, message):
        cid = message.get('chat', {}).get('id')
        if cid != G['ADMIN_ID']:
            return False
        with db() as conn:
            row = conn.execute("SELECT action,value FROM admin_state WHERE cid=? AND action IN ('loyalty_search','loyalty_amount')", (cid,)).fetchone()
        if not row:
            return False
        text = (message.get('text') or '').strip()
        if text.startswith('/') or text in G.get('MENU', {}):
            clear_state(cid)
            return False
        if row[0] == 'loyalty_search':
            try:
                customer_id = int(text)
            except ValueError:
                store.send(api, cid, 'أرسل رقم آيدي تيليجرام صحيحًا فقط.')
                return True
            if customer_id not in known_users():
                store.send(api, cid, 'هذا الآيدي غير مسجل في البوت. تحقق من الرقم وحاول مرة أخرى.')
                return True
            clear_state(cid)
            customer_page(api, cid, customer_id)
            return True
        try:
            amount = int(text)
        except ValueError:
            store.send(api, cid, 'أرسل عدد النقاط كرقم صحيح فقط.')
            return True
        if amount <= 0 or amount > 100000:
            store.send(api, cid, 'أرسل عددًا من 1 إلى 100000 نقطة.')
            return True
        data = json.loads(row[1])
        clear_state(cid)
        order_id = data['target'] if data['kind'] == 'order' else None
        reason = f"طلب #{order_id}" if order_id else 'إضافة يدوية من الإدارة'
        try:
            saved = award(data['customer_id'], amount, cid, reason, order_id)
        except Exception:
            saved = False
        if not saved:
            return store.send(api, cid, '⚠️ تعذرت الإضافة أو سبق تسجيل نقاط هذا الطلب.')
        customer_id = data['customer_id']
        balance = points_balance(customer_id)
        store.send(api, customer_id, f'⭐ تمت إضافة <b>{amount}</b> نقطة إلى رصيدك في VEXA STORE.\nرصيدك الحالي: <b>{balance}</b> نقطة.')
        store.send(api, cid, f'✅ تمت إضافة <b>{amount}</b> نقطة للعميل <code>{customer_id}</code>.\nالرصيد الحالي: <b>{balance}</b> نقطة.')
        customer_page(api, cid, customer_id)
        return True

    old_action = namespace['action']
    def action(api, cid, value):
        if value == 'admin:points':
            return admin_points(api, cid)
        if value == 'admin:homeicons':
            return store.admin_home_text_icons(api, cid)
        if value == 'loyalty:search':
            if cid != G['ADMIN_ID']:
                return
            set_state(cid, 'loyalty_search')
            return store.send(api, cid, 'أرسل آيدي العميل للبحث عن رصيد نقاطه.',
                              store.kb([[store.btn('❌ إلغاء', 'admin:points')]]))
        if value == 'loyalty:orders':
            return order_list(api, cid)
        if value.startswith('loyalty:order:'):
            return order_page(api, cid, value.rsplit(':', 1)[1])
        if value.startswith('loyalty:customer:'):
            try:
                return customer_page(api, cid, int(value.rsplit(':', 1)[1]))
            except ValueError:
                return
        if value.startswith('loyalty:add:user:'):
            return begin_add(api, cid, 'user', value.rsplit(':', 1)[1])
        if value.startswith('loyalty:add:order:'):
            return begin_add(api, cid, 'order', value.rsplit(':', 1)[1])
        return old_action(api, cid, value)

    old_receipt = namespace['handle_receipt']
    def handle_receipt(api, message):
        return receive(api, message) or old_receipt(api, message)

    store.loyalty_points = points_balance
    namespace.update(action=action, handle_action=action, handle_receipt=handle_receipt)
    G.update(action=action, handle_action=action, handle_receipt=handle_receipt)

