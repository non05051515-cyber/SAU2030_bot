"""Editable ChatGPT code-help page; order links retain existing authorization."""
import json
import os
import re

DEFAULT_AR = 'أكواد ChatGPT\n\nلطلب كود الدخول، افتح بوت الأكواد من زر «طلب الكود» داخل طلبك في فيكسا، ثم اضغط «طلب الكود» هناك واطلب رمز الدخول من ChatGPT.\nسيصلك الرمز عند وصوله للبريد. يمكنك استلام الكود مرتين لكل طلب.'
DEFAULT_EN = 'ChatGPT login codes\n\nOpen the code bot using the request-code button in your VEXA order. Press Request code there, then request your login code from ChatGPT.\nThe code arrives when received by email. Up to two codes per order.'

def ensure(s):
    with s.db() as c:
        c.execute('CREATE TABLE IF NOT EXISTS category_code_ui(key TEXT PRIMARY KEY,value TEXT NOT NULL)')

def get(s, key, default=''):
    ensure(s)
    with s.db() as c:
        row = c.execute('SELECT value FROM category_code_ui WHERE key=?', (key,)).fetchone()
    return row[0] if row else default

def is_chatgpt(s, pid):
    title = str(s.name(pid, 0)).lower()
    return str(pid).lower() == 'chatgpt' or 'chatgpt' in title or 'شات جي بي تي' in title

def chatgpt_product(s, pid):
    if is_chatgpt(s, pid):
        return True
    variant = s.VARIANTS.get(pid)
    if variant:
        return is_chatgpt(s, variant.get('category', ''))
    custom = s.custom_product(pid)
    return bool(custom and is_chatgpt(s, custom[5]))

def add_rows(s, cid, rows, route):
    if not route.startswith('c:') or not is_chatgpt(s, route[2:]):
        return rows
    result = list(rows)
    label = get(s, 'label', s.tr(cid, 'طلب كود الدخول', 'Request login code'))
    index = next((i for i, row in enumerate(result) if any(b.get('callback_data') == 'products' for b in row)), len(result))
    result.insert(index, [s.btn(label, 'codepage:' + route[2:], s.ui_icon('ui_category_codes'), style='primary')])
    return result

def page(s, api, cid, pid='chatgpt'):
    if not is_chatgpt(s, pid):
        return
    lang = 'en' if s.prefs(cid)[0] == 'en' else 'ar'
    text = get(s, 'text_' + lang, DEFAULT_EN if lang == 'en' else DEFAULT_AR)
    entities = json.loads(get(s, 'entities_' + lang, '[]'))
    import email_codes
    email_codes.ensure(s)
    with s.db() as c:
        orders = c.execute("SELECT id,pid FROM orders WHERE cid=? AND status='delivered' ORDER BY rowid DESC LIMIT 100", (cid,)).fetchall()
    rows = []
    for oid, product in orders:
        if not chatgpt_product(s, product) or not email_codes.eligible(s, cid, oid):
            continue
        rows.append([email_codes.button(s, cid, oid)])
        if len(rows) == 10:
            break
    username = os.getenv('OTP_BOT_USERNAME', 'SOQ_IDbot').strip().lstrip('@')
    if re.fullmatch(r'[A-Za-z0-9_]{5,32}', username):
        button = s.btn(s.tr(cid, 'فتح بوت الأكواد', 'Open code bot'), 'unused', s.ui_icon('ui_category_codes'), style='primary')
        button.pop('callback_data', None)
        button['url'] = 'https://t.me/' + username
        rows.append([button])
    rows.append([s.btn(s.tr(cid, 'رجوع', 'Back'), 'product:' + pid)])
    s.send(api, cid, text, s.kb(rows), entities=entities)

def panel(s, api, cid):
    if cid != s.G['ADMIN_ID']:
        return
    with s.db() as c:
        c.execute("DELETE FROM admin_state WHERE cid=? AND action='category_code_edit'", (cid,))
    s.send(api, cid, 'إعداد صفحة أكواد ChatGPT\nعدّل النص مع التنسيق والأيقونات المتحركة، أو اسم الزر أسفل المنتجات.', s.kb([
        [s.btn('تعديل الكلام بالعربي', 'codeui:text_ar'), s.btn('تعديل الكلام بالإنجليزي', 'codeui:text_en')],
        [s.btn('تعديل اسم زر طلب الكود', 'codeui:label')],
        [s.btn('أيقونة متحركة للزر', 'seticon:ui_category_codes')],
        [s.btn('معاينة الصفحة', 'codepage:chatgpt')],
        [s.btn('رجوع', 'admin')]]))

def install(s, namespace):
    s.UI_ICON_LABELS['ui_category_codes'] = 'أيقونة زر أكواد ChatGPT تحت المنتجات'
    old_action = namespace['action']
    old_receipt = namespace['handle_receipt']
    def action(api, cid, value):
        if value.startswith('codepage:'):
            return page(s, api, cid, value.split(':', 1)[1])
        if value == 'codeui:panel':
            return panel(s, api, cid)
        if value.startswith('codeui:'):
            if cid != s.G['ADMIN_ID']:
                return
            key = value.split(':', 1)[1]
            if key not in ('text_ar', 'text_en', 'label'):
                return
            with s.db() as c:
                c.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)', (cid, 'category_code_edit', key))
            return s.send(api, cid, 'أرسل اسم الزر الجديد (حتى 50 حرفًا).' if key == 'label' else 'أرسل الكلام الجديد للصفحة، ويمكنك إضافة التنسيق والأيقونات المتحركة (حتى 3000 حرف).', s.kb([[s.btn('إلغاء', 'codeui:panel')]]))
        return old_action(api, cid, value)
    def receipt(api, message):
        cid = message.get('chat', {}).get('id')
        if cid != s.G['ADMIN_ID']:
            return old_receipt(api, message)
        with s.db() as c:
            row = c.execute("SELECT value FROM admin_state WHERE cid=? AND action='category_code_edit'", (cid,)).fetchone()
        if not row:
            return old_receipt(api, message)
        text = message.get('text', '')
        if text.startswith('/'):
            with s.db() as c:
                c.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
            return old_receipt(api, message)
        key = row[0]
        limit = 50 if key == 'label' else 3000
        if not text.strip() or len(text.encode('utf-16-le')) // 2 > limit:
            s.send(api, cid, 'أرسل نصًا ضمن الحد المحدد.')
            return True
        ensure(s)
        with s.db() as c:
            c.execute('INSERT OR REPLACE INTO category_code_ui VALUES (?,?)', (key, text.strip() if key == 'label' else text))
            if key.startswith('text_'):
                c.execute('INSERT OR REPLACE INTO category_code_ui VALUES (?,?)', ('entities_' + key[5:], json.dumps(message.get('entities', []))))
            c.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
        s.send(api, cid, 'تم حفظ التعديل.')
        panel(s, api, cid)
        return True
    namespace['action'] = namespace['handle_action'] = action
    namespace['handle_receipt'] = receipt
