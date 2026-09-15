"""SAU2030 Telegram Store Bot - Python 3.10+"""
import json, os, time, urllib.request
from pathlib import Path
BASE=Path(__file__).resolve().parent
CONFIG=json.loads((BASE/"catalog.json").read_text(encoding="utf-8"))
ADMIN_ID=8386371522
USERS_FILE=Path("/data/users.json")
PENDING_RECEIPTS={}
FALLBACK_ICONS={"chatgpt":"🤖","youtube":"▶️","canva":"🎨","gemini":"✨","spotify":"🎵","capcut":"🎬","claude":"✳️","grok":"✖️","netflix":"📺","iptv":"📡"}
PRODUCTS={item["id"]:dict(item,icon=FALLBACK_ICONS.get(item["id"],"▫️"),product=item.get("product",item["name"])) for item in CONFIG["products"]}
CLAUDE_NAMES={"claude_pro":"إعادة شحن Claude Pro الرسمية لمدة شهر","claude_api_500m":"Claude API — 500 مليون توكن — 6 أيام","claude_api_100m":"Claude API — 100 مليون توكن — 3 أيام","claude_api_50m":"Claude API — 50 مليون توكن — يومان","claude_api_10m":"Claude API — 10 ملايين توكن — يوم واحد"}
CLAUDE_API_DETAILS={
"claude_api_500m":("500 مليون توكن","6 أيام"),
"claude_api_100m":("100 مليون توكن","3 أيام"),
"claude_api_50m":("50 مليون توكن","يومين"),
"claude_api_10m":("10 ملايين توكن","يوم واحد")}

def load_users():
    try:return set(json.loads(USERS_FILE.read_text(encoding="utf-8")))
    except Exception:return set()
def save_user(chat_id):
    users=load_users()
    if chat_id not in users:
        users.add(chat_id)
        try:USERS_FILE.write_text(json.dumps(sorted(users)),encoding="utf-8")
        except Exception as exc:print("Unable to save user:",exc)
class TelegramAPI:
    def __init__(self,token):self.base_url=f"https://api.telegram.org/bot{token}/"
    def call(self,method,**data):
        req=urllib.request.Request(self.base_url+method,json.dumps(data).encode("utf-8"),{"Content-Type":"application/json"})
        try:
            with urllib.request.urlopen(req,timeout=40) as r:result=json.load(r)
        except Exception as exc:print("Telegram API error:",type(exc).__name__);time.sleep(1);return None
        return result.get("result")
def button(text,callback):return {"text":text,"callback_data":callback}
def home_keyboard():return {"keyboard":[[{"text":"🚀 ابدأ"},{"text":"🛍 المنتجات"},{"text":"💬 الدعم"}],[{"text":"👛 المحفظة"},{"text":"🔗 API"}],[{"text":"🛡 الضمان"}]],"resize_keyboard":True,"is_persistent":True,"input_field_placeholder":"اختر من القائمة"}
def product_button(pid,p):
    eid=p.get("custom_emoji_id","")
    if CONFIG.get("custom_icons_enabled") and eid:return {"text":p["name"],"callback_data":f"product:{pid}","icon_custom_emoji_id":str(eid)}
    return button(f"{p['icon']} {p['name']}",f"product:{pid}")
def products_keyboard():
    items=list(PRODUCTS.items());rows=[]
    for i in range(0,len(items),3):rows.append([product_button(pid,p) for pid,p in items[i:i+3]])
    rows.append([button("🏠 الرئيسية","home")]);return {"inline_keyboard":rows}
def send_message(api,chat_id,text,keyboard=None,entities=None):
    data={"chat_id":chat_id,"text":text}
    if entities is not None:data["entities"]=entities
    else:data["parse_mode"]="HTML"
    if keyboard:data["reply_markup"]=keyboard
    return api.call("sendMessage",**data)
def send_product_card(api,cid,pid,p,caption,keyboard):
    image=BASE/"assets"/f"{pid}.png"
    if image.exists():
        try:
            boundary="----VEXAFormBoundary";fields={"chat_id":str(cid),"caption":caption,"parse_mode":"HTML","reply_markup":json.dumps(keyboard,ensure_ascii=False)};body=b""
            for key,value in fields.items():body+=(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n").encode("utf-8")
            body+=(f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"{image.name}\"\r\nContent-Type: image/png\r\n\r\n").encode("utf-8")+image.read_bytes()+f"\r\n--{boundary}--\r\n".encode()
            req=urllib.request.Request(api.base_url+"sendPhoto",body,{"Content-Type":f"multipart/form-data; boundary={boundary}"})
            with urllib.request.urlopen(req,timeout=40) as r:json.load(r)
            return
        except Exception as exc:print("Product photo error:",type(exc).__name__)
    send_message(api,cid,caption,keyboard)
def show_start(api,cid):send_message(api,cid,"👋 <b>مرحباً بك في VEXA STORE</b>\n\nمتجر الخدمات والاشتراكات الرقمية.\nاضغط الزر بالأسفل للدخول إلى المتجر 👇",{"inline_keyboard":[[button("🚀 START | ابدأ","enter_store")]]})
def show_home(api,cid):send_message(api,cid,"👋 أهلاً بك في <b>VEXA STORE</b>\n\n🛍 متجر الخدمات الرقمية\nاختر القسم المطلوب من القائمة:\n\n<tg-emoji emoji-id=\"5440411975509096877\">💳</tg-emoji> <b>لشحن النقاط والدعم :</b> @SOQ_ID",home_keyboard());show_products(api,cid)
def show_products(api,cid):send_message(api,cid,"🛍 <b>المنتجات</b>\n\nاختر الخدمة:",products_keyboard())
def show_product(api,cid,pid):
    p=PRODUCTS.get(pid)
    if not p:show_products(api,cid);return
    price=f"{p['price']} {p.get('currency','ر.س')}" if p.get("price") is not None else "يُضاف لاحقاً"
    if pid=="chatgpt":
        text="<tg-emoji emoji-id=\"5310259124817134249\">🤖</tg-emoji> <b>ChatGPT Plus | شات جي بي تي بلس</b>\n\n💵 السعر: <b>يُضاف لاحقاً</b>\n\n💦 <b>الوصف:</b>\nاختر نوع الاشتراك المناسب لك.\n\n🔐 بلس شهر — حساب خاص\n📧 بلس شهر — على إيميلك\n\n📅 المدة: شهر واحد";kb={"inline_keyboard":[[button("🔐 بلس شهر • حساب خاص","chatgpt_private")],[button("📧 بلس شهر • على إيميلك","chatgpt_email")],[button("↩️ العودة إلى المنتجات","products")]]};send_product_card(api,cid,pid,p,text,kb);return
    if pid=="claude":
        text="<tg-emoji emoji-id=\"6174520215376763867\">✳️</tg-emoji> <b>Claude | كلود</b>\n\nاختر المنتج المطلوب 👇"
        kb={"inline_keyboard":[[button("Claude Pro — إعادة شحن رسمية شهر","claude:claude_pro")],[button("Claude API — 500 مليون توكن — 6 أيام","claude:claude_api_500m")],[button("Claude API — 100 مليون توكن — 3 أيام","claude:claude_api_100m")],[button("Claude API — 50 مليون توكن — يومان","claude:claude_api_50m")],[button("Claude API — 10 ملايين توكن — يوم واحد","claude:claude_api_10m")],[button("↩️ العودة إلى المنتجات","products")]]};send_product_card(api,cid,pid,p,text,kb);return
    if pid=="youtube":
        text=f"▶️ <b>YouTube Premium | يوتيوب بريميوم</b>\n💵 السعر: <b>{price}</b>\n\n💦 <b>الوصف:</b>\n{p['description']}\n\n⚡ التفعيل: فوري بعد الطلب\n📅 المدة: شهر واحد";kb={"inline_keyboard":[[button("🛒 شراء الآن",f"buy:{pid}")],[button("💬 الدعم الفني","support")],[button("↩️ العودة إلى المنتجات","products")]]};send_product_card(api,cid,pid,p,text,kb);return
    text=f"{p['icon']} <b>{p['name']}</b>\n💵 السعر: <b>{price}</b>\n\n💦 <b>الوصف:</b>\n{p['description']}";kb={"inline_keyboard":[[button("🛒 شراء الآن",f"buy:{pid}")],[button("💬 الدعم الفني","support")],[button("↩️ العودة إلى المنتجات","products")]]};send_product_card(api,cid,pid,p,text,kb)
def show_claude_product(api,cid,pid):
    name=CLAUDE_NAMES.get(pid)
    if not name:show_product(api,cid,"claude");return
    if pid=="claude_pro":
        text="🛍 <b>إعادة شحن رسمية لـ Claude Pro لمدة شهر (مع ضمان محدود)</b>\n📦 الكمية المتوفرة: <b>2 قطعة</b>\n\n💦 <b>الوصف:</b>\nهذا رمز تفعيل رسمي (CDK) لاشتراك Claude Pro لمدة شهر واحد على حساب المستخدم الشخصي.\nيمكن استخدامه لتجديد الاشتراك أو لتفعيله لأول مرة.\nلا يلزم وجود بطاقة ائتمان.\n\n❗️ يجب أن تكون على الخطة المجانية ولا توجد لديك أي فواتير غير مدفوعة. إذا كان لديك اشتراك نشط، فيرجى الانتظار حتى ينتهي الاشتراك قبل إعادة الشحن.\nيوجد ضمان لمدة 30 يومًا للاشتراك فقط، ولا يشمل هذا الضمان حظر الحسابات.\n\n⚠️ <b>هام: يرجى القراءة قبل الطلب</b>\nستؤدي الحالات التالية إلى فشل الدفع، وإذا فشل الاشتراك بسببها فلن يتم استرداد الأموال.\n\n❌ <b>الحالة 1: الاشتراك لم ينتهِ</b>\nإذا سبق إعادة الشحن عبر iOS أو Android أو بطاقة ائتمان، فلا تقدم طلبًا جديدًا قبل انتهاء الاشتراك. تحقق أولًا من سجل الفواتير وتأكد من عدم وجود اشتراك نشط.\n\n❌ <b>الحالة 2: رصيد مستحق أو استردادات</b>\nوجود رصيد مستحق أو استردادات قد يؤدي إلى استخدام مبلغ إعادة الشحن لسداد الرصيد بدل معالجة الاشتراك، ولن يتم استرداد الأموال.\n\n❌ <b>الحالة 3: معرف المؤسسة محظور</b>\nقد يعمل تسجيل الدخول بصورة طبيعية رغم وجود حظر صامت على معرف المؤسسة. في هذه الحالة لن تنجح إعادة الشحن ولن يتم استرداد الأموال.\n\n❗️ إذا انطبقت عليك أي حالة مما سبق وفشلت العملية بعد الطلب، فلن يتم استرداد الأموال. تأكد من حسابك بعناية قبل الدفع."
    else:
        tokens,duration=CLAUDE_API_DETAILS[pid]
        text=f"✳️ <b>واجهة برمجة التطبيقات (API) الخاصة بـ Claude — {tokens}</b>\n\n💦 <b>الوصف:</b>\nواجهة برمجة التطبيقات (API) الخاصة بـ Claude — {tokens} لمدة {duration}.\nمتوافقة مع جميع الأدوات وبيئات التطوير المتكاملة (IDEs) مثل: 9router وClaude Dev وCursor وVS Code وغيرها.\n\n📅 مدة الصلاحية: <b>{duration}</b>"
    kb={"inline_keyboard":[[button("🛒 طلب المنتج",f"buy:{pid}")],[button("↩️ رجوع إلى Claude","product:claude")]]};send_message(api,cid,text,kb)
def order_name(pid):
    names={"chatgpt_private":"ChatGPT Plus — حساب خاص","chatgpt_email":"ChatGPT Plus — على إيميلك",**CLAUDE_NAMES};p=PRODUCTS.get(pid);return names.get(pid,p.get("name") if p else pid)
def back_action(pid):
    if pid.startswith("chatgpt_"):return "product:chatgpt"
    if pid.startswith("claude_"):return "product:claude"
    return f"product:{pid}"
def show_payment_methods(api,cid,pid):
    bank_button={"text":"تحويل بنكي — الراجحي","callback_data":f"paybank:{pid}","icon_custom_emoji_id":"5452024291472196683"};bybit_button={"text":"USDT — Bybit","callback_data":f"paybybit:{pid}","icon_custom_emoji_id":"5472387796574418157"};send_message(api,cid,f"💳 <b>اختر طريقة الدفع</b>\n\n🛒 المنتج: <b>{order_name(pid)}</b>",{"inline_keyboard":[[bank_button],[bybit_button],[button("↩️ رجوع",back_action(pid))]]})
def payment_value(name):return os.getenv(name,"غير مضاف بعد")
def show_bank(api,cid,pid):
    text=("🏦 <b>التحويل البنكي — مصرف الراجحي</b>\n\n"+f"اسم الحساب: <b>{payment_value('PAYMENT_BANK_HOLDER')}</b>\n"+f"رقم الحساب:\n<code>{payment_value('PAYMENT_BANK_ACCOUNT')}</code>\n\n"+f"الآيبان:\n<code>{payment_value('PAYMENT_BANK_IBAN')}</code>\n\n⚠️ تأكد من البيانات قبل التحويل، ثم اضغط «تم التحويل» وأرسل صورة الإيصال.");send_message(api,cid,text,{"inline_keyboard":[[button("✅ تم التحويل",f"receipt:bank:{pid}")],[button("↩️ طرق الدفع",f"buy:{pid}")]]})
def show_bybit(api,cid,pid):send_message(api,cid,"🪙 <b>USDT — Bybit</b>\n\nاختر طريقة إرسال USDT:",{"inline_keyboard":[[button("🟡 Bybit Pay",f"bybitid:{pid}")],[button("🔴 USDT • TRON (TRC20)",f"trc20:{pid}")],[button("🟡 USDT • BSC (BEP20)",f"bep20:{pid}")],[button("↩️ طرق الدفع",f"buy:{pid}")]]})
def show_crypto(api,cid,pid,kind):
    if kind=="bybitid":title="USDT — Bybit Pay";label="معرف Bybit Pay";value=payment_value("PAYMENT_BYBIT_PAY_ID");warn="تأكد من المعرف قبل الإرسال."
    elif kind=="trc20":title="USDT — TRON (TRC20)";label="عنوان المحفظة";value=payment_value("PAYMENT_USDT_TRC20");warn="أرسل USDT فقط عبر شبكة TRON (TRC20)."
    else:title="USDT — BSC (BEP20)";label="عنوان المحفظة";value=payment_value("PAYMENT_USDT_BEP20");warn="أرسل USDT فقط عبر شبكة BSC (BEP20)."
    send_message(api,cid,f"🪙 <b>{title}</b>\n\n{label}:\n<code>{value}</code>\n\n⚠️ {warn}\nبعد الدفع اضغط «تم التحويل» وأرسل صورة الإثبات.",{"inline_keyboard":[[button("✅ تم التحويل",f"receipt:{kind}:{pid}")],[button("↩️ Bybit",f"paybybit:{pid}")]]})
def request_receipt(api,cid,pid,method):PENDING_RECEIPTS[cid]={"product":pid,"method":method};send_message(api,cid,"📸 <b>إرسال إثبات الدفع</b>\n\nأرسل الآن صورة إيصال/إثبات التحويل هنا في المحادثة.\nسيتم إرسالها مباشرة للإدارة للمراجعة.",{"inline_keyboard":[[button("❌ إلغاء",f"buy:{pid}")]]})
def handle_receipt(api,m):
    cid=m["chat"]["id"];pending=PENDING_RECEIPTS.get(cid)
    if not pending:return False
    if not m.get("photo"):send_message(api,cid,"📸 فضلاً أرسل <b>صورة</b> إثبات الدفع.");return True
    method_names={"bank":"الراجحي","bybitid":"Bybit Pay","trc20":"USDT TRC20","bep20":"USDT BEP20"};user=m.get("from",{});username=("@"+user["username"]) if user.get("username") else "بدون معرف";send_message(api,ADMIN_ID,f"🧾 <b>إثبات دفع جديد</b>\n\n🛒 المنتج: <b>{order_name(pending['product'])}</b>\n💳 الطريقة: <b>{method_names.get(pending['method'],pending['method'])}</b>\n👤 العميل: {username}\n🆔 ID: <code>{cid}</code>");api.call("forwardMessage",chat_id=ADMIN_ID,from_chat_id=cid,message_id=m["message_id"]);PENDING_RECEIPTS.pop(cid,None);send_message(api,cid,"✅ <b>تم استلام إثبات الدفع</b>\n\nتم إرسال الإثبات للإدارة للمراجعة. سيتم متابعة طلبك بعد التحقق.",home_keyboard());return True

def broadcast_product(api,admin_id,args):
    parts=args.split()
    if len(parts)!=4:send_message(api,admin_id,"⚙️ <b>صيغة الإرسال</b>\n\n<code>/notify youtube 10 25 15</code>\n\nالترتيب: معرف المنتج، الكمية المضافة، المخزون الحالي، السعر");return
    pid,added,stock,price=parts;p=PRODUCTS.get(pid)
    if not p:send_message(api,admin_id,"❌ معرف المنتج غير موجود.\n\nالمتاح: <code>"+" | ".join(PRODUCTS.keys())+"</code>");return
    if pid=="chatgpt":text=(f'<tg-emoji emoji-id="5310259124817134249">🤖</tg-emoji> <b>{p["name"]}</b>\n'+f'<tg-emoji emoji-id="5397916757333654639">➕</tg-emoji> تمت الإضافة: <b>{added}</b>\n'+f'📦 المخزون الحالي: <b>{stock}</b>\n'+f'<tg-emoji emoji-id="5816492162488995555">💰</tg-emoji> السعر: <b>{price} ر.س</b>')
    else:text=f"{p['icon']} <b>{p['name']}</b>\n➕ تمت الإضافة: <b>{added}</b>\n📦 المخزون الحالي: <b>{stock}</b>\n💰 السعر: <b>{price} ر.س</b>"
    kb={"inline_keyboard":[[button("🛒 شراء الآن",f"product:{pid}")]]};users=load_users();ok=0
    for uid in users:
        if send_message(api,uid,text,kb) is not None:ok+=1
        time.sleep(.05)
    send_message(api,admin_id,f"✅ تم إرسال إشعار <b>{p['name']}</b> إلى {ok} مستخدم.")
def handle_callback(api,q):
    api.call("answerCallbackQuery",callback_query_id=q["id"]);m=q.get("message",{})
    if m.get("chat",{}).get("type")=="private":cid=m["chat"]["id"];save_user(cid);handle_action(api,cid,q.get("data","home"))
def handle_action(api,cid,action):
    if action in ("enter_store","home"):show_home(api,cid)
    elif action=="start":show_start(api,cid)
    elif action=="products":show_products(api,cid)
    elif action.startswith("product:"):show_product(api,cid,action.split(":",1)[1])
    elif action.startswith("claude:"):show_claude_product(api,cid,action.split(":",1)[1])
    elif action=="chatgpt_private":send_message(api,cid,"🔐 <b>ChatGPT Plus — حساب خاص</b>\n\nاشتراك Plus لمدة شهر.\n👤 حساب مخصص لك مع بيانات دخول خاصة.\n📅 المدة: شهر واحد\n⚡ التفعيل: بعد إتمام الطلب",{"inline_keyboard":[[button("🛒 طلب المنتج","buy:chatgpt_private")],[button("↩️ رجوع","product:chatgpt")]]})
    elif action=="chatgpt_email":send_message(api,cid,"📧 <b>ChatGPT Plus — على إيميلك</b>\n\nاشتراك Plus لمدة شهر.\n📩 يتم التفعيل على حسابك المرتبط بإيميلك.\n📅 المدة: شهر واحد\n⚡ التفعيل: بعد إتمام الطلب",{"inline_keyboard":[[button("🛒 طلب المنتج","buy:chatgpt_email")],[button("↩️ رجوع","product:chatgpt")]]})
    elif action.startswith("buy:"):PENDING_RECEIPTS.pop(cid,None);show_payment_methods(api,cid,action.split(":",1)[1])
    elif action.startswith("paybank:"):show_bank(api,cid,action.split(":",1)[1])
    elif action.startswith("paybybit:"):show_bybit(api,cid,action.split(":",1)[1])
    elif action.startswith(("bybitid:","trc20:","bep20:")):kind,pid=action.split(":",1);show_crypto(api,cid,pid,kind)
    elif action.startswith("receipt:"):_,method,pid=action.split(":",2);request_receipt(api,cid,pid,method)
    elif action=="support":send_message(api,cid,"💬 <b>الدعم الفني</b>\n\nلشحن النقاط والدعم: @SOQ_ID",home_keyboard())
    elif action=="wallet":send_message(api,cid,"👛 المحفظة\n\nالمحفظة غير مفعّلة حاليًا.",home_keyboard())
    elif action=="api":send_message(api,cid,"🔗 API\n\nسيتم إضافة إعدادات API لاحقاً.",home_keyboard())
    elif action=="warranty":send_message(api,cid,"🛡 الضمان\n\nسيتم إضافة سياسة الضمان هنا.",home_keyboard())
MENU_ACTIONS={"🚀 ابدأ":"start","🛍 المنتجات":"products","💬 الدعم":"support","👛 المحفظة":"wallet","🔗 API":"api","🛡 الضمان":"warranty"}
def main():
    token=os.getenv("BOT_TOKEN")
    if not token:raise RuntimeError("BOT_TOKEN environment variable is missing.")
    api=TelegramAPI(token);me=api.call("getMe")
    if not me:raise RuntimeError("Unable to connect to Telegram.")
    print(f"Bot @{me.get('username')} is running...");offset=0
    while True:
        try:
            updates=api.call("getUpdates",offset=offset,timeout=25,allowed_updates=["message","callback_query"])
            if not updates:continue
            for u in updates:
                offset=u["update_id"]+1
                if "callback_query" in u:handle_callback(api,u["callback_query"]);continue
                m=u.get("message",{})
                if m.get("chat",{}).get("type")!="private":continue
                cid=m["chat"]["id"];t=m.get("text","");save_user(cid)
                if handle_receipt(api,m):continue
                ce=next((e for e in m.get("entities",[]) if e.get("type")=="custom_emoji"),None)
                if t.startswith("/myid"):send_message(api,cid,f"🆔 Telegram ID: <code>{cid}</code>")
                elif t.startswith("/notify"):
                    if cid!=ADMIN_ID:send_message(api,cid,f"❌ هذا الحساب غير مصرح له بإرسال التنبيهات.\n\n🆔 Telegram ID لحسابك: <code>{cid}</code>")
                    elif t.startswith("/notify "):broadcast_product(api,cid,t.split(" ",1)[1])
                    else:broadcast_product(api,cid,"")
                elif ce:send_message(api,cid,"Emoji ID: <code>"+str(ce.get("custom_emoji_id"))+"</code>")
                elif t.startswith("/start"):show_start(api,cid)
                elif t.startswith("/products"):show_products(api,cid)
                elif t in MENU_ACTIONS:handle_action(api,cid,MENU_ACTIONS[t])
                else:show_home(api,cid)
        except KeyboardInterrupt:break
        except Exception as exc:print("Error:",exc);time.sleep(3)
if __name__=="__main__":main()
