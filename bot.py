"""SAU2030 Telegram Store Bot - Python 3.10+"""
import json, os, time, urllib.request
from pathlib import Path
BASE = Path(__file__).resolve().parent
CONFIG = json.loads((BASE / "catalog.json").read_text(encoding="utf-8"))
FALLBACK_ICONS = {"chatgpt":"🤖","youtube":"▶️","canva":"🎨","gemini":"✨","spotify":"🎵","capcut":"🎬","claude":"✳️","grok":"✖️","netflix":"📺"}
PRODUCTS = {item["id"]: dict(item, icon=FALLBACK_ICONS.get(item["id"],"▫️"), product=item.get("product",item["name"])) for item in CONFIG["products"]}
class TelegramAPI:
    def __init__(self, token): self.base_url=f"https://api.telegram.org/bot{token}/"
    def call(self, method, **data):
        req=urllib.request.Request(self.base_url+method,json.dumps(data).encode("utf-8"),{"Content-Type":"application/json"})
        try:
            with urllib.request.urlopen(req,timeout=40) as r: result=json.load(r)
        except Exception as exc:
            print("Telegram API error:",type(exc).__name__); time.sleep(3); return None
        return result.get("result")
def button(text,callback): return {"text":text,"callback_data":callback}
def home_keyboard():
    return {"keyboard":[[{"text":"🚀 ابدأ"},{"text":"🛍 المنتجات"},{"text":"💬 الدعم"}],[{"text":"👛 المحفظة"},{"text":"🔗 API"}],[{"text":"🛡 الضمان"}]],"resize_keyboard":True,"is_persistent":True,"input_field_placeholder":"اختر من القائمة"}
def product_button(product_id,product):
    eid=product.get("custom_emoji_id","")
    if CONFIG.get("custom_icons_enabled") and eid: return {"text":product["name"],"callback_data":f"product:{product_id}","icon_custom_emoji_id":str(eid)}
    return button(f"{product['icon']} {product['name']}",f"product:{product_id}")
def products_keyboard():
    items=list(PRODUCTS.items()); rows=[]
    for i in range(0,len(items),3): rows.append([product_button(pid,p) for pid,p in items[i:i+3]])
    rows.append([button("🏠 الرئيسية","home")]); return {"inline_keyboard":rows}
def send_message(api,chat_id,text,keyboard=None,entities=None):
    data={"chat_id":chat_id,"text":text}
    if entities is not None: data["entities"]=entities
    else: data["parse_mode"]="HTML"
    if keyboard: data["reply_markup"]=keyboard
    api.call("sendMessage",**data)
def show_start(api,chat_id):
    send_message(api,chat_id,"👋 <b>مرحباً بك في VEXA STORE</b>\n\nمتجر الخدمات والاشتراكات الرقمية.\nاضغط الزر بالأسفل للدخول إلى المتجر 👇",{"inline_keyboard":[[button("🚀 START | ابدأ","enter_store")]]})
def show_home(api,chat_id):
    text="👋 أهلاً بك في <b>VEXA STORE</b>\n\n🛍 متجر الخدمات الرقمية\nاختر القسم المطلوب من القائمة:\n\n<tg-emoji emoji-id=\"5440411975509096877\">💳</tg-emoji> <b>لشحن النقاط والدعم :</b> @SOQ_ID"
    send_message(api,chat_id,text,home_keyboard()); show_products(api,chat_id)
def show_products(api,chat_id): send_message(api,chat_id,"🛍 <b>المنتجات</b>\n\nاختر الخدمة:",products_keyboard())
def show_product(api,chat_id,product_id):
    product=PRODUCTS.get(product_id)
    if not product: show_products(api,chat_id); return
    if product_id=="chatgpt":
        text=("🤖 <b>ChatGPT Plus | شات جي بي تي بلس</b>\n\nاختر نوع الاشتراك المناسب لك:\n\n🔐 <b>بلس شهر — حساب خاص</b>\nاشتراك لمدة شهر بحساب مخصص لك مع بيانات دخول خاصة.\n\n📧 <b>بلس شهر — على إيميلك</b>\nاشتراك لمدة شهر يتم تفعيله على حسابك المرتبط بإيميلك.\n\n📅 المدة: شهر واحد")
        kb={"inline_keyboard":[[button("🔐 بلس شهر • حساب خاص","chatgpt_private")],[button("📧 بلس شهر • على إيميلك","chatgpt_email")],[button("↩️ العودة إلى المنتجات","products")]]}
        send_message(api,chat_id,text,kb); return
    if product_id=="youtube":
        text=("▶️ YouTube Premium | يوتيوب بريميوم\n\nاستمتع بتجربة مشاهدة أفضل مع YouTube Premium.\n\n🚫 بدون إعلانات\n▶️ تشغيل الفيديوهات في الخلفية\n📥 تنزيل المقاطع للمشاهدة بدون إنترنت\n🎵 يشمل YouTube Music Premium\n🖥 يعمل على الجوال والكمبيوتر والتلفزيون\n\n⚡ التفعيل: فوري بعد الطلب\n📅 المدة: شهر واحد\n💵 السعر: 15 ر.س")
        entities=[{"type":"custom_emoji","offset":0,"length":2,"custom_emoji_id":str(product.get("custom_emoji_id","5330032308139343696"))},{"type":"bold","offset":3,"length":36}]
        kb={"inline_keyboard":[[button("🛒 شراء الآن • 15 ر.س","buy:youtube")],[button("💬 الدعم الفني","support")],[button("↩️ العودة إلى المنتجات","products")]]}
        send_message(api,chat_id,text,kb,entities); return
    price=f"{product['price']} {product.get('currency','ر.س')}" if product.get('price') is not None else "سيتم تحديده"
    send_message(api,chat_id,f"{product['icon']} <b>{product['name']}</b>\n\n📦 {product['product']}\n📝 {product['description']}\n\n💰 السعر: {price}",{"inline_keyboard":[[button("🛒 تفاصيل الطلب",f"buy:{product_id}")],[button("↩️ المنتجات","products")],[button("🏠 الرئيسية","home")]]})
def handle_callback(api,q):
    api.call("answerCallbackQuery",callback_query_id=q["id"]); m=q.get("message",{})
    if m.get("chat",{}).get("type")=="private": handle_action(api,m["chat"]["id"],q.get("data","home"))
def handle_action(api,chat_id,action):
    if action=="enter_store" or action=="home": show_home(api,chat_id)
    elif action=="start": show_start(api,chat_id)
    elif action=="products": show_products(api,chat_id)
    elif action.startswith("product:"): show_product(api,chat_id,action.split(":",1)[1])
    elif action=="chatgpt_private": send_message(api,chat_id,"🔐 <b>ChatGPT Plus — حساب خاص</b>\n\nاشتراك Plus لمدة شهر.\n👤 حساب مخصص لك مع بيانات دخول خاصة.\n📅 المدة: شهر واحد\n⚡ التفعيل: بعد إتمام الطلب",{"inline_keyboard":[[button("🛒 طلب المنتج","buy:chatgpt_private")],[button("↩️ رجوع","product:chatgpt")]]})
    elif action=="chatgpt_email": send_message(api,chat_id,"📧 <b>ChatGPT Plus — على إيميلك</b>\n\nاشتراك Plus لمدة شهر.\n📩 يتم التفعيل على حسابك المرتبط بإيميلك.\n📅 المدة: شهر واحد\n⚡ التفعيل: بعد إتمام الطلب",{"inline_keyboard":[[button("🛒 طلب المنتج","buy:chatgpt_email")],[button("↩️ رجوع","product:chatgpt")]]})
    elif action.startswith("buy:"):
        pid=action.split(":",1)[1]; names={"chatgpt_private":"ChatGPT Plus — حساب خاص","chatgpt_email":"ChatGPT Plus — على إيميلك"}; p=PRODUCTS.get(pid); name=names.get(pid,p.get("name") if p else None)
        if name: send_message(api,chat_id,f"🛒 <b>طلب {name}</b>\n\nتم الوصول إلى صفحة الطلب.\nسيتم إضافة نظام الدفع لاحقاً.",{"inline_keyboard":[[button("↩️ رجوع","product:chatgpt" if pid.startswith("chatgpt_") else f"product:{pid}")]]})
    elif action=="support": send_message(api,chat_id,"💬 <b>الدعم الفني</b>\n\nسيتم إضافة حساب الدعم هنا.",home_keyboard())
    elif action=="wallet": send_message(api,chat_id,"👛 المحفظة\n\nالمحفظة غير مفعّلة حاليًا.",home_keyboard())
    elif action=="api": send_message(api,chat_id,"🔗 API\n\nسيتم إضافة إعدادات API لاحقاً.",home_keyboard())
    elif action=="warranty": send_message(api,chat_id,"🛡 الضمان\n\nسيتم إضافة سياسة الضمان هنا.",home_keyboard())
MENU_ACTIONS={"🚀 ابدأ":"start","🛍 المنتجات":"products","💬 الدعم":"support","👛 المحفظة":"wallet","🔗 API":"api","🛡 الضمان":"warranty"}
def main():
    token=os.getenv("BOT_TOKEN")
    if not token: raise RuntimeError("BOT_TOKEN environment variable is missing.")
    api=TelegramAPI(token); me=api.call("getMe")
    if not me: raise RuntimeError("Unable to connect to Telegram.")
    print(f"Bot @{me.get('username')} is running..."); offset=0
    while True:
        try:
            updates=api.call("getUpdates",offset=offset,timeout=25,allowed_updates=["message","callback_query"])
            if not updates: continue
            for u in updates:
                offset=u["update_id"]+1
                if "callback_query" in u: handle_callback(api,u["callback_query"])
                elif "message" in u:
                    m=u["message"]
                    if m.get("chat",{}).get("type")!="private": continue
                    cid=m["chat"]["id"]; t=m.get("text","")
                    ce=next((e for e in m.get("entities",[]) if e.get("type")=="custom_emoji"),None)
                    if ce: send_message(api,cid,"Emoji ID: <code>"+str(ce.get("custom_emoji_id"))+"</code>")
                    elif t.startswith("/start"): show_start(api,cid)
                    elif t.startswith("/products"): show_products(api,cid)
                    elif t in MENU_ACTIONS: handle_action(api,cid,MENU_ACTIONS[t])
                    else: show_home(api,cid)
        except KeyboardInterrupt: break
        except Exception as exc: print("Error:",exc); time.sleep(3)
if __name__=="__main__": main()
