"""Admin-only broadcast messaging for VEXA STORE."""
import json
from pathlib import Path

PENDING = set()
AUTO_AD_STATE = {}
USERS_FILE = Path('/data/users.json')


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

    def admin_panel(api, cid):
        if cid != admin_id:
            return namespace['show_home'](api, cid)
        PENDING.discard(cid)
        AUTO_AD_STATE.pop(cid, None)
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
            [sg['btn']('👀 نشاط العملاء', 'admin:activity')],
            [sg['btn']('🖼️ صورة المنتج', 'admin:photos')],
            [sg['btn']('✏️ تعديل اسم المنتج', 'admin:editname')],
            [sg['btn']('📝 تعديل وصف المنتج', 'admin:editdesc')],
            [sg['btn']('➕ إضافة منتج', 'admin:addproduct', style='success'), sg['btn']('📦 منتجاتي', 'admin:myproducts', style='primary')],
            [sg['btn']('📦 تعديل توفر المنتج', 'admin:stock', style='primary')],
            [sg['btn']('✏️ تعديل سعر منتج', 'admin:prices', style='primary')],
            [sg['btn']('🎛 إعداد عرض بيانات المنتج', 'admin:info', style='primary')],
            [sg['btn']('📢 إرسال رسالة للجميع', 'admin:broadcast', style='primary')],
            [sg['btn']('📊 الإحصائيات', 'admin:stats')],
            [sg['btn']('📣 إعلان تلقائي للقروب', 'admin:autoad', style='success')],
            [sg['btn']('➕ إضافة أيقونة', 'admin:icons', style='success')],
            [sg['btn']('🏠 الرئيسية', 'home')],
        ]))

    def action(api, cid, value):
        if cid == admin_id and not value.startswith(('txtcat:', 'txtpick:', 'txtedit:', 'photocat:', 'photopick:', 'photodel:')):
            with sg['db']() as conn:
                conn.execute("DELETE FROM admin_state WHERE cid=? AND action IN ('product_text','product_photo')", (cid,))
        if cid == admin_id and (value in ('admin:prices', 'admin:stock', 'admin:info', 'admin:editname', 'admin:editdesc', 'admin:photos') or value.startswith(('pricecat:', 'pricepick:', 'priceedit:', 'stockcat:', 'stockpick:', 'stockset:', 'txtcat:', 'txtpick:', 'txtedit:', 'photocat:', 'photopick:', 'photodel:'))):
            PENDING.discard(cid)
            AUTO_AD_STATE.pop(cid, None)
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


