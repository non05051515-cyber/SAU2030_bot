"""Local Telegram catalog. Python 3.10+, standard library only."""
import getpass, json, os, time, urllib.request, urllib.error, uuid
from pathlib import Path
BASE = Path(__file__).resolve().parent
CONFIG = json.loads((BASE / 'catalog.json').read_text(encoding='utf-8'))
PRODUCTS = {p['id']: p for p in CONFIG['products']}

class APIError(Exception):
    def __init__(self, code, retry=0): self.code, self.retry = code, retry

class Telegram:
    def __init__(self, token): self.root = 'https://api.telegram.org/bot' + token + '/'
    def call(self, method, **data):
        req = urllib.request.Request(self.root + method, json.dumps(data).encode(), {'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=40) as r: result = json.load(r)
        except urllib.error.HTTPError as e:
            try: result = json.load(e)
            except Exception: raise APIError(e.code) from None
        except (urllib.error.URLError, TimeoutError, OSError): raise APIError(0) from None
        if not result.get('ok'): raise APIError(result.get('error_code',0), result.get('parameters',{}).get('retry_after',0))
        return result['result']
    def photo(self, chat, path, caption, markup):
        boundary = uuid.uuid4().hex
        body = bytearray()
        for k,v in {'chat_id':str(chat),'caption':caption,'reply_markup':json.dumps(markup)}.items():
            body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="card.png"\r\nContent-Type: image/png\r\n\r\n'.encode())
        body.extend(path.read_bytes()); body.extend(f'\r\n--{boundary}--\r\n'.encode())
        try:
            req=urllib.request.Request(self.root+'sendPhoto', bytes(body), {'Content-Type':f'multipart/form-data; boundary={boundary}'})
            with urllib.request.urlopen(req,timeout=40) as r: result=json.load(r)
            if not result.get('ok'): raise APIError(result.get('error_code',0))
        except (urllib.error.URLError, TimeoutError, OSError): raise APIError(0) from None

def button(text, action, product=None):
    b={'text':text,'callback_data':action}
    if product and CONFIG.get('custom_icons_enabled') and product.get('custom_emoji_id'):
        b['icon_custom_emoji_id']=product['custom_emoji_id']
    return b

def screen(action):
    back=[button('↩️ التطبيقات','apps'),button('🏠 الرئيسية','home')]
    if action=='home':
        return 'أهلًا بك في SAU2030 👋\nتصفّح التطبيقات واختر المنتج للاطلاع على تفاصيله.\nهذه نسخة تجريبية؛ البيع والدفع غير مفعّلين.', [[button('🛍 التطبيقات','apps')],[button('💬 الدعم','support'),button('ℹ️ معلومات','about')]], None
    if action=='apps':
        items=[button(p['name'],'category:'+p['id'],p) for p in PRODUCTS.values()]
        return '🛍 التطبيقات\nاختر تطبيقًا لعرض منتجاته.\nالقائمة للمعاينة، ولا تعني توفر اشتراكات للبيع.', [items[i:i+3] for i in range(0,len(items),3)]+[[button('🏠 الرئيسية','home')]], None
    kind, _, key=action.partition(':')
    p=PRODUCTS.get(key)
    if kind=='category' and p:
        return p['name']+'\nاختر المنتج لعرض التفاصيل.',[[button(p['name']+' — تفاصيل الاشتراك','product:'+key,p)],back],None
    if kind=='product' and p:
        text=f"{p['name']}\n\n{p['description']}\n\n💰 السعر: لم يُحدد بعد\n🗓 المدة والخطة: لم تُحددا بعد\n📦 التوفر: غير مؤكد — عرض تجريبي\n\nتُضاف مزايا الخطة وطريقة التفعيل وشروطها بعد تحديد المنتج الفعلي."
        return text,[[button('🧪 تجربة الطلب','order:'+key)],[button('↩️ منتجات التطبيق','category:'+key)],back],BASE/'assets'/f'{key}.png'
    if kind=='order' and p:
        return '🧪 تجربة طلب '+p['name']+'\n\nوصلت إلى خطوة الطلب التجريبية. لم يتم تسجيل شراء أو خصم أي مبلغ. نفعّل الطلبات بعد تحديد الأسعار والتوفر وطريقة التواصل.',[[button('↩️ المنتج','product:'+key)],back],None
    if action=='support':
        return '💬 الدعم\n'+('تواصل مع '+CONFIG['support_username'] if CONFIG.get('support_username') else 'لم يُضف حساب الدعم بعد. سنضيفه قبل إطلاق المتجر.'),[back],None
    if action=='about':
        return 'ℹ️ نسخة تجريبية من SAU2030\nلا توجد مدفوعات أو اشتراكات مفعّلة حاليًا. أسماء التطبيقات تخص أصحابها؛ لا ندّعي شراكة رسمية معهم.',[back],None
    return screen('home')

def send_screen(api,chat,action):
    text,rows,photo=screen(action); markup={'inline_keyboard':rows}
    if photo and photo.exists():
        try: api.photo(chat,photo,text,markup); return
        except APIError: pass
    api.call('sendMessage',chat_id=chat,text=text,reply_markup=markup)

def handle(api,update):
    query=update.get('callback_query')
    msg=query.get('message',{}) if query else update.get('message',{})
    if query:
        try: api.call('answerCallbackQuery',callback_query_id=query['id'])
        except APIError: pass
    if msg.get('chat',{}).get('type')!='private': return
    action=query.get('data','home') if query else {'🛍 التطبيقات':'apps','💬 الدعم':'support','/products':'apps','/help':'about'}.get(msg.get('text','').split('@')[0],'home')
    send_screen(api,msg['chat']['id'],action)

def main():
    token=os.environ.get('TELEGRAM_BOT_TOKEN') or getpass.getpass('ألصق توكن البوت الجديد هنا (لن يظهر): ')
    api=Telegram(token.strip())
    try:
        me=api.call('getMe')
        if me.get('username','').lower()!='sau2030_bot':
            print('توقف: هذا التوكن لا يخص @SAU2030_bot. لم يتم تعديل البوت.'); return
        webhook=api.call('getWebhookInfo')
        if webhook.get('url'):
            print('توقف: البوت مربوط بطريقة تشغيل أخرى. لم نغير الربط.'); return
    except APIError:
        print('تعذر التحقق من التوكن أو الاتصال بتيليجرام.'); return
    print('تم تشغيل @SAU2030_bot. افتحه في تيليجرام واضغط Start. للإيقاف Ctrl+C.')
    offset=0
    while True:
        try:
            updates=api.call('getUpdates',offset=offset,timeout=25,allowed_updates=['message','callback_query'])
            for update in updates:
                try: handle(api,update)
                except APIError: print('تعذر إرسال رد. أعد الضغط على الزر.')
                offset=update['update_id']+1
        except APIError as e:
            if e.code in (401,409):
                print('توقف: توكن غير صالح أو نسخة أخرى من البوت تعمل.'); break
            time.sleep(max(3,min(e.retry,60)))
        except KeyboardInterrupt: break

if __name__=='__main__': main()
