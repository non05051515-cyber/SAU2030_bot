"""Bilingual catalogue extension; preserves the existing bot/admin entry points."""
import html
import json

BROADCAST_PENDING = set()
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
import urllib.parse
import urllib.request
import uuid
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

BASE = Path(__file__).resolve().parent
CATALOG = json.loads((BASE / 'imported_catalog.json').read_text(encoding='utf-8'))
VARIANTS = {v['id']: v for v in CATALOG['variants']}
RATE = Decimal(CATALOG['sar_per_usd'])
MARKUP = Decimal(CATALOG['markup_usd'])
DB_PATH = Path(os.getenv('STORE_STATE_PATH', '/data/storefront.sqlite3'))
LEGACY_LANG_PATH = Path('/data/languages.json')
USERS_PATH = Path(os.getenv('STORE_USERS_PATH', '/data/users.json'))
LEGACY = {'chatgpt_email': 'pd_02', 'chatgpt_private': 'pd_04',
          'claude_pro': 'pd_09', 'claude_api_500m': 'pd_10',
          'claude_api_100m': 'pd_11', 'claude_api_50m': 'pd_12', 'claude_api_10m': 'pd_13'}
SUPPORT = '@SOQ_ID'
G = {}


def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute('CREATE TABLE IF NOT EXISTS preferences (cid INTEGER PRIMARY KEY, lang TEXT NOT NULL DEFAULT "ar", currency TEXT NOT NULL DEFAULT "SAR")')
    conn.execute('CREATE TABLE IF NOT EXISTS receipts (cid INTEGER PRIMARY KEY, pid TEXT NOT NULL, method TEXT NOT NULL, usd TEXT, sar TEXT)')
    conn.execute('CREATE TABLE IF NOT EXISTS wallets (cid INTEGER PRIMARY KEY, balance_sar TEXT NOT NULL DEFAULT "0")')
    conn.execute('CREATE TABLE IF NOT EXISTS wallet_topups (id TEXT PRIMARY KEY, cid INTEGER NOT NULL, amount_sar TEXT NOT NULL, method TEXT NOT NULL, external_id TEXT, status TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS crypto_orders (id TEXT PRIMARY KEY, cid INTEGER NOT NULL, pid TEXT NOT NULL, amount_usd TEXT NOT NULL, external_id TEXT, status TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY, cid INTEGER NOT NULL, pid TEXT NOT NULL, method TEXT NOT NULL, usd TEXT, sar TEXT, status TEXT NOT NULL, created_at TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS activity (id INTEGER PRIMARY KEY AUTOINCREMENT, cid INTEGER NOT NULL, action TEXT NOT NULL, pid TEXT NOT NULL, created_at TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS announcements (pid TEXT PRIMARY KEY, announced_at TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS category_icons (pid TEXT PRIMARY KEY, custom_emoji_id TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS admin_state (cid INTEGER PRIMARY KEY, action TEXT NOT NULL, value TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS referrals (invitee INTEGER PRIMARY KEY, referrer INTEGER NOT NULL, joined_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 0, purchase_rewarded INTEGER NOT NULL DEFAULT 0)')
    conn.execute('CREATE TABLE IF NOT EXISTS referral_rewards (id INTEGER PRIMARY KEY AUTOINCREMENT, referrer INTEGER NOT NULL, kind TEXT NOT NULL, amount_usd TEXT NOT NULL, created_at TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS custom_topup_state (cid INTEGER PRIMARY KEY)')
    return conn


def prefs(cid):
    with db() as conn:
        row = conn.execute('SELECT lang,currency FROM preferences WHERE cid=?', (cid,)).fetchone()
        if not row:
            language = 'ar'
            try:
                language = json.loads(LEGACY_LANG_PATH.read_text(encoding='utf-8')).get(str(cid), 'ar')
            except Exception:
                pass
            if language not in ('ar', 'en'):
                language = 'ar'
            conn.execute('INSERT INTO preferences(cid,lang,currency) VALUES (?,?,?)', (cid, language, 'SAR'))
            row = (language, 'SAR')
    return row


def tr(cid, ar, en):
    return en if prefs(cid)[0] == 'en' else ar


def esc(value):
    return html.escape(str(value))


def btn(text, action, icon=None, style=None):
    result = {'text': text, 'callback_data': action}
    if icon and G['CONFIG'].get('custom_icons_enabled'):
        result['icon_custom_emoji_id'] = str(icon)
    if style:
        result['style'] = style
    return result


def kb(rows):
    return {'inline_keyboard': rows}


def send(api, cid, text, keyboard=None, entities=None):
    data = {'chat_id': cid, 'text': text}
    if entities is None:
        data['parse_mode'] = 'HTML'
    else:
        data['entities'] = entities
    if keyboard:
        data['reply_markup'] = keyboard
    return api.call('sendMessage', **data)


def nav(cid, parent='products'):
    return [btn(tr(cid, '↩️ رجوع', '↩️ Back'), parent)]


def menu(cid=0):
    rows = [[{'text': tr(cid, '🚀 ابدأ', '🚀 Start')}, {'text': tr(cid, '🛍 المنتجات', '🛍 Products')}, {'text': tr(cid, '💬 الدعم', '💬 Support')}],
                         [{'text': tr(cid, '👛 المحفظة', '👛 Wallet')}, {'text': '🔗 API'}, {'text': tr(cid, '🛡 الضمان', '🛡 Warranty')}],
                         [{'text': '🌐 اللغة / Language'}, {'text': '💱 العملة / Currency'}],
                         [{'text': tr(cid, '💎 الإحالات', '💎 Referrals')}]]
    if cid == G.get('ADMIN_ID'):
        rows.append([{'text': '🧾 لوحة الطلبات'}])
    return {'keyboard': rows,
            'resize_keyboard': True, 'is_persistent': True,
            'input_field_placeholder': tr(cid, 'اختر من القائمة', 'Choose from the menu')}




def register_referral(invitee, referrer):
    if not referrer or invitee == referrer:
        return
    try: referrer = int(referrer)
    except Exception: return
    with db() as conn:
        conn.execute('INSERT OR IGNORE INTO referrals(invitee,referrer,joined_at) VALUES (?,?,?)', (invitee, referrer, now_saudi()))


def referral_page(api, cid):
    with db() as conn:
        visits = conn.execute('SELECT COUNT(*) FROM referrals WHERE referrer=?', (cid,)).fetchone()[0]
        active = conn.execute('SELECT COUNT(*) FROM referrals WHERE referrer=? AND active=1', (cid,)).fetchone()[0]
        rp = Decimal(str(conn.execute('SELECT COALESCE(SUM(CAST(amount_usd AS REAL)),0) FROM referral_rewards WHERE referrer=? AND kind="active"', (cid,)).fetchone()[0] or 0))
        pp = Decimal(str(conn.execute('SELECT COALESCE(SUM(CAST(amount_usd AS REAL)),0) FROM referral_rewards WHERE referrer=? AND kind="purchase"', (cid,)).fetchone()[0] or 0))
    pending=max(visits-active,0); total=rp+pp; link=f'https://t.me/SAU2030_bot?start=ref_{cid}'
    text=f'💎 <b>نظام الإحالات</b>\n\n━━━━━━━━━━━━━━\n📊 <b>إحصائياتك</b>\n━━━━━━━━━━━━━━\n\n👥 الزيارات: {visits}\n⏳ معلق: {pending}\n✅ نشط: {active}\n❌ غادر: 0\n\n━━━━━━━━━━━━━━\n💰 <b>أرباحك</b>\n━━━━━━━━━━━━━━\n\n🎯 من الإحالات: ${rp:.2f}\n🛍 من المشتريات: ${pp:.2f}\n💎 المجموع: ${total:.2f}\n\n━━━━━━━━━━━━━━\n🔗 <b>رابطك:</b>\n<code>{link}</code>\n\n━━━━━━━━━━━━━━\n🎁 <b>طريقتان للربح:</b>\n\n🔥 كل 10 إحالة نشطة = $2.00\n💸 شراء صديق &gt; $10 = $0.50'
    send(api,cid,text,kb([[btn('↩️ رجوع','home')]]))


def amount(pid, currency='SAR', source_price=None):
    pid = LEGACY.get(pid, pid)
    if pid in VARIANTS:
        usd = Decimal(str(source_price if source_price is not None else VARIANTS[pid]['source_usd'])) + MARKUP
        value = usd * RATE if currency == 'SAR' else usd
    else:
        p = G['PRODUCTS'].get(pid, {})
        if p.get('price') is None:
            return None
        value = Decimal(str(p['price']))
        original = p.get('currency', 'SAR')
        if currency == 'USD' and original in ('SAR', 'ر.س'):
            value /= RATE
        elif currency == 'SAR' and original in ('USD', '$'):
            value *= RATE
    return value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def price(cid, pid, currency=None, source_price=None):
    currency = currency or prefs(cid)[1]
    value = amount(pid, currency, source_price)
    if value is None:
        return tr(cid, 'يُحدد عبر الدعم', 'Contact support for price')
    return f'{value:.2f} ' + ('USD' if currency == 'USD' else tr(cid, 'ر.س', 'SAR'))


def wallet_balance(cid):
    with db() as conn:
        row = conn.execute('SELECT balance_sar FROM wallets WHERE cid=?', (cid,)).fetchone()
    return Decimal(row[0]) if row else Decimal('0')


def wallet_credit(cid, value):
    value = Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    with db() as conn:
        conn.execute('INSERT OR IGNORE INTO wallets(cid,balance_sar) VALUES (?,?)', (cid, '0'))
        current = Decimal(conn.execute('SELECT balance_sar FROM wallets WHERE cid=?', (cid,)).fetchone()[0])
        updated = (current + value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        conn.execute('UPDATE wallets SET balance_sar=? WHERE cid=?', (str(updated), cid))
    return updated


def wallet_debit(cid, value):
    value = Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    with db() as conn:
        conn.execute('INSERT OR IGNORE INTO wallets(cid,balance_sar) VALUES (?,?)', (cid, '0'))
        current = Decimal(conn.execute('SELECT balance_sar FROM wallets WHERE cid=?', (cid,)).fetchone()[0])
        if current < value:
            return None
        updated = (current - value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        conn.execute('UPDATE wallets SET balance_sar=? WHERE cid=?', (str(updated), cid))
    return updated


def crypto_call(method, **data):
    token = os.getenv('CRYPTO_PAY_TOKEN')
    if not token:
        return None
    request = urllib.request.Request(
        'https://pay.crypt.bot/api/' + method,
        urllib.parse.urlencode(data).encode('utf-8'),
        {'Crypto-Pay-API-Token': token, 'Content-Type': 'application/x-www-form-urlencoded'},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
        return result.get('result') if result.get('ok') else None
    except Exception as exc:
        print('Crypto Pay error:', type(exc).__name__)
        return None


def crypto_invoice(usd, description, payload):
    result = crypto_call('createInvoice', currency_type='crypto', asset='USDT',
                         amount=f'{Decimal(str(usd)):.2f}', description=description[:1000],
                         payload=payload, expires_in=3600)
    if not result:
        return None
    url = result.get('bot_invoice_url') or result.get('mini_app_invoice_url') or result.get('web_app_invoice_url') or result.get('pay_url')
    return (str(result.get('invoice_id')), url) if result.get('invoice_id') and url else None


def crypto_paid(invoice_id):
    result = crypto_call('getInvoices', invoice_ids=str(invoice_id))
    items = result.get('items', []) if isinstance(result, dict) else []
    return bool(items and items[0].get('status') == 'paid')


def now_saudi():
    return datetime.now(timezone(timedelta(hours=3))).strftime('%Y-%m-%d %H:%M')


def log_activity(cid, action_name, pid):
    if cid == G.get('ADMIN_ID'):
        return
    with db() as conn:
        conn.execute('INSERT INTO activity(cid,action,pid,created_at) VALUES (?,?,?,?)',
                     (cid, action_name, pid, now_saudi()))
        conn.execute('DELETE FROM activity WHERE id NOT IN (SELECT id FROM activity ORDER BY id DESC LIMIT 500)')


def add_order(cid, pid, method, status):
    order_id = uuid.uuid4().hex[:10].upper()
    with db() as conn:
        conn.execute('INSERT INTO orders VALUES (?,?,?,?,?,?,?,?)',
                     (order_id, cid, pid, method, str(amount(pid, 'USD')), str(amount(pid, 'SAR')), status, now_saudi()))
    return order_id


def customer_link(cid):
    return f'<a href="tg://user?id={cid}">{cid}</a>'


def apply_icon_overrides():
    with db() as conn:
        rows = conn.execute('SELECT pid,custom_emoji_id FROM category_icons').fetchall()
    for pid, emoji_id in rows:
        if pid in G.get('PRODUCTS', {}):
            G['PRODUCTS'][pid]['custom_emoji_id'] = emoji_id
    if rows:
        G['CONFIG']['custom_icons_enabled'] = True


def admin_panel(api, cid):
    if cid != G['ADMIN_ID']:
        return home(api, cid)
    with db() as conn:
        orders_count = conn.execute('SELECT COUNT(*) FROM orders').fetchone()[0]
        review_count = conn.execute('SELECT COUNT(*) FROM orders WHERE status="review"').fetchone()[0]
        activity_count = conn.execute('SELECT COUNT(*) FROM activity').fetchone()[0]
    text = f'🧾 <b>لوحة إدارة VEXA</b>\n\nالطلبات: <b>{orders_count}</b>\nبانتظار المراجعة: <b>{review_count}</b>\nسجل الاختيارات: <b>{activity_count}</b>'
    send(api, cid, text, kb([[btn('📦 الطلبات الأخيرة', 'admin:orders', style='primary')],
                             [btn('👀 نشاط العملاء', 'admin:activity')],
                             [btn('📢 إرسال رسالة للجميع', 'admin:broadcast', style='primary')],
                             [btn('➕ إضافة أيقونة', 'admin:icons', style='success')],
                             [btn('🏠 الرئيسية', 'home')]]))


def admin_orders(api, cid):
    if cid != G['ADMIN_ID']:
        return home(api, cid)
    with db() as conn:
        rows = conn.execute('SELECT id,cid,pid,method,usd,sar,status,created_at FROM orders ORDER BY rowid DESC LIMIT 15').fetchall()
    if not rows:
        return send(api, cid, '📦 لا توجد طلبات مسجلة حتى الآن.', kb([[btn('↩️ لوحة الإدارة', 'admin')]]))
    status_names = {'paid': '✅ مدفوع', 'review': '⏳ مراجعة', 'rejected': '❌ مرفوض'}
    parts = ['📦 <b>آخر الطلبات</b>']
    for oid, user_id, pid, method, usd, sar, status, created in rows:
        parts.append(f'\n<b>#{esc(oid)}</b> • {status_names.get(status, esc(status))}\n{esc(name(pid, cid))}\n{esc(sar)} SAR / {esc(usd)} USD • {esc(method)}\nالعميل: {customer_link(user_id)} • {esc(created)}')
    send(api, cid, '\n'.join(parts), kb([[btn('🔄 تحديث', 'admin:orders')], [btn('↩️ لوحة الإدارة', 'admin')]]))


def admin_activity(api, cid):
    if cid != G['ADMIN_ID']:
        return home(api, cid)
    with db() as conn:
        rows = conn.execute('SELECT cid,action,pid,created_at FROM activity ORDER BY id DESC LIMIT 20').fetchall()
    if not rows:
        return send(api, cid, '👀 لا يوجد نشاط مسجل حتى الآن.', kb([[btn('↩️ لوحة الإدارة', 'admin')]]))
    parts = ['👀 <b>آخر اختيارات العملاء</b>']
    for user_id, action_name, pid, created in rows:
        label = 'فتح المنتج' if action_name == 'item' else 'فتح القسم'
        parts.append(f'\n{label}: <b>{esc(name(pid, cid))}</b>\nالعميل: {customer_link(user_id)} • {esc(created)}')
    send(api, cid, '\n'.join(parts), kb([[btn('🔄 تحديث', 'admin:activity')], [btn('↩️ لوحة الإدارة', 'admin')]]))


def admin_icons(api, cid):
    if cid != G['ADMIN_ID']:
        return home(api, cid)
    buttons = []
    for pid, product in G['PRODUCTS'].items():
        mark = '✅ ' if product.get('custom_emoji_id') else ''
        buttons.append(btn(mark + product['name'], 'seticon:' + pid, product.get('custom_emoji_id')))
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    send(api, cid, '➕ <b>إضافة أيقونة متحركة</b>\n\nاختر القسم، ثم أرسل الأيقونة للبوت في رسالة منفصلة.',
         kb(rows + [[btn('↩️ لوحة الإدارة', 'admin')]]))


def begin_icon_setup(api, cid, pid):
    if cid != G['ADMIN_ID'] or pid not in G['PRODUCTS']:
        return home(api, cid)
    with db() as conn:
        conn.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)', (cid, 'icon', pid))
    send(api, cid, f'أرسل الآن الأيقونة المتحركة الخاصة بقسم <b>{esc(G["PRODUCTS"][pid]["name"])}</b>.\n\nأرسل رمزًا مخصصًا واحدًا فقط، أو اضغط إلغاء.',
         kb([[btn('❌ إلغاء', 'cancelicon')]]))


def handle_admin_icon(api, message):
    cid = message.get('chat', {}).get('id')
    if cid != G.get('ADMIN_ID'):
        return False
    with db() as conn:
        state = conn.execute('SELECT action,value FROM admin_state WHERE cid=?', (cid,)).fetchone()
    if not state or state[0] != 'icon':
        return False
    entities = list(message.get('entities', [])) + list(message.get('caption_entities', []))
    emoji = next((entity.get('custom_emoji_id') for entity in entities
                  if entity.get('type') == 'custom_emoji' and entity.get('custom_emoji_id')), None)
    if not emoji:
        send(api, cid, 'لم أجد أيقونة مخصصة. أرسل الأيقونة المتحركة نفسها، وليس صورة أو ملصقًا.',
             kb([[btn('❌ إلغاء', 'cancelicon')]]))
        return True
    pid = state[1]
    with db() as conn:
        conn.execute('INSERT OR REPLACE INTO category_icons VALUES (?,?)', (pid, str(emoji)))
        conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
    G['PRODUCTS'][pid]['custom_emoji_id'] = str(emoji)
    G['CONFIG']['custom_icons_enabled'] = True
    send(api, cid, f'✅ تم حفظ الأيقونة لقسم <b>{esc(G["PRODUCTS"][pid]["name"])}</b>.',
         kb([[btn('➕ إضافة أيقونة أخرى', 'admin:icons')], [btn('🛍 معاينة المنتجات', 'products')]]))
    return True


def broadcast_new_products(api):
    """Broadcast each newly-added catalogue item once, across deploys."""
    with db() as conn:
        known = {row[0] for row in conn.execute('SELECT pid FROM announcements').fetchall()}
        if not known:
            # First migration: treat the existing catalogue as the baseline, except
            # items explicitly flagged for their first announcement.
            baseline = [pid for pid, variant in VARIANTS.items() if not variant.get('announce')]
            conn.executemany('INSERT OR IGNORE INTO announcements(pid,announced_at) VALUES (?,?)',
                             [(pid, now_saudi()) for pid in baseline])
            known.update(baseline)
    pending = [variant for pid, variant in VARIANTS.items() if pid not in known]
    if not pending:
        return
    try:
        users = [int(cid) for cid in json.loads(USERS_PATH.read_text(encoding='utf-8'))]
    except Exception:
        users = []
    for variant in pending:
        pid = variant['id']
        for cid in users:
            language = prefs(cid)[0]
            title = variant['name'][language]
            description = variant['description'][language]
            stock = variant.get('source_stock', 0)
            text = tr(cid, '🔥 <b>منتج جديد في VEXA STORE</b>', '🔥 <b>New product at VEXA STORE</b>')
            text += f'\n\n<b>{esc(title)}</b>\n➕ {tr(cid, "تمت الإضافة", "Added")}: {stock}\n📦 {tr(cid, "الكمية الحالية", "Current stock")}: {stock}'
            text += f'\n💵 {tr(cid, "السعر", "Price")}: {price(cid, pid, "SAR")} / {price(cid, pid, "USD")}\n\n{esc(description)}'
            rows = [[btn(tr(cid, '🛒 اشترِ الآن', '🛒 Buy now'), 'item:' + pid, style='success')]] if stock > 0 else []
            try:
                send(api, cid, text, kb(rows) if rows else None)
                time.sleep(0.04)
            except Exception:
                pass
        with db() as conn:
            conn.execute('INSERT OR REPLACE INTO announcements(pid,announced_at) VALUES (?,?)', (pid, now_saudi()))


def name(pid, cid=0):
    pid = LEGACY.get(pid, pid)
    if pid in VARIANTS:
        return VARIANTS[pid]['name'][prefs(cid)[0]]
    return G['PRODUCTS'].get(pid, {}).get('name', pid)


def start(api, cid):
    send(api, cid, tr(cid, '👋 <b>مرحباً بك في VEXA STORE</b>\n\nمتجر الخدمات والاشتراكات الرقمية.',
                     '👋 <b>Welcome to VEXA STORE</b>\n\nDigital services and subscriptions.'),
         kb([[btn('🚀 START | ابدأ', 'enter_store')], [btn('🌐 العربية / English', 'settings:lang')]]))


def home(api, cid):
    balance_sar = wallet_balance(cid)
    balance_usd = (balance_sar / RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    with db() as conn:
        purchases = conn.execute('SELECT COUNT(*) FROM orders WHERE cid=? AND status="paid"', (cid,)).fetchone()[0]
    text = tr(cid, f'👋 <b>أهلاً بك في VEXA STORE!</b>\n\n🆔 رقم العضوية: <code>{cid}</code>\n👤 حسابك: <a href="tg://user?id={cid}">فتح الحساب</a>\n💳 الرصيد: <b>${balance_usd:.2f}</b>\n🛍 المشتريات: <b>{purchases}</b>\n\nاختر من القائمة أدناه:', f'👋 <b>Welcome to VEXA STORE!</b>\n\n🆔 Member ID: <code>{cid}</code>\n👤 Account: <a href="tg://user?id={cid}">Open profile</a>\n💳 Balance: <b>${balance_usd:.2f}</b>\n🛍 Purchases: <b>{purchases}</b>\n\nChoose from the menu below:')
    rows = [[btn(tr(cid,'🛒 المنتجات','🛒 Products'),'products',style='primary'), btn(tr(cid,'💰 شحن الرصيد','💰 Top up'),'wallet:topup',style='success')], [btn(tr(cid,'💎 الإحالات','💎 Referrals'),'referrals'), btn(tr(cid,'👤 حسابي','👤 My account'),'wallet')], [btn(tr(cid,'💬 تواصل مع الدعم','💬 Contact support'),'support',style='danger'), btn(tr(cid,'⚠️ إبلاغ عن مشكلة','⚠️ Report issue'),'support')], [btn(tr(cid,'💱 العملة','💱 Currency'),'settings:currency'), btn('🌐 Language / اللغة','settings:lang')]]
    send(api, cid, text, kb(rows))

def products(api, cid):
    buttons = [btn(p['name'], 'product:' + pid, p.get('custom_emoji_id')) for pid, p in G['PRODUCTS'].items()]
    rows = [buttons[i:i+3] for i in range(0, len(buttons), 3)]
    rows += [[btn('🌐 Language / اللغة', 'settings:lang'), btn('💱 Currency / العملة', 'settings:currency')], [btn(tr(cid, '🏠 الرئيسية', '🏠 Home'), 'home')]]
    send(api, cid, tr(cid, '🛍 <b>المنتجات</b>\nاختر الخدمة:', '🛍 <b>Products</b>\nChoose a service:'), kb(rows))


def settings(api, cid, kind):
    if kind == 'lang':
        rows = [[btn('العربية', 'setlang:ar'), btn('English', 'setlang:en')]]
        text = '🌐 اختر اللغة / Choose language'
    else:
        rows = [[btn('🇸🇦 SAR — ريال سعودي', 'setcurrency:SAR'), btn('🇺🇸 USD — US Dollar', 'setcurrency:USD')]]
        text = '💱 اختر العملة / Choose currency'
    send(api, cid, text, kb(rows + [nav(cid)]))


def card(api, cid, image_path, title, text, keyboard):
    """Separate photo and full text so Telegram's caption limit never drops terms."""
    if image_path:
        path = (BASE / image_path).resolve()
        if path.is_relative_to(BASE) and path.is_file():
            boundary = 'VEXA' + uuid.uuid4().hex
            body = b''
            for key, val in {'chat_id': str(cid), 'caption': title[:900]}.items():
                body += f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{val}\r\n'.encode()
            body += f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="{path.name}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode() + path.read_bytes() + f'\r\n--{boundary}--\r\n'.encode()
            try:
                api_url = getattr(api, 'base_url', getattr(api, 'u', None))
                if not api_url:
                    raise AttributeError('Telegram API URL is unavailable')
                req = urllib.request.Request(api_url + 'sendPhoto', body, {'Content-Type': f'multipart/form-data; boundary={boundary}'})
                with urllib.request.urlopen(req, timeout=40) as response:
                    json.load(response)
            except Exception as exc:
                print('Product image failed:', type(exc).__name__)
    # Split plain content before HTML escaping; 1700 characters <= 3400 UTF-16 units.
    chunks = [text[i:i+1700] for i in range(0, len(text), 1700)] or ['']
    for i, chunk in enumerate(chunks):
        send(api, cid, esc(chunk), keyboard if i == len(chunks)-1 else None)


def category(api, cid, pid):
    p = G['PRODUCTS'].get(pid)
    if not p:
        products(api, cid)
        return
    choices = [v for v in VARIANTS.values() if v['category'] == pid]
    if choices:
        rows = []
        for v in choices:
            sold_out = v['source_stock'] == 0
            status = '⏸ ' if v.get('review_required') else (tr(cid, '🔴 نفد | ', '🔴 SOLD OUT | ') if sold_out else '')
            rows.append([btn(status + name(v['id'], cid) + ' | ' + price(cid, v['id']),
                             'item:' + v['id'], p.get('custom_emoji_id'), 'danger' if sold_out else None)])
        send(api, cid, '<b>' + esc(p['name']) + '</b>\n\n' + tr(cid, 'اختر المنتج:', 'Choose a product:'), kb(rows + [nav(cid)]))
        return
    english = {'youtube': 'YouTube Premium for one month. Ad-free viewing, background playback, offline downloads and YouTube Music Premium benefits.',
               'netflix': 'Netflix subscription for movies, series and entertainment.', 'iptv': 'IPTV subscriptions for compatible devices.'}
    description = p['description'] if prefs(cid)[0] == 'ar' else english.get(pid, p['name'])
    text = p['name'] + '\n\n' + price(cid, pid) + '\n\n' + description
    rows = [[btn(tr(cid, '🛒 طلب المنتج', '🛒 Order'), 'buy:' + pid)]] if amount(pid) is not None else []
    rows += [[btn(tr(cid, '💬 الدعم', '💬 Support'), 'support')], nav(cid)]
    card(api, cid, f'assets/{pid}.png', p['name'], text, kb(rows))


def item(api, cid, pid):
    pid = LEGACY.get(pid, pid)
    v = VARIANTS.get(pid)
    if not v:
        products(api, cid)
        return
    lang = prefs(cid)[0]
    available = tr(cid, 'التوفر لدى المورد قابل للتغير؛ يُؤكد قبل تنفيذ الطلب.', 'Supplier availability can change; confirmation is required before fulfilment.')
    if v['source_stock'] == 0:
        available = tr(cid, '🚫 نفد لدى المورد وقت المراجعة. الطلب غير متاح حاليًا.', '🚫 Out of stock at the last supplier check. Ordering is currently unavailable.')
    text = name(pid, cid) + '\n\n💰 ' + price(cid, pid) + '\n\n' + available + '\n\n' + v['description'][lang]
    if v.get('promotions'):
        text += '\n\n' + tr(cid, 'أسعار الكميات — تواصل مع الدعم:', 'Bulk prices — contact support:')
        for tier in v['promotions']:
            text += '\n' + str(tier['min_quantity']) + '+: ' + price(cid, pid, source_price=tier['source_usd']) + tr(cid, ' لكل قطعة', ' per unit')
    if v.get('review_required'):
        text += '\n\n⚠️ ' + v['review_required'][lang]
    rows = []
    if v['source_stock'] > 0 and not v.get('review_required'):
        rows.append([btn(tr(cid, '🛒 طلب قطعة واحدة', '🛒 Order one item'), 'buy:' + pid)])
    rows += [[btn(tr(cid, '💬 الدعم', '💬 Support'), 'support')], nav(cid, 'product:' + v['category'])]
    card(api, cid, v.get('image'), name(pid, cid), text, kb(rows))


def can_order(pid):
    pid = LEGACY.get(pid, pid)
    if pid in VARIANTS:
        return VARIANTS[pid]['source_stock'] > 0 and not VARIANTS[pid].get('review_required')
    return pid in G['PRODUCTS'] and amount(pid) is not None


def back(pid):
    pid = LEGACY.get(pid, pid)
    return ('item:' if pid in VARIANTS else 'product:') + pid


def summary(cid, pid):
    return esc(name(pid, cid)) + '\n💰 ' + price(cid, pid) + '\n' + tr(cid, 'الكمية: 1', 'Quantity: 1')


def wallet(api, cid):
    sar = wallet_balance(cid).quantize(Decimal('0.01'))
    usd = (sar / RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    text = tr(cid, '👛 <b>محفظة VEXA</b>\n\nرصيدك الحالي:', '👛 <b>VEXA Wallet</b>\n\nYour current balance:')
    text += f'\n<b>{sar:.2f} {tr(cid, "ر.س", "SAR")}</b>\n<b>{usd:.2f} USD</b>'
    send(api, cid, text, kb([[btn(tr(cid, '➕ شحن المحفظة', '➕ Top up wallet'), 'wallet:topup')], nav(cid, 'home')]))


def wallet_amounts(api, cid):
    rows = []
    for pair in ((20, 50), (100, 200)):
        row = []
        for value in pair:
            usd = (Decimal(value) / RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            row.append(btn(f'{value} {tr(cid, "ر.س", "SAR")} / {usd:.2f} USD', f'topup:{value}'))
        rows.append(row)
    text = tr(cid, 'اختر مبلغ شحن المحفظة — جميع المبالغ معروضة بالريال والدولار:',
              'Choose a wallet top-up amount — all amounts are shown in SAR and USD:')
    rows.append([btn(tr(cid, '✏️ مبلغ اختياري بالدولار', '✏️ Custom amount in USD'), 'topupcustom')])
    send(api, cid, text, kb(rows + [nav(cid, 'wallet')]))


def wallet_method(api, cid, value):
    try:
        value = Decimal(value).quantize(Decimal('0.01'))
    except Exception:
        return wallet_amounts(api, cid)
    if value <= 0 or value > 5000:
        return wallet_amounts(api, cid)
    usd = (value / RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    text = tr(cid, 'اختر طريقة شحن المحفظة:', 'Choose a wallet top-up method:') + f'\n\n<b>{value:.2f} SAR / {usd:.2f} USD</b>'
    send(api, cid, text, kb([[btn('💠 Crypto Pay', f'topupcrypto:{value}')],
                             [btn('🟡 Bybit / USDT', f'topupbybit:{value}')], nav(cid, 'wallet:topup')]))


def wallet_crypto(api, cid, value):
    value = Decimal(value).quantize(Decimal('0.01'))
    usd = (value / RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    topup_id = uuid.uuid4().hex[:16]
    invoice = crypto_invoice(usd, f'VEXA wallet top-up — {value:.2f} SAR', 'wallet:' + topup_id)
    if not invoice:
        send(api, cid, tr(cid, 'تعذر إنشاء فاتورة Crypto Pay. حاول لاحقًا أو استخدم Bybit.', 'Could not create a Crypto Pay invoice. Try later or use Bybit.'), kb([nav(cid, f'topup:{value}')]))
        return
    invoice_id, url = invoice
    with db() as conn:
        conn.execute('INSERT INTO wallet_topups VALUES (?,?,?,?,?,?)', (topup_id, cid, str(value), 'cryptopay', invoice_id, 'pending'))
    send(api, cid, tr(cid, 'ادفع الفاتورة ثم اضغط «تحقق من الدفع».', 'Pay the invoice, then tap “Check payment”.') + f'\n\n<b>{value:.2f} SAR / {usd:.2f} USDT</b>',
         kb([[{'text': tr(cid, '💠 فتح فاتورة Crypto Pay', '💠 Open Crypto Pay invoice'), 'url': url}],
             [btn(tr(cid, '✅ تحقق من الدفع', '✅ Check payment'), 'checktopup:' + topup_id)], nav(cid, 'wallet')]))


def wallet_bybit(api, cid, value):
    value = Decimal(value).quantize(Decimal('0.01'))
    topup_id = uuid.uuid4().hex[:16]
    with db() as conn:
        conn.execute('INSERT INTO wallet_topups VALUES (?,?,?,?,?,?)', (topup_id, cid, str(value), 'bybit', None, 'pending'))
    send(api, cid, tr(cid, 'اختر طريقة إرسال USDT عبر Bybit:', 'Choose how to send USDT via Bybit:'),
         kb([[btn('Bybit Pay', 'topupsend:bybitid:' + topup_id)],
             [btn('USDT • TRON (TRC20)', 'topupsend:trc20:' + topup_id)],
             [btn('USDT • BSC (BEP20)', 'topupsend:bep20:' + topup_id)], nav(cid, f'topup:{value}')]))


def wallet_bybit_details(api, cid, method, topup_id):
    keys = {'bybitid': ('Bybit Pay ID', 'PAYMENT_BYBIT_PAY_ID'),
            'trc20': ('USDT — TRON (TRC20)', 'PAYMENT_USDT_TRC20'),
            'bep20': ('USDT — BSC (BEP20)', 'PAYMENT_USDT_BEP20')}
    if method not in keys:
        return wallet(api, cid)
    with db() as conn:
        row = conn.execute('SELECT amount_sar,status FROM wallet_topups WHERE id=? AND cid=?', (topup_id, cid)).fetchone()
        if row and row[1] == 'pending':
            conn.execute('UPDATE wallet_topups SET method=? WHERE id=?', (method, topup_id))
    if not row or row[1] != 'pending':
        return wallet(api, cid)
    title, key = keys[method]
    usd = (Decimal(row[0]) / RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    value = os.getenv(key)
    if not value:
        return send(api, cid, tr(cid, 'بيانات Bybit غير مكتملة.', 'Bybit payment details are incomplete.'), kb([nav(cid, 'wallet')]))
    text = f'<b>{title}</b>\n\n{usd:.2f} USDT\n\n<code>{esc(value)}</code>\n\n' + tr(cid, 'بعد التحويل أرسل صورة الإثبات.', 'After transferring, send the receipt image.')
    send(api, cid, text, kb([[btn(tr(cid, '✅ تم التحويل', '✅ Payment sent'), 'topupreceipt:' + topup_id)], nav(cid, 'wallet')]))


def check_wallet_crypto(api, cid, topup_id):
    with db() as conn:
        row = conn.execute('SELECT amount_sar,external_id,status FROM wallet_topups WHERE id=? AND cid=?', (topup_id, cid)).fetchone()
    if not row:
        return wallet(api, cid)
    if row[2] == 'credited':
        return wallet(api, cid)
    if not crypto_paid(row[1]):
        send(api, cid, tr(cid, 'لم يصل الدفع بعد. أكمل الفاتورة ثم أعد التحقق.', 'Payment has not arrived yet. Complete the invoice and check again.'), kb([[btn(tr(cid, '🔄 تحقق مرة أخرى', '🔄 Check again'), 'checktopup:' + topup_id)], nav(cid, 'wallet')]))
        return
    with db() as conn:
        changed = conn.execute('UPDATE wallet_topups SET status="credited" WHERE id=? AND status="pending"', (topup_id,)).rowcount
    if changed:
        wallet_credit(cid, row[0])
    send(api, cid, tr(cid, '✅ تم شحن المحفظة بنجاح.', '✅ Wallet topped up successfully.'))
    wallet(api, cid)


def payments(api, cid, pid):
    if not can_order(pid):
        send(api, cid, tr(cid, 'الطلب غير متاح لهذا الخيار حاليًا. تواصل مع الدعم: ', 'Ordering is unavailable for this option. Contact support: ') + SUPPORT, kb([nav(cid, back(pid))]))
        return
    warning = tr(cid, 'التنفيذ بعد مراجعة الدفع وتأكيد التوفر. تواصل مع الدعم قبل التحويل.', 'Fulfilment follows payment review and availability confirmation. Contact support before transferring.')
    send(api, cid, tr(cid, '💳 <b>اختر طريقة الدفع</b>\n\n', '💳 <b>Choose payment method</b>\n\n') + summary(cid, pid) + '\n\n' + warning,
         kb([[btn('👛 ' + tr(cid, 'المحفظة', 'Wallet'), 'paywallet:' + pid)],
             [btn('💠 Crypto Pay', 'paycrypto:' + pid)],
             [btn('USDT — Bybit', 'paybybit:' + pid, '5472387796574418157')], nav(cid, back(pid))]))


def payment(api, cid, pid, method):
    if not can_order(pid):
        payments(api, cid, pid)
        return
    if method == 'bybit':
        send(api, cid, '🪙 <b>USDT — Bybit</b>\n\n' + summary(cid, pid),
             kb([[btn('Bybit Pay', 'bybitid:' + pid)], [btn('USDT • TRON (TRC20)', 'trc20:' + pid)], [btn('USDT • BSC (BEP20)', 'bep20:' + pid)], nav(cid, 'buy:' + pid)]))
        return
    choices = {'bybitid': ('Bybit Pay', 'PAYMENT_BYBIT_PAY_ID'), 'trc20': ('USDT — TRON (TRC20)', 'PAYMENT_USDT_TRC20'), 'bep20': ('USDT — BSC (BEP20)', 'PAYMENT_USDT_BEP20')}
    if method not in choices:
        return
    title, key = choices[method]
    configured = bool(os.getenv(key))
    text = title + '\n\n' + summary(cid, pid) + '\n\n<code>' + esc(os.getenv(key, tr(cid, 'غير مضاف بعد', 'Not configured'))) + '</code>\n\n'
    text += tr(cid, 'أكد مبلغ USDT والرسوم مع الدعم قبل الإرسال. استخدم الطريقة والشبكة المحددة فقط.', 'Confirm the USDT amount and fees with support before sending. Use only the specified method and network.')
    rows = []
    if configured:
        text += '\n\n' + tr(cid, 'بعد التحويل أرسل صورة الإثبات للمراجعة.', 'After transferring, submit a receipt photo for review.')
        rows.append([btn(tr(cid, '✅ تم التحويل', '✅ Payment sent'), f'receipt:{method}:{pid}')])
    else:
        text += '\n\n' + tr(cid, 'بيانات الدفع غير مكتملة. تواصل مع الدعم: ', 'Payment details are incomplete. Contact support: ') + SUPPORT
    send(api, cid, text, kb(rows + [nav(cid, 'buy:' + pid)]))


def pay_with_wallet(api, cid, pid):
    if not can_order(pid):
        return payments(api, cid, pid)
    cost = amount(pid, 'SAR')
    remaining = wallet_debit(cid, cost)
    if remaining is None:
        send(api, cid, tr(cid, 'رصيد المحفظة غير كافٍ.', 'Insufficient wallet balance.') +
             f'\n\n{tr(cid, "المطلوب", "Required")}: {cost:.2f} SAR\n{tr(cid, "الرصيد", "Balance")}: {wallet_balance(cid):.2f} SAR',
             kb([[btn(tr(cid, '➕ شحن المحفظة', '➕ Top up wallet'), 'wallet:topup')], nav(cid, 'buy:' + pid)]))
        return
    order_id = add_order(cid, pid, 'wallet', 'paid')
    send(api, G['ADMIN_ID'], f'🛒 <b>طلب مدفوع من المحفظة #{order_id}</b>\n\n' + summary(cid, pid) + f'\nالعميل: <code>{cid}</code>')
    send(api, cid, tr(cid, '✅ تم الدفع من المحفظة وإرسال الطلب للإدارة.', '✅ Paid from your wallet and the order was sent to administration.') + f'\n\n{tr(cid, "الرصيد المتبقي", "Remaining balance")}: {remaining:.2f} SAR', menu(cid))


def pay_with_crypto(api, cid, pid):
    if not can_order(pid):
        return payments(api, cid, pid)
    usd = amount(pid, 'USD')
    order_id = uuid.uuid4().hex[:16]
    invoice = crypto_invoice(usd, 'VEXA STORE — ' + name(pid, cid), 'order:' + order_id)
    if not invoice:
        return send(api, cid, tr(cid, 'تعذر إنشاء فاتورة Crypto Pay. حاول لاحقًا أو استخدم Bybit.', 'Could not create a Crypto Pay invoice. Try later or use Bybit.'), kb([nav(cid, 'buy:' + pid)]))
    invoice_id, url = invoice
    with db() as conn:
        conn.execute('INSERT INTO crypto_orders VALUES (?,?,?,?,?,?)', (order_id, cid, pid, str(usd), invoice_id, 'pending'))
    send(api, cid, tr(cid, 'ادفع الفاتورة ثم اضغط «تحقق من الدفع».', 'Pay the invoice, then tap “Check payment”.') + '\n\n' + summary(cid, pid),
         kb([[{'text': tr(cid, '💠 فتح فاتورة Crypto Pay', '💠 Open Crypto Pay invoice'), 'url': url}],
             [btn(tr(cid, '✅ تحقق من الدفع', '✅ Check payment'), 'checkorder:' + order_id)], nav(cid, 'buy:' + pid)]))


def check_crypto_order(api, cid, order_id):
    with db() as conn:
        row = conn.execute('SELECT pid,external_id,status FROM crypto_orders WHERE id=? AND cid=?', (order_id, cid)).fetchone()
    if not row:
        return products(api, cid)
    pid, invoice_id, status = row
    if status == 'paid':
        return send(api, cid, tr(cid, '✅ هذه الفاتورة مدفوعة وتم إرسال الطلب.', '✅ This invoice is paid and the order was sent.'), menu(cid))
    if not crypto_paid(invoice_id):
        return send(api, cid, tr(cid, 'لم يصل الدفع بعد. أكمل الفاتورة ثم أعد التحقق.', 'Payment has not arrived yet. Complete the invoice and check again.'),
                    kb([[btn(tr(cid, '🔄 تحقق مرة أخرى', '🔄 Check again'), 'checkorder:' + order_id)], nav(cid, 'buy:' + pid)]))
    with db() as conn:
        changed = conn.execute('UPDATE crypto_orders SET status="paid" WHERE id=? AND status="pending"', (order_id,)).rowcount
    if changed:
        saved_order_id = add_order(cid, pid, 'cryptopay', 'paid')
        send(api, G['ADMIN_ID'], f'💠 <b>طلب Crypto Pay مدفوع #{saved_order_id}</b>\n\n' + summary(cid, pid) + f'\nالعميل: <code>{cid}</code>')
    send(api, cid, tr(cid, '✅ تم الدفع وإرسال الطلب للإدارة.', '✅ Payment received and the order was sent to administration.'), menu(cid))


def receipt_request(api, cid, pid, method):
    if not can_order(pid) or method not in ('bank', 'bybitid', 'trc20', 'bep20'):
        payments(api, cid, pid)
        return
    with db() as conn:
        conn.execute('INSERT OR REPLACE INTO receipts VALUES (?,?,?,?,?)', (cid, pid, method, str(amount(pid, 'USD')), str(amount(pid, 'SAR'))))
    send(api, cid, tr(cid, '📸 أرسل صورة إثبات الدفع هنا. ستصل للإدارة للمراجعة.', '📸 Send your payment receipt photo here. It will be sent to the administrator for review.'), kb([[btn(tr(cid, '❌ إلغاء', '❌ Cancel'), 'cancel:' + pid)]]))


def receipt(api, message):
    cid = message['chat']['id']
    if handle_admin_icon(api, message):
        return True
    if cid == G.get('ADMIN_ID') and cid in BROADCAST_PENDING:
        if message.get('text', '').startswith('/'):
            BROADCAST_PENDING.discard(cid)
            return False
        try:
            users = [int(x) for x in json.loads(USERS_PATH.read_text(encoding='utf-8'))]
        except Exception:
            users = []
        ok = failed = 0
        for user_id in users:
            if user_id == cid:
                continue
            try:
                result = api.call('copyMessage', chat_id=user_id, from_chat_id=cid,
                                  message_id=message['message_id'])
                if result:
                    ok += 1
                else:
                    failed += 1
            except Exception:
                failed += 1
        BROADCAST_PENDING.discard(cid)
        send(api, cid, f'✅ <b>تم الإرسال</b>\n\nوصلت الرسالة إلى: <b>{ok}</b>\nتعذر الإرسال إلى: <b>{failed}</b>',
             kb([[btn('↩️ لوحة الإدارة', 'admin')]]))
        return True
    with db() as conn:
        custom = conn.execute('SELECT 1 FROM custom_topup_state WHERE cid=?', (cid,)).fetchone()
    if custom:
        if (message.get('text') or '').startswith('/'):
            with db() as conn:
                conn.execute('DELETE FROM custom_topup_state WHERE cid=?', (cid,))
            return False
        raw = (message.get('text') or '').strip().replace(',', '.')
        try:
            usd_value = Decimal(raw).quantize(Decimal('0.01'))
        except Exception:
            send(api, cid, tr(cid, 'أرسل المبلغ كرقم فقط، مثال: 20', 'Send the amount as a number only, e.g. 20'))
            return True
        if usd_value <= 0 or usd_value > Decimal('1333'):
            send(api, cid, tr(cid, 'اختر مبلغًا أكبر من 0 وحتى 1333 دولار.', 'Choose an amount above 0 and up to 1333 USD.'))
            return True
        with db() as conn:
            conn.execute('DELETE FROM custom_topup_state WHERE cid=?', (cid,))
        value = (usd_value * RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        wallet_method(api, cid, str(value))
        return True
    menu_actions = G.get('MENU_ACTIONS', G.get('MENU', {}))
    if message.get('text', '').startswith('/') or message.get('text') in menu_actions:
        with db() as conn:
            conn.execute('DELETE FROM receipts WHERE cid=?', (cid,))
            conn.execute('UPDATE wallet_topups SET status="cancelled" WHERE cid=? AND status="receipt_pending"', (cid,))
        return False
    with db() as conn:
        topup = conn.execute('SELECT id,amount_sar,method FROM wallet_topups WHERE cid=? AND status="receipt_pending" ORDER BY rowid DESC LIMIT 1', (cid,)).fetchone()
    if topup:
        topup_id, topup_sar, topup_method = topup
        if not message.get('photo'):
            send(api, cid, tr(cid, '📸 أرسل صورة إثبات الدفع.', '📸 Send the payment receipt image.'), kb([[btn(tr(cid, '❌ إلغاء', '❌ Cancel'), 'canceltopup:' + topup_id)]]))
            return True
        user = message.get('from', {})
        username = '@' + user['username'] if user.get('username') else str(cid)
        sent = send(api, G['ADMIN_ID'], f'👛 <b>طلب شحن محفظة</b>\n\nالمبلغ: {esc(topup_sar)} SAR\nالطريقة: {esc(topup_method)}\nالعميل: {esc(username)}\nID: <code>{cid}</code>',
                    kb([[btn('✅ اعتماد الشحن', 'approvetopup:' + topup_id)], [btn('❌ رفض', 'rejecttopup:' + topup_id)]]))
        forwarded = api.call('forwardMessage', chat_id=G['ADMIN_ID'], from_chat_id=cid, message_id=message['message_id']) if sent else None
        if not forwarded:
            send(api, cid, tr(cid, 'تعذر إرسال الإثبات. حاول مرة أخرى.', 'Could not send the receipt. Try again.'))
            return True
        with db() as conn:
            conn.execute('UPDATE wallet_topups SET status="review" WHERE id=? AND status="receipt_pending"', (topup_id,))
        send(api, cid, tr(cid, '✅ وصل إثبات شحن المحفظة للإدارة للمراجعة.', '✅ Wallet top-up receipt sent for review.'), menu(cid))
        return True
    with db() as conn:
        pending = conn.execute('SELECT pid,method,usd,sar FROM receipts WHERE cid=?', (cid,)).fetchone()
    if not pending:
        return False
    if not message.get('photo'):
        send(api, cid, tr(cid, '📸 أرسل صورة إثبات الدفع، أو اضغط إلغاء.', '📸 Send a receipt photo, or tap Cancel.'), kb([[btn(tr(cid, '❌ إلغاء', '❌ Cancel'), 'cancel:' + pending[0])]]))
        return True
    pid, method, usd, sar = pending
    user = message.get('from', {})
    username = '@' + user['username'] if user.get('username') else str(cid)
    result = send(api, G['ADMIN_ID'], '🧾 <b>إثبات دفع جديد</b>\n\n' + esc(name(pid)) + f'\nالكمية: 1\nالسعر عند الطلب: {sar} SAR / {usd} USD\nالطريقة: {esc(method)}\nالعميل: {esc(username)}\nID: <code>{cid}</code>')
    forwarded = api.call('forwardMessage', chat_id=G['ADMIN_ID'], from_chat_id=cid, message_id=message['message_id']) if result else None
    if not forwarded:
        send(api, cid, tr(cid, 'تعذر إرسال الإثبات للإدارة. أعد المحاولة أو تواصل مع ', 'Could not forward the receipt. Retry or contact ') + SUPPORT)
        return True
    add_order(cid, pid, method, 'review')
    with db() as conn:
        conn.execute('DELETE FROM receipts WHERE cid=?', (cid,))
    send(api, cid, tr(cid, '✅ وصل الإثبات للإدارة للمراجعة. ستتم متابعة طلبك بعد التحقق.', '✅ Receipt sent for review. Your order will be followed up after verification.'), menu(cid))
    return True


def review_topup(api, actor, topup_id, approve):
    if actor != G['ADMIN_ID']:
        return
    with db() as conn:
        row = conn.execute('SELECT cid,amount_sar,status FROM wallet_topups WHERE id=?', (topup_id,)).fetchone()
        if not row or row[2] != 'review':
            return send(api, actor, 'تمت معالجة طلب الشحن مسبقًا.')
        status = 'credited' if approve else 'rejected'
        conn.execute('UPDATE wallet_topups SET status=? WHERE id=? AND status="review"', (status, topup_id))
    cid, value, _ = row
    if approve:
        balance = wallet_credit(cid, value)
        send(api, cid, f'✅ تم اعتماد شحن المحفظة بمبلغ {esc(value)} SAR.\nالرصيد الحالي: {balance:.2f} SAR', menu(cid))
        send(api, actor, f'✅ تم شحن محفظة العميل <code>{cid}</code> بمبلغ {esc(value)} SAR.')
    else:
        send(api, cid, tr(cid, '❌ لم تتم الموافقة على إثبات شحن المحفظة. تواصل مع الدعم.', '❌ Your wallet top-up receipt was not approved. Contact support.'), menu(cid))
        send(api, actor, '❌ تم رفض طلب شحن المحفظة.')


def action(api, cid, value):
    prefix, _, arg = value.partition(':')
    arg = LEGACY.get(arg, arg)
    if prefix in ('home', 'enter_store'):
        home(api, cid)
    elif prefix == 'start':
        start(api, cid)
    elif prefix == 'products':
        products(api, cid)
    elif prefix == 'referrals':
        referral_page(api, cid)
    elif prefix == 'product':
        log_activity(cid, 'category', arg)
        category(api, cid, arg)
    elif prefix in ('item', 'claude'):
        log_activity(cid, 'item', arg)
        item(api, cid, arg)
    elif value in LEGACY:
        log_activity(cid, 'item', LEGACY[value])
        item(api, cid, LEGACY[value])
    elif prefix == 'admin':
        if arg == 'orders': admin_orders(api, cid)
        elif arg == 'activity': admin_activity(api, cid)
        elif arg == 'icons': admin_icons(api, cid)
        elif arg == 'broadcast' and cid == G['ADMIN_ID']:
            BROADCAST_PENDING.add(cid)
            send(api, cid, '📢 <b>إرسال رسالة للجميع</b>\n\nأرسل الآن الرسالة التي تريد إرسالها لجميع مستخدمي البوت.\nيمكنك إرسال نص أو صورة مع تعليق.',
                 kb([[btn('❌ إلغاء', 'admin:broadcast_cancel')]]))
        elif arg == 'broadcast_cancel' and cid == G['ADMIN_ID']:
            BROADCAST_PENDING.discard(cid)
            admin_panel(api, cid)
        else: admin_panel(api, cid)
    elif prefix == 'seticon':
        begin_icon_setup(api, cid, arg)
    elif prefix == 'cancelicon':
        if cid == G['ADMIN_ID']:
            with db() as conn:
                conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
            admin_icons(api, cid)
    elif prefix == 'settings':
        settings(api, cid, arg)
    elif prefix in ('setlang', 'setcurrency'):
        if (prefix == 'setlang' and arg not in ('ar', 'en')) or (prefix == 'setcurrency' and arg not in ('SAR', 'USD')):
            return
        with db() as conn:
            conn.execute('INSERT OR IGNORE INTO preferences(cid) VALUES (?)', (cid,))
            column = 'lang' if prefix == 'setlang' else 'currency'
            conn.execute(f'UPDATE preferences SET {column}=? WHERE cid=?', (arg, cid))
        home(api, cid)
    elif prefix in ('buy', 'cancel'):
        with db() as conn:
            conn.execute('DELETE FROM receipts WHERE cid=?', (cid,))
        if prefix == 'buy': payments(api, cid, arg)
        elif arg in VARIANTS: item(api, cid, arg)
        else: category(api, cid, arg)
    elif prefix == 'wallet':
        wallet_amounts(api, cid) if arg == 'topup' else wallet(api, cid)
    elif prefix == 'topup':
        wallet_method(api, cid, arg)
    elif prefix == 'topupcustom':
        with db() as conn: conn.execute('INSERT OR REPLACE INTO custom_topup_state(cid) VALUES (?)', (cid,))
        send(api, cid, tr(cid, '✏️ أرسل الآن مبلغ الشحن الذي تريده بالدولار.\nمثال: <b>$20</b>', '✏️ Send the custom top-up amount in USD.\nExample: <b>$20</b>'), kb([nav(cid, 'wallet:topup')]))
    elif prefix == 'topupcrypto':
        wallet_crypto(api, cid, arg)
    elif prefix == 'topupbybit':
        wallet_bybit(api, cid, arg)
    elif prefix == 'topupsend':
        method, _, topup_id = arg.partition(':'); wallet_bybit_details(api, cid, method, topup_id)
    elif prefix == 'topupreceipt':
        with db() as conn:
            changed = conn.execute('UPDATE wallet_topups SET status="receipt_pending" WHERE id=? AND cid=? AND status="pending"', (arg, cid)).rowcount
        if changed:
            send(api, cid, tr(cid, '📸 أرسل الآن صورة إثبات تحويل Bybit.', '📸 Send the Bybit payment receipt image now.'))
        else:
            wallet(api, cid)
    elif prefix == 'canceltopup':
        with db() as conn:
            conn.execute('UPDATE wallet_topups SET status="cancelled" WHERE id=? AND cid=? AND status IN ("pending","receipt_pending")', (arg, cid))
        wallet(api, cid)
    elif prefix == 'checktopup':
        check_wallet_crypto(api, cid, arg)
    elif prefix in ('approvetopup', 'rejecttopup'):
        review_topup(api, cid, arg, prefix == 'approvetopup')
    elif prefix == 'paywallet':
        pay_with_wallet(api, cid, arg)
    elif prefix == 'paycrypto':
        pay_with_crypto(api, cid, arg)
    elif prefix == 'checkorder':
        check_crypto_order(api, cid, arg)
    elif prefix in ('paybybit', 'bybitid', 'trc20', 'bep20'):
        payment(api, cid, arg, {'paybybit': 'bybit'}.get(prefix, prefix))
    elif prefix == 'receipt':
        method, _, pid = arg.partition(':')
        receipt_request(api, cid, LEGACY.get(pid, pid), method)
    elif prefix == 'support':
        send(api, cid, tr(cid, '💬 لشحن النقاط والدعم: ', '💬 Top-ups and support: ') + SUPPORT, menu(cid))
    elif prefix == 'api':
        send(api, cid, tr(cid, '🔗 إعدادات API المتجر غير مفعّلة حاليًا.', '🔗 Store API settings are not active yet.'), menu(cid))
    elif prefix == 'warranty':
        send(api, cid, tr(cid, '🛡 تختلف شروط الضمان حسب المنتج. راجع وصفه قبل الطلب. للاستفسار: ', '🛡 Warranty terms vary by product. Read its description before ordering. Questions: ') + SUPPORT, menu(cid))
    else:
        products(api, cid)


def install(namespace):
    global G
    G = namespace
    apply_icon_overrides()
    namespace.update({'show_start': start, 'show_home': home, 'show_products': products,
                      'show_product': category, 'show_claude_product': item, 'handle_action': action, 'action': action,
                      'handle_receipt': receipt, 'order_name': name, 'home_keyboard': menu,
                      'broadcast_new_products': broadcast_new_products})
    menu_actions = namespace.setdefault('MENU_ACTIONS', namespace.get('MENU', {}))
    menu_actions.update({'🚀 ابدأ': 'start', '🚀 Start': 'start', '🛍 المنتجات': 'products',
                         '🛍 Products': 'products', '💬 الدعم': 'support', '💬 Support': 'support',
                         '👛 المحفظة': 'wallet', '👛 Wallet': 'wallet', '🔗 API': 'api',
                         '🛡 الضمان': 'warranty', '🛡 Warranty': 'warranty',
                         '🌐 اللغة': 'settings:lang', '🌐 Language': 'settings:lang',
                         '🌐 اللغة / Language': 'settings:lang', '💱 العملة / Currency': 'settings:currency',
                         '🧾 لوحة الطلبات': 'admin'})
    # Accept reply buttons sent by older versions where the icon followed the label.
    menu_actions.update({'ابدأ 🚀': 'start', 'المنتجات 🛍': 'products', 'الدعم 💬': 'support',
                         'المحفظة 👛': 'wallet', 'الضمان 🛡': 'warranty',
                         'Start 🚀': 'start', 'Products 🛍': 'products', 'Support 💬': 'support'})
    namespace['MENU'] = menu_actions
, '')
        try:
            usd_value = Decimal(raw).quantize(Decimal('0.01'))
        except Exception:
            send(api, cid, tr(cid, 'أرسل المبلغ كرقم فقط، مثال: 20', 'Send the amount as a number only, e.g. 20'))
            return True
        if usd_value <= 0 or usd_value > Decimal('1333'):
            send(api, cid, tr(cid, 'اختر مبلغًا أكبر من $0 وحتى $1333.', 'Choose an amount above $0 and up to $1333.'))
            return True
        with db() as conn:
            conn.execute('DELETE FROM custom_topup_state WHERE cid=?', (cid,))
        value = (usd_value * RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        wallet_method(api, cid, str(value))
        return True
    menu_actions = G.get('MENU_ACTIONS', G.get('MENU', {}))
    if message.get('text', '').startswith('/') or message.get('text') in menu_actions:
        with db() as conn:
            conn.execute('DELETE FROM receipts WHERE cid=?', (cid,))
            conn.execute('UPDATE wallet_topups SET status="cancelled" WHERE cid=? AND status="receipt_pending"', (cid,))
        return False
    with db() as conn:
        topup = conn.execute('SELECT id,amount_sar,method FROM wallet_topups WHERE cid=? AND status="receipt_pending" ORDER BY rowid DESC LIMIT 1', (cid,)).fetchone()
    if topup:
        topup_id, topup_sar, topup_method = topup
        if not message.get('photo'):
            send(api, cid, tr(cid, '📸 أرسل صورة إثبات الدفع.', '📸 Send the payment receipt image.'), kb([[btn(tr(cid, '❌ إلغاء', '❌ Cancel'), 'canceltopup:' + topup_id)]]))
            return True
        user = message.get('from', {})
        username = '@' + user['username'] if user.get('username') else str(cid)
        sent = send(api, G['ADMIN_ID'], f'👛 <b>طلب شحن محفظة</b>\n\nالمبلغ: {esc(topup_sar)} SAR\nالطريقة: {esc(topup_method)}\nالعميل: {esc(username)}\nID: <code>{cid}</code>',
                    kb([[btn('✅ اعتماد الشحن', 'approvetopup:' + topup_id)], [btn('❌ رفض', 'rejecttopup:' + topup_id)]]))
        forwarded = api.call('forwardMessage', chat_id=G['ADMIN_ID'], from_chat_id=cid, message_id=message['message_id']) if sent else None
        if not forwarded:
            send(api, cid, tr(cid, 'تعذر إرسال الإثبات. حاول مرة أخرى.', 'Could not send the receipt. Try again.'))
            return True
        with db() as conn:
            conn.execute('UPDATE wallet_topups SET status="review" WHERE id=? AND status="receipt_pending"', (topup_id,))
        send(api, cid, tr(cid, '✅ وصل إثبات شحن المحفظة للإدارة للمراجعة.', '✅ Wallet top-up receipt sent for review.'), menu(cid))
        return True
    with db() as conn:
        pending = conn.execute('SELECT pid,method,usd,sar FROM receipts WHERE cid=?', (cid,)).fetchone()
    if not pending:
        return False
    if not message.get('photo'):
        send(api, cid, tr(cid, '📸 أرسل صورة إثبات الدفع، أو اضغط إلغاء.', '📸 Send a receipt photo, or tap Cancel.'), kb([[btn(tr(cid, '❌ إلغاء', '❌ Cancel'), 'cancel:' + pending[0])]]))
        return True
    pid, method, usd, sar = pending
    user = message.get('from', {})
    username = '@' + user['username'] if user.get('username') else str(cid)
    result = send(api, G['ADMIN_ID'], '🧾 <b>إثبات دفع جديد</b>\n\n' + esc(name(pid)) + f'\nالكمية: 1\nالسعر عند الطلب: {sar} SAR / {usd} USD\nالطريقة: {esc(method)}\nالعميل: {esc(username)}\nID: <code>{cid}</code>')
    forwarded = api.call('forwardMessage', chat_id=G['ADMIN_ID'], from_chat_id=cid, message_id=message['message_id']) if result else None
    if not forwarded:
        send(api, cid, tr(cid, 'تعذر إرسال الإثبات للإدارة. أعد المحاولة أو تواصل مع ', 'Could not forward the receipt. Retry or contact ') + SUPPORT)
        return True
    add_order(cid, pid, method, 'review')
    with db() as conn:
        conn.execute('DELETE FROM receipts WHERE cid=?', (cid,))
    send(api, cid, tr(cid, '✅ وصل الإثبات للإدارة للمراجعة. ستتم متابعة طلبك بعد التحقق.', '✅ Receipt sent for review. Your order will be followed up after verification.'), menu(cid))
    return True


def review_topup(api, actor, topup_id, approve):
    if actor != G['ADMIN_ID']:
        return
    with db() as conn:
        row = conn.execute('SELECT cid,amount_sar,status FROM wallet_topups WHERE id=?', (topup_id,)).fetchone()
        if not row or row[2] != 'review':
            return send(api, actor, 'تمت معالجة طلب الشحن مسبقًا.')
        status = 'credited' if approve else 'rejected'
        conn.execute('UPDATE wallet_topups SET status=? WHERE id=? AND status="review"', (status, topup_id))
    cid, value, _ = row
    if approve:
        balance = wallet_credit(cid, value)
        send(api, cid, f'✅ تم اعتماد شحن المحفظة بمبلغ {esc(value)} SAR.\nالرصيد الحالي: {balance:.2f} SAR', menu(cid))
        send(api, actor, f'✅ تم شحن محفظة العميل <code>{cid}</code> بمبلغ {esc(value)} SAR.')
    else:
        send(api, cid, tr(cid, '❌ لم تتم الموافقة على إثبات شحن المحفظة. تواصل مع الدعم.', '❌ Your wallet top-up receipt was not approved. Contact support.'), menu(cid))
        send(api, actor, '❌ تم رفض طلب شحن المحفظة.')


def action(api, cid, value):
    prefix, _, arg = value.partition(':')
    arg = LEGACY.get(arg, arg)
    if prefix in ('home', 'enter_store'):
        home(api, cid)
    elif prefix == 'start':
        start(api, cid)
    elif prefix == 'products':
        products(api, cid)
    elif prefix == 'referrals':
        referral_page(api, cid)
    elif prefix == 'product':
        log_activity(cid, 'category', arg)
        category(api, cid, arg)
    elif prefix in ('item', 'claude'):
        log_activity(cid, 'item', arg)
        item(api, cid, arg)
    elif value in LEGACY:
        log_activity(cid, 'item', LEGACY[value])
        item(api, cid, LEGACY[value])
    elif prefix == 'admin':
        if arg == 'orders': admin_orders(api, cid)
        elif arg == 'activity': admin_activity(api, cid)
        elif arg == 'icons': admin_icons(api, cid)
        elif arg == 'broadcast' and cid == G['ADMIN_ID']:
            BROADCAST_PENDING.add(cid)
            send(api, cid, '📢 <b>إرسال رسالة للجميع</b>\n\nأرسل الآن الرسالة التي تريد إرسالها لجميع مستخدمي البوت.\nيمكنك إرسال نص أو صورة مع تعليق.',
                 kb([[btn('❌ إلغاء', 'admin:broadcast_cancel')]]))
        elif arg == 'broadcast_cancel' and cid == G['ADMIN_ID']:
            BROADCAST_PENDING.discard(cid)
            admin_panel(api, cid)
        else: admin_panel(api, cid)
    elif prefix == 'seticon':
        begin_icon_setup(api, cid, arg)
    elif prefix == 'cancelicon':
        if cid == G['ADMIN_ID']:
            with db() as conn:
                conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
            admin_icons(api, cid)
    elif prefix == 'settings':
        settings(api, cid, arg)
    elif prefix in ('setlang', 'setcurrency'):
        if (prefix == 'setlang' and arg not in ('ar', 'en')) or (prefix == 'setcurrency' and arg not in ('SAR', 'USD')):
            return
        with db() as conn:
            conn.execute('INSERT OR IGNORE INTO preferences(cid) VALUES (?)', (cid,))
            column = 'lang' if prefix == 'setlang' else 'currency'
            conn.execute(f'UPDATE preferences SET {column}=? WHERE cid=?', (arg, cid))
        home(api, cid)
    elif prefix in ('buy', 'cancel'):
        with db() as conn:
            conn.execute('DELETE FROM receipts WHERE cid=?', (cid,))
        if prefix == 'buy': payments(api, cid, arg)
        elif arg in VARIANTS: item(api, cid, arg)
        else: category(api, cid, arg)
    elif prefix == 'wallet':
        wallet_amounts(api, cid) if arg == 'topup' else wallet(api, cid)
    elif prefix == 'topup':
        wallet_method(api, cid, arg)
    elif prefix == 'topupcustom':
        with db() as conn: conn.execute('INSERT OR REPLACE INTO custom_topup_state(cid) VALUES (?)', (cid,))
        send(api, cid, tr(cid, '✏️ أرسل الآن مبلغ الشحن الذي تريده بالدولار.\nمثال: <b>$20</b>', '✏️ Send the custom top-up amount in USD.\nExample: <b>$20</b>'), kb([nav(cid, 'wallet:topup')]))
    elif prefix == 'topupcrypto':
        wallet_crypto(api, cid, arg)
    elif prefix == 'topupbybit':
        wallet_bybit(api, cid, arg)
    elif prefix == 'topupsend':
        method, _, topup_id = arg.partition(':'); wallet_bybit_details(api, cid, method, topup_id)
    elif prefix == 'topupreceipt':
        with db() as conn:
            changed = conn.execute('UPDATE wallet_topups SET status="receipt_pending" WHERE id=? AND cid=? AND status="pending"', (arg, cid)).rowcount
        if changed:
            send(api, cid, tr(cid, '📸 أرسل الآن صورة إثبات تحويل Bybit.', '📸 Send the Bybit payment receipt image now.'))
        else:
            wallet(api, cid)
    elif prefix == 'canceltopup':
        with db() as conn:
            conn.execute('UPDATE wallet_topups SET status="cancelled" WHERE id=? AND cid=? AND status IN ("pending","receipt_pending")', (arg, cid))
        wallet(api, cid)
    elif prefix == 'checktopup':
        check_wallet_crypto(api, cid, arg)
    elif prefix in ('approvetopup', 'rejecttopup'):
        review_topup(api, cid, arg, prefix == 'approvetopup')
    elif prefix == 'paywallet':
        pay_with_wallet(api, cid, arg)
    elif prefix == 'paycrypto':
        pay_with_crypto(api, cid, arg)
    elif prefix == 'checkorder':
        check_crypto_order(api, cid, arg)
    elif prefix in ('paybybit', 'bybitid', 'trc20', 'bep20'):
        payment(api, cid, arg, {'paybybit': 'bybit'}.get(prefix, prefix))
    elif prefix == 'receipt':
        method, _, pid = arg.partition(':')
        receipt_request(api, cid, LEGACY.get(pid, pid), method)
    elif prefix == 'support':
        send(api, cid, tr(cid, '💬 لشحن النقاط والدعم: ', '💬 Top-ups and support: ') + SUPPORT, menu(cid))
    elif prefix == 'api':
        send(api, cid, tr(cid, '🔗 إعدادات API المتجر غير مفعّلة حاليًا.', '🔗 Store API settings are not active yet.'), menu(cid))
    elif prefix == 'warranty':
        send(api, cid, tr(cid, '🛡 تختلف شروط الضمان حسب المنتج. راجع وصفه قبل الطلب. للاستفسار: ', '🛡 Warranty terms vary by product. Read its description before ordering. Questions: ') + SUPPORT, menu(cid))
    else:
        products(api, cid)


def install(namespace):
    global G
    G = namespace
    apply_icon_overrides()
    namespace.update({'show_start': start, 'show_home': home, 'show_products': products,
                      'show_product': category, 'show_claude_product': item, 'handle_action': action, 'action': action,
                      'handle_receipt': receipt, 'order_name': name, 'home_keyboard': menu,
                      'broadcast_new_products': broadcast_new_products})
    menu_actions = namespace.setdefault('MENU_ACTIONS', namespace.get('MENU', {}))
    menu_actions.update({'🚀 ابدأ': 'start', '🚀 Start': 'start', '🛍 المنتجات': 'products',
                         '🛍 Products': 'products', '💬 الدعم': 'support', '💬 Support': 'support',
                         '👛 المحفظة': 'wallet', '👛 Wallet': 'wallet', '🔗 API': 'api',
                         '🛡 الضمان': 'warranty', '🛡 Warranty': 'warranty',
                         '🌐 اللغة': 'settings:lang', '🌐 Language': 'settings:lang',
                         '🌐 اللغة / Language': 'settings:lang', '💱 العملة / Currency': 'settings:currency',
                         '🧾 لوحة الطلبات': 'admin'})
    # Accept reply buttons sent by older versions where the icon followed the label.
    menu_actions.update({'ابدأ 🚀': 'start', 'المنتجات 🛍': 'products', 'الدعم 💬': 'support',
                         'المحفظة 👛': 'wallet', 'الضمان 🛡': 'warranty',
                         'Start 🚀': 'start', 'Products 🛍': 'products', 'Support 💬': 'support'})
    namespace['MENU'] = menu_actions
):
            raw = raw[1:].strip()
        try:
            usd_value = Decimal(raw).quantize(Decimal('0.01'))
        except Exception:
            send(api, cid, tr(cid, 'أرسل المبلغ كرقم فقط، مثال: 20', 'Send the amount as a number only, e.g. 20'))
            return True
        if usd_value <= 0 or usd_value > Decimal('1333'):
            send(api, cid, tr(cid, 'اختر مبلغًا أكبر من $0 وحتى $1333.', 'Choose an amount above $0 and up to $1333.'))
            return True
        with db() as conn:
            conn.execute('DELETE FROM custom_topup_state WHERE cid=?', (cid,))
        value = (usd_value * RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        wallet_method(api, cid, str(value))
        return True
    menu_actions = G.get('MENU_ACTIONS', G.get('MENU', {}))
    if message.get('text', '').startswith('/') or message.get('text') in menu_actions:
        with db() as conn:
            conn.execute('DELETE FROM receipts WHERE cid=?', (cid,))
            conn.execute('UPDATE wallet_topups SET status="cancelled" WHERE cid=? AND status="receipt_pending"', (cid,))
        return False
    with db() as conn:
        topup = conn.execute('SELECT id,amount_sar,method FROM wallet_topups WHERE cid=? AND status="receipt_pending" ORDER BY rowid DESC LIMIT 1', (cid,)).fetchone()
    if topup:
        topup_id, topup_sar, topup_method = topup
        if not message.get('photo'):
            send(api, cid, tr(cid, '📸 أرسل صورة إثبات الدفع.', '📸 Send the payment receipt image.'), kb([[btn(tr(cid, '❌ إلغاء', '❌ Cancel'), 'canceltopup:' + topup_id)]]))
            return True
        user = message.get('from', {})
        username = '@' + user['username'] if user.get('username') else str(cid)
        sent = send(api, G['ADMIN_ID'], f'👛 <b>طلب شحن محفظة</b>\n\nالمبلغ: {esc(topup_sar)} SAR\nالطريقة: {esc(topup_method)}\nالعميل: {esc(username)}\nID: <code>{cid}</code>',
                    kb([[btn('✅ اعتماد الشحن', 'approvetopup:' + topup_id)], [btn('❌ رفض', 'rejecttopup:' + topup_id)]]))
        forwarded = api.call('forwardMessage', chat_id=G['ADMIN_ID'], from_chat_id=cid, message_id=message['message_id']) if sent else None
        if not forwarded:
            send(api, cid, tr(cid, 'تعذر إرسال الإثبات. حاول مرة أخرى.', 'Could not send the receipt. Try again.'))
            return True
        with db() as conn:
            conn.execute('UPDATE wallet_topups SET status="review" WHERE id=? AND status="receipt_pending"', (topup_id,))
        send(api, cid, tr(cid, '✅ وصل إثبات شحن المحفظة للإدارة للمراجعة.', '✅ Wallet top-up receipt sent for review.'), menu(cid))
        return True
    with db() as conn:
        pending = conn.execute('SELECT pid,method,usd,sar FROM receipts WHERE cid=?', (cid,)).fetchone()
    if not pending:
        return False
    if not message.get('photo'):
        send(api, cid, tr(cid, '📸 أرسل صورة إثبات الدفع، أو اضغط إلغاء.', '📸 Send a receipt photo, or tap Cancel.'), kb([[btn(tr(cid, '❌ إلغاء', '❌ Cancel'), 'cancel:' + pending[0])]]))
        return True
    pid, method, usd, sar = pending
    user = message.get('from', {})
    username = '@' + user['username'] if user.get('username') else str(cid)
    result = send(api, G['ADMIN_ID'], '🧾 <b>إثبات دفع جديد</b>\n\n' + esc(name(pid)) + f'\nالكمية: 1\nالسعر عند الطلب: {sar} SAR / {usd} USD\nالطريقة: {esc(method)}\nالعميل: {esc(username)}\nID: <code>{cid}</code>')
    forwarded = api.call('forwardMessage', chat_id=G['ADMIN_ID'], from_chat_id=cid, message_id=message['message_id']) if result else None
    if not forwarded:
        send(api, cid, tr(cid, 'تعذر إرسال الإثبات للإدارة. أعد المحاولة أو تواصل مع ', 'Could not forward the receipt. Retry or contact ') + SUPPORT)
        return True
    add_order(cid, pid, method, 'review')
    with db() as conn:
        conn.execute('DELETE FROM receipts WHERE cid=?', (cid,))
    send(api, cid, tr(cid, '✅ وصل الإثبات للإدارة للمراجعة. ستتم متابعة طلبك بعد التحقق.', '✅ Receipt sent for review. Your order will be followed up after verification.'), menu(cid))
    return True


def review_topup(api, actor, topup_id, approve):
    if actor != G['ADMIN_ID']:
        return
    with db() as conn:
        row = conn.execute('SELECT cid,amount_sar,status FROM wallet_topups WHERE id=?', (topup_id,)).fetchone()
        if not row or row[2] != 'review':
            return send(api, actor, 'تمت معالجة طلب الشحن مسبقًا.')
        status = 'credited' if approve else 'rejected'
        conn.execute('UPDATE wallet_topups SET status=? WHERE id=? AND status="review"', (status, topup_id))
    cid, value, _ = row
    if approve:
        balance = wallet_credit(cid, value)
        send(api, cid, f'✅ تم اعتماد شحن المحفظة بمبلغ {esc(value)} SAR.\nالرصيد الحالي: {balance:.2f} SAR', menu(cid))
        send(api, actor, f'✅ تم شحن محفظة العميل <code>{cid}</code> بمبلغ {esc(value)} SAR.')
    else:
        send(api, cid, tr(cid, '❌ لم تتم الموافقة على إثبات شحن المحفظة. تواصل مع الدعم.', '❌ Your wallet top-up receipt was not approved. Contact support.'), menu(cid))
        send(api, actor, '❌ تم رفض طلب شحن المحفظة.')


def action(api, cid, value):
    prefix, _, arg = value.partition(':')
    arg = LEGACY.get(arg, arg)
    if prefix in ('home', 'enter_store'):
        home(api, cid)
    elif prefix == 'start':
        start(api, cid)
    elif prefix == 'products':
        products(api, cid)
    elif prefix == 'referrals':
        referral_page(api, cid)
    elif prefix == 'product':
        log_activity(cid, 'category', arg)
        category(api, cid, arg)
    elif prefix in ('item', 'claude'):
        log_activity(cid, 'item', arg)
        item(api, cid, arg)
    elif value in LEGACY:
        log_activity(cid, 'item', LEGACY[value])
        item(api, cid, LEGACY[value])
    elif prefix == 'admin':
        if arg == 'orders': admin_orders(api, cid)
        elif arg == 'activity': admin_activity(api, cid)
        elif arg == 'icons': admin_icons(api, cid)
        elif arg == 'broadcast' and cid == G['ADMIN_ID']:
            BROADCAST_PENDING.add(cid)
            send(api, cid, '📢 <b>إرسال رسالة للجميع</b>\n\nأرسل الآن الرسالة التي تريد إرسالها لجميع مستخدمي البوت.\nيمكنك إرسال نص أو صورة مع تعليق.',
                 kb([[btn('❌ إلغاء', 'admin:broadcast_cancel')]]))
        elif arg == 'broadcast_cancel' and cid == G['ADMIN_ID']:
            BROADCAST_PENDING.discard(cid)
            admin_panel(api, cid)
        else: admin_panel(api, cid)
    elif prefix == 'seticon':
        begin_icon_setup(api, cid, arg)
    elif prefix == 'cancelicon':
        if cid == G['ADMIN_ID']:
            with db() as conn:
                conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
            admin_icons(api, cid)
    elif prefix == 'settings':
        settings(api, cid, arg)
    elif prefix in ('setlang', 'setcurrency'):
        if (prefix == 'setlang' and arg not in ('ar', 'en')) or (prefix == 'setcurrency' and arg not in ('SAR', 'USD')):
            return
        with db() as conn:
            conn.execute('INSERT OR IGNORE INTO preferences(cid) VALUES (?)', (cid,))
            column = 'lang' if prefix == 'setlang' else 'currency'
            conn.execute(f'UPDATE preferences SET {column}=? WHERE cid=?', (arg, cid))
        home(api, cid)
    elif prefix in ('buy', 'cancel'):
        with db() as conn:
            conn.execute('DELETE FROM receipts WHERE cid=?', (cid,))
        if prefix == 'buy': payments(api, cid, arg)
        elif arg in VARIANTS: item(api, cid, arg)
        else: category(api, cid, arg)
    elif prefix == 'wallet':
        wallet_amounts(api, cid) if arg == 'topup' else wallet(api, cid)
    elif prefix == 'topup':
        wallet_method(api, cid, arg)
    elif prefix == 'topupcustom':
        with db() as conn: conn.execute('INSERT OR REPLACE INTO custom_topup_state(cid) VALUES (?)', (cid,))
        send(api, cid, tr(cid, '✏️ أرسل الآن مبلغ الشحن الذي تريده بالدولار.\nمثال: <b>$20</b>', '✏️ Send the custom top-up amount in USD.\nExample: <b>$20</b>'), kb([nav(cid, 'wallet:topup')]))
    elif prefix == 'topupcrypto':
        wallet_crypto(api, cid, arg)
    elif prefix == 'topupbybit':
        wallet_bybit(api, cid, arg)
    elif prefix == 'topupsend':
        method, _, topup_id = arg.partition(':'); wallet_bybit_details(api, cid, method, topup_id)
    elif prefix == 'topupreceipt':
        with db() as conn:
            changed = conn.execute('UPDATE wallet_topups SET status="receipt_pending" WHERE id=? AND cid=? AND status="pending"', (arg, cid)).rowcount
        if changed:
            send(api, cid, tr(cid, '📸 أرسل الآن صورة إثبات تحويل Bybit.', '📸 Send the Bybit payment receipt image now.'))
        else:
            wallet(api, cid)
    elif prefix == 'canceltopup':
        with db() as conn:
            conn.execute('UPDATE wallet_topups SET status="cancelled" WHERE id=? AND cid=? AND status IN ("pending","receipt_pending")', (arg, cid))
        wallet(api, cid)
    elif prefix == 'checktopup':
        check_wallet_crypto(api, cid, arg)
    elif prefix in ('approvetopup', 'rejecttopup'):
        review_topup(api, cid, arg, prefix == 'approvetopup')
    elif prefix == 'paywallet':
        pay_with_wallet(api, cid, arg)
    elif prefix == 'paycrypto':
        pay_with_crypto(api, cid, arg)
    elif prefix == 'checkorder':
        check_crypto_order(api, cid, arg)
    elif prefix in ('paybybit', 'bybitid', 'trc20', 'bep20'):
        payment(api, cid, arg, {'paybybit': 'bybit'}.get(prefix, prefix))
    elif prefix == 'receipt':
        method, _, pid = arg.partition(':')
        receipt_request(api, cid, LEGACY.get(pid, pid), method)
    elif prefix == 'support':
        send(api, cid, tr(cid, '💬 لشحن النقاط والدعم: ', '💬 Top-ups and support: ') + SUPPORT, menu(cid))
    elif prefix == 'api':
        send(api, cid, tr(cid, '🔗 إعدادات API المتجر غير مفعّلة حاليًا.', '🔗 Store API settings are not active yet.'), menu(cid))
    elif prefix == 'warranty':
        send(api, cid, tr(cid, '🛡 تختلف شروط الضمان حسب المنتج. راجع وصفه قبل الطلب. للاستفسار: ', '🛡 Warranty terms vary by product. Read its description before ordering. Questions: ') + SUPPORT, menu(cid))
    else:
        products(api, cid)


def install(namespace):
    global G
    G = namespace
    apply_icon_overrides()
    namespace.update({'show_start': start, 'show_home': home, 'show_products': products,
                      'show_product': category, 'show_claude_product': item, 'handle_action': action, 'action': action,
                      'handle_receipt': receipt, 'order_name': name, 'home_keyboard': menu,
                      'broadcast_new_products': broadcast_new_products})
    menu_actions = namespace.setdefault('MENU_ACTIONS', namespace.get('MENU', {}))
    menu_actions.update({'🚀 ابدأ': 'start', '🚀 Start': 'start', '🛍 المنتجات': 'products',
                         '🛍 Products': 'products', '💬 الدعم': 'support', '💬 Support': 'support',
                         '👛 المحفظة': 'wallet', '👛 Wallet': 'wallet', '🔗 API': 'api',
                         '🛡 الضمان': 'warranty', '🛡 Warranty': 'warranty',
                         '🌐 اللغة': 'settings:lang', '🌐 Language': 'settings:lang',
                         '🌐 اللغة / Language': 'settings:lang', '💱 العملة / Currency': 'settings:currency',
                         '🧾 لوحة الطلبات': 'admin'})
    # Accept reply buttons sent by older versions where the icon followed the label.
    menu_actions.update({'ابدأ 🚀': 'start', 'المنتجات 🛍': 'products', 'الدعم 💬': 'support',
                         'المحفظة 👛': 'wallet', 'الضمان 🛡': 'warranty',
                         'Start 🚀': 'start', 'Products 🛍': 'products', 'Support 💬': 'support'})
    namespace['MENU'] = menu_actions
, '')
        try:
            usd_value = Decimal(raw).quantize(Decimal('0.01'))
        except Exception:
            send(api, cid, tr(cid, 'أرسل المبلغ كرقم فقط، مثال: 20', 'Send the amount as a number only, e.g. 20'))
            return True
        if usd_value <= 0 or usd_value > Decimal('1333'):
            send(api, cid, tr(cid, 'اختر مبلغًا أكبر من $0 وحتى $1333.', 'Choose an amount above $0 and up to $1333.'))
            return True
        with db() as conn:
            conn.execute('DELETE FROM custom_topup_state WHERE cid=?', (cid,))
        value = (usd_value * RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        wallet_method(api, cid, str(value))
        return True
    menu_actions = G.get('MENU_ACTIONS', G.get('MENU', {}))
    if message.get('text', '').startswith('/') or message.get('text') in menu_actions:
        with db() as conn:
            conn.execute('DELETE FROM receipts WHERE cid=?', (cid,))
            conn.execute('UPDATE wallet_topups SET status="cancelled" WHERE cid=? AND status="receipt_pending"', (cid,))
        return False
    with db() as conn:
        topup = conn.execute('SELECT id,amount_sar,method FROM wallet_topups WHERE cid=? AND status="receipt_pending" ORDER BY rowid DESC LIMIT 1', (cid,)).fetchone()
    if topup:
        topup_id, topup_sar, topup_method = topup
        if not message.get('photo'):
            send(api, cid, tr(cid, '📸 أرسل صورة إثبات الدفع.', '📸 Send the payment receipt image.'), kb([[btn(tr(cid, '❌ إلغاء', '❌ Cancel'), 'canceltopup:' + topup_id)]]))
            return True
        user = message.get('from', {})
        username = '@' + user['username'] if user.get('username') else str(cid)
        sent = send(api, G['ADMIN_ID'], f'👛 <b>طلب شحن محفظة</b>\n\nالمبلغ: {esc(topup_sar)} SAR\nالطريقة: {esc(topup_method)}\nالعميل: {esc(username)}\nID: <code>{cid}</code>',
                    kb([[btn('✅ اعتماد الشحن', 'approvetopup:' + topup_id)], [btn('❌ رفض', 'rejecttopup:' + topup_id)]]))
        forwarded = api.call('forwardMessage', chat_id=G['ADMIN_ID'], from_chat_id=cid, message_id=message['message_id']) if sent else None
        if not forwarded:
            send(api, cid, tr(cid, 'تعذر إرسال الإثبات. حاول مرة أخرى.', 'Could not send the receipt. Try again.'))
            return True
        with db() as conn:
            conn.execute('UPDATE wallet_topups SET status="review" WHERE id=? AND status="receipt_pending"', (topup_id,))
        send(api, cid, tr(cid, '✅ وصل إثبات شحن المحفظة للإدارة للمراجعة.', '✅ Wallet top-up receipt sent for review.'), menu(cid))
        return True
    with db() as conn:
        pending = conn.execute('SELECT pid,method,usd,sar FROM receipts WHERE cid=?', (cid,)).fetchone()
    if not pending:
        return False
    if not message.get('photo'):
        send(api, cid, tr(cid, '📸 أرسل صورة إثبات الدفع، أو اضغط إلغاء.', '📸 Send a receipt photo, or tap Cancel.'), kb([[btn(tr(cid, '❌ إلغاء', '❌ Cancel'), 'cancel:' + pending[0])]]))
        return True
    pid, method, usd, sar = pending
    user = message.get('from', {})
    username = '@' + user['username'] if user.get('username') else str(cid)
    result = send(api, G['ADMIN_ID'], '🧾 <b>إثبات دفع جديد</b>\n\n' + esc(name(pid)) + f'\nالكمية: 1\nالسعر عند الطلب: {sar} SAR / {usd} USD\nالطريقة: {esc(method)}\nالعميل: {esc(username)}\nID: <code>{cid}</code>')
    forwarded = api.call('forwardMessage', chat_id=G['ADMIN_ID'], from_chat_id=cid, message_id=message['message_id']) if result else None
    if not forwarded:
        send(api, cid, tr(cid, 'تعذر إرسال الإثبات للإدارة. أعد المحاولة أو تواصل مع ', 'Could not forward the receipt. Retry or contact ') + SUPPORT)
        return True
    add_order(cid, pid, method, 'review')
    with db() as conn:
        conn.execute('DELETE FROM receipts WHERE cid=?', (cid,))
    send(api, cid, tr(cid, '✅ وصل الإثبات للإدارة للمراجعة. ستتم متابعة طلبك بعد التحقق.', '✅ Receipt sent for review. Your order will be followed up after verification.'), menu(cid))
    return True


def review_topup(api, actor, topup_id, approve):
    if actor != G['ADMIN_ID']:
        return
    with db() as conn:
        row = conn.execute('SELECT cid,amount_sar,status FROM wallet_topups WHERE id=?', (topup_id,)).fetchone()
        if not row or row[2] != 'review':
            return send(api, actor, 'تمت معالجة طلب الشحن مسبقًا.')
        status = 'credited' if approve else 'rejected'
        conn.execute('UPDATE wallet_topups SET status=? WHERE id=? AND status="review"', (status, topup_id))
    cid, value, _ = row
    if approve:
        balance = wallet_credit(cid, value)
        send(api, cid, f'✅ تم اعتماد شحن المحفظة بمبلغ {esc(value)} SAR.\nالرصيد الحالي: {balance:.2f} SAR', menu(cid))
        send(api, actor, f'✅ تم شحن محفظة العميل <code>{cid}</code> بمبلغ {esc(value)} SAR.')
    else:
        send(api, cid, tr(cid, '❌ لم تتم الموافقة على إثبات شحن المحفظة. تواصل مع الدعم.', '❌ Your wallet top-up receipt was not approved. Contact support.'), menu(cid))
        send(api, actor, '❌ تم رفض طلب شحن المحفظة.')


def action(api, cid, value):
    prefix, _, arg = value.partition(':')
    arg = LEGACY.get(arg, arg)
    if prefix in ('home', 'enter_store'):
        home(api, cid)
    elif prefix == 'start':
        start(api, cid)
    elif prefix == 'products':
        products(api, cid)
    elif prefix == 'referrals':
        referral_page(api, cid)
    elif prefix == 'product':
        log_activity(cid, 'category', arg)
        category(api, cid, arg)
    elif prefix in ('item', 'claude'):
        log_activity(cid, 'item', arg)
        item(api, cid, arg)
    elif value in LEGACY:
        log_activity(cid, 'item', LEGACY[value])
        item(api, cid, LEGACY[value])
    elif prefix == 'admin':
        if arg == 'orders': admin_orders(api, cid)
        elif arg == 'activity': admin_activity(api, cid)
        elif arg == 'icons': admin_icons(api, cid)
        elif arg == 'broadcast' and cid == G['ADMIN_ID']:
            BROADCAST_PENDING.add(cid)
            send(api, cid, '📢 <b>إرسال رسالة للجميع</b>\n\nأرسل الآن الرسالة التي تريد إرسالها لجميع مستخدمي البوت.\nيمكنك إرسال نص أو صورة مع تعليق.',
                 kb([[btn('❌ إلغاء', 'admin:broadcast_cancel')]]))
        elif arg == 'broadcast_cancel' and cid == G['ADMIN_ID']:
            BROADCAST_PENDING.discard(cid)
            admin_panel(api, cid)
        else: admin_panel(api, cid)
    elif prefix == 'seticon':
        begin_icon_setup(api, cid, arg)
    elif prefix == 'cancelicon':
        if cid == G['ADMIN_ID']:
            with db() as conn:
                conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
            admin_icons(api, cid)
    elif prefix == 'settings':
        settings(api, cid, arg)
    elif prefix in ('setlang', 'setcurrency'):
        if (prefix == 'setlang' and arg not in ('ar', 'en')) or (prefix == 'setcurrency' and arg not in ('SAR', 'USD')):
            return
        with db() as conn:
            conn.execute('INSERT OR IGNORE INTO preferences(cid) VALUES (?)', (cid,))
            column = 'lang' if prefix == 'setlang' else 'currency'
            conn.execute(f'UPDATE preferences SET {column}=? WHERE cid=?', (arg, cid))
        home(api, cid)
    elif prefix in ('buy', 'cancel'):
        with db() as conn:
            conn.execute('DELETE FROM receipts WHERE cid=?', (cid,))
        if prefix == 'buy': payments(api, cid, arg)
        elif arg in VARIANTS: item(api, cid, arg)
        else: category(api, cid, arg)
    elif prefix == 'wallet':
        wallet_amounts(api, cid) if arg == 'topup' else wallet(api, cid)
    elif prefix == 'topup':
        wallet_method(api, cid, arg)
    elif prefix == 'topupcustom':
        with db() as conn: conn.execute('INSERT OR REPLACE INTO custom_topup_state(cid) VALUES (?)', (cid,))
        send(api, cid, tr(cid, '✏️ أرسل الآن مبلغ الشحن الذي تريده بالدولار.\nمثال: <b>$20</b>', '✏️ Send the custom top-up amount in USD.\nExample: <b>$20</b>'), kb([nav(cid, 'wallet:topup')]))
    elif prefix == 'topupcrypto':
        wallet_crypto(api, cid, arg)
    elif prefix == 'topupbybit':
        wallet_bybit(api, cid, arg)
    elif prefix == 'topupsend':
        method, _, topup_id = arg.partition(':'); wallet_bybit_details(api, cid, method, topup_id)
    elif prefix == 'topupreceipt':
        with db() as conn:
            changed = conn.execute('UPDATE wallet_topups SET status="receipt_pending" WHERE id=? AND cid=? AND status="pending"', (arg, cid)).rowcount
        if changed:
            send(api, cid, tr(cid, '📸 أرسل الآن صورة إثبات تحويل Bybit.', '📸 Send the Bybit payment receipt image now.'))
        else:
            wallet(api, cid)
    elif prefix == 'canceltopup':
        with db() as conn:
            conn.execute('UPDATE wallet_topups SET status="cancelled" WHERE id=? AND cid=? AND status IN ("pending","receipt_pending")', (arg, cid))
        wallet(api, cid)
    elif prefix == 'checktopup':
        check_wallet_crypto(api, cid, arg)
    elif prefix in ('approvetopup', 'rejecttopup'):
        review_topup(api, cid, arg, prefix == 'approvetopup')
    elif prefix == 'paywallet':
        pay_with_wallet(api, cid, arg)
    elif prefix == 'paycrypto':
        pay_with_crypto(api, cid, arg)
    elif prefix == 'checkorder':
        check_crypto_order(api, cid, arg)
    elif prefix in ('paybybit', 'bybitid', 'trc20', 'bep20'):
        payment(api, cid, arg, {'paybybit': 'bybit'}.get(prefix, prefix))
    elif prefix == 'receipt':
        method, _, pid = arg.partition(':')
        receipt_request(api, cid, LEGACY.get(pid, pid), method)
    elif prefix == 'support':
        send(api, cid, tr(cid, '💬 لشحن النقاط والدعم: ', '💬 Top-ups and support: ') + SUPPORT, menu(cid))
    elif prefix == 'api':
        send(api, cid, tr(cid, '🔗 إعدادات API المتجر غير مفعّلة حاليًا.', '🔗 Store API settings are not active yet.'), menu(cid))
    elif prefix == 'warranty':
        send(api, cid, tr(cid, '🛡 تختلف شروط الضمان حسب المنتج. راجع وصفه قبل الطلب. للاستفسار: ', '🛡 Warranty terms vary by product. Read its description before ordering. Questions: ') + SUPPORT, menu(cid))
    else:
        products(api, cid)


def install(namespace):
    global G
    G = namespace
    apply_icon_overrides()
    namespace.update({'show_start': start, 'show_home': home, 'show_products': products,
                      'show_product': category, 'show_claude_product': item, 'handle_action': action, 'action': action,
                      'handle_receipt': receipt, 'order_name': name, 'home_keyboard': menu,
                      'broadcast_new_products': broadcast_new_products})
    menu_actions = namespace.setdefault('MENU_ACTIONS', namespace.get('MENU', {}))
    menu_actions.update({'🚀 ابدأ': 'start', '🚀 Start': 'start', '🛍 المنتجات': 'products',
                         '🛍 Products': 'products', '💬 الدعم': 'support', '💬 Support': 'support',
                         '👛 المحفظة': 'wallet', '👛 Wallet': 'wallet', '🔗 API': 'api',
                         '🛡 الضمان': 'warranty', '🛡 Warranty': 'warranty',
                         '🌐 اللغة': 'settings:lang', '🌐 Language': 'settings:lang',
                         '🌐 اللغة / Language': 'settings:lang', '💱 العملة / Currency': 'settings:currency',
                         '🧾 لوحة الطلبات': 'admin'})
    # Accept reply buttons sent by older versions where the icon followed the label.
    menu_actions.update({'ابدأ 🚀': 'start', 'المنتجات 🛍': 'products', 'الدعم 💬': 'support',
                         'المحفظة 👛': 'wallet', 'الضمان 🛡': 'warranty',
                         'Start 🚀': 'start', 'Products 🛍': 'products', 'Support 💬': 'support'})
    namespace['MENU'] = menu_actions
