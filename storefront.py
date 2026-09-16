"""Bilingual catalogue extension; preserves the existing bot/admin entry points."""
import html
import json
import os
import sqlite3
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
    return conn


def prefs(cid):
    with db() as conn:
        row = conn.execute('SELECT lang,currency FROM preferences WHERE cid=?', (cid,)).fetchone()
    return row or ('ar', 'SAR')


def tr(cid, ar, en):
    return en if prefs(cid)[0] == 'en' else ar


def esc(value):
    return html.escape(str(value))


def btn(text, action, icon=None):
    result = {'text': text, 'callback_data': action}
    if icon and G['CONFIG'].get('custom_icons_enabled'):
        result['icon_custom_emoji_id'] = str(icon)
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
    return {'keyboard': [[{'text': tr(cid, '🚀 ابدأ', '🚀 Start')}, {'text': tr(cid, '🛍 المنتجات', '🛍 Products')}, {'text': tr(cid, '💬 الدعم', '💬 Support')}],
                         [{'text': tr(cid, '👛 المحفظة', '👛 Wallet')}, {'text': '🔗 API'}, {'text': tr(cid, '🛡 الضمان', '🛡 Warranty')}],
                         [{'text': '🌐 اللغة / Language'}, {'text': '💱 العملة / Currency'}]],
            'resize_keyboard': True, 'is_persistent': True,
            'input_field_placeholder': tr(cid, 'اختر من القائمة', 'Choose from the menu')}


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
    send(api, cid, tr(cid, '👋 أهلاً بك في <b>VEXA STORE</b>\n\nاختر القسم المطلوب.\n\n',
                     '👋 Welcome to <b>VEXA STORE</b>\n\nChoose a category.\n\n') +
         '<tg-emoji emoji-id="5440411975509096877">💳</tg-emoji> ' +
         tr(cid, 'لشحن النقاط والدعم: ', 'Top-ups and support: ') + SUPPORT, menu(cid))
    products(api, cid)


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
                req = urllib.request.Request(api.base_url + 'sendPhoto', body, {'Content-Type': f'multipart/form-data; boundary={boundary}'})
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
            status = '⏸ ' if v.get('review_required') else ('🚫 ' if v['source_stock'] == 0 else '')
            rows.append([btn(status + name(v['id'], cid) + ' | ' + price(cid, v['id']), 'item:' + v['id'], p.get('custom_emoji_id'))])
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


def payments(api, cid, pid):
    if not can_order(pid):
        send(api, cid, tr(cid, 'الطلب غير متاح لهذا الخيار حاليًا. تواصل مع الدعم: ', 'Ordering is unavailable for this option. Contact support: ') + SUPPORT, kb([nav(cid, back(pid))]))
        return
    warning = tr(cid, 'التنفيذ بعد مراجعة الدفع وتأكيد التوفر. تواصل مع الدعم قبل التحويل.', 'Fulfilment follows payment review and availability confirmation. Contact support before transferring.')
    send(api, cid, tr(cid, '💳 <b>اختر طريقة الدفع</b>\n\n', '💳 <b>Choose payment method</b>\n\n') + summary(cid, pid) + '\n\n' + warning,
         kb([[btn(tr(cid, 'تحويل بنكي — الراجحي', 'Bank transfer — Al Rajhi'), 'paybank:' + pid, '5452024291472196683')],
             [btn('USDT — Bybit', 'paybybit:' + pid, '5472387796574418157')], nav(cid, back(pid))]))


def payment(api, cid, pid, method):
    if not can_order(pid):
        payments(api, cid, pid)
        return
    if method == 'bybit':
        send(api, cid, '🪙 <b>USDT — Bybit</b>\n\n' + summary(cid, pid),
             kb([[btn('Bybit Pay', 'bybitid:' + pid)], [btn('USDT • TRON (TRC20)', 'trc20:' + pid)], [btn('USDT • BSC (BEP20)', 'bep20:' + pid)], nav(cid, 'buy:' + pid)]))
        return
    if method == 'bank':
        keys = [('PAYMENT_BANK_HOLDER', tr(cid, 'اسم الحساب', 'Account holder')),
                ('PAYMENT_BANK_ACCOUNT', tr(cid, 'رقم الحساب', 'Account number')),
                ('PAYMENT_BANK_IBAN', 'IBAN')]
        title = tr(cid, '🏦 مصرف الراجحي', '🏦 Al Rajhi Bank')
        text = title + '\n\n' + summary(cid, pid) + '\n' + tr(cid, 'مبلغ التحويل: ', 'Transfer amount: ') + price(cid, pid, 'SAR')
        for key, label in keys:
            text += '\n\n' + label + ':\n<code>' + esc(os.getenv(key, tr(cid, 'غير مضاف بعد', 'Not configured'))) + '</code>'
        configured = all(os.getenv(key) for key, _ in keys)
    else:
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


def receipt_request(api, cid, pid, method):
    if not can_order(pid) or method not in ('bank', 'bybitid', 'trc20', 'bep20'):
        payments(api, cid, pid)
        return
    with db() as conn:
        conn.execute('INSERT OR REPLACE INTO receipts VALUES (?,?,?,?,?)', (cid, pid, method, str(amount(pid, 'USD')), str(amount(pid, 'SAR'))))
    send(api, cid, tr(cid, '📸 أرسل صورة إثبات الدفع هنا. ستصل للإدارة للمراجعة.', '📸 Send your payment receipt photo here. It will be sent to the administrator for review.'), kb([[btn(tr(cid, '❌ إلغاء', '❌ Cancel'), 'cancel:' + pid)]]))


def receipt(api, message):
    cid = message['chat']['id']
    if message.get('text', '').startswith('/') or message.get('text') in G['MENU_ACTIONS']:
        with db() as conn:
            conn.execute('DELETE FROM receipts WHERE cid=?', (cid,))
        return False
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
    with db() as conn:
        conn.execute('DELETE FROM receipts WHERE cid=?', (cid,))
    send(api, cid, tr(cid, '✅ وصل الإثبات للإدارة للمراجعة. ستتم متابعة طلبك بعد التحقق.', '✅ Receipt sent for review. Your order will be followed up after verification.'), menu(cid))
    return True


def action(api, cid, value):
    prefix, _, arg = value.partition(':')
    arg = LEGACY.get(arg, arg)
    if prefix in ('home', 'enter_store'):
        home(api, cid)
    elif prefix == 'start':
        start(api, cid)
    elif prefix == 'products':
        products(api, cid)
    elif prefix == 'product':
        category(api, cid, arg)
    elif prefix in ('item', 'claude'):
        item(api, cid, arg)
    elif value in LEGACY:
        item(api, cid, LEGACY[value])
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
    elif prefix in ('paybank', 'paybybit', 'bybitid', 'trc20', 'bep20'):
        payment(api, cid, arg, {'paybank': 'bank', 'paybybit': 'bybit'}.get(prefix, prefix))
    elif prefix == 'receipt':
        method, _, pid = arg.partition(':')
        receipt_request(api, cid, LEGACY.get(pid, pid), method)
    elif prefix == 'support':
        send(api, cid, tr(cid, '💬 لشحن النقاط والدعم: ', '💬 Top-ups and support: ') + SUPPORT, menu(cid))
    elif prefix == 'wallet':
        send(api, cid, tr(cid, '👛 المحفظة غير مفعّلة حاليًا.', '👛 The wallet is not active yet.'), menu(cid))
    elif prefix == 'api':
        send(api, cid, tr(cid, '🔗 إعدادات API المتجر غير مفعّلة حاليًا.', '🔗 Store API settings are not active yet.'), menu(cid))
    elif prefix == 'warranty':
        send(api, cid, tr(cid, '🛡 تختلف شروط الضمان حسب المنتج. راجع وصفه قبل الطلب. للاستفسار: ', '🛡 Warranty terms vary by product. Read its description before ordering. Questions: ') + SUPPORT, menu(cid))
    else:
        products(api, cid)


def install(namespace):
    global G
    G = namespace
    namespace.update({'show_start': start, 'show_home': home, 'show_products': products,
                      'show_product': category, 'show_claude_product': item, 'handle_action': action,
                      'handle_receipt': receipt, 'order_name': name, 'home_keyboard': menu})
    namespace['MENU_ACTIONS'].update({'🚀 Start': 'start', '🛍 Products': 'products', '💬 Support': 'support',
                                     '👛 Wallet': 'wallet', '🛡 Warranty': 'warranty',
                                     '🌐 اللغة / Language': 'settings:lang', '💱 العملة / Currency': 'settings:currency'})
