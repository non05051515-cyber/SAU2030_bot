"""Admin-only targeted private messages; isolated from broadcasts."""
import threading

_STATE = {}
_LOCK = threading.Lock()
MAX_RECIPIENTS = 20

def install(namespace):
    old_action = namespace['action']
    admin_id = namespace['ADMIN_ID']
    import storefront

    def show(api, cid, message, rows=None):
        return storefront.send(api, cid, message, storefront.kb(rows or [[storefront.btn('↩️ لوحة التحكم', 'admin')]]))

    def action(api, cid, value):
        if not value.startswith('private_send:'):
            return old_action(api, cid, value)
        if cid != admin_id:
            return namespace['show_home'](api, cid)
        command = value.split(':', 1)[1]
        if command == 'start':
            _STATE[cid] = {'step': 'count'}
            return show(api, cid, '✉️ <b>إرسال رسالة خاصة</b>\n\nاختر عدد العملاء (1 إلى 20):',
                        [[storefront.btn(str(i), 'private_send:count:' + str(i)) for i in range(j, min(j+4, 21))]
                         for j in range(1, 21, 4)] + [[storefront.btn('❌ إلغاء', 'private_send:cancel')]])
        if command == 'cancel':
            _STATE.pop(cid, None)
            return storefront.admin_panel(api, cid)
        if command.startswith('count:'):
            try: count = int(command.split(':')[1])
            except ValueError: return show(api, cid, 'عدد غير صالح.')
            if not 1 <= count <= MAX_RECIPIENTS: return show(api, cid, 'عدد غير صالح.')
            _STATE[cid] = {'step': 'recipient', 'count': count, 'recipients': []}
            return show(api, cid, '👤 أرسل الآن رقم Telegram ID للعميل رقم 1.\n\nيجب أن يكون العميل قد بدأ المحادثة مع البوت.',
                        [[storefront.btn('❌ إلغاء', 'private_send:cancel')]])
        state = _STATE.get(cid)
        if not state: return action(api, cid, 'private_send:start')
        if command == 'confirm' and state.get('step') == 'confirm':
            with _LOCK:
                state = _STATE.pop(cid, None)
            if not state: return show(api, cid, 'انتهت الجلسة.')
            success, failed = 0, []
            for target in state['recipients']:
                try:
                    result = api.call('copyMessage', chat_id=target, from_chat_id=cid, message_id=state['message_id'])
                    if result is None or result is False: raise RuntimeError('Telegram did not confirm delivery')
                    success += 1
                except Exception:
                    failed.append(str(target))
            summary = '✅ اكتمل الإرسال.\nتم الإرسال: <b>%s</b>\nفشل: <b>%s</b>' % (success, len(failed))
            if failed: summary += '\nمعرفات لم تستلم: <code>' + ', '.join(failed) + '</code>'
            return show(api, cid, summary)
        return show(api, cid, 'اختر إجراءً صحيحًا.')

    def handle_message(api, message):
        cid = message.get('chat', {}).get('id')
        if cid != admin_id or cid not in _STATE: return False
        state = _STATE[cid]
        if (message.get('text') or '').startswith('/'):
            _STATE.pop(cid, None)
            return False
        if state['step'] == 'recipient':
            raw = (message.get('text') or '').strip()
            if not raw.isdecimal():
                show(api, cid, 'أرسل Telegram ID رقميًا، وليس @username.\nيمكن معرفة الرقم من قائمة مستخدمي البوت.')
                return True
            target = int(raw)
            if target <= 0 or target in state['recipients']:
                show(api, cid, 'رقم غير صالح أو مكرر، أرسل رقمًا آخر.')
                return True
            state['recipients'].append(target)
            if len(state['recipients']) < state['count']:
                show(api, cid, '👤 أرسل Telegram ID للعميل رقم %s من %s.' % (len(state['recipients'])+1, state['count']))
            else:
                state['step'] = 'message'
                show(api, cid, '📝 أرسل الآن الرسالة التي تريد توصيلها للعملاء. يمكن إرسال نص أو صورة أو ملف.')
            return True
        if state['step'] == 'message':
            if not message.get('message_id') or not any(message.get(k) for k in ('text','photo','document','video','animation','voice')):
                show(api, cid, 'أرسل رسالة نصية أو وسائط مدعومة.')
                return True
            state['message_id'] = message['message_id']
            state['step'] = 'confirm'
            show(api, cid, '📨 <b>تأكيد الإرسال</b>\nعدد المستلمين: <b>%s</b>\nالمعرفات: <code>%s</code>\n\nهل تريد الإرسال الآن؟' %
                 (len(state['recipients']), ', '.join(map(str, state['recipients']))),
                 [[storefront.btn('✅ تأكيد الإرسال', 'private_send:confirm')],
                  [storefront.btn('❌ إلغاء', 'private_send:cancel')]])
            return True
        return True

    namespace['action'] = action
    namespace['handle_action'] = action
    namespace['handle_private_message'] = handle_message
