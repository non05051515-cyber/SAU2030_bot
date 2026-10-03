"""Admin-only broadcast messaging for VEXA STORE."""
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PENDING = set()
BROADCAST_DRAFTS = {}
BROADCAST_LOCK = threading.Lock()
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
        draft_cols = {row[1] for row in conn.execute('PRAGMA table_info(product_broadcast_drafts)').fetchall()}
        if 'template' not in draft_cols:
            conn.execute('ALTER TABLE product_broadcast_drafts ADD COLUMN template TEXT')
        if 'awaiting_field' not in draft_cols:
            conn.execute('ALTER TABLE product_broadcast_drafts ADD COLUMN awaiting_field TEXT')
        conn.execute('CREATE TABLE IF NOT EXISTS product_broadcast_jobs (token TEXT PRIMARY KEY, pid TEXT NOT NULL, photo TEXT, status TEXT NOT NULL)')
        job_cols = {row[1] for row in conn.execute('PRAGMA table_info(product_broadcast_jobs)').fetchall()}
        if 'template' not in job_cols:
            conn.execute('ALTER TABLE product_broadcast_jobs ADD COLUMN template TEXT')
        conn.execute('CREATE TABLE IF NOT EXISTS product_broadcast_recipients (token TEXT NOT NULL, cid INTEGER NOT NULL, status TEXT NOT NULL DEFAULT "pending", PRIMARY KEY(token,cid))')
        conn.execute('CREATE TABLE IF NOT EXISTS user_delivery_status (cid INTEGER PRIMARY KEY, departed INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL DEFAULT "")')
        conn.execute('CREATE TABLE IF NOT EXISTS broadcast_stats (id INTEGER PRIMARY KEY CHECK(id=1), sent INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT "")')

    def draft(cid):
        with sg['db']() as conn:
            prepare_broadcast_tables(conn)
            row = conn.execute('SELECT token,pid,photo,awaiting_photo,template,awaiting_field FROM product_broadcast_drafts WHERE cid=?', (cid,)).fetchone()
        if not row:
            return None
        result = dict(zip(('token', 'pid', 'photo', 'awaiting_photo', 'template', 'awaiting_field'), row))
        try:
            result['template'] = json.loads(result.get('template') or '{}')
        except Exception:
            result['template'] = {}
        return result

    def save_draft(cid, pending):
        with sg['db']() as conn:
            prepare_broadcast_tables(conn)
            conn.execute('INSERT OR REPLACE INTO product_broadcast_drafts(cid,token,pid,photo,awaiting_photo,template,awaiting_field) VALUES (?,?,?,?,?,?,?)',
                         (cid, pending['token'], pending['pid'], pending.get('photo'), int(bool(pending.get('awaiting_photo'))),
                          json.dumps(pending.get('template') or {}, ensure_ascii=False), pending.get('awaiting_field')))

    def clear_draft(cid):
        with sg['db']() as conn:
            prepare_broadcast_tables(conn)
            conn.execute('DELETE FROM product_broadcast_drafts WHERE cid=?', (cid,))

    def admin_panel(api, cid):
        if cid != admin_id:
            return namespace['show_home'](api, cid)
        PENDING.discard(cid)
        BROADCAST_DRAFTS.pop(cid, None)
        AUTO_AD_STATE.pop(cid, None)
        PRODUCT_BROADCAST.pop(cid, None)
        clear_draft(cid)
        with sg['db']() as conn:
            conn.execute("DELETE FROM admin_state WHERE cid=? AND action IN ('price','product_text','product_photo','coupon_name','coupon_amount','payment_method','category_description')", (cid,))
            orders_count = conn.execute('SELECT COUNT(*) FROM orders').fetchone()[0]
            review_count = conn.execute('SELECT COUNT(*) FROM orders WHERE status="review"').fetchone()[0]
            activity_count = sg['total_activity_count'](conn)
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
            [sg['btn']('➕ إضافة منتج داخل قسم', 'admin:addtocategory', style='success')],
            [sg['btn']('📦 تعديل توفر المنتج', 'admin:stock', style='primary')],
            [sg['btn']('🔌 ربط API بالمنتج', 'admin:supplierapi', style='primary')],
            [sg['btn']('🏦 طرق الدفع / إضافة طريقة دفع', 'pm:list', style='success')],
            [sg['btn']('🎟 أكواد الخصم', 'couponadmin:list', style='primary')],
            [sg['btn']('✏️ تعديل سعر منتج', 'admin:prices', style='primary')],
            [sg['btn']('🎛 إعداد عرض بيانات المنتج', 'admin:info', style='primary')],
            [sg['btn']('📢 إرسال رسالة للجميع', 'admin:broadcast', style='primary')],
            [sg['btn']('🛍 إرسال منتج للجميع', 'admin:product_broadcast', style='primary')],
            [sg['btn']('👥 النشر في المجموعة', 'channel:group', style='primary')],
            [sg['btn']('📢 النشر في القناة @VEXA2030', 'channel:list', style='success')],
            [sg['btn']('📊 الإحصائيات', 'admin:stats')],
            [sg['btn']('📣 إعلان تلقائي للقروب', 'admin:autoad', style='success')],
            [sg['btn']('➕ إضافة أيقونة', 'admin:icons', style='success')],
            [sg['btn']('⚙️ إدارة أزرار المتجر', 'admin:buttonlabels')],
            [sg['btn'](sg['ui_label']('ui_category_description', 'تعديل وصف القسم'), 'admin:categorydesc', sg['ui_icon']('ui_category_description'))],
            [sg['btn'](sg['ui_label']('ui_welcome', 'تعديل الرسالة الترحيبية'), 'admin:welcome', sg['ui_icon']('ui_welcome'))],
            [sg['btn']('🏠 الرئيسية', 'home')],
        ]))

    def categories(api, cid):
        with sg['db']() as conn:
            custom = conn.execute('SELECT cid,name FROM admin_categories ORDER BY rowid').fetchall()
        entries = [(pid, product['name']) for pid, product in sg['G']['PRODUCTS'].items()] + custom
        rows = [[sg['btn'](title, 'pbcat:' + pid)] for pid, title in entries if sg['category_visible'](pid)]
        return sg['send'](api, cid, '🛍 <b>إرسال منتج للجميع</b>\n\nاختر القسم:', sg['kb'](rows + [[sg['btn']('↩️ لوحة الإدارة', 'admin')]]))

    def products_in_category(category):
        if category not in sg['G']['PRODUCTS'] and not sg['custom_category'](category):
            return []
        return sg['admin_category_product_ids'](category)

    def default_template():
        return {
            'custom_text': '',
            'button_text': '🛒 شراء الآن',
        }

    def merged_template(value=None):
        result = default_template()
        if isinstance(value, dict):
            if 'custom_text' in value and value.get('custom_text') is not None:
                result['custom_text'] = str(value.get('custom_text') or '')[:3500]
            if value.get('button_text'):
                result['button_text'] = str(value['button_text'])[:40]
        return result

    def product_card(api, cid, pid, photo=None, template=None):
        """Send the admin-authored ad text, with a direct button to the selected product."""
        tpl = merged_template(template)
        body = (tpl.get('custom_text') or '').strip()
        if not body:
            cp = sg['custom_product'](pid)
            qty = cp[6] if cp else None
            body = '🛍 <b>' + sg['esc'](sg['name'](pid, cid)) + '</b>'
            if qty is not None:
                body += '\n📦 <b>الكمية:</b> ' + str(qty)
            body += '\n💵 <b>السعر:</b> ' + sg['esc'](sg['price'](cid, pid))
        button = sg['btn'](tpl.get('button_text') or '🛒 شراء الآن', 'item:' + pid,
                           sg['ui_icon']('ui_broadcast_buy'), style='success')
        markup = sg['kb']([[button]])
        if photo:
            result = api.call('sendPhoto', chat_id=cid, photo=photo, caption=body[:1000],
                              parse_mode='HTML', reply_markup=markup)
            if result:
                return result
        return sg['send'](api, cid, body[:3900], markup)

    def product_preview(api, cid, pending):
        template = merged_template(pending.get('template'))
        product_card(api, cid, pending['pid'], pending.get('photo'), template=template)
        token = pending['token']
        return sg['send'](
            api, cid,
            'جهّز الإعلان من خيارين فقط:',
            sg['kb']([
                [sg['btn']('📝 كتابة / تعديل نص الإعلان', 'pbtext:' + token, style='primary')],
                [sg['btn']('🖼️ إضافة / تغيير صورة', 'pbphoto:' + token)],
                [sg['btn']('✅ إرسال للجميع', 'pbconfirm:' + token, style='success')],
                [sg['btn']('❌ إلغاء', 'admin:product_broadcast')]
            ])
        )

    def deliver_queued(api):
        with sg['db']() as conn:
            prepare_broadcast_tables(conn)
            # Emergency stop for the stuck product broadcast requested by the admin.
            # Mark this specific persisted job cancelled so a Railway restart will
            # not resume it and keep sending progress/completion notifications.
            conn.execute('UPDATE product_broadcast_jobs SET status="cancelled" WHERE token=? AND status IN ("queued","running")', ('0ef24b184fc7',))
            jobs = conn.execute('SELECT token,pid,photo,template FROM product_broadcast_jobs WHERE status IN ("queued","running") ORDER BY rowid').fetchall()
        for token, pid, photo, template_json in jobs:
            try:
                job_template = json.loads(template_json or '{}')
            except Exception:
                job_template = {}
            with sg['db']() as conn:
                conn.execute('UPDATE product_broadcast_jobs SET status="running" WHERE token=?', (token,))
                recipients = [row[0] for row in conn.execute('SELECT cid FROM product_broadcast_recipients WHERE token=? AND status="pending" ORDER BY cid', (token,))]
                total = conn.execute('SELECT COUNT(*) FROM product_broadcast_recipients WHERE token=?', (token,)).fetchone()[0]
            print('Product broadcast started:', token, 'remaining:', len(recipients), flush=True)

            def deliver_product(user_id):
                try:
                    result = product_card(api, user_id, pid, photo, job_template)
                except Exception as exc:
                    print('Product delivery error:', type(exc).__name__, str(exc)[:200], flush=True)
                    result = None
                error = getattr(api, 'last_error', None)
                departed = int(not result and error and error.get('code') == 403 and
                               ('blocked by the user' in error.get('description', '') or
                                'user is deactivated' in error.get('description', '')))
                with sg['db']() as conn:
                    conn.execute('UPDATE product_broadcast_recipients SET status=? WHERE token=? AND cid=?',
                                 ('sent' if result else 'failed', token, user_id))
                    conn.execute('INSERT INTO user_delivery_status(cid,departed,updated_at) VALUES (?,?,?) ON CONFLICT(cid) DO UPDATE SET departed=excluded.departed, updated_at=excluded.updated_at',
                                 (user_id, departed, sg['now_saudi']()))
                return bool(result)

            # Telegram has a global bot send limit. Start requests at about 25/sec,
            # but keep several requests in flight so network latency does not make
            # broadcasts crawl one user at a time.
            with ThreadPoolExecutor(max_workers=16, thread_name_prefix='product-broadcast') as pool:
                futures = []
                next_slot = time.monotonic()
                for user_id in recipients:
                    now = time.monotonic()
                    if now < next_slot:
                        time.sleep(next_slot - now)
                    futures.append(pool.submit(deliver_product, user_id))
                    next_slot = max(next_slot + 0.04, time.monotonic())
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as exc:
                        print('Product broadcast future:', type(exc).__name__, str(exc)[:150], flush=True)
            with sg['db']() as conn:
                ok = conn.execute('SELECT COUNT(*) FROM product_broadcast_recipients WHERE token=? AND status="sent"', (token,)).fetchone()[0]
                failed = conn.execute('SELECT COUNT(*) FROM product_broadcast_recipients WHERE token=? AND status="failed"', (token,)).fetchone()[0]
                conn.execute('UPDATE product_broadcast_jobs SET status="done" WHERE token=?', (token,))
                conn.execute('INSERT OR REPLACE INTO broadcast_stats(id,sent,failed,created_at) VALUES (1,?,?,?)', (ok, failed, sg['now_saudi']()))
            print('Product broadcast completed:', token, 'sent:', ok, 'failed:', failed, flush=True)
            try:
                sg['send'](api, admin_id, f'✅ اكتمل إرسال المنتج.\\nوصل إلى: <b>{ok}</b>\\nتعذر الإرسال إلى: <b>{failed}</b>', sg['kb']([[sg['btn']('↩️ لوحة الإدارة', 'admin')]]))
            except Exception as exc:
                print('Product broadcast completion notification:', type(exc).__name__, flush=True)

    global DELIVERY_WORKER
    DELIVERY_WORKER = deliver_queued

    def action(api, cid, value):
        if value.startswith(('admin:product_broadcast', 'pbcat:', 'pbpick:', 'pbphoto:', 'pbconfirm:', 'pbtext:', 'pbpreview:')) and cid != admin_id:
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
            pending = {'token': token, 'pid': pid, 'template': default_template()}
            save_draft(cid, pending)
            return product_preview(api, cid, pending)
        if cid == admin_id and value.startswith('pbtext:'):
            pending = draft(cid)
            token = value.split(':', 1)[1]
            if not pending or pending['token'] != token:
                return categories(api, cid)
            pending['awaiting_field'] = 'custom_text'
            save_draft(cid, pending)
            return sg['send'](
                api, cid,
                '📝 أرسل الآن <b>نص الإعلان كاملًا</b> كما تريد ظهوره للعملاء.\n'
                'اكتب الأيقونات والسعر والكمية والكلام كله في رسالة واحدة.',
                sg['kb']([[sg['btn']('❌ إلغاء التعديل', 'pbpreview:' + token)]])
            )
        if cid == admin_id and value.startswith('pbpreview:'):
            pending = draft(cid)
            token = value.split(':', 1)[1]
            if not pending or pending['token'] != token:
                return categories(api, cid)
            pending['awaiting_field'] = None
            save_draft(cid, pending)
            return product_preview(api, cid, pending)
        if cid == admin_id and value.startswith('pbphoto:'):
            pending = draft(cid)
            if not pending or pending['token'] != value.split(':', 1)[1]:
                return categories(api, cid)
            pending['awaiting_photo'] = True
            save_draft(cid, pending)
            return sg['send'](api, cid, '🖼️ أرسل الآن صورة الإعلان. ستظهر مع نفس النص وزر الشراء.',
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
                conn.execute('INSERT OR IGNORE INTO product_broadcast_jobs(token,pid,photo,status,template) VALUES (?,?,?,"queued",?)',
                             (token, pid, pending.get('photo'), json.dumps(merged_template(pending.get('template')), ensure_ascii=False)))
                conn.executemany('INSERT OR IGNORE INTO product_broadcast_recipients(token,cid) VALUES (?,?)',
                                 [(token, user_id) for user_id in set(_users()) - {admin_id}])
                conn.execute('DELETE FROM product_broadcast_drafts WHERE cid=? AND token=?', (cid, token))
            result = sg['send'](api, cid, '⏳ بدأ إرسال المنتج للمستخدمين. سأرسل لك عدد من وصلتهم الرسالة عند الانتهاء.')
            tick_product_broadcast(api)
            return result
        if cid == admin_id and value == 'admin:supplierapi':
            return sg['supplier_api_menu'](api, cid)
        if cid == admin_id and value.startswith('suppliercat:'):
            return sg['supplier_api_menu'](api, cid, value.split(':', 1)[1])
        if cid == admin_id and value.startswith('supplierpick:'):
            return sg['supplier_api_editor'](api, cid, value.split(':', 1)[1])
        if cid == admin_id and value.startswith('supplierpandora:'):
            pid = value.split(':', 1)[1]
            endpoint, api_key, service_id, enabled, provider, variant_id = sg['supplier_api_row'](pid)
            endpoint = 'https://api.pandoradigital.shop/api/v1'
            provider = 'pandora'
            with sg['db']() as conn:
                conn.execute('INSERT INTO supplier_api(pid,endpoint,api_key,service_id,enabled,provider,variant_id) VALUES (?,?,?,?,?,?,?) ON CONFLICT(pid) DO UPDATE SET endpoint=excluded.endpoint,provider=excluded.provider',
                             (pid, endpoint, api_key, service_id, enabled, provider, variant_id))
            sg['send'](api, cid, '✅ تم اختيار <b>Pandora Digital</b> لهذا المنتج.\n\nأضف الآن مفتاح API ثم Product ID وVariant ID.')
            return sg['supplier_api_editor'](api, cid, pid)
        if cid == admin_id and value.startswith('supplierset:'):
            parts = value.split(':', 2)
            if len(parts) == 3:
                field, pid = parts[1], parts[2]
                if field in ('endpoint', 'key', 'service', 'variant'):
                    action_name = {'endpoint':'supplier_endpoint','key':'supplier_key','service':'supplier_service','variant':'supplier_variant'}[field]
                    with sg['db']() as conn:
                        conn.execute('INSERT OR REPLACE INTO admin_state VALUES (?,?,?)', (cid, action_name, pid))
                    prompt = {
                        'endpoint':'أرسل رابط API الكامل، مثال: <code>https://api.pandoradigital.shop/api/v1</code>',
                        'key':'أرسل مفتاح API. لن يظهر كاملًا بعد الحفظ.',
                        'service':'أرسل Product ID لدى المورد.',
                        'variant':'أرسل Variant ID لدى المورد.'
                    }[field]
                    return sg['send'](api, cid, '🔌 <b>' + sg['esc'](sg['name'](pid, cid)) + '</b>\n\n' + prompt,
                                      sg['kb']([[sg['btn']('❌ إلغاء', 'supplierpick:' + pid)]]))
        if cid == admin_id and value.startswith('suppliertest:'):
            return sg['supplier_test_connection'](api, cid, value.split(':', 1)[1])
        if cid == admin_id and value.startswith('suppliertoggle:'):
            pid = value.split(':', 1)[1]
            endpoint, api_key, service_id, enabled, provider, variant_id = sg['supplier_api_row'](pid)
            missing = []
            if not endpoint: missing.append('رابط API')
            if not api_key: missing.append('مفتاح API')
            if provider == 'pandora' and not service_id: missing.append('Product ID')
            if provider == 'pandora' and not variant_id: missing.append('Variant ID')
            if missing:
                sg['send'](api, cid, '⚠️ تم حفظ الموجود، لكن باقي قبل التفعيل: <b>' + sg['esc'](' + '.join(missing)) + '</b>.\n\nإذا هدفك فقط تجربة المفتاح الآن اضغط 🧪 اختبار الاتصال.')
                return sg['supplier_api_editor'](api, cid, pid)
            with sg['db']() as conn:
                conn.execute('INSERT INTO supplier_api(pid,endpoint,api_key,service_id,enabled,provider,variant_id) VALUES (?,?,?,?,?,?,?) ON CONFLICT(pid) DO UPDATE SET enabled=excluded.enabled',
                             (pid, endpoint, api_key, service_id, 0 if enabled else 1, provider, variant_id))
            return sg['supplier_api_editor'](api, cid, pid)
        if cid == admin_id and value.startswith('supplierdelete:'):
            pid = value.split(':', 1)[1]
            with sg['db']() as conn:
                conn.execute('DELETE FROM supplier_api WHERE pid=?', (pid,))
            sg['send'](api, cid, '✅ تم حذف ربط API لهذا المنتج.')
            return sg['supplier_api_editor'](api, cid, pid)

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
        if cid == admin_id and (value in ('admin:prices', 'admin:stock', 'admin:info', 'admin:editname', 'admin:editdesc', 'admin:photos', 'admin:supplierapi') or value.startswith(('pricecat:', 'pricepick:', 'priceedit:', 'stockcat:', 'stockpick:', 'stockset:', 'stockqty:', 'suppliercat:', 'supplierpick:', 'supplierset:', 'suppliertoggle:', 'suppliertest:', 'supplierdelete:', 'supplierpandora:', 'txtcat:', 'txtpick:', 'txtedit:', 'photocat:', 'photopick:', 'photodel:'))):
            PENDING.discard(cid)
            AUTO_AD_STATE.pop(cid, None)
        if cid == admin_id and value == 'admin:activity_reset':
            return sg['send'](api, cid, '⚠️ هل تريد حذف سجل نشاط العملاء وتصفير عداده؟ لن تتأثر الطلبات أو حسابات العملاء.', sg['kb']([[sg['btn']('✅ تأكيد التصفير', 'admin:activity_reset_confirm', style='danger')], [sg['btn']('❌ إلغاء', 'admin:activity')]]))
        if cid == admin_id and value == 'admin:activity_reset_confirm':
            with sg['db']() as conn:
                conn.execute('DELETE FROM activity')
                conn.execute("DELETE FROM sqlite_sequence WHERE name='activity'")
                conn.execute('DELETE FROM activity_totals')
                conn.execute('INSERT INTO activity_totals(id,total) VALUES (1,0)')
            sg['ACTIVITY_VIEW'].clear()
            return sg['send'](api, cid, '✅ تم تصفير نشاط العملاء. سيبدأ تسجيل التفاعلات الجديدة من الصفر.', sg['kb']([[sg['btn']('👀 نشاط العملاء', 'admin:activity')], [sg['btn']('↩️ لوحة الإدارة', 'admin')]]))
        if cid == admin_id and value == 'admin:users':
            users = sorted(set(_users()))
            with sg['db']() as conn:
                conn.execute('CREATE TABLE IF NOT EXISTS user_delivery_status (cid INTEGER PRIMARY KEY, departed INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL DEFAULT "")')
                departed_ids = {row[0] for row in conn.execute('SELECT cid FROM user_delivery_status WHERE departed=1').fetchall()}
                recent_rows = conn.execute('SELECT cid, MAX(created_at) FROM activity GROUP BY cid ORDER BY MAX(created_at) DESC LIMIT 15').fetchall()
            customers = set(users) - {admin_id}
            available = len(customers - departed_ids)
            recent = [(uid, ts) for uid, ts in recent_rows if uid != admin_id]
            lines = ['👥 <b>عملاء البوت</b>', '', f'🟢 عدد العملاء القابلين للمراسلة (آخر حالة معروفة): <b>{available}</b>']
            if recent:
                lines += ['', '🕒 <b>آخر نشاط مسجل:</b>']
                for uid, ts in recent[:10]:
                    lines.append(f'• <code>{uid}</code> — {sg["esc"](ts)}')
            lines += ['', 'ℹ️ العدد يستبعد حساب الإدارة والحسابات التي ثبت حظرها أو تعطيلها؛ لا يتيح تيليجرام فحص كل الحسابات مباشرة.']
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
            customers = users - {admin_id}
            with sg['db']() as conn:
                excluded = {row[0] for row in conn.execute('SELECT cid FROM user_delivery_status WHERE departed=1')}
            total = len(customers)
            departed = len(customers & excluded)
            available = len(customers - excluded)
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
            BROADCAST_DRAFTS[cid] = {'step': 'ar'}
            return sg['send'](
                api, cid,
                '📢 <b>إرسال رسالة للجميع</b>\n\nأرسل الرسالة بالعربية أولًا، ثم النسخة الإنجليزية.\nيمكنك إرسال نص أو صورة مع تعليق، وستُرسل النسخة المناسبة حسب لغة العميل داخل البوت.',
                sg['kb']([[sg['btn']('❌ إلغاء', 'admin:broadcast_cancel')]])
            )
        if cid == admin_id and value == 'admin:broadcast_cancel':
            PENDING.discard(cid)
            BROADCAST_DRAFTS.pop(cid, None)
            return admin_panel(api, cid)
        return old_action(api, cid, value)

    def handle_receipt(api, message):
        cid = message.get('chat', {}).get('id')
        pending = draft(cid) if cid == admin_id else None
        if pending and pending.get('awaiting_field') == 'custom_text':
            raw = (message.get('text') or message.get('caption') or '').strip()
            if not raw:
                sg['send'](api, cid, 'أرسل نص الإعلان في رسالة واحدة.')
                return True
            # Preserve Telegram custom/animated emoji IDs instead of flattening
            # them into ordinary Unicode emoji when the ad text is saved.
            source_message = message
            if message.get('caption') is not None and not message.get('text'):
                source_message = dict(message)
                source_message['text'] = message.get('caption') or ''
                source_message['entities'] = message.get('caption_entities') or []
            formatted = sg['description_message_html'](source_message, raw[:3500])
            tpl = merged_template(pending.get('template'))
            tpl['custom_text'] = formatted
            pending['template'] = tpl
            pending['awaiting_field'] = None
            save_draft(cid, pending)
            sg['send'](api, cid, '✅ تم حفظ نص الإعلان مع الأيقونات المتحركة.')
            return product_preview(api, cid, pending) or True
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
            pending = BROADCAST_DRAFTS.setdefault(cid, {'step': 'ar'})
            if pending['step'] == 'ar':
                pending.update(step='en', ar=message['message_id'])
                sg['send'](api, cid, '🇺🇸 أرسل الآن النسخة الإنجليزية من نفس الإعلان. لن يُرسل شيء للعملاء حتى تصل النسختان.', sg['kb']([[sg['btn']('❌ إلغاء', 'admin:broadcast_cancel')]]))
                return True
            if not message.get('text') and not message.get('caption') and not message.get('photo'):
                sg['send'](api, cid, 'أرسل نصًا أو صورة مع تعليق للنسخة الإنجليزية.')
                return True
            pending['en'] = message['message_id']
            if not BROADCAST_LOCK.acquire(blocking=False):
                return sg['send'](api, cid, '⏳ يوجد إرسال جماعي جارٍ. انتظر اكتماله.') or True
            arabic_id, english_id = pending['ar'], pending['en']
            PENDING.discard(cid)
            BROADCAST_DRAFTS.pop(cid, None)
            sg['send'](api, cid, '⏳ بدأ الإرسال بالخلفية. البوت سيبقى متاحًا للعملاء، وستصلك الإحصائية بعد الانتهاء.')
            def deliver():
                ok = failed = skipped = 0
                try:
                    with sg['db']() as conn:
                        prepare_broadcast_tables(conn)
                        excluded = {row[0] for row in conn.execute('SELECT cid FROM user_delivery_status WHERE departed=1')}
                    users = [u for u in _users() if u != admin_id and u not in excluded]
                    skipped = len(_users()) - 1 - len(users)

                    def deliver_message(user_id):
                        try:
                            selected = namespace['LANGS'].get(str(user_id), 'ar')
                            source_id = english_id if selected == 'en' else arabic_id
                            result = api.call('copyMessage', chat_id=user_id, from_chat_id=cid, message_id=source_id)
                            error = getattr(api, 'last_error', None)
                            departed = int(not result and error and error.get('code') == 403 and
                                           ('blocked by the user' in error.get('description', '') or
                                            'user is deactivated' in error.get('description', '')))
                            with sg['db']() as conn:
                                conn.execute('INSERT INTO user_delivery_status(cid,departed,updated_at) VALUES (?,?,?) ON CONFLICT(cid) DO UPDATE SET departed=excluded.departed, updated_at=excluded.updated_at',
                                             (user_id, departed, sg['now_saudi']()))
                            return bool(result)
                        except Exception as exc:
                            print('Broadcast delivery error:', type(exc).__name__, str(exc)[:150], flush=True)
                            return False

                    with ThreadPoolExecutor(max_workers=16, thread_name_prefix='message-broadcast') as pool:
                        futures = []
                        next_slot = time.monotonic()
                        for user_id in users:
                            now = time.monotonic()
                            if now < next_slot:
                                time.sleep(next_slot - now)
                            futures.append(pool.submit(deliver_message, user_id))
                            next_slot = max(next_slot + 0.04, time.monotonic())
                        for future in as_completed(futures):
                            try:
                                if future.result():
                                    ok += 1
                                else:
                                    failed += 1
                            except Exception as exc:
                                failed += 1
                                print('Broadcast future error:', type(exc).__name__, str(exc)[:150], flush=True)
                except Exception as exc:
                    print('Broadcast worker error:', type(exc).__name__, str(exc)[:150], flush=True)
                finally:
                    with sg['db']() as conn:
                        conn.execute('INSERT OR REPLACE INTO broadcast_stats(id,sent,failed,created_at) VALUES (1,?,?,?)', (ok, failed, sg['now_saudi']()))
                    sg['send'](api, cid, f'✅ <b>اكتمل الإرسال</b>\\n\\nوصلت: <b>{ok}</b>\\nتعذر: <b>{failed}</b>\\nمستبعدون سابقًا: <b>{skipped}</b>', sg['kb']([[sg['btn']('↩️ لوحة الإدارة', 'admin')]]))
                    BROADCAST_LOCK.release()
            threading.Thread(target=deliver, name='language-broadcast', daemon=True).start()
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
