"""Editable product facts, reusing existing warranty and animated icon storage."""
import re
import html
import uuid
import json


def prepare(s):
    with s.db() as c:
        c.execute('CREATE TABLE IF NOT EXISTS product_detail_fields (id TEXT PRIMARY KEY, pid TEXT NOT NULL, label TEXT NOT NULL, value TEXT NOT NULL, visible INTEGER NOT NULL DEFAULT 1)')


def related_ids(s, pid):
    pid = s.LEGACY.get(pid, pid)
    with s.db() as c:
        row = c.execute("SELECT provider,service_id,variant_id FROM supplier_api WHERE pid=?", (pid,)).fetchone()
        if not row or not row[0] or not row[1]:
            return [pid]
        matches = c.execute("SELECT pid FROM supplier_api WHERE provider=? AND service_id=? AND COALESCE(variant_id,'')=?", (row[0],row[1],row[2] or '')).fetchall()
    return [pid] + [r[0] for r in matches if r[0] != pid]


def fields(s, pid):
    prepare(s)
    with s.db() as c:
        return c.execute('SELECT id,label,value,visible,pid FROM product_detail_fields WHERE pid IN ('+','.join('?' for _ in related_ids(s,pid))+') ORDER BY rowid', related_ids(s,pid)).fetchall()


def clean_description(text):
    lines = text.splitlines()
    result = []
    for line in lines:
        plain = html.unescape(re.sub(r'<[^>]*>', '', line)).strip()
        # Remove copied headings and stale supplier metadata, keeping prose and emoji markup.
        if re.match(r'^[^\w]*?(?:السعر|المخزون|الكمية(?: المتوفرة)?|رصيدك|Price|Stock|Quantity|Balance)\s*:', plain, re.I):
            continue
        if re.match(r'^[^\w]*?(?:الوصف|Description)\s*:\s*$', plain, re.I):
            continue
        result.append(line)
    return '\n'.join(result).strip()



def section_ids(s, pid):
    """Exact category membership, never supplier identity or name similarity."""
    pid = s.LEGACY.get(pid, pid)
    cp = s.custom_product(pid)
    category = cp[5] if cp else s.VARIANTS.get(pid, {}).get('category')
    if not category:
        return []
    with s.db() as c:
        custom = [r[0] for r in c.execute(
            'SELECT pid FROM admin_products WHERE category_id=?', (category,))]
    return list(dict.fromkeys(
        [p for p, v in s.VARIANTS.items() if v.get('category') == category] + custom))


def offer_section(s, api, cid, pid, kind, field=''):
    targets = [p for p in section_ids(s, pid) if p != pid]
    if not targets:
        return
    token = uuid.uuid4().hex[:12]
    with s.db() as c:
        c.execute('CREATE TABLE IF NOT EXISTS product_detail_bulk (token TEXT PRIMARY KEY, cid INTEGER, payload TEXT)')
        c.execute('DELETE FROM product_detail_bulk WHERE cid=?', (cid,))
        if kind == 'icon':
            icon = c.execute('SELECT custom_emoji_id,fallback_emoji FROM product_info_icons WHERE pid=? AND field=?',
                             (pid, field)).fetchone()
            if not icon:
                return
            label = None
            if field.startswith('extra_'):
                row = c.execute('SELECT label FROM product_detail_fields WHERE id=? AND pid=?',
                                (field[6:], pid)).fetchone()
                if not row:
                    return
                label = row[0]
            data = dict(kind=kind, pid=pid, targets=targets, field=field, icon=list(icon), label=label)
        else:
            display = s.info_display(pid)
            data = dict(kind=kind, pid=pid, targets=targets, enabled=display[2], warranty=display[3])
        c.execute('INSERT INTO product_detail_bulk VALUES (?,?,?)', (token, cid, json.dumps(data)))
    text = ('هل تريد تطبيق هذه الأيقونة على جميع منتجات القسم نفسه؟'
            if kind == 'icon' else 'هل تريد تطبيق إعداد الضمان على جميع منتجات القسم نفسه؟')
    if kind == 'icon' and data.get('label'):
        text += '\\nستُطبّق على الحقول التي تحمل الاسم نفسه فقط، دون تغيير قيمها.'
    s.send(api, cid, text, s.kb([
        [s.btn('نعم، جميع منتجات القسم', 'detailbulk:yes:'+token)],
        [s.btn('هذا المنتج فقط', 'detailbulk:no:'+token)]]))


def apply_section(s, api, cid, arg):
    decision, _, token = arg.partition(':')
    if decision not in ('yes', 'no'):
        return
    with s.db() as c:
        c.execute('CREATE TABLE IF NOT EXISTS product_detail_bulk (token TEXT PRIMARY KEY, cid INTEGER, payload TEXT)')
        row = c.execute('SELECT payload FROM product_detail_bulk WHERE token=? AND cid=?', (token, cid)).fetchone()
        if not row:
            return s.send(api, cid, 'انتهى هذا الخيار. عدّل الإعداد من جديد.')
        data = json.loads(row[0])
        count = 0
        if decision == 'yes':
            current = set(section_ids(s, data['pid']))
            for pid in data['targets']:
                if pid not in current:
                    continue
                if data['kind'] == 'icon':
                    keys = [data['field']]
                    if data.get('label') is not None:
                        keys = ['extra_'+r[0] for r in c.execute(
                            'SELECT id FROM product_detail_fields WHERE pid=? AND label=?',
                            (pid, data['label'])).fetchall()]
                    for key in keys:
                        c.execute('INSERT OR REPLACE INTO product_info_icons(pid,field,custom_emoji_id,fallback_emoji) VALUES (?,?,?,?)',
                                  (pid, key, *data['icon']))
                    if keys:
                        count += 1
                else:
                    sp, ss, sw, warranty = s.info_display(pid)
                    # Toggle only visibility; editing text also copies the new warranty.
                    if data['kind'] == 'warranty_text':
                        warranty = data['warranty']
                    c.execute('INSERT OR REPLACE INTO product_info_display VALUES (?,?,?,?,?)',
                              (pid, sp, ss, data['enabled'], warranty))
                    count += 1
        c.execute('DELETE FROM product_detail_bulk WHERE token=?', (token,))
    s.send(api, cid, ('تم التطبيق على '+str(count)+' من منتجات القسم الأخرى.'
                     if decision == 'yes' else 'تم حفظ التعديل لهذا المنتج فقط.'),
           s.kb([[s.btn('رجوع لإعدادات المنتج', 'infopick:'+data['pid'])]]))


def install(s, namespace):
    old_editor = s.admin_info_editor
    old_action = namespace['action']
    old_receipt = namespace['handle_receipt']

    def info(pid, cid):
        pid = s.LEGACY.get(pid, pid)
        sp, ss, sw, warranty = s.info_display(pid)
        lines = []
        def line(key, label, value, fallback='', owner=None):
            icon = s.info_icon(owner or pid, key, fallback)
            lines.append((icon+' ' if icon else '')+'<b>'+s.esc(label)+':</b> '+value)
        if sp:
            line('price', s.tr(cid, 'السعر', 'Price'), s.price(cid, pid), '💵')
        if ss:
            line('stock', s.tr(cid, 'الكمية المتوفرة', 'Available quantity'), s.esc(s.product_stock(pid)), '📦')
        for fid, label, value, visible, owner in fields(s, pid):
            if visible:
                line('extra_'+fid, label, s.esc(value), owner=owner)
        if sw:
            line('warranty', s.tr(cid, 'الضمان', 'Warranty'), s.esc(warranty or s.tr(cid, 'غير محدد', 'Not specified')), '🛡')
        return '\n'.join(lines)

    def editor(api, cid, pid):
        if cid != s.G['ADMIN_ID']:
            return
        pid = s.LEGACY.get(pid, pid)
        with s.db() as c:
            c.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
        # Preserve the existing toggles, warranty editor, and navigation.
        old_editor(api, cid, pid)
        rows = [[s.btn('➕ إضافة حقل (اسم + قيمة)', 'detailadd:'+pid)],
                [s.btn('📊 إضافة تم البيع', 'detailsold:'+pid)],
                [s.btn('أيقونة الوصف', 'detailicon:description:'+pid)]]
        for fid, label, value, visible, owner in fields(s, pid):
            rows.append([s.btn(label, 'detailpick:'+fid)])
        rows += [[s.btn('معاينة المنتج', 'options:'+pid)], [s.btn('رجوع', 'admin:info')]]
        s.send(api, cid, '<b>حقول وصف المنتج</b>\nأضف اسم الحقل وقيمته، ثم اختر أيقونته المتحركة. الحقول محفوظة لهذا المنتج.', s.kb(rows))

    def pick(api, cid, fid):
        prepare(s)
        with s.db() as c:
            row = c.execute('SELECT pid,label,value,visible FROM product_detail_fields WHERE id=?', (fid,)).fetchone()
            c.execute('DELETE FROM admin_state WHERE cid=?', (cid,))
        if not row:
            return s.admin_info_menu(api, cid)
        pid, label, value, visible = row
        rows = [[s.btn('تعديل الاسم', 'detailset:label:'+fid), s.btn('تعديل القيمة', 'detailset:value:'+fid)],
                [s.btn('أيقونة متحركة', 'detailicon:extra_'+fid+':'+pid)],
                [s.btn('إخفاء' if visible else 'إظهار', 'detailtoggle:'+fid), s.btn('حذف الحقل', 'detaildelete:'+fid)],
                [s.btn('رجوع', 'infopick:'+pid)]]
        s.send(api, cid, '<b>'+s.esc(label)+':</b> '+s.esc(value), s.kb(rows))

    def action(api, cid, value):
        prefix, _, arg = value.partition(':')
        if prefix == 'detailbulk':
            if cid == s.G['ADMIN_ID']:
                return apply_section(s, api, cid, arg)
            return
        if prefix.startswith('detail'):
            if cid != s.G['ADMIN_ID']:
                return
            prepare(s)
            if prefix == 'detailpick':
                return pick(api, cid, arg)
            if prefix in ('detailadd', 'detailsold', 'detailset'):
                state = 'detail_new' if prefix == 'detailadd' else 'detail_sold' if prefix == 'detailsold' else 'detail_edit'
                prompt = 'أرسل اسم الحقل ثم قيمته في السطر الثاني.\nمثال:\nالمدة\nشهر' if prefix == 'detailadd' else 'أرسل القيمة التي تريد ظهورها بجانب «تم البيع».' if prefix == 'detailsold' else 'أرسل النص الجديد.'
                with s.db() as c:
                    c.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)', (cid, state, arg))
                return s.send(api, cid, prompt, s.kb([[s.btn('إلغاء', 'admin:info')]]))
            if prefix == 'detailicon':
                field, _, pid = arg.partition(':')
                if field != 'description':
                    with s.db() as c:
                        if not c.execute('SELECT 1 FROM product_detail_fields WHERE id=? AND pid=?', (field.removeprefix('extra_'),pid)).fetchone():
                            return
                with s.db() as c:
                    c.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)', (cid,'info_icon',field+':'+pid))
                return s.send(api,cid,'أرسل الأيقونة المتحركة من إيموجي تيليجرام المخصص.',s.kb([[s.btn('إلغاء','infopick:'+pid)]]))
            if prefix in ('detailtoggle', 'detaildelete'):
                with s.db() as c:
                    row = c.execute('SELECT pid FROM product_detail_fields WHERE id=?', (arg,)).fetchone()
                    if not row: return
                    if prefix == 'detailtoggle':
                        c.execute('UPDATE product_detail_fields SET visible=1-visible WHERE id=?', (arg,))
                    else:
                        c.execute('DELETE FROM product_detail_fields WHERE id=?', (arg,))
                        c.execute('DELETE FROM product_info_icons WHERE pid=? AND field=?', (row[0],'extra_'+arg))
                return editor(api,cid,row[0])
            return
        result = old_action(api,cid,value)
        if cid == s.G['ADMIN_ID'] and prefix == 'infotoggle':
            field, _, pid = arg.partition(':')
            if field == 'warranty':
                offer_section(s, api, cid, pid, 'warranty_toggle')
        return result

    def receipt(api, message):
        cid = message.get('chat',{}).get('id')
        if cid == s.G['ADMIN_ID']:
            with s.db() as c:
                row=c.execute("SELECT action,value FROM admin_state WHERE cid=? AND action IN ('detail_new','detail_sold','detail_edit')",(cid,)).fetchone()
            if row:
                text=(message.get('text') or '').strip()
                if text.startswith('/') or text in namespace.get('MENU',{}):
                    with s.db() as c:c.execute('DELETE FROM admin_state WHERE cid=?',(cid,))
                    return old_receipt(api,message)
                kind, target=row
                if kind == 'detail_new':
                    label, _, answer=text.partition('\n')
                    if not label or not answer.strip() or len(label)>80 or len(answer)>600:
                        s.send(api,cid,'أرسل الاسم (حتى 80 حرفًا) والقيمة (حتى 600 حرف) في سطرين.');return True
                elif not text or len(text)>600:
                    s.send(api,cid,'أرسل نصًا من 1 إلى 600 حرف.');return True
                prepare(s)
                with s.db() as c:
                    if kind == 'detail_edit':
                        field, _, fid=target.partition(':')
                        existing=c.execute('SELECT pid FROM product_detail_fields WHERE id=?',(fid,)).fetchone()
                        if not existing:return True
                        pid=existing[0]
                        if field not in ('label','value') or (field=='label' and len(text)>80):
                            s.send(api,cid,'اسم الحقل يجب ألا يتجاوز 80 حرفًا.');return True
                        c.execute('UPDATE product_detail_fields SET '+field+'=? WHERE id=?',(text,fid))
                    else:
                        pid=target
                        label, answer=('تم البيع',text) if kind=='detail_sold' else (label,answer.strip())
                        c.execute('INSERT INTO product_detail_fields(id,pid,label,value) VALUES (?,?,?,?)',(uuid.uuid4().hex[:12],pid,label,answer))
                    c.execute('DELETE FROM admin_state WHERE cid=?',(cid,))
                editor(api,cid,pid)
                return True
        return old_receipt(api,message)


    def saved_handler(handler, state):
        def handle(api, message):
            cid = message.get('chat', {}).get('id')
            pending = None
            if cid == s.G['ADMIN_ID']:
                with s.db() as c:
                    pending = c.execute('SELECT value FROM admin_state WHERE cid=? AND action=?',
                                        (cid, state)).fetchone()
            result = handler(api, message)
            if pending and result:
                with s.db() as c:
                    remains = c.execute('SELECT 1 FROM admin_state WHERE cid=?', (cid,)).fetchone()
                if not remains:
                    if state == 'info_warranty':
                        offer_section(s, api, cid, pending[0], 'warranty_text')
                    else:
                        field, _, pid = pending[0].partition(':')
                        if pid != '__global__' and field != 'category_prices':
                            offer_section(s, api, cid, pid, 'icon', field)
            return result
        return handle

    for handler_name, state in (('handle_info_warranty', 'info_warranty'), ('handle_info_icon', 'info_icon')):
        wrapped = saved_handler(getattr(s, handler_name), state)
        setattr(s, handler_name, wrapped)
        namespace[handler_name] = wrapped

    s.clean_product_description=clean_description
    s.info_block=info
    s.admin_info_editor=editor
    namespace['action']=namespace['handle_action']=action
    namespace['handle_receipt']=receipt
