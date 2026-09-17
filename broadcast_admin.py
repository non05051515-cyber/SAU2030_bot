"""Admin-only broadcast messaging for VEXA STORE."""
import json
from pathlib import Path

PENDING = set()
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
    sg = old_action.__globals__

    def admin_panel(api, cid):
        if cid != admin_id:
            return namespace['show_home'](api, cid)
        with sg['db']() as conn:
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
            [sg['btn']('📢 إرسال رسالة للجميع', 'admin:broadcast', style='primary')],
            [sg['btn']('➕ إضافة أيقونة', 'admin:icons', style='success')],
            [sg['btn']('🏠 الرئيسية', 'home')],
        ]))

    def action(api, cid, value):
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
                    else:
                        failed += 1
                except Exception:
                    failed += 1
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
