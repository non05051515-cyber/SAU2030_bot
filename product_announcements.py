"""Manual, previewed stock announcements with native emoji entities and deep links."""
import json
import uuid
import channel_catalog as catalog
import storefront as s
import broadcast_admin as broadcast

ICONS = {'product': ('🛍', 'المنتج'), 'added': ('➕', 'الكمية المضافة'),
         'stock': ('📦', 'المخزون'), 'price': ('💵', 'السعر'), 'buy': ('🛒', 'زر الشراء'),
         'stop_ads': ('🔕', 'زر إيقاف الإعلانات')}
TARGETS = {'channel': catalog.CHANNEL, 'group': '@SAU2030_k'}


def db():
    c = s.db()
    c.execute('CREATE TABLE IF NOT EXISTS product_ad_drafts (cid INTEGER PRIMARY KEY, token TEXT, payload TEXT, status TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS pandora_notification_icons (key TEXT PRIMARY KEY, custom_emoji_id TEXT NOT NULL)')
    return c


def draft(cid):
    with db() as c:
        row = c.execute('SELECT token,payload,status FROM product_ad_drafts WHERE cid=?', (cid,)).fetchone()
    return (row[0], json.loads(row[1]), row[2]) if row else None


def save(cid, data, status='ready'):
    token = uuid.uuid4().hex[:16]
    with db() as c:
        c.execute('INSERT OR REPLACE INTO product_ad_drafts VALUES (?,?,?,?)', (cid, token, json.dumps(data), status))
    return token


def clear(cid):
    with db() as c:
        c.execute('DELETE FROM product_ad_drafts WHERE cid=?', (cid,))


def card(data, language='en', unsubscribe=False):
    if data.get('language') in ('ar', 'en'):
        language = data['language']
    pid = data['pid']
    st = catalog.state(pid)
    with db() as c:
        icons = dict(c.execute('SELECT key,custom_emoji_id FROM pandora_notification_icons'))
    en = language == 'en'
    canonical = s.LEGACY.get(pid, pid)
    variant = s.VARIANTS.get(canonical)
    fallback = variant['name'][language] if variant else s.name(pid, s.G['ADMIN_ID'])
    title = s.text_override(canonical, 'name', language, fallback)
    lines = [('product', title)]
    if data.get('added') is not None:
        lines.append(('added', ('Added: ' if en else 'الكمية المضافة: ') + str(data['added'])))
    quantity = data.get('stock', st['quantity'])
    lines += [('stock', ('Current stock: ' if en else 'المخزون الحالي: ') + (str(quantity) if quantity is not None else ('Available' if en else 'متوفر'))),
              ('price', ('Price: ' if en else 'السعر: ') + str(s.price(0, pid, 'USD')))]
    text, entities = '', []
    units = lambda value: len(value.encode('utf-16-le')) // 2
    for key, value in lines:
        if text:
            text += '\n'
        offset = units(text)
        emoji = ICONS[key][0]
        text += emoji + ' ' + str(value)
        eid = str(icons.get(key, ''))
        if eid.isdecimal():
            entities.append({'type': 'custom_emoji', 'offset': offset, 'length': units(emoji), 'custom_emoji_id': eid})
        entities.append({'type': 'bold', 'offset': offset + units(emoji) + 1, 'length': units(str(value))})
    buy_text = 'Buy now' if en else 'شراء الآن'
    button = {'text': '🛒 ' + buy_text, 'url': catalog.link(pid), 'style': 'success'}
    if str(icons.get('buy', '')).isdecimal():
        button.update(text=buy_text, icon_custom_emoji_id=str(icons['buy']))
    row = [button]
    if unsubscribe:
        stop_button = s.btn('Stop ads' if en else 'إيقاف الإعلانات', 'ads:stop', style='primary')
        if str(icons.get('stop_ads', '')).isdecimal():
            stop_button['icon_custom_emoji_id'] = str(icons['stop_ads'])
        row.append(stop_button)
    return {'text': text, 'entities': entities, 'reply_markup': {'inline_keyboard': [row]}}


def preview(api, cid, data):
    token = save(cid, data)
    if not api.call('sendMessage', chat_id=cid, **card(data, s.prefs(cid)[0], unsubscribe=True)):
        return s.send(api, cid, 'تعذرت المعاينة. راجع إعداد الأيقونات وصلاحية البوت لاستخدامها.',
                      s.kb([[s.btn('🎨 الأيقونات المتحركة', 'ad:icons')], [s.btn('↩️ رجوع', 'ad:home')]]))
    return s.send(api, cid, 'هذه معاينة الإعلان. اختر مكان النشر:', s.kb([
        [s.btn('✅ نشر في القناة', 'ad:send:channel:' + token, style='success')],
        [s.btn('✅ نشر في المجموعة', 'ad:send:group:' + token, style='success')],
        [s.btn('📨 إرسال لمستخدمي البوت', 'ad:send:bot:' + token, style='success')],
        [s.btn('✏️ الكمية المضافة (Added)', 'ad:added')],
        [s.btn('📦 تعديل Current stock', 'ad:stock')],
        [s.btn('🌐 لغة الإعلان', 'ad:language')],
        [s.btn('🎨 الأيقونات المتحركة', 'ad:icons')],
        [s.btn('❌ إلغاء', 'ad:home')]]))


def install(namespace):
    old_action, old_input = namespace['action'], namespace['handle_admin_delivery']

    def action(api, cid, value):
        if value in ('ads:stop', 'ads:resume'):
            if cid <= 0:
                return
            enabled = value == 'ads:resume'
            broadcast.set_ads_enabled(cid, enabled)
            text = s.tr(cid, '✅ تم تفعيل الإعلانات من جديد.', '✅ Ads are enabled again.') if enabled else s.tr(cid,
                '✅ تم إيقاف الإعلانات. ستستمر رسائل طلباتك ومشترياتك.',
                '✅ Ads stopped. You will still receive order and purchase messages.')
            rows = [] if enabled else [[s.btn(s.tr(cid, 'إعادة تفعيل الإعلانات', 'Enable ads again'), 'ads:resume')]]
            return s.send(api, cid, text, s.kb(rows) if rows else None)
        if not value.startswith('ad:'):
            if cid == namespace['ADMIN_ID']:
                clear(cid)
            return old_action(api, cid, value)
        if cid != namespace['ADMIN_ID']:
            return
        parts = value.split(':')
        verb = parts[1]
        current = draft(cid)
        data = current[1] if current else {}
        if verb == 'home':
            clear(cid)
            with s.db() as c:
                c.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
            return s.send(api, cid, '📢 <b>إعلان منتج</b>\n\nاسم المنتج، الكمية المضافة، المخزون والسعر، مع زر شراء أخضر يفتح المنتج مباشرة.', s.kb([
                [s.btn('🛍 اختيار المنتج', 'ad:products:0', style='success')],
                [s.btn('🎨 الأيقونات المتحركة', 'ad:icons')],
                [s.btn('↩️ لوحة الإدارة', 'admin')]]))
        if verb == 'products':
            clear(cid)
            page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
            ids = [pid for pid in catalog.product_ids() if catalog.state(pid)['available']]
            rows = [[s.btn(s.name(pid, cid)[:50], 'ad:pick:' + pid)] for pid in ids[page*8:page*8+8]]
            nav = []
            if page: nav.append(s.btn('⬅️ السابق', f'ad:products:{page-1}'))
            if len(ids) > page*8+8: nav.append(s.btn('التالي ➡️', f'ad:products:{page+1}'))
            if nav: rows.append(nav)
            rows.append([s.btn('↩️ رجوع', 'ad:home')])
            return s.send(api, cid, 'اختر المنتج للإعلان:' if ids else 'لا توجد منتجات متوفرة للنشر.', s.kb(rows))
        if verb in ('pick', 'added'):
            if verb == 'pick': data = {'pid': ':'.join(parts[2:])}
            if data.get('pid') not in catalog.product_ids(): return
            save(cid, data, 'added')
            return s.send(api, cid, 'أرسل الكمية المضافة (Added)، مثل 20. هذا الرقم للإعلان فقط ولا يغير مخزون المنتج.', s.kb([
                [s.btn('بدون سطر الكمية المضافة', 'ad:skip')], [s.btn('❌ إلغاء', 'ad:home')]]))
        if verb == 'stock' and data.get('pid'):
            save(cid, data, 'stock')
            return s.send(api, cid, '📦 أرسل عدد المخزون الذي تريد عرضه في Current stock. التعديل للإعلان فقط ولا يغير مخزون المتجر.', s.kb([
                [s.btn('🔄 استخدام مخزون المنتج تلقائيًا', 'ad:stock_auto')],
                [s.btn('↩️ رجوع للمعاينة', 'ad:preview')]]))
        if verb == 'stock_auto' and data.get('pid'):
            data.pop('stock', None)
            return preview(api, cid, data)
        if verb in ('skip', 'preview') and data.get('pid'):
            if verb == 'skip': data['added'] = None
            return preview(api, cid, data)
        if verb == 'language' and data.get('pid'):
            if len(parts) == 3 and parts[2] in ('auto', 'ar', 'en'):
                data['language'] = parts[2]
                return preview(api, cid, data)
            current_language = {'auto': 'تلقائي حسب لغة العميل', 'ar': 'العربية', 'en': 'English'}.get(data.get('language', 'auto'), 'تلقائي')
            return s.send(api, cid, '🌐 <b>لغة الإعلان</b>\nالحالية: ' + current_language + '\n\nالتلقائي يرسل لكل مستخدم بلغته. في القناة والمجموعة، اختر العربية أو الإنجليزية لتحديد لغة المنشور.', s.kb([
                [s.btn('🇸🇦 العربية', 'ad:language:ar')],
                [s.btn('🇬🇧 English', 'ad:language:en')],
                [s.btn('🌐 تلقائي حسب لغة العميل', 'ad:language:auto')],
                [s.btn('↩️ معاينة الإعلان', 'ad:preview')]]))
        if verb == 'icons':
            save(cid, data)
            rows = [[s.btn(icon + ' ' + label, 'ad:icon:' + key)] for key, (icon, label) in ICONS.items()]
            rows.append([s.btn('↩️ معاينة الإعلان' if data.get('pid') else '↩️ رجوع', 'ad:preview' if data.get('pid') else 'ad:home')])
            return s.send(api, cid, '🎨 اختر الأيقونة التي تريد تغييرها، ثم أرسل الإيموجي المتحرك نفسه أو رسالة تحتوي عليه. تُحفظ الأيقونات للإعلانات القادمة.', s.kb(rows))
        if verb == 'icon' and len(parts) == 3 and parts[2] in ICONS:
            save(cid, data, 'icon:' + parts[2])
            return s.send(api, cid, 'أرسل أيقونة متحركة واحدة أو معرفها الرقمي. اكتب 0 لاستخدام الأيقونة العادية. لقطة الشاشة لا تحتوي على معرف الأيقونة.', s.kb([[s.btn('↩️ رجوع', 'ad:icons')]]))
        if verb == 'send' and len(parts) == 4 and parts[2] in (*TARGETS, 'bot'):
            with db() as c:
                claimed = c.execute("UPDATE product_ad_drafts SET status='sending' WHERE cid=? AND token=? AND status='ready'", (cid, parts[3])).rowcount
                row = c.execute('SELECT payload FROM product_ad_drafts WHERE cid=? AND token=?', (cid, parts[3])).fetchone() if claimed else None
            if not row: return s.send(api, cid, 'انتهت هذه المعاينة أو سبق نشرها. أنشئ إعلانًا جديدًا.')
            data = json.loads(row[0])
            if data.get('pid') not in catalog.product_ids() or not catalog.state(data['pid'])['available']:
                clear(cid)
                return s.send(api, cid, 'المنتج لم يعد متوفرًا للنشر.')
            if parts[2] == 'bot':
                payload = {'localized': {lang: card(data, lang, unsubscribe=True) for lang in ('ar', 'en')}}
                count = namespace['queue_product_announcement'](parts[3], data['pid'], payload)
                clear(cid)
                return s.send(api, cid, f'⏳ تمت جدولة الإعلان للإرسال إلى {count} من مستخدمي البوت. سيصلك تقرير بالنتيجة بعد الانتهاء.',
                              s.kb([[s.btn('↩️ إعلان منتج', 'ad:home')]]))
            result = api.call('sendMessage', chat_id=TARGETS[parts[2]], **card(data))
            clear(cid)
            return s.send(api, cid, '✅ تم نشر الإعلان مع زر الشراء.' if result else '❌ تعذر تأكيد النشر. راجع القناة أو المجموعة قبل المحاولة مجددًا، وتحقق من صلاحيات النشر والأيقونات.', s.kb([[s.btn('↩️ إعلان منتج', 'ad:home')]]))

    def message_input(api, message):
        cid = message.get('chat', {}).get('id')
        if cid != namespace['ADMIN_ID']: return old_input(api, message)
        current = draft(cid)
        if not current: return old_input(api, message)
        text = message.get('text', '').strip()
        if text.startswith('/') or text in namespace.get('MENU', {}):
            clear(cid)
            return old_input(api, message)
        _, data, status = current
        if status in ('added', 'stock'):
            try:
                added = int(text)
                if not 0 <= added <= 1000000000: raise ValueError()
            except ValueError:
                s.send(api, cid, 'أرسل عددًا صحيحًا موجبًا أو صفرًا.' if status == 'stock' else 'أرسل عددًا صحيحًا موجبًا أو صفرًا، أو اضغط «بدون سطر الكمية المضافة».')
                return True
            data[status] = added
            preview(api, cid, data)
            return True
        if status.startswith('icon:'):
            key = status.split(':')[1]
            entities = message.get('entities') or message.get('caption_entities') or []
            found = [str(e.get('custom_emoji_id', '')) for e in entities if e.get('type') == 'custom_emoji']
            eid = found[0] if len(found) == 1 else text
            if not eid.isdecimal() or len(found) > 1:
                s.send(api, cid, 'أرسل أيقونة متحركة واحدة فقط، أو معرفها الرقمي، أو 0 للأيقونة العادية.')
                return True
            with db() as c:
                if eid == '0': c.execute('DELETE FROM pandora_notification_icons WHERE key=?', (key,))
                else: c.execute('INSERT OR REPLACE INTO pandora_notification_icons VALUES (?,?)', (key, eid))
            save(cid, data)
            s.send(api, cid, '✅ تم حفظ الأيقونة.')
            action(api, cid, 'ad:icons')
            return True
        s.send(api, cid, 'استخدم الأزرار أسفل المعاينة لإكمال الإعلان أو إلغائه.')
        return True

    namespace['action'] = action
    namespace['handle_admin_delivery'] = message_input
