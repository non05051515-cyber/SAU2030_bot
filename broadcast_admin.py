"""Admin-only broadcast messaging for VEXA STORE."""
import json
import threading
import time
import uuid
from pathlib import Path

PENDING = set()
AUTO_AD_STATE = {}
USERS_FILE = Path('/data/users.json')
PRODUCT_BROADCAST = {}
DELIVERY_WORKER = None
DELIVERY_LOCK = threading.Lock()


def tick_product_broadcast(api):
    """Resume queued deliveries after a restart without blocking getUpdates."""
    if DELIVERY_WORKER and DELIVERY_LOCK.acquire(blocking=False):
        threading.Thread(target=_run_product_worker, args=(api,), daemon=True).start()


def _run_product_worker(api):
    try:
        DELIVERY_WORKER(api)
    except Exception as exc:
        print('Product broadcast worker:', type(exc).__name__, flush=True)
    finally:
        DELIVERY_LOCK.release()


def _users():
    try:
        data = json.loads(USERS_FILE.read_text(encoding='utf-8'))
        return [int(x) for x in data]
    except Exception:
        return []


def install(namespace):
    admin_id = namespace['ADMIN_ID']
    old_action = namespace['action']
    old_receipt = namespace['handle_receipt']
    # Always use storefront helpers/state. The wrapped action can belong to an
    # extension module, which made admin:broadcast fall through previously.
    import storefront as store
    sg = store.__dict__

    def prepare_broadcast_tables(conn):
        conn.execute('CREATE TABLE IF NOT EXISTS product_broadcast_drafts (cid INTEGER PRIMARY KEY, token TEXT NOT NULL, pid TEXT NOT NULL, photo TEXT, awaiting_photo INTEGER NOT NULL DEFAULT 0)')
        conn.execute('CREATE TABLE IF NOT EXISTS product_broadcast_jobs (token TEXT PRIMARY KEY, pid TEXT NOT NULL, photo TEXT, status TEXT NOT NULL)')
        conn.execute('CREATE TABLE IF NOT EXISTS product_broadcast_recipients (token TEXT NOT NULL, cid INTEGER NOT NULL, status TEXT NOT NULL DEFAULT "pending", PRIMARY KEY(token,cid))')

    def draft(cid):
        with sg['db']() as conn:
            prepare_broadcast_tables(conn)
            row = conn.execute('SELECT token,pid,photo,awaiting_photo FROM product_broadcast_drafts WHERE cid=?', (cid,)).fetchone()
        return dict(zip(('token', 'pid', 'photo', 'awaiting_photo'), row)) if row else None

    def save_draft(cid, pending):
        with sg['db']() as conn:
            prepare_broadcast_tables(conn)
            conn.execute('INSERT OR REPLACE INTO product_broadcast_drafts VALUES (?,?,?,?,?)',
                         (cid, pending['token'], pending['pid'], pending.get('photo'), int(bool(pending.get('awaiting_photo')))))

    def clear_draft(cid):
        with sg['db']() as conn:
            prepare_broadcast_tables(conn)
            conn.execute('DELETE FROM product_broadcast_drafts WHERE cid=?', (cid,))

    def admin_panel(api, cid):
        if cid != admin_id:
            return namespace['show_home'](api, cid)
        PENDING.discard(cid)
        AUTO_AD_STATE.pop(cid, None)
        PRODUCT_BROADCAST.pop(cid, None)
        clear_draft(cid)
        with sg['db']() as conn:
            conn.execute("DELETE FROM admin_state WHERE cid=? AND action IN ('price','product_text','product_photo')", (cid,))
            orders_count = conn.execute('SELECT COUNT(*) FROM orders').fetchone()[0]
            review_count = conn.execute('SELECT COUNT(*) FROM orders WHERE status="review"').fetchone()[0]
            activity_count = conn.execute('SELECT COUNT(*) FROM activity').fetchone()[0]
        text = (f'🧾 <b>لوحة إدارة VEXA</b>\n\n'
                f'الطلبات: <b>{orders_count}</b>\n'
                f'بانتظار المراجعة: <b>{review_count}</b>\n'
                f'سجل الاختيارات: <b>{activity_count}</b>')
        sg['send'](api, cid, text, sg['kb']([
            [sg['btn']('📦 الطلبات الأخيرة', 'admin:orders', style='primary')],
            [sg['btn']('👥 مستخدمو البوت', 'admin:users', style='success')],
            [sg['btn']('👀 نشاط العملاء', 'admin:activity')],
            [sg['btn']('👁 عرض/إخفاء المنتجات', 'admin:visibility', style='primary')],
            [sg['btn']('🖼️ صورة المنتج', 'admin:photos')],
            [sg['btn']('✏️ تعديل اسم المنتج', 'admin:editname')],
            [sg['btn']('📝 تعديل وصف المنتج', 'admin:editdesc')],
            [sg['btn']('➕ إضافة منتج', 'admin:addproduct', style='success'), sg['btn']('📦 منتجاتي', 'admin:myproducts', style='primary')],
            [sg['btn']('📦 تعديل توفر المنتج', 'admin:stock', style='primary')],
            [sg['btn']('✏️ تعديل سعر منتج', 'admin:prices', style='primary')],
            [sg['btn']('🎛 إعداد عرض بيانات المنتج', 'admin:info', style='primary')],
            [sg['btn']('📢 إرسال رسالة للجميع', 'admin:broadcast', style='primary')],
            [sg['btn']('🛍 إرسال منتج للجميع', 'admin:product_broadcast', style='primary')],
            [sg['btn']('📊 الإحصائيات', 'admin:stats')],
            [sg['btn']('📣 إعلان تلقائي للقروب', 'admin:autoad', style='success')],
            [sg['btn']('➕ إضافة أيقونة', 'admin:icons', style='success')],
            [sg['btn']('🏠 الرئيسية', 'home')],
        ]))

    def categories(api, cid):
        with sg['db']() as conn:
            custom = conn.execute('SELECT cid,name FROM admin_categories ORDER BY rowid').fetchall()
        entries = [(pid, product['name']) for pid, product in sg['G']['PRODUCTS'].items()] + custom
        rows = [[sg['btn'](title, 'pbcat:' + pid)] for pid, title in entries if sg['category_visible'](pid)]
        return sg['send'](api, cid, '🛍 <b>إرسال منتج للجميع</b>\n\nاختر القسم:', sg['kb'](rows + [[sg['btn']('↩️ لوحة الإدارة', 'admin')]]))

    def products_in_category(category):
        if category in sg['G']['PRODUCTS']:
            ids = [v['id'] for v in sg['VARIANTS'].values() if v['category'] == category]
            return ids or [category]
        if not sg['custom_category'](category):
            return []
        with sg['db']() as conn:
            return [r[0] for r in conn.execute('SELECT pid FROM admin_products WHERE category_id=? ORDER BY rowid', (category,))]

    def product_card(api, cid, pid, photo=None):
        title = sg['esc'](sg['name'](pid, cid))
        description = sg['esc'](sg['product_description'](pid, cid))
        status = '✅ متوفر' if sg['in_stock'](pid) else '🔴 غير متوفر حاليًا'
        details = sg['info_block'](pid, cid)
        if sg['info_display'](pid)[0]:
            details = details.partition('\n')[2]
        price_line = '💵 <b>السعر:</b> ' + sg['esc'](sg['price'](cid, pid))
        info = price_line + ('\n' + details if details else '')
        body = f'🛍 <b>{title}</b>\n\n{info}\n{status}'
        if description:
            body += '\n\n' + description
        buttons = [[sg['btn']('🛒 الذهاب للمنتج', 'item:' + pid, style='primary')]]
        if sg['can_order'](pid):
            buttons.append([sg['btn']('⚡ شراء مباشرة', 'buy:' + pid, style='success')])
        photo = photo or sg['saved_product_photo'](pid)
        if photo:
            # Telegram photo captions are limited to 1024 characters. Keep the
            # purchase buttons on the same message as the picture.
            caption = f'🛍 <b>{title}</b>\n\n{info}\n{status}'
            if description and len(caption) + len(description) < 850:
                caption += '\n\n' + description
            result = api.call('sendPhoto', chat_id=cid, photo=photo,
                              caption=caption, parse_mode='HTML', reply_markup=sg['kb'](buttons))
            if result:
                return result
        return sg['send'](api, cid, body[:3900], sg['kb'](buttons))

    def product_preview(api, cid, pending):
        product_card(api, cid, pending['pid'], pending.get('photo'))
        token = pending['token']
        return sg['send'](api, cid, 'هذه معاينة الإعلان. يمكنك إضافة صورة خاصة لهذا الإرسال أو تأكيده.',
                          sg['kb']([[sg['btn']('🖼️ إضافة صورة للإعلان', 'pbphoto:' + token)],
                                    [sg['btn']('✅ تأكيد الإرسال', 'pbconfirm:' + token, style='success')],
                                    [sg['btn']('❌ إلغاء', 'admin:product_broadcast')]]))

    def deliver_queued(api):
        with sg['db']() as conn:
            prepare_broadcast_tables(conn)
            jobs = conn.execute('SELECT token,pid,photo FROM product_broadcast_jobs WHERE status IN ("queued","running") ORDER BY rowid').fetchall()
        for token, pid, photo in jobs:
            with sg['db']() as conn:
                conn.execute('UPDATE product_broadcast_jobs SET status="running" WHERE token=?', (token,))
                recipients = [row[0] for row in conn.execute('SELECT cid FROM product_broadcast_recipients WHERE token=? AND status="pending" ORDER BY cid', (token,))]
            for user_id in recipients:
                try:
                    result = product_card(api, user_id, pid, photo)
                except Exception as exc:
                    print('Product delivery error:', type(exc).__name__, flush=True)
                    result = None
                with sg['db']() as conn:
                    conn.execute('UPDATE product_broadcast_recipients SET status=? WHERE token=? AND cid=?', ('sent' if result else 'failed', token, user_id))
                    conn.execute('INSERT INTO user_delivery_status(cid,departed,updated_at) VALUES (?,?,?) ON CONFLICT(cid) DO UPDATE SET departed=excluded.departed, updated_at=excluded.updated_at', (user_id, 0 if result else 1, sg['now_saudi']()))
                time.sleep(0.05)
            with sg['db']() as conn:
                ok = conn.execute('SELECT COUNT(*) FROM product_broadcast_recipients WHERE token=? AND status="sent"', (token,)).fetchone()[0]
                failed = conn.execute('SELECT COUNT(*) FROM product_broadcast_recipients WHERE token=? AND status="failed"', (token,)).fetchone()[0]
                conn.execute('UPDATE product_broadcast_jobs SET status="done" WHERE token=?', (token,))
                conn.execute('INSERT OR REPLACE INTO broadcast_stats(id,sent,failed,created_at) VALUES (1,?,?,?)', (ok, failed, sg['now_saudi']()))
            sg['send'](api, admin_id, f'✅ اكتمل إرسال المنتج.\nوصل إلى: <b>{ok}</b>\nتعذر الإرسال إلى: <b>{failed}</b>', sg['kb']([[sg['btn']('↩️ لوحة الإدارة', 'admin')]]))

    global DELIVERY_WORKER
    DELIVERY_WORKER = deliver_queued

    def action(api, cid, value):
        if value.startswith(('admin:product_broadcast', 'pbcat:', 'pbpick:', 'pbphoto:', 'pbconfirm:')) and cid != admin_id:
            return namespace['show_home'](api, cid)
        if cid == admin_id and value == 'admin:product_broadcast':
            PRODUCT_BROADCAST.pop(cid, None)
            clear_draft(cid)
            return categories(api, cid)
        if cid == admin_id and value.startswith('pbcat:'):
            category = value.split(':', 1)[1]
            ids = [pid for pid in products_in_category(category) if sg['product_visible'](pid)]
            rows = [[sg['btn'](sg['name'](pid, cid), 'pbpick:' + pid)] for pid in ids]
            return sg['send'](api, cid, 'اختر المنتج الذي تريد إرساله:' if rows else 'لا توجد منتجات ظاهرة في هذا القسم.', sg['kb'](rows + [[sg['btn']('↩️ الأقسام', 'admin:product_broadcast')]]))
        if cid == admin_id and value.startswith('pbpick:'):
            pid = value.split(':', 1)[1]
            valid = pid in sg['VARIANTS'] or bool(sg['custom_product'](pid)) or (pid in sg['G']['PRODUCTS'] and products_in_category(pid) == [pid])
            if not valid or not sg['product_visible'](pid):
                return categories(api, cid)
            token = uuid.uuid4().hex[:12]
            pending = {'token': token, 'pid': pid}
            save_draft(cid, pending)
            return product_preview(api, cid, pending)
        if cid == admin_id and value.startswith('pbphoto:'):
            pending = draft(cid)
            if not pending or pending['token'] != value.split(':', 1)[1]:
                return categories(api, cid)
            pending['awaiting_photo'] = True
            save_draft(cid, pending)
            return sg['send'](api, cid, '🖼️ أرسل الآن صورة الإعلان. ستظهر مع المنتج وزر الشراء في المعاينة وعند الإرسال.',
                              sg['kb']([[sg['btn']('❌ إلغاء', 'admin:product_broadcast')]]))
        if cid == admin_id and value.startswith('pbconfirm:'):
            token = value.split(':', 1)[1]
            pending = draft(cid)
            if not pending or pending['token'] != token:
                with sg['db']() as conn:
                    prepare_broadcast_tables(conn)
                    row = conn.execute('SELECT status FROM product_broadcast_jobs WHERE token=?', (token,)).fetchone()
                if row:
                    return sg['send'](api, cid, 'الإرسال قيد التنفيذ.' if row[0] != 'done' else 'تم إرسال هذا الإعلان مسبقًا.')
                return sg['send'](api, cid, 'انتهت صلاحية التأكيد. اختر المنتج مرة أخرى.', sg['kb']([[sg['btn']('🛍 اختيار منتج', 'admin:product_broadcast')]]))
            if pending.get('awaiting_photo'):
                return sg['send'](api, cid, 'أرسل الصورة أولًا أو ألغِ العملية واختر المنتج من جديد.')
            pid = pending['pid']
            if not sg['product_visible'](pid):
                return sg['send'](api, cid, 'المنتج مخفي الآن. لم يتم الإرسال.')
            with sg['db']() as conn:
                prepare_broadcast_tables(conn)
                conn.execute('INSERT OR IGNORE INTO product_broadcast_jobs VALUES (?,?,?,"queued")', (token, pid, pending.get('photo')))
                conn.executemany('INSERT OR IGNORE INTO product_broadcast_recipients(token,cid) VALUES (?,?)',
                                 [(token, user_id) for user_id in set(_users()) - {admin_id}])
                conn.execute('DELETE FROM product_broadcast_drafts WHERE cid=? AND token=?', (cid, token))
            result = sg['send'](api, cid, '⏳ بدأ إرسال المنتج للمستخدمين. سأرسل لك عدد من وصلتهم الرسالة عند الانتهاء.')
            tick_product_broadcast(api)
            return result
        if cid == admin_id and value in ('admin:visibility', 'admin:chatgptvis'):
            return sg['visibility_categories'](api, cid)
        if cid == admin_id and value.startswith('viscat:'):
            return sg['visibility_products'](api, cid, value.split(':', 1)[1])
        if cid == admin_id and value.startswith('vistoggle:'):
            return sg['toggle_visibility'](api, cid, value.split(':', 1)[1])
        if cid == admin_id and value.startswith('chatgptvis:'):
            return sg['toggle_visibility'](api, cid, value.split(':', 1)[1])

        if cid == admin_id and not value.startswith(('txtcat:', 'txtpick:', 'txtedit:', 'photocat:', 'photopick:', 'photodel:')):
            with sg['db']() as conn:
                conn.execute("DELETE FROM admin_state WHERE cid=? AND action IN ('product_text','product_photo')", (cid,))
        if cid == admin_id and (value in ('admin:prices', 'admin:stock', 'admin:info', 'admin:editname', 'admin:editdesc', 'admin:photos') or value.startswith(('pricecat:', 'pricepick:', 'priceedit:', 'stockcat:', 'stockpick:', 'stockset:', 'txtcat:', 'txtpick:', 'txtedit:', 'photocat:', 'photopick:', 'photodel:'))):
            PENDING.discard(cid)
            AUTO_AD_STATE.pop(cid, None)
        if cid == admin_id and value == 'admin:users':
            users = sorted(set(_users()))
            with sg['db']() as conn:
                conn.execute('CREATE TABLE IF NOT EXISTS user_delivery_status (cid INTEGER PRIMARY KEY, departed INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL DEFAULT "")')
                departed_ids = {row[0] for row in conn.execute('SELECT cid FROM user_delivery_status WHERE departed=1').fetchall()}
                recent_rows = conn.execute('SELECT cid, MAX(created_at) FROM activity GROUP BY cid ORDER BY MAX(created_at) DESC LIMIT 15').fetchall()
            total = len(users)
            available = max(total - len(departed_ids.intersection(users)), 0)
            recent = [(uid, ts) for uid, ts in recent_rows if uid != admin_id]
            lines = ['👥 <b>مستخدمو البوت</b>', '', f'إجمالي المستخدمين: <b>{total}</b>', f'🟢 المتاحون: <b>{available}</b>', f'🚪 غادروا/حظروا البوت: <b>{len(departed_ids.intersection(users))}</b>']
            if recent:
                lines += ['', '🕒 <b>آخر نشاط مسجل:</b>']
                for uid, ts in recent[:10]:
                    lines.append(f'• <code>{uid}</code> — {sg["esc"](ts)}')
            lines += ['', 'ℹ️ تيليجرام لا يتيح للبوت معرفة من هو Online الآن؛ هذه القائمة تعتمد على آخر تفاعل مسجل.']
            return sg['send'](api, cid, '\n'.join(lines), sg['kb']([[sg['btn']('🔄 تحديث', 'admin:users')], [sg['btn']('↩️ لوحة الإدارة', 'admin')]]))
        if cid == admin_id and value == 'admin:stats':
            try:
                users = set(_users())
            except Exception:
                users = set()
            with sg['db']() as conn:
                conn.execute('CREATE TABLE IF NOT EXISTS user_delivery_status (cid INTEGER PRIMARY KEY, departed INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL DEFAULT "")')
                conn.execute('CREATE TABLE IF NOT EXISTS broadcast_stats (id INTEGER PRIMARY KEY CHECK(id=1), sent INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT "")')
                departed = conn.execute('SELECT COUNT(*) FROM user_delivery_status WHERE departed=1').fetchone()[0]
                row = conn.execute('SELECT sent,failed,created_at FROM broadcast_stats WHERE id=1').fetchone()
            total = len(users)
            available = max(total - departed, 0)
            sent, failed, created = row if row else (0, 0, 'لا توجد رسالة جماعية بعد')
            text = (f'📊 <b>إحصائيات البوت</b>\n\n'
                    f'👥 إجمالي المستخدمين: <b>{total}</b>\n'
                    f'🟢 المتاحون: <b>{available}</b>\n'
                    f'🚪 غادروا البوت: <b>{departed}</b>\n\n'
                    f'📢 <b>آخر رسالة جماعية</b>\n'
                    f'✅ تم الإرسال: <b>{sent}</b>\n'
                    f'❌ فشل الإرسال: <b>{failed}</b>\n'
                    f'🕒 {sg["esc"](created)}')
            return sg['send'](api, cid, text, sg['kb']([[sg['btn']('🔄 تحديث', 'admin:stats')], [sg['btn']('↩️ لوحة الإدارة', 'admin')]]))
        if cid == admin_id and value == 'admin:autoad':
            AUTO_AD_STATE[cid] = {'step':'target'}
            return sg['send'](api,cid,'📣 <b>الإعلان التلقائي</b>\n\nأرسل معرف القروب مثل <code>@groupname</code> أو رقم القروب <code>-100...</code>.\nيجب أن يكون البوت داخل القروب. ',sg['kb']([[sg['btn']('⏹ إيقاف','admin:autoad_stop',style='danger')],[sg['btn']('❌ إلغاء','admin:autoad_cancel')]]))
        if cid == admin_id and value == 'admin:autoad_stop':
            with sg['db']() as conn:
                conn.execute('CREATE TABLE IF NOT EXISTS auto_ads (id INTEGER PRIMARY KEY,target TEXT,source_chat INTEGER,message_id INTEGER,interval_sec INTEGER,next_at REAL,enabled INTEGER)')
                conn.execute('UPDATE auto_ads SET enabled=0 WHERE id=1')
            AUTO_AD_STATE.pop(cid,None)
            return admin_panel(api,cid)
        if cid == admin_id and value == 'admin:autoad_cancel':
            AUTO_AD_STATE.pop(cid,None); return admin_panel(api,cid)
        if cid == admin_id and value.startswith('admin:autoad_interval:'):
            state=AUTO_AD_STATE.get(cid)
            if not state or state.get('step')!='interval': return admin_panel(api,cid)
            hours=int(value.rsplit(':',1)[1]); import time
            with sg['db']() as conn:
                conn.execute('CREATE TABLE IF NOT EXISTS auto_ads (id INTEGER PRIMARY KEY,target TEXT,source_chat INTEGER,message_id INTEGER,interval_sec INTEGER,next_at REAL,enabled INTEGER)')
                conn.execute('INSERT OR REPLACE INTO auto_ads VALUES (1,?,?,?,?,?,1)',(state['target'],cid,state['message_id'],hours*3600,time.time()))
            AUTO_AD_STATE.pop(cid,None)
            return sg['send'](api,cid,f'✅ تم تشغيل الإعلان كل <b>{hours} ساعة</b>.',sg['kb']([[sg['btn']('⏹ إيقاف','admin:autoad_stop',style='danger')],[sg['btn']('↩️ لوحة الإدارة','admin')]]))
        if cid == admin_id and value == 'admin:broadcast':
            PENDING.add(cid)
            return sg['send'](
                api, cid,
                '📢 <b>إرسال رسالة للجميع</b>\n\nأرسل الآن الرسالة التي تريد إرسالها لجميع مستخدمي البوت.\nيمكنك إرسال نص أو صورة مع تعليق.',
                sg['kb']([[sg['btn']('❌ إلغاء', 'admin:broadcast_cancel')]])
            )
        if cid == admin_id and value == 'admin:broadcast_cancel':
            PENDING.discard(cid)
            return admin_panel(api, cid)
        return old_action(api, cid, value)

    def handle_receipt(api, message):
        cid = message.get('chat', {}).get('id')
        pending = draft(cid) if cid == admin_id else None
        if pending and pending.get('awaiting_photo'):
            photos = message.get('photo') or []
            if not photos:
                sg['send'](api, cid, 'أرسل الصورة كصورة عادية من تيليجرام، وليس كملف.')
                return True
            pending['photo'] = photos[-1]['file_id']
            pending.pop('awaiting_photo', None)
            save_draft(cid, pending)
            return product_preview(api, cid, pending) or True
        state=AUTO_AD_STATE.get(cid)
        if cid==admin_id and state:
            if state.get('step')=='target':
                target=(message.get('text') or '').strip()
                if not target or (not target.startswith('@') and not target.startswith('-100')):
                    sg['send'](api,cid,'أرسل <code>@معرف_القروب</code> أو رقم القروب الذي يبدأ بـ <code>-100</code>.'); return True
                try: api.call('getChat',chat_id=target)
                except Exception:
                    sg['send'](api,cid,'❌ لم أستطع الوصول للقروب. تأكد أن البوت مضاف وأن المعرف صحيح.'); return True
                state.update(step='message',target=target)
                sg['send'](api,cid,'✅ أرسل الآن الرسالة الدعائية. يمكن أن تكون نصًا أو صورة مع تعليق.'); return True
            if state.get('step')=='message':
                state.update(step='interval',message_id=message['message_id'])
                sg['send'](api,cid,'⏱ <b>اختر وقت التكرار:</b>',sg['kb']([[sg['btn']('كل ساعة','admin:autoad_interval:1'),sg['btn']('كل ساعتين','admin:autoad_interval:2')],[sg['btn']('كل 6 ساعات','admin:autoad_interval:6'),sg['btn']('كل 12 ساعة','admin:autoad_interval:12')],[sg['btn']('كل 24 ساعة','admin:autoad_interval:24')],[sg['btn']('❌ إلغاء','admin:autoad_cancel')]])); return True
        if cid == admin_id and cid in PENDING:
            text = message.get('text', '')
            if text.startswith('/'):
                PENDING.discard(cid)
                return False
            users = [u for u in _users() if u != admin_id]
            ok = 0
            failed = 0
            for user_id in users:
                try:
                    result = api.call('copyMessage', chat_id=user_id, from_chat_id=cid,
                                      message_id=message['message_id'])
                    if result:
                        ok += 1
                        with sg['db']() as conn:
                            conn.execute('INSERT INTO user_delivery_status(cid,departed,updated_at) VALUES (?,?,?) ON CONFLICT(cid) DO UPDATE SET departed=excluded.departed, updated_at=excluded.updated_at', (user_id, 0, sg['now_saudi']()))
                    else:
                        failed += 1
                        with sg['db']() as conn:
                            conn.execute('INSERT INTO user_delivery_status(cid,departed,updated_at) VALUES (?,?,?) ON CONFLICT(cid) DO UPDATE SET departed=excluded.departed, updated_at=excluded.updated_at', (user_id, 1, sg['now_saudi']()))
                except Exception:
                    failed += 1
                    with sg['db']() as conn:
                        conn.execute('INSERT INTO user_delivery_status(cid,departed,updated_at) VALUES (?,?,?) ON CONFLICT(cid) DO UPDATE SET departed=excluded.departed, updated_at=excluded.updated_at', (user_id, 1, sg['now_saudi']()))
            with sg['db']() as conn:
                conn.execute('INSERT OR REPLACE INTO broadcast_stats(id,sent,failed,created_at) VALUES (1,?,?,?)', (ok, failed, sg['now_saudi']()))
            PENDING.discard(cid)
            sg['send'](api, cid,
                       f'✅ <b>تم الإرسال</b>\n\nوصلت الرسالة إلى: <b>{ok}</b>\nتعذر الإرسال إلى: <b>{failed}</b>',
                       sg['kb']([[sg['btn']('↩️ لوحة الإدارة', 'admin')]]))
            return True
        return old_receipt(api, message)

    sg['admin_panel'] = admin_panel
    namespace['action'] = action
    namespace['handle_action'] = action
    namespace['handle_receipt'] = handle_receipt


def tick_auto_ads(api):
    import time, storefront as sg
    now=time.time()
    try:
        with sg.db() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS auto_ads (id INTEGER PRIMARY KEY,target TEXT,source_chat INTEGER,message_id INTEGER,interval_sec INTEGER,next_at REAL,enabled INTEGER)')
            row=conn.execute('SELECT target,source_chat,message_id,interval_sec,next_at FROM auto_ads WHERE id=1 AND enabled=1').fetchone()
        if not row or row[4]>now: return
        target,source_chat,message_id,interval_sec,_=row
        api.call('copyMessage',chat_id=target,from_chat_id=source_chat,message_id=message_id)
        with sg.db() as conn: conn.execute('UPDATE auto_ads SET next_at=? WHERE id=1',(now+interval_sec,))
    except Exception as exc:
        print('Auto ad error:',type(exc).__name__)
