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
    conn.execute('CREATE TABLE IF NOT EXISTS user_delivery_status (cid INTEGER PRIMARY KEY, departed INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL DEFAULT "")')
    conn.execute('CREATE TABLE IF NOT EXISTS broadcast_stats (id INTEGER PRIMARY KEY CHECK(id=1), sent INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT "")')
    conn.execute('CREATE TABLE IF NOT EXISTS product_prices (pid TEXT PRIMARY KEY, value TEXT NOT NULL, currency TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS product_availability (pid TEXT PRIMARY KEY, available INTEGER NOT NULL CHECK(available IN (0,1)))')
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
    conn.execute('CREATE TABLE IF NOT EXISTS product_photos (pid TEXT PRIMARY KEY, file_id TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS product_info_display (pid TEXT PRIMARY KEY, show_price INTEGER NOT NULL DEFAULT 1, show_stock INTEGER NOT NULL DEFAULT 1, show_warranty INTEGER NOT NULL DEFAULT 0, warranty TEXT NOT NULL DEFAULT "")')
    conn.execute('CREATE TABLE IF NOT EXISTS product_info_icons (pid TEXT NOT NULL, field TEXT NOT NULL, custom_emoji_id TEXT NOT NULL, PRIMARY KEY(pid,field))')
    icon_cols={row[1] for row in conn.execute('PRAGMA table_info(product_info_icons)').fetchall()}
    if 'fallback_emoji' not in icon_cols:
        conn.execute('ALTER TABLE product_info_icons ADD COLUMN fallback_emoji TEXT NOT NULL DEFAULT "⭐"')
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


def add_order(cid, pid, method, status, usd=None, sar=None):
    order_id = uuid.uuid4().hex[:10].upper()
    with db() as conn:
        conn.execute('INSERT INTO orders VALUES (?,?,?,?,?,?,?,?)',
                     (order_id, cid, pid, method, str(amount(pid, 'USD') if usd is None else usd), str(amount(pid, 'SAR') if sar is None else sar), status, now_saudi()))
    return order_id


def customer_link(cid):
    return f'<a href="tg://user?id={cid}">{cid}</a>'


UI_ICON_LABELS = {
    'ui_start': '🚀 ابدأ / START', 'ui_products': '🛒 المنتجات', 'ui_topup': '💰 شحن الرصيد',
    'ui_referrals': '💎 الإحالات', 'ui_account': '👤 حسابي', 'ui_support': '💬 الدعم',
    'ui_report': '⚠️ إبلاغ عن مشكلة', 'ui_currency': '💱 العملة', 'ui_language': '🌐 اللغة',
    'ui_admin': '🧾 لوحة الطلبات', 'ui_back': '↩️ رجوع', 'ui_home': '🏠 الرئيسية',
    'ui_chatgpt': 'التحدث مع ChatGPT', 'ui_chatgpt_end': 'إنهاء المحادثة', 'ui_chatgpt_home': 'الرجوع للصفحة الرئيسية',
    'pay_wallet': 'المحفظة', 'pay_cryptopay': 'Crypto Pay', 'pay_bybit': 'USDT — Bybit',
    'pay_bybitid': 'Bybit Pay', 'pay_trc20': 'USDT • TRON (TRC20)', 'pay_bep20': 'USDT • BSC (BEP20)'
}

def ui_icon(key):
    with db() as conn:
        row = conn.execute('SELECT custom_emoji_id FROM category_icons WHERE pid=?', (key,)).fetchone()
    return row[0] if row else None

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
        ids = [pid for pid, v in VARIANTS.items() if v['category'] == category_id]
        if not ids and category_id in G['PRODUCTS']:
            ids = [category_id]
        rows = [[btn(('✅ ' if in_stock(pid) else '🔴 ') + name(pid, cid), 'stockpick:' + pid,
                     style=None if in_stock(pid) else 'danger')] for pid in ids]
    send(api, cid, '📦 <b>تعديل توفر المنتج</b>\nاختر القسم ثم المنتج:', kb(rows + [[btn('↩️ لوحة الإدارة', 'admin')]]))


def stock_editor(api, cid, pid, value=None):
    if cid != G['ADMIN_ID'] or (pid not in VARIANTS and pid not in G['PRODUCTS']):
        return
    saved = value in ('0', '1')
    if saved:
        with db() as conn:
            conn.execute('INSERT OR REPLACE INTO product_availability VALUES (?,?)', (pid, int(value)))
    status = '✅ متوفر' if in_stock(pid) else '🔴 نفدت الكمية'
    text = ('✅ تم حفظ الحالة\n\n' if saved else '') + '<b>' + esc(name(pid, cid)) + '</b>\n\n' + status
    if VARIANTS.get(pid, {}).get('review_required'):
        text += '\n⚠️ المنتج قيد المراجعة؛ تغيير التوفر لا يلغي إيقاف الطلب للمراجعة.'
    send(api, cid, text, kb([[btn('✅ متوفر', 'stockset:1:' + pid, style='success'),
                              btn('🔴 نفدت الكمية', 'stockset:0:' + pid, style='danger')],
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


def info_display(pid):
    with db() as conn:
        row = conn.execute('SELECT show_price,show_stock,show_warranty,warranty FROM product_info_display WHERE pid=?', (LEGACY.get(pid,pid),)).fetchone()
    return row or (1,1,0,'')

def product_stock(pid):
    cp=custom_product(pid)
    if cp: return int(cp[6] or 0)
    v=VARIANTS.get(LEGACY.get(pid,pid),{})
    for key in ('stock','quantity','available_quantity'):
        if key in v:
            try:return int(v[key])
            except:return v[key]
    return 1 if in_stock(pid) else 0

def info_icon(pid,field,fallback):
    with db() as conn:
        row=conn.execute('SELECT custom_emoji_id,fallback_emoji FROM product_info_icons WHERE pid=? AND field=?',(LEGACY.get(pid,pid),field)).fetchone()
    return ('<tg-emoji emoji-id="'+esc(row[0])+'">'+esc(row[1] or fallback)+'</tg-emoji>') if row else fallback

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
        rows=[[btn(label,'infocat:'+pid)] for pid,label in cats]
    else:
        ids=[pid for pid,v in VARIANTS.items() if v['category']==category_id]+custom_ids
        if not ids and category_id in G['PRODUCTS']: ids=[category_id]
        rows=[[btn(name(pid,cid),'infopick:'+pid)] for pid in ids]
    send(api,cid,'🎛 <b>بيانات المنتج الظاهرة</b>\nاختر القسم ثم المنتج:',kb(rows+[[btn('↩️ لوحة الإدارة','admin')]]))

def admin_info_editor(api,cid,pid):
    if cid!=G['ADMIN_ID']: return
    sp,ss,sw,w=info_display(pid)
    rows=[[btn(('✅ ' if sp else '❌ ')+'السعر','infotoggle:price:'+pid),btn(('✅ ' if ss else '❌ ')+'الكمية','infotoggle:stock:'+pid)],
          [btn(('✅ ' if sw else '❌ ')+'الضمان','infotoggle:warranty:'+pid),btn('✏️ نص الضمان','infowarranty:'+pid)],
          [btn('💵 أيقونة السعر','infoicon:price:'+pid),btn('📦 أيقونة الكمية','infoicon:stock:'+pid)],
          [btn('🛡 أيقونة الضمان','infoicon:warranty:'+pid)],
          [btn('↩️ منتج آخر','admin:info')],[btn('↩️ لوحة الإدارة','admin')]]
    send(api,cid,'🎛 <b>'+esc(name(pid,cid))+'</b>\n\nحدد المعلومات التي تريد ظهورها للعميل.\nالضمان الحالي: <b>'+esc(w or 'غير محدد')+'</b>',kb(rows))

def admin_text_menu(api, cid, field, category_id=None):
    if cid != G['ADMIN_ID'] or field not in ('name', 'description'):
        return
    with db() as conn:
        conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
        custom_cats = conn.execute('SELECT cid,name FROM admin_categories').fetchall()
        custom_ids = [r[0] for r in conn.execute('SELECT pid FROM admin_products WHERE category_id=?', (category_id,)).fetchall()] if category_id else []
    if category_id is None:
        cats = [(pid, p['name']) for pid, p in G['PRODUCTS'].items()] + custom_cats
        rows = [[btn(label, f'txtcat:{field}:{pid}')] for pid, label in cats]
    else:
        ids = [pid for pid, v in VARIANTS.items() if v['category'] == category_id] + custom_ids
        if not ids and category_id in G['PRODUCTS']:
            ids = [category_id]
        rows = [[btn(name(pid, cid), f'txtpick:{field}:{pid}')] for pid in ids]
    send(api, cid, '✏️ اختر القسم ثم المنتج لتعديل ' + ('الاسم' if field == 'name' else 'الوصف'), kb(rows + [[btn('↩️ لوحة الإدارة', 'admin')]]))


def admin_text_editor(api, cid, field, pid, lang=None):
    if cid != G['ADMIN_ID'] or field not in ('name', 'description'):
        return
    if pid not in VARIANTS and pid not in G['PRODUCTS'] and not custom_product(pid):
        return
    if lang not in ('ar', 'en'):
        return send(api, cid, 'اختر لغة النص الذي تريد تعديله:', kb([[btn('العربية', f'txtedit:{field}:ar:{pid}'), btn('English', f'txtedit:{field}:en:{pid}')], [btn('إلغاء', 'admin')]]))
    BROADCAST_PENDING.discard(cid)
    with db() as conn:
        conn.execute('DELETE FROM custom_topup_state WHERE cid=?', (cid,))
        conn.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)', (cid, 'product_text', json.dumps([pid, field, lang])))
    label = 'الاسم الجديد (حتى 120 حرفًا)' if field == 'name' else 'الوصف الجديد (حتى 1500 حرف، ويمكن استخدام عدة أسطر)'
    send(api, cid, 'أرسل ' + label + (' بالعربية.' if lang == 'ar' else ' بالإنجليزية.'), kb([[btn('إلغاء', 'admin')]]))


def handle_info_icon(api,message):
    cid=message.get('chat',{}).get('id')
    if cid!=G.get('ADMIN_ID'): return False
    with db() as conn: row=conn.execute("SELECT value FROM admin_state WHERE cid=? AND action='info_icon'",(cid,)).fetchone()
    if not row: return False
    entities=list(message.get('entities',[]))+list(message.get('caption_entities',[]))
    emoji=next((e.get('custom_emoji_id') for e in entities if e.get('type')=='custom_emoji' and e.get('custom_emoji_id')),None)
    if not emoji:
        send(api,cid,'لم أجد أيقونة مخصصة. أرسل الأيقونة المتحركة نفسها.',kb([[btn('❌ إلغاء','admin:info')]])); return True
    field,_,pid=row[0].partition(':')
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
    admin_info_editor(api,cid,pid); return True

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
    cid = message.get('chat', {}).get('id')
    if cid != G.get('ADMIN_ID'):
        return False
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
    with db() as conn:
        conn.execute('INSERT OR REPLACE INTO product_text VALUES (?,?,?,?)', (pid, field, lang, text))
        conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
    send(api, cid, '✅ تم حفظ ' + ('اسم المنتج' if field == 'name' else 'وصف المنتج') + '\n\n' + esc(text), kb([[btn('تعديل منتج آخر', 'admin:editname' if field == 'name' else 'admin:editdesc')], [btn('لوحة الإدارة', 'admin')]]))
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
        ids = [pid for pid, v in VARIANTS.items() if v['category'] == category_id] + custom_ids
        if not ids and category_id in G['PRODUCTS']:
            ids = [category_id]
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
    if delete:
        return send(api, cid, '✅ تم حذف صورة المنتج. سيظهر دون صورة عند فتحه مجددًا.', kb([[btn('🖼️ إضافة صورة', 'photopick:' + pid)], [btn('لوحة الإدارة', 'admin')]]))
    send(api, cid, '<b>' + esc(name(pid, cid)) + '</b>\n\nأرسل الصورة هنا كصورة في تيليجرام لإضافتها أو استبدال الصورة الحالية.', kb([[btn('🗑 حذف الصورة', 'photodel:' + pid, style='danger')], [btn('إلغاء', 'admin')]]))


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
    send(api, cid, '✅ تم حفظ صورة المنتج.', kb([[btn('👁 معاينة المنتج', ('item:' if row[0] in VARIANTS or custom_product(row[0]) else 'product:') + row[0])], [btn('🖼️ منتج آخر', 'admin:photos')], [btn('لوحة الإدارة', 'admin')]]))
    return True


def admin_prices(api, cid, category_id=None):
    if cid != G['ADMIN_ID']:
        return
    with db() as conn:
        conn.execute("DELETE FROM admin_state WHERE cid=? AND action='price'", (cid,))
    if category_id is None:
        rows = [[btn(p['name'], 'pricecat:' + pid)] for pid, p in G['PRODUCTS'].items()]
    else:
        ids = [pid for pid, v in VARIANTS.items() if v['category'] == category_id]
        if not ids and category_id in G['PRODUCTS']:
            ids = [category_id]
        rows = [[btn(name(pid, cid) + ' | ' + price(cid, pid, 'SAR'), 'pricepick:' + pid)] for pid in ids]
    send(api, cid, '✏️ اختر القسم أو المنتج لتعديل سعر البيع:', kb(rows + [[btn('↩️ لوحة الإدارة', 'admin')]]))


def price_editor(api, cid, pid, currency=None):
    if cid != G['ADMIN_ID'] or (pid not in VARIANTS and pid not in G['PRODUCTS']):
        return
    if currency not in ('SAR', 'USD'):
        return send(api, cid, esc(name(pid, cid)) + '\nالسعر الحالي: ' + price(cid, pid, 'SAR') + ' / ' + price(cid, pid, 'USD') + '\nاختر عملة السعر الجديد:', kb([[btn('ريال سعودي', 'priceedit:SAR:' + pid), btn('دولار', 'priceedit:USD:' + pid)], [btn('إلغاء', 'admin:prices')]]))
    BROADCAST_PENDING.discard(cid)
    with db() as conn:
        conn.execute('DELETE FROM custom_topup_state WHERE cid=?', (cid,))
        conn.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)', (cid, 'price', json.dumps([pid, currency])))
    send(api, cid, 'أرسل سعر البيع النهائي بالعملة ' + currency + '\nمثال: 19.50\nسيُحوّل للعملة الأخرى تلقائيًا، دون إضافة هامش ربح فوقه.', kb([[btn('إلغاء', 'admin:prices')]]))


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
    pid, currency = json.loads(state[0])
    with db() as conn:
        conn.execute('INSERT OR REPLACE INTO product_prices VALUES (?,?,?)', (pid, str(value), currency))
        conn.execute("DELETE FROM admin_state WHERE cid=? AND action='price'", (cid,))
    send(api, cid, '✅ تم حفظ سعر ' + esc(name(pid, cid)) + '\n' + price(cid, pid, 'SAR') + ' / ' + price(cid, pid, 'USD'), kb([[btn('تعديل منتج آخر', 'admin:prices')], [btn('لوحة الإدارة', 'admin')]]))
    return True



def admin_products_page(api, cid):
    if cid != G['ADMIN_ID']:
        return home(api, cid)
    with db() as conn:
        categories = conn.execute('SELECT cid,name FROM admin_categories ORDER BY rowid DESC').fetchall()
    buttons = [[btn('📁 ' + category_name, 'mycategory:' + category_id)] for category_id, category_name in categories]
    text = '📦 <b>منتجاتي</b>\n\nاختر قسمًا لإدارة منتجاته:' if buttons else '📦 <b>منتجاتي</b>\n\nلا توجد أقسام مضافة حتى الآن.'
    send(api, cid, text, kb(buttons + [[btn('➕ إضافة قسم ومنتجات', 'admin:addproduct', style='success')], [btn('↩️ لوحة الإدارة', 'admin')]]))


def admin_category_detail(api, cid, category_id):
    if cid != G['ADMIN_ID']:
        return home(api, cid)
    cat = custom_category(category_id)
    if not cat:
        return admin_products_page(api, cid)
    with db() as conn:
        rows = conn.execute('SELECT pid,name,price_usd,available,stock FROM admin_products WHERE category_id=? ORDER BY rowid', (category_id,)).fetchall()
    buttons = [[btn(('✅ ' if available and int(stock or 0)>0 else '🔴 ') + product_name + ' • $' + str(price_usd), 'myproduct:' + pid)] for pid, product_name, price_usd, available, stock in rows]
    send(api, cid, '📁 <b>' + esc(cat[1]) + '</b>\n\nالمنتجات داخل القسم:', kb(buttons + [[btn('↩️ منتجاتي', 'admin:myproducts')]]))


def begin_add_product(api, cid):
    if cid != G['ADMIN_ID']:
        return home(api, cid)
    BROADCAST_PENDING.discard(cid)
    with db() as conn:
        conn.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)', (cid, 'add_product_category', '{}'))
    send(api, cid, '➕ <b>إضافة قسم ومنتجات</b>\n\n1️⃣ أرسل <b>اسم القسم</b> الذي سيظهر للعملاء.\nمثال: <code>ChatGPT</code>', kb([[btn('❌ إلغاء', 'admin:cancelproduct')]]))


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
        prompt = '3️⃣ أرسل <b>اسم المنتج 1 من ' + str(count) + '</b>.'
    elif action_name == 'add_product_name':
        payload['current'] = {'name': raw[:100]}
        next_action = 'add_product_desc'
        prompt = '📝 أرسل <b>وصف المنتج</b> لـ <b>' + esc(payload['current']['name']) + '</b>.'
    elif action_name == 'add_product_desc':
        payload['current']['description'] = raw[:1500]
        next_action = 'add_product_price'
        prompt = '💵 أرسل <b>السعر بالدولار USD</b>.\nمثال: <code>5.36</code>'
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
            prompt = '✅ تم حفظ بيانات المنتج ' + str(payload['index']) + '.\n\nأرسل <b>اسم المنتج ' + str(payload['index'] + 1) + ' من ' + str(payload['count']) + '</b>.'
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
        category_id = 'cat_' + uuid.uuid4().hex[:10]
        saved_product_ids = []
        with db() as conn:
            conn.execute('INSERT INTO admin_categories(cid,name,created_at) VALUES (?,?,?)', (category_id, payload['category_name'], now_saudi()))
            for product in payload['products']:
                pid = 'custom_' + uuid.uuid4().hex[:10]
                usd = Decimal(product['price_usd']).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                sar = (usd * RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                conn.execute('INSERT INTO admin_products(pid,name,description,price_sar,available,created_at,category_id,price_usd,stock) VALUES (?,?,?,?,1,?,?,?,?)',
                             (pid, product['name'], product.get('description',''), str(sar), now_saudi(), category_id, str(usd), int(product.get('stock',1))))
                saved_product_ids.append(pid)
            conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
        for new_pid in saved_product_ids:
            broadcast_product_alert(api, new_pid, 'new')
        send(api, cid, '✅ تم إضافة القسم وكل المنتجات، وأصبحت ظاهرة للعملاء داخل قائمة المنتجات.', kb([[btn('📦 منتجاتي', 'admin:myproducts')], [btn('➕ إضافة قسم آخر', 'admin:addproduct')], [btn('↩️ لوحة الإدارة', 'admin')]]))
        return True

    with db() as conn:
        conn.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)', (cid, next_action, json.dumps(payload, ensure_ascii=False)))
    send(api, cid, prompt, kb([[btn('❌ إلغاء', 'admin:cancelproduct')]]))
    return True


def admin_product_detail(api, cid, pid):
    if cid != G['ADMIN_ID']:
        return home(api, cid)
    cp = custom_product(pid)
    if not cp:
        return admin_products_page(api, cid)
    _, product_name, description, price_usd, available, category_id, stock = cp
    text = '📦 <b>' + esc(product_name) + '</b>\n\n' + esc(description) + '\n\n💵 $' + esc(price_usd) + '\n📦 الكمية: ' + str(stock) + '\nالحالة: ' + ('✅ متوفر' if available and int(stock or 0)>0 else '🔴 غير متوفر')
    send(api, cid, text, kb([[btn('🔄 تغيير التوفر', 'myproducttoggle:' + pid)], [btn('🗑 حذف المنتج', 'myproductdelete:' + pid, style='danger')], [btn('↩️ القسم', 'mycategory:' + category_id)]]))


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
        activity_count = conn.execute('SELECT COUNT(*) FROM activity').fetchone()[0]
    text = f'🧾 <b>لوحة إدارة VEXA</b>\n\nالطلبات: <b>{orders_count}</b>\nبانتظار المراجعة: <b>{review_count}</b>\nسجل الاختيارات: <b>{activity_count}</b>'
    send(api, cid, text, kb([[btn('📦 الطلبات الأخيرة', 'admin:orders', style='primary')],
                             [btn('👀 نشاط العملاء', 'admin:activity')],
                             [btn('➕ إضافة منتج', 'admin:addproduct', style='success'), btn('📦 منتجاتي', 'admin:myproducts')],
                             [btn('✏️ تعديل سعر منتج', 'admin:prices')],
                             [btn('🎛 إعداد عرض بيانات المنتج', 'admin:info', style='primary')],
                             [btn('📦 تعديل توفر المنتج', 'admin:stock')],
                             [btn('📢 إرسال رسالة للجميع', 'admin:broadcast', style='primary')],
                             [btn('📊 الإحصائيات', 'admin:stats')],
                             [btn('➕ إضافة أيقونة', 'admin:icons', style='success')],
                             [btn('🏠 الرئيسية', 'home')]]))


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
        rows = conn.execute('SELECT id,cid,pid,method,usd,sar,status,created_at FROM orders ORDER BY rowid DESC LIMIT 15').fetchall()
    if not rows:
        return send(api, cid, '📦 لا توجد طلبات مسجلة حتى الآن.', kb([[btn('↩️ لوحة الإدارة', 'admin')]]))
    status_names = {'paid': '✅ مدفوع', 'review': '⏳ مراجعة', 'rejected': '❌ مرفوض'}
    parts = ['📦 <b>آخر الطلبات</b>']
    review_buttons = []
    for oid, user_id, pid, method, usd, sar, status, created in rows:
        parts.append(f'\n<b>#{esc(oid)}</b> • {status_names.get(status, esc(status))}\n{esc(name(pid, cid))}\n{esc(sar)} SAR / {esc(usd)} USD • {esc(method)}\nالعميل: {customer_link(user_id)} • {esc(created)}')
        if status == 'review':
            review_buttons.append([btn('✅ قبول #' + oid, 'payreview:accept:' + oid, style='success'), btn('❌ رفض', 'payreview:reject:' + oid, style='danger')])
    review_buttons.extend([[btn('🔄 تحديث', 'admin:orders')], [btn('↩️ لوحة الإدارة', 'admin')]])
    send(api, cid, '\n'.join(parts), kb(review_buttons))


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
        buttons.append(btn(product['name'], 'seticon:' + pid, product.get('custom_emoji_id')))
    with db() as conn:
        custom_categories = conn.execute('SELECT cid,name FROM admin_categories ORDER BY rowid').fetchall()
        custom_products = conn.execute('SELECT pid,name FROM admin_products ORDER BY rowid').fetchall()
    for category_id, category_name in custom_categories:
        buttons.append(btn('📁 ' + category_name, 'seticon:' + category_id, ui_icon(category_id)))
    for product_id, product_name in custom_products:
        buttons.append(btn('📦 ' + product_name, 'seticon:' + product_id, ui_icon(product_id)))
    for key, label in UI_ICON_LABELS.items():
        icon = ui_icon(key)
        buttons.append(btn(label, 'seticon:' + key, icon))
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    send(api, cid, '➕ <b>إضافة أيقونة متحركة</b>\n\nيمكنك الآن اختيار الأقسام والمنتجات الجديدة أيضًا، ثم إرسال الأيقونة للبوت.',
         kb(rows + [[btn('↩️ لوحة الإدارة', 'admin')]]))


def begin_icon_setup(api, cid, pid):
    if cid != G['ADMIN_ID']:
        return home(api, cid)
    custom_cat = custom_category(pid)
    cp = custom_product(pid)
    if pid not in G['PRODUCTS'] and pid not in UI_ICON_LABELS and not custom_cat and not cp:
        return admin_icons(api, cid)
    with db() as conn:
        conn.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)', (cid, 'icon', pid))
    if pid in G['PRODUCTS']:
        label = G['PRODUCTS'][pid]['name']
    elif pid in UI_ICON_LABELS:
        label = UI_ICON_LABELS[pid]
    elif custom_cat:
        label = custom_cat[1]
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
    if pid in VARIANTS:
        return text_override(pid, 'name', prefs(cid)[0], VARIANTS[pid]['name'][prefs(cid)[0]])
    cp = custom_product(pid)
    if cp:
        return text_override(pid, 'name', prefs(cid)[0], cp[1])
    return text_override(pid, 'name', prefs(cid)[0], G['PRODUCTS'].get(pid, {}).get('name', pid))


def reset_navigation_state(cid):
    """Exit any unfinished input/payment flow when the user explicitly starts over or goes home."""
    with db() as conn:
        conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
        conn.execute('DELETE FROM custom_topup_state WHERE cid=?', (cid,))
        conn.execute('DELETE FROM receipts WHERE cid=?', (cid,))
        conn.execute('UPDATE wallet_topups SET status="cancelled" WHERE cid=? AND status="receipt_pending"', (cid,))


def start(api, cid):
    reset_navigation_state(cid)
    send(api, cid, tr(cid, '👋 <b>مرحباً بك في VEXA STORE</b>\n\nمتجر الخدمات والاشتراكات الرقمية.',
                     '👋 <b>Welcome to VEXA STORE</b>\n\nDigital services and subscriptions.'),
         kb([[btn('🚀 START | ابدأ', 'enter_store', ui_icon('ui_start'))], [btn('🌐 العربية / English', 'settings:lang', ui_icon('ui_language'))]]))


def home(api, cid):
    balance_sar = wallet_balance(cid)
    balance_usd = (balance_sar / RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    with db() as conn:
        purchases = conn.execute('SELECT COUNT(*) FROM orders WHERE cid=? AND status="paid"', (cid,)).fetchone()[0]
    text = tr(cid, f'👋 <b>أهلاً بك في VEXA STORE!</b>\n\n🆔 رقم العضوية: <code>{cid}</code>\n👤 حسابك: <a href="tg://user?id={cid}">فتح الحساب</a>\n💳 الرصيد: <b>${balance_usd:.2f}</b>\n🛍 المشتريات: <b>{purchases}</b>\n\nاختر من القائمة أدناه:', f'👋 <b>Welcome to VEXA STORE!</b>\n\n🆔 Member ID: <code>{cid}</code>\n👤 Account: <a href="tg://user?id={cid}">Open profile</a>\n💳 Balance: <b>${balance_usd:.2f}</b>\n🛍 Purchases: <b>{purchases}</b>\n\nChoose from the menu below:')
    rows = [[btn(tr(cid,'🛒 المنتجات','🛒 Products'),'products',ui_icon('ui_products'),style='primary'), btn(tr(cid,'💰 شحن الرصيد','💰 Top up'),'wallet:topup',ui_icon('ui_topup'),style='success')], [btn(tr(cid,'💎 الإحالات','💎 Referrals'),'referrals',ui_icon('ui_referrals')), btn(tr(cid,'👤 حسابي','👤 My account'),'wallet',ui_icon('ui_account'))], [btn(tr(cid,'💬 تواصل مع الدعم','💬 Contact support'),'support',ui_icon('ui_support'),style='danger'), btn(tr(cid,'⚠️ إبلاغ عن مشكلة','⚠️ Report issue'),'support',ui_icon('ui_report'))], [btn(tr(cid,'💱 العملة','💱 Currency'),'settings:currency',ui_icon('ui_currency')), btn('🌐 Language / اللغة','settings:lang',ui_icon('ui_language'))]]
    if cid == G.get('ADMIN_ID'):
        rows.append([btn('🧾 لوحة الطلبات', 'admin', ui_icon('ui_admin'), style='primary')])
    send(api, cid, text, kb(rows))

def products(api, cid):
    buttons = [btn(p['name'], 'product:' + pid, p.get('custom_emoji_id')) for pid, p in G['PRODUCTS'].items() if category_visible(pid)]
    with db() as conn:
        custom_categories = conn.execute('SELECT cid,name FROM admin_categories ORDER BY rowid').fetchall()
    buttons += [btn(category_name, 'product:' + category_id, ui_icon(category_id)) for category_id, category_name in custom_categories if category_visible(category_id)]
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
    if pid in G['PRODUCTS']:
        variants = [v['id'] for v in VARIANTS.values() if v['category'] == pid]
        return any(map(product_visible, variants)) if variants else product_visible(pid)
    with db() as conn:
        ids = [row[0] for row in conn.execute('SELECT pid FROM admin_products WHERE category_id=?', (pid,))]
    return any(map(product_visible, ids))


def visibility_categories(api, cid):
    if cid != G['ADMIN_ID']:
        return
    with db() as conn:
        custom = conn.execute('SELECT cid,name FROM admin_categories ORDER BY rowid').fetchall()
    categories = [(pid, p['name']) for pid, p in G['PRODUCTS'].items()] + custom
    rows = [[btn(name, 'viscat:' + pid)] for pid, name in categories]
    send(api, cid, '👁 <b>إظهار وإخفاء المنتجات</b>\n\nاختر القسم:', kb(rows + [[btn('↩️ لوحة الإدارة', 'admin')]]))


def visibility_products(api, cid, category_id):
    if cid != G['ADMIN_ID']:
        return
    if category_id in G['PRODUCTS']:
        ids = [v['id'] for v in VARIANTS.values() if v['category'] == category_id] or [category_id]
    elif custom_category(category_id):
        with db() as conn:
            ids = [r[0] for r in conn.execute('SELECT pid FROM admin_products WHERE category_id=? ORDER BY rowid', (category_id,))]
    else:
        return visibility_categories(api, cid)
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


def grok_cards(api, cid, choices):
    """Compact photo cards for Grok only; prices share the checkout source."""
    send(api, cid, tr(cid, '✦ <b>اشتراكات Grok</b>\nاختر الباقة المناسبة لك:', '✦ <b>Grok subscriptions</b>\nChoose your plan:'))
    for v in choices:
        pid = v['id']
        available = can_order(pid)
        if v.get('review_required'):
            status = tr(cid, '⏸ قيد المراجعة — الطلب غير متاح', '⏸ Under review — ordering unavailable')
        elif not in_stock(pid):
            status = tr(cid, '🔴 نفدت الكمية', '🔴 Out of stock')
        else:
            status = tr(cid, '✅ متوفر لدى المورد — يُؤكد قبل الطلب', '✅ Supplier stock — confirm before ordering')
        caption = '<b>' + esc(name(pid, cid)) + '</b>\n\n'
        caption += '💰 <b>' + price(cid, pid, 'SAR') + ' | ' + price(cid, pid, 'USD') + '</b>\n\n' + status
        if v.get('manual_delivery'):
            caption += '\n' + tr(cid, '✉️ تسليم يدوي — تواصل مع الدعم قبل الشراء', '✉️ Manual delivery — contact support before buying')
        details = btn(tr(cid, '📋 التفاصيل', '📋 Details'), 'item:' + pid)
        rows = [[btn(tr(cid, '🛒 شراء الآن', '🛒 Buy now'), 'buy:' + pid, style='primary'), details]] if available else [[details]]
        if not available:
            rows[0][0]['style'] = 'danger'
        rows.append([btn(tr(cid, '💬 الدعم', '💬 Support'), 'support')])
        markup = kb(rows)
        override = saved_product_photo(pid)
        if override is not None:
            if not override or not api.call('sendPhoto', chat_id=cid, photo=override, caption=caption, parse_mode='HTML', reply_markup=markup):
                send(api, cid, caption, markup)
            continue
        path = (BASE / (v.get('image') or 'assets/grok.png')).resolve()
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
            label = ('🔴 نفد | ' if sold_out else '') + name(product_id, cid) + ' | ' + price(cid, product_id)
            rows.append([btn(label, 'item:' + product_id, ui_icon(product_id), 'danger' if sold_out else None)])
        send(api, cid, '<b>' + esc(custom_cat[1]) + '</b>\n\n' + tr(cid, 'اختر المنتج:', 'Choose a product:'), kb(rows + [nav(cid)]))
        return
    choices = [v for v in VARIANTS.values() if v['category'] == pid and product_visible(v['id'])]
    if pid == 'grok' and choices:
        return grok_cards(api, cid, choices)
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
            rows.append([btn(label, 'item:' + v['id'], p.get('custom_emoji_id'), 'danger' if sold_out else None)])
        send(api, cid, '<b>' + esc(p['name']) + '</b>\n\n' + tr(cid, 'اختر المنتج:', 'Choose a product:'), kb(rows + [nav(cid)]))
        return
    english = {'youtube': 'YouTube Premium for one month. Ad-free viewing, background playback, offline downloads and YouTube Music Premium benefits.',
               'netflix': 'Netflix subscription for movies, series and entertainment.', 'iptv': 'IPTV subscriptions for compatible devices.'}
    description = product_description(pid, cid)
    text = name(pid, cid) + '\n\n' + price(cid, pid) + '\n\n' + tr(cid, '✅ متوفر' if in_stock(pid) else '🔴 نفدت الكمية', '✅ Available' if in_stock(pid) else '🔴 Out of stock') + '\n\n' + description
    rows = [[btn(tr(cid, '🛒 طلب المنتج', '🛒 Order'), 'buy:' + pid)]] if can_order(pid) else []
    rows += [[btn(tr(cid, '💬 الدعم', '💬 Support'), 'support')], nav(cid)]
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
        text = name(pid, cid) + '\n\n' + info_block(pid,cid) + '\n\n' + status + '\n\n' + product_description(pid, cid)
        rows = [[btn(tr(cid, '🛒 طلب المنتج', '🛒 Order'), 'buy:' + pid)]] if can_order(pid) else []
        rows += [[btn(tr(cid, '💬 الدعم', '💬 Support'), 'support')], nav(cid, 'product:' + category_id)]
        card(api, cid, None, name(pid, cid), text, kb(rows), pid=pid)
        return
    lang = prefs(cid)[0]
    available = ''
    if not in_stock(pid):
        available = tr(cid, '🚫 نفد لدى المورد وقت المراجعة. الطلب غير متاح حاليًا.', '🚫 Out of stock at the last supplier check. Ordering is currently unavailable.')
    text = name(pid, cid) + '\n\n' + info_block(pid,cid) + (('\n\n' + available) if available else '') + '\n\n' + product_description(pid, cid)
    if v.get('promotions'):
        text += '\n\n' + tr(cid, 'أسعار الكميات — تواصل مع الدعم:', 'Bulk prices — contact support:')
        for tier in v['promotions']:
            text += '\n' + str(tier['min_quantity']) + '+: ' + price(cid, pid, source_price=tier['source_usd']) + tr(cid, ' لكل قطعة', ' per unit')
    if v.get('review_required'):
        text += '\n\n⚠️ ' + v['review_required'][lang]
    rows = []
    if can_order(pid):
        order_label = tr(cid, '🛒 طلب قطعة واحدة', '🛒 Order one item')
        if v.get('category') == 'chatgpt':
            order_label = tr(cid, '🛒 شراء الآن • ', '🛒 Buy now • ') + price(cid, pid)
        rows.append([btn(order_label, 'buy:' + pid, style='primary' if v.get('category') == 'chatgpt' else None)])
    rows += [[btn(tr(cid, '💬 الدعم', '💬 Support'), 'support')], nav(cid, 'product:' + v['category'])]
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
    send(api, cid, text, kb([[btn('Crypto Pay', f'topupcrypto:{value}', ui_icon('pay_cryptopay'))],
                             [btn('Bybit / USDT', f'topupbybit:{value}', ui_icon('pay_bybit'))], nav(cid, 'wallet:topup')]))


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
         kb([[btn('Bybit Pay', 'topupsend:bybitid:' + topup_id, ui_icon('pay_bybitid'))],
             [btn('USDT • TRON (TRC20)', 'topupsend:trc20:' + topup_id, ui_icon('pay_trc20'))],
             [btn('USDT • BSC (BEP20)', 'topupsend:bep20:' + topup_id, ui_icon('pay_bep20'))], nav(cid, f'topup:{value}')]))


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
         kb([[btn(tr(cid, 'المحفظة', 'Wallet'), 'paywallet:' + pid, ui_icon('pay_wallet'))],
             [btn('Crypto Pay', 'paycrypto:' + pid, ui_icon('pay_cryptopay'))],
             [btn('USDT — Bybit', 'paybybit:' + pid, ui_icon('pay_bybit'))], nav(cid, back(pid))]))


def payment(api, cid, pid, method):
    if not can_order(pid):
        payments(api, cid, pid)
        return
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
    send(api, G['ADMIN_ID'], f'🛒 <b>طلب مدفوع من المحفظة #{order_id}</b>\n\n' + esc(name(pid, cid)) + f'\nالسعر المدفوع: {paid_usd} USD\nالعميل: <code>{cid}</code>')
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
        saved_order_id = add_order(cid, pid, 'cryptopay', 'paid', usd=paid_usd, sar=(Decimal(paid_usd)*RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
        send(api, G['ADMIN_ID'], f'💠 <b>طلب Crypto Pay مدفوع #{saved_order_id}</b>\n\n' + esc(name(pid, cid)) + f'\nالسعر المدفوع: {paid_usd} USD\nالعميل: <code>{cid}</code>')
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
    add_order(cid, pid, method, 'review', usd=usd, sar=sar)
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
        reset_navigation_state(cid)
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
    elif prefix == 'payreview' and cid == G['ADMIN_ID']:
        decision, _, oid = arg.partition(':')
        with db() as conn:
            row = conn.execute('SELECT cid,status FROM orders WHERE id=?', (oid,)).fetchone()
            if not row or row[1] != 'review':
                return send(api, cid, '⚠️ الطلب غير موجود أو تمت معالجته مسبقاً.')
            customer = row[0]
            if decision == 'accept':
                conn.execute('UPDATE orders SET status="paid" WHERE id=? AND status="review"', (oid,))
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
                      'broadcast_new_products': broadcast_new_products,
                      'chatgpt_visibility_admin': chatgpt_visibility_admin,
                      'toggle_chatgpt_visibility': toggle_chatgpt_visibility,
                      'handle_admin_product': handle_admin_product, 'handle_info_warranty': handle_info_warranty, 'handle_info_icon': handle_info_icon})
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
    if pid in VARIANTS:
        return text_override(pid, 'name', prefs(cid)[0], VARIANTS[pid]['name'][prefs(cid)[0]])
    cp = custom_product(pid)
    if cp:
        return text_override(pid, 'name', prefs(cid)[0], cp[1])
    return text_override(pid, 'name', prefs(cid)[0], G['PRODUCTS'].get(pid, {}).get('name', pid))


def start(api, cid):
    reset_navigation_state(cid)
    send(api, cid, tr(cid, '👋 <b>مرحباً بك في VEXA STORE</b>\n\nمتجر الخدمات والاشتراكات الرقمية.',
                     '👋 <b>Welcome to VEXA STORE</b>\n\nDigital services and subscriptions.'),
         kb([[btn('🚀 START | ابدأ', 'enter_store', ui_icon('ui_start'))], [btn('🌐 العربية / English', 'settings:lang', ui_icon('ui_language'))]]))


def home(api, cid):
    balance_sar = wallet_balance(cid)
    balance_usd = (balance_sar / RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    with db() as conn:
        purchases = conn.execute('SELECT COUNT(*) FROM orders WHERE cid=? AND status="paid"', (cid,)).fetchone()[0]
    text = tr(cid, f'👋 <b>أهلاً بك في VEXA STORE!</b>\n\n🆔 رقم العضوية: <code>{cid}</code>\n👤 حسابك: <a href="tg://user?id={cid}">فتح الحساب</a>\n💳 الرصيد: <b>${balance_usd:.2f}</b>\n🛍 المشتريات: <b>{purchases}</b>\n\nاختر من القائمة أدناه:', f'👋 <b>Welcome to VEXA STORE!</b>\n\n🆔 Member ID: <code>{cid}</code>\n👤 Account: <a href="tg://user?id={cid}">Open profile</a>\n💳 Balance: <b>${balance_usd:.2f}</b>\n🛍 Purchases: <b>{purchases}</b>\n\nChoose from the menu below:')
    rows = [[btn(tr(cid,'🛒 المنتجات','🛒 Products'),'products',ui_icon('ui_products'),style='primary'), btn(tr(cid,'💰 شحن الرصيد','💰 Top up'),'wallet:topup',ui_icon('ui_topup'),style='success')], [btn(tr(cid,'💎 الإحالات','💎 Referrals'),'referrals',ui_icon('ui_referrals')), btn(tr(cid,'👤 حسابي','👤 My account'),'wallet',ui_icon('ui_account'))], [btn(tr(cid,'💬 تواصل مع الدعم','💬 Contact support'),'support',ui_icon('ui_support'),style='danger'), btn(tr(cid,'⚠️ إبلاغ عن مشكلة','⚠️ Report issue'),'support',ui_icon('ui_report'))], [btn(tr(cid,'💱 العملة','💱 Currency'),'settings:currency',ui_icon('ui_currency')), btn('🌐 Language / اللغة','settings:lang',ui_icon('ui_language'))]]
    if cid == G.get('ADMIN_ID'):
        rows.append([btn('🧾 لوحة الطلبات', 'admin', ui_icon('ui_admin'), style='primary')])
    send(api, cid, text, kb(rows))

def products(api, cid):
    buttons = [btn(p['name'], 'product:' + pid, p.get('custom_emoji_id')) for pid, p in G['PRODUCTS'].items() if category_visible(pid)]
    with db() as conn:
        custom_categories = conn.execute('SELECT cid,name FROM admin_categories ORDER BY rowid').fetchall()
    buttons += [btn(category_name, 'product:' + category_id, ui_icon(category_id)) for category_id, category_name in custom_categories if category_visible(category_id)]
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
    # Split plain content before HTML escaping; 1700 characters <= 3400 UTF-16 units.
    chunks = [text[i:i+1700] for i in range(0, len(text), 1700)] or ['']
    for i, chunk in enumerate(chunks):
        send(api, cid, esc(chunk), keyboard if i == len(chunks)-1 else None)



def grok_cards(api, cid, choices):
    """Compact photo cards for Grok only; prices share the checkout source."""
    send(api, cid, tr(cid, '✦ <b>اشتراكات Grok</b>\nاختر الباقة المناسبة لك:', '✦ <b>Grok subscriptions</b>\nChoose your plan:'))
    for v in choices:
        pid = v['id']
        available = can_order(pid)
        if v.get('review_required'):
            status = tr(cid, '⏸ قيد المراجعة — الطلب غير متاح', '⏸ Under review — ordering unavailable')
        elif not in_stock(pid):
            status = tr(cid, '🔴 نفدت الكمية', '🔴 Out of stock')
        else:
            status = tr(cid, '✅ متوفر لدى المورد — يُؤكد قبل الطلب', '✅ Supplier stock — confirm before ordering')
        caption = '<b>' + esc(name(pid, cid)) + '</b>\n\n'
        caption += '💰 <b>' + price(cid, pid, 'SAR') + ' | ' + price(cid, pid, 'USD') + '</b>\n\n' + status
        if v.get('manual_delivery'):
            caption += '\n' + tr(cid, '✉️ تسليم يدوي — تواصل مع الدعم قبل الشراء', '✉️ Manual delivery — contact support before buying')
        details = btn(tr(cid, '📋 التفاصيل', '📋 Details'), 'item:' + pid)
        rows = [[btn(tr(cid, '🛒 شراء الآن', '🛒 Buy now'), 'buy:' + pid, style='primary'), details]] if available else [[details]]
        if not available:
            rows[0][0]['style'] = 'danger'
        rows.append([btn(tr(cid, '💬 الدعم', '💬 Support'), 'support')])
        markup = kb(rows)
        override = saved_product_photo(pid)
        if override is not None:
            if not override or not api.call('sendPhoto', chat_id=cid, photo=override, caption=caption, parse_mode='HTML', reply_markup=markup):
                send(api, cid, caption, markup)
            continue
        path = (BASE / (v.get('image') or 'assets/grok.png')).resolve()
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
            label = ('🔴 نفد | ' if sold_out else '') + name(product_id, cid) + ' | ' + price(cid, product_id)
            rows.append([btn(label, 'item:' + product_id, ui_icon(product_id), 'danger' if sold_out else None)])
        send(api, cid, '<b>' + esc(custom_cat[1]) + '</b>\n\n' + tr(cid, 'اختر المنتج:', 'Choose a product:'), kb(rows + [nav(cid)]))
        return
    choices = [v for v in VARIANTS.values() if v['category'] == pid and product_visible(v['id'])]
    if pid == 'grok' and choices:
        return grok_cards(api, cid, choices)
    if choices:
        rows = []
        for v in choices:
            sold_out = not in_stock(v['id'])
            status = '⏸ ' if v.get('review_required') else (tr(cid, '🔴 نفد | ', '🔴 SOLD OUT | ') if sold_out else '')
            rows.append([btn(status + name(v['id'], cid) + ' | ' + price(cid, v['id']),
                             'item:' + v['id'], p.get('custom_emoji_id'), 'danger' if sold_out else None)])
        send(api, cid, '<b>' + esc(p['name']) + '</b>\n\n' + tr(cid, 'اختر المنتج:', 'Choose a product:'), kb(rows + [nav(cid)]))
        return
    english = {'youtube': 'YouTube Premium for one month. Ad-free viewing, background playback, offline downloads and YouTube Music Premium benefits.',
               'netflix': 'Netflix subscription for movies, series and entertainment.', 'iptv': 'IPTV subscriptions for compatible devices.'}
    description = product_description(pid, cid)
    text = name(pid, cid) + '\n\n' + price(cid, pid) + '\n\n' + tr(cid, '✅ متوفر' if in_stock(pid) else '🔴 نفدت الكمية', '✅ Available' if in_stock(pid) else '🔴 Out of stock') + '\n\n' + description
    rows = [[btn(tr(cid, '🛒 طلب المنتج', '🛒 Order'), 'buy:' + pid)]] if can_order(pid) else []
    rows += [[btn(tr(cid, '💬 الدعم', '💬 Support'), 'support')], nav(cid)]
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
        text = name(pid, cid) + '\n\n' + info_block(pid,cid) + '\n\n' + status + '\n\n' + product_description(pid, cid)
        rows = [[btn(tr(cid, '🛒 طلب المنتج', '🛒 Order'), 'buy:' + pid)]] if can_order(pid) else []
        rows += [[btn(tr(cid, '💬 الدعم', '💬 Support'), 'support')], nav(cid, 'product:' + category_id)]
        card(api, cid, None, name(pid, cid), text, kb(rows), pid=pid)
        return
    lang = prefs(cid)[0]
    available = ''
    if not in_stock(pid):
        available = tr(cid, '🚫 نفد لدى المورد وقت المراجعة. الطلب غير متاح حاليًا.', '🚫 Out of stock at the last supplier check. Ordering is currently unavailable.')
    text = name(pid, cid) + '\n\n' + info_block(pid,cid) + (('\n\n' + available) if available else '') + '\n\n' + product_description(pid, cid)
    if v.get('promotions'):
        text += '\n\n' + tr(cid, 'أسعار الكميات — تواصل مع الدعم:', 'Bulk prices — contact support:')
        for tier in v['promotions']:
            text += '\n' + str(tier['min_quantity']) + '+: ' + price(cid, pid, source_price=tier['source_usd']) + tr(cid, ' لكل قطعة', ' per unit')
    if v.get('review_required'):
        text += '\n\n⚠️ ' + v['review_required'][lang]
    rows = []
    if can_order(pid):
        rows.append([btn(tr(cid, '🛒 طلب قطعة واحدة', '🛒 Order one item'), 'buy:' + pid)])
    rows += [[btn(tr(cid, '💬 الدعم', '💬 Support'), 'support')], nav(cid, 'product:' + v['category'])]
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
    send(api, cid, text, kb([[btn('Crypto Pay', f'topupcrypto:{value}', ui_icon('pay_cryptopay'))],
                             [btn('Bybit / USDT', f'topupbybit:{value}', ui_icon('pay_bybit'))], nav(cid, 'wallet:topup')]))


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
         kb([[btn('Bybit Pay', 'topupsend:bybitid:' + topup_id, ui_icon('pay_bybitid'))],
             [btn('USDT • TRON (TRC20)', 'topupsend:trc20:' + topup_id, ui_icon('pay_trc20'))],
             [btn('USDT • BSC (BEP20)', 'topupsend:bep20:' + topup_id, ui_icon('pay_bep20'))], nav(cid, f'topup:{value}')]))


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
         kb([[btn(tr(cid, 'المحفظة', 'Wallet'), 'paywallet:' + pid, ui_icon('pay_wallet'))],
             [btn('Crypto Pay', 'paycrypto:' + pid, ui_icon('pay_cryptopay'))],
             [btn('USDT — Bybit', 'paybybit:' + pid, ui_icon('pay_bybit'))], nav(cid, back(pid))]))


def payment(api, cid, pid, method):
    if not can_order(pid):
        payments(api, cid, pid)
        return
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
    send(api, G['ADMIN_ID'], f'🛒 <b>طلب مدفوع من المحفظة #{order_id}</b>\n\n' + esc(name(pid, cid)) + f'\nالسعر المدفوع: {paid_usd} USD\nالعميل: <code>{cid}</code>')
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
        saved_order_id = add_order(cid, pid, 'cryptopay', 'paid', usd=paid_usd, sar=(Decimal(paid_usd)*RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
        send(api, G['ADMIN_ID'], f'💠 <b>طلب Crypto Pay مدفوع #{saved_order_id}</b>\n\n' + esc(name(pid, cid)) + f'\nالسعر المدفوع: {paid_usd} USD\nالعميل: <code>{cid}</code>')
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
    add_order(cid, pid, method, 'review', usd=usd, sar=sar)
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
        reset_navigation_state(cid)
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
    elif prefix == 'payreview' and cid == G['ADMIN_ID']:
        decision, _, oid = arg.partition(':')
        with db() as conn:
            row = conn.execute('SELECT cid,status FROM orders WHERE id=?', (oid,)).fetchone()
            if not row or row[1] != 'review':
                return send(api, cid, '⚠️ الطلب غير موجود أو تمت معالجته مسبقاً.')
            customer = row[0]
            if decision == 'accept':
                conn.execute('UPDATE orders SET status="paid" WHERE id=? AND status="review"', (oid,))
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
            send(api,cid,'أرسل الآن الأيقونة المتحركة المخصصة لهذا الحقل.',kb([[btn('❌ إلغاء','infopick:'+pid)]]))
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
                      'broadcast_new_products': broadcast_new_products,
                      'handle_admin_product': handle_admin_product, 'handle_info_warranty': handle_info_warranty, 'handle_info_icon': handle_info_icon})
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
