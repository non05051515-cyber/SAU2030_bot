"""Owner-managed welcome text and optional Telegram photo, persisted in SQLite."""
import storefront as s

DEFAULTS = {
    'ar': '👋 <b>مرحباً بك في VEXA STORE</b>\n\nمتجر الخدمات والاشتراكات الرقمية.',
    'en': '👋 <b>Welcome to VEXA STORE</b>\n\nDigital services and subscriptions.',
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
    keyboard = s.kb([[s.btn('🚀 START | ابدأ', 'enter_store', s.ui_icon('ui_start'))],
                     [s.btn('🌐 العربية / English', 'settings:lang', s.ui_icon('ui_language'))]])
    photo = values.get('photo')
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


def menu(api, cid):
    if cid != s.G['ADMIN_ID']:
        return
    with s.db() as conn:
        conn.execute("DELETE FROM admin_state WHERE cid=? AND action IN ('welcome_text','welcome_photo')", (cid,))
    rows = [[button('ui_welcome_ar', 'welcome:text:ar'), button('ui_welcome_en', 'welcome:text:en')],
            [button('ui_welcome_photo', 'welcome:photo')]]
    if settings().get('photo'):
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
        save('photo', photos[-1]['file_id'])
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
                save('photo', '')
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
    namespace.update(show_start=show, action=action, handle_action=action, handle_receipt=receipt)
