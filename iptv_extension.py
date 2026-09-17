"""IPTV catalogue additions and activation-menu icon management."""
from decimal import Decimal, ROUND_HALF_UP
import storefront as s

IPTV_IDS = ('iptv_1m', 'iptv_3m', 'iptv_6m', 'iptv_1y')
DEVICE_LABELS = {
    'tv': ('شاشة', 'TV'),
    'ios': ('آيفون / آيباد', 'iPhone / iPad'),
    'computer': ('كمبيوتر / لابتوب', 'Computer / Laptop'),
    'android': ('أندرويد', 'Android'),
}
DEVICE_ICON_KEYS = {key: 'iptvact_' + key for key in DEVICE_LABELS}

IPTV_VARIANTS = [
    {'id':'iptv_1m','category':'iptv','name':{'ar':'IPTV — شهر','en':'IPTV — 1 Month'},'description':{'ar':'اشتراك IPTV لمدة شهر.','en':'IPTV subscription for 1 month.'},'fixed_sar':'5','source_usd':'0','source_stock':5,'source_checked_at':'2026-09-18','promotions':[],'image':None},
    {'id':'iptv_3m','category':'iptv','name':{'ar':'IPTV — 3 أشهر','en':'IPTV — 3 Months'},'description':{'ar':'اشتراك IPTV لمدة 3 أشهر.','en':'IPTV subscription for 3 months.'},'fixed_sar':'10','source_usd':'0','source_stock':3,'source_checked_at':'2026-09-18','promotions':[],'image':None},
    {'id':'iptv_6m','category':'iptv','name':{'ar':'IPTV — 6 أشهر','en':'IPTV — 6 Months'},'description':{'ar':'اشتراك IPTV لمدة 6 أشهر.','en':'IPTV subscription for 6 months.'},'fixed_sar':'15','source_usd':'0','source_stock':5,'source_checked_at':'2026-09-18','promotions':[],'image':None},
    {'id':'iptv_1y','category':'iptv','name':{'ar':'IPTV — سنة','en':'IPTV — 1 Year'},'description':{'ar':'اشتراك IPTV لمدة سنة.','en':'IPTV subscription for 1 year.'},'fixed_sar':'25','source_usd':'0','source_stock':2,'source_checked_at':'2026-09-18','promotions':[],'image':None},
]
for variant in IPTV_VARIANTS:
    s.VARIANTS[variant['id']] = variant

_original_amount = s.amount
_original_category = s.category
_original_action = s.action
_original_broadcast = s.broadcast_new_products
_original_admin_icons = s.admin_icons
_original_begin_icon_setup = s.begin_icon_setup
_original_handle_admin_icon = s.handle_admin_icon


def saved_icon(key):
    with s.db() as conn:
        row = conn.execute('SELECT custom_emoji_id FROM category_icons WHERE pid=?', (key,)).fetchone()
    return row[0] if row else None


def amount(pid, currency='SAR', source_price=None):
    pid = s.LEGACY.get(pid, pid)
    variant = s.VARIANTS.get(pid)
    if variant and variant.get('fixed_sar') is not None:
        sar = Decimal(str(variant['fixed_sar']))
        value = sar if currency == 'SAR' else sar / s.RATE
        return value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return _original_amount(pid, currency, source_price)


def category(api, cid, pid):
    if pid != 'iptv':
        return _original_category(api, cid, pid)
    product = s.G['PRODUCTS'].get('iptv')
    if not product:
        return s.products(api, cid)
    rows = []
    for variant_id in IPTV_IDS:
        variant = s.VARIANTS[variant_id]
        sold_out = variant['source_stock'] == 0
        status = s.tr(cid, '🔴 نفد | ', '🔴 SOLD OUT | ') if sold_out else ''
        rows.append([s.btn(status + s.name(variant_id, cid) + ' | ' + s.price(cid, variant_id),
                           'item:' + variant_id, product.get('custom_emoji_id'),
                           'danger' if sold_out else None)])
    rows.append([s.btn(s.tr(cid, '🛠 طرق التفعيل', '🛠 Activation Methods'), 'iptvactivation')])
    rows.append(s.nav(cid))
    s.send(api, cid, '<b>IPTV</b>\n\n' + s.tr(cid, 'اختر المنتج:', 'Choose a product:'), s.kb(rows))


def activation_menu(api, cid):
    def device_btn(key):
        ar, en = DEVICE_LABELS[key]
        return s.btn(s.tr(cid, ar, en), 'iptvact:' + key, saved_icon(DEVICE_ICON_KEYS[key]))
    rows = [
        [device_btn('tv'), device_btn('ios')],
        [device_btn('computer'), device_btn('android')],
        [s.btn(s.tr(cid, '↩️ رجوع', '↩️ Back'), 'product:iptv')]
    ]
    return s.send(api, cid,
                  s.tr(cid, '📺 <b>طريقة التشغيل</b>\n\n👇 اختر نوع جهازك لعرض الشرح:',
                       '📺 <b>Activation Method</b>\n\n👇 Choose your device to view the instructions:'),
                  s.kb(rows))


def iptv_icon_menu(api, cid):
    if cid != s.G['ADMIN_ID']:
        return s.home(api, cid)
    rows = []
    for key in ('tv', 'ios', 'computer', 'android'):
        ar, en = DEVICE_LABELS[key]
        icon = saved_icon(DEVICE_ICON_KEYS[key])
        mark = '✅ ' if icon else ''
        rows.append([s.btn(mark + s.tr(cid, ar, en), 'seticon:' + DEVICE_ICON_KEYS[key], icon)])
    rows.append([s.btn('↩️ رجوع للأقسام', 'admin:icons')])
    return s.send(api, cid, '📺 <b>أيقونات طرق تفعيل IPTV</b>\n\nاختر الزر ثم أرسل الأيقونة المتحركة.', s.kb(rows))


def admin_icons(api, cid):
    if cid != s.G['ADMIN_ID']:
        return s.home(api, cid)
    buttons = []
    for pid, product in s.G['PRODUCTS'].items():
        mark = '✅ ' if product.get('custom_emoji_id') else ''
        callback = 'admin:iptvicons' if pid == 'iptv' else 'seticon:' + pid
        buttons.append(s.btn(mark + product['name'], callback, product.get('custom_emoji_id')))
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    s.send(api, cid, '➕ <b>إضافة أيقونة متحركة</b>\n\nاختر القسم. عند اختيار IPTV ستظهر أزرار طرق التفعيل داخله.',
           s.kb(rows + [[s.btn('↩️ لوحة الإدارة', 'admin')]]))


def begin_icon_setup(api, cid, pid):
    if pid not in DEVICE_ICON_KEYS.values():
        return _original_begin_icon_setup(api, cid, pid)
    if cid != s.G['ADMIN_ID']:
        return s.home(api, cid)
    device = next(key for key, value in DEVICE_ICON_KEYS.items() if value == pid)
    ar, en = DEVICE_LABELS[device]
    with s.db() as conn:
        conn.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)', (cid, 'iptv_icon', pid))
    s.send(api, cid, s.tr(cid, f'أرسل الآن الأيقونة المتحركة لزر <b>{s.esc(ar)}</b>.',
                          f'Send the animated icon for <b>{s.esc(en)}</b>.'),
           s.kb([[s.btn('❌ إلغاء', 'cancelicon')]]))


def handle_admin_icon(api, message):
    cid = message.get('chat', {}).get('id')
    if cid != s.G.get('ADMIN_ID'):
        return False
    with s.db() as conn:
        state = conn.execute('SELECT action,value FROM admin_state WHERE cid=?', (cid,)).fetchone()
    if not state or state[0] != 'iptv_icon':
        return _original_handle_admin_icon(api, message)
    entities = list(message.get('entities', [])) + list(message.get('caption_entities', []))
    emoji = next((e.get('custom_emoji_id') for e in entities if e.get('type') == 'custom_emoji' and e.get('custom_emoji_id')), None)
    if not emoji:
        s.send(api, cid, 'لم أجد أيقونة مخصصة. أرسل الأيقونة المتحركة نفسها.', s.kb([[s.btn('❌ إلغاء', 'cancelicon')]]))
        return True
    key = state[1]
    with s.db() as conn:
        conn.execute('INSERT OR REPLACE INTO category_icons VALUES (?,?)', (key, str(emoji)))
        conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
    s.G['CONFIG']['custom_icons_enabled'] = True
    s.send(api, cid, '✅ تم حفظ الأيقونة المتحركة.', s.kb([[s.btn('📺 أيقونة أخرى', 'admin:iptvicons')], [s.btn('👁 معاينة طرق التفعيل', 'iptvactivation')]]))
    return True


def action(api, cid, value):
    if value == 'iptvactivation':
        return activation_menu(api, cid)
    if value == 'admin:iptvicons':
        return iptv_icon_menu(api, cid)
    if value.startswith('iptvact:'):
        device = value.split(':', 1)[1]
        ar, en = DEVICE_LABELS.get(device, ('طريقة التفعيل', 'Activation Method'))
        if device == 'android':
            text = s.tr(cid,
                '<b>▶️ أجهزة أندرويد — تطبيق Next+</b>\n\n'
                'يعمل بطريقة سهلة باستخدام رقم الخادم.\n\n'
                '<b>طريقة الإعداد:</b>\n'
                '1️⃣ حمّل تطبيق <b>Next+</b> على جهاز الأندرويد.\n'
                '2️⃣ افتح التطبيق.\n'
                '3️⃣ أدخل بيانات الاشتراك.\n'
                '4️⃣ أدخل رقم الخادم: <code>55555</code>\n\n'
                '• اسم المستخدم يتم تسليمه بعد الدفع.\n'
                '• كلمة المرور يتم تسليمها بعد الدفع.\n\n'
                '✅ بعد إدخال البيانات ورقم الخادم راح يشتغل الاشتراك بإذن الله.',
                '<b>▶️ Android Devices — Next+</b>\n\n'
                'Install Next+, open the app, enter your subscription details, then enter Server ID <code>55555</code>.\n'
                'The username and password are provided after payment.')
            return s.send(api, cid, text,
                          s.kb([[s.btn(s.tr(cid, '↩️ رجوع لطرق التفعيل', '↩️ Back to Activation Methods'), 'iptvactivation')]]))
        if device == 'tv':
            text = s.tr(cid,
                '<b>📺 شاشات Samsung و LG — تطبيق 0Player</b>\n\n'
                'يعمل بطريقة سهلة وقريبة من نظام آبل، ويعتمد على رقم الخادم (Server ID).\n\n'
                '<b>طريقة الإعداد:</b>\n'
                '1️⃣ حمّل تطبيق <b>0Player</b> من المتجر الرسمي في الشاشة.\n'
                '2️⃣ افتح التطبيق واختر: <b>إضافة قائمة بالرمز</b>.\n'
                '3️⃣ اختر: <b>قوائم التشغيل عبر QR رمز</b>.\n'
                '4️⃣ ثم اختر: <b>Portal Code</b>.\n'
                '5️⃣ عبّئ البيانات كالتالي:\n'
                '• الخانة الأولى: أي اسم تريده.\n'
                '• الخانة الثانية (الخادم): <code>92929480</code>\n'
                '• الخانة الثالثة: اسم المستخدم.\n'
                '• الخانة الرابعة: كلمة المرور.\n\n'
                '✅ وبعدها راح يشتغل الاشتراك مباشرة.\n\n'
                '<b>📌 ملاحظة مهمة:</b>\n'
                '• يدعم شاشات LG من موديل 2016 إلى 2026.\n'
                '• يدعم شاشات Samsung من موديل 2022 إلى 2026.\n\n'
                'وشاشات أندرويد (متجر أندرويد): يتم تحميل تطبيق Downloader، وبيتم إرسال كود تحميل التطبيق بعد الدفع.\n\n'
                'أي شخص يحتاج مساعدة في الإعداد، يمكنكم التواصل معنا مباشرة.',
                '<b>📺 Samsung & LG TVs — 0Player</b>\n\n'
                'Install 0Player from the TV official store, choose Add List by Code, QR playlists, then Portal Code.\n'
                'Enter any name, Server ID <code>92929480</code>, username, and password.\n\n'
                'LG: models 2016–2026. Samsung: models 2022–2026.\n'
                'For Android TVs, install Downloader; the download code is provided after payment.')
            return s.send(api, cid, text,
                          s.kb([[s.btn(s.tr(cid, '↩️ رجوع لطرق التفعيل', '↩️ Back to Activation Methods'), 'iptvactivation')]]))
        if device == 'computer':
            text = s.tr(cid,
                '<b>💻 لمستخدمي نظام ويندوز 🎉</b>\n\n'
                'بإمكانكم استخدامه الآن على أجهزة الكمبيوتر واللابتوب.\n\n'
                '📥 حمّل تطبيق <b>Next+</b> للويندوز من الزر بالأسفل.\n\n'
                '🔢 <b>رقم الخادم / السيرفر:</b> <code>55555</code>\n\n'
                'كل اللي عليك تحميل التطبيق، ثم إدخال بيانات الاشتراك ورقم الخادم، وبإذن الله يشتغل معك بشكل طبيعي. 🌹',
                '<b>💻 Windows Users 🎉</b>\n\n'
                'You can use the service on Windows computers and laptops.\n\n'
                '📥 Download <b>Next+</b> for Windows using the button below.\n\n'
                '🔢 <b>Server number:</b> <code>55555</code>\n\n'
                'Download the app, then enter your subscription details and the server number.')
            return s.send(api, cid, text, s.kb([
                [{'text': s.tr(cid, '📥 تحميل Next+ للويندوز', '📥 Download Next+ for Windows'),
                  'url': 'https://apks.splayer.in/windows/next.exe'}],
                [s.btn(s.tr(cid, '↩️ رجوع لطرق التفعيل', '↩️ Back to Activation Methods'), 'iptvactivation')]
            ]))
        if device == 'ios':
            text = s.tr(cid,
                '<b>عبر أجهزة Apple — آيفون / آيباد</b>\n\n'
                '1- حمّل تطبيق Var Player من App Store من الزر بالأسفل.\n'
                '2- اختر <b>كود ✏️</b>.\n'
                '3- اكتب الرقم: <code>55555</code>\n\n'
                '<b>بعد الدخول:</b>\n'
                '1- ضع اسمك.\n'
                '2- أدخل اليوزر.\n'
                '3- أدخل الباسوورد.\n\n'
                'اليوزر والباسوورد يتم تسليمك البيانات بعد الدفع.',
                '<b>Apple Devices — iPhone / iPad</b>\n\n'
                '1- Download Var Player from the App Store using the button below.\n'
                '2- Choose <b>Code ✏️</b>.\n'
                '3- Enter: <code>55555</code>\n\n'
                '<b>After entering:</b>\n'
                '1- Enter your name.\n'
                '2- Enter the username.\n'
                '3- Enter the password.\n\n'
                'The username and password are provided after payment.')
            return s.send(api, cid, text, s.kb([
                [{'text': s.tr(cid, '📲 تحميل Var Player', '📲 Download Var Player'),
                  'url': 'https://apps.apple.com/tr/app/var-player-unlock-your-world/'}],
                [s.btn(s.tr(cid, '↩️ رجوع لطرق التفعيل', '↩️ Back to Activation Methods'), 'iptvactivation')]
            ]))
        return s.send(api, cid,
                      s.tr(cid, f'<b>{ar}</b>\n\nسيتم إضافة شرح التفعيل هنا.',
                           f'<b>{en}</b>\n\nActivation instructions will be added here.'),
                      s.kb([[s.btn(s.tr(cid, '↩️ رجوع لطرق التفعيل', '↩️ Back to Activation Methods'), 'iptvactivation')]]))
    return _original_action(api, cid, value)


def broadcast_new_products(api):
    with s.db() as conn:
        conn.executemany('INSERT OR IGNORE INTO announcements(pid,announced_at) VALUES (?,?)',
                         [(pid, s.now_saudi()) for pid in IPTV_IDS])
    return _original_broadcast(api)


s.amount = amount
s.category = category
s.action = action
s.admin_icons = admin_icons
s.begin_icon_setup = begin_icon_setup
s.handle_admin_icon = handle_admin_icon
s.broadcast_new_products = broadcast_new_products
