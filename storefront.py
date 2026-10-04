"""Bilingual catalogue extension; preserves the existing bot/admin entry points."""
import html
import sys
import discounts
import payment_methods
import product_options
import json
import unicodedata

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
SUPPORT = '@m7mmd2030_1'
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
    conn.execute('CREATE TABLE IF NOT EXISTS ui_button_labels (key TEXT PRIMARY KEY, label TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS admin_state (cid INTEGER PRIMARY KEY, action TEXT NOT NULL, value TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS referrals (invitee INTEGER PRIMARY KEY, referrer INTEGER NOT NULL, joined_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 0, purchase_rewarded INTEGER NOT NULL DEFAULT 0)')
    conn.execute('CREATE TABLE IF NOT EXISTS referral_rewards (id INTEGER PRIMARY KEY AUTOINCREMENT, referrer INTEGER NOT NULL, kind TEXT NOT NULL, amount_usd TEXT NOT NULL, created_at TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS custom_topup_state (cid INTEGER PRIMARY KEY)')
    conn.execute('CREATE TABLE IF NOT EXISTS wallet_transfer_state (cid INTEGER PRIMARY KEY, step TEXT NOT NULL, recipient INTEGER, amount_sar TEXT)')
    conn.execute('CREATE TABLE IF NOT EXISTS wallet_transfers (id TEXT PRIMARY KEY, sender INTEGER NOT NULL, recipient INTEGER NOT NULL, amount_sar TEXT NOT NULL, created_at TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS user_delivery_status (cid INTEGER PRIMARY KEY, departed INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL DEFAULT "")')
    conn.execute('CREATE TABLE IF NOT EXISTS broadcast_stats (id INTEGER PRIMARY KEY CHECK(id=1), sent INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT "")')
    conn.execute('CREATE TABLE IF NOT EXISTS product_prices (pid TEXT PRIMARY KEY, value TEXT NOT NULL, currency TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS product_availability (pid TEXT PRIMARY KEY, available INTEGER NOT NULL CHECK(available IN (0,1)))')
    conn.execute('CREATE TABLE IF NOT EXISTS product_stock_overrides (pid TEXT PRIMARY KEY, stock INTEGER NOT NULL DEFAULT 0)')
    conn.execute('CREATE TABLE IF NOT EXISTS supplier_api (pid TEXT PRIMARY KEY, endpoint TEXT NOT NULL DEFAULT "", api_key TEXT NOT NULL DEFAULT "", service_id TEXT NOT NULL DEFAULT "", enabled INTEGER NOT NULL DEFAULT 0, provider TEXT NOT NULL DEFAULT "generic", variant_id TEXT NOT NULL DEFAULT "")')
    try:
        conn.execute('ALTER TABLE supplier_api ADD COLUMN variant_id TEXT NOT NULL DEFAULT ""')
    except Exception:
        pass
    conn.execute('CREATE TABLE IF NOT EXISTS supplier_orders (order_id TEXT PRIMARY KEY, supplier_order_id TEXT NOT NULL DEFAULT "", status TEXT NOT NULL DEFAULT "", delivery TEXT NOT NULL DEFAULT "", last_error TEXT NOT NULL DEFAULT "", updated_at TEXT NOT NULL DEFAULT "")')
    conn.execute('CREATE TABLE IF NOT EXISTS pandora_pricing (pid TEXT PRIMARY KEY, supplier_cost_usd TEXT NOT NULL DEFAULT "", margin_usd TEXT NOT NULL DEFAULT "0", updated_at TEXT NOT NULL DEFAULT "")')
    try:
        conn.execute('ALTER TABLE supplier_api ADD COLUMN provider TEXT NOT NULL DEFAULT "generic"')
    except Exception:
        pass
    conn.execute('CREATE TABLE IF NOT EXISTS product_visibility (pid TEXT PRIMARY KEY, visible INTEGER NOT NULL CHECK(visible IN (0,1)))')
    conn.execute('CREATE TABLE IF NOT EXISTS admin_products (pid TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT "", price_sar TEXT NOT NULL, available INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS admin_categories (cid TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL)')
    cols = {row[1] for row in conn.execute('PRAGMA table_info(admin_products)').fetchall()}
    if 'category_id' not in cols:
        conn.execute('ALTER TABLE admin_products ADD COLUMN category_id TEXT')
    if 'price_usd' not in cols:
        conn.execute('ALTER TABLE admin_products ADD COLUMN price_usd TEXT')
    if 'stock' not in cols:
        conn.execute('ALTER TABLE admin_products ADD COLUMN stock INTEGER NOT NULL DEFAULT 1')
    conn.execute('CREATE TABLE IF NOT EXISTS product_text (pid TEXT NOT NULL, field TEXT NOT NULL, lang TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(pid,field,lang))')
    conn.execute('CREATE TABLE IF NOT EXISTS description_emoji (pid TEXT, lang TEXT, plain TEXT NOT NULL, html TEXT NOT NULL, PRIMARY KEY(pid,lang))')
    conn.execute('CREATE TABLE IF NOT EXISTS product_photos (pid TEXT PRIMARY KEY, file_id TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS content_migrations (key TEXT PRIMARY KEY, applied_at TEXT NOT NULL)')
    if not conn.execute("SELECT 1 FROM content_migrations WHERE key='concise_product_descriptions_v1'").fetchone():
        rows = conn.execute('SELECT pid,name FROM admin_products').fetchall()
        for product_pid, product_name in rows:
            low = (product_name or '').lower()
            ar_desc = ''
            en_desc = ''
            if 'youtube' in low and ('family' in low or 'دعوة' in product_name or 'عائل' in product_name):
                ar_desc = 'YouTube Premium دعوة عائلية لمدة شهر.\nتفعيل على حسابك الشخصي.'
                en_desc = 'YouTube Premium family invitation for 1 month.\nActivated on your personal account.'
            elif 'youtube' in low and ('full' in low or 'private' in low or 'كامل' in product_name or 'خاص' in product_name):
                ar_desc = 'YouTube Premium حساب كامل خاص لمدة شهر.\nالحساب للاستخدام الشخصي.'
                en_desc = 'YouTube Premium private full account for 1 month.\nFor personal use.'
            elif 'crunchy' in low and ('profile' in low or 'ملف' in product_name):
                ar_desc = 'Crunchyroll ملف خاص لمدة شهر.\nمخصص للاستخدام الشخصي.'
                en_desc = 'Crunchyroll private profile for 1 month.\nFor personal use.'
            elif 'crunchy' in low and ('7d' in low or '7 day' in low or '7 أيام' in product_name or '7 ايام' in product_name):
                ar_desc = 'Crunchyroll حساب كامل لمدة 7 أيام.\nجاهز للاستخدام.'
                en_desc = 'Crunchyroll full account for 7 days.\nReady to use.'
            elif 'crunchy' in low and ('1m' in low or 'month' in low or 'شهر' in product_name):
                ar_desc = 'Crunchyroll حساب كامل لمدة شهر.\nجاهز للاستخدام.'
                en_desc = 'Crunchyroll full account for 1 month.\nReady to use.'
            elif 'shahid' in low or 'شاهد' in product_name:
                ar_desc = 'Shahid VIP اشتراك خاص لمدة شهر.\nمخصص للاستخدام الشخصي.'
                en_desc = 'Shahid VIP private subscription for 1 month.\nFor personal use.'
            else:
                ar_desc = 'منتج ' + product_name + '.\nيتم التسليم بعد تأكيد الطلب.'
                en_desc = product_name + ' product.\nDelivered after order confirmation.'
            conn.execute('UPDATE admin_products SET description=? WHERE pid=?', (ar_desc[:1500], product_pid))
            conn.execute('INSERT OR REPLACE INTO product_text(pid,field,lang,value) VALUES (?,?,?,?)',
                         (product_pid, 'description', 'en', en_desc[:1500]))
        conn.execute("INSERT OR REPLACE INTO content_migrations(key,applied_at) VALUES ('concise_product_descriptions_v1', datetime('now'))")
    conn.execute('CREATE TABLE IF NOT EXISTS product_info_display (pid TEXT PRIMARY KEY, show_price INTEGER NOT NULL DEFAULT 1, show_stock INTEGER NOT NULL DEFAULT 1, show_warranty INTEGER NOT NULL DEFAULT 0, warranty TEXT NOT NULL DEFAULT "")')
    conn.execute('CREATE TABLE IF NOT EXISTS product_info_icons (pid TEXT NOT NULL, field TEXT NOT NULL, custom_emoji_id TEXT NOT NULL, PRIMARY KEY(pid,field))')
    icon_cols={row[1] for row in conn.execute('PRAGMA table_info(product_info_icons)').fetchall()}
    if 'fallback_emoji' not in icon_cols:
        conn.execute('ALTER TABLE product_info_icons ADD COLUMN fallback_emoji TEXT NOT NULL DEFAULT "⭐"')
    discounts.prepare(conn)
    product_options.prepare(conn)
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
            conn.execute('INSERT INTO preferences(cid,lang,currency) VALUES (?,?,?)', (cid, language, 'USD'))
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
    def rbtn(text, icon_key):
        button = {'text': text, 'style': 'primary'}
        icon = ui_icon(icon_key)
        if icon:
            button['icon_custom_emoji_id'] = icon
        return button
    rows = [[rbtn(tr(cid, '🚀 ابدأ', '🚀 Start'), 'ui_start'),
             rbtn(tr(cid, '🛍 المنتجات', '🛍 Products'), 'ui_products'),
             rbtn('⚡ VEXA VOLT', 'ui_volt')],
            [rbtn(tr(cid, '👛 المحفظة', '👛 Wallet'), 'ui_account'),
             rbtn('🔗 API', 'ui_api'),
             rbtn(tr(cid, '🛡 الضمان', '🛡 Warranty'), 'ui_warranty')],
            [rbtn('🌐 اللغة / Language', 'ui_language'),
             rbtn('💱 العملة / Currency', 'ui_currency')],
            [rbtn(tr(cid, '💎 الإحالات', '💎 Referrals'), 'ui_referrals')]]
    if cid == G.get('ADMIN_ID'):
        rows.append([rbtn('🧾 لوحة الطلبات', 'ui_admin')])
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
    with db() as conn:
        override = conn.execute('SELECT value,currency FROM product_prices WHERE pid=?', (pid,)).fetchone()
    if override:
        value = Decimal(override[0])
        if currency != override[1]:
            value = value * RATE if currency == 'SAR' else value / RATE
        return value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    cp = custom_product(pid)
    if cp and cp[3] is not None:
        usd = Decimal(str(cp[3]))
        value = usd * RATE if currency == 'SAR' else usd
        return value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
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
    currency = currency or 'USD'
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


def auto_translate(text, target='en'):
    text = (text or '').strip()
    if not text:
        return text
    try:
        import chatgpt_extension
        language = 'English' if target == 'en' else 'Arabic'
        translated = chatgpt_extension.translate_text(text, target)
        return (translated or text).strip()
    except Exception as exc:
        print('Auto translation error:', type(exc).__name__, flush=True)
        return text


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


def total_activity_count(conn):
    """Lifetime activity count, including events pruned from the detail log.

    Initialize from SQLite's AUTOINCREMENT sequence, which preserves the
    highest allocated activity ID even after old rows are deleted.
    """
    conn.execute('CREATE TABLE IF NOT EXISTS activity_totals (id INTEGER PRIMARY KEY CHECK(id=1), total INTEGER NOT NULL)')
    conn.execute('INSERT OR IGNORE INTO activity_totals(id,total) VALUES (1, MAX(COALESCE((SELECT seq FROM sqlite_sequence WHERE name="activity"), 0), (SELECT COUNT(*) FROM activity)))')
    return conn.execute('SELECT total FROM activity_totals WHERE id=1').fetchone()[0]


def log_activity(cid, action_name, pid):
    if cid == G.get('ADMIN_ID'):
        return
    with db() as conn:
        total_activity_count(conn)
        conn.execute('INSERT INTO activity(cid,action,pid,created_at) VALUES (?,?,?,?)',
                     (cid, action_name, pid, now_saudi()))
        conn.execute('UPDATE activity_totals SET total=total+1 WHERE id=1')
        conn.execute('DELETE FROM activity WHERE id NOT IN (SELECT id FROM activity ORDER BY id DESC LIMIT 500)')


def add_order(cid, pid, method, status, usd=None, sar=None, quantity=None):
    if quantity is None:
        quantity = product_options.snapshot(sys.modules[__name__], "receipt", cid) if status == "review" else product_options.selected(sys.modules[__name__], cid, pid)
    order_id = uuid.uuid4().hex[:10].upper()
    with db() as conn:
        conn.execute('INSERT INTO orders VALUES (?,?,?,?,?,?,?,?)',
                     (order_id, cid, pid, method, str(amount(pid, 'USD') if usd is None else usd), str(amount(pid, 'SAR') if sar is None else sar), status, now_saudi()))
        conn.execute("INSERT OR REPLACE INTO quantity_snapshots VALUES (?,?,?)", ("order", order_id, quantity))
    return order_id


def customer_link(cid):
    return f'<a href="tg://user?id={cid}">{cid}</a>'


UI_ICON_LABELS = {
    'ui_category_description': 'تعديل وصف القسم',
    'ui_start': '🚀 ابدأ / START', 'ui_products': '🛒 المنتجات', 'ui_topup': '💰 شحن الرصيد',
    'ui_referrals': '💎 الإحالات', 'ui_account': '📦 طلباتي', 'ui_settings': 'الإعدادات', 'ui_coupon': 'كود الخصم', 'ui_support': '⚡ VEXA VOLT',
    'ui_report': '⚠️ إبلاغ عن مشكلة', 'ui_currency': '💱 العملة', 'ui_language': '🌐 اللغة',
    'ui_community': '📢 مجتمع VEXA STORE',
    'ui_admin': '🧾 لوحة الطلبات', 'ui_back': '↩️ رجوع', 'ui_home': '🏠 الرئيسية',
    'ui_broadcast_product': 'زر الذهاب للمنتج في الإعلان',
    'ui_broadcast_buy': 'زر شراء مباشرة في الإعلان',
    'ui_chatgpt': 'التحدث مع ChatGPT', 'ui_chatgpt_end': 'إنهاء المحادثة', 'ui_chatgpt_home': 'الرجوع للصفحة الرئيسية',
    'pay_wallet': 'المحفظة', 'pay_cryptopay': 'Crypto Pay', 'pay_bybit': 'USDT — Bybit',
    'pay_bybitid': 'Bybit Pay', 'pay_trc20': 'USDT • TRON (TRC20)', 'pay_bep20': 'USDT • BSC (BEP20)'
}

def ui_icon(key):
    with db() as conn:
        row = conn.execute('SELECT custom_emoji_id FROM category_icons WHERE pid=?', (key,)).fetchone()
    return row[0] if row else None

def ui_label(key, default):
    """Optional complete button caption; independent from Telegram custom icon."""
    with db() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS ui_button_labels (key TEXT PRIMARY KEY, label TEXT NOT NULL)')
        row = conn.execute('SELECT label FROM ui_button_labels WHERE key=?', (key,)).fetchone()
    return row[0] if row else default


def admin_button_labels(api, cid):
    if cid != G['ADMIN_ID']:
        return home(api, cid)
    labels = dict(UI_ICON_LABELS)
    labels.update({'ui_quantity': 'أزرار الكمية', 'ui_quantity_custom': 'كمية مخصصة',
                   'ui_stock_alert': 'تنبيه التوفر', 'ui_delivery_note': 'ملاحظات التسليم'})
    labels.update({f'ui_quantity_{n}': f'الكمية {n}' for n in (1, 2, 3, 5, 10)})
    labels.pop('ui_quantity', None)
    rows = [[btn(label, 'buttonlabel:' + key)] for key, label in labels.items()]
    send(api, cid, '✏️ <b>إدارة أزرار المتجر</b>\n\nاختر زرًا لتغيير نصه بالكامل، أو اختر قسمًا أو منتجًا لتغيير اسمه.',
         kb([[btn('📁 أسماء الأقسام', 'buttonnames:categories'), btn('📦 أسماء المنتجات', 'admin:editname')]] +
            rows + [[btn('↩️ لوحة الإدارة', 'admin')]]))


def begin_button_label(api, cid, key):
    if cid != G['ADMIN_ID']:
        return home(api, cid)
    if key not in UI_ICON_LABELS and key not in ('ui_quantity','ui_quantity_custom','ui_stock_alert','ui_delivery_note') and key not in (f'ui_quantity_{n}' for n in (1,2,3,5,10)):
        return admin_button_labels(api, cid)
    with db() as conn:
        conn.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)', (cid, 'button_label', key))
    send(api, cid, '✏️ أرسل نص الزر الجديد كاملًا؛ سيحل محل النص القديم.\nمثال: 🛍 ×1\n\nيمكنك أيضًا إزالة الأيقونة المتحركة المستقلة عن النص.',
         kb([[btn('🗑 مسح الاسم والأيقونة', 'resetbuttonlabel:' + key)],
             [btn('🗑 إزالة الأيقونة فقط', 'removebuttonicon:' + key)],
             [btn('❌ إلغاء', 'cancelbuttonlabel')]]))


def handle_button_label(api, message):
    cid = message.get('chat', {}).get('id')
    if cid != G.get('ADMIN_ID'):
        return False
    with db() as conn:
        row = conn.execute("SELECT value FROM admin_state WHERE cid=? AND action='button_label'", (cid,)).fetchone()
    if not row:
        return False
    value = (message.get('text') or '').strip()
    if value not in ('/reset',) and (not value or len(value) > 64):
        send(api, cid, 'أرسل اسمًا من ١ إلى ٦٤ حرفًا، أو /reset لاستعادة الاسم الافتراضي.')
        return True
    with db() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS ui_button_labels (key TEXT PRIMARY KEY, label TEXT NOT NULL)')
        if value == '/reset':
            conn.execute('DELETE FROM ui_button_labels WHERE key=?', (row[0],))
        else:
            conn.execute('INSERT OR REPLACE INTO ui_button_labels VALUES (?,?)', (row[0], value))
        conn.execute('DELETE FROM category_icons WHERE pid=?', (row[0],))
        if row[0].startswith('ui_quantity_'):
            conn.execute('INSERT INTO category_icons VALUES (?,?)', (row[0], ''))
        conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
    send(api, cid, '✅ تم تحديث اسم الزر دون تغيير وظيفته.',
         kb([[btn('✏️ تعديل زر آخر', 'admin:buttonlabels')], [btn('↩️ لوحة الإدارة', 'admin')]]))
    return True


def button_names_categories(api, cid):
    if cid != G['ADMIN_ID']:
        return home(api, cid)
    with db() as conn:
        custom = conn.execute('SELECT cid,name FROM admin_categories ORDER BY rowid').fetchall()
    categories = [(pid, p['name']) for pid, p in G['PRODUCTS'].items()] + custom
    rows = [[btn(name(pid, cid), 'txtpick:name:' + pid)] for pid, _ in categories]
    send(api, cid, '📁 اختر القسم لتغيير الاسم الظاهر على زره:',
         kb(rows + [[btn('↩️ إدارة الأزرار', 'admin:buttonlabels')]]))


def reset_button_label(api, cid, key):
    if cid != G['ADMIN_ID'] or key not in UI_ICON_LABELS:
        return
    with db() as conn:
        conn.execute('DELETE FROM ui_button_labels WHERE key=?', (key,))
        conn.execute('DELETE FROM category_icons WHERE pid=?', (key,))
        if key.startswith('ui_quantity_'):
            conn.execute('INSERT INTO category_icons VALUES (?,?)', (key, ''))
        conn.execute("DELETE FROM admin_state WHERE cid=? AND action='button_label'", (cid,))
    send(api, cid, '✅ تم مسح الاسم والأيقونة المخصصين. يمكنك كتابة اسم جديد من الصفر.',
         kb([[btn('✏️ كتابة اسم جديد', 'buttonlabel:' + key)], [btn('↩️ إدارة الأزرار', 'admin:buttonlabels')]]))


def remove_button_icon(api, cid, key):
    if cid != G['ADMIN_ID'] or key not in UI_ICON_LABELS:
        return
    with db() as conn:
        conn.execute('DELETE FROM category_icons WHERE pid=?', (key,))
        if key.startswith('ui_quantity_'):
            conn.execute('INSERT INTO category_icons VALUES (?,?)', (key, ''))
    send(api, cid, '✅ أزيلت الأيقونة المتحركة من هذا الزر.',
         kb([[btn('✏️ تغيير النص', 'buttonlabel:' + key)], [btn('↩️ إدارة الأزرار', 'admin:buttonlabels')]]))


def remove_name_icon(api, cid, pid):
    if cid != G['ADMIN_ID'] or not (pid in G['PRODUCTS'] or pid in VARIANTS or custom_category(pid) or custom_product(pid)):
        return
    with db() as conn:
        conn.execute('INSERT OR REPLACE INTO category_icons VALUES (?,?)', (pid, ''))
    if pid in G['PRODUCTS']:
        G['PRODUCTS'][pid]['custom_emoji_id'] = ''
    send(api, cid, '✅ أزيلت الأيقونة المتحركة من الزر.',
         kb([[btn('✏️ كتابة الاسم الجديد', 'txtpick:name:' + pid)], [btn('↩️ إدارة الأزرار', 'admin:buttonlabels')]]))


def apply_icon_overrides():
    with db() as conn:
        rows = conn.execute('SELECT pid,custom_emoji_id FROM category_icons').fetchall()
    for pid, emoji_id in rows:
        if pid in G.get('PRODUCTS', {}):
            G['PRODUCTS'][pid]['custom_emoji_id'] = emoji_id
    if rows:
        G['CONFIG']['custom_icons_enabled'] = True



def in_stock(pid):
    pid = LEGACY.get(pid, pid)
    with db() as conn:
        row = conn.execute('SELECT available FROM product_availability WHERE pid=?', (pid,)).fetchone()
    if row is not None:
        return bool(row[0])
    if pid in VARIANTS:
        return VARIANTS[pid].get('source_stock', 0) > 0
    cp = custom_product(pid)
    if cp:
        return bool(cp[4]) and int(cp[6] or 0) > 0
    return pid in G['PRODUCTS']


def admin_stock(api, cid, category_id=None):
    if cid != G['ADMIN_ID']:
        return
    with db() as conn:
        conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
    if category_id is None:
        rows = [[btn(p['name'], 'stockcat:' + pid)] for pid, p in G['PRODUCTS'].items()]
    else:
        ids = admin_category_product_ids(category_id)
        rows = [[btn(('✅ ' if in_stock(pid) else '🔴 ') + name(pid, cid), 'stockpick:' + pid,
                     style=None if in_stock(pid) else 'danger')] for pid in ids]
    send(api, cid, '📦 <b>تعديل توفر المنتج</b>\nاختر القسم ثم المنتج:', kb(rows + [[btn('↩️ لوحة الإدارة', 'admin')]]))


def stock_editor(api, cid, pid, value=None):
    if cid != G['ADMIN_ID'] or (pid not in VARIANTS and pid not in G['PRODUCTS'] and not custom_product(pid)):
        return
    pid = LEGACY.get(pid, pid)
    saved = value in ('0', '1')
    if saved:
        with db() as conn:
            conn.execute('INSERT OR REPLACE INTO product_availability VALUES (?,?)', (pid, int(value)))
            if custom_product(pid):
                conn.execute('UPDATE admin_products SET available=? WHERE pid=?', (int(value), pid))
    status = '✅ متوفر' if in_stock(pid) else '🔴 غير متوفر'
    qty = product_stock(pid)
    text = ('✅ تم حفظ الحالة\n\n' if saved else '') + '<b>' + esc(name(pid, cid)) + '</b>\n\n' + status + '\n📦 الكمية الحالية: <b>' + esc(qty) + '</b>'
    if VARIANTS.get(pid, {}).get('review_required'):
        text += '\n⚠️ المنتج قيد المراجعة؛ تغيير التوفر لا يلغي إيقاف الطلب للمراجعة.'
    send(api, cid, text, kb([[btn('✅ متوفر', 'stockset:1:' + pid, style='success'),
                              btn('🔴 غير متوفر', 'stockset:0:' + pid, style='danger')],
                             [btn('📦 تعديل الكمية', 'stockqty:' + pid, style='primary')],
                             [btn('↩️ منتج آخر', 'admin:stock')], [btn('لوحة الإدارة', 'admin')]]))


def text_override(pid, field, lang, default):
    with db() as conn:
        row = conn.execute('SELECT value FROM product_text WHERE pid=? AND field=? AND lang=?', (LEGACY.get(pid, pid), field, lang)).fetchone()
    return row[0] if row else default


def product_description(pid, cid=0):
    pid = LEGACY.get(pid, pid)
    lang = prefs(cid)[0]
    if pid in VARIANTS:
        default = VARIANTS[pid].get('description', {}).get(lang, '')
    else:
        cp = custom_product(pid)
        default = cp[2] if cp else G['PRODUCTS'].get(pid, {}).get('description', '')
    return text_override(pid, 'description', lang, default)


def description_message_html(message, plain):
    """Preserve custom emoji IDs using Telegram's UTF-16 entity offsets."""
    raw = message.get('text') or ''
    leading = len(raw) - len(raw.lstrip())
    shift = len(raw[:leading].encode('utf-16-le')) // 2
    data = plain.encode('utf-16-le')
    spans = []
    for entity in message.get('entities', []):
        emoji_id = str(entity.get('custom_emoji_id', ''))
        if entity.get('type') != 'custom_emoji' or not emoji_id.isdecimal():
            continue
        start = entity.get('offset', -1) - shift
        end = start + entity.get('length', 0)
        if 0 <= start < end <= len(data) // 2:
            spans.append((start * 2, end * 2, emoji_id))
    parts, cursor = [], 0
    for start, end, emoji_id in sorted(spans):
        if start < cursor:
            continue
        try:
            before = data[cursor:start].decode('utf-16-le')
            fallback = data[start:end].decode('utf-16-le')
        except UnicodeDecodeError:
            continue
        parts.extend((esc(before), '<tg-emoji emoji-id="' + emoji_id + '">' + esc(fallback) + '</tg-emoji>'))
        cursor = end
    parts.append(esc(data[cursor:].decode('utf-16-le')))
    return ''.join(parts)


def product_description_html(pid, cid=0):
    pid = LEGACY.get(pid, pid)
    plain = product_description(pid, cid)
    lang = prefs(cid)[0]
    with db() as conn:
        row = conn.execute('SELECT plain,html FROM description_emoji WHERE pid=? AND lang=?', (pid, lang)).fetchone()
        # Base custom descriptions apply to either language unless overridden.
        override = conn.execute("SELECT 1 FROM product_text WHERE pid=? AND field='description' AND lang=?", (pid, lang)).fetchone()
        if row is None and not override:
            row = conn.execute("SELECT plain,html FROM description_emoji WHERE pid=? AND lang='*'", (pid,)).fetchone()
    return row[1] if row and row[0] == plain else esc(plain)


def info_display(pid):
    with db() as conn:
        row = conn.execute('SELECT show_price,show_stock,show_warranty,warranty FROM product_info_display WHERE pid=?', (LEGACY.get(pid,pid),)).fetchone()
    return row or (1,1,0,'')

def product_stock(pid):
    pid = LEGACY.get(pid, pid)
    with db() as conn:
        row = conn.execute('SELECT stock FROM product_stock_overrides WHERE pid=?', (pid,)).fetchone()
    if row is not None:
        return int(row[0] or 0)
    cp=custom_product(pid)
    if cp: return int(cp[6] or 0)
    v=VARIANTS.get(pid,{})
    for key in ('stock','quantity','available_quantity','source_stock'):
        if key in v:
            try:return int(v[key])
            except:return v[key]
    return 1 if in_stock(pid) else 0

def info_icon(pid,field,fallback):
    with db() as conn:
        if field in ('price','stock'):
            row=conn.execute('SELECT custom_emoji_id,fallback_emoji FROM product_info_icons WHERE pid=? AND field=?',('__global__',field)).fetchone()
        else:
            row=conn.execute('SELECT custom_emoji_id,fallback_emoji FROM product_info_icons WHERE pid=? AND field=?',(LEGACY.get(pid,pid),field)).fetchone()
    if not row: return fallback
    if not row[0]: return esc(row[1] or fallback)
    return '<tg-emoji emoji-id="'+esc(row[0])+'">'+esc(row[1] or fallback)+'</tg-emoji>'

def info_block(pid,cid):
    sp,ss,sw,w=info_display(pid); lines=[]
    if sp: lines.append(info_icon(pid,'price','💵')+' <b>'+tr(cid,'السعر','Price')+':</b> '+price(cid,pid))
    if ss: lines.append(info_icon(pid,'stock','📦')+' <b>'+tr(cid,'الكمية','Quantity')+':</b> '+esc(product_stock(pid)))
    if sw and w: lines.append(info_icon(pid,'warranty','🛡')+' <b>'+tr(cid,'الضمان','Warranty')+':</b> '+esc(w))
    return '\n'.join(lines)

def admin_info_menu(api,cid,category_id=None):
    if cid!=G['ADMIN_ID']: return home(api,cid)
    with db() as conn:
        custom_cats=conn.execute('SELECT cid,name FROM admin_categories').fetchall()
        custom_ids=[r[0] for r in conn.execute('SELECT pid FROM admin_products WHERE category_id=?',(category_id,)).fetchall()] if category_id else []
    if category_id is None:
        cats=[(pid,p['name']) for pid,p in G['PRODUCTS'].items()]+custom_cats
        rows=[
            [btn('💵 أيقونة السعر — لجميع المنتجات','infoicon:price:__global__',style='success')],
            [btn('📦 أيقونة الكمية — لجميع المنتجات','infoicon:stock:__global__',style='success')],
        ] + [[btn(label,'infocat:'+pid)] for pid,label in cats]
    else:
        ids=admin_category_product_ids(category_id)
        rows=[[btn(name(pid,cid),'infopick:'+pid)] for pid in ids]
    send(api,cid,'🎛 <b>بيانات المنتج الظاهرة</b>\nاختر القسم ثم المنتج:',kb(rows+[[btn('↩️ لوحة الإدارة','admin')]]))

def admin_info_editor(api,cid,pid):
    if cid!=G['ADMIN_ID']: return
    sp,ss,sw,w=info_display(pid)
    rows=[[btn(('✅ ' if sp else '❌ ')+'السعر','infotoggle:price:'+pid),btn(('✅ ' if ss else '❌ ')+'الكمية','infotoggle:stock:'+pid)],
          [btn(('✅ ' if sw else '❌ ')+'الضمان','infotoggle:warranty:'+pid),btn('✏️ نص الضمان','infowarranty:'+pid)],
          [btn('🛡 أيقونة الضمان','infoicon:warranty:'+pid)],
          [btn('↩️ منتج آخر','admin:info')],[btn('↩️ لوحة الإدارة','admin')]]
    send(api,cid,'🎛 <b>'+esc(name(pid,cid))+'</b>\n\nحدد المعلومات التي تريد ظهورها للعميل.\nالضمان الحالي: <b>'+esc(w or 'غير محدد')+'</b>',kb(rows))

def admin_category_product_ids(category_id):
    """Use one complete product list for text and price administration."""
    with db() as conn:
        custom_ids = [r[0] for r in conn.execute('SELECT pid FROM admin_products WHERE category_id=?', (category_id,)).fetchall()] if category_id else []
        if category_id == 'youtube':
            # Older YouTube items may belong to custom categories whose names
            # were accidentally entered as product names. Search both names.
            youtube_ids = conn.execute('''SELECT DISTINCT p.pid FROM admin_products p
                LEFT JOIN admin_categories c ON c.cid=p.category_id
                WHERE lower(p.name) LIKE '%youtube%'
                   OR p.name LIKE '%يوتيوب%'
                   OR lower(COALESCE(c.name,'')) LIKE '%youtube%'
                   OR COALESCE(c.name,'') LIKE '%يوتيوب%' ''').fetchall()
            custom_ids += [row[0] for row in youtube_ids if row[0] not in custom_ids]
    return list(dict.fromkeys(category_product_ids(category_id) + custom_ids))


def admin_text_menu(api, cid, field, category_id=None):
    if cid != G['ADMIN_ID'] or field not in ('name', 'description'):
        return
    with db() as conn:
        conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
        custom_cats = conn.execute('SELECT cid,name FROM admin_categories').fetchall()
    if category_id is None:
        cats = [(pid, ('YouTube' if pid == 'youtube' else name(pid, cid))) for pid in G['PRODUCTS']]
        cats += [(pid, label) for pid, label in custom_cats
                 if not ('youtube' in label.lower() or 'يوتيوب' in label)]
        rows = [[btn(label, f'txtcat:{field}:{pid}')] for pid, label in cats]
    else:
        ids = admin_category_product_ids(category_id)
        rows = [[btn(name(pid, cid), f'txtpick:{field}:{pid}')] for pid in dict.fromkeys(ids)]
    send(api, cid, '✏️ اختر القسم ثم المنتج لتعديل ' + ('الاسم' if field == 'name' else 'الوصف'), kb(rows + [[btn('↩️ لوحة الإدارة', 'admin')]]))


def category_description_html(pid, cid):
    # Category copy is independent of product descriptions, even when IDs match.
    with db() as conn:
        row = conn.execute("SELECT value FROM product_text WHERE pid=? AND field='category_description' AND lang=?",
                           (pid, prefs(cid)[0])).fetchone()
    return row[0] if row else None


def category_label(pid, cid=0):
    """Category labels must not be replaced by a product-name override."""
    if pid == 'youtube':
        return 'YouTube'
    custom = custom_category(pid)
    if custom:
        return custom[1]
    product = G['PRODUCTS'].get(pid)
    if product:
        return product.get('name', pid)
    return name(pid, cid)


def category_heading(pid, cid):
    description = category_description_html(pid, cid)
    return '<b>' + esc(category_label(pid, cid)) + '</b>\n\n' + (description if description is not None else tr(cid, 'اختر المنتج:', 'Choose a product:'))


def admin_category_description(api, cid, pid=None, lang=None):
    if cid != G['ADMIN_ID']:
        return
    with db() as conn:
        conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
        cats = list(G['PRODUCTS']) + [r[0] for r in conn.execute('SELECT cid FROM admin_categories ORDER BY rowid')]
    if pid is None:
        rows = [[btn(category_label(key, cid), 'catdesc:' + key)] for key in dict.fromkeys(cats)]
        return send(api, cid, '✏️ اختر القسم لتعديل الوصف الذي يظهر فوق المنتجات:', kb(rows + [[btn('↩️ لوحة الإدارة', 'admin')]]))
    if pid not in cats:
        return admin_category_description(api, cid)
    if lang not in ('ar', 'en'):
        return send(api, cid, 'اختر لغة وصف القسم:\n\n🇺🇸 إذا كتبت الوصف بالإنجليزية سيتم إنشاء النسخة العربية تلقائيًا.', kb([[btn('العربية', 'catdesclang:ar:' + pid), btn('English + ترجمة عربية تلقائية', 'catdesclang:en:' + pid)], [btn('إلغاء', 'admin:categorydesc')]]))
    BROADCAST_PENDING.discard(cid)
    with db() as conn:
        conn.execute('DELETE FROM custom_topup_state WHERE cid=?', (cid,))
        conn.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)', (cid, 'category_description', json.dumps([pid, lang])))
        row = conn.execute("SELECT value FROM product_text WHERE pid=? AND field='category_description' AND lang=?", (pid, lang)).fetchone()
    current = row[0] if row else esc('اختر المنتج:' if lang == 'ar' else 'Choose a product:')
    send(api, cid, '<b>' + esc(category_label(pid, cid)) + '</b>\n\nالوصف الحالي:\n' + current + '\n\nأرسل الوصف الجديد (حتى 1500 حرف). يمكنك استخدام أسطر وأيقونات متحركة.', kb([[btn('إلغاء', 'admin:categorydesc')]]))


def handle_category_description(api, message):
    cid = message.get('chat', {}).get('id')
    if cid != G.get('ADMIN_ID'):
        return False
    with db() as conn:
        row = conn.execute("SELECT value FROM admin_state WHERE cid=? AND action='category_description'", (cid,)).fetchone()
    if not row:
        return False
    text = (message.get('text') or '').strip()
    if text.startswith('/') or text in G.get('MENU', {}):
        with db() as conn:
            conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
        return False
    if not text or len(text.encode('utf-16-le')) // 2 > 1500:
        send(api, cid, 'أرسل نصًا غير فارغ لا يتجاوز 1500 حرف.', kb([[btn('إلغاء', 'admin:categorydesc')]]))
        return True
    pid, lang = json.loads(row[0])
    formatted = description_message_html(message, text)
    translated_en = auto_translate(text, 'en')[:1500] if lang == 'ar' else None
    translated_ar = auto_translate(text, 'ar')[:1500] if lang == 'en' else None
    with db() as conn:
        conn.execute('INSERT OR REPLACE INTO product_text VALUES (?,?,?,?)', (pid, 'category_description', lang, formatted))
        if translated_en:
            conn.execute('INSERT OR REPLACE INTO product_text VALUES (?,?,?,?)', (pid, 'category_description', 'en', esc(translated_en)))
        if translated_ar:
            conn.execute('INSERT OR REPLACE INTO product_text VALUES (?,?,?,?)', (pid, 'category_description', 'ar', esc(translated_ar)))
        conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
    msg = '✅ تم حفظ وصف القسم.'
    if translated_en:
        msg += '\n🌐 وتم تحديث الإنجليزية تلقائيًا.'
    if translated_ar:
        msg += '\n🇸🇦 وتم إنشاء النسخة العربية تلقائيًا.'
    send(api, cid, msg + '\n\n' + formatted, kb([[btn('معاينة القسم', 'product:' + pid)], [btn('تعديل قسم آخر', 'admin:categorydesc')], [btn('لوحة الإدارة', 'admin')]]))
    return True


def admin_text_editor(api, cid, field, pid, lang=None):
    if cid != G['ADMIN_ID'] or field not in ('name', 'description'):
        return
    if pid not in VARIANTS and pid not in G['PRODUCTS'] and not custom_product(pid) and not custom_category(pid):
        return
    if lang not in ('ar', 'en'):
        return send(api, cid, 'اختر لغة النص الذي تريد تعديله:\n\n🇸🇦 عند تعديل العربية سيتم تحديث الإنجليزية تلقائيًا.', kb([[btn('🇸🇦 العربية + تحديث English', f'txtedit:{field}:ar:{pid}'), btn('🇺🇸 English يدوي', f'txtedit:{field}:en:{pid}')], [btn('إلغاء', 'admin')]]))
    BROADCAST_PENDING.discard(cid)
    with db() as conn:
        conn.execute('DELETE FROM custom_topup_state WHERE cid=?', (cid,))
        conn.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)', (cid, 'product_text', json.dumps([pid, field, lang])))
    label = 'الاسم الجديد (حتى 120 حرفًا)' if field == 'name' else 'الوصف الجديد (حتى 1500 حرف، ويمكن استخدام عدة أسطر)'
    send(api, cid, 'أرسل ' + label + (' بالعربية.' if lang == 'ar' else ' بالإنجليزية.') + '\nسيحل النص الجديد محل الاسم القديم بالكامل.',
         kb(([[btn('🗑 إزالة الأيقونة المتحركة', 'removenameicon:' + pid)]] if field == 'name' else []) +
            [[btn('إلغاء', 'admin')]]))


def handle_info_icon(api,message):
    cid=message.get('chat',{}).get('id')
    if cid!=G.get('ADMIN_ID'): return False
    with db() as conn: row=conn.execute("SELECT value FROM admin_state WHERE cid=? AND action='info_icon'",(cid,)).fetchone()
    if not row: return False
    entities=list(message.get('entities',[]))+list(message.get('caption_entities',[]))
    emoji=next((e.get('custom_emoji_id') for e in entities if e.get('type')=='custom_emoji' and e.get('custom_emoji_id')),None)
    field,_,pid=row[0].partition(':')
    if not emoji:
        normal=(message.get('text') or '').strip()
        is_emoji=(0<len(normal)<=12 and not any(ch.isspace() or ch.isalpha() for ch in normal)
                  and any(unicodedata.category(ch)=='So' for ch in normal))
        if is_emoji:
            with db() as conn:
                conn.execute('INSERT OR REPLACE INTO product_info_icons(pid,field,custom_emoji_id,fallback_emoji) VALUES (?,?,?,?)',(pid,field,'',normal))
                conn.execute('DELETE FROM admin_state WHERE cid=?',(cid,))
            back_action = 'admin:info' if pid == '__global__' else 'infopick:'+pid
            back_label = '↩️ إعدادات البيانات' if pid == '__global__' else '↩️ إعدادات المنتج'
            send(api,cid,'✅ تم حفظ '+esc(normal)+' كأيقونة عادية. إذا أردتها متحركة، اختر إيموجي تيليجرام المخصص وأرسله.',kb([[btn(back_label,back_action)]]))
            return True
        send(api,cid,'لم أجد أيقونة. أرسل إيموجي واحدًا مثل ➕، أو اختر أيقونة متحركة مخصصة من إيموجي تيليجرام.',kb([[btn('❌ إلغاء','admin:info')]])); return True
    fallback='⭐'
    try:
        stickers=api.call('getCustomEmojiStickers',custom_emoji_ids=[str(emoji)]) or []
        if stickers and stickers[0].get('emoji'):
            fallback=stickers[0]['emoji']
    except Exception:
        pass
    with db() as conn:
        conn.execute('INSERT OR REPLACE INTO product_info_icons(pid,field,custom_emoji_id,fallback_emoji) VALUES (?,?,?,?)',(pid,field,str(emoji),fallback))
        conn.execute('DELETE FROM admin_state WHERE cid=?',(cid,))
    if pid == '__global__':
        admin_info_menu(api,cid)
    else:
        admin_info_editor(api,cid,pid)
    return True

def handle_info_warranty(api,message):
    cid=message.get('chat',{}).get('id')
    if cid!=G.get('ADMIN_ID'): return False
    with db() as conn: row=conn.execute("SELECT value FROM admin_state WHERE cid=? AND action='info_warranty'",(cid,)).fetchone()
    if not row:return False
    value=(message.get('text') or '').strip()
    if not value:return True
    pid=row[0]; sp,ss,sw,_=info_display(pid)
    with db() as conn:
        conn.execute('INSERT OR REPLACE INTO product_info_display VALUES (?,?,?,?,?)',(pid,sp,ss,1,value[:120]))
        conn.execute('DELETE FROM admin_state WHERE cid=?',(cid,))
    admin_info_editor(api,cid,pid); return True

def handle_admin_text(api, message):
    if handle_category_description(api, message):
        return True
    cid = message.get('chat', {}).get('id')
    if cid != G.get('ADMIN_ID'):
        return False
    with db() as conn:
        supplier_state = conn.execute("SELECT action,value FROM admin_state WHERE cid=? AND action IN ('supplier_endpoint','supplier_key','supplier_service','supplier_variant','supplier_margin')", (cid,)).fetchone()
    if supplier_state:
        action_name, pid = supplier_state
        raw = (message.get('text') or '').strip()
        if raw.startswith('/'):
            with db() as conn:
                conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
            return False
        if not raw:
            send(api, cid, 'أرسل قيمة صحيحة.')
            return True
        endpoint, api_key, service_id, enabled, provider, variant_id = supplier_api_row(pid)
        if action_name == 'supplier_margin':
            try:
                margin = Decimal(raw.replace('
            if not (raw.startswith('https://') or raw.startswith('http://')):
                send(api, cid, 'أرسل رابط API يبدأ بـ <code>https://</code> أو <code>http://</code>.')
                return True
            endpoint = ''.join(ch for ch in raw[:500].strip() if ord(ch) < 128)
        elif action_name == 'supplier_key':
            api_key = ''.join(ch for ch in raw[:500].strip() if ord(ch) < 128)
        elif action_name == 'supplier_service':
            service_id = raw[:200]
        else:
            variant_id = raw[:200]
        with db() as conn:
            conn.execute('INSERT INTO supplier_api(pid,endpoint,api_key,service_id,enabled,provider,variant_id) VALUES (?,?,?,?,?,?,?) ON CONFLICT(pid) DO UPDATE SET endpoint=excluded.endpoint,api_key=excluded.api_key,service_id=excluded.service_id,enabled=excluded.enabled,provider=excluded.provider,variant_id=excluded.variant_id',
                         (LEGACY.get(pid,pid), endpoint, api_key, service_id, enabled, provider, variant_id))
            conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
        send(api, cid, '✅ تم حفظ إعداد API.')
        supplier_api_editor(api, cid, pid)
        return True
    with db() as conn:
        qty_row = conn.execute("SELECT value FROM admin_state WHERE cid=? AND action='stock_quantity'", (cid,)).fetchone()
    if qty_row:
        raw_qty = (message.get('text') or '').strip().translate(str.maketrans('٠١٢٣٤٥٦٧٨٩', '0123456789'))
        if raw_qty.startswith('/'):
            with db() as conn:
                conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
            return False
        try:
            qty = int(raw_qty)
            if qty < 0 or qty > 1000000:
                raise ValueError()
        except Exception:
            send(api, cid, 'أرسل كمية صحيحة كرقم، مثال: <code>4</code>.')
            return True
        pid = LEGACY.get(qty_row[0], qty_row[0])
        with db() as conn:
            conn.execute('INSERT OR REPLACE INTO product_stock_overrides VALUES (?,?)', (pid, qty))
            if custom_product(pid):
                conn.execute('UPDATE admin_products SET stock=? WHERE pid=?', (qty, pid))
            conn.execute('INSERT OR REPLACE INTO product_availability VALUES (?,?)', (pid, 1 if qty > 0 else 0))
            if custom_product(pid):
                conn.execute('UPDATE admin_products SET available=? WHERE pid=?', (1 if qty > 0 else 0, pid))
            conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
        send(api, cid, '✅ تم تحديث الكمية إلى <b>' + esc(qty) + '</b>.')
        stock_editor(api, cid, pid)
        return True
    with db() as conn:
        row = conn.execute("SELECT value FROM admin_state WHERE cid=? AND action='product_text'", (cid,)).fetchone()
    if not row:
        return False
    text = (message.get('text') or '').strip()
    if text.startswith('/') or text in G.get('MENU', {}):
        with db() as conn:
            conn.execute("DELETE FROM admin_state WHERE cid=? AND action='product_text'", (cid,))
        return False
    pid, field, lang = json.loads(row[0])
    limit = 120 if field == 'name' else 1500
    if not text or len(text) > limit or (field == 'name' and '\n' in text):
        send(api, cid, f'أرسل نصًا غير فارغ لا يتجاوز {limit} حرفًا.' + (' الاسم يكون في سطر واحد.' if field == 'name' else ''), kb([[btn('إلغاء', 'admin')]]))
        return True
    translated_en = auto_translate(text, 'en')[:limit] if lang == 'ar' else None
    with db() as conn:
        conn.execute('INSERT OR REPLACE INTO product_text VALUES (?,?,?,?)', (pid, field, lang, text))
        if translated_en:
            conn.execute('INSERT OR REPLACE INTO product_text VALUES (?,?,?,?)', (pid, field, 'en', translated_en))
        if field == 'description':
            conn.execute('INSERT OR REPLACE INTO description_emoji VALUES (?,?,?,?)',
                         (pid, lang, text, description_message_html(message, text)))
            if translated_en:
                conn.execute('DELETE FROM description_emoji WHERE pid=? AND lang=?', (pid, 'en'))
        conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
    confirmation = '✅ تم حفظ ' + ('اسم المنتج' if field == 'name' else 'وصف المنتج')
    if translated_en:
        confirmation += '\n🌐 وتم تحديث النسخة الإنجليزية تلقائيًا.'
    confirmation += '\n\n' + (description_message_html(message, text) if field == 'description' else esc(text))
    send(api, cid, confirmation, kb([[btn('تعديل منتج آخر', 'admin:editname' if field == 'name' else 'admin:editdesc')], [btn('لوحة الإدارة', 'admin')]]))
    return True


def saved_product_photo(pid):
    with db() as conn:
        row = conn.execute('SELECT file_id FROM product_photos WHERE pid=?', (LEGACY.get(pid, pid),)).fetchone()
    return row[0] if row else None


def admin_photo_menu(api, cid, category_id=None):
    if cid != G['ADMIN_ID']:
        return
    with db() as conn:
        conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
        cats = [(pid, p['name']) for pid, p in G['PRODUCTS'].items()] + conn.execute('SELECT cid,name FROM admin_categories').fetchall()
        custom_ids = [r[0] for r in conn.execute('SELECT pid FROM admin_products WHERE category_id=?', (category_id,)).fetchall()] if category_id else []
    if category_id is None:
        rows = [[btn(label, 'photocat:' + pid)] for pid, label in cats]
    else:
        ids = admin_category_product_ids(category_id)
        rows = [[btn(name(pid, cid), 'photopick:' + pid)] for pid in ids]
    send(api, cid, '🖼️ صورة المنتج\nاختر القسم ثم المنتج:', kb(rows + [[btn('↩️ لوحة الإدارة', 'admin')]]))


def admin_photo_editor(api, cid, pid, delete=False):
    if cid != G['ADMIN_ID'] or (pid not in VARIANTS and pid not in G['PRODUCTS'] and not custom_product(pid)):
        return
    BROADCAST_PENDING.discard(cid)
    with db() as conn:
        conn.execute('DELETE FROM custom_topup_state WHERE cid=?', (cid,))
        if delete:
            conn.execute('INSERT OR REPLACE INTO product_photos VALUES (?,?)', (pid, ''))
            conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
        else:
            conn.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)', (cid, 'product_photo', pid))
    parent = 'myproduct:' + pid if custom_product(pid) else 'admin:photos'
    if delete:
        return send(api, cid, '✅ تم حذف صورة المنتج. سيظهر دون صورة عند فتحه مجددًا.', kb([[btn('🖼️ إضافة صورة', 'photopick:' + pid)], [btn('↩️ رجوع', parent)]]))
    send(api, cid, '<b>' + esc(name(pid, cid)) + '</b>\n\nأرسل الصورة هنا كصورة في تيليجرام لإضافتها أو استبدال الصورة الحالية.', kb([[btn('🗑 حذف الصورة', 'photodel:' + pid, style='danger')], [btn('↩️ رجوع', parent)]]))


def handle_admin_photo(api, message):
    cid = message.get('chat', {}).get('id')
    if cid != G.get('ADMIN_ID'):
        return False
    with db() as conn:
        row = conn.execute("SELECT value FROM admin_state WHERE cid=? AND action='product_photo'", (cid,)).fetchone()
    if not row:
        return False
    text = message.get('text', '')
    if text.startswith('/') or text in G.get('MENU', {}):
        with db() as conn:
            conn.execute("DELETE FROM admin_state WHERE cid=? AND action='product_photo'", (cid,))
        return False
    photos = message.get('photo') or []
    file_id = photos[-1].get('file_id') if photos else None
    if not file_id:
        send(api, cid, 'أرسل الصورة كصورة في تيليجرام، وليس كملف أو نص.', kb([[btn('إلغاء', 'admin')]]))
        return True
    with db() as conn:
        conn.execute('INSERT OR REPLACE INTO product_photos VALUES (?,?)', (row[0], file_id))
        conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
    parent = 'myproduct:' + row[0] if custom_product(row[0]) else 'admin:photos'
    send(api, cid, '✅ تم حفظ صورة المنتج.', kb([[btn('👁 معاينة المنتج', ('item:' if row[0] in VARIANTS or custom_product(row[0]) else 'product:') + row[0])], [btn('↩️ رجوع للمنتج', parent)], [btn('🖼️ منتج آخر', 'admin:photos')], [btn('لوحة الإدارة', 'admin')]]))
    return True


def admin_prices(api, cid, category_id=None):
    if cid != G['ADMIN_ID']:
        return
    with db() as conn:
        conn.execute("DELETE FROM admin_state WHERE cid=? AND action IN ('price', 'category_description')", (cid,))
    if category_id is None:
        with db() as conn:
            custom_categories = conn.execute('SELECT cid,name FROM admin_categories ORDER BY rowid').fetchall()
        rows = [[btn(p['name'], 'pricecat:' + pid)] for pid, p in G['PRODUCTS'].items()]
        rows += [[btn(label, 'pricecat:' + pid)] for pid, label in custom_categories]
    else:
        ids = admin_category_product_ids(category_id)
        rows = [[btn(name(pid, cid) + ' | ' + price(cid, pid, 'USD'), 'pricepick:' + pid)] for pid in ids]
    send(api, cid, '✏️ اختر القسم أو المنتج لتعديل سعر البيع:', kb(rows + [[btn('↩️ لوحة الإدارة', 'admin')]]))


def price_editor(api, cid, pid, currency=None):
    if cid != G['ADMIN_ID'] or (pid not in VARIANTS and pid not in G['PRODUCTS'] and not custom_product(pid)):
        return
    BROADCAST_PENDING.discard(cid)
    with db() as conn:
        conn.execute('DELETE FROM custom_topup_state WHERE cid=?', (cid,))
        conn.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)', (cid, 'price', json.dumps([pid, 'USD'])))
    send(api, cid,
         esc(name(pid, cid)) + '\nالسعر الحالي: <b>' + price(cid, pid, 'USD') +
         '</b>\n\nأرسل سعر البيع النهائي بالدولار USD.\nمثال: <code>6.35</code>',
         kb([[btn('إلغاء', 'admin:prices')]]))


def handle_admin_price(api, message):
    cid = message.get('chat', {}).get('id')
    if cid != G.get('ADMIN_ID'):
        return False
    with db() as conn:
        state = conn.execute("SELECT value FROM admin_state WHERE cid=? AND action='price'", (cid,)).fetchone()
    if not state:
        return False
    raw = (message.get('text') or '').strip()
    if raw.startswith('/') or raw in G.get('MENU', {}):
        with db() as conn:
            conn.execute("DELETE FROM admin_state WHERE cid=? AND action='price'", (cid,))
        return False
    raw = raw.translate(str.maketrans('٠١٢٣٤٥٦٧٨٩٫', '0123456789.')).replace(',', '.')
    try:
        value = Decimal(raw)
        if not value.is_finite() or value <= 0 or value > 1000000 or value != value.quantize(Decimal('0.01')):
            raise ValueError()
    except Exception:
        send(api, cid, 'أرسل سعرًا أكبر من صفر، برقم فقط وبحد أقصى منزلتين عشريتين. مثال: 19.50', kb([[btn('إلغاء', 'admin:prices')]]))
        return True
    pid, _currency = json.loads(state[0])
    usd = value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    sar = (usd * Decimal('3.75')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    with db() as conn:
        conn.execute('INSERT OR REPLACE INTO product_prices VALUES (?,?,?)', (pid, str(usd), 'USD'))
        conn.execute('UPDATE admin_products SET price_usd=?,price_sar=? WHERE pid=?', (str(usd), str(sar), pid))
        conn.execute("DELETE FROM admin_state WHERE cid=? AND action='price'", (cid,))
    send(api, cid, '✅ تم حفظ سعر ' + esc(name(pid, cid)) + '\n💵 <b>' + price(cid, pid, 'USD') + '</b>\n\nافتح قائمة المنتجات من جديد لرؤية السعر الجديد.', kb([[btn('تعديل منتج آخر', 'admin:prices')], [btn('لوحة الإدارة', 'admin')]]))
    return True



def admin_products_page(api, cid):
    if cid != G['ADMIN_ID']:
        return home(api, cid)
    with db() as conn:
        custom_categories = conn.execute('SELECT cid,name FROM admin_categories ORDER BY rowid DESC').fetchall()
    seen = set()
    categories = []
    for category_id in G['PRODUCTS']:
        if category_id not in seen:
            categories.append((category_id, category_label(category_id, cid)))
            seen.add(category_id)
    for category_id, category_name in custom_categories:
        if category_id not in seen:
            categories.append((category_id, category_name))
            seen.add(category_id)
    buttons = [[btn('📁 ' + esc(category_name), 'mycategory:' + category_id)] for category_id, category_name in categories]
    text = '📦 <b>منتجاتي</b>\n\nاختر القسم، ثم المنتج الذي تريد إدارته أو ربطه بالـ API:'
    send(api, cid, text, kb(buttons + [[btn('➕ إضافة قسم ومنتجات', 'admin:addproduct', style='success')], [btn('↩️ لوحة الإدارة', 'admin')]]))


def admin_category_detail(api, cid, category_id):
    if cid != G['ADMIN_ID']:
        return home(api, cid)
    ids = admin_category_product_ids(category_id)
    buttons = []
    for pid in ids:
        cp = custom_product(pid)
        if cp:
            _pid, product_name, _description, _price_usd, available, _cat, stock = cp
            qty = int(stock or 0)
            is_available = bool(available and qty > 0)
        else:
            product_name = name(pid, cid)
            qty = product_stock(pid)
            is_available = bool(in_stock(pid))
        label = ('✅ ' if is_available else '🔴 ') + compact_name(pid, cid) + ' • ' + price(cid, pid, 'USD') + ' • ' + compact_stock(qty)
        buttons.append([btn(label, 'myproduct:' + pid, style='success' if is_available else 'danger'),
                        btn('🔌 API', 'supplierpick:' + pid)])
    title = category_label(category_id, cid)
    extra = [[btn('➕ إضافة منتج لهذا القسم', 'addtocategory:' + category_id, style='success')]]
    send(api, cid, '📁 <b>' + esc(title) + '</b>\n\nكل المنتجات داخل القسم:', kb(buttons + extra + [[btn('↩️ منتجاتي', 'admin:myproducts')]]))


def begin_add_product(api, cid):
    if cid != G['ADMIN_ID']:
        return home(api, cid)
    BROADCAST_PENDING.discard(cid)
    with db() as conn:
        conn.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)', (cid, 'add_product_category', '{}'))
    send(api, cid, '➕ <b>إضافة قسم ومنتجات</b>\n\n1️⃣ أرسل <b>اسم القسم</b> الذي سيظهر للعملاء.\nمثال: <code>ChatGPT</code>', kb([[btn('❌ إلغاء', 'admin:cancelproduct')]]))


def add_to_category(api, cid, category_id=None):
    if cid != G['ADMIN_ID']:
        return home(api, cid)
    reset_navigation_state(cid)
    BROADCAST_PENDING.discard(cid)
    if category_id is None:
        with db() as conn:
            custom = conn.execute('SELECT cid,name FROM admin_categories ORDER BY rowid').fetchall()
        categories = list(G['PRODUCTS']) + [row[0] for row in custom]
        return send(api, cid, '📁 اختر القسم الذي تريد إضافة منتج جديد إليه:',
                    kb([[btn(('▶️ YouTube' if key == 'youtube' else name(key, cid)), 'addtocategory:' + key)] for key in categories] +
                       [[btn('↩️ لوحة التحكم', 'admin')]]))
    if category_id not in G['PRODUCTS'] and not custom_category(category_id):
        return add_to_category(api, cid)
    payload = {'category_id': category_id, 'category_name': ('YouTube' if category_id == 'youtube' else name(category_id, cid)),
               'count': 1, 'index': 0, 'products': []}
    with db() as conn:
        conn.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)',
                     (cid, 'add_product_name', json.dumps(payload, ensure_ascii=False)))
    send(api, cid, '➕ إضافة منتج داخل <b>' + esc(payload['category_name']) + '</b>\n\nأرسل اسم المنتج الجديد <b>بالعربي</b>:',
         kb([[btn('❌ إلغاء', 'admin:cancelproduct')]]))


def show_extended_category(api, cid, category_id):
    """Include owner-added products alongside a built-in category's products."""
    if category_id not in G['PRODUCTS']:
        return False
    with db() as conn:
        custom = [row[0] for row in conn.execute('SELECT pid FROM admin_products WHERE category_id=? ORDER BY rowid', (category_id,))]
    if not custom:
        return False
    variants = [pid for pid, v in VARIANTS.items() if v['category'] == category_id]
    originals = variants or ([category_id] if amount(category_id, 'SAR') is not None else [])
    rows = [[btn(compact_name(pid, cid) + ' | 💰 ' + price(cid, pid) + ' | ' + compact_stock(product_stock(pid)), 'options:' + pid,
                 ui_icon(pid), style='danger' if not in_stock(pid) else None)]
            for pid in originals + custom if product_visible(pid)]
    send(api, cid, category_heading(category_id, cid), kb(rows + [nav(cid)]))
    return True


def handle_admin_product(api, message):
    cid = message.get('chat', {}).get('id')
    if cid != G.get('ADMIN_ID'):
        return False
    with db() as conn:
        state = conn.execute('SELECT action,value FROM admin_state WHERE cid=?', (cid,)).fetchone()
    if not state or not state[0].startswith('add_product_'):
        return False
    raw = (message.get('text') or '').strip()
    if not raw:
        send(api, cid, 'أرسل نصًا للمتابعة.')
        return True
    action_name, payload_raw = state
    try:
        payload = json.loads(payload_raw or '{}')
    except Exception:
        payload = {}

    if action_name == 'add_product_category':
        payload = {'category_name': raw[:80], 'products': [], 'index': 0}
        next_action = 'add_product_count'
        prompt = '2️⃣ كم <b>عدد المنتجات</b> التي تريد إضافتها داخل قسم <b>' + esc(payload['category_name']) + '</b>؟\nمثال: <code>4</code>'
    elif action_name == 'add_product_count':
        normalized = raw.translate(str.maketrans('٠١٢٣٤٥٦٧٨٩', '0123456789'))
        try:
            count = int(normalized)
            if count < 1 or count > 50:
                raise ValueError()
        except Exception:
            send(api, cid, 'أرسل عددًا من <b>1 إلى 50</b>.')
            return True
        payload['count'] = count
        payload['index'] = 0
        next_action = 'add_product_name'
        prompt = '3️⃣ أرسل <b>اسم المنتج 1 من ' + str(count) + '</b>. سيتم إنشاء الإنجليزية تلقائيًا.'
    elif action_name == 'add_product_name':
        payload['current'] = {'name': raw[:100]}
        next_action = 'add_product_desc'
        prompt = '📝 أرسل <b>وصف المنتج</b> لـ <b>' + esc(payload['current']['name']) + '</b>. سيتم إنشاء الإنجليزية تلقائيًا.'
    elif action_name == 'add_product_desc':
        payload['current']['description'] = raw[:1500]
        payload['current']['description_html'] = description_message_html(message, raw[:1500])
        payload['current']['name_en'] = auto_translate(payload['current']['name'], 'en')[:100]
        payload['current']['description_en'] = auto_translate(payload['current']['description'], 'en')[:1500]
        next_action = 'add_product_price'
        prompt = '✅ تم إنشاء النسخة الإنجليزية تلقائيًا.\n\n💵 أرسل <b>السعر بالدولار USD</b>.\nمثال: <code>5.36</code>'
    elif action_name == 'add_product_price':
        normalized = raw.replace('$','').strip().translate(str.maketrans('٠١٢٣٤٥٦٧٨٩٫', '0123456789.')).replace(',', '.')
        try:
            value = Decimal(normalized).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            if value <= 0:
                raise ValueError()
        except Exception:
            send(api, cid, 'السعر غير صحيح. أرسل رقمًا بالدولار مثل <code>5.36</code>.')
            return True
        payload['current']['price_usd'] = str(value)
        next_action = 'add_product_stock'
        prompt = '📦 كم <b>الكمية المتوفرة</b> من هذا المنتج؟\nمثال: <code>10</code>'
    elif action_name == 'add_product_stock':
        normalized = raw.translate(str.maketrans('٠١٢٣٤٥٦٧٨٩', '0123456789'))
        try:
            stock = int(normalized)
            if stock < 0 or stock > 1000000:
                raise ValueError()
        except Exception:
            send(api, cid, 'أرسل كمية صحيحة مثل <code>10</code>.')
            return True
        payload['current']['stock'] = stock
        payload['products'].append(payload.pop('current'))
        payload['index'] = int(payload.get('index', 0)) + 1
        if payload['index'] < int(payload['count']):
            next_action = 'add_product_name'
            prompt = '✅ تم حفظ بيانات المنتج ' + str(payload['index']) + '.\n\nأرسل <b>اسم المنتج ' + str(payload['index'] + 1) + ' من ' + str(payload['count']) + '</b>. سيتم إنشاء الإنجليزية تلقائيًا.'
        else:
            lines = ['✅ <b>راجع القسم قبل الحفظ</b>', '', '📁 ' + esc(payload['category_name'])]
            for i, product in enumerate(payload['products'], 1):
                lines.append(str(i) + '. <b>' + esc(product['name']) + '</b> — $' + esc(product['price_usd']) + ' — الكمية: ' + str(product['stock']))
            lines += ['', 'أرسل <b>نعم</b> لحفظ القسم والمنتجات أو <b>لا</b> للإلغاء.']
            next_action = 'add_product_confirm'
            prompt = '\n'.join(lines)
    else:
        if raw.lower() not in ('نعم', 'yes', 'y'):
            with db() as conn:
                conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
            admin_products_page(api, cid)
            return True
        category_id = payload.get('category_id') or 'cat_' + uuid.uuid4().hex[:10]
        if payload.get('category_id') and category_id not in G['PRODUCTS'] and not custom_category(category_id):
            send(api, cid, 'القسم لم يعد موجودًا. اختر قسمًا آخر.')
            add_to_category(api, cid)
            return True
        saved_product_ids = []
        with db() as conn:
            if not payload.get('category_id'):
                conn.execute('INSERT INTO admin_categories(cid,name,created_at) VALUES (?,?,?)', (category_id, payload['category_name'], now_saudi()))
                conn.execute('INSERT OR REPLACE INTO product_text(pid,field,lang,value) VALUES (?,?,?,?)', (category_id, 'name', 'en', auto_translate(payload['category_name'], 'en')[:100]))
            for product in payload['products']:
                pid = 'custom_' + uuid.uuid4().hex[:10]
                usd = Decimal(product['price_usd']).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                sar = (usd * RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                conn.execute('INSERT INTO admin_products(pid,name,description,price_sar,available,created_at,category_id,price_usd,stock) VALUES (?,?,?,?,1,?,?,?,?)',
                             (pid, product['name'], product.get('description',''), str(sar), now_saudi(), category_id, str(usd), int(product.get('stock',1))))
                saved_product_ids.append(pid)
                conn.execute('INSERT OR REPLACE INTO product_text(pid,field,lang,value) VALUES (?,?,?,?)', (pid, 'name', 'en', product['name_en']))
                conn.execute('INSERT OR REPLACE INTO product_text(pid,field,lang,value) VALUES (?,?,?,?)', (pid, 'description', 'en', product['description_en']))
                if 'description_html' in product:
                    conn.execute('INSERT OR REPLACE INTO description_emoji VALUES (?,?,?,?)',
                                 (pid, '*', product.get('description', ''), product['description_html']))
            conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
        with db() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS channel_publish_choices (pid TEXT PRIMARY KEY, status TEXT NOT NULL)')
            for new_pid in saved_product_ids:
                conn.execute("INSERT OR REPLACE INTO channel_publish_choices VALUES (?,'pending')", (new_pid,))
        send(api, cid, '✅ تم حفظ المنتجات وظهرت في المتجر.\\n\\n📣 هل تريد نشرها في القناة الآن أم لاحقًا؟',
             kb([[btn('📣 نشر الآن', 'channel:publish_new:' + ','.join(saved_product_ids), style='success')],
                 [btn('🕒 لاحقًا', 'channel:defer_new:' + ','.join(saved_product_ids))]]))
        return True

    with db() as conn:
        conn.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)', (cid, next_action, json.dumps(payload, ensure_ascii=False)))
    send(api, cid, prompt, kb([[btn('❌ إلغاء', 'admin:cancelproduct')]]))
    return True


def admin_product_detail(api, cid, pid):
    if cid != G['ADMIN_ID']:
        return home(api, cid)
    cp = custom_product(pid)
    if cp:
        _, product_name, description, price_usd, available, category_id, stock = cp
        qty = int(stock or 0)
        is_available = bool(available and qty > 0)
        rows = [[btn('🖼️ إضافة/تعديل صورة المنتج', 'photopick:' + pid)],
                [btn('💵 تعديل السعر', 'pricepick:' + pid)],
                [btn('🔌 ربط API بالمنتج', 'supplierpick:' + pid, style='primary')],
                [btn('🔄 تغيير التوفر', 'myproducttoggle:' + pid)],
                [btn('🗑 حذف المنتج', 'myproductdelete:' + pid, style='danger')],
                [btn('↩️ القسم', 'mycategory:' + category_id)]]
    else:
        if pid not in VARIANTS and pid not in G['PRODUCTS']:
            return admin_products_page(api, cid)
        product_name = name(pid, cid)
        description = product_description(pid, cid)
        qty = product_stock(pid)
        is_available = bool(in_stock(pid))
        category_id = VARIANTS.get(pid, {}).get('category', pid)
        rows = [[btn('🖼️ إضافة/تعديل صورة المنتج', 'photopick:' + pid)],
                [btn('💵 تعديل السعر', 'pricepick:' + pid)],
                [btn('🔌 ربط API بالمنتج', 'supplierpick:' + pid, style='primary')],
                [btn('📦 تعديل التوفر/الكمية', 'admin:stock')],
                [btn('↩️ القسم', 'mycategory:' + category_id)]]
    text = ('📦 <b>' + esc(product_name) + '</b>\n\n' + esc(description or '') +
            '\n\n💵 ' + price(cid, pid, 'USD') +
            '\n📦 الكمية: ' + esc(qty) +
            '\nالحالة: ' + ('✅ متوفر' if is_available else '🔴 غير متوفر'))
    send(api, cid, text, kb(rows))


def toggle_admin_product(api, cid, pid):
    if cid != G['ADMIN_ID']:
        return
    with db() as conn:
        conn.execute('UPDATE admin_products SET available=CASE available WHEN 1 THEN 0 ELSE 1 END WHERE pid=?', (pid,))
    admin_product_detail(api, cid, pid)


def delete_admin_product(api, cid, pid):
    if cid != G['ADMIN_ID']:
        return
    cp = custom_product(pid)
    category_id = cp[5] if cp else None
    with db() as conn:
        conn.execute('DELETE FROM admin_products WHERE pid=?', (pid,))
    if category_id:
        admin_category_detail(api, cid, category_id)
    else:
        admin_products_page(api, cid)


def admin_panel(api, cid):
    if cid != G['ADMIN_ID']:
        return home(api, cid)
    with db() as conn:
        conn.execute("DELETE FROM admin_state WHERE cid=? AND action='price'", (cid,))
        orders_count = conn.execute('SELECT COUNT(*) FROM orders').fetchone()[0]
        review_count = conn.execute('SELECT COUNT(*) FROM orders WHERE status="review"').fetchone()[0]
        activity_count = total_activity_count(conn)
    text = f'🧾 <b>لوحة إدارة VEXA</b>\n\nالطلبات: <b>{orders_count}</b>\nبانتظار المراجعة: <b>{review_count}</b>\nسجل الاختيارات: <b>{activity_count}</b>'
    send(api, cid, text, kb([[btn('🔔 الطلبات الجديدة / التسليم', 'admin:orders', style='primary')],
                             [btn('👀 نشاط العملاء', 'admin:activity')],
                             [btn('📨 مراسلات العملاء', 'inbox:menu', style='primary')],
                             [btn('➕ إضافة منتج', 'admin:addproduct', style='success'), btn('📦 منتجاتي', 'admin:myproducts')],
                             [btn('🎟 أكواد الخصم', 'couponadmin:list')],
                             [btn('✏️ تعديل السعر', 'admin:prices')],
                             [btn('🎛 إعداد عرض بيانات المنتج', 'admin:info', style='primary')],
                             [btn('📦 تعديل توفر المنتج', 'admin:stock')],
                             [btn('🔌 ربط API بالمنتج', 'admin:supplierapi', style='primary')],
                             [btn('📢 إرسال رسالة للجميع', 'admin:broadcast', style='primary')],
                             [btn('📊 الإحصائيات', 'admin:stats')],
                             [btn('➕ إضافة أيقونة', 'admin:icons', style='success')],
                             [btn('✏️ تعديل أسماء الأزرار', 'admin:buttonlabels')],
                             [btn(ui_label('ui_category_description', 'تعديل وصف القسم'), 'admin:categorydesc', ui_icon('ui_category_description'))],
                             [btn('🏠 الرئيسية', 'home')]]))


def supplier_api_row(pid):
    pid = LEGACY.get(pid, pid)
    with db() as conn:
        row = conn.execute('SELECT endpoint,api_key,service_id,enabled,COALESCE(provider,"generic"),COALESCE(variant_id,"") FROM supplier_api WHERE pid=?', (pid,)).fetchone()
    if not row:
        return ('', '', '', 0, 'generic', '')
    endpoint, api_key, service_id, enabled, provider, variant_id = row
    # For Pandora, prefer Railway secrets so a rotated key is picked up immediately
    # without storing the secret in SQLite or GitHub.
    if provider == 'pandora':
        endpoint = (os.getenv('PANDORA_API_BASE') or 'https://api.pandoradigital.shop/api/v1').strip()
        api_key = (os.getenv('PANDORA_API_KEY') or '').strip()
    return endpoint, api_key, service_id, enabled, provider, variant_id


def supplier_api_menu(api, cid, category_id=None):
    if cid != G['ADMIN_ID']:
        return
    with db() as conn:
        conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
        custom_cats = conn.execute('SELECT cid,name FROM admin_categories ORDER BY rowid').fetchall()
    if category_id is None:
        cats = [(pid, ('YouTube' if pid == 'youtube' else name(pid, cid))) for pid in G['PRODUCTS']]
        cats += [(pid, label) for pid, label in custom_cats if pid not in G['PRODUCTS']]
        cats.sort(key=lambda item: (0 if str(item[0]).lower() == 'capcut' or 'capcut' in str(item[1]).lower() else 1, str(item[1]).lower()))
        rows = [[btn(label, 'suppliercat:' + pid)] for pid, label in cats]
        send(api, cid, '🔌 <b>ربط API بالمنتج</b>\n\nاختر القسم:', kb(rows + [[btn('↩️ لوحة الإدارة', 'admin')]]))
        return
    ids = admin_category_product_ids(category_id)
    rows = []
    for pid in dict.fromkeys(ids):
        endpoint, api_key, service_id, enabled, provider, variant_id = supplier_api_row(pid)
        mark = '🟢 ' if enabled and endpoint and api_key else '⚪️ '
        rows.append([btn(mark + name(pid, cid), 'supplierpick:' + pid)])
    send(api, cid, '🔌 اختر المنتج الذي تريد ربطه بالمورد:', kb(rows + [[btn('↩️ الأقسام', 'admin:supplierapi')], [btn('↩️ لوحة الإدارة', 'admin')]]))


def _supplier_json_request(url, api_key, method='GET', payload=None, extra_headers=None, timeout=20):
    headers = {
        'Authorization': 'Bearer ' + api_key,
        'Accept': 'application/json',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.9'
    }
    if payload is not None:
        headers['Content-Type'] = 'application/json'
    if extra_headers:
        headers.update(extra_headers)
    data = json.dumps(payload).encode('utf-8') if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode('utf-8')
    return json.loads(raw) if raw else {}


def supplier_delivery_text(items, cid):
    clean = [str(x).strip() for x in (items or []) if str(x).strip()]
    if not clean:
        return ''
    title = tr(cid, '✅ <b>تم تسليم طلبك تلقائيًا</b>', '✅ <b>Your order was delivered automatically</b>')
    body = '\n'.join('• <code>' + esc(x) + '</code>' for x in clean)
    return title + '\n\n' + body


def pandora_pricing_row(pid):
    pid = LEGACY.get(pid, pid)
    with db() as conn:
        row = conn.execute('SELECT supplier_cost_usd,margin_usd,updated_at FROM pandora_pricing WHERE pid=?', (pid,)).fetchone()
    return row or ('', '0', '')


def pandora_quote_cost(pid, quantity=1):
    endpoint, api_key, product_id, enabled, provider, variant_id = supplier_api_row(pid)
    if not (provider == 'pandora' and endpoint and api_key and product_id and variant_id):
        return None
    quote = _supplier_json_request(endpoint.rstrip('/') + '/quotes', api_key, 'POST',
        {'product_id': product_id, 'variant_id': variant_id, 'quantity': int(quantity or 1)})
    if not quote.get('can_purchase', False) or quote.get('unit_price') is None:
        return None
    return Decimal(str(quote.get('unit_price'))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def pandora_refresh_price(pid):
    pid = LEGACY.get(pid, pid)
    cost = pandora_quote_cost(pid, 1)
    if cost is None:
        return None
    _, margin_raw, _ = pandora_pricing_row(pid)
    margin = Decimal(str(margin_raw or '0')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    sale = (cost + margin).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    with db() as conn:
        conn.execute('INSERT INTO pandora_pricing(pid,supplier_cost_usd,margin_usd,updated_at) VALUES (?,?,?,?) ON CONFLICT(pid) DO UPDATE SET supplier_cost_usd=excluded.supplier_cost_usd,updated_at=excluded.updated_at',
                     (pid, str(cost), str(margin), now_saudi()))
        conn.execute('INSERT OR REPLACE INTO product_prices(pid,value,currency) VALUES (?,?,?)', (pid, str(sale), 'USD'))
    return cost, margin, sale


def pandora_set_margin(pid, margin):
    pid = LEGACY.get(pid, pid)
    margin = Decimal(str(margin)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    cost_raw, _, _ = pandora_pricing_row(pid)
    if not cost_raw:
        refreshed = pandora_refresh_price(pid)
        if not refreshed:
            return None
        cost_raw = str(refreshed[0])
    cost = Decimal(str(cost_raw)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    sale = (cost + margin).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    with db() as conn:
        conn.execute('INSERT INTO pandora_pricing(pid,supplier_cost_usd,margin_usd,updated_at) VALUES (?,?,?,?) ON CONFLICT(pid) DO UPDATE SET margin_usd=excluded.margin_usd,updated_at=excluded.updated_at',
                     (pid, str(cost), str(margin), now_saudi()))
        conn.execute('INSERT OR REPLACE INTO product_prices(pid,value,currency) VALUES (?,?,?)', (pid, str(sale), 'USD'))
    return sale


def pandora_fulfill_order(api, internal_order_id):
    with db() as conn:
        order = conn.execute('SELECT cid,pid,status FROM orders WHERE id=?', (internal_order_id,)).fetchone()
        snap = conn.execute('SELECT quantity FROM quantity_snapshots WHERE scope=? AND key=?', ('order', internal_order_id)).fetchone()
        existing = conn.execute('SELECT supplier_order_id,status,delivery FROM supplier_orders WHERE order_id=?', (internal_order_id,)).fetchone()
    if not order:
        return False
    cid, pid, order_status = order
    endpoint, api_key, product_id, enabled, provider, variant_id = supplier_api_row(pid)
    if not (enabled and provider == 'pandora' and endpoint and api_key and product_id and variant_id):
        return False
    quantity = int(snap[0]) if snap and snap[0] else 1

    try:
        supplier_order_id = existing[0] if existing and existing[0] else ''
        result = None

        if supplier_order_id:
            result = _supplier_json_request(endpoint.rstrip('/') + '/orders/' + urllib.parse.quote(str(supplier_order_id)), api_key)
        else:
            quote = _supplier_json_request(
                endpoint.rstrip('/') + '/quotes', api_key, 'POST',
                {'product_id': product_id, 'variant_id': variant_id, 'quantity': quantity}
            )
            if not quote.get('can_purchase', False):
                raise RuntimeError('Supplier cannot fulfill now')
            unit_price = quote.get('unit_price')
            price_version = quote.get('price_version')
            if unit_price is None or not price_version:
                raise RuntimeError('Invalid quote response')
            current_cost = Decimal(str(unit_price)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            _, margin_raw, _ = pandora_pricing_row(pid)
            configured_margin = Decimal(str(margin_raw or '0')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            with db() as conn:
                paid = conn.execute('SELECT usd FROM orders WHERE id=?', (internal_order_id,)).fetchone()
            paid_unit = (Decimal(str(paid[0] if paid and paid[0] else '0')) / Decimal(quantity)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            if paid_unit < (current_cost + configured_margin):
                raise RuntimeError('Pandora price changed below configured margin')
            expected_unit_price = float(current_cost)
            payload = {
                'product_id': product_id,
                'variant_id': variant_id,
                'quantity': quantity,
                'expected_unit_price': expected_unit_price,
                'price_version': price_version,
                'client_order_reference': internal_order_id
            }
            idem = 'vexa-' + internal_order_id
            result = _supplier_json_request(
                endpoint.rstrip('/') + '/orders', api_key, 'POST', payload,
                {'Idempotency-Key': idem}
            )
            supplier_order_id = str(result.get('id') or '')
            with db() as conn:
                conn.execute('INSERT OR REPLACE INTO supplier_orders(order_id,supplier_order_id,status,delivery,last_error,updated_at) VALUES (?,?,?,?,?,?)',
                             (internal_order_id, supplier_order_id, str(result.get('status') or ''), '', '', now_saudi()))

        # Short polling window for orders that finish just after creation.
        for _ in range(5):
            delivery = (result or {}).get('delivery') or {}
            items = delivery.get('items') or []
            status = str((result or {}).get('status') or '')
            if items:
                text = supplier_delivery_text(items, cid)
                with db() as conn:
                    conn.execute('UPDATE supplier_orders SET status=?,delivery=?,last_error="",updated_at=? WHERE order_id=?',
                                 (status or 'delivered', json.dumps(items, ensure_ascii=False), now_saudi(), internal_order_id))
                    conn.execute('UPDATE orders SET status="paid" WHERE id=?', (internal_order_id,))
                send(api, cid, text, menu(cid))
                send(api, G['ADMIN_ID'], '✅ <b>تسليم تلقائي عبر Pandora</b>\nالطلب: <code>' + esc(internal_order_id) + '</code>\nالعميل: <code>' + esc(cid) + '</code>\nالمنتج: ' + esc(name(pid, cid)))
                return True
            if not supplier_order_id or status.lower() in ('failed','rejected','cancelled'):
                break
            time.sleep(1.2)
            result = _supplier_json_request(endpoint.rstrip('/') + '/orders/' + urllib.parse.quote(str(supplier_order_id)), api_key)

        with db() as conn:
            conn.execute('UPDATE supplier_orders SET status=?,last_error=?,updated_at=? WHERE order_id=?',
                         (str((result or {}).get('status') or 'pending'), 'delivery_pending', now_saudi(), internal_order_id))
        send(api, cid, tr(cid, '✅ تم استلام طلبك وهو قيد التجهيز التلقائي. سيتم متابعته من الإدارة إذا تأخر التسليم.', '✅ Your order was received and is being processed automatically. Administration will follow up if delivery is delayed.'))
        send(api, G['ADMIN_ID'], '⚠️ <b>طلب Pandora بانتظار التسليم</b>\nالطلب: <code>' + esc(internal_order_id) + '</code>\nSupplier order: <code>' + esc(supplier_order_id or 'unknown') + '</code>')
        return True
    except Exception as exc:
        with db() as conn:
            conn.execute('INSERT INTO supplier_orders(order_id,supplier_order_id,status,delivery,last_error,updated_at) VALUES (?,?,?,?,?,?) ON CONFLICT(order_id) DO UPDATE SET status=excluded.status,last_error=excluded.last_error,updated_at=excluded.updated_at',
                         (internal_order_id, existing[0] if existing else '', 'error', '', type(exc).__name__, now_saudi()))
        send(api, G['ADMIN_ID'], '❌ <b>فشل تنفيذ طلب Pandora تلقائيًا</b>\nالطلب: <code>' + esc(internal_order_id) + '</code>\nالخطأ: <code>' + esc(type(exc).__name__) + '</code>')
        send(api, cid, tr(cid, '✅ تم الدفع، لكن تعذر التسليم التلقائي الآن. تم تحويل الطلب للإدارة لإكماله بدون إعادة الدفع.', '✅ Payment was received, but automatic delivery failed. The order was sent to administration; you do not need to pay again.'))
        return True


def fulfill_paid_order(api, order_id):
    """Run the configured supplier exactly once after payment approval.

    Pandora uses supplier_orders + client_order_reference + Idempotency-Key,
    so retries/restarts cannot create duplicate supplier orders.
    """
    with db() as conn:
        row = conn.execute('SELECT pid,status FROM orders WHERE id=?', (order_id,)).fetchone()
    if not row or row[1] != 'paid':
        return False
    pid = row[0]
    endpoint, api_key, product_id, enabled, provider, variant_id = supplier_api_row(pid)
    if not enabled:
        return False
    if provider == 'pandora' and endpoint and api_key and product_id and variant_id:
        return pandora_fulfill_order(api, order_id)
    return False


def supplier_test_connection(api, cid, pid):
    if cid != G['ADMIN_ID']:
        return
    endpoint, api_key, service_id, enabled, provider, variant_id = supplier_api_row(pid)
    if not endpoint or not api_key:
        return send(api, cid, '⚠️ أضف رابط API والمفتاح أولًا.')
    try:
        endpoint = ''.join(ch for ch in endpoint.strip() if ord(ch) < 128)
        api_key = ''.join(ch for ch in api_key.strip() if ord(ch) < 128)
        if not endpoint.startswith(('http://','https://')):
            return send(api, cid, '❌ رابط API غير صحيح. أعد حفظ الرابط بدون أي رموز أو مسافات إضافية.')
        if not api_key:
            return send(api, cid, '❌ مفتاح API غير صحيح أو يحتوي رموز غير مدعومة. أعد نسخه من Pandora ثم احفظه من جديد.')

        # The integration needs catalog access to locate products/variants.
        # balance:read is optional, so test /products instead of /balance.
        req = urllib.request.Request(endpoint.rstrip('/') + '/products?limit=1',
                                     headers={'Authorization': 'Bearer ' + api_key,
                                              'Accept': 'application/json',
                                              'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36',
                                              'Accept-Language': 'en-US,en;q=0.9'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        count = len(data.get('items') or []) if isinstance(data, dict) else 0
        send(api, cid, '✅ اتصال Pandora ناجح والمفتاح يملك صلاحية قراءة المنتجات.\n📦 تم الوصول إلى الكتالوج بنجاح.')
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            msg = '❌ المفتاح غير صحيح أو منتهي أو ملغي.'
        elif exc.code == 403:
            msg = '❌ المفتاح لا يملك صلاحية <code>catalog:read</code>. اطلب تفعيلها على مفتاح Pandora.'
        else:
            msg = '❌ فشل الاتصال مع Pandora. HTTP ' + str(exc.code)
        send(api, cid, msg)
    except UnicodeEncodeError:
        send(api, cid, '❌ يوجد رمز أو مسافة غير صالحة داخل رابط API أو المفتاح. أعد نسخهما مباشرة من Pandora بدون أي نص إضافي.')
    except Exception as exc:
        send(api, cid, '❌ فشل اختبار الاتصال: <code>' + esc(type(exc).__name__) + '</code>')


def pandora_startup_probe():
    """Read-only Pandora auth/connectivity diagnostics. Never creates an order."""
    endpoint = (os.getenv('PANDORA_API_BASE') or 'https://api.pandoradigital.shop/api/v1').strip()
    api_key = (os.getenv('PANDORA_API_KEY') or '').strip()
    if not api_key:
        print('Pandora probe: missing PANDORA_API_KEY', flush=True)
        return
    url = endpoint.rstrip('/') + '/products?limit=1'
    modes = [
        ('bearer', {'Authorization': 'Bearer ' + api_key, 'Accept': 'application/json',
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36',
                    'Accept-Language': 'en-US,en;q=0.9'}),
        ('x-api-key', {'X-API-Key': api_key, 'Accept': 'application/json',
                       'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36',
                       'Accept-Language': 'en-US,en;q=0.9'}),
        ('api-key', {'Api-Key': api_key, 'Accept': 'application/json',
                     'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36',
                     'Accept-Language': 'en-US,en;q=0.9'}),
        ('auth-raw', {'Authorization': api_key, 'Accept': 'application/json',
                      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36',
                      'Accept-Language': 'en-US,en;q=0.9'}),
    ]
    for mode, headers in modes:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=12) as resp:
                raw = resp.read().decode('utf-8', 'replace')
                data = json.loads(raw) if raw else {}
            items = _pandora_list(data)
            print('Pandora probe: auth=' + mode + ' OK products_read=' + str(len(items)), flush=True)
            return
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode('utf-8', 'replace')[:500]
            except Exception:
                body = ''
            print('Pandora probe: auth=' + mode + ' HTTP ' + str(exc.code) + ' body=' + body.replace('\n',' '), flush=True)
        except Exception as exc:
            print('Pandora probe: auth=' + mode + ' failed ' + type(exc).__name__, flush=True)


def _pandora_bool(value, default=True):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    return str(value).strip().lower() not in ('0','false','no','off','disabled','unavailable','out_of_stock','sold_out')


def _pandora_stock_value(item):
    if not isinstance(item, dict):
        return None
    for key in ('stock','quantity','qty','available_stock','inventory','remaining'):
        value = item.get(key)
        if isinstance(value, dict):
            value = value.get('available') or value.get('quantity') or value.get('stock')
        try:
            if value is not None:
                return max(0, int(float(value)))
        except Exception:
            pass
    return None


def pandora_sync_product(pid):
    """Refresh local availability/stock from the linked Pandora product/variant."""
    endpoint, api_key, product_id, enabled, provider, variant_id = supplier_api_row(pid)
    if not (provider == 'pandora' and endpoint and api_key and product_id and variant_id):
        return None
    try:
        payload = _supplier_json_request(endpoint.rstrip('/') + '/products?limit=100', api_key, timeout=20)
        target_product = None
        for item in _pandora_list(payload):
            if _pandora_product_id(item) == str(product_id):
                target_product = item
                break
        if not target_product:
            return None

        target_variant = None
        raw_variants = target_product.get('variants') or target_product.get('options') or target_product.get('skus') or []
        if isinstance(raw_variants, dict):
            raw_variants = raw_variants.get('data') or raw_variants.get('items') or list(raw_variants.values())
        if isinstance(raw_variants, list):
            for variant in raw_variants:
                if not isinstance(variant, dict):
                    continue
                vid = str(variant.get('id') or variant.get('variant_id') or variant.get('variantId') or variant.get('sku') or '')
                if vid == str(variant_id):
                    target_variant = variant
                    break

        source = target_variant or target_product
        stock = _pandora_stock_value(source)
        if stock is None:
            stock = _pandora_stock_value(target_product)
        available_value = source.get('available') if isinstance(source, dict) else None
        if available_value is None and isinstance(source, dict):
            available_value = source.get('is_available')
        if available_value is None and isinstance(source, dict):
            available_value = source.get('active')
        available = _pandora_bool(available_value, True)
        if stock is not None:
            available = available and stock > 0

        with db() as conn:
            if custom_product(pid):
                if stock is not None:
                    conn.execute('UPDATE admin_products SET stock=?,available=? WHERE pid=?', (stock, 1 if available else 0, pid))
                else:
                    conn.execute('UPDATE admin_products SET available=? WHERE pid=?', (1 if available else 0, pid))
            else:
                if stock is not None:
                    conn.execute('INSERT OR REPLACE INTO product_stock_overrides(pid,stock) VALUES (?,?)', (pid, stock))
                conn.execute('INSERT OR REPLACE INTO product_availability(pid,available) VALUES (?,?)', (pid, 1 if available else 0))
        return {'stock': stock, 'available': available}
    except Exception as exc:
        print('Pandora stock sync failed:', pid, type(exc).__name__, flush=True)
        return None


def supplier_api_editor(api, cid, pid):
    if cid != G['ADMIN_ID']:
        return
    pid = LEGACY.get(pid, pid)
    if pid not in VARIANTS and pid not in G['PRODUCTS'] and not custom_product(pid):
        return supplier_api_menu(api, cid)
    endpoint, api_key, service_id, enabled, provider, variant_id = supplier_api_row(pid)
    if provider == 'pandora' and service_id and variant_id:
        pandora_sync_product(pid)
        endpoint, api_key, service_id, enabled, provider, variant_id = supplier_api_row(pid)
    masked = ('••••••' + api_key[-4:]) if api_key else 'غير مضاف'
    text = ('🔌 <b>' + esc(name(pid, cid)) + '</b>\n\n'
            'المورد: <b>' + esc('Pandora Digital' if provider == 'pandora' else 'Generic API') + '</b>\n'
            'الحالة: <b>' + ('🟢 مفعّل' if enabled else '⚪️ غير مفعّل') + '</b>\n'
            '🌐 رابط Pandora العام: <b>' + ('✅ جاهز' if endpoint else '❌ غير موجود') + '</b>\n'
            '🔑 مفتاح Pandora العام: <b>' + ('✅ جاهز' if api_key else '❌ غير موجود') + '</b>\n'
            '🆔 Product ID: <code>' + esc(service_id or 'غير مضاف') + '</code>\n'
            '🧩 Variant ID: <code>' + esc(variant_id or 'غير مضاف') + '</code>\n\n'
            'أضف فقط Product ID و Variant ID لهذا المنتج.')
    rows = [[btn('🧩 Pandora Digital', 'supplierpandora:' + pid, style='primary')],
            [btn('🔎 اختيار منتج من Pandora', 'pandorabrowse:' + pid, style='primary')],
            [btn(('✅ ' if service_id else '❌ ') + 'Product ID', 'supplierset:service:' + pid), btn(('✅ ' if variant_id else '❌ ') + 'Variant ID', 'supplierset:variant:' + pid)],
            [btn('💰 تحديث تكلفة Pandora', 'supplierprice:' + pid, style='primary')],
            [btn('➕ تعديل هامش الربح', 'suppliermargin:' + pid, style='success')],
            [btn('🧪 اختبار الاتصال', 'suppliertest:' + pid)],
            [btn('✅ تفعيل الربط' if not enabled else '⏸ إيقاف الربط', 'suppliertoggle:' + pid, style='success' if not enabled else 'danger')],
            [btn('🗑 حذف الربط', 'supplierdelete:' + pid, style='danger')],
            [btn('↩️ المنتجات', 'admin:supplierapi')], [btn('↩️ لوحة الإدارة', 'admin')]]
    send(api, cid, text, kb(rows))



def _pandora_list(payload):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ('data','products','items','results'):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            for nested in ('data','products','items','results'):
                if isinstance(value.get(nested), list):
                    return value[nested]
    return []


def _pandora_product_name(item):
    if not isinstance(item, dict):
        return str(item)
    return str(item.get('name') or item.get('title') or item.get('product_name') or item.get('label') or item.get('id') or 'Pandora Product')


def _pandora_category_name(item):
    """Best-effort Pandora category label from common API payload shapes."""
    if not isinstance(item, dict):
        return 'Other'
    for key in ('category_name','category','product_category','service','group','brand','type'):
        value = item.get(key)
        if isinstance(value, dict):
            value = value.get('name') or value.get('title') or value.get('label') or value.get('slug') or value.get('id')
        elif isinstance(value, list):
            value = next((x.get('name') if isinstance(x, dict) else x for x in value if x), None)
        if value:
            text = str(value).strip()
            if text:
                return text[:60]
    return 'Other'


def _pandora_product_id(item):
    if not isinstance(item, dict):
        return ''
    return str(item.get('id') or item.get('product_id') or item.get('productId') or item.get('uuid') or '')


def _pandora_variants(item):
    if not isinstance(item, dict):
        return []
    variants = item.get('variants') or item.get('options') or item.get('skus') or []
    if isinstance(variants, dict):
        variants = variants.get('data') or variants.get('items') or list(variants.values())
    if not isinstance(variants, list):
        variants = []
    out = []
    for v in variants:
        if not isinstance(v, dict):
            continue
        vid = str(v.get('id') or v.get('variant_id') or v.get('variantId') or v.get('sku') or '')
        if not vid:
            continue
        label = str(v.get('name') or v.get('title') or v.get('label') or v.get('sku') or vid)
        out.append({'id': vid, 'name': label})
    root_vid = str(item.get('variant_id') or item.get('variantId') or '')
    if not out and root_vid:
        out.append({'id': root_vid, 'name': str(item.get('variant_name') or item.get('variantName') or 'Default')})
    return out


def pandora_catalog_open(api, cid, pid, page=0):
    if cid != G['ADMIN_ID']:
        return
    pid = LEGACY.get(pid, pid)
    endpoint = (os.getenv('PANDORA_API_BASE') or 'https://api.pandoradigital.shop/api/v1').strip()
    api_key = (os.getenv('PANDORA_API_KEY') or '').strip()
    if not api_key:
        return send(api, cid, '❌ مفتاح Pandora غير موجود في Railway.')
    try:
        payload = _supplier_json_request(endpoint.rstrip('/') + '/products?limit=100', api_key, timeout=20)
        raw_items = _pandora_list(payload)
        products = []
        for item in raw_items:
            product_id = _pandora_product_id(item)
            if not product_id:
                continue
            products.append({'id': product_id, 'name': _pandora_product_name(item)[:80],
                             'category': _pandora_category_name(item),
                             'variants': _pandora_variants(item)})
        if not products:
            return send(api, cid, '⚠️ لم أستطع قراءة منتجات Pandora. تأكد أن المفتاح يملك صلاحية <code>catalog:read</code>.', kb([[btn('↩️ رجوع', 'supplierpick:' + pid)]]))
        with db() as conn:
            conn.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)',
                         (cid, 'pandora_catalog', json.dumps({'pid': pid, 'products': products}, ensure_ascii=False)))
        pandora_catalog_categories(api, cid)
    except Exception as exc:
        code = getattr(exc, 'code', None)
        if code == 403:
            msg = '❌ مفتاح Pandora لا يملك صلاحية <code>catalog:read</code>.'
        elif code == 401:
            msg = '❌ مفتاح Pandora غير صحيح أو منتهي.'
        else:
            msg = '❌ تعذر تحميل كتالوج Pandora الآن: <code>' + esc(type(exc).__name__) + '</code>'
        send(api, cid, msg, kb([[btn('↩️ رجوع', 'supplierpick:' + pid)]]))


def pandora_catalog_categories(api, cid):
    with db() as conn:
        row = conn.execute("SELECT value FROM admin_state WHERE cid=? AND action='pandora_catalog'", (cid,)).fetchone()
    if not row:
        return supplier_api_menu(api, cid)
    state = json.loads(row[0])
    products = state.get('products') or []
    grouped = {}
    for index, product in enumerate(products):
        category = str(product.get('category') or 'Other').strip() or 'Other'
        grouped.setdefault(category, []).append(index)
    categories = sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0].lower()))
    state['pandora_categories'] = [{'name': name, 'items': items} for name, items in categories]
    with db() as conn:
        conn.execute('UPDATE admin_state SET value=? WHERE cid=? AND action="pandora_catalog"',
                     (json.dumps(state, ensure_ascii=False), cid))
    rows = []
    for cidx, (category, items) in enumerate(categories):
        rows.append([btn('📁 ' + category[:38] + ' • ' + str(len(items)), 'pandoracat:' + str(cidx) + ':0')])
    rows.append([btn('↩️ رجوع', 'supplierpick:' + state.get('pid',''))])
    send(api, cid, '🔎 <b>أقسام Pandora</b>\n\nاختر القسم، وسيظهر عدد المنتجات الموجودة داخله:', kb(rows))


def pandora_catalog_page(api, cid, category_index=0, page=0):
    with db() as conn:
        row = conn.execute("SELECT value FROM admin_state WHERE cid=? AND action='pandora_catalog'", (cid,)).fetchone()
    if not row:
        return supplier_api_menu(api, cid)
    state = json.loads(row[0])
    products = state.get('products') or []
    categories = state.get('pandora_categories') or []
    try:
        category_index = int(category_index)
        category = categories[category_index]
    except Exception:
        return pandora_catalog_categories(api, cid)
    indices = category.get('items') or []
    page = max(0, int(page or 0))
    per_page = 8
    start = page * per_page
    if start >= len(indices) and page:
        page = 0
        start = 0
    rows = []
    for pos in range(start, min(start + per_page, len(indices))):
        index = indices[pos]
        if index >= len(products):
            continue
        label = products[index].get('name') or products[index].get('id')
        rows.append([btn('📦 ' + str(label)[:45], 'pandorap:' + str(index))])
    nav = []
    if page > 0:
        nav.append(btn('⬅️ السابق', 'pandoracat:' + str(category_index) + ':' + str(page-1)))
    if start + per_page < len(indices):
        nav.append(btn('التالي ➡️', 'pandoracat:' + str(category_index) + ':' + str(page+1)))
    if nav:
        rows.append(nav)
    rows.append([btn('↩️ أقسام Pandora', 'pandoracategories')])
    rows.append([btn('↩️ إعداد API', 'supplierpick:' + state.get('pid',''))])
    send(api, cid, '📁 <b>' + esc(category.get('name','Pandora')) + '</b>\n'
         '📦 عدد المنتجات: <b>' + str(len(indices)) + '</b>\n\nاختر المنتج المطابق لمنتج VEXA:', kb(rows))


def pandora_catalog_product(api, cid, index):
    with db() as conn:
        row = conn.execute("SELECT value FROM admin_state WHERE cid=? AND action='pandora_catalog'", (cid,)).fetchone()
    if not row:
        return supplier_api_menu(api, cid)
    state = json.loads(row[0]); products = state.get('products') or []
    try:
        index = int(index); product = products[index]
    except Exception:
        return pandora_catalog_categories(api, cid)
    variants = product.get('variants') or []
    if len(variants) == 1:
        return pandora_catalog_save(api, cid, index, 0)
    if not variants:
        with db() as conn:
            current = conn.execute('SELECT endpoint,api_key,service_id,enabled,provider,variant_id FROM supplier_api WHERE pid=?', (state['pid'],)).fetchone()
            endpoint, api_key, _, enabled, _, variant_id = current if current else ('','','',0,'pandora','')
            conn.execute('INSERT INTO supplier_api(pid,endpoint,api_key,service_id,enabled,provider,variant_id) VALUES (?,?,?,?,?,?,?) ON CONFLICT(pid) DO UPDATE SET service_id=excluded.service_id,provider="pandora"',
                         (state['pid'], endpoint, api_key, product['id'], enabled, 'pandora', variant_id))
        return send(api, cid, '✅ تم حفظ Product ID تلقائيًا.\n⚠️ Pandora لم يُرجع Variant لهذا المنتج؛ أضف Variant ID يدويًا.', kb([[btn('↩️ إعداد API', 'supplierpick:' + state['pid'])]]))
    rows = [[btn('🧩 ' + str(v.get('name') or v.get('id'))[:45], 'pandorav:' + str(index) + ':' + str(i))] for i,v in enumerate(variants[:20])]
    rows.append([btn('↩️ أقسام Pandora', 'pandoracategories')])
    send(api, cid, '🧩 <b>' + esc(product.get('name','Pandora')) + '</b>\n\nاختر الـ Variant:', kb(rows))


def pandora_catalog_save(api, cid, pindex, vindex):
    with db() as conn:
        row = conn.execute("SELECT value FROM admin_state WHERE cid=? AND action='pandora_catalog'", (cid,)).fetchone()
    if not row:
        return supplier_api_menu(api, cid)
    state = json.loads(row[0]); products = state.get('products') or []
    try:
        product = products[int(pindex)]
        variant = (product.get('variants') or [])[int(vindex)]
    except Exception:
        return pandora_catalog_categories(api, cid)
    pid = state['pid']
    endpoint, api_key, _, enabled, _, _ = supplier_api_row(pid)
    with db() as conn:
        conn.execute('INSERT INTO supplier_api(pid,endpoint,api_key,service_id,enabled,provider,variant_id) VALUES (?,?,?,?,?,?,?) ON CONFLICT(pid) DO UPDATE SET service_id=excluded.service_id,variant_id=excluded.variant_id,provider="pandora",enabled=1',
                     (pid, endpoint, api_key, str(product['id']), 1, 'pandora', str(variant['id'])))
        conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
    sync = pandora_sync_product(pid)
    sync_text = ''
    if sync:
        sync_text = '\n📦 المخزون: <b>' + esc(sync.get('stock') if sync.get('stock') is not None else 'غير محدد') + '</b>\nالحالة: <b>' + ('🟢 متوفر' if sync.get('available') else '🔴 غير متوفر') + '</b>'
    send(api, cid, '✅ تم ربط المنتج وتفعيل Pandora تلقائيًا.\n\nProduct ID: <code>' + esc(product['id']) + '</code>\nVariant ID: <code>' + esc(variant['id']) + '</code>' + sync_text)
    supplier_api_editor(api, cid, pid)


def admin_stats(api, cid):
    if cid != G['ADMIN_ID']:
        return home(api, cid)
    try:
        users = [int(x) for x in json.loads(USERS_PATH.read_text(encoding='utf-8'))]
    except Exception:
        users = []
    total = len(set(users))
    with db() as conn:
        departed = conn.execute('SELECT COUNT(*) FROM user_delivery_status WHERE departed=1').fetchone()[0]
        row = conn.execute('SELECT sent,failed,created_at FROM broadcast_stats WHERE id=1').fetchone()
    available = max(total - departed, 0)
    sent, failed, created = row if row else (0, 0, 'لا توجد رسالة جماعية بعد')
    text = (f'📊 <b>إحصائيات البوت</b>\n\n'
            f'👥 إجمالي المستخدمين: <b>{total}</b>\n'
            f'🟢 المتاحون: <b>{available}</b>\n'
            f'🚪 غادروا البوت: <b>{departed}</b>\n\n'
            f'📢 <b>آخر رسالة جماعية</b>\n'
            f'✅ تم الإرسال: <b>{sent}</b>\n'
            f'❌ فشل الإرسال: <b>{failed}</b>\n'
            f'🕒 {esc(created)}')
    send(api, cid, text, kb([[btn('🔄 تحديث', 'admin:stats')], [btn('↩️ لوحة الإدارة', 'admin')]]))


def admin_orders(api, cid):
    if cid != G['ADMIN_ID']:
        return home(api, cid)
    with db() as conn:
        rows = conn.execute('SELECT id,cid,pid,method,usd,sar,status,created_at FROM orders ORDER BY rowid DESC LIMIT 20').fetchall()
        pending_count = conn.execute('SELECT COUNT(*) FROM orders WHERE status IN ("review","paid")').fetchone()[0]
    if not rows:
        return send(api, cid, '📦 لا توجد طلبات مسجلة حتى الآن.', kb([[btn('↩️ لوحة الإدارة', 'admin')]]))
    status_names = {'paid': '🟢 بانتظار التسليم', 'review': '🟡 بانتظار مراجعة الدفع', 'rejected': '❌ مرفوض', 'delivered': '✅ تم التسليم'}
    parts = [f'🔔 <b>الطلبات</b>\nبانتظار الإجراء: <b>{pending_count}</b>']
    action_buttons = []
    for oid, user_id, pid, method, usd, sar, status, created in rows:
        parts.append(f'\n<b>#{esc(oid)}</b> • {status_names.get(status, esc(status))}\n{esc(name(pid, cid))}\nالكمية: {product_options.snapshot(sys.modules[__name__], "order", oid)}\n{esc(sar)} SAR / {esc(usd)} USD • {esc(method)}\nالعميل: {customer_link(user_id)} • {esc(created)}')
        if status == 'review':
            action_buttons.append([btn('✅ قبول #' + oid, 'payreview:accept:' + oid, style='success'), btn('❌ رفض', 'payreview:reject:' + oid, style='danger')])
        elif status == 'paid':
            action_buttons.append([btn('📤 تسليم #' + oid, 'orderdeliver:' + oid, style='success')])
    action_buttons.extend([[btn('🔄 تحديث', 'admin:orders')], [btn('↩️ لوحة الإدارة', 'admin')]])
    send(api, cid, '\n'.join(parts), kb(action_buttons))


# Only the currently opened admin activity message is refreshed.
ACTIVITY_VIEW = {}
ACTIVITY_LABELS = {
    'category': 'فتح القسم', 'item': 'فتح المنتج', 'start': 'بدء البوت',
    'home': 'فتح الرئيسية', 'products': 'عرض المنتجات', 'buy': 'بدء الطلب',
    'wallet': 'فتح المحفظة', 'support': 'فتح الدعم', 'receipt': 'إرسال صورة',
    'message': 'إرسال رسالة', 'interaction': 'تفاعل مع البوت',
}


def track_customer_activity(cid, value=None, message=None):
    # Record the Telegram sender, never the chat containing the interaction.
    if not isinstance(cid, int) or cid <= 0:
        return
    if cid == G.get('ADMIN_ID'):
        if value != 'admin:activity':
            ACTIVITY_VIEW.clear()
        return
    if message is not None:
        text = message.get('text', '')
        value = G.get('MENU', {}).get(text)
        if text.startswith('/start'):
            value = 'start'
        elif text.startswith('/products'):
            value = 'products'
        if value is None:
            log_activity(cid, 'receipt' if message.get('photo') else 'message', '')
            return
    value = value or ''
    prefix, _, arg = value.partition(':')
    if value in LEGACY:
        action_name, pid = 'item', LEGACY[value]
    elif prefix in ('product', 'item', 'claude', 'buy'):
        action_name = {'product': 'category', 'claude': 'item'}.get(prefix, prefix)
        pid = LEGACY.get(arg, arg)
    else:
        action_name = {'enter_store': 'home'}.get(prefix, prefix)
        if action_name not in ACTIVITY_LABELS:
            action_name = 'interaction'
        pid = ''
    # Never persist private message text, payment details, or arbitrary callback data.
    log_activity(cid, action_name, pid)


def activity_page(cid):
    with db() as conn:
        rows = conn.execute('SELECT id,cid,action,pid,created_at FROM activity ORDER BY id DESC LIMIT 20').fetchall()
    parts = ['👀 <b>آخر نشاط العملاء</b>', '🟢 تحديث تلقائي أثناء فتح الصفحة (حتى 15 دقيقة).']
    if not rows:
        parts.append('لا يوجد نشاط مسجل حتى الآن.')
    for _, user_id, action_name, pid, created in rows:
        label = ACTIVITY_LABELS.get(action_name, 'تفاعل مع البوت')
        detail = ': <b>' + esc(name(pid, cid)[:100]) + '</b>' if pid else ''
        entry = f'\n{label}{detail}\nالعميل: {customer_link(user_id)} • {esc(created)}'
        if len(('\n'.join(parts) + entry).encode('utf-16-le')) // 2 > 3500:
            break
        parts.append(entry)
    return '\n'.join(parts), rows[0][0] if rows else 0


def activity_keyboard():
    return kb([[btn('🔄 تحديث', 'admin:activity')], [btn('🗑 تصفير النشاط', 'admin:activity_reset')], [btn('↩️ لوحة الإدارة', 'admin')]])


def admin_activity(api, cid):
    if cid != G['ADMIN_ID']:
        return home(api, cid)
    text, latest = activity_page(cid)
    result = send(api, cid, text, activity_keyboard())
    ACTIVITY_VIEW.clear()
    if isinstance(result, dict) and result.get('message_id'):
        ACTIVITY_VIEW.update(cid=cid, message_id=result['message_id'], latest=latest,
                             expires=time.monotonic() + 900)


def tick_customer_activity(api):
    if not ACTIVITY_VIEW:
        return
    if time.monotonic() >= ACTIVITY_VIEW['expires']:
        ACTIVITY_VIEW.clear()
        return
    cid = ACTIVITY_VIEW['cid']
    with db() as conn:
        latest = conn.execute('SELECT COALESCE(MAX(id),0) FROM activity').fetchone()[0]
    if latest == ACTIVITY_VIEW['latest']:
        return
    text, latest = activity_page(cid)
    result = api.call('editMessageText', chat_id=cid, message_id=ACTIVITY_VIEW['message_id'],
                      text=text, parse_mode='HTML', reply_markup=activity_keyboard())
    if result:
        ACTIVITY_VIEW['latest'] = latest
    else:
        # Deleted/inaccessible messages must not cause an endless retry loop.
        ACTIVITY_VIEW.clear()


def admin_icons(api, cid):
    if cid != G['ADMIN_ID']:
        return home(api, cid)
    send(api, cid, '➕ <b>إضافة أيقونة متحركة</b>\n\nاختر أين تريد إضافة الأيقونة:',
         kb([[btn('📦 أسماء المنتجات', 'iconmenu:products', style='primary')],
             [btn('📁 أسماء الأقسام', 'iconmenu:categories')],
             [btn('🏠 أيقونات أزرار الرئيسية', 'iconmenu:buttons', style='primary')],
             [btn('↩️ لوحة الإدارة', 'admin')]]))


def admin_icon_products(api, cid):
    if cid != G['ADMIN_ID']:
        return home(api, cid)
    buttons = []
    seen = set()

    # Every sellable catalog product/variant.
    for pid in VARIANTS:
        if pid in seen:
            continue
        seen.add(pid)
        buttons.append(btn(name(pid, cid), 'seticon:' + pid, ui_icon(pid)))

    # Products created from the admin panel.
    with db() as conn:
        custom_products = conn.execute('SELECT pid,name FROM admin_products ORDER BY rowid').fetchall()
    for pid, product_name in custom_products:
        if pid in seen:
            continue
        seen.add(pid)
        buttons.append(btn(product_name, 'seticon:' + pid, ui_icon(pid)))

    # Standalone built-in products that are not represented by variants.
    for pid, product in G['PRODUCTS'].items():
        if pid in seen:
            continue
        try:
            children = products_in_category(pid)
        except Exception:
            children = [pid]
        if children == [pid]:
            seen.add(pid)
            buttons.append(btn('YouTube' if pid == 'youtube' else name(pid, cid),
                               'seticon:' + pid, ui_icon(pid) or product.get('custom_emoji_id')))

    rows = [[button] for button in buttons]
    send(api, cid, '📦 <b>أسماء المنتجات</b>\n\nاختر المنتج الذي تريد وضع أيقونة متحركة بجانب اسمه:',
         kb(rows + [[btn('↩️ رجوع', 'admin:icons')]]))


def admin_icon_categories(api, cid):
    if cid != G['ADMIN_ID']:
        return home(api, cid)
    buttons = []
    for pid, product in G['PRODUCTS'].items():
        buttons.append(btn('YouTube' if pid == 'youtube' else product['name'],
                           'seticon:' + pid, ui_icon(pid) or product.get('custom_emoji_id')))
    with db() as conn:
        custom_categories = conn.execute('SELECT cid,name FROM admin_categories ORDER BY rowid').fetchall()
    for category_id, category_name in custom_categories:
        buttons.append(btn(category_name, 'seticon:' + category_id, ui_icon(category_id)))
    rows = [[button] for button in buttons]
    send(api, cid, '📁 <b>أسماء الأقسام</b>\n\nاختر القسم الذي تريد إضافة أيقونة متحركة له:',
         kb(rows + [[btn('↩️ رجوع', 'admin:icons')]]))


def admin_icon_buttons(api, cid):
    if cid != G['ADMIN_ID']:
        return home(api, cid)
    buttons = [btn(label, 'seticon:' + key, ui_icon(key)) for key, label in UI_ICON_LABELS.items()]
    for row in payment_methods.methods(sys.modules[__name__]):
        buttons.append(btn(row[1], 'seticon:pay_custom_' + str(row[0]), ui_icon('pay_custom_' + str(row[0]))))
    rows = [[button] for button in buttons]
    send(api, cid, '🔘 <b>أزرار المتجر</b>\n\nاختر الزر الذي تريد إضافة أيقونة متحركة له:',
         kb(rows + [[btn('↩️ رجوع', 'admin:icons')]]))

def begin_icon_setup(api, cid, pid):
    if cid != G['ADMIN_ID']:
        return home(api, cid)
    custom_cat = custom_category(pid)
    cp = custom_product(pid)
    is_variant = pid in VARIANTS
    dynamic_payment = pid.startswith('pay_custom_') and pid[11:].isdigit()
    if pid not in G['PRODUCTS'] and pid not in UI_ICON_LABELS and not custom_cat and not cp and not is_variant and not dynamic_payment:
        return admin_icons(api, cid)
    with db() as conn:
        conn.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)', (cid, 'icon', pid))
    if is_variant:
        label = name(pid, cid)
    elif pid in G['PRODUCTS']:
        label = G['PRODUCTS'][pid]['name']
    elif pid in UI_ICON_LABELS:
        label = UI_ICON_LABELS[pid]
    elif custom_cat:
        label = custom_cat[1]
    elif dynamic_payment:
        row = payment_methods.get(sys.modules[__name__], pid[11:])
        label = row[1] if row else pid
    else:
        label = cp[1]
    send(api, cid, f'أرسل الآن الأيقونة المتحركة الخاصة بـ <b>{esc(label)}</b>.\n\nأرسل رمزًا مخصصًا واحدًا فقط، أو اضغط إلغاء.',
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
    if pid in G['PRODUCTS']:
        G['PRODUCTS'][pid]['custom_emoji_id'] = str(emoji)
    G['CONFIG']['custom_icons_enabled'] = True
    custom_cat = custom_category(pid)
    cp = custom_product(pid)
    if pid in G['PRODUCTS']:
        label = G['PRODUCTS'][pid]['name']
    elif pid in UI_ICON_LABELS:
        label = UI_ICON_LABELS[pid]
    elif custom_cat:
        label = custom_cat[1]
    elif cp:
        label = cp[1]
    else:
        label = pid
    send(api, cid, f'✅ تم حفظ الأيقونة لـ <b>{esc(label)}</b>.',
         kb([[btn('➕ إضافة أيقونة أخرى', 'admin:icons')], [btn('🛍 معاينة المنتجات', 'products')]]))
    return True



def broadcast_product_alert(api, pid, kind='new'):
    """Broadcast a product alert with only a green direct-purchase button."""
    try:
        users = [int(user_id) for user_id in json.loads(USERS_PATH.read_text(encoding='utf-8'))]
    except Exception:
        users = []
    for user_id in users:
        try:
            cp = custom_product(pid)
            heading = '✨ <b>NEW PRODUCT — VEXA STORE</b>' if kind == 'new' else '🔥 <b>BACK IN STOCK — VEXA STORE</b>'
            if cp:
                _, product_name, description, price_usd, available, category_id, stock = cp
                text = heading + '\\n\\n<b>' + esc(product_name) + '</b>'
                if description:
                    text += '\\n\\n' + esc(description)
                text += '\\n\\n💵 <b>$' + esc(price_usd) + '</b>'
                if kind == 'stock':
                    text += '\\n📦 ' + esc(str(stock)) + ' available'
            else:
                text = heading + '\\n\\n<b>' + esc(name(pid, user_id)) + '</b>\\n\\n💵 ' + price(user_id, pid, 'USD')
            send(api, user_id, text, kb([[btn('Buy Now 🛒', 'item:' + pid, style='success')]]))
            time.sleep(0.04)
        except Exception:
            pass


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
            description = product_description(pid, cid)
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


def custom_category(category_id):
    with db() as conn:
        return conn.execute('SELECT cid,name FROM admin_categories WHERE cid=?', (category_id,)).fetchone()


def custom_product(pid):
    with db() as conn:
        return conn.execute('SELECT pid,name,description,price_usd,available,category_id,stock FROM admin_products WHERE pid=?', (pid,)).fetchone()


def name(pid, cid=0):
    pid = LEGACY.get(pid, pid)
    if pid == 'youtube':
        return text_override(pid, 'name', prefs(cid)[0], 'YouTube')
    if pid in VARIANTS:
        return text_override(pid, 'name', prefs(cid)[0], VARIANTS[pid]['name'][prefs(cid)[0]])
    category = custom_category(pid)
    if category:
        return text_override(pid, 'name', prefs(cid)[0], category[1])
    cp = custom_product(pid)
    if cp:
        return text_override(pid, 'name', prefs(cid)[0], cp[1])
    return text_override(pid, 'name', prefs(cid)[0], G['PRODUCTS'].get(pid, {}).get('name', pid))


def reset_navigation_state(cid):
    """Exit any unfinished input/payment flow when the user explicitly starts over or goes home."""
    with db() as conn:
        conn.execute('DELETE FROM discount_input WHERE cid=?', (cid,))
        conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
        conn.execute('DELETE FROM custom_topup_state WHERE cid=?', (cid,))
        conn.execute('DELETE FROM receipts WHERE cid=?', (cid,))
        conn.execute('UPDATE wallet_topups SET status="cancelled" WHERE cid=? AND status="receipt_pending"', (cid,))


def compact_stock(value):
    """Stable stock segment for Telegram RTL/LTR buttons: 📦 4."""
    try:
        value = int(value)
    except Exception:
        value = 0
    return '\u2066📦 ' + str(value) + '\u2069'


def compact_name(pid, cid=0):
    """Short button label for every product; full name stays inside product details."""
    pid = LEGACY.get(pid, pid)
    language = prefs(cid)[0]

    ar = {
        'pd_01':'Pro X5 • شهر',
        'pd_02':'Plus • شهر تجديد',
        'pd_03':'Go • شهر',
        'pd_04':'Plus • Apple Pay • شهر',
        'pd_05':'CODEX 500M • 6 أيام',
        'pd_06':'CODEX 100M • 3 أيام',
        'pd_07':'CODEX 50M • يومان',
        'pd_08':'CODEX 10M • يوم',
        'pd_09':'Pro • شهر تجديد',
        'pd_10':'API 500M • 6 أيام',
        'pd_11':'API 100M • 3 أيام',
        'pd_12':'API 50M • يومان',
        'pd_13':'API 10M • يوم',
        'pd_14':'Pro فردي • 6 أشهر',
        'pd_15':'Pro • شهر + 1600',
        'pd_16':'Pro • شهر',
        'pd_17':'7 أيام',
        'pd_18':'SuperGrok • 3 أشهر',
        'pd_19':'X Premium+ • شهر',
        'pd_20':'Heavy • شهر',
        'pd_21':'SuperGrok • 7 أيام',
        'pd_22':'18 شهر',
        'pd_23':'Edu • 500',
        'pd_24':'Premium 4K • Full Acc • 1M',
        'iptv_1m':'شهر',
        'iptv_3m':'3 أشهر',
        'iptv_6m':'6 أشهر',
        'iptv_1y':'سنة',
    }
    en = {
        'pd_01':'Pro X5 • 1M',
        'pd_02':'Plus • Recharge 1M',
        'pd_03':'Go • 1M',
        'pd_04':'Plus • Apple Pay • 1M',
        'pd_05':'CODEX 500M • 6D',
        'pd_06':'CODEX 100M • 3D',
        'pd_07':'CODEX 50M • 2D',
        'pd_08':'CODEX 10M • 1D',
        'pd_09':'Pro • Recharge 1M',
        'pd_10':'API 500M • 6D',
        'pd_11':'API 100M • 3D',
        'pd_12':'API 50M • 2D',
        'pd_13':'API 10M • 1D',
        'pd_14':'Pro Individual • 6M',
        'pd_15':'Pro • 1M + 1600',
        'pd_16':'Pro • 1M',
        'pd_17':'7D',
        'pd_18':'SuperGrok • 3M',
        'pd_19':'X Premium+ • 1M',
        'pd_20':'Heavy • 1M',
        'pd_21':'SuperGrok • 7D',
        'pd_22':'18M',
        'pd_23':'Edu • 500',
        'pd_24':'Premium 4K • Full Acc • 1M',
        'iptv_1m':'1M',
        'iptv_3m':'3M',
        'iptv_6m':'6M',
        'iptv_1y':'1Y',
    }
    full = name(pid, cid)
    label = full.strip()
    low_full = full.lower().replace('-', ' ').replace('_', ' ')

    # Always prefer the current edited product name over old catalog presets.
    if 'apple pay' in low_full or 'applepay' in low_full:
        if language == 'ar':
            return 'Apple Pay • شهر خاص' if ('خاص' in full or 'private' in low_full) else 'Apple Pay • شهر'
        return 'Apple Pay • Private 1M' if ('خاص' in full or 'private' in low_full) else 'Apple Pay • 1M'
    if 'upi' in low_full or 'ubi' in low_full:
        tag = 'UPI' if 'upi' in low_full else 'UBI'
        if language == 'ar':
            return tag + ' • شهر واحد'
        return tag + ' • 1M'

    # Keep Crunchyroll product buttons LTR and compact so the visual order is
    # always: product name | USD price | stock, even in the Arabic interface.
    if 'ملف خاص' in full or ('private' in low_full and 'profile' in low_full):
        return 'Private Profile • 1M'
    if ('حساب كامل' in full and ('7 أيام' in full or '7 ايام' in full)) or ('full' in low_full and '7' in low_full):
        return 'Full Acc • 7D'
    if ('حساب كامل' in full and ('شهر' in full or '1m' in low_full)) or ('full acc' in low_full and '1m' in low_full):
        return 'Full Acc • 1M'

    # YouTube names are often long; keep the plan type visible so price/stock
    # never get pushed off the Telegram button.
    if 'youtube' in low_full:
        if 'family invitation' in low_full or 'دعوة' in full or 'عائل' in full:
            return 'دعوة عائلية • شهر' if language == 'ar' else 'Family Invite • 1M'
        if 'private full account' in low_full or 'full account' in low_full or 'حساب كامل' in full or 'حساب خاص' in full:
            return 'حساب خاص • شهر' if language == 'ar' else 'Private • 1M'
        if 'premium' in low_full:
            return 'YouTube Premium • شهر' if language == 'ar' else 'YouTube Premium • 1M'

    preset = (en if language == 'en' else ar).get(pid)
    if preset:
        return preset
    category_id = VARIANTS.get(pid, {}).get('category')
    cp = custom_product(pid)
    if not category_id and cp:
        category_id = cp[5]

    # Remove a repeated category/brand prefix from custom and future products.
    if category_id:
        try:
            category_label = name(category_id, cid).strip()
            if category_label and label.lower().startswith(category_label.lower()):
                label = label[len(category_label):].lstrip(' -—|•:').strip()
        except Exception:
            pass

    replacements = (
        [('لمدة ', ''), ('شهر واحد', 'شهر'), ('تجديد رسمي', 'تجديد'),
         ('رمز تفعيل', 'كود'), ('حساب خاص', 'خاص'), ('حساب فردي', 'فردي'),
         ('اشتراك ', ''), ('بضمان كامل', 'ضمان كامل')]
        if language == 'ar' else
        [(' official recharge', ' Recharge'), ('Official Recharge', 'Recharge'),
         ('1 Month', '1M'), ('1 month', '1M'), ('6 Months', '6M'),
         ('3 Months', '3M'), ('7 Days', '7D'), ('Individual Account', 'Individual')]
    )
    for old, new in replacements:
        label = label.replace(old, new)
    label = ' '.join(label.split()).strip(' -—|•:')
    label = label or full
    # Telegram renders inline-button text on one line. Limit only the product
    # name segment so the USD price and stock segment remain fully visible.
    if len(label) > 22:
        label = label[:21].rstrip(' -—|•:') + '…'
    return label


def product_button_icon(pid, cid=0):
    """Return the product button custom icon, except products that should have no leading icon."""
    try:
        low = name(pid, cid).lower().replace('-', ' ').replace('_', ' ')
        if 'upi' in low:
            return None
    except Exception:
        pass
    return ui_icon(pid)


def start(api, cid):
    reset_navigation_state(cid)
    send(api, cid,
         '🌐 <b>اختر لغتك | Choose your language</b>',
         kb([[btn('🇸🇦 العربية', 'setlang:ar'),
              btn('🇺🇸 English', 'setlang:en')]]))


def home(api, cid):
    balance_sar = wallet_balance(cid)
    balance_usd = (balance_sar / RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    with db() as conn:
        purchases = conn.execute('SELECT COUNT(*) FROM orders WHERE cid=? AND status="paid"', (cid,)).fetchone()[0]
    text = tr(cid, f'👋 <b>أهلاً بك في VEXA STORE!</b>\n\n🆔 رقم العضوية: <code>{cid}</code>\n👤 حسابك: <a href="tg://user?id={cid}">فتح الحساب</a>\n💳 الرصيد: <b>${balance_usd:.2f}</b>\n🛍 المشتريات: <b>{purchases}</b>\n\nاختر من القائمة أدناه:', f'👋 <b>Welcome to VEXA STORE!</b>\n\n🆔 Member ID: <code>{cid}</code>\n👤 Account: <a href="tg://user?id={cid}">Open profile</a>\n💳 Balance: <b>${balance_usd:.2f}</b>\n🛍 Purchases: <b>{purchases}</b>\n\nChoose from the menu below:')
    rows = [[btn(tr(cid,'المنتجات','Products'),'products',ui_icon('ui_products'),style='primary'), btn(tr(cid,'شحن الرصيد','Top up'),'wallet:topup',ui_icon('ui_topup'),style='primary')], [btn(tr(cid,'الإحالات','Referrals'),'referrals',ui_icon('ui_referrals'),style='primary'), btn(tr(cid,'حسابي','My account'),'wallet',ui_icon('ui_account'),style='primary')], [btn(tr(cid,'تواصل مع الدعم','Contact support'),'support',ui_icon('ui_support'),style='primary'), btn(tr(cid,'إبلاغ عن مشكلة','Report issue'),'support',ui_icon('ui_report'),style='primary')], [btn('Language / اللغة','settings:lang',ui_icon('ui_language'),style='primary')]]
    rows.append([btn(tr(cid, '📢 مجتمع VEXA STORE', '📢 VEXA STORE Community'), 'community', ui_icon('ui_community'), style='primary')])
    if cid == G.get('ADMIN_ID'):
        rows.append([btn('لوحة الطلبات', 'admin', ui_icon('ui_admin'), style='primary')])
    send(api, cid, text, kb(rows))

def products(api, cid):
    buttons = [btn(category_label(pid, cid), 'product:' + pid, ui_icon(pid) or p.get('custom_emoji_id')) for pid, p in G['PRODUCTS'].items() if category_visible(pid)]
    with db() as conn:
        custom_categories = conn.execute('SELECT cid,name FROM admin_categories ORDER BY rowid').fetchall()
    buttons += [btn(name(category_id, cid), 'product:' + category_id, ui_icon(category_id)) for category_id, category_name in custom_categories if category_visible(category_id)]
    rows = [buttons[i:i+3] for i in range(0, len(buttons), 3)]
    rows += [[btn(tr(cid, '🏠 الرئيسية', '🏠 Home'), 'home')]]
    send(api, cid, tr(cid, '🛍 <b>المنتجات</b>\nاختر الخدمة:', '🛍 <b>Products</b>\nChoose a service:'), kb(rows))


def settings(api, cid, kind):
    if kind == 'lang':
        rows = [[btn('العربية', 'setlang:ar'), btn('English', 'setlang:en')]]
        text = '🌐 اختر اللغة / Choose language'
    else:
        rows = [[btn('🇺🇸 USD — US Dollar', 'setcurrency:USD')]]
        text = '💱 أسعار المنتجات ثابتة بالدولار USD\nيظهر التحويل للريال السعودي عند الدفع فقط.'
    send(api, cid, text, kb(rows + [nav(cid)]))


def card(api, cid, image_path, title, text, keyboard, pid=None):
    """Separate photo and full text so Telegram's caption limit never drops terms."""
    override = saved_product_photo(pid) if pid else None
    if override is not None:
        image_path = None
        if override:
            api.call('sendPhoto', chat_id=cid, photo=override, caption=title[:900])
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
    # Product text contains safe HTML markup for bold/custom emoji.
    # Send it directly so Telegram parses the custom emoji entity.
    send(api, cid, text, keyboard)



def product_visible(pid):
    with db() as conn:
        row = conn.execute('SELECT visible FROM product_visibility WHERE pid=?', (pid,)).fetchone()
    if row is not None:
        return bool(row[0])
    # Default ChatGPT catalogue requested by the store owner.
    if VARIANTS.get(pid, {}).get('category') == 'chatgpt':
        return pid in ('pd_01', 'pd_04', 'pd_05')
    return True


def category_visible(pid):
    with db() as conn:
        ids = [row[0] for row in conn.execute('SELECT pid FROM admin_products WHERE category_id=?', (pid,))]
    if pid in G['PRODUCTS']:
        variants = [v['id'] for v in VARIANTS.values() if v['category'] == pid]
        ids += variants or [pid]
    return any(map(product_visible, ids))


def visibility_categories(api, cid):
    if cid != G['ADMIN_ID']:
        return
    with db() as conn:
        custom = conn.execute('SELECT cid,name FROM admin_categories ORDER BY rowid').fetchall()
    categories = [(pid, p['name']) for pid, p in G['PRODUCTS'].items()] + custom
    rows = [[btn(name, 'viscat:' + pid)] for pid, name in categories]
    send(api, cid, '👁 <b>إظهار وإخفاء المنتجات</b>\n\nاختر القسم:', kb(rows + [[btn('↩️ لوحة الإدارة', 'admin')]]))


def category_product_ids(category_id):
    """Return original and added products, including hidden/out-of-stock items."""
    with db() as conn:
        custom_ids = [row[0] for row in conn.execute(
            'SELECT pid FROM admin_products WHERE category_id=? ORDER BY rowid',
            (category_id,))]
    originals = [pid for pid, v in VARIANTS.items() if v['category'] == category_id]
    if not originals and category_id in G['PRODUCTS']:
        originals = [category_id]
    return list(dict.fromkeys(originals + custom_ids))


def visibility_products(api, cid, category_id):
    if cid != G['ADMIN_ID']:
        return
    if category_id not in G['PRODUCTS'] and not custom_category(category_id):
        return visibility_categories(api, cid)
    ids = category_product_ids(category_id)
    rows = [[btn(('👁 ' if product_visible(pid) else '🙈 ') + name(pid, cid), 'vistoggle:' + pid)] for pid in ids]
    send(api, cid, '👁 ظاهر في المتجر | 🙈 مخفي من المتجر\nالمنتج المخفي يبقى هنا حتى تستطيع إظهاره مجددًا.',
         kb(rows + [[btn('↩️ الأقسام', 'admin:visibility')]]))


def toggle_visibility(api, cid, pid):
    if cid != G['ADMIN_ID']:
        return
    cp = custom_product(pid)
    if pid not in VARIANTS and pid not in G['PRODUCTS'] and not cp:
        return visibility_categories(api, cid)
    category_id = VARIANTS[pid]['category'] if pid in VARIANTS else (cp[5] if cp else pid)
    with db() as conn:
        conn.execute('INSERT OR REPLACE INTO product_visibility(pid,visible) VALUES (?,?)',
                     (pid, 0 if product_visible(pid) else 1))
    visibility_products(api, cid, category_id)


def chatgpt_visibility_admin(api, cid):
    if cid != G['ADMIN_ID']:
        return home(api, cid)
    choices = [v for v in VARIANTS.values() if v.get('category') == 'chatgpt']
    rows = []
    for v in choices:
        visible = product_visible(v['id'])
        rows.append([btn(('👁 ' if visible else '🙈 ') + name(v['id'], cid),
                         'chatgptvis:' + v['id'],
                         style='success' if visible else 'danger')])
    rows.append([btn('↩️ لوحة الإدارة', 'admin')])
    send(api, cid, '🤖 <b>إظهار وإخفاء منتجات ChatGPT</b>\n\n👁 ظاهر للعملاء\n🙈 مخفي عن العملاء\n\nاضغط على المنتج لتغيير حالته.', kb(rows))


def toggle_chatgpt_visibility(api, cid, pid):
    if cid != G['ADMIN_ID'] or VARIANTS.get(pid, {}).get('category') != 'chatgpt':
        return
    new_value = 0 if product_visible(pid) else 1
    with db() as conn:
        conn.execute('INSERT OR REPLACE INTO product_visibility(pid,visible) VALUES (?,?)', (pid, new_value))
    chatgpt_visibility_admin(api, cid)


def chatgpt_cards(api, cid, choices):
    """Show ChatGPT products using the same card layout as Grok."""
    return grok_cards(api, cid, [v for v in choices if product_visible(v['id'])])


def grok_cards(api, cid, choices, show_heading=True, default_image="assets/grok.png"):
    """Compact photo cards for Grok only; prices share the checkout source."""
    if show_heading:
        send(api, cid, tr(cid, '✦ <b>اشتراكات Grok</b>\nاختر الباقة المناسبة لك:', '✦ <b>Grok subscriptions</b>\nChoose your plan:'))
    for v in choices:
        pid = v['id']
        available = can_order(pid)
        if v.get('review_required'):
            status = tr(cid, '⏸ قيد المراجعة — الطلب غير متاح', '⏸ Under review — ordering unavailable')
        elif not in_stock(pid):
            status = tr(cid, '🔴 نفدت الكمية', '🔴 Out of stock')
        else:
            status = tr(cid, '🟢 متوفر', '🟢 Available')
        caption = '<b>' + esc(name(pid, cid)) + '</b>\n\n'
        caption += '💰 <b>' + price(cid, pid, 'USD') + '</b>\n\n' + status
        if v.get('manual_delivery'):
            caption += '\n' + tr(cid, '✉️ يتم إرسال بيانات المنتج بعد تأكيد الدفع', '✉️ Product details are sent after payment confirmation')
        details = btn(tr(cid, '📋 التفاصيل', '📋 Details'), 'item:' + pid)
        rows = [[btn(tr(cid, '🛒 شراء الآن', '🛒 Buy now'), 'buy:' + pid, style='success'), details]] if available else [[btn(tr(cid, '🔴 غير متوفر', '🔴 Unavailable'), 'item:' + pid, style='danger')]]
        if not available:
            rows[0][0]['style'] = 'danger'
        rows.append([btn(tr(cid, '⚡ VEXA VOLT', '⚡ VEXA VOLT'), 'support')])
        markup = kb(rows)
        override = saved_product_photo(pid)
        if override is not None:
            if not override or not api.call('sendPhoto', chat_id=cid, photo=override, caption=caption, parse_mode='HTML', reply_markup=markup):
                send(api, cid, caption, markup)
            continue
        path = (BASE / (v.get('image') or default_image)).resolve()
        delivered = False
        if path.is_relative_to(BASE) and path.is_file():
            boundary = 'VEXA' + uuid.uuid4().hex
            fields = {'chat_id': str(cid), 'caption': caption, 'parse_mode': 'HTML',
                      'reply_markup': json.dumps(markup, ensure_ascii=False)}
            body = b''
            for key, value in fields.items():
                body += f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
            body += f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="{path.name}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
            body += path.read_bytes() + f'\r\n--{boundary}--\r\n'.encode()
            try:
                api_url = getattr(api, 'base_url', None) or getattr(api, 'u', None)
                req = urllib.request.Request(api_url + 'sendPhoto', body, {'Content-Type': f'multipart/form-data; boundary={boundary}'})
                with urllib.request.urlopen(req, timeout=40) as response:
                    delivered = bool(json.load(response).get('ok'))
            except Exception as exc:
                print('Grok card image failed:', type(exc).__name__)
        if not delivered:
            send(api, cid, caption, markup)
    send(api, cid, tr(cid, 'تصفح أقسام المتجر:', 'Browse store categories:'),
         kb([[btn(tr(cid, '↩️ الأقسام', '↩️ Categories'), 'products'),
              btn(tr(cid, '🏠 الرئيسية', '🏠 Home'), 'home')]]))


def category(api, cid, pid):
    if not category_visible(pid):
        return products(api, cid)
    p = G['PRODUCTS'].get(pid)
    if not p:
        custom_cat = custom_category(pid)
        if not custom_cat:
            products(api, cid)
            return
        with db() as conn:
            choices = conn.execute('SELECT pid,name,price_usd,available,stock FROM admin_products WHERE category_id=? ORDER BY rowid', (pid,)).fetchall()
        rows = []
        for product_id, product_name, price_usd, available, stock in choices:
            if not product_visible(product_id):
                continue
            sold_out = not available or int(stock or 0) <= 0
            qty = int(stock or 0)
            label = compact_name(product_id, cid) + ' | 💵 ' + price(cid, product_id, 'USD') + ' | ' + compact_stock(qty)
            if sold_out: label = '🔴 ' + label
            rows.append([btn(label, 'item:' + product_id, product_button_icon(product_id, cid), style='danger' if sold_out else 'success')])
        send(api, cid, category_heading(pid, cid), kb(rows + [nav(cid)]))
        return
    if pid == 'youtube':
        with db() as conn:
            yt_rows = conn.execute('''SELECT DISTINCT p.pid,p.name,p.price_usd,p.available,p.stock
                FROM admin_products p
                LEFT JOIN admin_categories c ON c.cid=p.category_id
                WHERE p.category_id='youtube'
                   OR lower(p.name) LIKE '%youtube%'
                   OR p.name LIKE '%يوتيوب%'
                   OR lower(COALESCE(c.name,'')) LIKE '%youtube%'
                   OR COALESCE(c.name,'') LIKE '%يوتيوب%'
                ORDER BY p.rowid''').fetchall()
        if yt_rows:
            rows = []
            for product_id, product_name, price_usd, available, stock in yt_rows:
                if not product_visible(product_id):
                    continue
                qty = int(stock or 0)
                sold_out = (not bool(available)) or qty <= 0
                label = ('🔴 ' if sold_out else '🟢 ') + compact_name(product_id, cid) + ' | 💵 ' + price(cid, product_id, 'USD') + ' | ' + compact_stock(qty)
                rows.append([btn(label, 'item:' + product_id, product_button_icon(product_id, cid),
                                 style='danger' if sold_out else 'success')])
            if rows:
                send(api, cid, category_heading(pid, cid), kb(rows + [nav(cid)]))
                return
    choices = [v for v in VARIANTS.values() if v['category'] == pid and product_visible(v['id'])]
    if pid == 'chatgpt' and choices:
        return chatgpt_cards(api, cid, choices)
    if choices:
        rows = []
        for v in choices:
            sold_out = not in_stock(v['id'])
            status = '⏸ ' if v.get('review_required') else (tr(cid, '🔴 نفد | ', '🔴 SOLD OUT | ') if sold_out else '')
            if pid == 'chatgpt':
                # Keep the price at the beginning so Telegram cannot hide it
                # when a long product name is truncated on mobile.
                label = status + '💰 ' + price(cid, v['id']) + ' • ' + name(v['id'], cid)
            else:
                label = status + name(v['id'], cid) + ' | ' + price(cid, v['id'])
            variant_icon = product_button_icon(v['id'], cid)
            rows.append([btn(label, 'item:' + v['id'], p.get('custom_emoji_id') if variant_icon is None else variant_icon, style='danger' if sold_out else 'success')])
        send(api, cid, category_heading(pid, cid), kb(rows + [nav(cid)]))
        return
    english = {'youtube': 'YouTube Premium for one month. Ad-free viewing, background playback, offline downloads and YouTube Music Premium benefits.',
               'netflix': 'Netflix subscription for movies, series and entertainment.', 'iptv': 'IPTV subscriptions for compatible devices.'}
    description = product_description(pid, cid)
    text = esc(name(pid, cid)) + '\n\n' + esc(price(cid, pid)) + '\n\n' + esc(tr(cid, '✅ متوفر' if in_stock(pid) else '🔴 نفدت الكمية', '✅ Available' if in_stock(pid) else '🔴 Out of stock')) + '\n\n' + esc(description)
    rows = [[btn(tr(cid, '🛒 طلب المنتج', '🛒 Order'), 'buy:' + pid)]] if can_order(pid) else []
    rows += [[btn(tr(cid, '⚡ VEXA VOLT', '⚡ VEXA VOLT'), 'support')], nav(cid)]
    card(api, cid, f'assets/{pid}.png', name(pid, cid), text, kb(rows), pid=pid)


def item(api, cid, pid):
    pid = LEGACY.get(pid, pid)
    if not product_visible(pid):
        return send(api, cid, tr(cid, 'هذا المنتج مخفي حاليًا.', 'This product is currently hidden.'), kb([nav(cid, 'products')]))
    v = VARIANTS.get(pid)
    if not v:
        cp = custom_product(pid)
        if not cp:
            products(api, cid)
            return
        _, product_name, description, price_usd, available, category_id, stock = cp
        status = tr(cid, '✅ متوفر', '✅ Available') if available and int(stock or 0) > 0 else tr(cid, '🔴 نفدت الكمية', '🔴 Out of stock')
        text = esc(name(pid, cid)) + '\n\n' + info_block(pid,cid) + '\n\n' + esc(status) + '\n\n' + esc(product_description(pid, cid))
        rows = [[btn(tr(cid, '🛒 طلب المنتج', '🛒 Order'), 'buy:' + pid)]] if can_order(pid) else []
        rows += [[btn(tr(cid, '⚡ VEXA VOLT', '⚡ VEXA VOLT'), 'support')], nav(cid, 'product:' + category_id)]
        card(api, cid, None, name(pid, cid), text, kb(rows), pid=pid)
        return
    lang = prefs(cid)[0]
    available = ''
    if not in_stock(pid):
        available = tr(cid, '🚫 نفد لدى المورد وقت المراجعة. الطلب غير متاح حاليًا.', '🚫 Out of stock at the last supplier check. Ordering is currently unavailable.')
    text = esc(name(pid, cid)) + '\n\n' + info_block(pid,cid) + (('\n\n' + esc(available)) if available else '') + '\n\n' + esc(product_description(pid, cid))
    if v.get('promotions'):
        text += '\n\n' + esc(tr(cid, 'أسعار الكميات — تواصل مع الدعم:', 'Bulk prices — contact support:'))
        for tier in v['promotions']:
            text += '\n' + esc(tier['min_quantity']) + '+: ' + esc(price(cid, pid, source_price=tier['source_usd'])) + esc(tr(cid, ' لكل قطعة', ' per unit'))
    if v.get('review_required'):
        text += '\n\n⚠️ ' + esc(v['review_required'][lang])
    rows = []
    if can_order(pid):
        order_label = tr(cid, '🛒 طلب قطعة واحدة', '🛒 Order one item')
        if v.get('category') == 'chatgpt':
            order_label = tr(cid, '🛒 شراء الآن • ', '🛒 Buy now • ') + price(cid, pid)
        rows.append([btn(order_label, 'buy:' + pid, style='primary' if v.get('category') == 'chatgpt' else None)])
    rows += [[btn(tr(cid, '⚡ VEXA VOLT', '⚡ VEXA VOLT'), 'support')], nav(cid, 'product:' + v['category'])]
    card(api, cid, v.get('image'), name(pid, cid), text, kb(rows), pid=pid)


def can_order(pid):
    pid = LEGACY.get(pid, pid)
    if not product_visible(pid):
        return False
    if pid in VARIANTS:
        return in_stock(pid) and not VARIANTS[pid].get('review_required')
    if custom_product(pid):
        return in_stock(pid) and amount(pid) is not None
    return pid in G['PRODUCTS'] and in_stock(pid) and amount(pid) is not None


def back(pid):
    pid = LEGACY.get(pid, pid)
    return ('item:' if pid in VARIANTS or custom_product(pid) else 'product:') + pid


def checkout_totals(cid, pid):
    return discounts.totals(sys.modules[__name__], cid, pid)


def summary(cid, pid):
    qty = product_options.selected(sys.modules[__name__], cid, pid)
    sar, usd, discount, code = checkout_totals(cid, pid)
    text = esc(name(pid,cid)) + '\n💵 سعر الوحدة: ' + price(cid,pid,'USD') + f'\n🛍 الكمية: {qty}'
    if code:
        discount_usd = (Decimal(str(discount)) / RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        text += '\n🎟 ' + esc(code) + f' — الخصم: {discount_usd:.2f} USD / {discount:.2f} SAR'
    text += f'\n\n💲 <b>الإجمالي بالدولار:</b> {usd:.2f} USD'
    text += f'\n🇸🇦 <b>الإجمالي بالريال:</b> {sar:.2f} SAR'
    return text


def wallet(api, cid):
    sar = wallet_balance(cid).quantize(Decimal('0.01'))
    usd = (sar / RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    text = tr(cid, '<b>محفظة VEXA</b>\n\nرصيدك الحالي:', '<b>VEXA Wallet</b>\n\nYour current balance:')
    text += f'\n<b>{sar:.2f} {tr(cid, "ر.س", "SAR")}</b>\n<b>{usd:.2f} USD</b>'
    rows = [[btn(tr(cid, 'إضافة رصيد', 'Add funds'), 'wallet:topup', ui_icon('ui_wallet_add'), style='success'),
             btn(tr(cid, 'تحويل', 'Transfer'), 'wallet:transfer', ui_icon('ui_wallet_transfer'), style='primary')],
            [btn(tr(cid, 'الرجوع للقائمة', 'Back to Menu'), 'home', ui_icon('ui_wallet_back'), style='primary')]]
    send(api, cid, text, kb(rows))


def wallet_amounts(api, cid):
    rows = []
    for pair in ((20, 50), (100, 200)):
        row = []
        for value in pair:
            usd = (Decimal(value) / RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            row.append(btn(f'{value} {tr(cid, "ر.س", "SAR")} / {usd:.2f} USD', f'topup:{value}', style='success'))
        rows.append(row)
    text = tr(cid, 'اختر مبلغ إضافة الرصيد — جميع المبالغ معروضة بالريال والدولار:',
              'Choose an add-funds amount — all amounts are shown in SAR and USD:')
    rows.append([btn(tr(cid, 'مبلغ اختياري بالدولار', 'Custom amount in USD'), 'topupcustom', style='success')])
    rows.append([btn(tr(cid, 'رجوع', 'Back'), 'wallet', style='primary')])
    send(api, cid, text, kb(rows))


def wallet_method(api, cid, value):
    try:
        value = Decimal(value).quantize(Decimal('0.01'))
    except Exception:
        return wallet_amounts(api, cid)
    if value <= 0 or value > 5000:
        return wallet_amounts(api, cid)
    usd = (value / RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    text = tr(cid, 'اختر طريقة إضافة الرصيد:', 'Choose an add-funds method:') + f'\n\n<b>{value:.2f} SAR / {usd:.2f} USD</b>'
    rows = [
        [btn('Crypto Pay', f'topupcrypto:{value}', ui_icon('pay_cryptopay'), style='success')],
        [btn('Bybit / USDT', f'topupbybit:{value}', ui_icon('pay_bybit'), style='success')]
    ]
    for method in payment_methods.methods(sys.modules[__name__], True):
        rows.append([btn(method[1], f'topupmanual:{method[0]}:{value}', ui_icon('pay_custom_' + str(method[0])), style='success')])
    rows.append([btn(tr(cid, 'رجوع', 'Back'), 'wallet:topup', style='primary')])
    send(api, cid, text, kb(rows))


def wallet_transfer_begin(api, cid):
    with db() as conn:
        conn.execute('INSERT OR REPLACE INTO wallet_transfer_state(cid,step,recipient,amount_sar) VALUES (?,? ,NULL,NULL)', (cid, 'recipient'))
    send(api, cid, tr(cid, 'أرسل رقم ID للمستخدم المستلم داخل البوت.', 'Send the recipient user ID inside the bot.'),
         kb([[btn(tr(cid, 'إلغاء', 'Cancel'), 'wallet', style='primary')]]))


def wallet_manual_topup(api, cid, method_id, value):
    row = payment_methods.get(sys.modules[__name__], str(method_id))
    if not row:
        return wallet_method(api, cid, value)
    try:
        value = Decimal(value).quantize(Decimal('0.01'))
    except Exception:
        return wallet_amounts(api, cid)
    topup_id = uuid.uuid4().hex[:16]
    with db() as conn:
        conn.execute('INSERT INTO wallet_topups VALUES (?,?,?,?,?,?)', (topup_id, cid, str(value), 'manual_' + str(method_id), None, 'pending'))
    usd = (value / RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    details = payment_methods.details(sys.modules[__name__], dict(zip(('name','holder','account'), row[1:4])))
    text = details + f'\n\n<b>{value:.2f} SAR / {usd:.2f} USD</b>\n\n' + tr(cid, 'بعد التحويل اضغط تم التحويل ثم أرسل صورة الإثبات.', 'After paying, tap Payment sent, then send the receipt image.')
    send(api, cid, text, kb([[btn(tr(cid, 'تم التحويل', 'Payment sent'), 'topupreceipt:' + topup_id, style='success')],
                             [btn(tr(cid, 'رجوع', 'Back'), f'topup:{value}', style='primary')]]))


def wallet_transfer_confirm(api, cid, recipient, amount_sar):
    try:
        recipient = int(recipient)
        amount = Decimal(str(amount_sar)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except Exception:
        return wallet(api, cid)
    if recipient == cid or amount <= 0:
        return wallet(api, cid)
    transfer_id = uuid.uuid4().hex[:16]
    with db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('INSERT OR IGNORE INTO wallets(cid,balance_sar) VALUES (?,?)', (cid, '0'))
        conn.execute('INSERT OR IGNORE INTO wallets(cid,balance_sar) VALUES (?,?)', (recipient, '0'))
        current = Decimal(conn.execute('SELECT balance_sar FROM wallets WHERE cid=?', (cid,)).fetchone()[0])
        if current < amount:
            conn.rollback()
            return send(api, cid, tr(cid, 'رصيدك غير كافٍ لإتمام التحويل.', 'Your balance is insufficient for this transfer.'), kb([[btn(tr(cid, 'رجوع', 'Back'), 'wallet', style='primary')]]))
        sender_new = (current - amount).quantize(Decimal('0.01'))
        recipient_current = Decimal(conn.execute('SELECT balance_sar FROM wallets WHERE cid=?', (recipient,)).fetchone()[0])
        recipient_new = (recipient_current + amount).quantize(Decimal('0.01'))
        conn.execute('UPDATE wallets SET balance_sar=? WHERE cid=?', (str(sender_new), cid))
        conn.execute('UPDATE wallets SET balance_sar=? WHERE cid=?', (str(recipient_new), recipient))
        conn.execute('INSERT INTO wallet_transfers VALUES (?,?,?,?,?)', (transfer_id, cid, recipient, str(amount), now_saudi()))
        conn.execute('DELETE FROM wallet_transfer_state WHERE cid=?', (cid,))
        conn.commit()
    usd = (amount / RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    send(api, cid, tr(cid, 'تم تحويل الرصيد بنجاح.', 'Balance transferred successfully.') + f'\n<b>{amount:.2f} SAR / {usd:.2f} USD</b>', kb([[btn(tr(cid, 'الرجوع للمحفظة', 'Back to Wallet'), 'wallet', style='primary')]]))
    try:
        send(api, recipient, tr(recipient, 'تمت إضافة رصيد إلى محفظتك من مستخدم آخر.', 'Balance was added to your wallet by another user.') + f'\n<b>{amount:.2f} SAR / {usd:.2f} USD</b>', menu(recipient))
    except Exception:
        pass


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
         kb([[btn('Bybit Pay', 'topupsend:bybitid:' + topup_id, ui_icon('pay_bybitid'), style='success')],
             [btn('USDT • TRON (TRC20)', 'topupsend:trc20:' + topup_id, ui_icon('pay_trc20'), style='success')],
             [btn('USDT • BSC (BEP20)', 'topupsend:bep20:' + topup_id, ui_icon('pay_bep20'), style='success')],
             [btn(tr(cid, 'رجوع', 'Back'), f'topup:{value}', style='primary')]]))


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
    warning = tr(cid, 'يتم تنفيذ الطلب بعد مراجعة الدفع وتأكيد التوفر، ثم إرسال بيانات المنتج إليك.', 'Your order is fulfilled after payment review and availability confirmation, then the product details are sent to you.')
    send(api, cid, tr(cid, '💳 <b>اختر طريقة الدفع</b>\n\n', '💳 <b>Choose payment method</b>\n\n') + summary(cid, pid) + '\n\n' + warning,
         kb([[btn(tr(cid, '🎟 كود خصم', '🎟 Discount code'), 'coupon:' + pid, style='primary'), btn(tr(cid, 'إزالة الخصم', 'Remove discount'), 'couponremove:' + pid)],
             [btn(tr(cid, 'المحفظة', 'Wallet'), 'paywallet:' + pid, ui_icon('pay_wallet'))],
             [btn('Crypto Pay', 'paycrypto:' + pid, ui_icon('pay_cryptopay'))],
             [btn('USDT — Bybit', 'paybybit:' + pid, ui_icon('pay_bybit'))],
             [btn('⭐ نجوم تيليجرام', 'paystars:' + pid)],
             [btn('🎁 هدايا تيليجرام', 'paygifts:' + pid)]] + payment_methods.rows(sys.modules[__name__], pid) + [nav(cid, back(pid))]))


def payment(api, cid, pid, method):
    if not can_order(pid):
        payments(api, cid, pid)
        return
    if checkout_totals(cid, pid)[0] == 0:
        return pay_with_wallet(api, cid, pid)
    if method == 'bybit':
        send(api, cid, '🪙 <b>USDT — Bybit</b>\n\n' + summary(cid, pid),
             kb([[btn('Bybit Pay', 'bybitid:' + pid, ui_icon('pay_bybitid'))], [btn('USDT • TRON (TRC20)', 'trc20:' + pid, ui_icon('pay_trc20'))], [btn('USDT • BSC (BEP20)', 'bep20:' + pid, ui_icon('pay_bep20'))], nav(cid, 'buy:' + pid)]))
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
        sar, usd, _, _ = checkout_totals(cid, pid)
        with db() as conn:
            conn.execute('INSERT OR REPLACE INTO payment_quotes VALUES (?,?,?,?,?)', (cid, pid, method, str(usd), str(sar)))
        text += '\n\n' + tr(cid, 'بعد التحويل أرسل صورة الإثبات للمراجعة.', 'After transferring, submit a receipt photo for review.')
        rows.append([btn(tr(cid, '✅ تم التحويل', '✅ Payment sent'), f'receipt:{method}:{pid}')])
    else:
        text += '\n\n' + tr(cid, 'بيانات الدفع غير مكتملة. تواصل مع الدعم: ', 'Payment details are incomplete. Contact support: ') + SUPPORT
    send(api, cid, text, kb(rows + [nav(cid, 'buy:' + pid)]))


def pay_with_wallet(api, cid, pid):
    if not can_order(pid):
        return payments(api, cid, pid)
    cost, paid_usd, _, _ = checkout_totals(cid, pid)
    remaining = wallet_debit(cid, cost)
    if remaining is None:
        send(api, cid, tr(cid, 'رصيد المحفظة غير كافٍ.', 'Insufficient wallet balance.') +
             f'\n\n{tr(cid, "المطلوب", "Required")}: {cost:.2f} SAR\n{tr(cid, "الرصيد", "Balance")}: {wallet_balance(cid):.2f} SAR',
             kb([[btn(tr(cid, '➕ شحن المحفظة', '➕ Top up wallet'), 'wallet:topup')], nav(cid, 'buy:' + pid)]))
        return
    order_id = add_order(cid, pid, 'wallet', 'paid', usd=paid_usd, sar=cost)
    send(api, G['ADMIN_ID'], f'🛒 <b>طلب مدفوع من المحفظة #{order_id}</b>\n\n' + esc(name(pid, cid)) + f'\nالكمية: {product_options.snapshot(sys.modules[__name__], "order", order_id)}\nالسعر المدفوع: {paid_usd} USD\nالعميل: <code>{cid}</code>')
    if fulfill_paid_order(api, order_id):
        return
    send(api, cid, tr(cid, '✅ تم الدفع من المحفظة وإرسال الطلب للإدارة.', '✅ Paid from your wallet and the order was sent to administration.') + f'\n\n{tr(cid, "الرصيد المتبقي", "Remaining balance")}: {remaining:.2f} SAR', menu(cid))


def pay_with_crypto(api, cid, pid):
    if not can_order(pid):
        return payments(api, cid, pid)
    sar, usd, _, _ = checkout_totals(cid, pid)
    if usd == 0:
        return pay_with_wallet(api, cid, pid)
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
        row = conn.execute('SELECT pid,external_id,status,amount_usd FROM crypto_orders WHERE id=? AND cid=?', (order_id, cid)).fetchone()
    if not row:
        return products(api, cid)
    pid, invoice_id, status, paid_usd = row
    if status == 'paid':
        return send(api, cid, tr(cid, '✅ هذه الفاتورة مدفوعة وتم إرسال الطلب.', '✅ This invoice is paid and the order was sent.'), menu(cid))
    if not crypto_paid(invoice_id):
        return send(api, cid, tr(cid, 'لم يصل الدفع بعد. أكمل الفاتورة ثم أعد التحقق.', 'Payment has not arrived yet. Complete the invoice and check again.'),
                    kb([[btn(tr(cid, '🔄 تحقق مرة أخرى', '🔄 Check again'), 'checkorder:' + order_id)], nav(cid, 'buy:' + pid)]))
    with db() as conn:
        changed = conn.execute('UPDATE crypto_orders SET status="paid" WHERE id=? AND status="pending"', (order_id,)).rowcount
    if changed:
        saved_order_id = add_order(cid, pid, 'cryptopay', 'paid', usd=paid_usd, sar=(Decimal(paid_usd)*RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP), quantity=product_options.snapshot(sys.modules[__name__], 'crypto', order_id))
        send(api, G['ADMIN_ID'], f'💠 <b>طلب Crypto Pay مدفوع #{saved_order_id}</b>\n\n' + esc(name(pid, cid)) + f'\nالكمية: {product_options.snapshot(sys.modules[__name__], "order", saved_order_id)}\nالسعر المدفوع: {paid_usd} USD\nالعميل: <code>{cid}</code>')
        if fulfill_paid_order(api, saved_order_id):
            return
    send(api, cid, tr(cid, '✅ تم الدفع وإرسال الطلب للإدارة.', '✅ Payment received and the order was sent to administration.'), menu(cid))


def receipt_request(api, cid, pid, method):
    if not can_order(pid) or (method not in ('bank', 'bybitid', 'trc20', 'bep20') and not payment_methods.valid(sys.modules[__name__], method)):
        payments(api, cid, pid)
        return
    sar, usd, _, _ = checkout_totals(cid, pid)
    with db() as conn:
        quote = conn.execute('SELECT usd,sar FROM payment_quotes WHERE cid=? AND pid=? AND method=?', (cid, pid, method)).fetchone()
        if quote:
            usd, sar = quote
        conn.execute('INSERT OR REPLACE INTO receipts VALUES (?,?,?,?,?)', (cid, pid, method, str(usd), str(sar)))
    send(api, cid, tr(cid, '📸 أرسل صورة إثبات الدفع هنا. ستصل للإدارة للمراجعة.', '📸 Send your payment receipt photo here. It will be sent to the administrator for review.'), kb([[btn(tr(cid, '❌ إلغاء', '❌ Cancel'), 'cancel:' + pid)]]))


def receipt(api, message):
    if payment_methods.message(sys.modules[__name__], api, message):
        return True
    cid = message['chat']['id']
    if discounts.message(sys.modules[__name__], api, message):
        return True
    if handle_admin_photo(api, message):
        return True
    if handle_admin_text(api, message):
        return True
    if handle_admin_price(api, message):
        return True
    if handle_button_label(api, message):
        return True
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
                    with db() as conn:
                        conn.execute('INSERT INTO user_delivery_status(cid,departed,updated_at) VALUES (?,?,?) ON CONFLICT(cid) DO UPDATE SET departed=excluded.departed, updated_at=excluded.updated_at', (user_id, 0, now_saudi()))
                else:
                    failed += 1
                    with db() as conn:
                        conn.execute('INSERT INTO user_delivery_status(cid,departed,updated_at) VALUES (?,?,?) ON CONFLICT(cid) DO UPDATE SET departed=excluded.departed, updated_at=excluded.updated_at', (user_id, 1, now_saudi()))
            except Exception:
                failed += 1
                with db() as conn:
                    conn.execute('INSERT INTO user_delivery_status(cid,departed,updated_at) VALUES (?,?,?) ON CONFLICT(cid) DO UPDATE SET departed=excluded.departed, updated_at=excluded.updated_at', (user_id, 1, now_saudi()))
        with db() as conn:
            conn.execute('INSERT OR REPLACE INTO broadcast_stats(id,sent,failed,created_at) VALUES (1,?,?,?)', (ok, failed, now_saudi()))
        BROADCAST_PENDING.discard(cid)
        send(api, cid, f'✅ <b>تم الإرسال</b>\n\nوصلت الرسالة إلى: <b>{ok}</b>\nتعذر الإرسال إلى: <b>{failed}</b>',
             kb([[btn('↩️ لوحة الإدارة', 'admin')]]))
        return True
    with db() as conn:
        transfer_state = conn.execute('SELECT step,recipient,amount_sar FROM wallet_transfer_state WHERE cid=?', (cid,)).fetchone()
    if transfer_state:
        raw = (message.get('text') or '').strip()
        if raw.startswith('/'):
            with db() as conn:
                conn.execute('DELETE FROM wallet_transfer_state WHERE cid=?', (cid,))
            return False
        step, recipient, amount_sar = transfer_state
        if step == 'recipient':
            try:
                target = int(raw)
            except Exception:
                send(api, cid, tr(cid, 'أرسل رقم ID صحيح فقط.', 'Send a valid numeric user ID only.'))
                return True
            if target == cid:
                send(api, cid, tr(cid, 'لا يمكنك التحويل لنفس حسابك.', 'You cannot transfer to your own account.'))
                return True
            try:
                known_users = {int(x) for x in json.loads(USERS_PATH.read_text(encoding='utf-8'))}
            except Exception:
                known_users = set()
            if target not in known_users:
                send(api, cid, tr(cid, 'هذا المستخدم غير موجود داخل البوت.', 'This user is not registered in the bot.'))
                return True
            with db() as conn:
                conn.execute('UPDATE wallet_transfer_state SET step=?,recipient=? WHERE cid=?', ('amount', target, cid))
            send(api, cid, tr(cid, 'أرسل مبلغ التحويل بالدولار USD، مثال: 5', 'Send the transfer amount in USD, e.g. 5'))
            return True
        if step == 'amount':
            try:
                usd_value = Decimal(raw.replace(',', '.')).quantize(Decimal('0.01'))
            except Exception:
                send(api, cid, tr(cid, 'أرسل المبلغ كرقم فقط.', 'Send the amount as a number only.'))
                return True
            if usd_value <= 0:
                send(api, cid, tr(cid, 'المبلغ يجب أن يكون أكبر من صفر.', 'Amount must be greater than zero.'))
                return True
            sar_value = (usd_value * RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            if wallet_balance(cid) < sar_value:
                send(api, cid, tr(cid, 'رصيدك غير كافٍ.', 'Your balance is insufficient.'))
                return True
            with db() as conn:
                conn.execute('UPDATE wallet_transfer_state SET amount_sar=? WHERE cid=?', (str(sar_value), cid))
            send(api, cid,
                 tr(cid, 'تأكيد التحويل إلى المستخدم:', 'Confirm transfer to user:') + f' <code>{recipient}</code>\n<b>{usd_value:.2f} USD / {sar_value:.2f} SAR</b>',
                 kb([[btn(tr(cid, 'تأكيد التحويل', 'Confirm transfer'), f'wallettransferconfirm:{recipient}:{sar_value}', style='success')],
                     [btn(tr(cid, 'إلغاء', 'Cancel'), 'wallet', style='primary')]]))
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
    result = send(api, G['ADMIN_ID'], '🧾 <b>إثبات دفع جديد</b>\n\n' + esc(name(pid)) + f'\nالكمية: {product_options.snapshot(sys.modules[__name__], "receipt", cid)}\nالسعر عند الطلب: {sar} SAR / {usd} USD\nالطريقة: {esc(method)}\nالعميل: {esc(username)}\nID: <code>{cid}</code>')
    forwarded = api.call('forwardMessage', chat_id=G['ADMIN_ID'], from_chat_id=cid, message_id=message['message_id']) if result else None
    if not forwarded:
        send(api, cid, tr(cid, 'تعذر إرسال الإثبات للإدارة. أعد المحاولة أو تواصل مع ', 'Could not forward the receipt. Retry or contact ') + SUPPORT)
        return True
    add_order(cid, pid, method, 'review', usd=usd, sar=sar)
    with db() as conn:
        conn.execute('DELETE FROM payment_quotes WHERE cid=? AND pid=? AND method=?', (cid, pid, method))
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
    if payment_methods.action(sys.modules[__name__], api, cid, value):
        return
    if discounts.action(sys.modules[__name__], api, cid, value):
        return
    prefix, _, arg = value.partition(':')
    arg = LEGACY.get(arg, arg)
    if prefix in ('home', 'enter_store'):
        reset_navigation_state(cid)
        home(api, cid)
    elif prefix == 'start':
        start(api, cid)
    elif prefix == 'products':
        products(api, cid)
    elif prefix == 'referrals':
        referral_page(api, cid)
    elif prefix == 'product':
        category(api, cid, arg)
    elif prefix in ('item', 'claude'):
        item(api, cid, arg)
    elif value in LEGACY:
        item(api, cid, LEGACY[value])
    elif prefix == 'catdesc':
        admin_category_description(api, cid, arg)
    elif prefix == 'catdesclang':
        lang, _, pid = arg.partition(':')
        admin_category_description(api, cid, pid, lang)
    elif prefix == 'admin':
        if arg == 'categorydesc': admin_category_description(api, cid)
        elif arg == 'orders': admin_orders(api, cid)
        elif arg == 'activity': admin_activity(api, cid)
        elif arg == 'stats': admin_stats(api, cid)
        elif arg == 'icons': admin_icons(api, cid)
        elif arg == 'buttonlabels': admin_button_labels(api, cid)
        elif arg == 'prices': admin_prices(api, cid)
        elif arg == 'photos': admin_photo_menu(api, cid)
        elif arg == 'editname': admin_text_menu(api, cid, 'name')
        elif arg == 'editdesc': admin_text_menu(api, cid, 'description')
        elif arg == 'stock': admin_stock(api, cid)
        elif arg == 'supplierapi': supplier_api_menu(api, cid)
        elif arg == 'info': admin_info_menu(api, cid)
        elif arg == 'addproduct': begin_add_product(api, cid)
        elif arg == 'myproducts': admin_products_page(api, cid)
        elif arg == 'cancelproduct' and cid == G['ADMIN_ID']:
            with db() as conn: conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
            admin_panel(api, cid)
        elif arg == 'broadcast' and cid == G['ADMIN_ID']:
            BROADCAST_PENDING.add(cid)
            send(api, cid, '📢 <b>إرسال رسالة للجميع</b>\n\nأرسل الآن الرسالة التي تريد إرسالها لجميع مستخدمي البوت.\nيمكنك إرسال نص أو صورة مع تعليق.',
                 kb([[btn('❌ إلغاء', 'admin:broadcast_cancel')]]))
        elif arg == 'broadcast_cancel' and cid == G['ADMIN_ID']:
            BROADCAST_PENDING.discard(cid)
            admin_panel(api, cid)
        else: admin_panel(api, cid)
    elif prefix == 'payreview' and cid == G['ADMIN_ID']:
        decision, _, oid = arg.partition(':')
        with db() as conn:
            row = conn.execute('SELECT cid,status FROM orders WHERE id=?', (oid,)).fetchone()
            if not row or row[1] != 'review':
                return send(api, cid, '⚠️ الطلب غير موجود أو تمت معالجته مسبقاً.')
            customer = row[0]
            if decision == 'accept':
                conn.execute('UPDATE orders SET status="paid" WHERE id=? AND status="review"', (oid,))
                if fulfill_paid_order(api, oid):
                    return send(api, cid, f'✅ تم قبول الطلب <b>#{esc(oid)}</b> وبدأ التنفيذ التلقائي عبر المورد.')
                G['PENDING_ADMIN_DELIVERY'][G['ADMIN_ID']]={'customer':customer,'order_id':oid}
                send(api, customer, '✅ <b>تم قبول الدفع.</b>\n\nسيتم إرسال طلبك لك قريباً.')
                return send(api, cid, f'✅ تم قبول الطلب <b>#{esc(oid)}</b>.\n\n📤 أرسل الآن أي رسالة أو صورة أو ملف تريد إرساله للعميل.\nسيتم إرسال <b>الرسالة التالية فقط</b> له مباشرة.')
            conn.execute('UPDATE orders SET status="rejected" WHERE id=? AND status="review"', (oid,))
        send(api, customer, '❌ <b>تم رفض إثبات الدفع.</b>\n\nيرجى إعادة المحاولة أو التواصل مع الدعم.')
        send(api, cid, f'❌ تم رفض الطلب <b>#{esc(oid)}</b> وإبلاغ العميل.')
    elif prefix == 'infocat':
        admin_info_menu(api,cid,arg)
    elif prefix == 'infopick':
        admin_info_editor(api,cid,arg)
    elif prefix == 'infotoggle' and cid == G['ADMIN_ID']:
        field,_,pid=arg.partition(':'); sp,ss,sw,w=info_display(pid)
        if field=='price': sp=0 if sp else 1
        elif field=='stock': ss=0 if ss else 1
        elif field=='warranty': sw=0 if sw else 1
        with db() as conn: conn.execute('INSERT OR REPLACE INTO product_info_display VALUES (?,?,?,?,?)',(pid,sp,ss,sw,w))
        admin_info_editor(api,cid,pid)
    elif prefix == 'infowarranty' and cid == G['ADMIN_ID']:
        with db() as conn: conn.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)',(cid,'info_warranty',arg))
        send(api,cid,'✏️ أرسل نص الضمان لهذا المنتج، مثال: <b>15 يوم</b>.',kb([[btn('إلغاء','admin:info')]]))
    elif prefix == 'mycategory':
        admin_category_detail(api, cid, arg)
    elif prefix == 'myproduct':
        admin_product_detail(api, cid, arg)
    elif prefix == 'myproducttoggle':
        toggle_admin_product(api, cid, arg)
    elif prefix == 'myproductdelete':
        delete_admin_product(api, cid, arg)
    elif prefix == 'stockcat':
        admin_stock(api, cid, arg)
    elif prefix == 'stockpick':
        stock_editor(api, cid, arg)
    elif prefix == 'stockset':
        value, _, pid = arg.partition(':')
        if value in ('0', '1'):
            stock_editor(api, cid, pid, value)
    elif prefix == 'stockqty' and cid == G['ADMIN_ID']:
        pid = LEGACY.get(arg, arg)
        if pid in VARIANTS or pid in G['PRODUCTS'] or custom_product(pid):
            with db() as conn:
                conn.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)', (cid, 'stock_quantity', pid))
            send(api, cid, '<b>' + esc(name(pid, cid)) + '</b>\n\n📦 الكمية الحالية: <b>' + esc(product_stock(pid)) + '</b>\n\nأرسل الكمية الجديدة كرقم، مثال: <code>4</code>.', kb([[btn('❌ إلغاء', 'stockpick:' + pid)]]))
    elif prefix == 'pandorabrowse' and cid == G['ADMIN_ID']:
        pandora_catalog_open(api, cid, arg, 0)
    elif prefix == 'pandorapage' and cid == G['ADMIN_ID']:
        pandora_catalog_categories(api, cid)
    elif prefix == 'pandoracategories' and cid == G['ADMIN_ID']:
        pandora_catalog_categories(api, cid)
    elif prefix == 'pandoracat' and cid == G['ADMIN_ID']:
        cidx, _, page = arg.partition(':')
        pandora_catalog_page(api, cid, cidx or 0, page or 0)
    elif prefix == 'pandorap' and cid == G['ADMIN_ID']:
        pandora_catalog_product(api, cid, arg)
    elif prefix == 'pandorav' and cid == G['ADMIN_ID']:
        pidx, _, vidx = arg.partition(':')
        pandora_catalog_save(api, cid, pidx, vidx)
    elif prefix == 'suppliercat':
        supplier_api_menu(api, cid, arg)
    elif prefix == 'supplierpick':
        supplier_api_editor(api, cid, arg)
    elif prefix == 'supplierpandora' and cid == G['ADMIN_ID']:
        pid = LEGACY.get(arg, arg)
        endpoint, api_key, service_id, enabled, provider, variant_id = supplier_api_row(pid)
        endpoint = 'https://api.pandoradigital.shop/api/v1'
        provider = 'pandora'
        with db() as conn:
            conn.execute('INSERT INTO supplier_api(pid,endpoint,api_key,service_id,enabled,provider,variant_id) VALUES (?,?,?,?,?,?,?) ON CONFLICT(pid) DO UPDATE SET endpoint=excluded.endpoint,provider=excluded.provider',
                         (pid, endpoint, api_key, service_id, enabled, provider, variant_id))
        send(api, cid, '✅ تم اختيار <b>Pandora Digital</b> لهذا المنتج.\n\nالرابط والمفتاح يُستخدمان من Railway تلقائيًا. أضف الآن Product ID و Variant ID فقط.')
        supplier_api_editor(api, cid, pid)
    elif prefix == 'supplierset' and cid == G['ADMIN_ID']:
        field, _, pid = arg.partition(':')
        if field in ('endpoint', 'key', 'service', 'variant'):
            action_name = {'endpoint':'supplier_endpoint','key':'supplier_key','service':'supplier_service','variant':'supplier_variant'}[field]
            with db() as conn:
                conn.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)', (cid, action_name, pid))
            prompt = {'endpoint':'أرسل رابط API الكامل، مثال: <code>https://example.com/api/order</code>',
                      'key':'أرسل مفتاح API. لن يظهر كاملًا بعد الحفظ.',
                      'service':'أرسل Product ID لدى المورد.','variant':'أرسل Variant ID لدى المورد.'}[field]
            send(api, cid, '🔌 <b>' + esc(name(pid, cid)) + '</b>\n\n' + prompt, kb([[btn('❌ إلغاء', 'supplierpick:' + pid)]]))
    elif prefix == 'supplierprice' and cid == G['ADMIN_ID']:
        pid = LEGACY.get(arg, arg)
        try:
            row = pandora_refresh_price(pid)
            if row:
                cost, margin, sale = row
                send(api, cid, f'✅ تكلفة Pandora: <b>${cost:.2f}</b>\nهامش الربح: <b>${margin:.2f}</b>\nسعر البيع: <b>${sale:.2f}</b>')
            else:
                send(api, cid, '⚠️ تعذر جلب تكلفة Pandora لهذا المنتج.')
        except Exception as exc:
            send(api, cid, '❌ تعذر تحديث السعر: <code>' + esc(type(exc).__name__) + '</code>')
        supplier_api_editor(api, cid, pid)
    elif prefix == 'suppliermargin' and cid == G['ADMIN_ID']:
        pid = LEGACY.get(arg, arg)
        with db() as conn:
            conn.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)', (cid, 'supplier_margin', pid))
        send(api, cid, '➕ أرسل هامش الربح بالدولار، مثال: <code>2.00</code>.', kb([[btn('❌ إلغاء', 'supplierpick:' + pid)]]))
    elif prefix == 'suppliertest' and cid == G['ADMIN_ID']:
        supplier_test_connection(api, cid, arg)
    elif prefix == 'suppliertoggle' and cid == G['ADMIN_ID']:
        pid = LEGACY.get(arg, arg)
        endpoint, api_key, service_id, enabled, provider, variant_id = supplier_api_row(pid)
        missing = []
        if not endpoint: missing.append('رابط API')
        if not api_key: missing.append('مفتاح API')
        if provider == 'pandora' and not service_id: missing.append('Product ID')
        if provider == 'pandora' and not variant_id: missing.append('Variant ID')
        if missing:
            send(api, cid, '⚠️ تم حفظ الموجود، لكن باقي قبل التفعيل: <b>' + esc(' + '.join(missing)) + '</b>.\n\nإذا هدفك فقط تجربة المفتاح الآن اضغط 🧪 اختبار الاتصال.')
            supplier_api_editor(api, cid, pid)
        else:
            with db() as conn:
                conn.execute('INSERT INTO supplier_api(pid,endpoint,api_key,service_id,enabled,provider,variant_id) VALUES (?,?,?,?,?,?,?) ON CONFLICT(pid) DO UPDATE SET enabled=excluded.enabled',
                             (pid, endpoint, api_key, service_id, 0 if enabled else 1, provider, variant_id))
            supplier_api_editor(api, cid, pid)
    elif prefix == 'supplierdelete' and cid == G['ADMIN_ID']:
        pid = LEGACY.get(arg, arg)
        with db() as conn:
            conn.execute('DELETE FROM supplier_api WHERE pid=?', (pid,))
        send(api, cid, '✅ تم حذف ربط API لهذا المنتج.')
        supplier_api_editor(api, cid, pid)
    elif prefix == 'txtcat':
        field, _, category_id = arg.partition(':')
        admin_text_menu(api, cid, field, category_id)
    elif prefix == 'txtpick':
        field, _, pid = arg.partition(':')
        admin_text_editor(api, cid, field, pid)
    elif prefix == 'txtedit':
        parts = arg.split(':', 2)
        if len(parts) == 3:
            field, lang, pid = parts
            admin_text_editor(api, cid, field, pid, lang)
    elif prefix == 'photocat':
        admin_photo_menu(api, cid, arg)
    elif prefix == 'photopick':
        admin_photo_editor(api, cid, arg)
    elif prefix == 'photodel':
        admin_photo_editor(api, cid, arg, delete=True)
    elif prefix == 'pricecat':
        admin_prices(api, cid, arg)
    elif prefix == 'pricepick':
        price_editor(api, cid, arg)
    elif prefix == 'priceedit':
        currency, _, pid = arg.partition(':')
        price_editor(api, cid, pid, currency)
    elif prefix == 'buttonlabel':
        begin_button_label(api, cid, arg)
    elif prefix == 'buttonnames' and arg == 'categories':
        button_names_categories(api, cid)
    elif prefix == 'resetbuttonlabel':
        reset_button_label(api, cid, arg)
    elif prefix == 'removebuttonicon':
        remove_button_icon(api, cid, arg)
    elif prefix == 'removenameicon':
        remove_name_icon(api, cid, arg)
    elif prefix == 'cancelbuttonlabel':
        if cid == G['ADMIN_ID']:
            with db() as conn: conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
            admin_button_labels(api, cid)
    elif prefix == 'iconmenu':
        if arg == 'products': admin_icon_products(api, cid)
        elif arg == 'categories': admin_icon_categories(api, cid)
        elif arg == 'buttons': admin_icon_buttons(api, cid)
        else: admin_icons(api, cid)
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
            stored_value = arg if prefix == 'setlang' else 'USD'
            conn.execute(f'UPDATE preferences SET {column}=? WHERE cid=?', (stored_value, cid))
        home(api, cid)
    elif prefix in ('buy', 'cancel'):
        with db() as conn:
            conn.execute('DELETE FROM receipts WHERE cid=?', (cid,))
        if prefix == 'buy': payments(api, cid, arg)
        elif arg in VARIANTS: item(api, cid, arg)
        else: category(api, cid, arg)
    elif prefix == 'wallet':
        if arg == 'topup': wallet_amounts(api, cid)
        elif arg == 'transfer': wallet_transfer_begin(api, cid)
        else: wallet(api, cid)
    elif prefix == 'wallettransferconfirm':
        recipient, _, amount_sar = arg.partition(':')
        wallet_transfer_confirm(api, cid, recipient, amount_sar)
    elif prefix == 'topupmanual':
        method_id, _, value = arg.partition(':')
        wallet_manual_topup(api, cid, method_id, value)
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
    elif prefix == 'community':
        send(api, cid, tr(cid, '📢 <b>مجتمع VEXA STORE</b>\n\n🟢 المنتجات والاشتراكات المتوفرة\n🎁 أكواد الخصم والعروض\n🆕 المنتجات الجديدة\n🔔 تنبيهات التوفر\n\n🚀 انضم إلى مجموعتنا الرسمية!', '📢 <b>VEXA STORE Community</b>\n\n🟢 Available products and subscriptions\n🎁 Discount codes and offers\n🆕 New products\n🔔 Restock alerts\n\n🚀 Join our official group!'), kb([[{'text': tr(cid, '👥 الانضمام للمجموعة', '👥 Join the group'), 'url': 'https://t.me/SAU2030_k'}], [btn(tr(cid, '↩️ الرئيسية', '↩️ Home'), 'home')]]))
    elif prefix == 'support':
        send(api, cid, 'Support:' + SUPPORT, menu(cid))
    elif prefix == 'api':
        send(api, cid, tr(cid, '🔗 إعدادات API المتجر غير مفعّلة حاليًا.', '🔗 Store API settings are not active yet.'), menu(cid))
    elif prefix == 'warranty':
        send(api, cid, tr(cid, '🛡 تختلف شروط الضمان حسب المنتج. راجع وصفه قبل الطلب. للاستفسار: ', '🛡 Warranty terms vary by product. Read its description before ordering. Questions: ') + SUPPORT, menu(cid))
    else:
        products(api, cid)


def install(namespace):
    global G
    G = namespace
    try:
        __import__('threading').Thread(target=pandora_startup_probe, daemon=True).start()
    except Exception:
        pass
    apply_icon_overrides()
    namespace.update({'show_start': start, 'show_home': home, 'show_products': products,
                      'show_product': category, 'show_claude_product': item, 'handle_action': action, 'action': action,
                      'handle_receipt': receipt, 'order_name': name, 'home_keyboard': menu,
                      'broadcast_new_products': broadcast_new_products,
                      'chatgpt_visibility_admin': chatgpt_visibility_admin,
                      'toggle_chatgpt_visibility': toggle_chatgpt_visibility,
                      'handle_admin_product': handle_admin_product, 'handle_info_warranty': handle_info_warranty, 'handle_info_icon': handle_info_icon})
    menu_actions = namespace.setdefault('MENU_ACTIONS', namespace.get('MENU', {}))
    menu_actions.update({'🚀 ابدأ': 'start', '🚀 Start': 'start', '🛍 المنتجات': 'products',
                         '🛍 Products': 'products', '⚡ VEXA VOLT': 'support', '⚡ VEXA VOLT': 'support',
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
            description = product_description(pid, cid)
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


def custom_category(category_id):
    with db() as conn:
        return conn.execute('SELECT cid,name FROM admin_categories WHERE cid=?', (category_id,)).fetchone()


def custom_product(pid):
    with db() as conn:
        return conn.execute('SELECT pid,name,description,price_usd,available,category_id,stock FROM admin_products WHERE pid=?', (pid,)).fetchone()


def name(pid, cid=0):
    pid = LEGACY.get(pid, pid)
    if pid == 'youtube':
        return text_override(pid, 'name', prefs(cid)[0], 'YouTube')
    if pid in VARIANTS:
        return text_override(pid, 'name', prefs(cid)[0], VARIANTS[pid]['name'][prefs(cid)[0]])
    category = custom_category(pid)
    if category:
        return text_override(pid, 'name', prefs(cid)[0], category[1])
    cp = custom_product(pid)
    if cp:
        return text_override(pid, 'name', prefs(cid)[0], cp[1])
    return text_override(pid, 'name', prefs(cid)[0], G['PRODUCTS'].get(pid, {}).get('name', pid))


def start(api, cid):
    reset_navigation_state(cid)
    send(api, cid, tr(cid, '👋 <b>مرحباً بك في VEXA STORE</b>\n\nمتجر الخدمات والاشتراكات الرقمية.',
                     '👋 <b>Welcome to VEXA STORE</b>\n\nDigital services and subscriptions.'),
         kb([[btn('🚀 START | ابدأ', 'enter_store', ui_icon('ui_start'), style='primary')], [btn('🌐 العربية / English', 'settings:lang', ui_icon('ui_language'), style='primary')]]))


def home(api, cid):
    balance_sar = wallet_balance(cid)
    balance_usd = (balance_sar / RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    with db() as conn:
        purchases = conn.execute('SELECT COUNT(*) FROM orders WHERE cid=? AND status="paid"', (cid,)).fetchone()[0]
    text = tr(cid, f'👋 <b>أهلاً بك في VEXA STORE!</b>\n\n🆔 رقم العضوية: <code>{cid}</code>\n👤 حسابك: <a href="tg://user?id={cid}">فتح الحساب</a>\n💳 الرصيد: <b>${balance_usd:.2f}</b>\n🛍 المشتريات: <b>{purchases}</b>\n\nاختر من القائمة أدناه:', f'👋 <b>Welcome to VEXA STORE!</b>\n\n🆔 Member ID: <code>{cid}</code>\n👤 Account: <a href="tg://user?id={cid}">Open profile</a>\n💳 Balance: <b>${balance_usd:.2f}</b>\n🛍 Purchases: <b>{purchases}</b>\n\nChoose from the menu below:')
    rows = [[btn(tr(cid,'المنتجات','Products'),'products',ui_icon('ui_products'),style='primary'), btn(tr(cid,'شحن الرصيد','Top up'),'wallet:topup',ui_icon('ui_topup'),style='primary')], [btn(tr(cid,'الإحالات','Referrals'),'referrals',ui_icon('ui_referrals'),style='primary'), btn(tr(cid,'حسابي','My account'),'wallet',ui_icon('ui_account'),style='primary')], [btn(tr(cid,'تواصل مع الدعم','Contact support'),'support',ui_icon('ui_support'),style='primary'), btn(tr(cid,'إبلاغ عن مشكلة','Report issue'),'support',ui_icon('ui_report'),style='primary')], [btn(tr(cid,'العملة','Currency'),'settings:currency',ui_icon('ui_currency'),style='primary'), btn('Language / اللغة','settings:lang',ui_icon('ui_language'),style='primary')]]
    rows.append([btn(tr(cid, '📢 مجتمع VEXA STORE', '📢 VEXA STORE Community'), 'community', ui_icon('ui_community'), style='primary')])
    if cid == G.get('ADMIN_ID'):
        rows.append([btn('لوحة الطلبات', 'admin', ui_icon('ui_admin'), style='primary')])
    send(api, cid, text, kb(rows))

def products(api, cid):
    buttons = [btn(category_label(pid, cid), 'product:' + pid, ui_icon(pid) or p.get('custom_emoji_id')) for pid, p in G['PRODUCTS'].items() if category_visible(pid)]
    with db() as conn:
        custom_categories = conn.execute('SELECT cid,name FROM admin_categories ORDER BY rowid').fetchall()
    buttons += [btn(name(category_id, cid), 'product:' + category_id, ui_icon(category_id)) for category_id, category_name in custom_categories if category_visible(category_id)]
    rows = [buttons[i:i+3] for i in range(0, len(buttons), 3)]
    rows += [[btn(tr(cid, '🏠 الرئيسية', '🏠 Home'), 'home')]]
    send(api, cid, tr(cid, '🛍 <b>المنتجات</b>\nاختر الخدمة:', '🛍 <b>Products</b>\nChoose a service:'), kb(rows))


def settings(api, cid, kind):
    if kind == 'lang':
        rows = [[btn('العربية', 'setlang:ar'), btn('English', 'setlang:en')]]
        text = '🌐 اختر اللغة / Choose language'
    else:
        rows = [[btn('🇺🇸 USD — US Dollar', 'setcurrency:USD')]]
        text = '💱 أسعار المنتجات ثابتة بالدولار USD\nيظهر التحويل للريال السعودي عند الدفع فقط.'
    send(api, cid, text, kb(rows + [nav(cid)]))


def card(api, cid, image_path, title, text, keyboard, pid=None):
    """Separate photo and full text so Telegram's caption limit never drops terms."""
    override = saved_product_photo(pid) if pid else None
    if override is not None:
        image_path = None
        if override:
            api.call('sendPhoto', chat_id=cid, photo=override, caption=title[:900])
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
    # The product fields are escaped at their source; info_block supplies
    # the only HTML markup, including the custom emoji entity.
    send(api, cid, text, keyboard)



def grok_cards(api, cid, choices, show_heading=True, default_image="assets/grok.png"):
    """Compact photo cards for Grok only; prices share the checkout source."""
    if show_heading:
        send(api, cid, tr(cid, '✦ <b>اشتراكات Grok</b>\nاختر الباقة المناسبة لك:', '✦ <b>Grok subscriptions</b>\nChoose your plan:'))
    for v in choices:
        pid = v['id']
        available = can_order(pid)
        if v.get('review_required'):
            status = tr(cid, '⏸ قيد المراجعة — الطلب غير متاح', '⏸ Under review — ordering unavailable')
        elif not in_stock(pid):
            status = tr(cid, '🔴 نفدت الكمية', '🔴 Out of stock')
        else:
            status = tr(cid, '🟢 متوفر', '🟢 Available')
        caption = '<b>' + esc(name(pid, cid)) + '</b>\n\n'
        caption += '💰 <b>' + price(cid, pid, 'USD') + '</b>\n\n' + status
        if v.get('manual_delivery'):
            caption += '\n' + tr(cid, '✉️ يتم إرسال بيانات المنتج بعد تأكيد الدفع', '✉️ Product details are sent after payment confirmation')
        details = btn(tr(cid, '📋 التفاصيل', '📋 Details'), 'item:' + pid)
        rows = [[btn(tr(cid, '🛒 شراء الآن', '🛒 Buy now'), 'buy:' + pid, style='success'), details]] if available else [[btn(tr(cid, '🔴 غير متوفر', '🔴 Unavailable'), 'item:' + pid, style='danger')]]
        if not available:
            rows[0][0]['style'] = 'danger'
        rows.append([btn(tr(cid, '⚡ VEXA VOLT', '⚡ VEXA VOLT'), 'support')])
        markup = kb(rows)
        override = saved_product_photo(pid)
        if override is not None:
            if not override or not api.call('sendPhoto', chat_id=cid, photo=override, caption=caption, parse_mode='HTML', reply_markup=markup):
                send(api, cid, caption, markup)
            continue
        path = (BASE / (v.get('image') or default_image)).resolve()
        delivered = False
        if path.is_relative_to(BASE) and path.is_file():
            boundary = 'VEXA' + uuid.uuid4().hex
            fields = {'chat_id': str(cid), 'caption': caption, 'parse_mode': 'HTML',
                      'reply_markup': json.dumps(markup, ensure_ascii=False)}
            body = b''
            for key, value in fields.items():
                body += f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
            body += f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="{path.name}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
            body += path.read_bytes() + f'\r\n--{boundary}--\r\n'.encode()
            try:
                api_url = getattr(api, 'base_url', None) or getattr(api, 'u', None)
                req = urllib.request.Request(api_url + 'sendPhoto', body, {'Content-Type': f'multipart/form-data; boundary={boundary}'})
                with urllib.request.urlopen(req, timeout=40) as response:
                    delivered = bool(json.load(response).get('ok'))
            except Exception as exc:
                print('Grok card image failed:', type(exc).__name__)
        if not delivered:
            send(api, cid, caption, markup)
    send(api, cid, tr(cid, 'تصفح أقسام المتجر:', 'Browse store categories:'),
         kb([[btn(tr(cid, '↩️ الأقسام', '↩️ Categories'), 'products'),
              btn(tr(cid, '🏠 الرئيسية', '🏠 Home'), 'home')]]))


def category(api, cid, pid):
    if not category_visible(pid):
        return products(api, cid)
    p = G['PRODUCTS'].get(pid)
    if not p:
        custom_cat = custom_category(pid)
        if not custom_cat:
            products(api, cid)
            return
        with db() as conn:
            choices = conn.execute('SELECT pid,name,price_usd,available,stock FROM admin_products WHERE category_id=? ORDER BY rowid', (pid,)).fetchall()
        rows = []
        for product_id, product_name, price_usd, available, stock in choices:
            if not product_visible(product_id):
                continue
            sold_out = not available or int(stock or 0) <= 0
            qty = int(stock or 0)
            label = compact_name(product_id, cid) + ' | 💵 ' + price(cid, product_id, 'USD') + ' | ' + compact_stock(qty)
            if sold_out:
                label = '🔴 ' + label
            rows.append([btn(label, 'item:' + product_id, product_button_icon(product_id, cid), style='danger' if sold_out else 'success')])
        send(api, cid, category_heading(pid, cid), kb(rows + [nav(cid)]))
        return
    if pid == 'youtube':
        with db() as conn:
            yt_rows = conn.execute('''SELECT DISTINCT p.pid,p.name,p.price_usd,p.available,p.stock
                FROM admin_products p
                LEFT JOIN admin_categories c ON c.cid=p.category_id
                WHERE p.category_id='youtube'
                   OR lower(p.name) LIKE '%youtube%'
                   OR p.name LIKE '%يوتيوب%'
                   OR lower(COALESCE(c.name,'')) LIKE '%youtube%'
                   OR COALESCE(c.name,'') LIKE '%يوتيوب%'
                ORDER BY p.rowid''').fetchall()
        if yt_rows:
            rows = []
            for product_id, product_name, price_usd, available, stock in yt_rows:
                if not product_visible(product_id):
                    continue
                qty = int(stock or 0)
                sold_out = (not bool(available)) or qty <= 0
                label = ('🔴 ' if sold_out else '🟢 ') + compact_name(product_id, cid) + ' | 💵 ' + price(cid, product_id, 'USD') + ' | ' + compact_stock(qty)
                rows.append([btn(label, 'item:' + product_id, product_button_icon(product_id, cid),
                                 style='danger' if sold_out else 'success')])
            if rows:
                send(api, cid, category_heading(pid, cid), kb(rows + [nav(cid)]))
                return
    choices = [v for v in VARIANTS.values() if v['category'] == pid and product_visible(v['id'])]
    if choices:
        rows = []
        for v in choices:
            sold_out = not in_stock(v['id'])
            status = '⏸ ' if v.get('review_required') else ('🔴 ' if sold_out else '🟢 ')
            qty = product_stock(v['id'])
            rows.append([btn(status + compact_name(v['id'], cid) + ' | 💵 ' + price(cid, v['id'], 'USD') + ' | ' + compact_stock(qty),
                             'item:' + v['id'], p.get('custom_emoji_id') if product_button_icon(v['id'], cid) is None else product_button_icon(v['id'], cid), style='danger' if sold_out else 'success')])
        send(api, cid, category_heading(pid, cid), kb(rows + [nav(cid)]))
        return
    english = {'youtube': 'YouTube Premium for one month. Ad-free viewing, background playback, offline downloads and YouTube Music Premium benefits.',
               'netflix': 'Netflix subscription for movies, series and entertainment.', 'iptv': 'IPTV subscriptions for compatible devices.'}
    description = product_description(pid, cid)
    text = esc(name(pid, cid)) + '\n\n' + esc(price(cid, pid)) + '\n\n' + esc(tr(cid, '✅ متوفر' if in_stock(pid) else '🔴 نفدت الكمية', '✅ Available' if in_stock(pid) else '🔴 Out of stock')) + '\n\n' + esc(description)
    rows = [[btn(tr(cid, '🛒 طلب المنتج', '🛒 Order'), 'buy:' + pid)]] if can_order(pid) else []
    rows += [[btn(tr(cid, '⚡ VEXA VOLT', '⚡ VEXA VOLT'), 'support')], nav(cid)]
    card(api, cid, f'assets/{pid}.png', name(pid, cid), text, kb(rows), pid=pid)


def item(api, cid, pid):
    pid = LEGACY.get(pid, pid)
    if not product_visible(pid):
        return send(api, cid, tr(cid, 'هذا المنتج مخفي حاليًا.', 'This product is currently hidden.'), kb([nav(cid, 'products')]))
    v = VARIANTS.get(pid)
    if not v:
        cp = custom_product(pid)
        if not cp:
            products(api, cid)
            return
        _, product_name, description, price_usd, available, category_id, stock = cp
        status = tr(cid, '✅ متوفر', '✅ Available') if available and int(stock or 0) > 0 else tr(cid, '🔴 نفدت الكمية', '🔴 Out of stock')
        text = esc(name(pid, cid)) + '\n\n' + info_block(pid,cid) + '\n\n' + esc(status) + '\n\n' + esc(product_description(pid, cid))
        rows = [[btn(tr(cid, '🛒 طلب المنتج', '🛒 Order'), 'buy:' + pid)]] if can_order(pid) else []
        rows += [[btn(tr(cid, '⚡ VEXA VOLT', '⚡ VEXA VOLT'), 'support')], nav(cid, 'product:' + category_id)]
        card(api, cid, None, name(pid, cid), text, kb(rows), pid=pid)
        return
    lang = prefs(cid)[0]
    available = ''
    if not in_stock(pid):
        available = tr(cid, '🚫 نفد لدى المورد وقت المراجعة. الطلب غير متاح حاليًا.', '🚫 Out of stock at the last supplier check. Ordering is currently unavailable.')
    text = esc(name(pid, cid)) + '\n\n' + info_block(pid,cid) + (('\n\n' + esc(available)) if available else '') + '\n\n' + esc(product_description(pid, cid))
    if v.get('promotions'):
        text += '\n\n' + esc(tr(cid, 'أسعار الكميات — تواصل مع الدعم:', 'Bulk prices — contact support:'))
        for tier in v['promotions']:
            text += '\n' + esc(tier['min_quantity']) + '+: ' + esc(price(cid, pid, source_price=tier['source_usd'])) + esc(tr(cid, ' لكل قطعة', ' per unit'))
    if v.get('review_required'):
        text += '\n\n⚠️ ' + esc(v['review_required'][lang])
    rows = []
    if can_order(pid):
        rows.append([btn(tr(cid, '🛒 طلب قطعة واحدة', '🛒 Order one item'), 'buy:' + pid)])
    rows += [[btn(tr(cid, '⚡ VEXA VOLT', '⚡ VEXA VOLT'), 'support')], nav(cid, 'product:' + v['category'])]
    card(api, cid, v.get('image'), name(pid, cid), text, kb(rows), pid=pid)


def can_order(pid):
    pid = LEGACY.get(pid, pid)
    if not product_visible(pid):
        return False
    if pid in VARIANTS:
        return in_stock(pid) and not VARIANTS[pid].get('review_required')
    if custom_product(pid):
        return in_stock(pid) and amount(pid) is not None
    return pid in G['PRODUCTS'] and in_stock(pid) and amount(pid) is not None


def back(pid):
    pid = LEGACY.get(pid, pid)
    return ('item:' if pid in VARIANTS or custom_product(pid) else 'product:') + pid


def checkout_totals(cid, pid):
    return discounts.totals(sys.modules[__name__], cid, pid)


def summary(cid, pid):
    qty = product_options.selected(sys.modules[__name__], cid, pid)
    sar, usd, discount, code = checkout_totals(cid, pid)
    text = esc(name(pid,cid)) + '\n💵 سعر الوحدة: ' + price(cid,pid,'USD') + f'\n🛍 الكمية: {qty}'
    if code:
        discount_usd = (Decimal(str(discount)) / RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        text += '\n🎟 ' + esc(code) + f' — الخصم: {discount_usd:.2f} USD / {discount:.2f} SAR'
    text += f'\n\n💲 <b>الإجمالي بالدولار:</b> {usd:.2f} USD'
    text += f'\n🇸🇦 <b>الإجمالي بالريال:</b> {sar:.2f} SAR'
    return text


def wallet(api, cid):
    sar = wallet_balance(cid).quantize(Decimal('0.01'))
    usd = (sar / RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    text = tr(cid, '<b>محفظة VEXA</b>\n\nرصيدك الحالي:', '<b>VEXA Wallet</b>\n\nYour current balance:')
    text += f'\n<b>{sar:.2f} {tr(cid, "ر.س", "SAR")}</b>\n<b>{usd:.2f} USD</b>'
    rows = [[btn(tr(cid, 'إضافة رصيد', 'Add funds'), 'wallet:topup', ui_icon('ui_wallet_add'), style='success'),
             btn(tr(cid, 'تحويل', 'Transfer'), 'wallet:transfer', ui_icon('ui_wallet_transfer'), style='primary')],
            [btn(tr(cid, 'الرجوع للقائمة', 'Back to Menu'), 'home', ui_icon('ui_wallet_back'), style='primary')]]
    send(api, cid, text, kb(rows))


def wallet_amounts(api, cid):
    rows = []
    for pair in ((20, 50), (100, 200)):
        row = []
        for value in pair:
            usd = (Decimal(value) / RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            row.append(btn(f'{value} {tr(cid, "ر.س", "SAR")} / {usd:.2f} USD', f'topup:{value}', style='success'))
        rows.append(row)
    text = tr(cid, 'اختر مبلغ إضافة الرصيد — جميع المبالغ معروضة بالريال والدولار:',
              'Choose an add-funds amount — all amounts are shown in SAR and USD:')
    rows.append([btn(tr(cid, 'مبلغ اختياري بالدولار', 'Custom amount in USD'), 'topupcustom', style='success')])
    rows.append([btn(tr(cid, 'رجوع', 'Back'), 'wallet', style='primary')])
    send(api, cid, text, kb(rows))


def wallet_method(api, cid, value):
    try:
        value = Decimal(value).quantize(Decimal('0.01'))
    except Exception:
        return wallet_amounts(api, cid)
    if value <= 0 or value > 5000:
        return wallet_amounts(api, cid)
    usd = (value / RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    text = tr(cid, 'اختر طريقة إضافة الرصيد:', 'Choose an add-funds method:') + f'\n\n<b>{value:.2f} SAR / {usd:.2f} USD</b>'
    rows = [
        [btn('Crypto Pay', f'topupcrypto:{value}', ui_icon('pay_cryptopay'), style='success')],
        [btn('Bybit / USDT', f'topupbybit:{value}', ui_icon('pay_bybit'), style='success')]
    ]
    for method in payment_methods.methods(sys.modules[__name__], True):
        rows.append([btn(method[1], f'topupmanual:{method[0]}:{value}', ui_icon('pay_custom_' + str(method[0])), style='success')])
    rows.append([btn(tr(cid, 'رجوع', 'Back'), 'wallet:topup', style='primary')])
    send(api, cid, text, kb(rows))


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
         kb([[btn('Bybit Pay', 'topupsend:bybitid:' + topup_id, ui_icon('pay_bybitid'), style='success')],
             [btn('USDT • TRON (TRC20)', 'topupsend:trc20:' + topup_id, ui_icon('pay_trc20'), style='success')],
             [btn('USDT • BSC (BEP20)', 'topupsend:bep20:' + topup_id, ui_icon('pay_bep20'), style='success')],
             [btn(tr(cid, 'رجوع', 'Back'), f'topup:{value}', style='primary')]]))


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
    warning = tr(cid, 'يتم تنفيذ الطلب بعد مراجعة الدفع وتأكيد التوفر، ثم إرسال بيانات المنتج إليك.', 'Your order is fulfilled after payment review and availability confirmation, then the product details are sent to you.')
    send(api, cid, tr(cid, '💳 <b>اختر طريقة الدفع</b>\n\n', '💳 <b>Choose payment method</b>\n\n') + summary(cid, pid) + '\n\n' + warning,
         kb([[btn(tr(cid, '🎟 كود خصم', '🎟 Discount code'), 'coupon:' + pid, style='primary'), btn(tr(cid, 'إزالة الخصم', 'Remove discount'), 'couponremove:' + pid)],
             [btn(tr(cid, 'المحفظة', 'Wallet'), 'paywallet:' + pid, ui_icon('pay_wallet'))],
             [btn('Crypto Pay', 'paycrypto:' + pid, ui_icon('pay_cryptopay'))],
             [btn('USDT — Bybit', 'paybybit:' + pid, ui_icon('pay_bybit'))]] + payment_methods.rows(sys.modules[__name__], pid) + [nav(cid, back(pid))]))


def payment(api, cid, pid, method):
    if not can_order(pid):
        payments(api, cid, pid)
        return
    if checkout_totals(cid, pid)[0] == 0:
        return pay_with_wallet(api, cid, pid)
    if method == 'bybit':
        send(api, cid, '🪙 <b>USDT — Bybit</b>\n\n' + summary(cid, pid),
             kb([[btn('Bybit Pay', 'bybitid:' + pid, ui_icon('pay_bybitid'))], [btn('USDT • TRON (TRC20)', 'trc20:' + pid, ui_icon('pay_trc20'))], [btn('USDT • BSC (BEP20)', 'bep20:' + pid, ui_icon('pay_bep20'))], nav(cid, 'buy:' + pid)]))
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
        sar, usd, _, _ = checkout_totals(cid, pid)
        with db() as conn:
            conn.execute('INSERT OR REPLACE INTO payment_quotes VALUES (?,?,?,?,?)', (cid, pid, method, str(usd), str(sar)))
        text += '\n\n' + tr(cid, 'بعد التحويل أرسل صورة الإثبات للمراجعة.', 'After transferring, submit a receipt photo for review.')
        rows.append([btn(tr(cid, '✅ تم التحويل', '✅ Payment sent'), f'receipt:{method}:{pid}')])
    else:
        text += '\n\n' + tr(cid, 'بيانات الدفع غير مكتملة. تواصل مع الدعم: ', 'Payment details are incomplete. Contact support: ') + SUPPORT
    send(api, cid, text, kb(rows + [nav(cid, 'buy:' + pid)]))


def pay_with_wallet(api, cid, pid):
    if not can_order(pid):
        return payments(api, cid, pid)
    cost, paid_usd, _, _ = checkout_totals(cid, pid)
    remaining = wallet_debit(cid, cost)
    if remaining is None:
        send(api, cid, tr(cid, 'رصيد المحفظة غير كافٍ.', 'Insufficient wallet balance.') +
             f'\n\n{tr(cid, "المطلوب", "Required")}: {cost:.2f} SAR\n{tr(cid, "الرصيد", "Balance")}: {wallet_balance(cid):.2f} SAR',
             kb([[btn(tr(cid, '➕ شحن المحفظة', '➕ Top up wallet'), 'wallet:topup')], nav(cid, 'buy:' + pid)]))
        return
    order_id = add_order(cid, pid, 'wallet', 'paid', usd=paid_usd, sar=cost)
    send(api, G['ADMIN_ID'], f'🛒 <b>طلب مدفوع من المحفظة #{order_id}</b>\n\n' + esc(name(pid, cid)) + f'\nالكمية: {product_options.snapshot(sys.modules[__name__], "order", order_id)}\nالسعر المدفوع: {paid_usd} USD\nالعميل: <code>{cid}</code>')
    if fulfill_paid_order(api, order_id):
        return
    send(api, cid, tr(cid, '✅ تم الدفع من المحفظة وإرسال الطلب للإدارة.', '✅ Paid from your wallet and the order was sent to administration.') + f'\n\n{tr(cid, "الرصيد المتبقي", "Remaining balance")}: {remaining:.2f} SAR', menu(cid))


def pay_with_crypto(api, cid, pid):
    if not can_order(pid):
        return payments(api, cid, pid)
    sar, usd, _, _ = checkout_totals(cid, pid)
    if usd == 0:
        return pay_with_wallet(api, cid, pid)
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
        row = conn.execute('SELECT pid,external_id,status,amount_usd FROM crypto_orders WHERE id=? AND cid=?', (order_id, cid)).fetchone()
    if not row:
        return products(api, cid)
    pid, invoice_id, status, paid_usd = row
    if status == 'paid':
        return send(api, cid, tr(cid, '✅ هذه الفاتورة مدفوعة وتم إرسال الطلب.', '✅ This invoice is paid and the order was sent.'), menu(cid))
    if not crypto_paid(invoice_id):
        return send(api, cid, tr(cid, 'لم يصل الدفع بعد. أكمل الفاتورة ثم أعد التحقق.', 'Payment has not arrived yet. Complete the invoice and check again.'),
                    kb([[btn(tr(cid, '🔄 تحقق مرة أخرى', '🔄 Check again'), 'checkorder:' + order_id)], nav(cid, 'buy:' + pid)]))
    with db() as conn:
        changed = conn.execute('UPDATE crypto_orders SET status="paid" WHERE id=? AND status="pending"', (order_id,)).rowcount
    if changed:
        saved_order_id = add_order(cid, pid, 'cryptopay', 'paid', usd=paid_usd, sar=(Decimal(paid_usd)*RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP), quantity=product_options.snapshot(sys.modules[__name__], 'crypto', order_id))
        send(api, G['ADMIN_ID'], f'💠 <b>طلب Crypto Pay مدفوع #{saved_order_id}</b>\n\n' + esc(name(pid, cid)) + f'\nالكمية: {product_options.snapshot(sys.modules[__name__], "order", saved_order_id)}\nالسعر المدفوع: {paid_usd} USD\nالعميل: <code>{cid}</code>')
        if fulfill_paid_order(api, saved_order_id):
            return
    send(api, cid, tr(cid, '✅ تم الدفع وإرسال الطلب للإدارة.', '✅ Payment received and the order was sent to administration.'), menu(cid))


def receipt_request(api, cid, pid, method):
    if not can_order(pid) or (method not in ('bank', 'bybitid', 'trc20', 'bep20') and not payment_methods.valid(sys.modules[__name__], method)):
        payments(api, cid, pid)
        return
    sar, usd, _, _ = checkout_totals(cid, pid)
    with db() as conn:
        quote = conn.execute('SELECT usd,sar FROM payment_quotes WHERE cid=? AND pid=? AND method=?', (cid, pid, method)).fetchone()
        if quote:
            usd, sar = quote
        conn.execute('INSERT OR REPLACE INTO receipts VALUES (?,?,?,?,?)', (cid, pid, method, str(usd), str(sar)))
    send(api, cid, tr(cid, '📸 أرسل صورة إثبات الدفع هنا. ستصل للإدارة للمراجعة.', '📸 Send your payment receipt photo here. It will be sent to the administrator for review.'), kb([[btn(tr(cid, '❌ إلغاء', '❌ Cancel'), 'cancel:' + pid)]]))


def receipt(api, message):
    if payment_methods.message(sys.modules[__name__], api, message):
        return True
    cid = message['chat']['id']
    if discounts.message(sys.modules[__name__], api, message):
        return True
    if handle_admin_photo(api, message):
        return True
    if handle_admin_text(api, message):
        return True
    if handle_admin_price(api, message):
        return True
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
        transfer_state = conn.execute('SELECT step,recipient,amount_sar FROM wallet_transfer_state WHERE cid=?', (cid,)).fetchone()
    if transfer_state:
        raw = (message.get('text') or '').strip()
        if raw.startswith('/'):
            with db() as conn:
                conn.execute('DELETE FROM wallet_transfer_state WHERE cid=?', (cid,))
            return False
        step, recipient, amount_sar = transfer_state
        if step == 'recipient':
            try:
                target = int(raw)
            except Exception:
                send(api, cid, tr(cid, 'أرسل رقم ID صحيح فقط.', 'Send a valid numeric user ID only.'))
                return True
            if target == cid:
                send(api, cid, tr(cid, 'لا يمكنك التحويل لنفس حسابك.', 'You cannot transfer to your own account.'))
                return True
            try:
                known_users = {int(x) for x in json.loads(USERS_PATH.read_text(encoding='utf-8'))}
            except Exception:
                known_users = set()
            if target not in known_users:
                send(api, cid, tr(cid, 'هذا المستخدم غير موجود داخل البوت.', 'This user is not registered in the bot.'))
                return True
            with db() as conn:
                conn.execute('UPDATE wallet_transfer_state SET step=?,recipient=? WHERE cid=?', ('amount', target, cid))
            send(api, cid, tr(cid, 'أرسل مبلغ التحويل بالدولار USD، مثال: 5', 'Send the transfer amount in USD, e.g. 5'))
            return True
        if step == 'amount':
            try:
                usd_value = Decimal(raw.replace(',', '.')).quantize(Decimal('0.01'))
            except Exception:
                send(api, cid, tr(cid, 'أرسل المبلغ كرقم فقط.', 'Send the amount as a number only.'))
                return True
            if usd_value <= 0:
                send(api, cid, tr(cid, 'المبلغ يجب أن يكون أكبر من صفر.', 'Amount must be greater than zero.'))
                return True
            sar_value = (usd_value * RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            if wallet_balance(cid) < sar_value:
                send(api, cid, tr(cid, 'رصيدك غير كافٍ.', 'Your balance is insufficient.'))
                return True
            with db() as conn:
                conn.execute('UPDATE wallet_transfer_state SET amount_sar=? WHERE cid=?', (str(sar_value), cid))
            send(api, cid,
                 tr(cid, 'تأكيد التحويل إلى المستخدم:', 'Confirm transfer to user:') + f' <code>{recipient}</code>\n<b>{usd_value:.2f} USD / {sar_value:.2f} SAR</b>',
                 kb([[btn(tr(cid, 'تأكيد التحويل', 'Confirm transfer'), f'wallettransferconfirm:{recipient}:{sar_value}', style='success')],
                     [btn(tr(cid, 'إلغاء', 'Cancel'), 'wallet', style='primary')]]))
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
    result = send(api, G['ADMIN_ID'], '🧾 <b>إثبات دفع جديد</b>\n\n' + esc(name(pid)) + f'\nالكمية: {product_options.snapshot(sys.modules[__name__], "receipt", cid)}\nالسعر عند الطلب: {sar} SAR / {usd} USD\nالطريقة: {esc(method)}\nالعميل: {esc(username)}\nID: <code>{cid}</code>')
    forwarded = api.call('forwardMessage', chat_id=G['ADMIN_ID'], from_chat_id=cid, message_id=message['message_id']) if result else None
    if not forwarded:
        send(api, cid, tr(cid, 'تعذر إرسال الإثبات للإدارة. أعد المحاولة أو تواصل مع ', 'Could not forward the receipt. Retry or contact ') + SUPPORT)
        return True
    add_order(cid, pid, method, 'review', usd=usd, sar=sar)
    with db() as conn:
        conn.execute('DELETE FROM payment_quotes WHERE cid=? AND pid=? AND method=?', (cid, pid, method))
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
    if payment_methods.action(sys.modules[__name__], api, cid, value):
        return
    if discounts.action(sys.modules[__name__], api, cid, value):
        return
    prefix, _, arg = value.partition(':')
    arg = LEGACY.get(arg, arg)
    if prefix in ('home', 'enter_store'):
        reset_navigation_state(cid)
        home(api, cid)
    elif prefix == 'start':
        start(api, cid)
    elif prefix == 'products':
        products(api, cid)
    elif prefix == 'referrals':
        referral_page(api, cid)
    elif prefix == 'product':
        category(api, cid, arg)
    elif prefix in ('item', 'claude'):
        item(api, cid, arg)
    elif value in LEGACY:
        item(api, cid, LEGACY[value])
    elif prefix == 'catdesc':
        admin_category_description(api, cid, arg)
    elif prefix == 'catdesclang':
        lang, _, pid = arg.partition(':')
        admin_category_description(api, cid, pid, lang)
    elif prefix == 'admin':
        if arg == 'categorydesc': admin_category_description(api, cid)
        elif arg == 'orders': admin_orders(api, cid)
        elif arg == 'activity': admin_activity(api, cid)
        elif arg == 'stats': admin_stats(api, cid)
        elif arg == 'icons': admin_icons(api, cid)
        elif arg == 'prices': admin_prices(api, cid)
        elif arg == 'photos': admin_photo_menu(api, cid)
        elif arg == 'editname': admin_text_menu(api, cid, 'name')
        elif arg == 'editdesc': admin_text_menu(api, cid, 'description')
        elif arg == 'stock': admin_stock(api, cid)
        elif arg == 'info': admin_info_menu(api, cid)
        elif arg == 'addproduct': begin_add_product(api, cid)
        elif arg == 'myproducts': admin_products_page(api, cid)
        elif arg == 'cancelproduct' and cid == G['ADMIN_ID']:
            with db() as conn: conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
            admin_panel(api, cid)
        elif arg == 'broadcast' and cid == G['ADMIN_ID']:
            BROADCAST_PENDING.add(cid)
            send(api, cid, '📢 <b>إرسال رسالة للجميع</b>\n\nأرسل الآن الرسالة التي تريد إرسالها لجميع مستخدمي البوت.\nيمكنك إرسال نص أو صورة مع تعليق.',
                 kb([[btn('❌ إلغاء', 'admin:broadcast_cancel')]]))
        elif arg == 'broadcast_cancel' and cid == G['ADMIN_ID']:
            BROADCAST_PENDING.discard(cid)
            admin_panel(api, cid)
        else: admin_panel(api, cid)
    elif prefix == 'orderdeliver' and cid == G['ADMIN_ID']:
        oid = arg
        with db() as conn:
            row = conn.execute('SELECT cid,status,pid FROM orders WHERE id=?', (oid,)).fetchone()
        if not row:
            return send(api, cid, '⚠️ الطلب غير موجود.')
        customer, status, pid = row
        if status == 'delivered':
            return send(api, cid, '✅ هذا الطلب تم تسليمه مسبقًا.')
        if status != 'paid':
            return send(api, cid, '⚠️ لازم يكون الطلب مدفوع قبل التسليم.')
        G['PENDING_ADMIN_DELIVERY'][G['ADMIN_ID']]={'customer':customer,'order_id':oid}
        return send(api, cid, f'📤 <b>تسليم الطلب #{esc(oid)}</b>\n{esc(name(pid, cid))}\n\nأرسل الآن الرسالة أو الكود أو الصورة أو الملف، وسيتم إرساله مباشرة للعميل وتسجيل الطلب كمُسلّم.')
    elif prefix == 'payreview' and cid == G['ADMIN_ID']:
        decision, _, oid = arg.partition(':')
        with db() as conn:
            row = conn.execute('SELECT cid,status FROM orders WHERE id=?', (oid,)).fetchone()
            if not row or row[1] != 'review':
                return send(api, cid, '⚠️ الطلب غير موجود أو تمت معالجته مسبقاً.')
            customer = row[0]
            if decision == 'accept':
                conn.execute('UPDATE orders SET status="paid" WHERE id=? AND status="review"', (oid,))
                if fulfill_paid_order(api, oid):
                    return send(api, cid, f'✅ تم قبول الطلب <b>#{esc(oid)}</b> وبدأ التنفيذ التلقائي عبر المورد.')
                G['PENDING_ADMIN_DELIVERY'][G['ADMIN_ID']]={'customer':customer,'order_id':oid}
                send(api, customer, '✅ <b>تم قبول الدفع.</b>\n\nسيتم إرسال طلبك لك قريباً.')
                return send(api, cid, f'✅ تم قبول الطلب <b>#{esc(oid)}</b>.\n\n📤 أرسل الآن أي رسالة أو صورة أو ملف تريد إرساله للعميل.\nسيتم إرسال <b>الرسالة التالية فقط</b> له مباشرة.')
            conn.execute('UPDATE orders SET status="rejected" WHERE id=? AND status="review"', (oid,))
        send(api, customer, '❌ <b>تم رفض إثبات الدفع.</b>\n\nيرجى إعادة المحاولة أو التواصل مع الدعم.')
        send(api, cid, f'❌ تم رفض الطلب <b>#{esc(oid)}</b> وإبلاغ العميل.')
    elif prefix == 'infocat':
        admin_info_menu(api, cid, arg)
    elif prefix == 'infopick':
        admin_info_editor(api, cid, arg)
    elif prefix == 'infotoggle' and cid == G['ADMIN_ID']:
        field, _, pid = arg.partition(':'); sp, ss, sw, w = info_display(pid)
        if field == 'price': sp = 0 if sp else 1
        elif field == 'stock': ss = 0 if ss else 1
        elif field == 'warranty': sw = 0 if sw else 1
        with db() as conn: conn.execute('INSERT OR REPLACE INTO product_info_display VALUES (?,?,?,?,?)', (pid, sp, ss, sw, w))
        admin_info_editor(api, cid, pid)
    elif prefix == 'infowarranty' and cid == G['ADMIN_ID']:
        with db() as conn: conn.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)', (cid, 'info_warranty', arg))
        send(api, cid, '✏️ أرسل نص الضمان لهذا المنتج، مثال: <b>15 يوم</b>.', kb([[btn('إلغاء', 'admin:info')]]))
    elif prefix == 'infoicon' and cid == G['ADMIN_ID']:
        field, _, pid = arg.partition(':')
        if field in ('price','stock','warranty'):
            with db() as conn: conn.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)',(cid,'info_icon',field+':'+pid))
            target_text = 'لجميع المنتجات' if pid == '__global__' else 'لهذا المنتج'
            cancel_action = 'admin:info' if pid == '__global__' else 'infopick:'+pid
            send(api,cid,'أرسل الآن الأيقونة المتحركة '+target_text+'.',kb([[btn('❌ إلغاء',cancel_action)]]))
    elif prefix == 'pandorabrowse' and cid == G['ADMIN_ID']:
        pandora_catalog_open(api, cid, arg, 0)
    elif prefix == 'pandorapage' and cid == G['ADMIN_ID']:
        pandora_catalog_categories(api, cid)
    elif prefix == 'pandoracategories' and cid == G['ADMIN_ID']:
        pandora_catalog_categories(api, cid)
    elif prefix == 'pandoracat' and cid == G['ADMIN_ID']:
        cidx, _, page = arg.partition(':')
        pandora_catalog_page(api, cid, cidx or 0, page or 0)
    elif prefix == 'pandorap' and cid == G['ADMIN_ID']:
        pandora_catalog_product(api, cid, arg)
    elif prefix == 'pandorav' and cid == G['ADMIN_ID']:
        pidx, _, vidx = arg.partition(':')
        pandora_catalog_save(api, cid, pidx, vidx)
    elif prefix == 'suppliercat':
        supplier_api_menu(api, cid, arg)
    elif prefix == 'supplierpick':
        supplier_api_editor(api, cid, arg)
    elif prefix == 'supplierpandora' and cid == G['ADMIN_ID']:
        pid = LEGACY.get(arg, arg)
        endpoint, api_key, service_id, enabled, provider, variant_id = supplier_api_row(pid)
        endpoint = 'https://api.pandoradigital.shop/api/v1'
        provider = 'pandora'
        with db() as conn:
            conn.execute('INSERT INTO supplier_api(pid,endpoint,api_key,service_id,enabled,provider,variant_id) VALUES (?,?,?,?,?,?,?) ON CONFLICT(pid) DO UPDATE SET endpoint=excluded.endpoint,provider=excluded.provider',
                         (pid, endpoint, api_key, service_id, enabled, provider, variant_id))
        supplier_api_editor(api, cid, pid)
    elif prefix == 'supplierset' and cid == G['ADMIN_ID']:
        field, _, pid = arg.partition(':')
        if field in ('service', 'variant'):
            action_name = {'service':'supplier_service','variant':'supplier_variant'}[field]
            with db() as conn:
                conn.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)', (cid, action_name, pid))
            prompt = {'service':'أرسل Product ID لدى المورد.','variant':'أرسل Variant ID لدى المورد.'}[field]
            send(api, cid, '🔌 <b>' + esc(name(pid, cid)) + '</b>\n\n' + prompt, kb([[btn('❌ إلغاء', 'supplierpick:' + pid)]]))
    elif prefix == 'suppliertest' and cid == G['ADMIN_ID']:
        supplier_test_connection(api, cid, arg)
    elif prefix == 'suppliertoggle' and cid == G['ADMIN_ID']:
        pid = LEGACY.get(arg, arg)
        endpoint, api_key, service_id, enabled, provider, variant_id = supplier_api_row(pid)
        missing = []
        if not endpoint: missing.append('رابط API')
        if not api_key: missing.append('مفتاح API')
        if not service_id: missing.append('Product ID')
        if not variant_id: missing.append('Variant ID')
        if missing:
            send(api, cid, '⚠️ باقي قبل التفعيل: <b>' + esc(' + '.join(missing)) + '</b>.')
            supplier_api_editor(api, cid, pid)
        else:
            with db() as conn:
                conn.execute('INSERT INTO supplier_api(pid,endpoint,api_key,service_id,enabled,provider,variant_id) VALUES (?,?,?,?,?,?,?) ON CONFLICT(pid) DO UPDATE SET enabled=excluded.enabled',
                             (pid, endpoint, api_key, service_id, 0 if enabled else 1, provider, variant_id))
            supplier_api_editor(api, cid, pid)
    elif prefix == 'supplierdelete' and cid == G['ADMIN_ID']:
        pid = LEGACY.get(arg, arg)
        with db() as conn:
            conn.execute('DELETE FROM supplier_api WHERE pid=?', (pid,))
        supplier_api_editor(api, cid, pid)
    elif prefix == 'mycategory':
        admin_category_detail(api, cid, arg)
    elif prefix == 'myproduct':
        admin_product_detail(api, cid, arg)
    elif prefix == 'myproducttoggle':
        toggle_admin_product(api, cid, arg)
    elif prefix == 'myproductdelete':
        delete_admin_product(api, cid, arg)
    elif prefix == 'stockcat':
        admin_stock(api, cid, arg)
    elif prefix == 'stockpick':
        stock_editor(api, cid, arg)
    elif prefix == 'stockset':
        value, _, pid = arg.partition(':')
        if value in ('0', '1'):
            stock_editor(api, cid, pid, value)
    elif prefix == 'txtcat':
        field, _, category_id = arg.partition(':')
        admin_text_menu(api, cid, field, category_id)
    elif prefix == 'txtpick':
        field, _, pid = arg.partition(':')
        admin_text_editor(api, cid, field, pid)
    elif prefix == 'txtedit':
        parts = arg.split(':', 2)
        if len(parts) == 3:
            field, lang, pid = parts
            admin_text_editor(api, cid, field, pid, lang)
    elif prefix == 'photocat':
        admin_photo_menu(api, cid, arg)
    elif prefix == 'photopick':
        admin_photo_editor(api, cid, arg)
    elif prefix == 'photodel':
        admin_photo_editor(api, cid, arg, delete=True)
    elif prefix == 'pricecat':
        admin_prices(api, cid, arg)
    elif prefix == 'pricepick':
        price_editor(api, cid, arg)
    elif prefix == 'priceedit':
        currency, _, pid = arg.partition(':')
        price_editor(api, cid, pid, currency)
    elif prefix == 'iconmenu':
        if arg == 'products': admin_icon_products(api, cid)
        elif arg == 'categories': admin_icon_categories(api, cid)
        elif arg == 'buttons': admin_icon_buttons(api, cid)
        else: admin_icons(api, cid)
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
            stored_value = arg if prefix == 'setlang' else 'USD'
            conn.execute(f'UPDATE preferences SET {column}=? WHERE cid=?', (stored_value, cid))
        home(api, cid)
    elif prefix in ('buy', 'cancel'):
        with db() as conn:
            conn.execute('DELETE FROM receipts WHERE cid=?', (cid,))
        if prefix == 'buy': payments(api, cid, arg)
        elif arg in VARIANTS: item(api, cid, arg)
        else: category(api, cid, arg)
    elif prefix == 'wallet':
        if arg == 'topup': wallet_amounts(api, cid)
        elif arg == 'transfer': wallet_transfer_begin(api, cid)
        else: wallet(api, cid)
    elif prefix == 'wallettransferconfirm':
        recipient, _, amount_sar = arg.partition(':')
        wallet_transfer_confirm(api, cid, recipient, amount_sar)
    elif prefix == 'topupmanual':
        method_id, _, value = arg.partition(':')
        wallet_manual_topup(api, cid, method_id, value)
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
        send(api, cid, 'Support:' + SUPPORT, menu(cid))
    elif prefix == 'api':
        send(api, cid, tr(cid, '🔗 إعدادات API المتجر غير مفعّلة حاليًا.', '🔗 Store API settings are not active yet.'), menu(cid))
    elif prefix == 'warranty':
        send(api, cid, tr(cid, '🛡 تختلف شروط الضمان حسب المنتج. راجع وصفه قبل الطلب. للاستفسار: ', '🛡 Warranty terms vary by product. Read its description before ordering. Questions: ') + SUPPORT, menu(cid))
    else:
        products(api, cid)


def install(namespace):
    global G
    G = namespace
    try:
        __import__('threading').Thread(target=pandora_startup_probe, daemon=True).start()
    except Exception:
        pass
    apply_icon_overrides()
    namespace.update({'show_start': start, 'show_home': home, 'show_products': products,
                      'show_product': category, 'show_claude_product': item, 'handle_action': action, 'action': action,
                      'handle_receipt': receipt, 'order_name': name, 'home_keyboard': menu,
                      'broadcast_new_products': broadcast_new_products,
                      'handle_admin_product': handle_admin_product, 'handle_info_warranty': handle_info_warranty, 'handle_info_icon': handle_info_icon})
    menu_actions = namespace.setdefault('MENU_ACTIONS', namespace.get('MENU', {}))
    menu_actions.update({'🚀 ابدأ': 'start', '🚀 Start': 'start', '🛍 المنتجات': 'products',
                         '🛍 Products': 'products', '⚡ VEXA VOLT': 'support', '⚡ VEXA VOLT': 'support',
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
,'').replace(',','.')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                if margin < 0: raise ValueError()
                sale = pandora_set_margin(pid, margin)
            except Exception:
                send(api, cid, 'أرسل هامش ربح صحيح، مثال: <code>2.00</code>.')
                return True
            with db() as conn:
                conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
            send(api, cid, '✅ تم حفظ هامش الربح.' + (f'\nسعر البيع الجديد: <b>${sale:.2f}</b>' if sale is not None else ''))
            supplier_api_editor(api, cid, pid)
            return True
        if action_name == 'supplier_endpoint':
            if not (raw.startswith('https://') or raw.startswith('http://')):
                send(api, cid, 'أرسل رابط API يبدأ بـ <code>https://</code> أو <code>http://</code>.')
                return True
            endpoint = ''.join(ch for ch in raw[:500].strip() if ord(ch) < 128)
        elif action_name == 'supplier_key':
            api_key = ''.join(ch for ch in raw[:500].strip() if ord(ch) < 128)
        elif action_name == 'supplier_service':
            service_id = raw[:200]
        else:
            variant_id = raw[:200]
        with db() as conn:
            conn.execute('INSERT INTO supplier_api(pid,endpoint,api_key,service_id,enabled,provider,variant_id) VALUES (?,?,?,?,?,?,?) ON CONFLICT(pid) DO UPDATE SET endpoint=excluded.endpoint,api_key=excluded.api_key,service_id=excluded.service_id,enabled=excluded.enabled,provider=excluded.provider,variant_id=excluded.variant_id',
                         (LEGACY.get(pid,pid), endpoint, api_key, service_id, enabled, provider, variant_id))
            conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
        send(api, cid, '✅ تم حفظ إعداد API.')
        supplier_api_editor(api, cid, pid)
        return True
    with db() as conn:
        qty_row = conn.execute("SELECT value FROM admin_state WHERE cid=? AND action='stock_quantity'", (cid,)).fetchone()
    if qty_row:
        raw_qty = (message.get('text') or '').strip().translate(str.maketrans('٠١٢٣٤٥٦٧٨٩', '0123456789'))
        if raw_qty.startswith('/'):
            with db() as conn:
                conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
            return False
        try:
            qty = int(raw_qty)
            if qty < 0 or qty > 1000000:
                raise ValueError()
        except Exception:
            send(api, cid, 'أرسل كمية صحيحة كرقم، مثال: <code>4</code>.')
            return True
        pid = LEGACY.get(qty_row[0], qty_row[0])
        with db() as conn:
            conn.execute('INSERT OR REPLACE INTO product_stock_overrides VALUES (?,?)', (pid, qty))
            if custom_product(pid):
                conn.execute('UPDATE admin_products SET stock=? WHERE pid=?', (qty, pid))
            conn.execute('INSERT OR REPLACE INTO product_availability VALUES (?,?)', (pid, 1 if qty > 0 else 0))
            if custom_product(pid):
                conn.execute('UPDATE admin_products SET available=? WHERE pid=?', (1 if qty > 0 else 0, pid))
            conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
        send(api, cid, '✅ تم تحديث الكمية إلى <b>' + esc(qty) + '</b>.')
        stock_editor(api, cid, pid)
        return True
    with db() as conn:
        row = conn.execute("SELECT value FROM admin_state WHERE cid=? AND action='product_text'", (cid,)).fetchone()
    if not row:
        return False
    text = (message.get('text') or '').strip()
    if text.startswith('/') or text in G.get('MENU', {}):
        with db() as conn:
            conn.execute("DELETE FROM admin_state WHERE cid=? AND action='product_text'", (cid,))
        return False
    pid, field, lang = json.loads(row[0])
    limit = 120 if field == 'name' else 1500
    if not text or len(text) > limit or (field == 'name' and '\n' in text):
        send(api, cid, f'أرسل نصًا غير فارغ لا يتجاوز {limit} حرفًا.' + (' الاسم يكون في سطر واحد.' if field == 'name' else ''), kb([[btn('إلغاء', 'admin')]]))
        return True
    translated_en = auto_translate(text, 'en')[:limit] if lang == 'ar' else None
    with db() as conn:
        conn.execute('INSERT OR REPLACE INTO product_text VALUES (?,?,?,?)', (pid, field, lang, text))
        if translated_en:
            conn.execute('INSERT OR REPLACE INTO product_text VALUES (?,?,?,?)', (pid, field, 'en', translated_en))
        if field == 'description':
            conn.execute('INSERT OR REPLACE INTO description_emoji VALUES (?,?,?,?)',
                         (pid, lang, text, description_message_html(message, text)))
            if translated_en:
                conn.execute('DELETE FROM description_emoji WHERE pid=? AND lang=?', (pid, 'en'))
        conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
    confirmation = '✅ تم حفظ ' + ('اسم المنتج' if field == 'name' else 'وصف المنتج')
    if translated_en:
        confirmation += '\n🌐 وتم تحديث النسخة الإنجليزية تلقائيًا.'
    confirmation += '\n\n' + (description_message_html(message, text) if field == 'description' else esc(text))
    send(api, cid, confirmation, kb([[btn('تعديل منتج آخر', 'admin:editname' if field == 'name' else 'admin:editdesc')], [btn('لوحة الإدارة', 'admin')]]))
    return True


def saved_product_photo(pid):
    with db() as conn:
        row = conn.execute('SELECT file_id FROM product_photos WHERE pid=?', (LEGACY.get(pid, pid),)).fetchone()
    return row[0] if row else None


def admin_photo_menu(api, cid, category_id=None):
    if cid != G['ADMIN_ID']:
        return
    with db() as conn:
        conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
        cats = [(pid, p['name']) for pid, p in G['PRODUCTS'].items()] + conn.execute('SELECT cid,name FROM admin_categories').fetchall()
        custom_ids = [r[0] for r in conn.execute('SELECT pid FROM admin_products WHERE category_id=?', (category_id,)).fetchall()] if category_id else []
    if category_id is None:
        rows = [[btn(label, 'photocat:' + pid)] for pid, label in cats]
    else:
        ids = admin_category_product_ids(category_id)
        rows = [[btn(name(pid, cid), 'photopick:' + pid)] for pid in ids]
    send(api, cid, '🖼️ صورة المنتج\nاختر القسم ثم المنتج:', kb(rows + [[btn('↩️ لوحة الإدارة', 'admin')]]))


def admin_photo_editor(api, cid, pid, delete=False):
    if cid != G['ADMIN_ID'] or (pid not in VARIANTS and pid not in G['PRODUCTS'] and not custom_product(pid)):
        return
    BROADCAST_PENDING.discard(cid)
    with db() as conn:
        conn.execute('DELETE FROM custom_topup_state WHERE cid=?', (cid,))
        if delete:
            conn.execute('INSERT OR REPLACE INTO product_photos VALUES (?,?)', (pid, ''))
            conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
        else:
            conn.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)', (cid, 'product_photo', pid))
    parent = 'myproduct:' + pid if custom_product(pid) else 'admin:photos'
    if delete:
        return send(api, cid, '✅ تم حذف صورة المنتج. سيظهر دون صورة عند فتحه مجددًا.', kb([[btn('🖼️ إضافة صورة', 'photopick:' + pid)], [btn('↩️ رجوع', parent)]]))
    send(api, cid, '<b>' + esc(name(pid, cid)) + '</b>\n\nأرسل الصورة هنا كصورة في تيليجرام لإضافتها أو استبدال الصورة الحالية.', kb([[btn('🗑 حذف الصورة', 'photodel:' + pid, style='danger')], [btn('↩️ رجوع', parent)]]))


def handle_admin_photo(api, message):
    cid = message.get('chat', {}).get('id')
    if cid != G.get('ADMIN_ID'):
        return False
    with db() as conn:
        row = conn.execute("SELECT value FROM admin_state WHERE cid=? AND action='product_photo'", (cid,)).fetchone()
    if not row:
        return False
    text = message.get('text', '')
    if text.startswith('/') or text in G.get('MENU', {}):
        with db() as conn:
            conn.execute("DELETE FROM admin_state WHERE cid=? AND action='product_photo'", (cid,))
        return False
    photos = message.get('photo') or []
    file_id = photos[-1].get('file_id') if photos else None
    if not file_id:
        send(api, cid, 'أرسل الصورة كصورة في تيليجرام، وليس كملف أو نص.', kb([[btn('إلغاء', 'admin')]]))
        return True
    with db() as conn:
        conn.execute('INSERT OR REPLACE INTO product_photos VALUES (?,?)', (row[0], file_id))
        conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
    parent = 'myproduct:' + row[0] if custom_product(row[0]) else 'admin:photos'
    send(api, cid, '✅ تم حفظ صورة المنتج.', kb([[btn('👁 معاينة المنتج', ('item:' if row[0] in VARIANTS or custom_product(row[0]) else 'product:') + row[0])], [btn('↩️ رجوع للمنتج', parent)], [btn('🖼️ منتج آخر', 'admin:photos')], [btn('لوحة الإدارة', 'admin')]]))
    return True


def admin_prices(api, cid, category_id=None):
    if cid != G['ADMIN_ID']:
        return
    with db() as conn:
        conn.execute("DELETE FROM admin_state WHERE cid=? AND action IN ('price', 'category_description')", (cid,))
    if category_id is None:
        with db() as conn:
            custom_categories = conn.execute('SELECT cid,name FROM admin_categories ORDER BY rowid').fetchall()
        rows = [[btn(p['name'], 'pricecat:' + pid)] for pid, p in G['PRODUCTS'].items()]
        rows += [[btn(label, 'pricecat:' + pid)] for pid, label in custom_categories]
    else:
        ids = admin_category_product_ids(category_id)
        rows = [[btn(name(pid, cid) + ' | ' + price(cid, pid, 'USD'), 'pricepick:' + pid)] for pid in ids]
    send(api, cid, '✏️ اختر القسم أو المنتج لتعديل سعر البيع:', kb(rows + [[btn('↩️ لوحة الإدارة', 'admin')]]))


def price_editor(api, cid, pid, currency=None):
    if cid != G['ADMIN_ID'] or (pid not in VARIANTS and pid not in G['PRODUCTS'] and not custom_product(pid)):
        return
    BROADCAST_PENDING.discard(cid)
    with db() as conn:
        conn.execute('DELETE FROM custom_topup_state WHERE cid=?', (cid,))
        conn.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)', (cid, 'price', json.dumps([pid, 'USD'])))
    send(api, cid,
         esc(name(pid, cid)) + '\nالسعر الحالي: <b>' + price(cid, pid, 'USD') +
         '</b>\n\nأرسل سعر البيع النهائي بالدولار USD.\nمثال: <code>6.35</code>',
         kb([[btn('إلغاء', 'admin:prices')]]))


def handle_admin_price(api, message):
    cid = message.get('chat', {}).get('id')
    if cid != G.get('ADMIN_ID'):
        return False
    with db() as conn:
        state = conn.execute("SELECT value FROM admin_state WHERE cid=? AND action='price'", (cid,)).fetchone()
    if not state:
        return False
    raw = (message.get('text') or '').strip()
    if raw.startswith('/') or raw in G.get('MENU', {}):
        with db() as conn:
            conn.execute("DELETE FROM admin_state WHERE cid=? AND action='price'", (cid,))
        return False
    raw = raw.translate(str.maketrans('٠١٢٣٤٥٦٧٨٩٫', '0123456789.')).replace(',', '.')
    try:
        value = Decimal(raw)
        if not value.is_finite() or value <= 0 or value > 1000000 or value != value.quantize(Decimal('0.01')):
            raise ValueError()
    except Exception:
        send(api, cid, 'أرسل سعرًا أكبر من صفر، برقم فقط وبحد أقصى منزلتين عشريتين. مثال: 19.50', kb([[btn('إلغاء', 'admin:prices')]]))
        return True
    pid, _currency = json.loads(state[0])
    usd = value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    sar = (usd * Decimal('3.75')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    with db() as conn:
        conn.execute('INSERT OR REPLACE INTO product_prices VALUES (?,?,?)', (pid, str(usd), 'USD'))
        conn.execute('UPDATE admin_products SET price_usd=?,price_sar=? WHERE pid=?', (str(usd), str(sar), pid))
        conn.execute("DELETE FROM admin_state WHERE cid=? AND action='price'", (cid,))
    send(api, cid, '✅ تم حفظ سعر ' + esc(name(pid, cid)) + '\n💵 <b>' + price(cid, pid, 'USD') + '</b>\n\nافتح قائمة المنتجات من جديد لرؤية السعر الجديد.', kb([[btn('تعديل منتج آخر', 'admin:prices')], [btn('لوحة الإدارة', 'admin')]]))
    return True



def admin_products_page(api, cid):
    if cid != G['ADMIN_ID']:
        return home(api, cid)
    with db() as conn:
        custom_categories = conn.execute('SELECT cid,name FROM admin_categories ORDER BY rowid DESC').fetchall()
    seen = set()
    categories = []
    for category_id in G['PRODUCTS']:
        if category_id not in seen:
            categories.append((category_id, category_label(category_id, cid)))
            seen.add(category_id)
    for category_id, category_name in custom_categories:
        if category_id not in seen:
            categories.append((category_id, category_name))
            seen.add(category_id)
    buttons = [[btn('📁 ' + esc(category_name), 'mycategory:' + category_id)] for category_id, category_name in categories]
    text = '📦 <b>منتجاتي</b>\n\nاختر القسم، ثم المنتج الذي تريد إدارته أو ربطه بالـ API:'
    send(api, cid, text, kb(buttons + [[btn('➕ إضافة قسم ومنتجات', 'admin:addproduct', style='success')], [btn('↩️ لوحة الإدارة', 'admin')]]))


def admin_category_detail(api, cid, category_id):
    if cid != G['ADMIN_ID']:
        return home(api, cid)
    ids = admin_category_product_ids(category_id)
    buttons = []
    for pid in ids:
        cp = custom_product(pid)
        if cp:
            _pid, product_name, _description, _price_usd, available, _cat, stock = cp
            qty = int(stock or 0)
            is_available = bool(available and qty > 0)
        else:
            product_name = name(pid, cid)
            qty = product_stock(pid)
            is_available = bool(in_stock(pid))
        label = ('✅ ' if is_available else '🔴 ') + compact_name(pid, cid) + ' • ' + price(cid, pid, 'USD') + ' • ' + compact_stock(qty)
        buttons.append([btn(label, 'myproduct:' + pid, style='success' if is_available else 'danger'),
                        btn('🔌 API', 'supplierpick:' + pid)])
    title = category_label(category_id, cid)
    extra = [[btn('➕ إضافة منتج لهذا القسم', 'addtocategory:' + category_id, style='success')]]
    send(api, cid, '📁 <b>' + esc(title) + '</b>\n\nكل المنتجات داخل القسم:', kb(buttons + extra + [[btn('↩️ منتجاتي', 'admin:myproducts')]]))


def begin_add_product(api, cid):
    if cid != G['ADMIN_ID']:
        return home(api, cid)
    BROADCAST_PENDING.discard(cid)
    with db() as conn:
        conn.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)', (cid, 'add_product_category', '{}'))
    send(api, cid, '➕ <b>إضافة قسم ومنتجات</b>\n\n1️⃣ أرسل <b>اسم القسم</b> الذي سيظهر للعملاء.\nمثال: <code>ChatGPT</code>', kb([[btn('❌ إلغاء', 'admin:cancelproduct')]]))


def add_to_category(api, cid, category_id=None):
    if cid != G['ADMIN_ID']:
        return home(api, cid)
    reset_navigation_state(cid)
    BROADCAST_PENDING.discard(cid)
    if category_id is None:
        with db() as conn:
            custom = conn.execute('SELECT cid,name FROM admin_categories ORDER BY rowid').fetchall()
        categories = list(G['PRODUCTS']) + [row[0] for row in custom]
        return send(api, cid, '📁 اختر القسم الذي تريد إضافة منتج جديد إليه:',
                    kb([[btn(('▶️ YouTube' if key == 'youtube' else name(key, cid)), 'addtocategory:' + key)] for key in categories] +
                       [[btn('↩️ لوحة التحكم', 'admin')]]))
    if category_id not in G['PRODUCTS'] and not custom_category(category_id):
        return add_to_category(api, cid)
    payload = {'category_id': category_id, 'category_name': ('YouTube' if category_id == 'youtube' else name(category_id, cid)),
               'count': 1, 'index': 0, 'products': []}
    with db() as conn:
        conn.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)',
                     (cid, 'add_product_name', json.dumps(payload, ensure_ascii=False)))
    send(api, cid, '➕ إضافة منتج داخل <b>' + esc(payload['category_name']) + '</b>\n\nأرسل اسم المنتج الجديد <b>بالعربي</b>:',
         kb([[btn('❌ إلغاء', 'admin:cancelproduct')]]))


def show_extended_category(api, cid, category_id):
    """Include owner-added products alongside a built-in category's products."""
    if category_id not in G['PRODUCTS']:
        return False
    with db() as conn:
        custom = [row[0] for row in conn.execute('SELECT pid FROM admin_products WHERE category_id=? ORDER BY rowid', (category_id,))]
    if not custom:
        return False
    variants = [pid for pid, v in VARIANTS.items() if v['category'] == category_id]
    originals = variants or ([category_id] if amount(category_id, 'SAR') is not None else [])
    rows = [[btn(compact_name(pid, cid) + ' | 💰 ' + price(cid, pid) + ' | ' + compact_stock(product_stock(pid)), 'options:' + pid,
                 ui_icon(pid), style='danger' if not in_stock(pid) else None)]
            for pid in originals + custom if product_visible(pid)]
    send(api, cid, category_heading(category_id, cid), kb(rows + [nav(cid)]))
    return True


def handle_admin_product(api, message):
    cid = message.get('chat', {}).get('id')
    if cid != G.get('ADMIN_ID'):
        return False
    with db() as conn:
        state = conn.execute('SELECT action,value FROM admin_state WHERE cid=?', (cid,)).fetchone()
    if not state or not state[0].startswith('add_product_'):
        return False
    raw = (message.get('text') or '').strip()
    if not raw:
        send(api, cid, 'أرسل نصًا للمتابعة.')
        return True
    action_name, payload_raw = state
    try:
        payload = json.loads(payload_raw or '{}')
    except Exception:
        payload = {}

    if action_name == 'add_product_category':
        payload = {'category_name': raw[:80], 'products': [], 'index': 0}
        next_action = 'add_product_count'
        prompt = '2️⃣ كم <b>عدد المنتجات</b> التي تريد إضافتها داخل قسم <b>' + esc(payload['category_name']) + '</b>؟\nمثال: <code>4</code>'
    elif action_name == 'add_product_count':
        normalized = raw.translate(str.maketrans('٠١٢٣٤٥٦٧٨٩', '0123456789'))
        try:
            count = int(normalized)
            if count < 1 or count > 50:
                raise ValueError()
        except Exception:
            send(api, cid, 'أرسل عددًا من <b>1 إلى 50</b>.')
            return True
        payload['count'] = count
        payload['index'] = 0
        next_action = 'add_product_name'
        prompt = '3️⃣ أرسل <b>اسم المنتج 1 من ' + str(count) + '</b>. سيتم إنشاء الإنجليزية تلقائيًا.'
    elif action_name == 'add_product_name':
        payload['current'] = {'name': raw[:100]}
        next_action = 'add_product_desc'
        prompt = '📝 أرسل <b>وصف المنتج</b> لـ <b>' + esc(payload['current']['name']) + '</b>. سيتم إنشاء الإنجليزية تلقائيًا.'
    elif action_name == 'add_product_desc':
        payload['current']['description'] = raw[:1500]
        payload['current']['description_html'] = description_message_html(message, raw[:1500])
        payload['current']['name_en'] = auto_translate(payload['current']['name'], 'en')[:100]
        payload['current']['description_en'] = auto_translate(payload['current']['description'], 'en')[:1500]
        next_action = 'add_product_price'
        prompt = '✅ تم إنشاء النسخة الإنجليزية تلقائيًا.\n\n💵 أرسل <b>السعر بالدولار USD</b>.\nمثال: <code>5.36</code>'
    elif action_name == 'add_product_price':
        normalized = raw.replace('$','').strip().translate(str.maketrans('٠١٢٣٤٥٦٧٨٩٫', '0123456789.')).replace(',', '.')
        try:
            value = Decimal(normalized).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            if value <= 0:
                raise ValueError()
        except Exception:
            send(api, cid, 'السعر غير صحيح. أرسل رقمًا بالدولار مثل <code>5.36</code>.')
            return True
        payload['current']['price_usd'] = str(value)
        next_action = 'add_product_stock'
        prompt = '📦 كم <b>الكمية المتوفرة</b> من هذا المنتج؟\nمثال: <code>10</code>'
    elif action_name == 'add_product_stock':
        normalized = raw.translate(str.maketrans('٠١٢٣٤٥٦٧٨٩', '0123456789'))
        try:
            stock = int(normalized)
            if stock < 0 or stock > 1000000:
                raise ValueError()
        except Exception:
            send(api, cid, 'أرسل كمية صحيحة مثل <code>10</code>.')
            return True
        payload['current']['stock'] = stock
        payload['products'].append(payload.pop('current'))
        payload['index'] = int(payload.get('index', 0)) + 1
        if payload['index'] < int(payload['count']):
            next_action = 'add_product_name'
            prompt = '✅ تم حفظ بيانات المنتج ' + str(payload['index']) + '.\n\nأرسل <b>اسم المنتج ' + str(payload['index'] + 1) + ' من ' + str(payload['count']) + '</b>. سيتم إنشاء الإنجليزية تلقائيًا.'
        else:
            lines = ['✅ <b>راجع القسم قبل الحفظ</b>', '', '📁 ' + esc(payload['category_name'])]
            for i, product in enumerate(payload['products'], 1):
                lines.append(str(i) + '. <b>' + esc(product['name']) + '</b> — $' + esc(product['price_usd']) + ' — الكمية: ' + str(product['stock']))
            lines += ['', 'أرسل <b>نعم</b> لحفظ القسم والمنتجات أو <b>لا</b> للإلغاء.']
            next_action = 'add_product_confirm'
            prompt = '\n'.join(lines)
    else:
        if raw.lower() not in ('نعم', 'yes', 'y'):
            with db() as conn:
                conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
            admin_products_page(api, cid)
            return True
        category_id = payload.get('category_id') or 'cat_' + uuid.uuid4().hex[:10]
        if payload.get('category_id') and category_id not in G['PRODUCTS'] and not custom_category(category_id):
            send(api, cid, 'القسم لم يعد موجودًا. اختر قسمًا آخر.')
            add_to_category(api, cid)
            return True
        saved_product_ids = []
        with db() as conn:
            if not payload.get('category_id'):
                conn.execute('INSERT INTO admin_categories(cid,name,created_at) VALUES (?,?,?)', (category_id, payload['category_name'], now_saudi()))
                conn.execute('INSERT OR REPLACE INTO product_text(pid,field,lang,value) VALUES (?,?,?,?)', (category_id, 'name', 'en', auto_translate(payload['category_name'], 'en')[:100]))
            for product in payload['products']:
                pid = 'custom_' + uuid.uuid4().hex[:10]
                usd = Decimal(product['price_usd']).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                sar = (usd * RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                conn.execute('INSERT INTO admin_products(pid,name,description,price_sar,available,created_at,category_id,price_usd,stock) VALUES (?,?,?,?,1,?,?,?,?)',
                             (pid, product['name'], product.get('description',''), str(sar), now_saudi(), category_id, str(usd), int(product.get('stock',1))))
                saved_product_ids.append(pid)
                conn.execute('INSERT OR REPLACE INTO product_text(pid,field,lang,value) VALUES (?,?,?,?)', (pid, 'name', 'en', product['name_en']))
                conn.execute('INSERT OR REPLACE INTO product_text(pid,field,lang,value) VALUES (?,?,?,?)', (pid, 'description', 'en', product['description_en']))
                if 'description_html' in product:
                    conn.execute('INSERT OR REPLACE INTO description_emoji VALUES (?,?,?,?)',
                                 (pid, '*', product.get('description', ''), product['description_html']))
            conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
        with db() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS channel_publish_choices (pid TEXT PRIMARY KEY, status TEXT NOT NULL)')
            for new_pid in saved_product_ids:
                conn.execute("INSERT OR REPLACE INTO channel_publish_choices VALUES (?,'pending')", (new_pid,))
        send(api, cid, '✅ تم حفظ المنتجات وظهرت في المتجر.\\n\\n📣 هل تريد نشرها في القناة الآن أم لاحقًا؟',
             kb([[btn('📣 نشر الآن', 'channel:publish_new:' + ','.join(saved_product_ids), style='success')],
                 [btn('🕒 لاحقًا', 'channel:defer_new:' + ','.join(saved_product_ids))]]))
        return True

    with db() as conn:
        conn.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)', (cid, next_action, json.dumps(payload, ensure_ascii=False)))
    send(api, cid, prompt, kb([[btn('❌ إلغاء', 'admin:cancelproduct')]]))
    return True


def admin_product_detail(api, cid, pid):
    if cid != G['ADMIN_ID']:
        return home(api, cid)
    cp = custom_product(pid)
    if cp:
        _, product_name, description, price_usd, available, category_id, stock = cp
        qty = int(stock or 0)
        is_available = bool(available and qty > 0)
        rows = [[btn('🖼️ إضافة/تعديل صورة المنتج', 'photopick:' + pid)],
                [btn('💵 تعديل السعر', 'pricepick:' + pid)],
                [btn('🔌 ربط API بالمنتج', 'supplierpick:' + pid, style='primary')],
                [btn('🔄 تغيير التوفر', 'myproducttoggle:' + pid)],
                [btn('🗑 حذف المنتج', 'myproductdelete:' + pid, style='danger')],
                [btn('↩️ القسم', 'mycategory:' + category_id)]]
    else:
        if pid not in VARIANTS and pid not in G['PRODUCTS']:
            return admin_products_page(api, cid)
        product_name = name(pid, cid)
        description = product_description(pid, cid)
        qty = product_stock(pid)
        is_available = bool(in_stock(pid))
        category_id = VARIANTS.get(pid, {}).get('category', pid)
        rows = [[btn('🖼️ إضافة/تعديل صورة المنتج', 'photopick:' + pid)],
                [btn('💵 تعديل السعر', 'pricepick:' + pid)],
                [btn('🔌 ربط API بالمنتج', 'supplierpick:' + pid, style='primary')],
                [btn('📦 تعديل التوفر/الكمية', 'admin:stock')],
                [btn('↩️ القسم', 'mycategory:' + category_id)]]
    text = ('📦 <b>' + esc(product_name) + '</b>\n\n' + esc(description or '') +
            '\n\n💵 ' + price(cid, pid, 'USD') +
            '\n📦 الكمية: ' + esc(qty) +
            '\nالحالة: ' + ('✅ متوفر' if is_available else '🔴 غير متوفر'))
    send(api, cid, text, kb(rows))


def toggle_admin_product(api, cid, pid):
    if cid != G['ADMIN_ID']:
        return
    with db() as conn:
        conn.execute('UPDATE admin_products SET available=CASE available WHEN 1 THEN 0 ELSE 1 END WHERE pid=?', (pid,))
    admin_product_detail(api, cid, pid)


def delete_admin_product(api, cid, pid):
    if cid != G['ADMIN_ID']:
        return
    cp = custom_product(pid)
    category_id = cp[5] if cp else None
    with db() as conn:
        conn.execute('DELETE FROM admin_products WHERE pid=?', (pid,))
    if category_id:
        admin_category_detail(api, cid, category_id)
    else:
        admin_products_page(api, cid)


def admin_panel(api, cid):
    if cid != G['ADMIN_ID']:
        return home(api, cid)
    with db() as conn:
        conn.execute("DELETE FROM admin_state WHERE cid=? AND action='price'", (cid,))
        orders_count = conn.execute('SELECT COUNT(*) FROM orders').fetchone()[0]
        review_count = conn.execute('SELECT COUNT(*) FROM orders WHERE status="review"').fetchone()[0]
        activity_count = total_activity_count(conn)
    text = f'🧾 <b>لوحة إدارة VEXA</b>\n\nالطلبات: <b>{orders_count}</b>\nبانتظار المراجعة: <b>{review_count}</b>\nسجل الاختيارات: <b>{activity_count}</b>'
    send(api, cid, text, kb([[btn('🔔 الطلبات الجديدة / التسليم', 'admin:orders', style='primary')],
                             [btn('👀 نشاط العملاء', 'admin:activity')],
                             [btn('📨 مراسلات العملاء', 'inbox:menu', style='primary')],
                             [btn('➕ إضافة منتج', 'admin:addproduct', style='success'), btn('📦 منتجاتي', 'admin:myproducts')],
                             [btn('🎟 أكواد الخصم', 'couponadmin:list')],
                             [btn('✏️ تعديل السعر', 'admin:prices')],
                             [btn('🎛 إعداد عرض بيانات المنتج', 'admin:info', style='primary')],
                             [btn('📦 تعديل توفر المنتج', 'admin:stock')],
                             [btn('🔌 ربط API بالمنتج', 'admin:supplierapi', style='primary')],
                             [btn('📢 إرسال رسالة للجميع', 'admin:broadcast', style='primary')],
                             [btn('📊 الإحصائيات', 'admin:stats')],
                             [btn('➕ إضافة أيقونة', 'admin:icons', style='success')],
                             [btn('✏️ تعديل أسماء الأزرار', 'admin:buttonlabels')],
                             [btn(ui_label('ui_category_description', 'تعديل وصف القسم'), 'admin:categorydesc', ui_icon('ui_category_description'))],
                             [btn('🏠 الرئيسية', 'home')]]))


def supplier_api_row(pid):
    pid = LEGACY.get(pid, pid)
    with db() as conn:
        row = conn.execute('SELECT endpoint,api_key,service_id,enabled,COALESCE(provider,"generic"),COALESCE(variant_id,"") FROM supplier_api WHERE pid=?', (pid,)).fetchone()
    if not row:
        return ('', '', '', 0, 'generic', '')
    endpoint, api_key, service_id, enabled, provider, variant_id = row
    # For Pandora, prefer Railway secrets so a rotated key is picked up immediately
    # without storing the secret in SQLite or GitHub.
    if provider == 'pandora':
        endpoint = (os.getenv('PANDORA_API_BASE') or 'https://api.pandoradigital.shop/api/v1').strip()
        api_key = (os.getenv('PANDORA_API_KEY') or '').strip()
    return endpoint, api_key, service_id, enabled, provider, variant_id


def supplier_api_menu(api, cid, category_id=None):
    if cid != G['ADMIN_ID']:
        return
    with db() as conn:
        conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
        custom_cats = conn.execute('SELECT cid,name FROM admin_categories ORDER BY rowid').fetchall()
    if category_id is None:
        cats = [(pid, ('YouTube' if pid == 'youtube' else name(pid, cid))) for pid in G['PRODUCTS']]
        cats += [(pid, label) for pid, label in custom_cats if pid not in G['PRODUCTS']]
        cats.sort(key=lambda item: (0 if str(item[0]).lower() == 'capcut' or 'capcut' in str(item[1]).lower() else 1, str(item[1]).lower()))
        rows = [[btn(label, 'suppliercat:' + pid)] for pid, label in cats]
        send(api, cid, '🔌 <b>ربط API بالمنتج</b>\n\nاختر القسم:', kb(rows + [[btn('↩️ لوحة الإدارة', 'admin')]]))
        return
    ids = admin_category_product_ids(category_id)
    rows = []
    for pid in dict.fromkeys(ids):
        endpoint, api_key, service_id, enabled, provider, variant_id = supplier_api_row(pid)
        mark = '🟢 ' if enabled and endpoint and api_key else '⚪️ '
        rows.append([btn(mark + name(pid, cid), 'supplierpick:' + pid)])
    send(api, cid, '🔌 اختر المنتج الذي تريد ربطه بالمورد:', kb(rows + [[btn('↩️ الأقسام', 'admin:supplierapi')], [btn('↩️ لوحة الإدارة', 'admin')]]))


def _supplier_json_request(url, api_key, method='GET', payload=None, extra_headers=None, timeout=20):
    headers = {
        'Authorization': 'Bearer ' + api_key,
        'Accept': 'application/json',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.9'
    }
    if payload is not None:
        headers['Content-Type'] = 'application/json'
    if extra_headers:
        headers.update(extra_headers)
    data = json.dumps(payload).encode('utf-8') if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode('utf-8')
    return json.loads(raw) if raw else {}


def supplier_delivery_text(items, cid):
    clean = [str(x).strip() for x in (items or []) if str(x).strip()]
    if not clean:
        return ''
    title = tr(cid, '✅ <b>تم تسليم طلبك تلقائيًا</b>', '✅ <b>Your order was delivered automatically</b>')
    body = '\n'.join('• <code>' + esc(x) + '</code>' for x in clean)
    return title + '\n\n' + body


def pandora_pricing_row(pid):
    pid = LEGACY.get(pid, pid)
    with db() as conn:
        row = conn.execute('SELECT supplier_cost_usd,margin_usd,updated_at FROM pandora_pricing WHERE pid=?', (pid,)).fetchone()
    return row or ('', '0', '')


def pandora_quote_cost(pid, quantity=1):
    endpoint, api_key, product_id, enabled, provider, variant_id = supplier_api_row(pid)
    if not (provider == 'pandora' and endpoint and api_key and product_id and variant_id):
        return None
    quote = _supplier_json_request(endpoint.rstrip('/') + '/quotes', api_key, 'POST',
        {'product_id': product_id, 'variant_id': variant_id, 'quantity': int(quantity or 1)})
    if not quote.get('can_purchase', False) or quote.get('unit_price') is None:
        return None
    return Decimal(str(quote.get('unit_price'))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def pandora_refresh_price(pid):
    pid = LEGACY.get(pid, pid)
    cost = pandora_quote_cost(pid, 1)
    if cost is None:
        return None
    _, margin_raw, _ = pandora_pricing_row(pid)
    margin = Decimal(str(margin_raw or '0')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    sale = (cost + margin).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    with db() as conn:
        conn.execute('INSERT INTO pandora_pricing(pid,supplier_cost_usd,margin_usd,updated_at) VALUES (?,?,?,?) ON CONFLICT(pid) DO UPDATE SET supplier_cost_usd=excluded.supplier_cost_usd,updated_at=excluded.updated_at',
                     (pid, str(cost), str(margin), now_saudi()))
        conn.execute('INSERT OR REPLACE INTO product_prices(pid,value,currency) VALUES (?,?,?)', (pid, str(sale), 'USD'))
    return cost, margin, sale


def pandora_set_margin(pid, margin):
    pid = LEGACY.get(pid, pid)
    margin = Decimal(str(margin)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    cost_raw, _, _ = pandora_pricing_row(pid)
    if not cost_raw:
        refreshed = pandora_refresh_price(pid)
        if not refreshed:
            return None
        cost_raw = str(refreshed[0])
    cost = Decimal(str(cost_raw)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    sale = (cost + margin).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    with db() as conn:
        conn.execute('INSERT INTO pandora_pricing(pid,supplier_cost_usd,margin_usd,updated_at) VALUES (?,?,?,?) ON CONFLICT(pid) DO UPDATE SET margin_usd=excluded.margin_usd,updated_at=excluded.updated_at',
                     (pid, str(cost), str(margin), now_saudi()))
        conn.execute('INSERT OR REPLACE INTO product_prices(pid,value,currency) VALUES (?,?,?)', (pid, str(sale), 'USD'))
    return sale


def pandora_fulfill_order(api, internal_order_id):
    with db() as conn:
        order = conn.execute('SELECT cid,pid,status FROM orders WHERE id=?', (internal_order_id,)).fetchone()
        snap = conn.execute('SELECT quantity FROM quantity_snapshots WHERE scope=? AND key=?', ('order', internal_order_id)).fetchone()
        existing = conn.execute('SELECT supplier_order_id,status,delivery FROM supplier_orders WHERE order_id=?', (internal_order_id,)).fetchone()
    if not order:
        return False
    cid, pid, order_status = order
    endpoint, api_key, product_id, enabled, provider, variant_id = supplier_api_row(pid)
    if not (enabled and provider == 'pandora' and endpoint and api_key and product_id and variant_id):
        return False
    quantity = int(snap[0]) if snap and snap[0] else 1

    try:
        supplier_order_id = existing[0] if existing and existing[0] else ''
        result = None

        if supplier_order_id:
            result = _supplier_json_request(endpoint.rstrip('/') + '/orders/' + urllib.parse.quote(str(supplier_order_id)), api_key)
        else:
            quote = _supplier_json_request(
                endpoint.rstrip('/') + '/quotes', api_key, 'POST',
                {'product_id': product_id, 'variant_id': variant_id, 'quantity': quantity}
            )
            if not quote.get('can_purchase', False):
                raise RuntimeError('Supplier cannot fulfill now')
            unit_price = quote.get('unit_price')
            price_version = quote.get('price_version')
            if unit_price is None or not price_version:
                raise RuntimeError('Invalid quote response')
            current_cost = Decimal(str(unit_price)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            _, margin_raw, _ = pandora_pricing_row(pid)
            configured_margin = Decimal(str(margin_raw or '0')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            with db() as conn:
                paid = conn.execute('SELECT usd FROM orders WHERE id=?', (internal_order_id,)).fetchone()
            paid_unit = (Decimal(str(paid[0] if paid and paid[0] else '0')) / Decimal(quantity)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            if paid_unit < (current_cost + configured_margin):
                raise RuntimeError('Pandora price changed below configured margin')
            expected_unit_price = float(current_cost)
            payload = {
                'product_id': product_id,
                'variant_id': variant_id,
                'quantity': quantity,
                'expected_unit_price': expected_unit_price,
                'price_version': price_version,
                'client_order_reference': internal_order_id
            }
            idem = 'vexa-' + internal_order_id
            result = _supplier_json_request(
                endpoint.rstrip('/') + '/orders', api_key, 'POST', payload,
                {'Idempotency-Key': idem}
            )
            supplier_order_id = str(result.get('id') or '')
            with db() as conn:
                conn.execute('INSERT OR REPLACE INTO supplier_orders(order_id,supplier_order_id,status,delivery,last_error,updated_at) VALUES (?,?,?,?,?,?)',
                             (internal_order_id, supplier_order_id, str(result.get('status') or ''), '', '', now_saudi()))

        # Short polling window for orders that finish just after creation.
        for _ in range(5):
            delivery = (result or {}).get('delivery') or {}
            items = delivery.get('items') or []
            status = str((result or {}).get('status') or '')
            if items:
                text = supplier_delivery_text(items, cid)
                with db() as conn:
                    conn.execute('UPDATE supplier_orders SET status=?,delivery=?,last_error="",updated_at=? WHERE order_id=?',
                                 (status or 'delivered', json.dumps(items, ensure_ascii=False), now_saudi(), internal_order_id))
                    conn.execute('UPDATE orders SET status="paid" WHERE id=?', (internal_order_id,))
                send(api, cid, text, menu(cid))
                send(api, G['ADMIN_ID'], '✅ <b>تسليم تلقائي عبر Pandora</b>\nالطلب: <code>' + esc(internal_order_id) + '</code>\nالعميل: <code>' + esc(cid) + '</code>\nالمنتج: ' + esc(name(pid, cid)))
                return True
            if not supplier_order_id or status.lower() in ('failed','rejected','cancelled'):
                break
            time.sleep(1.2)
            result = _supplier_json_request(endpoint.rstrip('/') + '/orders/' + urllib.parse.quote(str(supplier_order_id)), api_key)

        with db() as conn:
            conn.execute('UPDATE supplier_orders SET status=?,last_error=?,updated_at=? WHERE order_id=?',
                         (str((result or {}).get('status') or 'pending'), 'delivery_pending', now_saudi(), internal_order_id))
        send(api, cid, tr(cid, '✅ تم استلام طلبك وهو قيد التجهيز التلقائي. سيتم متابعته من الإدارة إذا تأخر التسليم.', '✅ Your order was received and is being processed automatically. Administration will follow up if delivery is delayed.'))
        send(api, G['ADMIN_ID'], '⚠️ <b>طلب Pandora بانتظار التسليم</b>\nالطلب: <code>' + esc(internal_order_id) + '</code>\nSupplier order: <code>' + esc(supplier_order_id or 'unknown') + '</code>')
        return True
    except Exception as exc:
        with db() as conn:
            conn.execute('INSERT INTO supplier_orders(order_id,supplier_order_id,status,delivery,last_error,updated_at) VALUES (?,?,?,?,?,?) ON CONFLICT(order_id) DO UPDATE SET status=excluded.status,last_error=excluded.last_error,updated_at=excluded.updated_at',
                         (internal_order_id, existing[0] if existing else '', 'error', '', type(exc).__name__, now_saudi()))
        send(api, G['ADMIN_ID'], '❌ <b>فشل تنفيذ طلب Pandora تلقائيًا</b>\nالطلب: <code>' + esc(internal_order_id) + '</code>\nالخطأ: <code>' + esc(type(exc).__name__) + '</code>')
        send(api, cid, tr(cid, '✅ تم الدفع، لكن تعذر التسليم التلقائي الآن. تم تحويل الطلب للإدارة لإكماله بدون إعادة الدفع.', '✅ Payment was received, but automatic delivery failed. The order was sent to administration; you do not need to pay again.'))
        return True


def fulfill_paid_order(api, order_id):
    """Run the configured supplier exactly once after payment approval.

    Pandora uses supplier_orders + client_order_reference + Idempotency-Key,
    so retries/restarts cannot create duplicate supplier orders.
    """
    with db() as conn:
        row = conn.execute('SELECT pid,status FROM orders WHERE id=?', (order_id,)).fetchone()
    if not row or row[1] != 'paid':
        return False
    pid = row[0]
    endpoint, api_key, product_id, enabled, provider, variant_id = supplier_api_row(pid)
    if not enabled:
        return False
    if provider == 'pandora' and endpoint and api_key and product_id and variant_id:
        return pandora_fulfill_order(api, order_id)
    return False


def supplier_test_connection(api, cid, pid):
    if cid != G['ADMIN_ID']:
        return
    endpoint, api_key, service_id, enabled, provider, variant_id = supplier_api_row(pid)
    if not endpoint or not api_key:
        return send(api, cid, '⚠️ أضف رابط API والمفتاح أولًا.')
    try:
        endpoint = ''.join(ch for ch in endpoint.strip() if ord(ch) < 128)
        api_key = ''.join(ch for ch in api_key.strip() if ord(ch) < 128)
        if not endpoint.startswith(('http://','https://')):
            return send(api, cid, '❌ رابط API غير صحيح. أعد حفظ الرابط بدون أي رموز أو مسافات إضافية.')
        if not api_key:
            return send(api, cid, '❌ مفتاح API غير صحيح أو يحتوي رموز غير مدعومة. أعد نسخه من Pandora ثم احفظه من جديد.')

        # The integration needs catalog access to locate products/variants.
        # balance:read is optional, so test /products instead of /balance.
        req = urllib.request.Request(endpoint.rstrip('/') + '/products?limit=1',
                                     headers={'Authorization': 'Bearer ' + api_key,
                                              'Accept': 'application/json',
                                              'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36',
                                              'Accept-Language': 'en-US,en;q=0.9'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        count = len(data.get('items') or []) if isinstance(data, dict) else 0
        send(api, cid, '✅ اتصال Pandora ناجح والمفتاح يملك صلاحية قراءة المنتجات.\n📦 تم الوصول إلى الكتالوج بنجاح.')
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            msg = '❌ المفتاح غير صحيح أو منتهي أو ملغي.'
        elif exc.code == 403:
            msg = '❌ المفتاح لا يملك صلاحية <code>catalog:read</code>. اطلب تفعيلها على مفتاح Pandora.'
        else:
            msg = '❌ فشل الاتصال مع Pandora. HTTP ' + str(exc.code)
        send(api, cid, msg)
    except UnicodeEncodeError:
        send(api, cid, '❌ يوجد رمز أو مسافة غير صالحة داخل رابط API أو المفتاح. أعد نسخهما مباشرة من Pandora بدون أي نص إضافي.')
    except Exception as exc:
        send(api, cid, '❌ فشل اختبار الاتصال: <code>' + esc(type(exc).__name__) + '</code>')


def pandora_startup_probe():
    """Read-only Pandora auth/connectivity diagnostics. Never creates an order."""
    endpoint = (os.getenv('PANDORA_API_BASE') or 'https://api.pandoradigital.shop/api/v1').strip()
    api_key = (os.getenv('PANDORA_API_KEY') or '').strip()
    if not api_key:
        print('Pandora probe: missing PANDORA_API_KEY', flush=True)
        return
    url = endpoint.rstrip('/') + '/products?limit=1'
    modes = [
        ('bearer', {'Authorization': 'Bearer ' + api_key, 'Accept': 'application/json',
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36',
                    'Accept-Language': 'en-US,en;q=0.9'}),
        ('x-api-key', {'X-API-Key': api_key, 'Accept': 'application/json',
                       'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36',
                       'Accept-Language': 'en-US,en;q=0.9'}),
        ('api-key', {'Api-Key': api_key, 'Accept': 'application/json',
                     'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36',
                     'Accept-Language': 'en-US,en;q=0.9'}),
        ('auth-raw', {'Authorization': api_key, 'Accept': 'application/json',
                      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36',
                      'Accept-Language': 'en-US,en;q=0.9'}),
    ]
    for mode, headers in modes:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=12) as resp:
                raw = resp.read().decode('utf-8', 'replace')
                data = json.loads(raw) if raw else {}
            items = _pandora_list(data)
            print('Pandora probe: auth=' + mode + ' OK products_read=' + str(len(items)), flush=True)
            return
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode('utf-8', 'replace')[:500]
            except Exception:
                body = ''
            print('Pandora probe: auth=' + mode + ' HTTP ' + str(exc.code) + ' body=' + body.replace('\n',' '), flush=True)
        except Exception as exc:
            print('Pandora probe: auth=' + mode + ' failed ' + type(exc).__name__, flush=True)


def _pandora_bool(value, default=True):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    return str(value).strip().lower() not in ('0','false','no','off','disabled','unavailable','out_of_stock','sold_out')


def _pandora_stock_value(item):
    if not isinstance(item, dict):
        return None
    for key in ('stock','quantity','qty','available_stock','inventory','remaining'):
        value = item.get(key)
        if isinstance(value, dict):
            value = value.get('available') or value.get('quantity') or value.get('stock')
        try:
            if value is not None:
                return max(0, int(float(value)))
        except Exception:
            pass
    return None


def pandora_sync_product(pid):
    """Refresh local availability/stock from the linked Pandora product/variant."""
    endpoint, api_key, product_id, enabled, provider, variant_id = supplier_api_row(pid)
    if not (provider == 'pandora' and endpoint and api_key and product_id and variant_id):
        return None
    try:
        payload = _supplier_json_request(endpoint.rstrip('/') + '/products?limit=100', api_key, timeout=20)
        target_product = None
        for item in _pandora_list(payload):
            if _pandora_product_id(item) == str(product_id):
                target_product = item
                break
        if not target_product:
            return None

        target_variant = None
        raw_variants = target_product.get('variants') or target_product.get('options') or target_product.get('skus') or []
        if isinstance(raw_variants, dict):
            raw_variants = raw_variants.get('data') or raw_variants.get('items') or list(raw_variants.values())
        if isinstance(raw_variants, list):
            for variant in raw_variants:
                if not isinstance(variant, dict):
                    continue
                vid = str(variant.get('id') or variant.get('variant_id') or variant.get('variantId') or variant.get('sku') or '')
                if vid == str(variant_id):
                    target_variant = variant
                    break

        source = target_variant or target_product
        stock = _pandora_stock_value(source)
        if stock is None:
            stock = _pandora_stock_value(target_product)
        available_value = source.get('available') if isinstance(source, dict) else None
        if available_value is None and isinstance(source, dict):
            available_value = source.get('is_available')
        if available_value is None and isinstance(source, dict):
            available_value = source.get('active')
        available = _pandora_bool(available_value, True)
        if stock is not None:
            available = available and stock > 0

        with db() as conn:
            if custom_product(pid):
                if stock is not None:
                    conn.execute('UPDATE admin_products SET stock=?,available=? WHERE pid=?', (stock, 1 if available else 0, pid))
                else:
                    conn.execute('UPDATE admin_products SET available=? WHERE pid=?', (1 if available else 0, pid))
            else:
                if stock is not None:
                    conn.execute('INSERT OR REPLACE INTO product_stock_overrides(pid,stock) VALUES (?,?)', (pid, stock))
                conn.execute('INSERT OR REPLACE INTO product_availability(pid,available) VALUES (?,?)', (pid, 1 if available else 0))
        return {'stock': stock, 'available': available}
    except Exception as exc:
        print('Pandora stock sync failed:', pid, type(exc).__name__, flush=True)
        return None


def supplier_api_editor(api, cid, pid):
    if cid != G['ADMIN_ID']:
        return
    pid = LEGACY.get(pid, pid)
    if pid not in VARIANTS and pid not in G['PRODUCTS'] and not custom_product(pid):
        return supplier_api_menu(api, cid)
    endpoint, api_key, service_id, enabled, provider, variant_id = supplier_api_row(pid)
    if provider == 'pandora' and service_id and variant_id:
        pandora_sync_product(pid)
        endpoint, api_key, service_id, enabled, provider, variant_id = supplier_api_row(pid)
    masked = ('••••••' + api_key[-4:]) if api_key else 'غير مضاف'
    text = ('🔌 <b>' + esc(name(pid, cid)) + '</b>\n\n'
            'المورد: <b>' + esc('Pandora Digital' if provider == 'pandora' else 'Generic API') + '</b>\n'
            'الحالة: <b>' + ('🟢 مفعّل' if enabled else '⚪️ غير مفعّل') + '</b>\n'
            '🌐 رابط Pandora العام: <b>' + ('✅ جاهز' if endpoint else '❌ غير موجود') + '</b>\n'
            '🔑 مفتاح Pandora العام: <b>' + ('✅ جاهز' if api_key else '❌ غير موجود') + '</b>\n'
            '🆔 Product ID: <code>' + esc(service_id or 'غير مضاف') + '</code>\n'
            '🧩 Variant ID: <code>' + esc(variant_id or 'غير مضاف') + '</code>\n\n'
            'أضف فقط Product ID و Variant ID لهذا المنتج.')
    rows = [[btn('🧩 Pandora Digital', 'supplierpandora:' + pid, style='primary')],
            [btn('🔎 اختيار منتج من Pandora', 'pandorabrowse:' + pid, style='primary')],
            [btn(('✅ ' if service_id else '❌ ') + 'Product ID', 'supplierset:service:' + pid), btn(('✅ ' if variant_id else '❌ ') + 'Variant ID', 'supplierset:variant:' + pid)],
            [btn('🧪 اختبار الاتصال', 'suppliertest:' + pid)],
            [btn('✅ تفعيل الربط' if not enabled else '⏸ إيقاف الربط', 'suppliertoggle:' + pid, style='success' if not enabled else 'danger')],
            [btn('🗑 حذف الربط', 'supplierdelete:' + pid, style='danger')],
            [btn('↩️ المنتجات', 'admin:supplierapi')], [btn('↩️ لوحة الإدارة', 'admin')]]
    send(api, cid, text, kb(rows))



def _pandora_list(payload):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ('data','products','items','results'):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            for nested in ('data','products','items','results'):
                if isinstance(value.get(nested), list):
                    return value[nested]
    return []


def _pandora_product_name(item):
    if not isinstance(item, dict):
        return str(item)
    return str(item.get('name') or item.get('title') or item.get('product_name') or item.get('label') or item.get('id') or 'Pandora Product')


def _pandora_category_name(item):
    """Best-effort Pandora category label from common API payload shapes."""
    if not isinstance(item, dict):
        return 'Other'
    for key in ('category_name','category','product_category','service','group','brand','type'):
        value = item.get(key)
        if isinstance(value, dict):
            value = value.get('name') or value.get('title') or value.get('label') or value.get('slug') or value.get('id')
        elif isinstance(value, list):
            value = next((x.get('name') if isinstance(x, dict) else x for x in value if x), None)
        if value:
            text = str(value).strip()
            if text:
                return text[:60]
    return 'Other'


def _pandora_product_id(item):
    if not isinstance(item, dict):
        return ''
    return str(item.get('id') or item.get('product_id') or item.get('productId') or item.get('uuid') or '')


def _pandora_variants(item):
    if not isinstance(item, dict):
        return []
    variants = item.get('variants') or item.get('options') or item.get('skus') or []
    if isinstance(variants, dict):
        variants = variants.get('data') or variants.get('items') or list(variants.values())
    if not isinstance(variants, list):
        variants = []
    out = []
    for v in variants:
        if not isinstance(v, dict):
            continue
        vid = str(v.get('id') or v.get('variant_id') or v.get('variantId') or v.get('sku') or '')
        if not vid:
            continue
        label = str(v.get('name') or v.get('title') or v.get('label') or v.get('sku') or vid)
        out.append({'id': vid, 'name': label})
    root_vid = str(item.get('variant_id') or item.get('variantId') or '')
    if not out and root_vid:
        out.append({'id': root_vid, 'name': str(item.get('variant_name') or item.get('variantName') or 'Default')})
    return out


def pandora_catalog_open(api, cid, pid, page=0):
    if cid != G['ADMIN_ID']:
        return
    pid = LEGACY.get(pid, pid)
    endpoint = (os.getenv('PANDORA_API_BASE') or 'https://api.pandoradigital.shop/api/v1').strip()
    api_key = (os.getenv('PANDORA_API_KEY') or '').strip()
    if not api_key:
        return send(api, cid, '❌ مفتاح Pandora غير موجود في Railway.')
    try:
        payload = _supplier_json_request(endpoint.rstrip('/') + '/products?limit=100', api_key, timeout=20)
        raw_items = _pandora_list(payload)
        products = []
        for item in raw_items:
            product_id = _pandora_product_id(item)
            if not product_id:
                continue
            products.append({'id': product_id, 'name': _pandora_product_name(item)[:80],
                             'category': _pandora_category_name(item),
                             'variants': _pandora_variants(item)})
        if not products:
            return send(api, cid, '⚠️ لم أستطع قراءة منتجات Pandora. تأكد أن المفتاح يملك صلاحية <code>catalog:read</code>.', kb([[btn('↩️ رجوع', 'supplierpick:' + pid)]]))
        with db() as conn:
            conn.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)',
                         (cid, 'pandora_catalog', json.dumps({'pid': pid, 'products': products}, ensure_ascii=False)))
        pandora_catalog_categories(api, cid)
    except Exception as exc:
        code = getattr(exc, 'code', None)
        if code == 403:
            msg = '❌ مفتاح Pandora لا يملك صلاحية <code>catalog:read</code>.'
        elif code == 401:
            msg = '❌ مفتاح Pandora غير صحيح أو منتهي.'
        else:
            msg = '❌ تعذر تحميل كتالوج Pandora الآن: <code>' + esc(type(exc).__name__) + '</code>'
        send(api, cid, msg, kb([[btn('↩️ رجوع', 'supplierpick:' + pid)]]))


def pandora_catalog_categories(api, cid):
    with db() as conn:
        row = conn.execute("SELECT value FROM admin_state WHERE cid=? AND action='pandora_catalog'", (cid,)).fetchone()
    if not row:
        return supplier_api_menu(api, cid)
    state = json.loads(row[0])
    products = state.get('products') or []
    grouped = {}
    for index, product in enumerate(products):
        category = str(product.get('category') or 'Other').strip() or 'Other'
        grouped.setdefault(category, []).append(index)
    categories = sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0].lower()))
    state['pandora_categories'] = [{'name': name, 'items': items} for name, items in categories]
    with db() as conn:
        conn.execute('UPDATE admin_state SET value=? WHERE cid=? AND action="pandora_catalog"',
                     (json.dumps(state, ensure_ascii=False), cid))
    rows = []
    for cidx, (category, items) in enumerate(categories):
        rows.append([btn('📁 ' + category[:38] + ' • ' + str(len(items)), 'pandoracat:' + str(cidx) + ':0')])
    rows.append([btn('↩️ رجوع', 'supplierpick:' + state.get('pid',''))])
    send(api, cid, '🔎 <b>أقسام Pandora</b>\n\nاختر القسم، وسيظهر عدد المنتجات الموجودة داخله:', kb(rows))


def pandora_catalog_page(api, cid, category_index=0, page=0):
    with db() as conn:
        row = conn.execute("SELECT value FROM admin_state WHERE cid=? AND action='pandora_catalog'", (cid,)).fetchone()
    if not row:
        return supplier_api_menu(api, cid)
    state = json.loads(row[0])
    products = state.get('products') or []
    categories = state.get('pandora_categories') or []
    try:
        category_index = int(category_index)
        category = categories[category_index]
    except Exception:
        return pandora_catalog_categories(api, cid)
    indices = category.get('items') or []
    page = max(0, int(page or 0))
    per_page = 8
    start = page * per_page
    if start >= len(indices) and page:
        page = 0
        start = 0
    rows = []
    for pos in range(start, min(start + per_page, len(indices))):
        index = indices[pos]
        if index >= len(products):
            continue
        label = products[index].get('name') or products[index].get('id')
        rows.append([btn('📦 ' + str(label)[:45], 'pandorap:' + str(index))])
    nav = []
    if page > 0:
        nav.append(btn('⬅️ السابق', 'pandoracat:' + str(category_index) + ':' + str(page-1)))
    if start + per_page < len(indices):
        nav.append(btn('التالي ➡️', 'pandoracat:' + str(category_index) + ':' + str(page+1)))
    if nav:
        rows.append(nav)
    rows.append([btn('↩️ أقسام Pandora', 'pandoracategories')])
    rows.append([btn('↩️ إعداد API', 'supplierpick:' + state.get('pid',''))])
    send(api, cid, '📁 <b>' + esc(category.get('name','Pandora')) + '</b>\n'
         '📦 عدد المنتجات: <b>' + str(len(indices)) + '</b>\n\nاختر المنتج المطابق لمنتج VEXA:', kb(rows))


def pandora_catalog_product(api, cid, index):
    with db() as conn:
        row = conn.execute("SELECT value FROM admin_state WHERE cid=? AND action='pandora_catalog'", (cid,)).fetchone()
    if not row:
        return supplier_api_menu(api, cid)
    state = json.loads(row[0]); products = state.get('products') or []
    try:
        index = int(index); product = products[index]
    except Exception:
        return pandora_catalog_categories(api, cid)
    variants = product.get('variants') or []
    if len(variants) == 1:
        return pandora_catalog_save(api, cid, index, 0)
    if not variants:
        with db() as conn:
            current = conn.execute('SELECT endpoint,api_key,service_id,enabled,provider,variant_id FROM supplier_api WHERE pid=?', (state['pid'],)).fetchone()
            endpoint, api_key, _, enabled, _, variant_id = current if current else ('','','',0,'pandora','')
            conn.execute('INSERT INTO supplier_api(pid,endpoint,api_key,service_id,enabled,provider,variant_id) VALUES (?,?,?,?,?,?,?) ON CONFLICT(pid) DO UPDATE SET service_id=excluded.service_id,provider="pandora"',
                         (state['pid'], endpoint, api_key, product['id'], enabled, 'pandora', variant_id))
        return send(api, cid, '✅ تم حفظ Product ID تلقائيًا.\n⚠️ Pandora لم يُرجع Variant لهذا المنتج؛ أضف Variant ID يدويًا.', kb([[btn('↩️ إعداد API', 'supplierpick:' + state['pid'])]]))
    rows = [[btn('🧩 ' + str(v.get('name') or v.get('id'))[:45], 'pandorav:' + str(index) + ':' + str(i))] for i,v in enumerate(variants[:20])]
    rows.append([btn('↩️ أقسام Pandora', 'pandoracategories')])
    send(api, cid, '🧩 <b>' + esc(product.get('name','Pandora')) + '</b>\n\nاختر الـ Variant:', kb(rows))


def pandora_catalog_save(api, cid, pindex, vindex):
    with db() as conn:
        row = conn.execute("SELECT value FROM admin_state WHERE cid=? AND action='pandora_catalog'", (cid,)).fetchone()
    if not row:
        return supplier_api_menu(api, cid)
    state = json.loads(row[0]); products = state.get('products') or []
    try:
        product = products[int(pindex)]
        variant = (product.get('variants') or [])[int(vindex)]
    except Exception:
        return pandora_catalog_categories(api, cid)
    pid = state['pid']
    endpoint, api_key, _, enabled, _, _ = supplier_api_row(pid)
    with db() as conn:
        conn.execute('INSERT INTO supplier_api(pid,endpoint,api_key,service_id,enabled,provider,variant_id) VALUES (?,?,?,?,?,?,?) ON CONFLICT(pid) DO UPDATE SET service_id=excluded.service_id,variant_id=excluded.variant_id,provider="pandora",enabled=1',
                     (pid, endpoint, api_key, str(product['id']), 1, 'pandora', str(variant['id'])))
        conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
    sync = pandora_sync_product(pid)
    sync_text = ''
    if sync:
        sync_text = '\n📦 المخزون: <b>' + esc(sync.get('stock') if sync.get('stock') is not None else 'غير محدد') + '</b>\nالحالة: <b>' + ('🟢 متوفر' if sync.get('available') else '🔴 غير متوفر') + '</b>'
    send(api, cid, '✅ تم ربط المنتج وتفعيل Pandora تلقائيًا.\n\nProduct ID: <code>' + esc(product['id']) + '</code>\nVariant ID: <code>' + esc(variant['id']) + '</code>' + sync_text)
    supplier_api_editor(api, cid, pid)


def admin_stats(api, cid):
    if cid != G['ADMIN_ID']:
        return home(api, cid)
    try:
        users = [int(x) for x in json.loads(USERS_PATH.read_text(encoding='utf-8'))]
    except Exception:
        users = []
    total = len(set(users))
    with db() as conn:
        departed = conn.execute('SELECT COUNT(*) FROM user_delivery_status WHERE departed=1').fetchone()[0]
        row = conn.execute('SELECT sent,failed,created_at FROM broadcast_stats WHERE id=1').fetchone()
    available = max(total - departed, 0)
    sent, failed, created = row if row else (0, 0, 'لا توجد رسالة جماعية بعد')
    text = (f'📊 <b>إحصائيات البوت</b>\n\n'
            f'👥 إجمالي المستخدمين: <b>{total}</b>\n'
            f'🟢 المتاحون: <b>{available}</b>\n'
            f'🚪 غادروا البوت: <b>{departed}</b>\n\n'
            f'📢 <b>آخر رسالة جماعية</b>\n'
            f'✅ تم الإرسال: <b>{sent}</b>\n'
            f'❌ فشل الإرسال: <b>{failed}</b>\n'
            f'🕒 {esc(created)}')
    send(api, cid, text, kb([[btn('🔄 تحديث', 'admin:stats')], [btn('↩️ لوحة الإدارة', 'admin')]]))


def admin_orders(api, cid):
    if cid != G['ADMIN_ID']:
        return home(api, cid)
    with db() as conn:
        rows = conn.execute('SELECT id,cid,pid,method,usd,sar,status,created_at FROM orders ORDER BY rowid DESC LIMIT 20').fetchall()
        pending_count = conn.execute('SELECT COUNT(*) FROM orders WHERE status IN ("review","paid")').fetchone()[0]
    if not rows:
        return send(api, cid, '📦 لا توجد طلبات مسجلة حتى الآن.', kb([[btn('↩️ لوحة الإدارة', 'admin')]]))
    status_names = {'paid': '🟢 بانتظار التسليم', 'review': '🟡 بانتظار مراجعة الدفع', 'rejected': '❌ مرفوض', 'delivered': '✅ تم التسليم'}
    parts = [f'🔔 <b>الطلبات</b>\nبانتظار الإجراء: <b>{pending_count}</b>']
    action_buttons = []
    for oid, user_id, pid, method, usd, sar, status, created in rows:
        parts.append(f'\n<b>#{esc(oid)}</b> • {status_names.get(status, esc(status))}\n{esc(name(pid, cid))}\nالكمية: {product_options.snapshot(sys.modules[__name__], "order", oid)}\n{esc(sar)} SAR / {esc(usd)} USD • {esc(method)}\nالعميل: {customer_link(user_id)} • {esc(created)}')
        if status == 'review':
            action_buttons.append([btn('✅ قبول #' + oid, 'payreview:accept:' + oid, style='success'), btn('❌ رفض', 'payreview:reject:' + oid, style='danger')])
        elif status == 'paid':
            action_buttons.append([btn('📤 تسليم #' + oid, 'orderdeliver:' + oid, style='success')])
    action_buttons.extend([[btn('🔄 تحديث', 'admin:orders')], [btn('↩️ لوحة الإدارة', 'admin')]])
    send(api, cid, '\n'.join(parts), kb(action_buttons))


# Only the currently opened admin activity message is refreshed.
ACTIVITY_VIEW = {}
ACTIVITY_LABELS = {
    'category': 'فتح القسم', 'item': 'فتح المنتج', 'start': 'بدء البوت',
    'home': 'فتح الرئيسية', 'products': 'عرض المنتجات', 'buy': 'بدء الطلب',
    'wallet': 'فتح المحفظة', 'support': 'فتح الدعم', 'receipt': 'إرسال صورة',
    'message': 'إرسال رسالة', 'interaction': 'تفاعل مع البوت',
}


def track_customer_activity(cid, value=None, message=None):
    # Record the Telegram sender, never the chat containing the interaction.
    if not isinstance(cid, int) or cid <= 0:
        return
    if cid == G.get('ADMIN_ID'):
        if value != 'admin:activity':
            ACTIVITY_VIEW.clear()
        return
    if message is not None:
        text = message.get('text', '')
        value = G.get('MENU', {}).get(text)
        if text.startswith('/start'):
            value = 'start'
        elif text.startswith('/products'):
            value = 'products'
        if value is None:
            log_activity(cid, 'receipt' if message.get('photo') else 'message', '')
            return
    value = value or ''
    prefix, _, arg = value.partition(':')
    if value in LEGACY:
        action_name, pid = 'item', LEGACY[value]
    elif prefix in ('product', 'item', 'claude', 'buy'):
        action_name = {'product': 'category', 'claude': 'item'}.get(prefix, prefix)
        pid = LEGACY.get(arg, arg)
    else:
        action_name = {'enter_store': 'home'}.get(prefix, prefix)
        if action_name not in ACTIVITY_LABELS:
            action_name = 'interaction'
        pid = ''
    # Never persist private message text, payment details, or arbitrary callback data.
    log_activity(cid, action_name, pid)


def activity_page(cid):
    with db() as conn:
        rows = conn.execute('SELECT id,cid,action,pid,created_at FROM activity ORDER BY id DESC LIMIT 20').fetchall()
    parts = ['👀 <b>آخر نشاط العملاء</b>', '🟢 تحديث تلقائي أثناء فتح الصفحة (حتى 15 دقيقة).']
    if not rows:
        parts.append('لا يوجد نشاط مسجل حتى الآن.')
    for _, user_id, action_name, pid, created in rows:
        label = ACTIVITY_LABELS.get(action_name, 'تفاعل مع البوت')
        detail = ': <b>' + esc(name(pid, cid)[:100]) + '</b>' if pid else ''
        entry = f'\n{label}{detail}\nالعميل: {customer_link(user_id)} • {esc(created)}'
        if len(('\n'.join(parts) + entry).encode('utf-16-le')) // 2 > 3500:
            break
        parts.append(entry)
    return '\n'.join(parts), rows[0][0] if rows else 0


def activity_keyboard():
    return kb([[btn('🔄 تحديث', 'admin:activity')], [btn('🗑 تصفير النشاط', 'admin:activity_reset')], [btn('↩️ لوحة الإدارة', 'admin')]])


def admin_activity(api, cid):
    if cid != G['ADMIN_ID']:
        return home(api, cid)
    text, latest = activity_page(cid)
    result = send(api, cid, text, activity_keyboard())
    ACTIVITY_VIEW.clear()
    if isinstance(result, dict) and result.get('message_id'):
        ACTIVITY_VIEW.update(cid=cid, message_id=result['message_id'], latest=latest,
                             expires=time.monotonic() + 900)


def tick_customer_activity(api):
    if not ACTIVITY_VIEW:
        return
    if time.monotonic() >= ACTIVITY_VIEW['expires']:
        ACTIVITY_VIEW.clear()
        return
    cid = ACTIVITY_VIEW['cid']
    with db() as conn:
        latest = conn.execute('SELECT COALESCE(MAX(id),0) FROM activity').fetchone()[0]
    if latest == ACTIVITY_VIEW['latest']:
        return
    text, latest = activity_page(cid)
    result = api.call('editMessageText', chat_id=cid, message_id=ACTIVITY_VIEW['message_id'],
                      text=text, parse_mode='HTML', reply_markup=activity_keyboard())
    if result:
        ACTIVITY_VIEW['latest'] = latest
    else:
        # Deleted/inaccessible messages must not cause an endless retry loop.
        ACTIVITY_VIEW.clear()


def admin_icons(api, cid):
    if cid != G['ADMIN_ID']:
        return home(api, cid)
    send(api, cid, '➕ <b>إضافة أيقونة متحركة</b>\n\nاختر أين تريد إضافة الأيقونة:',
         kb([[btn('📦 أسماء المنتجات', 'iconmenu:products', style='primary')],
             [btn('📁 أسماء الأقسام', 'iconmenu:categories')],
             [btn('🏠 أيقونات أزرار الرئيسية', 'iconmenu:buttons', style='primary')],
             [btn('↩️ لوحة الإدارة', 'admin')]]))


def admin_icon_products(api, cid):
    if cid != G['ADMIN_ID']:
        return home(api, cid)
    buttons = []
    seen = set()

    # Every sellable catalog product/variant.
    for pid in VARIANTS:
        if pid in seen:
            continue
        seen.add(pid)
        buttons.append(btn(name(pid, cid), 'seticon:' + pid, ui_icon(pid)))

    # Products created from the admin panel.
    with db() as conn:
        custom_products = conn.execute('SELECT pid,name FROM admin_products ORDER BY rowid').fetchall()
    for pid, product_name in custom_products:
        if pid in seen:
            continue
        seen.add(pid)
        buttons.append(btn(product_name, 'seticon:' + pid, ui_icon(pid)))

    # Standalone built-in products that are not represented by variants.
    for pid, product in G['PRODUCTS'].items():
        if pid in seen:
            continue
        try:
            children = products_in_category(pid)
        except Exception:
            children = [pid]
        if children == [pid]:
            seen.add(pid)
            buttons.append(btn('YouTube' if pid == 'youtube' else name(pid, cid),
                               'seticon:' + pid, ui_icon(pid) or product.get('custom_emoji_id')))

    rows = [[button] for button in buttons]
    send(api, cid, '📦 <b>أسماء المنتجات</b>\n\nاختر المنتج الذي تريد وضع أيقونة متحركة بجانب اسمه:',
         kb(rows + [[btn('↩️ رجوع', 'admin:icons')]]))


def admin_icon_categories(api, cid):
    if cid != G['ADMIN_ID']:
        return home(api, cid)
    buttons = []
    for pid, product in G['PRODUCTS'].items():
        buttons.append(btn('YouTube' if pid == 'youtube' else product['name'],
                           'seticon:' + pid, ui_icon(pid) or product.get('custom_emoji_id')))
    with db() as conn:
        custom_categories = conn.execute('SELECT cid,name FROM admin_categories ORDER BY rowid').fetchall()
    for category_id, category_name in custom_categories:
        buttons.append(btn(category_name, 'seticon:' + category_id, ui_icon(category_id)))
    rows = [[button] for button in buttons]
    send(api, cid, '📁 <b>أسماء الأقسام</b>\n\nاختر القسم الذي تريد إضافة أيقونة متحركة له:',
         kb(rows + [[btn('↩️ رجوع', 'admin:icons')]]))


def admin_icon_buttons(api, cid):
    if cid != G['ADMIN_ID']:
        return home(api, cid)
    buttons = [btn(label, 'seticon:' + key, ui_icon(key)) for key, label in UI_ICON_LABELS.items()]
    for row in payment_methods.methods(sys.modules[__name__]):
        buttons.append(btn(row[1], 'seticon:pay_custom_' + str(row[0]), ui_icon('pay_custom_' + str(row[0]))))
    rows = [[button] for button in buttons]
    send(api, cid, '🔘 <b>أزرار المتجر</b>\n\nاختر الزر الذي تريد إضافة أيقونة متحركة له:',
         kb(rows + [[btn('↩️ رجوع', 'admin:icons')]]))

def begin_icon_setup(api, cid, pid):
    if cid != G['ADMIN_ID']:
        return home(api, cid)
    custom_cat = custom_category(pid)
    cp = custom_product(pid)
    is_variant = pid in VARIANTS
    dynamic_payment = pid.startswith('pay_custom_') and pid[11:].isdigit()
    if pid not in G['PRODUCTS'] and pid not in UI_ICON_LABELS and not custom_cat and not cp and not is_variant and not dynamic_payment:
        return admin_icons(api, cid)
    with db() as conn:
        conn.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)', (cid, 'icon', pid))
    if is_variant:
        label = name(pid, cid)
    elif pid in G['PRODUCTS']:
        label = G['PRODUCTS'][pid]['name']
    elif pid in UI_ICON_LABELS:
        label = UI_ICON_LABELS[pid]
    elif custom_cat:
        label = custom_cat[1]
    elif dynamic_payment:
        row = payment_methods.get(sys.modules[__name__], pid[11:])
        label = row[1] if row else pid
    else:
        label = cp[1]
    send(api, cid, f'أرسل الآن الأيقونة المتحركة الخاصة بـ <b>{esc(label)}</b>.\n\nأرسل رمزًا مخصصًا واحدًا فقط، أو اضغط إلغاء.',
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
    if pid in G['PRODUCTS']:
        G['PRODUCTS'][pid]['custom_emoji_id'] = str(emoji)
    G['CONFIG']['custom_icons_enabled'] = True
    custom_cat = custom_category(pid)
    cp = custom_product(pid)
    if pid in G['PRODUCTS']:
        label = G['PRODUCTS'][pid]['name']
    elif pid in UI_ICON_LABELS:
        label = UI_ICON_LABELS[pid]
    elif custom_cat:
        label = custom_cat[1]
    elif cp:
        label = cp[1]
    else:
        label = pid
    send(api, cid, f'✅ تم حفظ الأيقونة لـ <b>{esc(label)}</b>.',
         kb([[btn('➕ إضافة أيقونة أخرى', 'admin:icons')], [btn('🛍 معاينة المنتجات', 'products')]]))
    return True



def broadcast_product_alert(api, pid, kind='new'):
    """Broadcast a product alert with only a green direct-purchase button."""
    try:
        users = [int(user_id) for user_id in json.loads(USERS_PATH.read_text(encoding='utf-8'))]
    except Exception:
        users = []
    for user_id in users:
        try:
            cp = custom_product(pid)
            heading = '✨ <b>NEW PRODUCT — VEXA STORE</b>' if kind == 'new' else '🔥 <b>BACK IN STOCK — VEXA STORE</b>'
            if cp:
                _, product_name, description, price_usd, available, category_id, stock = cp
                text = heading + '\\n\\n<b>' + esc(product_name) + '</b>'
                if description:
                    text += '\\n\\n' + esc(description)
                text += '\\n\\n💵 <b>$' + esc(price_usd) + '</b>'
                if kind == 'stock':
                    text += '\\n📦 ' + esc(str(stock)) + ' available'
            else:
                text = heading + '\\n\\n<b>' + esc(name(pid, user_id)) + '</b>\\n\\n💵 ' + price(user_id, pid, 'USD')
            send(api, user_id, text, kb([[btn('Buy Now 🛒', 'item:' + pid, style='success')]]))
            time.sleep(0.04)
        except Exception:
            pass


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
            description = product_description(pid, cid)
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


def custom_category(category_id):
    with db() as conn:
        return conn.execute('SELECT cid,name FROM admin_categories WHERE cid=?', (category_id,)).fetchone()


def custom_product(pid):
    with db() as conn:
        return conn.execute('SELECT pid,name,description,price_usd,available,category_id,stock FROM admin_products WHERE pid=?', (pid,)).fetchone()


def name(pid, cid=0):
    pid = LEGACY.get(pid, pid)
    if pid == 'youtube':
        return text_override(pid, 'name', prefs(cid)[0], 'YouTube')
    if pid in VARIANTS:
        return text_override(pid, 'name', prefs(cid)[0], VARIANTS[pid]['name'][prefs(cid)[0]])
    category = custom_category(pid)
    if category:
        return text_override(pid, 'name', prefs(cid)[0], category[1])
    cp = custom_product(pid)
    if cp:
        return text_override(pid, 'name', prefs(cid)[0], cp[1])
    return text_override(pid, 'name', prefs(cid)[0], G['PRODUCTS'].get(pid, {}).get('name', pid))


def reset_navigation_state(cid):
    """Exit any unfinished input/payment flow when the user explicitly starts over or goes home."""
    with db() as conn:
        conn.execute('DELETE FROM discount_input WHERE cid=?', (cid,))
        conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
        conn.execute('DELETE FROM custom_topup_state WHERE cid=?', (cid,))
        conn.execute('DELETE FROM receipts WHERE cid=?', (cid,))
        conn.execute('UPDATE wallet_topups SET status="cancelled" WHERE cid=? AND status="receipt_pending"', (cid,))


def compact_stock(value):
    """Stable stock segment for Telegram RTL/LTR buttons: 📦 4."""
    try:
        value = int(value)
    except Exception:
        value = 0
    return '\u2066📦 ' + str(value) + '\u2069'


def compact_name(pid, cid=0):
    """Short button label for every product; full name stays inside product details."""
    pid = LEGACY.get(pid, pid)
    language = prefs(cid)[0]

    ar = {
        'pd_01':'Pro X5 • شهر',
        'pd_02':'Plus • شهر تجديد',
        'pd_03':'Go • شهر',
        'pd_04':'Plus • Apple Pay • شهر',
        'pd_05':'CODEX 500M • 6 أيام',
        'pd_06':'CODEX 100M • 3 أيام',
        'pd_07':'CODEX 50M • يومان',
        'pd_08':'CODEX 10M • يوم',
        'pd_09':'Pro • شهر تجديد',
        'pd_10':'API 500M • 6 أيام',
        'pd_11':'API 100M • 3 أيام',
        'pd_12':'API 50M • يومان',
        'pd_13':'API 10M • يوم',
        'pd_14':'Pro فردي • 6 أشهر',
        'pd_15':'Pro • شهر + 1600',
        'pd_16':'Pro • شهر',
        'pd_17':'7 أيام',
        'pd_18':'SuperGrok • 3 أشهر',
        'pd_19':'X Premium+ • شهر',
        'pd_20':'Heavy • شهر',
        'pd_21':'SuperGrok • 7 أيام',
        'pd_22':'18 شهر',
        'pd_23':'Edu • 500',
        'pd_24':'Premium 4K • Full Acc • 1M',
        'iptv_1m':'شهر',
        'iptv_3m':'3 أشهر',
        'iptv_6m':'6 أشهر',
        'iptv_1y':'سنة',
    }
    en = {
        'pd_01':'Pro X5 • 1M',
        'pd_02':'Plus • Recharge 1M',
        'pd_03':'Go • 1M',
        'pd_04':'Plus • Apple Pay • 1M',
        'pd_05':'CODEX 500M • 6D',
        'pd_06':'CODEX 100M • 3D',
        'pd_07':'CODEX 50M • 2D',
        'pd_08':'CODEX 10M • 1D',
        'pd_09':'Pro • Recharge 1M',
        'pd_10':'API 500M • 6D',
        'pd_11':'API 100M • 3D',
        'pd_12':'API 50M • 2D',
        'pd_13':'API 10M • 1D',
        'pd_14':'Pro Individual • 6M',
        'pd_15':'Pro • 1M + 1600',
        'pd_16':'Pro • 1M',
        'pd_17':'7D',
        'pd_18':'SuperGrok • 3M',
        'pd_19':'X Premium+ • 1M',
        'pd_20':'Heavy • 1M',
        'pd_21':'SuperGrok • 7D',
        'pd_22':'18M',
        'pd_23':'Edu • 500',
        'pd_24':'Premium 4K • Full Acc • 1M',
        'iptv_1m':'1M',
        'iptv_3m':'3M',
        'iptv_6m':'6M',
        'iptv_1y':'1Y',
    }
    full = name(pid, cid)
    label = full.strip()
    low_full = full.lower().replace('-', ' ').replace('_', ' ')

    # Always prefer the current edited product name over old catalog presets.
    if 'apple pay' in low_full or 'applepay' in low_full:
        if language == 'ar':
            return 'Apple Pay • شهر خاص' if ('خاص' in full or 'private' in low_full) else 'Apple Pay • شهر'
        return 'Apple Pay • Private 1M' if ('خاص' in full or 'private' in low_full) else 'Apple Pay • 1M'
    if 'upi' in low_full or 'ubi' in low_full:
        tag = 'UPI' if 'upi' in low_full else 'UBI'
        if language == 'ar':
            return tag + ' • شهر واحد'
        return tag + ' • 1M'

    # Keep Crunchyroll product buttons LTR and compact so the visual order is
    # always: product name | USD price | stock, even in the Arabic interface.
    if 'ملف خاص' in full or ('private' in low_full and 'profile' in low_full):
        return 'Private Profile • 1M'
    if ('حساب كامل' in full and ('7 أيام' in full or '7 ايام' in full)) or ('full' in low_full and '7' in low_full):
        return 'Full Acc • 7D'
    if ('حساب كامل' in full and ('شهر' in full or '1m' in low_full)) or ('full acc' in low_full and '1m' in low_full):
        return 'Full Acc • 1M'

    # YouTube names are often long; keep the plan type visible so price/stock
    # never get pushed off the Telegram button.
    if 'youtube' in low_full:
        if 'family invitation' in low_full or 'دعوة' in full or 'عائل' in full:
            return 'دعوة عائلية • شهر' if language == 'ar' else 'Family Invite • 1M'
        if 'private full account' in low_full or 'full account' in low_full or 'حساب كامل' in full or 'حساب خاص' in full:
            return 'حساب خاص • شهر' if language == 'ar' else 'Private • 1M'
        if 'premium' in low_full:
            return 'YouTube Premium • شهر' if language == 'ar' else 'YouTube Premium • 1M'

    preset = (en if language == 'en' else ar).get(pid)
    if preset:
        return preset
    category_id = VARIANTS.get(pid, {}).get('category')
    cp = custom_product(pid)
    if not category_id and cp:
        category_id = cp[5]

    # Remove a repeated category/brand prefix from custom and future products.
    if category_id:
        try:
            category_label = name(category_id, cid).strip()
            if category_label and label.lower().startswith(category_label.lower()):
                label = label[len(category_label):].lstrip(' -—|•:').strip()
        except Exception:
            pass

    replacements = (
        [('لمدة ', ''), ('شهر واحد', 'شهر'), ('تجديد رسمي', 'تجديد'),
         ('رمز تفعيل', 'كود'), ('حساب خاص', 'خاص'), ('حساب فردي', 'فردي'),
         ('اشتراك ', ''), ('بضمان كامل', 'ضمان كامل')]
        if language == 'ar' else
        [(' official recharge', ' Recharge'), ('Official Recharge', 'Recharge'),
         ('1 Month', '1M'), ('1 month', '1M'), ('6 Months', '6M'),
         ('3 Months', '3M'), ('7 Days', '7D'), ('Individual Account', 'Individual')]
    )
    for old, new in replacements:
        label = label.replace(old, new)
    label = ' '.join(label.split()).strip(' -—|•:')
    label = label or full
    # Telegram renders inline-button text on one line. Limit only the product
    # name segment so the USD price and stock segment remain fully visible.
    if len(label) > 22:
        label = label[:21].rstrip(' -—|•:') + '…'
    return label


def product_button_icon(pid, cid=0):
    """Return the product button custom icon, except products that should have no leading icon."""
    try:
        low = name(pid, cid).lower().replace('-', ' ').replace('_', ' ')
        if 'upi' in low:
            return None
    except Exception:
        pass
    return ui_icon(pid)


def start(api, cid):
    reset_navigation_state(cid)
    send(api, cid,
         '🌐 <b>اختر لغتك | Choose your language</b>',
         kb([[btn('🇸🇦 العربية', 'setlang:ar'),
              btn('🇺🇸 English', 'setlang:en')]]))


def home(api, cid):
    balance_sar = wallet_balance(cid)
    balance_usd = (balance_sar / RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    with db() as conn:
        purchases = conn.execute('SELECT COUNT(*) FROM orders WHERE cid=? AND status="paid"', (cid,)).fetchone()[0]
    text = tr(cid, f'👋 <b>أهلاً بك في VEXA STORE!</b>\n\n🆔 رقم العضوية: <code>{cid}</code>\n👤 حسابك: <a href="tg://user?id={cid}">فتح الحساب</a>\n💳 الرصيد: <b>${balance_usd:.2f}</b>\n🛍 المشتريات: <b>{purchases}</b>\n\nاختر من القائمة أدناه:', f'👋 <b>Welcome to VEXA STORE!</b>\n\n🆔 Member ID: <code>{cid}</code>\n👤 Account: <a href="tg://user?id={cid}">Open profile</a>\n💳 Balance: <b>${balance_usd:.2f}</b>\n🛍 Purchases: <b>{purchases}</b>\n\nChoose from the menu below:')
    rows = [[btn(tr(cid,'المنتجات','Products'),'products',ui_icon('ui_products'),style='primary'), btn(tr(cid,'شحن الرصيد','Top up'),'wallet:topup',ui_icon('ui_topup'),style='primary')], [btn(tr(cid,'الإحالات','Referrals'),'referrals',ui_icon('ui_referrals'),style='primary'), btn(tr(cid,'حسابي','My account'),'wallet',ui_icon('ui_account'),style='primary')], [btn(tr(cid,'تواصل مع الدعم','Contact support'),'support',ui_icon('ui_support'),style='primary'), btn(tr(cid,'إبلاغ عن مشكلة','Report issue'),'support',ui_icon('ui_report'),style='primary')], [btn('Language / اللغة','settings:lang',ui_icon('ui_language'),style='primary')]]
    rows.append([btn(tr(cid, '📢 مجتمع VEXA STORE', '📢 VEXA STORE Community'), 'community', ui_icon('ui_community'), style='primary')])
    if cid == G.get('ADMIN_ID'):
        rows.append([btn('لوحة الطلبات', 'admin', ui_icon('ui_admin'), style='primary')])
    send(api, cid, text, kb(rows))

def products(api, cid):
    buttons = [btn(category_label(pid, cid), 'product:' + pid, ui_icon(pid) or p.get('custom_emoji_id')) for pid, p in G['PRODUCTS'].items() if category_visible(pid)]
    with db() as conn:
        custom_categories = conn.execute('SELECT cid,name FROM admin_categories ORDER BY rowid').fetchall()
    buttons += [btn(name(category_id, cid), 'product:' + category_id, ui_icon(category_id)) for category_id, category_name in custom_categories if category_visible(category_id)]
    rows = [buttons[i:i+3] for i in range(0, len(buttons), 3)]
    rows += [[btn(tr(cid, '🏠 الرئيسية', '🏠 Home'), 'home')]]
    send(api, cid, tr(cid, '🛍 <b>المنتجات</b>\nاختر الخدمة:', '🛍 <b>Products</b>\nChoose a service:'), kb(rows))


def settings(api, cid, kind):
    if kind == 'lang':
        rows = [[btn('العربية', 'setlang:ar'), btn('English', 'setlang:en')]]
        text = '🌐 اختر اللغة / Choose language'
    else:
        rows = [[btn('🇺🇸 USD — US Dollar', 'setcurrency:USD')]]
        text = '💱 أسعار المنتجات ثابتة بالدولار USD\nيظهر التحويل للريال السعودي عند الدفع فقط.'
    send(api, cid, text, kb(rows + [nav(cid)]))


def card(api, cid, image_path, title, text, keyboard, pid=None):
    """Separate photo and full text so Telegram's caption limit never drops terms."""
    override = saved_product_photo(pid) if pid else None
    if override is not None:
        image_path = None
        if override:
            api.call('sendPhoto', chat_id=cid, photo=override, caption=title[:900])
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
    # Product text contains safe HTML markup for bold/custom emoji.
    # Send it directly so Telegram parses the custom emoji entity.
    send(api, cid, text, keyboard)



def product_visible(pid):
    with db() as conn:
        row = conn.execute('SELECT visible FROM product_visibility WHERE pid=?', (pid,)).fetchone()
    if row is not None:
        return bool(row[0])
    # Default ChatGPT catalogue requested by the store owner.
    if VARIANTS.get(pid, {}).get('category') == 'chatgpt':
        return pid in ('pd_01', 'pd_04', 'pd_05')
    return True


def category_visible(pid):
    with db() as conn:
        ids = [row[0] for row in conn.execute('SELECT pid FROM admin_products WHERE category_id=?', (pid,))]
    if pid in G['PRODUCTS']:
        variants = [v['id'] for v in VARIANTS.values() if v['category'] == pid]
        ids += variants or [pid]
    return any(map(product_visible, ids))


def visibility_categories(api, cid):
    if cid != G['ADMIN_ID']:
        return
    with db() as conn:
        custom = conn.execute('SELECT cid,name FROM admin_categories ORDER BY rowid').fetchall()
    categories = [(pid, p['name']) for pid, p in G['PRODUCTS'].items()] + custom
    rows = [[btn(name, 'viscat:' + pid)] for pid, name in categories]
    send(api, cid, '👁 <b>إظهار وإخفاء المنتجات</b>\n\nاختر القسم:', kb(rows + [[btn('↩️ لوحة الإدارة', 'admin')]]))


def category_product_ids(category_id):
    """Return original and added products, including hidden/out-of-stock items."""
    with db() as conn:
        custom_ids = [row[0] for row in conn.execute(
            'SELECT pid FROM admin_products WHERE category_id=? ORDER BY rowid',
            (category_id,))]
    originals = [pid for pid, v in VARIANTS.items() if v['category'] == category_id]
    if not originals and category_id in G['PRODUCTS']:
        originals = [category_id]
    return list(dict.fromkeys(originals + custom_ids))


def visibility_products(api, cid, category_id):
    if cid != G['ADMIN_ID']:
        return
    if category_id not in G['PRODUCTS'] and not custom_category(category_id):
        return visibility_categories(api, cid)
    ids = category_product_ids(category_id)
    rows = [[btn(('👁 ' if product_visible(pid) else '🙈 ') + name(pid, cid), 'vistoggle:' + pid)] for pid in ids]
    send(api, cid, '👁 ظاهر في المتجر | 🙈 مخفي من المتجر\nالمنتج المخفي يبقى هنا حتى تستطيع إظهاره مجددًا.',
         kb(rows + [[btn('↩️ الأقسام', 'admin:visibility')]]))


def toggle_visibility(api, cid, pid):
    if cid != G['ADMIN_ID']:
        return
    cp = custom_product(pid)
    if pid not in VARIANTS and pid not in G['PRODUCTS'] and not cp:
        return visibility_categories(api, cid)
    category_id = VARIANTS[pid]['category'] if pid in VARIANTS else (cp[5] if cp else pid)
    with db() as conn:
        conn.execute('INSERT OR REPLACE INTO product_visibility(pid,visible) VALUES (?,?)',
                     (pid, 0 if product_visible(pid) else 1))
    visibility_products(api, cid, category_id)


def chatgpt_visibility_admin(api, cid):
    if cid != G['ADMIN_ID']:
        return home(api, cid)
    choices = [v for v in VARIANTS.values() if v.get('category') == 'chatgpt']
    rows = []
    for v in choices:
        visible = product_visible(v['id'])
        rows.append([btn(('👁 ' if visible else '🙈 ') + name(v['id'], cid),
                         'chatgptvis:' + v['id'],
                         style='success' if visible else 'danger')])
    rows.append([btn('↩️ لوحة الإدارة', 'admin')])
    send(api, cid, '🤖 <b>إظهار وإخفاء منتجات ChatGPT</b>\n\n👁 ظاهر للعملاء\n🙈 مخفي عن العملاء\n\nاضغط على المنتج لتغيير حالته.', kb(rows))


def toggle_chatgpt_visibility(api, cid, pid):
    if cid != G['ADMIN_ID'] or VARIANTS.get(pid, {}).get('category') != 'chatgpt':
        return
    new_value = 0 if product_visible(pid) else 1
    with db() as conn:
        conn.execute('INSERT OR REPLACE INTO product_visibility(pid,visible) VALUES (?,?)', (pid, new_value))
    chatgpt_visibility_admin(api, cid)


def chatgpt_cards(api, cid, choices):
    """Show ChatGPT products using the same card layout as Grok."""
    return grok_cards(api, cid, [v for v in choices if product_visible(v['id'])])


def grok_cards(api, cid, choices, show_heading=True, default_image="assets/grok.png"):
    """Compact photo cards for Grok only; prices share the checkout source."""
    if show_heading:
        send(api, cid, tr(cid, '✦ <b>اشتراكات Grok</b>\nاختر الباقة المناسبة لك:', '✦ <b>Grok subscriptions</b>\nChoose your plan:'))
    for v in choices:
        pid = v['id']
        available = can_order(pid)
        if v.get('review_required'):
            status = tr(cid, '⏸ قيد المراجعة — الطلب غير متاح', '⏸ Under review — ordering unavailable')
        elif not in_stock(pid):
            status = tr(cid, '🔴 نفدت الكمية', '🔴 Out of stock')
        else:
            status = tr(cid, '🟢 متوفر', '🟢 Available')
        caption = '<b>' + esc(name(pid, cid)) + '</b>\n\n'
        caption += '💰 <b>' + price(cid, pid, 'USD') + '</b>\n\n' + status
        if v.get('manual_delivery'):
            caption += '\n' + tr(cid, '✉️ يتم إرسال بيانات المنتج بعد تأكيد الدفع', '✉️ Product details are sent after payment confirmation')
        details = btn(tr(cid, '📋 التفاصيل', '📋 Details'), 'item:' + pid)
        rows = [[btn(tr(cid, '🛒 شراء الآن', '🛒 Buy now'), 'buy:' + pid, style='success'), details]] if available else [[btn(tr(cid, '🔴 غير متوفر', '🔴 Unavailable'), 'item:' + pid, style='danger')]]
        if not available:
            rows[0][0]['style'] = 'danger'
        rows.append([btn(tr(cid, '⚡ VEXA VOLT', '⚡ VEXA VOLT'), 'support')])
        markup = kb(rows)
        override = saved_product_photo(pid)
        if override is not None:
            if not override or not api.call('sendPhoto', chat_id=cid, photo=override, caption=caption, parse_mode='HTML', reply_markup=markup):
                send(api, cid, caption, markup)
            continue
        path = (BASE / (v.get('image') or default_image)).resolve()
        delivered = False
        if path.is_relative_to(BASE) and path.is_file():
            boundary = 'VEXA' + uuid.uuid4().hex
            fields = {'chat_id': str(cid), 'caption': caption, 'parse_mode': 'HTML',
                      'reply_markup': json.dumps(markup, ensure_ascii=False)}
            body = b''
            for key, value in fields.items():
                body += f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
            body += f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="{path.name}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
            body += path.read_bytes() + f'\r\n--{boundary}--\r\n'.encode()
            try:
                api_url = getattr(api, 'base_url', None) or getattr(api, 'u', None)
                req = urllib.request.Request(api_url + 'sendPhoto', body, {'Content-Type': f'multipart/form-data; boundary={boundary}'})
                with urllib.request.urlopen(req, timeout=40) as response:
                    delivered = bool(json.load(response).get('ok'))
            except Exception as exc:
                print('Grok card image failed:', type(exc).__name__)
        if not delivered:
            send(api, cid, caption, markup)
    send(api, cid, tr(cid, 'تصفح أقسام المتجر:', 'Browse store categories:'),
         kb([[btn(tr(cid, '↩️ الأقسام', '↩️ Categories'), 'products'),
              btn(tr(cid, '🏠 الرئيسية', '🏠 Home'), 'home')]]))


def category(api, cid, pid):
    if not category_visible(pid):
        return products(api, cid)
    p = G['PRODUCTS'].get(pid)
    if not p:
        custom_cat = custom_category(pid)
        if not custom_cat:
            products(api, cid)
            return
        with db() as conn:
            choices = conn.execute('SELECT pid,name,price_usd,available,stock FROM admin_products WHERE category_id=? ORDER BY rowid', (pid,)).fetchall()
        rows = []
        for product_id, product_name, price_usd, available, stock in choices:
            if not product_visible(product_id):
                continue
            sold_out = not available or int(stock or 0) <= 0
            qty = int(stock or 0)
            label = compact_name(product_id, cid) + ' | 💵 ' + price(cid, product_id, 'USD') + ' | ' + compact_stock(qty)
            if sold_out: label = '🔴 ' + label
            rows.append([btn(label, 'item:' + product_id, product_button_icon(product_id, cid), style='danger' if sold_out else 'success')])
        send(api, cid, category_heading(pid, cid), kb(rows + [nav(cid)]))
        return
    if pid == 'youtube':
        with db() as conn:
            yt_rows = conn.execute('''SELECT DISTINCT p.pid,p.name,p.price_usd,p.available,p.stock
                FROM admin_products p
                LEFT JOIN admin_categories c ON c.cid=p.category_id
                WHERE p.category_id='youtube'
                   OR lower(p.name) LIKE '%youtube%'
                   OR p.name LIKE '%يوتيوب%'
                   OR lower(COALESCE(c.name,'')) LIKE '%youtube%'
                   OR COALESCE(c.name,'') LIKE '%يوتيوب%'
                ORDER BY p.rowid''').fetchall()
        if yt_rows:
            rows = []
            for product_id, product_name, price_usd, available, stock in yt_rows:
                if not product_visible(product_id):
                    continue
                qty = int(stock or 0)
                sold_out = (not bool(available)) or qty <= 0
                label = ('🔴 ' if sold_out else '🟢 ') + compact_name(product_id, cid) + ' | 💵 ' + price(cid, product_id, 'USD') + ' | ' + compact_stock(qty)
                rows.append([btn(label, 'item:' + product_id, product_button_icon(product_id, cid),
                                 style='danger' if sold_out else 'success')])
            if rows:
                send(api, cid, category_heading(pid, cid), kb(rows + [nav(cid)]))
                return
    choices = [v for v in VARIANTS.values() if v['category'] == pid and product_visible(v['id'])]
    if pid == 'chatgpt' and choices:
        return chatgpt_cards(api, cid, choices)
    if choices:
        rows = []
        for v in choices:
            sold_out = not in_stock(v['id'])
            status = '⏸ ' if v.get('review_required') else (tr(cid, '🔴 نفد | ', '🔴 SOLD OUT | ') if sold_out else '')
            if pid == 'chatgpt':
                # Keep the price at the beginning so Telegram cannot hide it
                # when a long product name is truncated on mobile.
                label = status + '💰 ' + price(cid, v['id']) + ' • ' + name(v['id'], cid)
            else:
                label = status + name(v['id'], cid) + ' | ' + price(cid, v['id'])
            variant_icon = product_button_icon(v['id'], cid)
            rows.append([btn(label, 'item:' + v['id'], p.get('custom_emoji_id') if variant_icon is None else variant_icon, style='danger' if sold_out else 'success')])
        send(api, cid, category_heading(pid, cid), kb(rows + [nav(cid)]))
        return
    english = {'youtube': 'YouTube Premium for one month. Ad-free viewing, background playback, offline downloads and YouTube Music Premium benefits.',
               'netflix': 'Netflix subscription for movies, series and entertainment.', 'iptv': 'IPTV subscriptions for compatible devices.'}
    description = product_description(pid, cid)
    text = esc(name(pid, cid)) + '\n\n' + esc(price(cid, pid)) + '\n\n' + esc(tr(cid, '✅ متوفر' if in_stock(pid) else '🔴 نفدت الكمية', '✅ Available' if in_stock(pid) else '🔴 Out of stock')) + '\n\n' + esc(description)
    rows = [[btn(tr(cid, '🛒 طلب المنتج', '🛒 Order'), 'buy:' + pid)]] if can_order(pid) else []
    rows += [[btn(tr(cid, '⚡ VEXA VOLT', '⚡ VEXA VOLT'), 'support')], nav(cid)]
    card(api, cid, f'assets/{pid}.png', name(pid, cid), text, kb(rows), pid=pid)


def item(api, cid, pid):
    pid = LEGACY.get(pid, pid)
    if not product_visible(pid):
        return send(api, cid, tr(cid, 'هذا المنتج مخفي حاليًا.', 'This product is currently hidden.'), kb([nav(cid, 'products')]))
    v = VARIANTS.get(pid)
    if not v:
        cp = custom_product(pid)
        if not cp:
            products(api, cid)
            return
        _, product_name, description, price_usd, available, category_id, stock = cp
        status = tr(cid, '✅ متوفر', '✅ Available') if available and int(stock or 0) > 0 else tr(cid, '🔴 نفدت الكمية', '🔴 Out of stock')
        text = esc(name(pid, cid)) + '\n\n' + info_block(pid,cid) + '\n\n' + esc(status) + '\n\n' + esc(product_description(pid, cid))
        rows = [[btn(tr(cid, '🛒 طلب المنتج', '🛒 Order'), 'buy:' + pid)]] if can_order(pid) else []
        rows += [[btn(tr(cid, '⚡ VEXA VOLT', '⚡ VEXA VOLT'), 'support')], nav(cid, 'product:' + category_id)]
        card(api, cid, None, name(pid, cid), text, kb(rows), pid=pid)
        return
    lang = prefs(cid)[0]
    available = ''
    if not in_stock(pid):
        available = tr(cid, '🚫 نفد لدى المورد وقت المراجعة. الطلب غير متاح حاليًا.', '🚫 Out of stock at the last supplier check. Ordering is currently unavailable.')
    text = esc(name(pid, cid)) + '\n\n' + info_block(pid,cid) + (('\n\n' + esc(available)) if available else '') + '\n\n' + esc(product_description(pid, cid))
    if v.get('promotions'):
        text += '\n\n' + esc(tr(cid, 'أسعار الكميات — تواصل مع الدعم:', 'Bulk prices — contact support:'))
        for tier in v['promotions']:
            text += '\n' + esc(tier['min_quantity']) + '+: ' + esc(price(cid, pid, source_price=tier['source_usd'])) + esc(tr(cid, ' لكل قطعة', ' per unit'))
    if v.get('review_required'):
        text += '\n\n⚠️ ' + esc(v['review_required'][lang])
    rows = []
    if can_order(pid):
        order_label = tr(cid, '🛒 طلب قطعة واحدة', '🛒 Order one item')
        if v.get('category') == 'chatgpt':
            order_label = tr(cid, '🛒 شراء الآن • ', '🛒 Buy now • ') + price(cid, pid)
        rows.append([btn(order_label, 'buy:' + pid, style='primary' if v.get('category') == 'chatgpt' else None)])
    rows += [[btn(tr(cid, '⚡ VEXA VOLT', '⚡ VEXA VOLT'), 'support')], nav(cid, 'product:' + v['category'])]
    card(api, cid, v.get('image'), name(pid, cid), text, kb(rows), pid=pid)


def can_order(pid):
    pid = LEGACY.get(pid, pid)
    if not product_visible(pid):
        return False
    if pid in VARIANTS:
        return in_stock(pid) and not VARIANTS[pid].get('review_required')
    if custom_product(pid):
        return in_stock(pid) and amount(pid) is not None
    return pid in G['PRODUCTS'] and in_stock(pid) and amount(pid) is not None


def back(pid):
    pid = LEGACY.get(pid, pid)
    return ('item:' if pid in VARIANTS or custom_product(pid) else 'product:') + pid


def checkout_totals(cid, pid):
    return discounts.totals(sys.modules[__name__], cid, pid)


def summary(cid, pid):
    qty = product_options.selected(sys.modules[__name__], cid, pid)
    sar, usd, discount, code = checkout_totals(cid, pid)
    text = esc(name(pid,cid)) + '\n💵 سعر الوحدة: ' + price(cid,pid,'USD') + f'\n🛍 الكمية: {qty}'
    if code:
        discount_usd = (Decimal(str(discount)) / RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        text += '\n🎟 ' + esc(code) + f' — الخصم: {discount_usd:.2f} USD / {discount:.2f} SAR'
    text += f'\n\n💲 <b>الإجمالي بالدولار:</b> {usd:.2f} USD'
    text += f'\n🇸🇦 <b>الإجمالي بالريال:</b> {sar:.2f} SAR'
    return text


def wallet(api, cid):
    sar = wallet_balance(cid).quantize(Decimal('0.01'))
    usd = (sar / RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    text = tr(cid, '<b>محفظة VEXA</b>\n\nرصيدك الحالي:', '<b>VEXA Wallet</b>\n\nYour current balance:')
    text += f'\n<b>{sar:.2f} {tr(cid, "ر.س", "SAR")}</b>\n<b>{usd:.2f} USD</b>'
    rows = [[btn(tr(cid, 'إضافة رصيد', 'Add funds'), 'wallet:topup', ui_icon('ui_wallet_add'), style='success'),
             btn(tr(cid, 'تحويل', 'Transfer'), 'wallet:transfer', ui_icon('ui_wallet_transfer'), style='primary')],
            [btn(tr(cid, 'الرجوع للقائمة', 'Back to Menu'), 'home', ui_icon('ui_wallet_back'), style='primary')]]
    send(api, cid, text, kb(rows))


def wallet_amounts(api, cid):
    rows = []
    for pair in ((20, 50), (100, 200)):
        row = []
        for value in pair:
            usd = (Decimal(value) / RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            row.append(btn(f'{value} {tr(cid, "ر.س", "SAR")} / {usd:.2f} USD', f'topup:{value}', style='success'))
        rows.append(row)
    text = tr(cid, 'اختر مبلغ إضافة الرصيد — جميع المبالغ معروضة بالريال والدولار:',
              'Choose an add-funds amount — all amounts are shown in SAR and USD:')
    rows.append([btn(tr(cid, 'مبلغ اختياري بالدولار', 'Custom amount in USD'), 'topupcustom', style='success')])
    rows.append([btn(tr(cid, 'رجوع', 'Back'), 'wallet', style='primary')])
    send(api, cid, text, kb(rows))


def wallet_method(api, cid, value):
    try:
        value = Decimal(value).quantize(Decimal('0.01'))
    except Exception:
        return wallet_amounts(api, cid)
    if value <= 0 or value > 5000:
        return wallet_amounts(api, cid)
    usd = (value / RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    text = tr(cid, 'اختر طريقة إضافة الرصيد:', 'Choose an add-funds method:') + f'\n\n<b>{value:.2f} SAR / {usd:.2f} USD</b>'
    rows = [
        [btn('Crypto Pay', f'topupcrypto:{value}', ui_icon('pay_cryptopay'), style='success')],
        [btn('Bybit / USDT', f'topupbybit:{value}', ui_icon('pay_bybit'), style='success')]
    ]
    for method in payment_methods.methods(sys.modules[__name__], True):
        rows.append([btn(method[1], f'topupmanual:{method[0]}:{value}', ui_icon('pay_custom_' + str(method[0])), style='success')])
    rows.append([btn(tr(cid, 'رجوع', 'Back'), 'wallet:topup', style='primary')])
    send(api, cid, text, kb(rows))


def wallet_transfer_begin(api, cid):
    with db() as conn:
        conn.execute('INSERT OR REPLACE INTO wallet_transfer_state(cid,step,recipient,amount_sar) VALUES (?,? ,NULL,NULL)', (cid, 'recipient'))
    send(api, cid, tr(cid, 'أرسل رقم ID للمستخدم المستلم داخل البوت.', 'Send the recipient user ID inside the bot.'),
         kb([[btn(tr(cid, 'إلغاء', 'Cancel'), 'wallet', style='primary')]]))


def wallet_manual_topup(api, cid, method_id, value):
    row = payment_methods.get(sys.modules[__name__], str(method_id))
    if not row:
        return wallet_method(api, cid, value)
    try:
        value = Decimal(value).quantize(Decimal('0.01'))
    except Exception:
        return wallet_amounts(api, cid)
    topup_id = uuid.uuid4().hex[:16]
    with db() as conn:
        conn.execute('INSERT INTO wallet_topups VALUES (?,?,?,?,?,?)', (topup_id, cid, str(value), 'manual_' + str(method_id), None, 'pending'))
    usd = (value / RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    details = payment_methods.details(sys.modules[__name__], dict(zip(('name','holder','account'), row[1:4])))
    text = details + f'\n\n<b>{value:.2f} SAR / {usd:.2f} USD</b>\n\n' + tr(cid, 'بعد التحويل اضغط تم التحويل ثم أرسل صورة الإثبات.', 'After paying, tap Payment sent, then send the receipt image.')
    send(api, cid, text, kb([[btn(tr(cid, 'تم التحويل', 'Payment sent'), 'topupreceipt:' + topup_id, style='success')],
                             [btn(tr(cid, 'رجوع', 'Back'), f'topup:{value}', style='primary')]]))


def wallet_transfer_confirm(api, cid, recipient, amount_sar):
    try:
        recipient = int(recipient)
        amount = Decimal(str(amount_sar)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except Exception:
        return wallet(api, cid)
    if recipient == cid or amount <= 0:
        return wallet(api, cid)
    transfer_id = uuid.uuid4().hex[:16]
    with db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('INSERT OR IGNORE INTO wallets(cid,balance_sar) VALUES (?,?)', (cid, '0'))
        conn.execute('INSERT OR IGNORE INTO wallets(cid,balance_sar) VALUES (?,?)', (recipient, '0'))
        current = Decimal(conn.execute('SELECT balance_sar FROM wallets WHERE cid=?', (cid,)).fetchone()[0])
        if current < amount:
            conn.rollback()
            return send(api, cid, tr(cid, 'رصيدك غير كافٍ لإتمام التحويل.', 'Your balance is insufficient for this transfer.'), kb([[btn(tr(cid, 'رجوع', 'Back'), 'wallet', style='primary')]]))
        sender_new = (current - amount).quantize(Decimal('0.01'))
        recipient_current = Decimal(conn.execute('SELECT balance_sar FROM wallets WHERE cid=?', (recipient,)).fetchone()[0])
        recipient_new = (recipient_current + amount).quantize(Decimal('0.01'))
        conn.execute('UPDATE wallets SET balance_sar=? WHERE cid=?', (str(sender_new), cid))
        conn.execute('UPDATE wallets SET balance_sar=? WHERE cid=?', (str(recipient_new), recipient))
        conn.execute('INSERT INTO wallet_transfers VALUES (?,?,?,?,?)', (transfer_id, cid, recipient, str(amount), now_saudi()))
        conn.execute('DELETE FROM wallet_transfer_state WHERE cid=?', (cid,))
        conn.commit()
    usd = (amount / RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    send(api, cid, tr(cid, 'تم تحويل الرصيد بنجاح.', 'Balance transferred successfully.') + f'\n<b>{amount:.2f} SAR / {usd:.2f} USD</b>', kb([[btn(tr(cid, 'الرجوع للمحفظة', 'Back to Wallet'), 'wallet', style='primary')]]))
    try:
        send(api, recipient, tr(recipient, 'تمت إضافة رصيد إلى محفظتك من مستخدم آخر.', 'Balance was added to your wallet by another user.') + f'\n<b>{amount:.2f} SAR / {usd:.2f} USD</b>', menu(recipient))
    except Exception:
        pass


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
         kb([[btn('Bybit Pay', 'topupsend:bybitid:' + topup_id, ui_icon('pay_bybitid'), style='success')],
             [btn('USDT • TRON (TRC20)', 'topupsend:trc20:' + topup_id, ui_icon('pay_trc20'), style='success')],
             [btn('USDT • BSC (BEP20)', 'topupsend:bep20:' + topup_id, ui_icon('pay_bep20'), style='success')],
             [btn(tr(cid, 'رجوع', 'Back'), f'topup:{value}', style='primary')]]))


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
    warning = tr(cid, 'يتم تنفيذ الطلب بعد مراجعة الدفع وتأكيد التوفر، ثم إرسال بيانات المنتج إليك.', 'Your order is fulfilled after payment review and availability confirmation, then the product details are sent to you.')
    send(api, cid, tr(cid, '💳 <b>اختر طريقة الدفع</b>\n\n', '💳 <b>Choose payment method</b>\n\n') + summary(cid, pid) + '\n\n' + warning,
         kb([[btn(tr(cid, '🎟 كود خصم', '🎟 Discount code'), 'coupon:' + pid, style='primary'), btn(tr(cid, 'إزالة الخصم', 'Remove discount'), 'couponremove:' + pid)],
             [btn(tr(cid, 'المحفظة', 'Wallet'), 'paywallet:' + pid, ui_icon('pay_wallet'))],
             [btn('Crypto Pay', 'paycrypto:' + pid, ui_icon('pay_cryptopay'))],
             [btn('USDT — Bybit', 'paybybit:' + pid, ui_icon('pay_bybit'))],
             [btn('⭐ نجوم تيليجرام', 'paystars:' + pid)],
             [btn('🎁 هدايا تيليجرام', 'paygifts:' + pid)]] + payment_methods.rows(sys.modules[__name__], pid) + [nav(cid, back(pid))]))


def payment(api, cid, pid, method):
    if not can_order(pid):
        payments(api, cid, pid)
        return
    if checkout_totals(cid, pid)[0] == 0:
        return pay_with_wallet(api, cid, pid)
    if method == 'bybit':
        send(api, cid, '🪙 <b>USDT — Bybit</b>\n\n' + summary(cid, pid),
             kb([[btn('Bybit Pay', 'bybitid:' + pid, ui_icon('pay_bybitid'))], [btn('USDT • TRON (TRC20)', 'trc20:' + pid, ui_icon('pay_trc20'))], [btn('USDT • BSC (BEP20)', 'bep20:' + pid, ui_icon('pay_bep20'))], nav(cid, 'buy:' + pid)]))
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
        sar, usd, _, _ = checkout_totals(cid, pid)
        with db() as conn:
            conn.execute('INSERT OR REPLACE INTO payment_quotes VALUES (?,?,?,?,?)', (cid, pid, method, str(usd), str(sar)))
        text += '\n\n' + tr(cid, 'بعد التحويل أرسل صورة الإثبات للمراجعة.', 'After transferring, submit a receipt photo for review.')
        rows.append([btn(tr(cid, '✅ تم التحويل', '✅ Payment sent'), f'receipt:{method}:{pid}')])
    else:
        text += '\n\n' + tr(cid, 'بيانات الدفع غير مكتملة. تواصل مع الدعم: ', 'Payment details are incomplete. Contact support: ') + SUPPORT
    send(api, cid, text, kb(rows + [nav(cid, 'buy:' + pid)]))


def pay_with_wallet(api, cid, pid):
    if not can_order(pid):
        return payments(api, cid, pid)
    cost, paid_usd, _, _ = checkout_totals(cid, pid)
    remaining = wallet_debit(cid, cost)
    if remaining is None:
        send(api, cid, tr(cid, 'رصيد المحفظة غير كافٍ.', 'Insufficient wallet balance.') +
             f'\n\n{tr(cid, "المطلوب", "Required")}: {cost:.2f} SAR\n{tr(cid, "الرصيد", "Balance")}: {wallet_balance(cid):.2f} SAR',
             kb([[btn(tr(cid, '➕ شحن المحفظة', '➕ Top up wallet'), 'wallet:topup')], nav(cid, 'buy:' + pid)]))
        return
    order_id = add_order(cid, pid, 'wallet', 'paid', usd=paid_usd, sar=cost)
    send(api, G['ADMIN_ID'], f'🛒 <b>طلب مدفوع من المحفظة #{order_id}</b>\n\n' + esc(name(pid, cid)) + f'\nالكمية: {product_options.snapshot(sys.modules[__name__], "order", order_id)}\nالسعر المدفوع: {paid_usd} USD\nالعميل: <code>{cid}</code>')
    if fulfill_paid_order(api, order_id):
        return
    send(api, cid, tr(cid, '✅ تم الدفع من المحفظة وإرسال الطلب للإدارة.', '✅ Paid from your wallet and the order was sent to administration.') + f'\n\n{tr(cid, "الرصيد المتبقي", "Remaining balance")}: {remaining:.2f} SAR', menu(cid))


def pay_with_crypto(api, cid, pid):
    if not can_order(pid):
        return payments(api, cid, pid)
    sar, usd, _, _ = checkout_totals(cid, pid)
    if usd == 0:
        return pay_with_wallet(api, cid, pid)
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
        row = conn.execute('SELECT pid,external_id,status,amount_usd FROM crypto_orders WHERE id=? AND cid=?', (order_id, cid)).fetchone()
    if not row:
        return products(api, cid)
    pid, invoice_id, status, paid_usd = row
    if status == 'paid':
        return send(api, cid, tr(cid, '✅ هذه الفاتورة مدفوعة وتم إرسال الطلب.', '✅ This invoice is paid and the order was sent.'), menu(cid))
    if not crypto_paid(invoice_id):
        return send(api, cid, tr(cid, 'لم يصل الدفع بعد. أكمل الفاتورة ثم أعد التحقق.', 'Payment has not arrived yet. Complete the invoice and check again.'),
                    kb([[btn(tr(cid, '🔄 تحقق مرة أخرى', '🔄 Check again'), 'checkorder:' + order_id)], nav(cid, 'buy:' + pid)]))
    with db() as conn:
        changed = conn.execute('UPDATE crypto_orders SET status="paid" WHERE id=? AND status="pending"', (order_id,)).rowcount
    if changed:
        saved_order_id = add_order(cid, pid, 'cryptopay', 'paid', usd=paid_usd, sar=(Decimal(paid_usd)*RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP), quantity=product_options.snapshot(sys.modules[__name__], 'crypto', order_id))
        send(api, G['ADMIN_ID'], f'💠 <b>طلب Crypto Pay مدفوع #{saved_order_id}</b>\n\n' + esc(name(pid, cid)) + f'\nالكمية: {product_options.snapshot(sys.modules[__name__], "order", saved_order_id)}\nالسعر المدفوع: {paid_usd} USD\nالعميل: <code>{cid}</code>')
        if fulfill_paid_order(api, saved_order_id):
            return
    send(api, cid, tr(cid, '✅ تم الدفع وإرسال الطلب للإدارة.', '✅ Payment received and the order was sent to administration.'), menu(cid))


def receipt_request(api, cid, pid, method):
    if not can_order(pid) or (method not in ('bank', 'bybitid', 'trc20', 'bep20') and not payment_methods.valid(sys.modules[__name__], method)):
        payments(api, cid, pid)
        return
    sar, usd, _, _ = checkout_totals(cid, pid)
    with db() as conn:
        quote = conn.execute('SELECT usd,sar FROM payment_quotes WHERE cid=? AND pid=? AND method=?', (cid, pid, method)).fetchone()
        if quote:
            usd, sar = quote
        conn.execute('INSERT OR REPLACE INTO receipts VALUES (?,?,?,?,?)', (cid, pid, method, str(usd), str(sar)))
    send(api, cid, tr(cid, '📸 أرسل صورة إثبات الدفع هنا. ستصل للإدارة للمراجعة.', '📸 Send your payment receipt photo here. It will be sent to the administrator for review.'), kb([[btn(tr(cid, '❌ إلغاء', '❌ Cancel'), 'cancel:' + pid)]]))


def receipt(api, message):
    if payment_methods.message(sys.modules[__name__], api, message):
        return True
    cid = message['chat']['id']
    if discounts.message(sys.modules[__name__], api, message):
        return True
    if handle_admin_photo(api, message):
        return True
    if handle_admin_text(api, message):
        return True
    if handle_admin_price(api, message):
        return True
    if handle_button_label(api, message):
        return True
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
                    with db() as conn:
                        conn.execute('INSERT INTO user_delivery_status(cid,departed,updated_at) VALUES (?,?,?) ON CONFLICT(cid) DO UPDATE SET departed=excluded.departed, updated_at=excluded.updated_at', (user_id, 0, now_saudi()))
                else:
                    failed += 1
                    with db() as conn:
                        conn.execute('INSERT INTO user_delivery_status(cid,departed,updated_at) VALUES (?,?,?) ON CONFLICT(cid) DO UPDATE SET departed=excluded.departed, updated_at=excluded.updated_at', (user_id, 1, now_saudi()))
            except Exception:
                failed += 1
                with db() as conn:
                    conn.execute('INSERT INTO user_delivery_status(cid,departed,updated_at) VALUES (?,?,?) ON CONFLICT(cid) DO UPDATE SET departed=excluded.departed, updated_at=excluded.updated_at', (user_id, 1, now_saudi()))
        with db() as conn:
            conn.execute('INSERT OR REPLACE INTO broadcast_stats(id,sent,failed,created_at) VALUES (1,?,?,?)', (ok, failed, now_saudi()))
        BROADCAST_PENDING.discard(cid)
        send(api, cid, f'✅ <b>تم الإرسال</b>\n\nوصلت الرسالة إلى: <b>{ok}</b>\nتعذر الإرسال إلى: <b>{failed}</b>',
             kb([[btn('↩️ لوحة الإدارة', 'admin')]]))
        return True
    with db() as conn:
        transfer_state = conn.execute('SELECT step,recipient,amount_sar FROM wallet_transfer_state WHERE cid=?', (cid,)).fetchone()
    if transfer_state:
        raw = (message.get('text') or '').strip()
        if raw.startswith('/'):
            with db() as conn:
                conn.execute('DELETE FROM wallet_transfer_state WHERE cid=?', (cid,))
            return False
        step, recipient, amount_sar = transfer_state
        if step == 'recipient':
            try:
                target = int(raw)
            except Exception:
                send(api, cid, tr(cid, 'أرسل رقم ID صحيح فقط.', 'Send a valid numeric user ID only.'))
                return True
            if target == cid:
                send(api, cid, tr(cid, 'لا يمكنك التحويل لنفس حسابك.', 'You cannot transfer to your own account.'))
                return True
            try:
                known_users = {int(x) for x in json.loads(USERS_PATH.read_text(encoding='utf-8'))}
            except Exception:
                known_users = set()
            if target not in known_users:
                send(api, cid, tr(cid, 'هذا المستخدم غير موجود داخل البوت.', 'This user is not registered in the bot.'))
                return True
            with db() as conn:
                conn.execute('UPDATE wallet_transfer_state SET step=?,recipient=? WHERE cid=?', ('amount', target, cid))
            send(api, cid, tr(cid, 'أرسل مبلغ التحويل بالدولار USD، مثال: 5', 'Send the transfer amount in USD, e.g. 5'))
            return True
        if step == 'amount':
            try:
                usd_value = Decimal(raw.replace(',', '.')).quantize(Decimal('0.01'))
            except Exception:
                send(api, cid, tr(cid, 'أرسل المبلغ كرقم فقط.', 'Send the amount as a number only.'))
                return True
            if usd_value <= 0:
                send(api, cid, tr(cid, 'المبلغ يجب أن يكون أكبر من صفر.', 'Amount must be greater than zero.'))
                return True
            sar_value = (usd_value * RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            if wallet_balance(cid) < sar_value:
                send(api, cid, tr(cid, 'رصيدك غير كافٍ.', 'Your balance is insufficient.'))
                return True
            with db() as conn:
                conn.execute('UPDATE wallet_transfer_state SET amount_sar=? WHERE cid=?', (str(sar_value), cid))
            send(api, cid,
                 tr(cid, 'تأكيد التحويل إلى المستخدم:', 'Confirm transfer to user:') + f' <code>{recipient}</code>\n<b>{usd_value:.2f} USD / {sar_value:.2f} SAR</b>',
                 kb([[btn(tr(cid, 'تأكيد التحويل', 'Confirm transfer'), f'wallettransferconfirm:{recipient}:{sar_value}', style='success')],
                     [btn(tr(cid, 'إلغاء', 'Cancel'), 'wallet', style='primary')]]))
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
    result = send(api, G['ADMIN_ID'], '🧾 <b>إثبات دفع جديد</b>\n\n' + esc(name(pid)) + f'\nالكمية: {product_options.snapshot(sys.modules[__name__], "receipt", cid)}\nالسعر عند الطلب: {sar} SAR / {usd} USD\nالطريقة: {esc(method)}\nالعميل: {esc(username)}\nID: <code>{cid}</code>')
    forwarded = api.call('forwardMessage', chat_id=G['ADMIN_ID'], from_chat_id=cid, message_id=message['message_id']) if result else None
    if not forwarded:
        send(api, cid, tr(cid, 'تعذر إرسال الإثبات للإدارة. أعد المحاولة أو تواصل مع ', 'Could not forward the receipt. Retry or contact ') + SUPPORT)
        return True
    add_order(cid, pid, method, 'review', usd=usd, sar=sar)
    with db() as conn:
        conn.execute('DELETE FROM payment_quotes WHERE cid=? AND pid=? AND method=?', (cid, pid, method))
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
    if payment_methods.action(sys.modules[__name__], api, cid, value):
        return
    if discounts.action(sys.modules[__name__], api, cid, value):
        return
    prefix, _, arg = value.partition(':')
    arg = LEGACY.get(arg, arg)
    if prefix in ('home', 'enter_store'):
        reset_navigation_state(cid)
        home(api, cid)
    elif prefix == 'start':
        start(api, cid)
    elif prefix == 'products':
        products(api, cid)
    elif prefix == 'referrals':
        referral_page(api, cid)
    elif prefix == 'product':
        category(api, cid, arg)
    elif prefix in ('item', 'claude'):
        item(api, cid, arg)
    elif value in LEGACY:
        item(api, cid, LEGACY[value])
    elif prefix == 'catdesc':
        admin_category_description(api, cid, arg)
    elif prefix == 'catdesclang':
        lang, _, pid = arg.partition(':')
        admin_category_description(api, cid, pid, lang)
    elif prefix == 'admin':
        if arg == 'categorydesc': admin_category_description(api, cid)
        elif arg == 'orders': admin_orders(api, cid)
        elif arg == 'activity': admin_activity(api, cid)
        elif arg == 'stats': admin_stats(api, cid)
        elif arg == 'icons': admin_icons(api, cid)
        elif arg == 'buttonlabels': admin_button_labels(api, cid)
        elif arg == 'prices': admin_prices(api, cid)
        elif arg == 'photos': admin_photo_menu(api, cid)
        elif arg == 'editname': admin_text_menu(api, cid, 'name')
        elif arg == 'editdesc': admin_text_menu(api, cid, 'description')
        elif arg == 'stock': admin_stock(api, cid)
        elif arg == 'supplierapi': supplier_api_menu(api, cid)
        elif arg == 'info': admin_info_menu(api, cid)
        elif arg == 'addproduct': begin_add_product(api, cid)
        elif arg == 'myproducts': admin_products_page(api, cid)
        elif arg == 'cancelproduct' and cid == G['ADMIN_ID']:
            with db() as conn: conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
            admin_panel(api, cid)
        elif arg == 'broadcast' and cid == G['ADMIN_ID']:
            BROADCAST_PENDING.add(cid)
            send(api, cid, '📢 <b>إرسال رسالة للجميع</b>\n\nأرسل الآن الرسالة التي تريد إرسالها لجميع مستخدمي البوت.\nيمكنك إرسال نص أو صورة مع تعليق.',
                 kb([[btn('❌ إلغاء', 'admin:broadcast_cancel')]]))
        elif arg == 'broadcast_cancel' and cid == G['ADMIN_ID']:
            BROADCAST_PENDING.discard(cid)
            admin_panel(api, cid)
        else: admin_panel(api, cid)
    elif prefix == 'payreview' and cid == G['ADMIN_ID']:
        decision, _, oid = arg.partition(':')
        with db() as conn:
            row = conn.execute('SELECT cid,status FROM orders WHERE id=?', (oid,)).fetchone()
            if not row or row[1] != 'review':
                return send(api, cid, '⚠️ الطلب غير موجود أو تمت معالجته مسبقاً.')
            customer = row[0]
            if decision == 'accept':
                conn.execute('UPDATE orders SET status="paid" WHERE id=? AND status="review"', (oid,))
                if fulfill_paid_order(api, oid):
                    return send(api, cid, f'✅ تم قبول الطلب <b>#{esc(oid)}</b> وبدأ التنفيذ التلقائي عبر المورد.')
                G['PENDING_ADMIN_DELIVERY'][G['ADMIN_ID']]={'customer':customer,'order_id':oid}
                send(api, customer, '✅ <b>تم قبول الدفع.</b>\n\nسيتم إرسال طلبك لك قريباً.')
                return send(api, cid, f'✅ تم قبول الطلب <b>#{esc(oid)}</b>.\n\n📤 أرسل الآن أي رسالة أو صورة أو ملف تريد إرساله للعميل.\nسيتم إرسال <b>الرسالة التالية فقط</b> له مباشرة.')
            conn.execute('UPDATE orders SET status="rejected" WHERE id=? AND status="review"', (oid,))
        send(api, customer, '❌ <b>تم رفض إثبات الدفع.</b>\n\nيرجى إعادة المحاولة أو التواصل مع الدعم.')
        send(api, cid, f'❌ تم رفض الطلب <b>#{esc(oid)}</b> وإبلاغ العميل.')
    elif prefix == 'infocat':
        admin_info_menu(api,cid,arg)
    elif prefix == 'infopick':
        admin_info_editor(api,cid,arg)
    elif prefix == 'infotoggle' and cid == G['ADMIN_ID']:
        field,_,pid=arg.partition(':'); sp,ss,sw,w=info_display(pid)
        if field=='price': sp=0 if sp else 1
        elif field=='stock': ss=0 if ss else 1
        elif field=='warranty': sw=0 if sw else 1
        with db() as conn: conn.execute('INSERT OR REPLACE INTO product_info_display VALUES (?,?,?,?,?)',(pid,sp,ss,sw,w))
        admin_info_editor(api,cid,pid)
    elif prefix == 'infowarranty' and cid == G['ADMIN_ID']:
        with db() as conn: conn.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)',(cid,'info_warranty',arg))
        send(api,cid,'✏️ أرسل نص الضمان لهذا المنتج، مثال: <b>15 يوم</b>.',kb([[btn('إلغاء','admin:info')]]))
    elif prefix == 'mycategory':
        admin_category_detail(api, cid, arg)
    elif prefix == 'myproduct':
        admin_product_detail(api, cid, arg)
    elif prefix == 'myproducttoggle':
        toggle_admin_product(api, cid, arg)
    elif prefix == 'myproductdelete':
        delete_admin_product(api, cid, arg)
    elif prefix == 'stockcat':
        admin_stock(api, cid, arg)
    elif prefix == 'stockpick':
        stock_editor(api, cid, arg)
    elif prefix == 'stockset':
        value, _, pid = arg.partition(':')
        if value in ('0', '1'):
            stock_editor(api, cid, pid, value)
    elif prefix == 'stockqty' and cid == G['ADMIN_ID']:
        pid = LEGACY.get(arg, arg)
        if pid in VARIANTS or pid in G['PRODUCTS'] or custom_product(pid):
            with db() as conn:
                conn.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)', (cid, 'stock_quantity', pid))
            send(api, cid, '<b>' + esc(name(pid, cid)) + '</b>\n\n📦 الكمية الحالية: <b>' + esc(product_stock(pid)) + '</b>\n\nأرسل الكمية الجديدة كرقم، مثال: <code>4</code>.', kb([[btn('❌ إلغاء', 'stockpick:' + pid)]]))
    elif prefix == 'pandorabrowse' and cid == G['ADMIN_ID']:
        pandora_catalog_open(api, cid, arg, 0)
    elif prefix == 'pandorapage' and cid == G['ADMIN_ID']:
        pandora_catalog_categories(api, cid)
    elif prefix == 'pandoracategories' and cid == G['ADMIN_ID']:
        pandora_catalog_categories(api, cid)
    elif prefix == 'pandoracat' and cid == G['ADMIN_ID']:
        cidx, _, page = arg.partition(':')
        pandora_catalog_page(api, cid, cidx or 0, page or 0)
    elif prefix == 'pandorap' and cid == G['ADMIN_ID']:
        pandora_catalog_product(api, cid, arg)
    elif prefix == 'pandorav' and cid == G['ADMIN_ID']:
        pidx, _, vidx = arg.partition(':')
        pandora_catalog_save(api, cid, pidx, vidx)
    elif prefix == 'suppliercat':
        supplier_api_menu(api, cid, arg)
    elif prefix == 'supplierpick':
        supplier_api_editor(api, cid, arg)
    elif prefix == 'supplierpandora' and cid == G['ADMIN_ID']:
        pid = LEGACY.get(arg, arg)
        endpoint, api_key, service_id, enabled, provider, variant_id = supplier_api_row(pid)
        endpoint = 'https://api.pandoradigital.shop/api/v1'
        provider = 'pandora'
        with db() as conn:
            conn.execute('INSERT INTO supplier_api(pid,endpoint,api_key,service_id,enabled,provider,variant_id) VALUES (?,?,?,?,?,?,?) ON CONFLICT(pid) DO UPDATE SET endpoint=excluded.endpoint,provider=excluded.provider',
                         (pid, endpoint, api_key, service_id, enabled, provider, variant_id))
        send(api, cid, '✅ تم اختيار <b>Pandora Digital</b> لهذا المنتج.\n\nالرابط والمفتاح يُستخدمان من Railway تلقائيًا. أضف الآن Product ID و Variant ID فقط.')
        supplier_api_editor(api, cid, pid)
    elif prefix == 'supplierset' and cid == G['ADMIN_ID']:
        field, _, pid = arg.partition(':')
        if field in ('endpoint', 'key', 'service', 'variant'):
            action_name = {'endpoint':'supplier_endpoint','key':'supplier_key','service':'supplier_service','variant':'supplier_variant'}[field]
            with db() as conn:
                conn.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)', (cid, action_name, pid))
            prompt = {'endpoint':'أرسل رابط API الكامل، مثال: <code>https://example.com/api/order</code>',
                      'key':'أرسل مفتاح API. لن يظهر كاملًا بعد الحفظ.',
                      'service':'أرسل Product ID لدى المورد.','variant':'أرسل Variant ID لدى المورد.'}[field]
            send(api, cid, '🔌 <b>' + esc(name(pid, cid)) + '</b>\n\n' + prompt, kb([[btn('❌ إلغاء', 'supplierpick:' + pid)]]))
    elif prefix == 'supplierprice' and cid == G['ADMIN_ID']:
        pid = LEGACY.get(arg, arg)
        try:
            row = pandora_refresh_price(pid)
            if row:
                cost, margin, sale = row
                send(api, cid, f'✅ تكلفة Pandora: <b>${cost:.2f}</b>\nهامش الربح: <b>${margin:.2f}</b>\nسعر البيع: <b>${sale:.2f}</b>')
            else:
                send(api, cid, '⚠️ تعذر جلب تكلفة Pandora لهذا المنتج.')
        except Exception as exc:
            send(api, cid, '❌ تعذر تحديث السعر: <code>' + esc(type(exc).__name__) + '</code>')
        supplier_api_editor(api, cid, pid)
    elif prefix == 'suppliermargin' and cid == G['ADMIN_ID']:
        pid = LEGACY.get(arg, arg)
        with db() as conn:
            conn.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)', (cid, 'supplier_margin', pid))
        send(api, cid, '➕ أرسل هامش الربح بالدولار، مثال: <code>2.00</code>.', kb([[btn('❌ إلغاء', 'supplierpick:' + pid)]]))
    elif prefix == 'suppliertest' and cid == G['ADMIN_ID']:
        supplier_test_connection(api, cid, arg)
    elif prefix == 'suppliertoggle' and cid == G['ADMIN_ID']:
        pid = LEGACY.get(arg, arg)
        endpoint, api_key, service_id, enabled, provider, variant_id = supplier_api_row(pid)
        missing = []
        if not endpoint: missing.append('رابط API')
        if not api_key: missing.append('مفتاح API')
        if provider == 'pandora' and not service_id: missing.append('Product ID')
        if provider == 'pandora' and not variant_id: missing.append('Variant ID')
        if missing:
            send(api, cid, '⚠️ تم حفظ الموجود، لكن باقي قبل التفعيل: <b>' + esc(' + '.join(missing)) + '</b>.\n\nإذا هدفك فقط تجربة المفتاح الآن اضغط 🧪 اختبار الاتصال.')
            supplier_api_editor(api, cid, pid)
        else:
            with db() as conn:
                conn.execute('INSERT INTO supplier_api(pid,endpoint,api_key,service_id,enabled,provider,variant_id) VALUES (?,?,?,?,?,?,?) ON CONFLICT(pid) DO UPDATE SET enabled=excluded.enabled',
                             (pid, endpoint, api_key, service_id, 0 if enabled else 1, provider, variant_id))
            supplier_api_editor(api, cid, pid)
    elif prefix == 'supplierdelete' and cid == G['ADMIN_ID']:
        pid = LEGACY.get(arg, arg)
        with db() as conn:
            conn.execute('DELETE FROM supplier_api WHERE pid=?', (pid,))
        send(api, cid, '✅ تم حذف ربط API لهذا المنتج.')
        supplier_api_editor(api, cid, pid)
    elif prefix == 'txtcat':
        field, _, category_id = arg.partition(':')
        admin_text_menu(api, cid, field, category_id)
    elif prefix == 'txtpick':
        field, _, pid = arg.partition(':')
        admin_text_editor(api, cid, field, pid)
    elif prefix == 'txtedit':
        parts = arg.split(':', 2)
        if len(parts) == 3:
            field, lang, pid = parts
            admin_text_editor(api, cid, field, pid, lang)
    elif prefix == 'photocat':
        admin_photo_menu(api, cid, arg)
    elif prefix == 'photopick':
        admin_photo_editor(api, cid, arg)
    elif prefix == 'photodel':
        admin_photo_editor(api, cid, arg, delete=True)
    elif prefix == 'pricecat':
        admin_prices(api, cid, arg)
    elif prefix == 'pricepick':
        price_editor(api, cid, arg)
    elif prefix == 'priceedit':
        currency, _, pid = arg.partition(':')
        price_editor(api, cid, pid, currency)
    elif prefix == 'buttonlabel':
        begin_button_label(api, cid, arg)
    elif prefix == 'buttonnames' and arg == 'categories':
        button_names_categories(api, cid)
    elif prefix == 'resetbuttonlabel':
        reset_button_label(api, cid, arg)
    elif prefix == 'removebuttonicon':
        remove_button_icon(api, cid, arg)
    elif prefix == 'removenameicon':
        remove_name_icon(api, cid, arg)
    elif prefix == 'cancelbuttonlabel':
        if cid == G['ADMIN_ID']:
            with db() as conn: conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
            admin_button_labels(api, cid)
    elif prefix == 'iconmenu':
        if arg == 'products': admin_icon_products(api, cid)
        elif arg == 'categories': admin_icon_categories(api, cid)
        elif arg == 'buttons': admin_icon_buttons(api, cid)
        else: admin_icons(api, cid)
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
            stored_value = arg if prefix == 'setlang' else 'USD'
            conn.execute(f'UPDATE preferences SET {column}=? WHERE cid=?', (stored_value, cid))
        home(api, cid)
    elif prefix in ('buy', 'cancel'):
        with db() as conn:
            conn.execute('DELETE FROM receipts WHERE cid=?', (cid,))
        if prefix == 'buy': payments(api, cid, arg)
        elif arg in VARIANTS: item(api, cid, arg)
        else: category(api, cid, arg)
    elif prefix == 'wallet':
        if arg == 'topup': wallet_amounts(api, cid)
        elif arg == 'transfer': wallet_transfer_begin(api, cid)
        else: wallet(api, cid)
    elif prefix == 'wallettransferconfirm':
        recipient, _, amount_sar = arg.partition(':')
        wallet_transfer_confirm(api, cid, recipient, amount_sar)
    elif prefix == 'topupmanual':
        method_id, _, value = arg.partition(':')
        wallet_manual_topup(api, cid, method_id, value)
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
    elif prefix == 'community':
        send(api, cid, tr(cid, '📢 <b>مجتمع VEXA STORE</b>\n\n🟢 المنتجات والاشتراكات المتوفرة\n🎁 أكواد الخصم والعروض\n🆕 المنتجات الجديدة\n🔔 تنبيهات التوفر\n\n🚀 انضم إلى مجموعتنا الرسمية!', '📢 <b>VEXA STORE Community</b>\n\n🟢 Available products and subscriptions\n🎁 Discount codes and offers\n🆕 New products\n🔔 Restock alerts\n\n🚀 Join our official group!'), kb([[{'text': tr(cid, '👥 الانضمام للمجموعة', '👥 Join the group'), 'url': 'https://t.me/SAU2030_k'}], [btn(tr(cid, '↩️ الرئيسية', '↩️ Home'), 'home')]]))
    elif prefix == 'support':
        send(api, cid, 'Support:' + SUPPORT, menu(cid))
    elif prefix == 'api':
        send(api, cid, tr(cid, '🔗 إعدادات API المتجر غير مفعّلة حاليًا.', '🔗 Store API settings are not active yet.'), menu(cid))
    elif prefix == 'warranty':
        send(api, cid, tr(cid, '🛡 تختلف شروط الضمان حسب المنتج. راجع وصفه قبل الطلب. للاستفسار: ', '🛡 Warranty terms vary by product. Read its description before ordering. Questions: ') + SUPPORT, menu(cid))
    else:
        products(api, cid)


def install(namespace):
    global G
    G = namespace
    try:
        __import__('threading').Thread(target=pandora_startup_probe, daemon=True).start()
    except Exception:
        pass
    apply_icon_overrides()
    namespace.update({'show_start': start, 'show_home': home, 'show_products': products,
                      'show_product': category, 'show_claude_product': item, 'handle_action': action, 'action': action,
                      'handle_receipt': receipt, 'order_name': name, 'home_keyboard': menu,
                      'broadcast_new_products': broadcast_new_products,
                      'chatgpt_visibility_admin': chatgpt_visibility_admin,
                      'toggle_chatgpt_visibility': toggle_chatgpt_visibility,
                      'handle_admin_product': handle_admin_product, 'handle_info_warranty': handle_info_warranty, 'handle_info_icon': handle_info_icon})
    menu_actions = namespace.setdefault('MENU_ACTIONS', namespace.get('MENU', {}))
    menu_actions.update({'🚀 ابدأ': 'start', '🚀 Start': 'start', '🛍 المنتجات': 'products',
                         '🛍 Products': 'products', '⚡ VEXA VOLT': 'support', '⚡ VEXA VOLT': 'support',
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
            description = product_description(pid, cid)
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


def custom_category(category_id):
    with db() as conn:
        return conn.execute('SELECT cid,name FROM admin_categories WHERE cid=?', (category_id,)).fetchone()


def custom_product(pid):
    with db() as conn:
        return conn.execute('SELECT pid,name,description,price_usd,available,category_id,stock FROM admin_products WHERE pid=?', (pid,)).fetchone()


def name(pid, cid=0):
    pid = LEGACY.get(pid, pid)
    if pid == 'youtube':
        return text_override(pid, 'name', prefs(cid)[0], 'YouTube')
    if pid in VARIANTS:
        return text_override(pid, 'name', prefs(cid)[0], VARIANTS[pid]['name'][prefs(cid)[0]])
    category = custom_category(pid)
    if category:
        return text_override(pid, 'name', prefs(cid)[0], category[1])
    cp = custom_product(pid)
    if cp:
        return text_override(pid, 'name', prefs(cid)[0], cp[1])
    return text_override(pid, 'name', prefs(cid)[0], G['PRODUCTS'].get(pid, {}).get('name', pid))


def start(api, cid):
    reset_navigation_state(cid)
    send(api, cid, tr(cid, '👋 <b>مرحباً بك في VEXA STORE</b>\n\nمتجر الخدمات والاشتراكات الرقمية.',
                     '👋 <b>Welcome to VEXA STORE</b>\n\nDigital services and subscriptions.'),
         kb([[btn('🚀 START | ابدأ', 'enter_store', ui_icon('ui_start'), style='primary')], [btn('🌐 العربية / English', 'settings:lang', ui_icon('ui_language'), style='primary')]]))


def home(api, cid):
    balance_sar = wallet_balance(cid)
    balance_usd = (balance_sar / RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    with db() as conn:
        purchases = conn.execute('SELECT COUNT(*) FROM orders WHERE cid=? AND status="paid"', (cid,)).fetchone()[0]
    text = tr(cid, f'👋 <b>أهلاً بك في VEXA STORE!</b>\n\n🆔 رقم العضوية: <code>{cid}</code>\n👤 حسابك: <a href="tg://user?id={cid}">فتح الحساب</a>\n💳 الرصيد: <b>${balance_usd:.2f}</b>\n🛍 المشتريات: <b>{purchases}</b>\n\nاختر من القائمة أدناه:', f'👋 <b>Welcome to VEXA STORE!</b>\n\n🆔 Member ID: <code>{cid}</code>\n👤 Account: <a href="tg://user?id={cid}">Open profile</a>\n💳 Balance: <b>${balance_usd:.2f}</b>\n🛍 Purchases: <b>{purchases}</b>\n\nChoose from the menu below:')
    rows = [[btn(tr(cid,'المنتجات','Products'),'products',ui_icon('ui_products'),style='primary'), btn(tr(cid,'شحن الرصيد','Top up'),'wallet:topup',ui_icon('ui_topup'),style='primary')], [btn(tr(cid,'الإحالات','Referrals'),'referrals',ui_icon('ui_referrals'),style='primary'), btn(tr(cid,'حسابي','My account'),'wallet',ui_icon('ui_account'),style='primary')], [btn(tr(cid,'تواصل مع الدعم','Contact support'),'support',ui_icon('ui_support'),style='primary'), btn(tr(cid,'إبلاغ عن مشكلة','Report issue'),'support',ui_icon('ui_report'),style='primary')], [btn(tr(cid,'العملة','Currency'),'settings:currency',ui_icon('ui_currency'),style='primary'), btn('Language / اللغة','settings:lang',ui_icon('ui_language'),style='primary')]]
    rows.append([btn(tr(cid, '📢 مجتمع VEXA STORE', '📢 VEXA STORE Community'), 'community', ui_icon('ui_community'), style='primary')])
    if cid == G.get('ADMIN_ID'):
        rows.append([btn('لوحة الطلبات', 'admin', ui_icon('ui_admin'), style='primary')])
    send(api, cid, text, kb(rows))

def products(api, cid):
    buttons = [btn(category_label(pid, cid), 'product:' + pid, ui_icon(pid) or p.get('custom_emoji_id')) for pid, p in G['PRODUCTS'].items() if category_visible(pid)]
    with db() as conn:
        custom_categories = conn.execute('SELECT cid,name FROM admin_categories ORDER BY rowid').fetchall()
    buttons += [btn(name(category_id, cid), 'product:' + category_id, ui_icon(category_id)) for category_id, category_name in custom_categories if category_visible(category_id)]
    rows = [buttons[i:i+3] for i in range(0, len(buttons), 3)]
    rows += [[btn(tr(cid, '🏠 الرئيسية', '🏠 Home'), 'home')]]
    send(api, cid, tr(cid, '🛍 <b>المنتجات</b>\nاختر الخدمة:', '🛍 <b>Products</b>\nChoose a service:'), kb(rows))


def settings(api, cid, kind):
    if kind == 'lang':
        rows = [[btn('العربية', 'setlang:ar'), btn('English', 'setlang:en')]]
        text = '🌐 اختر اللغة / Choose language'
    else:
        rows = [[btn('🇺🇸 USD — US Dollar', 'setcurrency:USD')]]
        text = '💱 أسعار المنتجات ثابتة بالدولار USD\nيظهر التحويل للريال السعودي عند الدفع فقط.'
    send(api, cid, text, kb(rows + [nav(cid)]))


def card(api, cid, image_path, title, text, keyboard, pid=None):
    """Separate photo and full text so Telegram's caption limit never drops terms."""
    override = saved_product_photo(pid) if pid else None
    if override is not None:
        image_path = None
        if override:
            api.call('sendPhoto', chat_id=cid, photo=override, caption=title[:900])
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
    # The product fields are escaped at their source; info_block supplies
    # the only HTML markup, including the custom emoji entity.
    send(api, cid, text, keyboard)



def grok_cards(api, cid, choices, show_heading=True, default_image="assets/grok.png"):
    """Compact photo cards for Grok only; prices share the checkout source."""
    if show_heading:
        send(api, cid, tr(cid, '✦ <b>اشتراكات Grok</b>\nاختر الباقة المناسبة لك:', '✦ <b>Grok subscriptions</b>\nChoose your plan:'))
    for v in choices:
        pid = v['id']
        available = can_order(pid)
        if v.get('review_required'):
            status = tr(cid, '⏸ قيد المراجعة — الطلب غير متاح', '⏸ Under review — ordering unavailable')
        elif not in_stock(pid):
            status = tr(cid, '🔴 نفدت الكمية', '🔴 Out of stock')
        else:
            status = tr(cid, '🟢 متوفر', '🟢 Available')
        caption = '<b>' + esc(name(pid, cid)) + '</b>\n\n'
        caption += '💰 <b>' + price(cid, pid, 'USD') + '</b>\n\n' + status
        if v.get('manual_delivery'):
            caption += '\n' + tr(cid, '✉️ يتم إرسال بيانات المنتج بعد تأكيد الدفع', '✉️ Product details are sent after payment confirmation')
        details = btn(tr(cid, '📋 التفاصيل', '📋 Details'), 'item:' + pid)
        rows = [[btn(tr(cid, '🛒 شراء الآن', '🛒 Buy now'), 'buy:' + pid, style='success'), details]] if available else [[btn(tr(cid, '🔴 غير متوفر', '🔴 Unavailable'), 'item:' + pid, style='danger')]]
        if not available:
            rows[0][0]['style'] = 'danger'
        rows.append([btn(tr(cid, '⚡ VEXA VOLT', '⚡ VEXA VOLT'), 'support')])
        markup = kb(rows)
        override = saved_product_photo(pid)
        if override is not None:
            if not override or not api.call('sendPhoto', chat_id=cid, photo=override, caption=caption, parse_mode='HTML', reply_markup=markup):
                send(api, cid, caption, markup)
            continue
        path = (BASE / (v.get('image') or default_image)).resolve()
        delivered = False
        if path.is_relative_to(BASE) and path.is_file():
            boundary = 'VEXA' + uuid.uuid4().hex
            fields = {'chat_id': str(cid), 'caption': caption, 'parse_mode': 'HTML',
                      'reply_markup': json.dumps(markup, ensure_ascii=False)}
            body = b''
            for key, value in fields.items():
                body += f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
            body += f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="{path.name}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
            body += path.read_bytes() + f'\r\n--{boundary}--\r\n'.encode()
            try:
                api_url = getattr(api, 'base_url', None) or getattr(api, 'u', None)
                req = urllib.request.Request(api_url + 'sendPhoto', body, {'Content-Type': f'multipart/form-data; boundary={boundary}'})
                with urllib.request.urlopen(req, timeout=40) as response:
                    delivered = bool(json.load(response).get('ok'))
            except Exception as exc:
                print('Grok card image failed:', type(exc).__name__)
        if not delivered:
            send(api, cid, caption, markup)
    send(api, cid, tr(cid, 'تصفح أقسام المتجر:', 'Browse store categories:'),
         kb([[btn(tr(cid, '↩️ الأقسام', '↩️ Categories'), 'products'),
              btn(tr(cid, '🏠 الرئيسية', '🏠 Home'), 'home')]]))


def category(api, cid, pid):
    if not category_visible(pid):
        return products(api, cid)
    p = G['PRODUCTS'].get(pid)
    if not p:
        custom_cat = custom_category(pid)
        if not custom_cat:
            products(api, cid)
            return
        with db() as conn:
            choices = conn.execute('SELECT pid,name,price_usd,available,stock FROM admin_products WHERE category_id=? ORDER BY rowid', (pid,)).fetchall()
        rows = []
        for product_id, product_name, price_usd, available, stock in choices:
            if not product_visible(product_id):
                continue
            sold_out = not available or int(stock or 0) <= 0
            qty = int(stock or 0)
            label = compact_name(product_id, cid) + ' | 💵 ' + price(cid, product_id, 'USD') + ' | ' + compact_stock(qty)
            if sold_out:
                label = '🔴 ' + label
            rows.append([btn(label, 'item:' + product_id, product_button_icon(product_id, cid), style='danger' if sold_out else 'success')])
        send(api, cid, category_heading(pid, cid), kb(rows + [nav(cid)]))
        return
    if pid == 'youtube':
        with db() as conn:
            yt_rows = conn.execute('''SELECT DISTINCT p.pid,p.name,p.price_usd,p.available,p.stock
                FROM admin_products p
                LEFT JOIN admin_categories c ON c.cid=p.category_id
                WHERE p.category_id='youtube'
                   OR lower(p.name) LIKE '%youtube%'
                   OR p.name LIKE '%يوتيوب%'
                   OR lower(COALESCE(c.name,'')) LIKE '%youtube%'
                   OR COALESCE(c.name,'') LIKE '%يوتيوب%'
                ORDER BY p.rowid''').fetchall()
        if yt_rows:
            rows = []
            for product_id, product_name, price_usd, available, stock in yt_rows:
                if not product_visible(product_id):
                    continue
                qty = int(stock or 0)
                sold_out = (not bool(available)) or qty <= 0
                label = ('🔴 ' if sold_out else '🟢 ') + compact_name(product_id, cid) + ' | 💵 ' + price(cid, product_id, 'USD') + ' | ' + compact_stock(qty)
                rows.append([btn(label, 'item:' + product_id, product_button_icon(product_id, cid),
                                 style='danger' if sold_out else 'success')])
            if rows:
                send(api, cid, category_heading(pid, cid), kb(rows + [nav(cid)]))
                return
    choices = [v for v in VARIANTS.values() if v['category'] == pid and product_visible(v['id'])]
    if choices:
        rows = []
        for v in choices:
            sold_out = not in_stock(v['id'])
            status = '⏸ ' if v.get('review_required') else ('🔴 ' if sold_out else '🟢 ')
            qty = product_stock(v['id'])
            rows.append([btn(status + compact_name(v['id'], cid) + ' | 💵 ' + price(cid, v['id'], 'USD') + ' | ' + compact_stock(qty),
                             'item:' + v['id'], p.get('custom_emoji_id') if product_button_icon(v['id'], cid) is None else product_button_icon(v['id'], cid), style='danger' if sold_out else 'success')])
        send(api, cid, category_heading(pid, cid), kb(rows + [nav(cid)]))
        return
    english = {'youtube': 'YouTube Premium for one month. Ad-free viewing, background playback, offline downloads and YouTube Music Premium benefits.',
               'netflix': 'Netflix subscription for movies, series and entertainment.', 'iptv': 'IPTV subscriptions for compatible devices.'}
    description = product_description(pid, cid)
    text = esc(name(pid, cid)) + '\n\n' + esc(price(cid, pid)) + '\n\n' + esc(tr(cid, '✅ متوفر' if in_stock(pid) else '🔴 نفدت الكمية', '✅ Available' if in_stock(pid) else '🔴 Out of stock')) + '\n\n' + esc(description)
    rows = [[btn(tr(cid, '🛒 طلب المنتج', '🛒 Order'), 'buy:' + pid)]] if can_order(pid) else []
    rows += [[btn(tr(cid, '⚡ VEXA VOLT', '⚡ VEXA VOLT'), 'support')], nav(cid)]
    card(api, cid, f'assets/{pid}.png', name(pid, cid), text, kb(rows), pid=pid)


def item(api, cid, pid):
    pid = LEGACY.get(pid, pid)
    if not product_visible(pid):
        return send(api, cid, tr(cid, 'هذا المنتج مخفي حاليًا.', 'This product is currently hidden.'), kb([nav(cid, 'products')]))
    v = VARIANTS.get(pid)
    if not v:
        cp = custom_product(pid)
        if not cp:
            products(api, cid)
            return
        _, product_name, description, price_usd, available, category_id, stock = cp
        status = tr(cid, '✅ متوفر', '✅ Available') if available and int(stock or 0) > 0 else tr(cid, '🔴 نفدت الكمية', '🔴 Out of stock')
        text = esc(name(pid, cid)) + '\n\n' + info_block(pid,cid) + '\n\n' + esc(status) + '\n\n' + esc(product_description(pid, cid))
        rows = [[btn(tr(cid, '🛒 طلب المنتج', '🛒 Order'), 'buy:' + pid)]] if can_order(pid) else []
        rows += [[btn(tr(cid, '⚡ VEXA VOLT', '⚡ VEXA VOLT'), 'support')], nav(cid, 'product:' + category_id)]
        card(api, cid, None, name(pid, cid), text, kb(rows), pid=pid)
        return
    lang = prefs(cid)[0]
    available = ''
    if not in_stock(pid):
        available = tr(cid, '🚫 نفد لدى المورد وقت المراجعة. الطلب غير متاح حاليًا.', '🚫 Out of stock at the last supplier check. Ordering is currently unavailable.')
    text = esc(name(pid, cid)) + '\n\n' + info_block(pid,cid) + (('\n\n' + esc(available)) if available else '') + '\n\n' + esc(product_description(pid, cid))
    if v.get('promotions'):
        text += '\n\n' + esc(tr(cid, 'أسعار الكميات — تواصل مع الدعم:', 'Bulk prices — contact support:'))
        for tier in v['promotions']:
            text += '\n' + esc(tier['min_quantity']) + '+: ' + esc(price(cid, pid, source_price=tier['source_usd'])) + esc(tr(cid, ' لكل قطعة', ' per unit'))
    if v.get('review_required'):
        text += '\n\n⚠️ ' + esc(v['review_required'][lang])
    rows = []
    if can_order(pid):
        rows.append([btn(tr(cid, '🛒 طلب قطعة واحدة', '🛒 Order one item'), 'buy:' + pid)])
    rows += [[btn(tr(cid, '⚡ VEXA VOLT', '⚡ VEXA VOLT'), 'support')], nav(cid, 'product:' + v['category'])]
    card(api, cid, v.get('image'), name(pid, cid), text, kb(rows), pid=pid)


def can_order(pid):
    pid = LEGACY.get(pid, pid)
    if not product_visible(pid):
        return False
    if pid in VARIANTS:
        return in_stock(pid) and not VARIANTS[pid].get('review_required')
    if custom_product(pid):
        return in_stock(pid) and amount(pid) is not None
    return pid in G['PRODUCTS'] and in_stock(pid) and amount(pid) is not None


def back(pid):
    pid = LEGACY.get(pid, pid)
    return ('item:' if pid in VARIANTS or custom_product(pid) else 'product:') + pid


def checkout_totals(cid, pid):
    return discounts.totals(sys.modules[__name__], cid, pid)


def summary(cid, pid):
    qty = product_options.selected(sys.modules[__name__], cid, pid)
    sar, usd, discount, code = checkout_totals(cid, pid)
    text = esc(name(pid,cid)) + '\n💵 سعر الوحدة: ' + price(cid,pid,'USD') + f'\n🛍 الكمية: {qty}'
    if code:
        discount_usd = (Decimal(str(discount)) / RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        text += '\n🎟 ' + esc(code) + f' — الخصم: {discount_usd:.2f} USD / {discount:.2f} SAR'
    text += f'\n\n💲 <b>الإجمالي بالدولار:</b> {usd:.2f} USD'
    text += f'\n🇸🇦 <b>الإجمالي بالريال:</b> {sar:.2f} SAR'
    return text


def wallet(api, cid):
    sar = wallet_balance(cid).quantize(Decimal('0.01'))
    usd = (sar / RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    text = tr(cid, '<b>محفظة VEXA</b>\n\nرصيدك الحالي:', '<b>VEXA Wallet</b>\n\nYour current balance:')
    text += f'\n<b>{sar:.2f} {tr(cid, "ر.س", "SAR")}</b>\n<b>{usd:.2f} USD</b>'
    rows = [[btn(tr(cid, 'إضافة رصيد', 'Add funds'), 'wallet:topup', ui_icon('ui_wallet_add'), style='success'),
             btn(tr(cid, 'تحويل', 'Transfer'), 'wallet:transfer', ui_icon('ui_wallet_transfer'), style='primary')],
            [btn(tr(cid, 'الرجوع للقائمة', 'Back to Menu'), 'home', ui_icon('ui_wallet_back'), style='primary')]]
    send(api, cid, text, kb(rows))


def wallet_amounts(api, cid):
    rows = []
    for pair in ((20, 50), (100, 200)):
        row = []
        for value in pair:
            usd = (Decimal(value) / RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            row.append(btn(f'{value} {tr(cid, "ر.س", "SAR")} / {usd:.2f} USD', f'topup:{value}', style='success'))
        rows.append(row)
    text = tr(cid, 'اختر مبلغ إضافة الرصيد — جميع المبالغ معروضة بالريال والدولار:',
              'Choose an add-funds amount — all amounts are shown in SAR and USD:')
    rows.append([btn(tr(cid, 'مبلغ اختياري بالدولار', 'Custom amount in USD'), 'topupcustom', style='success')])
    rows.append([btn(tr(cid, 'رجوع', 'Back'), 'wallet', style='primary')])
    send(api, cid, text, kb(rows))


def wallet_method(api, cid, value):
    try:
        value = Decimal(value).quantize(Decimal('0.01'))
    except Exception:
        return wallet_amounts(api, cid)
    if value <= 0 or value > 5000:
        return wallet_amounts(api, cid)
    usd = (value / RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    text = tr(cid, 'اختر طريقة إضافة الرصيد:', 'Choose an add-funds method:') + f'\n\n<b>{value:.2f} SAR / {usd:.2f} USD</b>'
    rows = [
        [btn('Crypto Pay', f'topupcrypto:{value}', ui_icon('pay_cryptopay'), style='success')],
        [btn('Bybit / USDT', f'topupbybit:{value}', ui_icon('pay_bybit'), style='success')]
    ]
    for method in payment_methods.methods(sys.modules[__name__], True):
        rows.append([btn(method[1], f'topupmanual:{method[0]}:{value}', ui_icon('pay_custom_' + str(method[0])), style='success')])
    rows.append([btn(tr(cid, 'رجوع', 'Back'), 'wallet:topup', style='primary')])
    send(api, cid, text, kb(rows))


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
         kb([[btn('Bybit Pay', 'topupsend:bybitid:' + topup_id, ui_icon('pay_bybitid'), style='success')],
             [btn('USDT • TRON (TRC20)', 'topupsend:trc20:' + topup_id, ui_icon('pay_trc20'), style='success')],
             [btn('USDT • BSC (BEP20)', 'topupsend:bep20:' + topup_id, ui_icon('pay_bep20'), style='success')],
             [btn(tr(cid, 'رجوع', 'Back'), f'topup:{value}', style='primary')]]))


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
    warning = tr(cid, 'يتم تنفيذ الطلب بعد مراجعة الدفع وتأكيد التوفر، ثم إرسال بيانات المنتج إليك.', 'Your order is fulfilled after payment review and availability confirmation, then the product details are sent to you.')
    send(api, cid, tr(cid, '💳 <b>اختر طريقة الدفع</b>\n\n', '💳 <b>Choose payment method</b>\n\n') + summary(cid, pid) + '\n\n' + warning,
         kb([[btn(tr(cid, '🎟 كود خصم', '🎟 Discount code'), 'coupon:' + pid, style='primary'), btn(tr(cid, 'إزالة الخصم', 'Remove discount'), 'couponremove:' + pid)],
             [btn(tr(cid, 'المحفظة', 'Wallet'), 'paywallet:' + pid, ui_icon('pay_wallet'))],
             [btn('Crypto Pay', 'paycrypto:' + pid, ui_icon('pay_cryptopay'))],
             [btn('USDT — Bybit', 'paybybit:' + pid, ui_icon('pay_bybit'))]] + payment_methods.rows(sys.modules[__name__], pid) + [nav(cid, back(pid))]))


def payment(api, cid, pid, method):
    if not can_order(pid):
        payments(api, cid, pid)
        return
    if checkout_totals(cid, pid)[0] == 0:
        return pay_with_wallet(api, cid, pid)
    if method == 'bybit':
        send(api, cid, '🪙 <b>USDT — Bybit</b>\n\n' + summary(cid, pid),
             kb([[btn('Bybit Pay', 'bybitid:' + pid, ui_icon('pay_bybitid'))], [btn('USDT • TRON (TRC20)', 'trc20:' + pid, ui_icon('pay_trc20'))], [btn('USDT • BSC (BEP20)', 'bep20:' + pid, ui_icon('pay_bep20'))], nav(cid, 'buy:' + pid)]))
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
        sar, usd, _, _ = checkout_totals(cid, pid)
        with db() as conn:
            conn.execute('INSERT OR REPLACE INTO payment_quotes VALUES (?,?,?,?,?)', (cid, pid, method, str(usd), str(sar)))
        text += '\n\n' + tr(cid, 'بعد التحويل أرسل صورة الإثبات للمراجعة.', 'After transferring, submit a receipt photo for review.')
        rows.append([btn(tr(cid, '✅ تم التحويل', '✅ Payment sent'), f'receipt:{method}:{pid}')])
    else:
        text += '\n\n' + tr(cid, 'بيانات الدفع غير مكتملة. تواصل مع الدعم: ', 'Payment details are incomplete. Contact support: ') + SUPPORT
    send(api, cid, text, kb(rows + [nav(cid, 'buy:' + pid)]))


def pay_with_wallet(api, cid, pid):
    if not can_order(pid):
        return payments(api, cid, pid)
    cost, paid_usd, _, _ = checkout_totals(cid, pid)
    remaining = wallet_debit(cid, cost)
    if remaining is None:
        send(api, cid, tr(cid, 'رصيد المحفظة غير كافٍ.', 'Insufficient wallet balance.') +
             f'\n\n{tr(cid, "المطلوب", "Required")}: {cost:.2f} SAR\n{tr(cid, "الرصيد", "Balance")}: {wallet_balance(cid):.2f} SAR',
             kb([[btn(tr(cid, '➕ شحن المحفظة', '➕ Top up wallet'), 'wallet:topup')], nav(cid, 'buy:' + pid)]))
        return
    order_id = add_order(cid, pid, 'wallet', 'paid', usd=paid_usd, sar=cost)
    send(api, G['ADMIN_ID'], f'🛒 <b>طلب مدفوع من المحفظة #{order_id}</b>\n\n' + esc(name(pid, cid)) + f'\nالكمية: {product_options.snapshot(sys.modules[__name__], "order", order_id)}\nالسعر المدفوع: {paid_usd} USD\nالعميل: <code>{cid}</code>')
    if fulfill_paid_order(api, order_id):
        return
    send(api, cid, tr(cid, '✅ تم الدفع من المحفظة وإرسال الطلب للإدارة.', '✅ Paid from your wallet and the order was sent to administration.') + f'\n\n{tr(cid, "الرصيد المتبقي", "Remaining balance")}: {remaining:.2f} SAR', menu(cid))


def pay_with_crypto(api, cid, pid):
    if not can_order(pid):
        return payments(api, cid, pid)
    sar, usd, _, _ = checkout_totals(cid, pid)
    if usd == 0:
        return pay_with_wallet(api, cid, pid)
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
        row = conn.execute('SELECT pid,external_id,status,amount_usd FROM crypto_orders WHERE id=? AND cid=?', (order_id, cid)).fetchone()
    if not row:
        return products(api, cid)
    pid, invoice_id, status, paid_usd = row
    if status == 'paid':
        return send(api, cid, tr(cid, '✅ هذه الفاتورة مدفوعة وتم إرسال الطلب.', '✅ This invoice is paid and the order was sent.'), menu(cid))
    if not crypto_paid(invoice_id):
        return send(api, cid, tr(cid, 'لم يصل الدفع بعد. أكمل الفاتورة ثم أعد التحقق.', 'Payment has not arrived yet. Complete the invoice and check again.'),
                    kb([[btn(tr(cid, '🔄 تحقق مرة أخرى', '🔄 Check again'), 'checkorder:' + order_id)], nav(cid, 'buy:' + pid)]))
    with db() as conn:
        changed = conn.execute('UPDATE crypto_orders SET status="paid" WHERE id=? AND status="pending"', (order_id,)).rowcount
    if changed:
        saved_order_id = add_order(cid, pid, 'cryptopay', 'paid', usd=paid_usd, sar=(Decimal(paid_usd)*RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP), quantity=product_options.snapshot(sys.modules[__name__], 'crypto', order_id))
        send(api, G['ADMIN_ID'], f'💠 <b>طلب Crypto Pay مدفوع #{saved_order_id}</b>\n\n' + esc(name(pid, cid)) + f'\nالكمية: {product_options.snapshot(sys.modules[__name__], "order", saved_order_id)}\nالسعر المدفوع: {paid_usd} USD\nالعميل: <code>{cid}</code>')
        if fulfill_paid_order(api, saved_order_id):
            return
    send(api, cid, tr(cid, '✅ تم الدفع وإرسال الطلب للإدارة.', '✅ Payment received and the order was sent to administration.'), menu(cid))


def receipt_request(api, cid, pid, method):
    if not can_order(pid) or (method not in ('bank', 'bybitid', 'trc20', 'bep20') and not payment_methods.valid(sys.modules[__name__], method)):
        payments(api, cid, pid)
        return
    sar, usd, _, _ = checkout_totals(cid, pid)
    with db() as conn:
        quote = conn.execute('SELECT usd,sar FROM payment_quotes WHERE cid=? AND pid=? AND method=?', (cid, pid, method)).fetchone()
        if quote:
            usd, sar = quote
        conn.execute('INSERT OR REPLACE INTO receipts VALUES (?,?,?,?,?)', (cid, pid, method, str(usd), str(sar)))
    send(api, cid, tr(cid, '📸 أرسل صورة إثبات الدفع هنا. ستصل للإدارة للمراجعة.', '📸 Send your payment receipt photo here. It will be sent to the administrator for review.'), kb([[btn(tr(cid, '❌ إلغاء', '❌ Cancel'), 'cancel:' + pid)]]))


def receipt(api, message):
    if payment_methods.message(sys.modules[__name__], api, message):
        return True
    cid = message['chat']['id']
    if discounts.message(sys.modules[__name__], api, message):
        return True
    if handle_admin_photo(api, message):
        return True
    if handle_admin_text(api, message):
        return True
    if handle_admin_price(api, message):
        return True
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
        transfer_state = conn.execute('SELECT step,recipient,amount_sar FROM wallet_transfer_state WHERE cid=?', (cid,)).fetchone()
    if transfer_state:
        raw = (message.get('text') or '').strip()
        if raw.startswith('/'):
            with db() as conn:
                conn.execute('DELETE FROM wallet_transfer_state WHERE cid=?', (cid,))
            return False
        step, recipient, amount_sar = transfer_state
        if step == 'recipient':
            try:
                target = int(raw)
            except Exception:
                send(api, cid, tr(cid, 'أرسل رقم ID صحيح فقط.', 'Send a valid numeric user ID only.'))
                return True
            if target == cid:
                send(api, cid, tr(cid, 'لا يمكنك التحويل لنفس حسابك.', 'You cannot transfer to your own account.'))
                return True
            try:
                known_users = {int(x) for x in json.loads(USERS_PATH.read_text(encoding='utf-8'))}
            except Exception:
                known_users = set()
            if target not in known_users:
                send(api, cid, tr(cid, 'هذا المستخدم غير موجود داخل البوت.', 'This user is not registered in the bot.'))
                return True
            with db() as conn:
                conn.execute('UPDATE wallet_transfer_state SET step=?,recipient=? WHERE cid=?', ('amount', target, cid))
            send(api, cid, tr(cid, 'أرسل مبلغ التحويل بالدولار USD، مثال: 5', 'Send the transfer amount in USD, e.g. 5'))
            return True
        if step == 'amount':
            try:
                usd_value = Decimal(raw.replace(',', '.')).quantize(Decimal('0.01'))
            except Exception:
                send(api, cid, tr(cid, 'أرسل المبلغ كرقم فقط.', 'Send the amount as a number only.'))
                return True
            if usd_value <= 0:
                send(api, cid, tr(cid, 'المبلغ يجب أن يكون أكبر من صفر.', 'Amount must be greater than zero.'))
                return True
            sar_value = (usd_value * RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            if wallet_balance(cid) < sar_value:
                send(api, cid, tr(cid, 'رصيدك غير كافٍ.', 'Your balance is insufficient.'))
                return True
            with db() as conn:
                conn.execute('UPDATE wallet_transfer_state SET amount_sar=? WHERE cid=?', (str(sar_value), cid))
            send(api, cid,
                 tr(cid, 'تأكيد التحويل إلى المستخدم:', 'Confirm transfer to user:') + f' <code>{recipient}</code>\n<b>{usd_value:.2f} USD / {sar_value:.2f} SAR</b>',
                 kb([[btn(tr(cid, 'تأكيد التحويل', 'Confirm transfer'), f'wallettransferconfirm:{recipient}:{sar_value}', style='success')],
                     [btn(tr(cid, 'إلغاء', 'Cancel'), 'wallet', style='primary')]]))
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
    result = send(api, G['ADMIN_ID'], '🧾 <b>إثبات دفع جديد</b>\n\n' + esc(name(pid)) + f'\nالكمية: {product_options.snapshot(sys.modules[__name__], "receipt", cid)}\nالسعر عند الطلب: {sar} SAR / {usd} USD\nالطريقة: {esc(method)}\nالعميل: {esc(username)}\nID: <code>{cid}</code>')
    forwarded = api.call('forwardMessage', chat_id=G['ADMIN_ID'], from_chat_id=cid, message_id=message['message_id']) if result else None
    if not forwarded:
        send(api, cid, tr(cid, 'تعذر إرسال الإثبات للإدارة. أعد المحاولة أو تواصل مع ', 'Could not forward the receipt. Retry or contact ') + SUPPORT)
        return True
    add_order(cid, pid, method, 'review', usd=usd, sar=sar)
    with db() as conn:
        conn.execute('DELETE FROM payment_quotes WHERE cid=? AND pid=? AND method=?', (cid, pid, method))
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
    if payment_methods.action(sys.modules[__name__], api, cid, value):
        return
    if discounts.action(sys.modules[__name__], api, cid, value):
        return
    prefix, _, arg = value.partition(':')
    arg = LEGACY.get(arg, arg)
    if prefix in ('home', 'enter_store'):
        reset_navigation_state(cid)
        home(api, cid)
    elif prefix == 'start':
        start(api, cid)
    elif prefix == 'products':
        products(api, cid)
    elif prefix == 'referrals':
        referral_page(api, cid)
    elif prefix == 'product':
        category(api, cid, arg)
    elif prefix in ('item', 'claude'):
        item(api, cid, arg)
    elif value in LEGACY:
        item(api, cid, LEGACY[value])
    elif prefix == 'catdesc':
        admin_category_description(api, cid, arg)
    elif prefix == 'catdesclang':
        lang, _, pid = arg.partition(':')
        admin_category_description(api, cid, pid, lang)
    elif prefix == 'admin':
        if arg == 'categorydesc': admin_category_description(api, cid)
        elif arg == 'orders': admin_orders(api, cid)
        elif arg == 'activity': admin_activity(api, cid)
        elif arg == 'stats': admin_stats(api, cid)
        elif arg == 'icons': admin_icons(api, cid)
        elif arg == 'prices': admin_prices(api, cid)
        elif arg == 'photos': admin_photo_menu(api, cid)
        elif arg == 'editname': admin_text_menu(api, cid, 'name')
        elif arg == 'editdesc': admin_text_menu(api, cid, 'description')
        elif arg == 'stock': admin_stock(api, cid)
        elif arg == 'info': admin_info_menu(api, cid)
        elif arg == 'addproduct': begin_add_product(api, cid)
        elif arg == 'myproducts': admin_products_page(api, cid)
        elif arg == 'cancelproduct' and cid == G['ADMIN_ID']:
            with db() as conn: conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
            admin_panel(api, cid)
        elif arg == 'broadcast' and cid == G['ADMIN_ID']:
            BROADCAST_PENDING.add(cid)
            send(api, cid, '📢 <b>إرسال رسالة للجميع</b>\n\nأرسل الآن الرسالة التي تريد إرسالها لجميع مستخدمي البوت.\nيمكنك إرسال نص أو صورة مع تعليق.',
                 kb([[btn('❌ إلغاء', 'admin:broadcast_cancel')]]))
        elif arg == 'broadcast_cancel' and cid == G['ADMIN_ID']:
            BROADCAST_PENDING.discard(cid)
            admin_panel(api, cid)
        else: admin_panel(api, cid)
    elif prefix == 'orderdeliver' and cid == G['ADMIN_ID']:
        oid = arg
        with db() as conn:
            row = conn.execute('SELECT cid,status,pid FROM orders WHERE id=?', (oid,)).fetchone()
        if not row:
            return send(api, cid, '⚠️ الطلب غير موجود.')
        customer, status, pid = row
        if status == 'delivered':
            return send(api, cid, '✅ هذا الطلب تم تسليمه مسبقًا.')
        if status != 'paid':
            return send(api, cid, '⚠️ لازم يكون الطلب مدفوع قبل التسليم.')
        G['PENDING_ADMIN_DELIVERY'][G['ADMIN_ID']]={'customer':customer,'order_id':oid}
        return send(api, cid, f'📤 <b>تسليم الطلب #{esc(oid)}</b>\n{esc(name(pid, cid))}\n\nأرسل الآن الرسالة أو الكود أو الصورة أو الملف، وسيتم إرساله مباشرة للعميل وتسجيل الطلب كمُسلّم.')
    elif prefix == 'payreview' and cid == G['ADMIN_ID']:
        decision, _, oid = arg.partition(':')
        with db() as conn:
            row = conn.execute('SELECT cid,status FROM orders WHERE id=?', (oid,)).fetchone()
            if not row or row[1] != 'review':
                return send(api, cid, '⚠️ الطلب غير موجود أو تمت معالجته مسبقاً.')
            customer = row[0]
            if decision == 'accept':
                conn.execute('UPDATE orders SET status="paid" WHERE id=? AND status="review"', (oid,))
                if fulfill_paid_order(api, oid):
                    return send(api, cid, f'✅ تم قبول الطلب <b>#{esc(oid)}</b> وبدأ التنفيذ التلقائي عبر المورد.')
                G['PENDING_ADMIN_DELIVERY'][G['ADMIN_ID']]={'customer':customer,'order_id':oid}
                send(api, customer, '✅ <b>تم قبول الدفع.</b>\n\nسيتم إرسال طلبك لك قريباً.')
                return send(api, cid, f'✅ تم قبول الطلب <b>#{esc(oid)}</b>.\n\n📤 أرسل الآن أي رسالة أو صورة أو ملف تريد إرساله للعميل.\nسيتم إرسال <b>الرسالة التالية فقط</b> له مباشرة.')
            conn.execute('UPDATE orders SET status="rejected" WHERE id=? AND status="review"', (oid,))
        send(api, customer, '❌ <b>تم رفض إثبات الدفع.</b>\n\nيرجى إعادة المحاولة أو التواصل مع الدعم.')
        send(api, cid, f'❌ تم رفض الطلب <b>#{esc(oid)}</b> وإبلاغ العميل.')
    elif prefix == 'infocat':
        admin_info_menu(api, cid, arg)
    elif prefix == 'infopick':
        admin_info_editor(api, cid, arg)
    elif prefix == 'infotoggle' and cid == G['ADMIN_ID']:
        field, _, pid = arg.partition(':'); sp, ss, sw, w = info_display(pid)
        if field == 'price': sp = 0 if sp else 1
        elif field == 'stock': ss = 0 if ss else 1
        elif field == 'warranty': sw = 0 if sw else 1
        with db() as conn: conn.execute('INSERT OR REPLACE INTO product_info_display VALUES (?,?,?,?,?)', (pid, sp, ss, sw, w))
        admin_info_editor(api, cid, pid)
    elif prefix == 'infowarranty' and cid == G['ADMIN_ID']:
        with db() as conn: conn.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)', (cid, 'info_warranty', arg))
        send(api, cid, '✏️ أرسل نص الضمان لهذا المنتج، مثال: <b>15 يوم</b>.', kb([[btn('إلغاء', 'admin:info')]]))
    elif prefix == 'infoicon' and cid == G['ADMIN_ID']:
        field, _, pid = arg.partition(':')
        if field in ('price','stock','warranty'):
            with db() as conn: conn.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)',(cid,'info_icon',field+':'+pid))
            target_text = 'لجميع المنتجات' if pid == '__global__' else 'لهذا المنتج'
            cancel_action = 'admin:info' if pid == '__global__' else 'infopick:'+pid
            send(api,cid,'أرسل الآن الأيقونة المتحركة '+target_text+'.',kb([[btn('❌ إلغاء',cancel_action)]]))
    elif prefix == 'pandorabrowse' and cid == G['ADMIN_ID']:
        pandora_catalog_open(api, cid, arg, 0)
    elif prefix == 'pandorapage' and cid == G['ADMIN_ID']:
        pandora_catalog_categories(api, cid)
    elif prefix == 'pandoracategories' and cid == G['ADMIN_ID']:
        pandora_catalog_categories(api, cid)
    elif prefix == 'pandoracat' and cid == G['ADMIN_ID']:
        cidx, _, page = arg.partition(':')
        pandora_catalog_page(api, cid, cidx or 0, page or 0)
    elif prefix == 'pandorap' and cid == G['ADMIN_ID']:
        pandora_catalog_product(api, cid, arg)
    elif prefix == 'pandorav' and cid == G['ADMIN_ID']:
        pidx, _, vidx = arg.partition(':')
        pandora_catalog_save(api, cid, pidx, vidx)
    elif prefix == 'suppliercat':
        supplier_api_menu(api, cid, arg)
    elif prefix == 'supplierpick':
        supplier_api_editor(api, cid, arg)
    elif prefix == 'supplierpandora' and cid == G['ADMIN_ID']:
        pid = LEGACY.get(arg, arg)
        endpoint, api_key, service_id, enabled, provider, variant_id = supplier_api_row(pid)
        endpoint = 'https://api.pandoradigital.shop/api/v1'
        provider = 'pandora'
        with db() as conn:
            conn.execute('INSERT INTO supplier_api(pid,endpoint,api_key,service_id,enabled,provider,variant_id) VALUES (?,?,?,?,?,?,?) ON CONFLICT(pid) DO UPDATE SET endpoint=excluded.endpoint,provider=excluded.provider',
                         (pid, endpoint, api_key, service_id, enabled, provider, variant_id))
        supplier_api_editor(api, cid, pid)
    elif prefix == 'supplierset' and cid == G['ADMIN_ID']:
        field, _, pid = arg.partition(':')
        if field in ('service', 'variant'):
            action_name = {'service':'supplier_service','variant':'supplier_variant'}[field]
            with db() as conn:
                conn.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)', (cid, action_name, pid))
            prompt = {'service':'أرسل Product ID لدى المورد.','variant':'أرسل Variant ID لدى المورد.'}[field]
            send(api, cid, '🔌 <b>' + esc(name(pid, cid)) + '</b>\n\n' + prompt, kb([[btn('❌ إلغاء', 'supplierpick:' + pid)]]))
    elif prefix == 'suppliertest' and cid == G['ADMIN_ID']:
        supplier_test_connection(api, cid, arg)
    elif prefix == 'suppliertoggle' and cid == G['ADMIN_ID']:
        pid = LEGACY.get(arg, arg)
        endpoint, api_key, service_id, enabled, provider, variant_id = supplier_api_row(pid)
        missing = []
        if not endpoint: missing.append('رابط API')
        if not api_key: missing.append('مفتاح API')
        if not service_id: missing.append('Product ID')
        if not variant_id: missing.append('Variant ID')
        if missing:
            send(api, cid, '⚠️ باقي قبل التفعيل: <b>' + esc(' + '.join(missing)) + '</b>.')
            supplier_api_editor(api, cid, pid)
        else:
            with db() as conn:
                conn.execute('INSERT INTO supplier_api(pid,endpoint,api_key,service_id,enabled,provider,variant_id) VALUES (?,?,?,?,?,?,?) ON CONFLICT(pid) DO UPDATE SET enabled=excluded.enabled',
                             (pid, endpoint, api_key, service_id, 0 if enabled else 1, provider, variant_id))
            supplier_api_editor(api, cid, pid)
    elif prefix == 'supplierdelete' and cid == G['ADMIN_ID']:
        pid = LEGACY.get(arg, arg)
        with db() as conn:
            conn.execute('DELETE FROM supplier_api WHERE pid=?', (pid,))
        supplier_api_editor(api, cid, pid)
    elif prefix == 'mycategory':
        admin_category_detail(api, cid, arg)
    elif prefix == 'myproduct':
        admin_product_detail(api, cid, arg)
    elif prefix == 'myproducttoggle':
        toggle_admin_product(api, cid, arg)
    elif prefix == 'myproductdelete':
        delete_admin_product(api, cid, arg)
    elif prefix == 'stockcat':
        admin_stock(api, cid, arg)
    elif prefix == 'stockpick':
        stock_editor(api, cid, arg)
    elif prefix == 'stockset':
        value, _, pid = arg.partition(':')
        if value in ('0', '1'):
            stock_editor(api, cid, pid, value)
    elif prefix == 'txtcat':
        field, _, category_id = arg.partition(':')
        admin_text_menu(api, cid, field, category_id)
    elif prefix == 'txtpick':
        field, _, pid = arg.partition(':')
        admin_text_editor(api, cid, field, pid)
    elif prefix == 'txtedit':
        parts = arg.split(':', 2)
        if len(parts) == 3:
            field, lang, pid = parts
            admin_text_editor(api, cid, field, pid, lang)
    elif prefix == 'photocat':
        admin_photo_menu(api, cid, arg)
    elif prefix == 'photopick':
        admin_photo_editor(api, cid, arg)
    elif prefix == 'photodel':
        admin_photo_editor(api, cid, arg, delete=True)
    elif prefix == 'pricecat':
        admin_prices(api, cid, arg)
    elif prefix == 'pricepick':
        price_editor(api, cid, arg)
    elif prefix == 'priceedit':
        currency, _, pid = arg.partition(':')
        price_editor(api, cid, pid, currency)
    elif prefix == 'iconmenu':
        if arg == 'products': admin_icon_products(api, cid)
        elif arg == 'categories': admin_icon_categories(api, cid)
        elif arg == 'buttons': admin_icon_buttons(api, cid)
        else: admin_icons(api, cid)
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
            stored_value = arg if prefix == 'setlang' else 'USD'
            conn.execute(f'UPDATE preferences SET {column}=? WHERE cid=?', (stored_value, cid))
        home(api, cid)
    elif prefix in ('buy', 'cancel'):
        with db() as conn:
            conn.execute('DELETE FROM receipts WHERE cid=?', (cid,))
        if prefix == 'buy': payments(api, cid, arg)
        elif arg in VARIANTS: item(api, cid, arg)
        else: category(api, cid, arg)
    elif prefix == 'wallet':
        if arg == 'topup': wallet_amounts(api, cid)
        elif arg == 'transfer': wallet_transfer_begin(api, cid)
        else: wallet(api, cid)
    elif prefix == 'wallettransferconfirm':
        recipient, _, amount_sar = arg.partition(':')
        wallet_transfer_confirm(api, cid, recipient, amount_sar)
    elif prefix == 'topupmanual':
        method_id, _, value = arg.partition(':')
        wallet_manual_topup(api, cid, method_id, value)
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
        send(api, cid, 'Support:' + SUPPORT, menu(cid))
    elif prefix == 'api':
        send(api, cid, tr(cid, '🔗 إعدادات API المتجر غير مفعّلة حاليًا.', '🔗 Store API settings are not active yet.'), menu(cid))
    elif prefix == 'warranty':
        send(api, cid, tr(cid, '🛡 تختلف شروط الضمان حسب المنتج. راجع وصفه قبل الطلب. للاستفسار: ', '🛡 Warranty terms vary by product. Read its description before ordering. Questions: ') + SUPPORT, menu(cid))
    else:
        products(api, cid)


def install(namespace):
    global G
    G = namespace
    try:
        __import__('threading').Thread(target=pandora_startup_probe, daemon=True).start()
    except Exception:
        pass
    apply_icon_overrides()
    namespace.update({'show_start': start, 'show_home': home, 'show_products': products,
                      'show_product': category, 'show_claude_product': item, 'handle_action': action, 'action': action,
                      'handle_receipt': receipt, 'order_name': name, 'home_keyboard': menu,
                      'broadcast_new_products': broadcast_new_products,
                      'handle_admin_product': handle_admin_product, 'handle_info_warranty': handle_info_warranty, 'handle_info_icon': handle_info_icon})
    menu_actions = namespace.setdefault('MENU_ACTIONS', namespace.get('MENU', {}))
    menu_actions.update({'🚀 ابدأ': 'start', '🚀 Start': 'start', '🛍 المنتجات': 'products',
                         '🛍 Products': 'products', '⚡ VEXA VOLT': 'support', '⚡ VEXA VOLT': 'support',
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
