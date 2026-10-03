"""Arabic product quantity controls and durable checkout quantity snapshots."""
import time
from decimal import Decimal


def prepare(c):
    c.execute('CREATE TABLE IF NOT EXISTS selected_quantities (cid INTEGER, pid TEXT, qty INTEGER NOT NULL, PRIMARY KEY(cid,pid))')
    c.execute('CREATE TABLE IF NOT EXISTS quantity_input (cid INTEGER PRIMARY KEY, pid TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS quantity_snapshots (kind TEXT, key TEXT, qty INTEGER NOT NULL, PRIMARY KEY(kind,key))')
    c.execute('CREATE TABLE IF NOT EXISTS stock_alerts (cid INTEGER, pid TEXT, was_available INTEGER, PRIMARY KEY(cid,pid))')
    # Freeze quantity alongside the existing immutable price snapshots.
    c.execute('''CREATE TRIGGER IF NOT EXISTS quantity_quote AFTER INSERT ON payment_quotes BEGIN
        INSERT OR REPLACE INTO quantity_snapshots VALUES ('quote', NEW.cid || ':' || NEW.pid || ':' || NEW.method,
        COALESCE((SELECT qty FROM selected_quantities WHERE cid=NEW.cid AND pid=NEW.pid),1)); END''')
    c.execute('''CREATE TRIGGER IF NOT EXISTS quantity_receipt AFTER INSERT ON receipts BEGIN
        INSERT OR REPLACE INTO quantity_snapshots VALUES ('receipt', CAST(NEW.cid AS TEXT),
        COALESCE((SELECT qty FROM quantity_snapshots WHERE kind='quote' AND key=NEW.cid || ':' || NEW.pid || ':' || NEW.method),
        (SELECT qty FROM selected_quantities WHERE cid=NEW.cid AND pid=NEW.pid),1)); END''')
    c.execute('''CREATE TRIGGER IF NOT EXISTS quantity_crypto AFTER INSERT ON crypto_orders BEGIN
        INSERT OR REPLACE INTO quantity_snapshots VALUES ('crypto', NEW.id,
        COALESCE((SELECT qty FROM selected_quantities WHERE cid=NEW.cid AND pid=NEW.pid),1)); END''')


def selected(s, cid, pid):
    with s.db() as c:
        row = c.execute('SELECT qty FROM selected_quantities WHERE cid=? AND pid=?', (cid,pid)).fetchone()
    return row[0] if row else 1


def snapshot(s, kind, key):
    with s.db() as c:
        row = c.execute('SELECT qty FROM quantity_snapshots WHERE kind=? AND key=?', (kind,str(key))).fetchone()
    return row[0] if row else 1


def limit(s, pid):
    try:
        return max(0, int(s.product_stock(pid)))
    except (ValueError, TypeError):
        return 1 if s.in_stock(pid) else 0


def valid(s, cid, pid):
    return s.can_order(pid) and 1 <= selected(s,cid,pid) <= limit(s,pid)


def choose(s, api, cid, pid, value):
    try:
        qty = int(value)
    except (ValueError, TypeError):
        qty = 0
    if not s.can_order(pid) or not 1 <= qty <= min(limit(s,pid), 10000):
        return s.send(api,cid,f'الكمية المتوفرة حاليًا: {min(limit(s,pid),10000)}. اختر كمية لا تتجاوز المخزون الفعلي.',
                      s.kb([[s.btn('↩️ رجوع للمنتج','options:'+pid)]]))
    with s.db() as c:
        c.execute('INSERT OR REPLACE INTO selected_quantities VALUES (?,?,?)',(cid,pid,qty))
        c.execute('DELETE FROM quantity_input WHERE cid=?',(cid,))
    s.payments(api,cid,pid)


def button(s, text, data, key, style=None):
    icon = s.ui_icon(key)
    if key in {f'ui_quantity_{n}' for n in (1,2,3,5,10)} and icon is None:
        icon = s.ui_icon('ui_quantity')
    return s.btn(s.ui_label(key, text),data,icon,style=style)


def page(s, api, cid, pid):
    if not s.product_visible(pid):
        return s.send(api,cid,'هذا المنتج غير متاح حاليًا.')
    qty = selected(s,cid,pid)
    rows = []
    if s.can_order(pid):
        # Do not offer quantities that exceed the product's actual inventory.
        available = min(limit(s, pid), 10000)
        nums = [n for n in (1, 2, 3, 5, 10) if n <= available]
        choices = [button(s,'🛍 ×'+str(n),f'chooseqty:{pid}:{n}',f'ui_quantity_{n}',
                          'primary' if qty==n else None) for n in nums]
        rows = [choices[:3]]
        if len(choices) > 3:
            rows.append(choices[3:])
        if available > 1:
            rows.append([button(s,'⭐ كمية مخصصة','customqty:'+pid,'ui_quantity_custom')])
    with s.db() as c:
        subscribed = c.execute('SELECT 1 FROM stock_alerts WHERE cid=? AND pid=?',(cid,pid)).fetchone()
    if s.can_order(pid):
        rows.append([button(s,'🛒 شراء الآن','buy:'+pid,'ui_buy_now','success')])
    else:
        rows.append([button(s,'🔕 إيقاف تنبيه التوفر' if subscribed else '🔔 تفعيل تنبيه التوفر',
                            'stockalert:'+pid,'ui_stock_alert','primary')])
    rows.append([button(s,'📃 ملاحظات التسليم','deliverynote:'+pid,'ui_delivery_note')])
    v = s.VARIANTS.get(pid)
    cp = s.custom_product(pid)
    parent = v['category'] if v else cp[5] if cp else None
    rows.append([button(s,'↩️ رجوع','product:'+parent if parent else 'products','ui_back')])
    text = '<b>'+s.esc(s.name(pid,cid))+'</b>\n\n'+s.info_block(pid,cid)
    text += f'\n👛 رصيدك: {s.wallet_balance(cid):.2f} ر.س\n\n'+s.product_description_html(pid,cid)
    if s.can_order(pid):
        unit = s.amount(pid,'SAR')
        text += f'\n\n🛍 الكمية المختارة: {qty}\n💰 الإجمالي قبل الخصم: {unit*qty:.2f} ر.س\nاختر الكمية للانتقال إلى الدفع.'
    else:
        text += '\n\n🔴 الطلب غير متاح حاليًا.'
    s.card(api,cid,v.get('image') if v else None,s.name(pid,cid),text,s.kb(rows),pid=pid)


def install(s, namespace):
    old_action = namespace['action']
    old_receipt = namespace['handle_receipt']
    s.UI_ICON_LABELS.update({'ui_buy_now':'أيقونة شراء الآن'})
    s.UI_ICON_LABELS.update({'ui_quantity':'أيقونة الكميات','ui_quantity_custom':'أيقونة الكمية المخصصة',
                            'ui_stock_alert':'أيقونة تنبيه التوفر','ui_delivery_note':'أيقونة ملاحظات التسليم'})
    s.UI_ICON_LABELS.update({f'ui_quantity_{n}': f'أيقونة الكمية {n}' for n in (1,2,3,5,10)})

    def action(api,cid,value):
        prefix,_,arg = value.partition(':')
        # Supplier API admin routes are handled here at the outer action layer
        # so no extension can swallow them and return to the admin panel.
        if cid == s.G['ADMIN_ID'] and value == 'admin:supplierapi':
            return s.supplier_api_menu(api, cid)
        if cid == s.G['ADMIN_ID'] and prefix == 'suppliercat':
            return s.supplier_api_menu(api, cid, arg)
        if cid == s.G['ADMIN_ID'] and prefix == 'supplierpick':
            return s.supplier_api_editor(api, cid, arg)
        if cid == s.G['ADMIN_ID'] and prefix == 'supplierpandora':
            endpoint, api_key, service_id, enabled, provider, variant_id = s.supplier_api_row(arg)
            endpoint = 'https://api.pandoradigital.shop/api/v1'
            provider = 'pandora'
            with s.db() as conn:
                conn.execute('INSERT INTO supplier_api(pid,endpoint,api_key,service_id,enabled,provider,variant_id) VALUES (?,?,?,?,?,?,?) ON CONFLICT(pid) DO UPDATE SET endpoint=excluded.endpoint,provider=excluded.provider',
                             (arg, endpoint, api_key, service_id, enabled, provider, variant_id))
            s.send(api, cid, '✅ تم اختيار <b>Pandora Digital</b> لهذا المنتج.')
            return s.supplier_api_editor(api, cid, arg)
        if cid == s.G['ADMIN_ID'] and prefix == 'supplierset':
            field, _, pid = arg.partition(':')
            if field in ('endpoint','key','service','variant'):
                action_name = {'endpoint':'supplier_endpoint','key':'supplier_key','service':'supplier_service','variant':'supplier_variant'}[field]
                with s.db() as conn:
                    conn.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)', (cid, action_name, pid))
                prompt = {'endpoint':'أرسل رابط API الكامل.',
                          'key':'أرسل مفتاح API. لن يظهر كاملًا بعد الحفظ.',
                          'service':'أرسل Product ID لدى المورد.',
                          'variant':'أرسل Variant ID لدى المورد.'}[field]
                return s.send(api, cid, '🔌 <b>' + s.esc(s.name(pid, cid)) + '</b>\n\n' + prompt,
                              s.kb([[s.btn('❌ إلغاء', 'supplierpick:' + pid)]]))
        if cid == s.G['ADMIN_ID'] and prefix == 'suppliertest':
            return s.supplier_test_connection(api, cid, arg)
        if cid == s.G['ADMIN_ID'] and prefix == 'suppliertoggle':
            endpoint, api_key, service_id, enabled, provider, variant_id = s.supplier_api_row(arg)
            if not endpoint or not api_key or (provider == 'pandora' and (not service_id or not variant_id)):
                s.send(api, cid, '⚠️ أضف رابط API والمفتاح وProduct ID وVariant ID أولًا.' if provider == 'pandora' else '⚠️ أضف رابط API والمفتاح أولًا.')
                return s.supplier_api_editor(api, cid, arg)
            with s.db() as conn:
                conn.execute('INSERT INTO supplier_api(pid,endpoint,api_key,service_id,enabled,provider,variant_id) VALUES (?,?,?,?,?,?,?) ON CONFLICT(pid) DO UPDATE SET enabled=excluded.enabled',
                             (arg, endpoint, api_key, service_id, 0 if enabled else 1, provider, variant_id))
            return s.supplier_api_editor(api, cid, arg)
        if cid == s.G['ADMIN_ID'] and prefix == 'supplierdelete':
            with s.db() as conn:
                conn.execute('DELETE FROM supplier_api WHERE pid=?', (arg,))
            s.send(api, cid, '✅ تم حذف ربط API لهذا المنتج.')
            return s.supplier_api_editor(api, cid, arg)
        if value == 'admin:addtocategory':
            return s.add_to_category(api, cid)
        if prefix == 'addtocategory':
            return s.add_to_category(api, cid, arg)
        if prefix == 'product' and s.show_extended_category(api, cid, arg):
            return
        button_actions = {
            'buttonlabel': s.begin_button_label,
            'resetbuttonlabel': s.reset_button_label,
            'removebuttonicon': s.remove_button_icon,
            'removenameicon': s.remove_name_icon,
        }
        if prefix in button_actions:
            return button_actions[prefix](api, cid, arg)
        if value == 'buttonnames:categories':
            return s.button_names_categories(api, cid)
        if value in ('admin:buttonlabels', 'cancelbuttonlabel'):
            if cid == s.G['ADMIN_ID']:
                with s.db() as c:
                    c.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
            return s.admin_button_labels(api, cid)
        with s.db() as c:
            c.execute('DELETE FROM quantity_input WHERE cid=?',(cid,))
        if prefix in ('item','claude','options') or value in s.LEGACY:
            return page(s,api,cid,s.LEGACY.get(value,s.LEGACY.get(arg,arg)))
        if prefix=='product' and s.amount(arg,'SAR') is not None and not any(v['category']==arg for v in s.VARIANTS.values()):
            if s.category_visible(arg) and s.category_description_html(arg, cid) is not None:
                s.send(api, cid, s.category_heading(arg, cid))
            return page(s,api,cid,arg)
        if prefix=='chooseqty':
            pid,_,qty = arg.rpartition(':')
            return choose(s,api,cid,pid,qty)
        if prefix=='customqty':
            if not s.can_order(arg): return page(s,api,cid,arg)
            s.reset_navigation_state(cid)
            with s.db() as c:
                c.execute('INSERT OR REPLACE INTO quantity_input VALUES (?,?)',(cid,arg))
            return s.send(api,cid,f'✍️ أرسل الكمية المطلوبة من ١ إلى {min(limit(s,arg),10000)}.',
                          s.kb([[s.btn('❌ إلغاء','options:'+arg)]]))
        if prefix=='stockalert':
            if not s.product_visible(arg): return
            with s.db() as c:
                exists=c.execute('SELECT 1 FROM stock_alerts WHERE cid=? AND pid=?',(cid,arg)).fetchone()
                if exists: c.execute('DELETE FROM stock_alerts WHERE cid=? AND pid=?',(cid,arg))
                else: c.execute('INSERT INTO stock_alerts VALUES (?,?,?)',(cid,arg,int(s.can_order(arg))))
            return page(s,api,cid,arg)
        if prefix=='deliverynote':
            text=s.product_description_html(arg,cid)
            warranty=s.info_display(arg)
            if warranty[2] and warranty[3]: text+='\n\nالضمان: '+s.esc(warranty[3])
            return s.send(api,cid,'📃 <b>ملاحظات التسليم</b>\n\n'+(text or 'تواصل مع الدعم لمعرفة تفاصيل التسليم.'),
                          s.kb([[s.btn('💬 الدعم','support')],[s.btn('↩️ رجوع','options:'+arg)]]))
        if prefix in ('buy','paywallet','paycrypto','paybybit','bybitid','trc20','bep20','custompay'):
            pid=arg.split(':',1)[1] if prefix=='custompay' and ':' in arg else arg
            pid=s.LEGACY.get(pid,pid)
            if not valid(s,cid,pid):
                return s.send(api,cid,'الكمية المختارة لم تعد متاحة. اختر كمية مناسبة من صفحة المنتج.',
                              s.kb([[s.btn('↩️ المنتج','options:'+pid)]]))
        return old_action(api,cid,value)

    def receipt(api,msg):
        cid=msg['chat']['id']
        if s.handle_button_label(api, msg):
            return True
        with s.db() as c:
            pending=c.execute('SELECT pid FROM quantity_input WHERE cid=?',(cid,)).fetchone()
        if pending:
            text=msg.get('text','').strip()
            if text.startswith('/') or text in namespace.get('MENU',{}):
                with s.db() as c:c.execute('DELETE FROM quantity_input WHERE cid=?',(cid,))
            else:
                choose(s,api,cid,pending[0],text)
                return True
        return old_receipt(api,msg)

    last_tick=[0]
    def tick(api):
        if time.monotonic()-last_tick[0]<30:return
        last_tick[0]=time.monotonic()
        with s.db() as c: rows=c.execute('SELECT cid,pid,was_available FROM stock_alerts').fetchall()
        for cid,pid,was in rows:
            available=int(s.can_order(pid))
            if available and not was:
                result=s.send(api,cid,'🔔 عاد المنتج للتوفر: '+s.esc(s.name(pid,cid)),
                              s.kb([[s.btn('🛍 عرض المنتج','options:'+pid)]]))
                if not result: continue
            with s.db() as c:c.execute('UPDATE stock_alerts SET was_available=? WHERE cid=? AND pid=?',(available,cid,pid))

    namespace['action']=action
    namespace['handle_action']=action
    namespace['handle_receipt']=receipt
    namespace['tick_stock_alerts']=tick
