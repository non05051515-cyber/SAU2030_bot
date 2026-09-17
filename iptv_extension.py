"""IPTV catalogue additions only."""
from decimal import Decimal, ROUND_HALF_UP
import storefront as s

IPTV_IDS = ('iptv_1m', 'iptv_3m', 'iptv_6m', 'iptv_1y')

IPTV_VARIANTS = [
    {
        'id': 'iptv_1m', 'category': 'iptv',
        'name': {'ar': 'IPTV — شهر', 'en': 'IPTV — 1 Month'},
        'description': {'ar': 'اشتراك IPTV لمدة شهر.', 'en': 'IPTV subscription for 1 month.'},
        'fixed_sar': '5', 'source_usd': '0', 'source_stock': 5,
        'source_checked_at': '2026-09-18', 'promotions': [], 'image': None,
    },
    {
        'id': 'iptv_3m', 'category': 'iptv',
        'name': {'ar': 'IPTV — 3 أشهر', 'en': 'IPTV — 3 Months'},
        'description': {'ar': 'اشتراك IPTV لمدة 3 أشهر.', 'en': 'IPTV subscription for 3 months.'},
        'fixed_sar': '10', 'source_usd': '0', 'source_stock': 3,
        'source_checked_at': '2026-09-18', 'promotions': [], 'image': None,
    },
    {
        'id': 'iptv_6m', 'category': 'iptv',
        'name': {'ar': 'IPTV — 6 أشهر', 'en': 'IPTV — 6 Months'},
        'description': {'ar': 'اشتراك IPTV لمدة 6 أشهر.', 'en': 'IPTV subscription for 6 months.'},
        'fixed_sar': '15', 'source_usd': '0', 'source_stock': 5,
        'source_checked_at': '2026-09-18', 'promotions': [], 'image': None,
    },
    {
        'id': 'iptv_1y', 'category': 'iptv',
        'name': {'ar': 'IPTV — سنة', 'en': 'IPTV — 1 Year'},
        'description': {'ar': 'اشتراك IPTV لمدة سنة.', 'en': 'IPTV subscription for 1 year.'},
        'fixed_sar': '25', 'source_usd': '0', 'source_stock': 2,
        'source_checked_at': '2026-09-18', 'promotions': [], 'image': None,
    },
]

for variant in IPTV_VARIANTS:
    s.VARIANTS[variant['id']] = variant

_original_amount = s.amount
_original_category = s.category
_original_action = s.action
_original_broadcast = s.broadcast_new_products


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
    rows = [
        [s.btn(s.tr(cid, '📺 شاشة', '📺 TV'), 'iptvact:tv'),
         s.btn(s.tr(cid, ' آيفون / آيباد', ' iPhone / iPad'), 'iptvact:ios')],
        [s.btn(s.tr(cid, '💻 كمبيوتر / لابتوب', '💻 Computer / Laptop'), 'iptvact:computer'),
         s.btn(s.tr(cid, '▶️ أندرويد', '▶️ Android'), 'iptvact:android')],
        [s.btn(s.tr(cid, '↩️ رجوع', '↩️ Back'), 'product:iptv')]
    ]
    return s.send(api, cid,
                  s.tr(cid, '📺 <b>طريقة التشغيل</b>\n\n👇 اختر نوع جهازك لعرض الشرح:',
                       '📺 <b>Activation Method</b>\n\n👇 Choose your device to view the instructions:'),
                  s.kb(rows))


def action(api, cid, value):
    if value == 'iptvactivation':
        return activation_menu(api, cid)
    if value.startswith('iptvact:'):
        device = value.split(':', 1)[1]
        labels = {
            'tv': ('📺 شاشة', '📺 TV'),
            'ios': (' آيفون / آيباد', ' iPhone / iPad'),
            'computer': ('💻 كمبيوتر / لابتوب', '💻 Computer / Laptop'),
            'android': ('▶️ أندرويد', '▶️ Android'),
        }
        ar, en = labels.get(device, ('طريقة التفعيل', 'Activation Method'))
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
s.broadcast_new_products = broadcast_new_products
