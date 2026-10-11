"""Persistent ordering for visible top-level VEXA products and categories."""
import json

TABLE = 'product_display_order'


def _ensure(store):
    with store.db() as conn:
        conn.execute(f'CREATE TABLE IF NOT EXISTS {TABLE} (scope TEXT NOT NULL, entry_id TEXT NOT NULL, position INTEGER NOT NULL, PRIMARY KEY(scope,entry_id))')


def _base_ids(store, scope):
    if scope == 'category':
        ids = [str(pid) for pid in store.G['PRODUCTS'] if store.category_visible(pid)]
        with store.db() as conn:
            custom = conn.execute('SELECT cid FROM admin_categories ORDER BY rowid').fetchall()
        ids.extend(str(row[0]) for row in custom if store.category_visible(str(row[0])))
    else:
        with store.db() as conn:
            rows = conn.execute('SELECT pid FROM admin_products WHERE category_id IS NULL OR trim(COALESCE(category_id,""))="" ORDER BY rowid').fetchall()
        ids = [str(row[0]) for row in rows if store.product_visible(str(row[0]))]
    return list(dict.fromkeys(ids))


def _ordered(store, scope, ids=None):
    _ensure(store)
    ids = list(_base_ids(store, scope) if ids is None else ids)
    with store.db() as conn:
        saved = dict(conn.execute(f'SELECT entry_id,position FROM {TABLE} WHERE scope=?', (scope,)).fetchall())
    original = {entry_id: index for index, entry_id in enumerate(ids)}
    return sorted(ids, key=lambda entry_id: (saved.get(entry_id, 10**9 + original[entry_id]), original[entry_id]))


def _move(store, scope, entry_id, position):
    ids = _ordered(store, scope)
    if entry_id not in ids or position < 1 or position > len(ids):
        return False, len(ids)
    ids.remove(entry_id)
    ids.insert(position - 1, entry_id)
    _ensure(store)
    with store.db() as conn:
        conn.executemany(f'INSERT OR REPLACE INTO {TABLE}(scope,entry_id,position) VALUES (?,?,?)',
                         [(scope, item, index + 1) for index, item in enumerate(ids)])
    return True, len(ids)


def _display_name(store, cid, scope, entry_id):
    if scope == 'category':
        return store.category_label(entry_id, cid)
    cp = store.custom_product(entry_id)
    label = store.name(entry_id, cid) if cp else entry_id
    return label


def _prompt(api, cid, store, scope, entry_id, publish_ids=None, origin='admin'):
    ids = _ordered(store, scope)
    if entry_id not in ids:
        ids.append(entry_id)
    lines = ['📍 <b>اختر مكان الظهور</b>', '',
             ('القسم' if scope == 'category' else 'المنتج') + ': <b>' + store.esc(_display_name(store, cid, scope, entry_id)) + '</b>',
             'أرسل رقم الترتيب الذي تريده (1 = أول القائمة).']
    lines.extend(f'{i}. {store.esc(_display_name(store, cid, scope, item))}' for i, item in enumerate(ids, 1))
    payload = {'scope': scope, 'entry_id': entry_id, 'publish_ids': list(publish_ids or []), 'origin': origin}
    with store.db() as conn:
        conn.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)',
                     (cid, 'product_order_position', json.dumps(payload, ensure_ascii=False)))
    store.send(api, cid, '\n'.join(lines), store.kb([
        [store.btn('❌ إلغاء', 'product_order:cancel')],
        [store.btn('↩️ لوحة الإدارة', 'admin')]
    ]))


def prompt_position(api, cid, scope, entry_id, publish_ids=None):
    import storefront as store
    if cid != store.G['ADMIN_ID'] or scope not in ('category', 'direct'):
        return
    return _prompt(api, cid, store, scope, entry_id, publish_ids, origin='added')


def menu(api, cid, store):
    rows = [[store.btn('📁 ترتيب الأقسام الظاهرة', 'product_order:list:category')],
            [store.btn('🛍 ترتيب المنتجات المباشرة', 'product_order:list:direct')],
            [store.btn('↩️ لوحة الإدارة', 'admin')]]
    return store.send(api, cid, '↕️ <b>ترتيب الظهور</b>\n\nاختر القائمة التي تريد إعادة ترتيبها:', store.kb(rows))


def _show_list(api, cid, store, scope):
    ids = _ordered(store, scope)
    if not ids:
        return store.send(api, cid, 'لا توجد عناصر في هذه القائمة.', store.kb([
            [store.btn('↩️ ترتيب الظهور', 'admin:product_order')]]))
    rows = [[store.btn(f'{i}. {_display_name(store,cid,scope,item)}', 'product_order:pick:' + scope + ':' + item)]
            for i, item in enumerate(ids, 1)]
    rows.append([store.btn('↩️ ترتيب الظهور', 'admin:product_order')])
    return store.send(api, cid, 'اختر المنتج أو القسم، ثم أرسل رقمه الجديد:', store.kb(rows))


def install(store, namespace):
    _ensure(store)
    old_send = store.send
    old_action = namespace['action']
    old_input = namespace['handle_admin_product']

    def ordered_send(api, cid, text, markup=None):
        if isinstance(text, str) and ('اختر الخدمة:' in text or 'Choose a service:' in text) and isinstance(markup, dict):
            keyboard = markup.get('inline_keyboard')
            if isinstance(keyboard, list):
                categories, direct, other = [], [], []
                for row in keyboard:
                    for button in row:
                        callback = str(button.get('callback_data', ''))
                        if callback.startswith('product:'):
                            categories.append((callback.split(':', 1)[1], button))
                        elif callback.startswith('options:'):
                            direct.append((callback.split(':', 1)[1], button))
                        else:
                            other.append([button])
                cat_ids = _ordered(store, 'category', [item for item, _ in categories])
                direct_ids = _ordered(store, 'direct', [item for item, _ in direct])
                cat_map, direct_map = dict(categories), dict(direct)
                new_rows = [[cat_map[x] for x in cat_ids[i:i+3]] for i in range(0, len(cat_ids), 3)]
                new_rows += [[direct_map[x]] for x in direct_ids]
                markup['inline_keyboard'] = new_rows + other
        return old_send(api, cid, text, markup)

    def action(api, cid, value):
        if cid == store.G['ADMIN_ID'] and value == 'admin:product_order':
            return menu(api, cid, store)
        if cid == store.G['ADMIN_ID'] and value.startswith('product_order:list:'):
            scope = value.rsplit(':', 1)[1]
            if scope in ('category', 'direct'):
                return _show_list(api, cid, store, scope)
        if cid == store.G['ADMIN_ID'] and value.startswith('product_order:pick:'):
            parts = value.split(':', 3)
            if len(parts) == 4 and parts[2] in ('category', 'direct'):
                return _prompt(api, cid, store, parts[2], parts[3])
        if cid == store.G['ADMIN_ID'] and value == 'product_order:cancel':
            with store.db() as conn:
                conn.execute('DELETE FROM admin_state WHERE cid=? AND action=?', (cid, 'product_order_position'))
            return menu(api, cid, store)
        return old_action(api, cid, value)

    def handle_input(api, message):
        cid = message.get('chat', {}).get('id')
        if cid == store.G['ADMIN_ID']:
            with store.db() as conn:
                state = conn.execute('SELECT action,value FROM admin_state WHERE cid=?', (cid,)).fetchone()
            if state and state[0] == 'product_order_position':
                try:
                    payload = json.loads(state[1] or '{}')
                except Exception:
                    payload = {}
                raw = (message.get('text') or '').strip().translate(str.maketrans('٠١٢٣٤٥٦٧٨٩', '0123456789'))
                try:
                    position = int(raw)
                except ValueError:
                    store.send(api, cid, 'أرسل رقمًا صحيحًا للمكان المطلوب.')
                    return True
                scope = payload.get('scope')
                ok, total = _move(store, scope, payload.get('entry_id', ''), position)
                if not ok:
                    store.send(api, cid, f'اختر رقمًا من 1 إلى {total}.')
                    return True
                with store.db() as conn:
                    conn.execute('DELETE FROM admin_state WHERE cid=? AND action=?', (cid, 'product_order_position'))
                if payload.get('publish_ids'):
                    ids = payload['publish_ids']
                    store.send(api, cid, '✅ تم ترتيب العنصر. هل تريد نشر المنتجات الجديدة في القناة الآن أم لاحقًا؟',
                               store.kb([[store.btn('📣 نشر الآن', 'channel:publish_new:' + ','.join(ids), style='success')],
                                         [store.btn('🕒 لاحقًا', 'channel:defer_new:' + ','.join(ids))]]))
                else:
                    store.send(api, cid, '✅ تم حفظ الترتيب الجديد.', store.kb([
                        [store.btn('↕️ ترتيب عنصر آخر', 'admin:product_order')],
                        [store.btn('↩️ لوحة الإدارة', 'admin')]]))
                return True
        return old_input(api, message)

    store.send = ordered_send
    namespace['action'] = action
    namespace['handle_admin_product'] = handle_input
