"""Wholesale storefront navigation and owner-managed welcome content."""
import storefront as s

DEFAULTS = {
    'ar': '👋 <b>مرحباً بك في VEXA STORE</b>\n\nمورد اشتراكات ومنتجات رقمية لأصحاب المتاجر والبوتات. اختر القسم لعرض الباقات المتوفرة.',
    'en': '👋 <b>Welcome to VEXA STORE</b>\n\nA supplier of digital subscriptions and products for store and bot owners. Pick a category to browse available plans.',
}
LABELS = {'ui_welcome': 'تعديل الرسالة الترحيبية', 'ui_welcome_ar': 'تعديل النص العربي',
          'ui_welcome_en': 'تعديل النص الإنجليزي', 'ui_welcome_photo': 'إضافة / تغيير صورة الترحيب',
          'ui_welcome_remove': 'حذف صورة الترحيب', 'ui_welcome_preview': 'معاينة رسالة الترحيب'}


def settings():
    with s.db() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS welcome_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        return dict(conn.execute('SELECT key,value FROM welcome_settings'))


def save(key, value):
    settings()
    with s.db() as conn:
        conn.execute('INSERT OR REPLACE INTO welcome_settings VALUES (?,?)', (key, value))


def button(key, action):
    return s.btn(s.ui_label(key, LABELS[key]), action, s.ui_icon(key))


def show(api, cid):
    s.reset_navigation_state(cid)
    values = settings()
    lang = s.prefs(cid)[0]
    text = values.get(lang, DEFAULTS[lang])
    keyboard = s.kb([[s.btn(s.tr(cid, '🛍 تصفّح كتالوج الجملة', '🛍 Browse wholesale catalog'), 'merchant:shop', s.ui_icon('ui_start'), style='primary')],
                     [s.btn('🌐 العربية / English', 'settings:lang', s.ui_icon('ui_language'))]])
    photo = values.get('cover_photo')
    if photo is None:
        return s.card(api, cid, 'assets/vexa-ai-cover.jpg', 'VEXA STORE', text, keyboard)
    if photo:
        # Long welcome text remains a separate message to respect caption limits.
        short = int(values.get('length_' + lang, '200')) <= 1024
        data = {'chat_id': cid, 'photo': photo}
        if short:
            data.update(caption=text, parse_mode='HTML', reply_markup=keyboard)
        try:
            sent = api.call('sendPhoto', **data)
        except Exception:
            sent = None
        if short and sent:
            return sent
    return s.send(api, cid, text, keyboard)


SECTIONS = {
    'ai': ('🤖 الذكاء الاصطناعي', '🤖 Artificial intelligence'),
    'computer': ('💻 الكمبيوتر والبرامج', '💻 Computer & software'),
    'design': ('🎨 التصميم والمونتاج', '🎨 Design & video'),
    'entertainment': ('🎬 الترفيه', '🎬 Entertainment'),
    'other': ('📦 خدمات أخرى', '📦 Other services'),
}
CATEGORY_SECTIONS = {
    'chatgpt': 'ai', 'claude': 'ai', 'gemini': 'ai', 'grok': 'ai',
    'computer': 'computer', 'canva': 'design', 'capcut': 'design',
    'pandora_capcut': 'design', 'youtube': 'entertainment',
    'netflix': 'entertainment', 'iptv': 'entertainment',
}
KEYWORDS = {
    'ai': ('chatgpt', 'claude', 'gemini', 'grok', 'perplexity', 'ذكاء اصطناعي', 'ذكاء إصطناعي'),
    'computer': ('windows', 'office', 'microsoft', 'computer', 'vpn', 'antivirus', 'كمبيوتر', 'ويندوز', 'أوفيس'),
    'design': ('canva', 'capcut', 'adobe', 'design', 'تصميم', 'مونتاج'),
    'entertainment': ('youtube', 'netflix', 'iptv', 'ترفيه', 'شاهد'),
}
PAGE_SIZE = 12


def category_section(pid, name):
    if pid in CATEGORY_SECTIONS:
        return CATEGORY_SECTIONS[pid]
    label = (pid + ' ' + name).casefold()
    return next((section for section, terms in KEYWORDS.items() if any(term in label for term in terms)), 'other')


def catalog_groups(cid):
    from product_order import _ordered

    groups = {section: [] for section in SECTIONS}
    for scope, route in (('category', 'product:'), ('direct', 'options:')):
        for pid in _ordered(s, scope):
            label = s.name(pid, cid)
            group = category_section(pid, label)
            groups[group].append((label, route + pid))
    return groups


def section_button(cid, section):
    style = 'primary' if section in ('ai', 'design') else 'success' if section == 'computer' else None
    return s.btn(s.tr(cid, *SECTIONS[section]), 'wholesale:' + section, style=style)


def wholesale_home(api, cid):
    body = s.tr(
        cid,
        '<b>VEXA STORE</b>\nمورد اشتراكات ومنتجات رقمية للتجّار وأصحاب البوتات.\n\nاختر قسمًا للاطلاع على الباقات والأسعار والتوفر.',
        '<b>VEXA STORE</b>\nDigital subscriptions and products for merchants and bot owners.\n\nChoose a department to see plans, prices and availability.',
    )
    groups = catalog_groups(cid)
    rows = [[section_button(cid, 'ai'), section_button(cid, 'computer')]]
    other = [section_button(cid, key) for key in ('design', 'entertainment', 'other') if groups[key]]
    rows.extend([other[index:index + 2] for index in range(0, len(other), 2)])
    rows.extend([
        [s.btn(s.tr(cid, '💳 رصيدي', '💳 My balance'), 'wallet', style='success'),
         s.btn(s.tr(cid, '➕ شحن الرصيد', '➕ Top up'), 'wallet:topup')],
        [s.btn(s.tr(cid, '💬 الدعم وطلبات الجملة', '💬 Support & bulk orders'), 'support', style='primary')],
        [s.btn(s.tr(cid, 'الإحالات', 'Referrals'), 'referrals'),
         s.btn(s.tr(cid, 'المجتمع', 'Community'), 'community')],
        [s.btn(s.tr(cid, 'الإبلاغ عن مشكلة', 'Report an issue'), 'support'),
         s.btn(s.tr(cid, 'العملة', 'Currency'), 'settings:currency')],
        [s.btn('العربية / English', 'settings:lang')],
    ])
    if cid == s.G['ADMIN_ID']:
        rows.append([s.btn('لوحة الطلبات', 'admin', style='primary')])
    return s.send(api, cid, body, s.kb(rows))


def wholesale_section(api, cid, section, page):
    items = catalog_groups(cid)[section]
    last = max(0, (len(items) - 1) // PAGE_SIZE)
    page = min(max(0, page), last)
    rows = [[s.btn(label, callback, style='primary')] for label, callback in items[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]]
    if last:
        pages = []
        if page:
            pages.append(s.btn(s.tr(cid, 'السابق', 'Previous'), f'wholesale:{section}:{page - 1}'))
        if page < last:
            pages.append(s.btn(s.tr(cid, 'التالي', 'Next'), f'wholesale:{section}:{page + 1}'))
        rows.append(pages)
    rows.append([s.btn(s.tr(cid, 'الأقسام', 'Departments'), 'home')])
    title = '<b>' + s.esc(s.tr(cid, *SECTIONS[section])) + '</b>'
    note = s.tr(cid, 'اختر المنتج لعرض الباقات والسعر.', 'Choose a product to see its plans and price.') if items else s.tr(cid, 'ما فيه منتجات معروضة في هذا القسم حاليًا.', 'There are no listings in this department yet.')
    return s.send(api, cid, title + '\n\n' + note, s.kb(rows))


def wholesale_action(api, cid, value):
    if value in ('products', 'enter_store', 'home'):
        wholesale_home(api, cid)
        return True
    if value.startswith('wholesale:'):
        _, _, selector = value.partition(':')
        section, _, page = selector.partition(':')
        if section in SECTIONS and (not page or page.isdecimal() and len(page) <= 4):
            wholesale_section(api, cid, section, int(page or '0'))
        else:
            wholesale_home(api, cid)
        return True
    return False


def menu(api, cid):
    if cid != s.G['ADMIN_ID']:
        return
    with s.db() as conn:
        conn.execute("DELETE FROM admin_state WHERE cid=? AND action IN ('welcome_text','welcome_photo')", (cid,))
    rows = [[button('ui_welcome_ar', 'welcome:text:ar'), button('ui_welcome_en', 'welcome:text:en')],
            [button('ui_welcome_photo', 'welcome:photo')]]
    if settings().get('cover_photo') != '':
        rows.append([button('ui_welcome_remove', 'welcome:remove')])
    rows += [[button('ui_welcome_preview', 'welcome:preview')], [s.btn('↩️ لوحة الإدارة', 'admin')]]
    return s.send(api, cid, '👋 <b>الرسالة الترحيبية</b>\n\nعدّل النص الذي يظهر فوق زر ابدأ، أو أضف صورة ترحيبية.\nيمكنك استخدام الأسطر والأيقونات المتحركة.', s.kb(rows))


def receive(api, message):
    cid = message.get('chat', {}).get('id')
    if cid != s.G['ADMIN_ID']:
        return False
    with s.db() as conn:
        row = conn.execute("SELECT action,value FROM admin_state WHERE cid=? AND action IN ('welcome_text','welcome_photo')", (cid,)).fetchone()
    if not row:
        return False
    text = (message.get('text') or '').strip()
    if text.startswith('/') or text in s.G.get('MENU', {}):
        with s.db() as conn:
            conn.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
        return False
    if row[0] == 'welcome_photo':
        photos = message.get('photo') or []
        if not photos or not photos[-1].get('file_id'):
            s.send(api, cid, 'أرسل الصورة كصورة في تيليجرام.', s.kb([[s.btn('إلغاء', 'welcome:menu')]]))
            return True
        save('cover_photo', photos[-1]['file_id'])
    else:
        length = len(text.encode('utf-16-le')) // 2
        if not text or length > 1500:
            s.send(api, cid, 'أرسل نصًا غير فارغ حتى 1500 حرف.', s.kb([[s.btn('إلغاء', 'welcome:menu')]]))
            return True
        save(row[1], s.description_message_html(message, text))
        save('length_' + row[1], str(length))
    s.send(api, cid, '✅ تم حفظ الرسالة الترحيبية.' if row[0] == 'welcome_text' else '✅ تم حفظ صورة الترحيب.')
    menu(api, cid)
    return True


def install(namespace):
    old_action = namespace['action']
    old_receipt = namespace['handle_receipt']
    s.UI_ICON_LABELS.update(LABELS)

    def action(api, cid, value):
        if wholesale_action(api, cid, value):
            return
        if value.startswith('welcome:') or value == 'admin:welcome':
            if cid != s.G['ADMIN_ID']:
                return
            s.reset_navigation_state(cid)
            if value in ('admin:welcome', 'welcome:menu'):
                return menu(api, cid)
            if value == 'welcome:preview':
                show(api, cid)
                return menu(api, cid)
            if value == 'welcome:remove':
                save('cover_photo', '')
                s.send(api, cid, '✅ تم حذف صورة الترحيب.')
                return menu(api, cid)
            if value in ('welcome:text:ar', 'welcome:text:en', 'welcome:photo'):
                photo = value == 'welcome:photo'
                lang = value.rsplit(':', 1)[-1]
                with s.db() as conn:
                    conn.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)',
                                 (cid, 'welcome_photo' if photo else 'welcome_text', '' if photo else lang))
                prompt = 'أرسل صورة الترحيب الآن.' if photo else 'أرسل نص الترحيب الجديد ' + ('بالعربية' if lang == 'ar' else 'بالإنجليزية') + ' (حتى 1500 حرف).'
                return s.send(api, cid, prompt, s.kb([[s.btn('إلغاء', 'welcome:menu')]]))
            return menu(api, cid)
        if cid == s.G['ADMIN_ID']:
            with s.db() as conn:
                conn.execute("DELETE FROM admin_state WHERE cid=? AND action IN ('welcome_text','welcome_photo')", (cid,))
        return old_action(api, cid, value)

    def receipt(api, message):
        return receive(api, message) or old_receipt(api, message)

    s.start = show
    s.home = wholesale_home
    s.products = wholesale_home
    namespace.update(show_start=show, show_home=wholesale_home, show_products=wholesale_home,
                     action=action, handle_action=action, handle_receipt=receipt)
