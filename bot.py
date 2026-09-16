"""SAU2030 Telegram Store Bot - Arabic/English."""
import json, os, time, urllib.request
from pathlib import Path
BASE=Path(__file__).resolve().parent
CONFIG=json.loads((BASE/"catalog.json").read_text(encoding="utf-8"))
ADMIN_ID=8386371522
USERS_FILE=Path("/data/users.json");LANG_FILE=Path("/data/languages.json");PENDING_RECEIPTS={}
FALLBACK_ICONS={"chatgpt":"🤖","youtube":"▶️","canva":"🎨","gemini":"✨","spotify":"🎵","capcut":"🎬","claude":"✳️","grok":"✖️","netflix":"📺","iptv":"📡"}
PRODUCTS={x["id"]:dict(x,icon=FALLBACK_ICONS.get(x["id"],"▫️")) for x in CONFIG["products"]}
PRODUCT_EN={"chatgpt":"ChatGPT","youtube":"YouTube","canva":"Canva","gemini":"Gemini","capcut":"CapCut","claude":"Claude","grok":"Grok","netflix":"NETFLIX","iptv":"IPTV"}
CAPCUT={"capcut_6m":{"ar":"CapCut Pro — حساب فردي لمدة 6 أشهر","en":"CapCut Pro — Individual Account for 6 Months","price":"46.88","ar_desc":"حساب فردي لمدة 6 أشهر.\nضمان لمدة 3 أشهر.\nحساب مستقر للغاية.","en_desc":"Individual account for 6 months.\n3-month warranty.\nHighly stable account."},"capcut_1m_1600":{"ar":"CapCut Pro — شهر + 1600 وحدة ائتمان","en":"CapCut Pro — 1 Month + 1600 Credits","price":"13.69","ar_desc":"اشتراك CapCut Pro لمدة شهر مع 1600 وحدة ائتمان.\nضمان كامل.","en_desc":"CapCut Pro for one month with 1600 credits.\nFull warranty."},"capcut_1m":{"ar":"CapCut Pro — شهر واحد","en":"CapCut Pro — 1 Month","price":"11.21","ar_desc":"حساب Pro خاص لمدة 30 يومًا.\nيمكن استخدامه على جهازين كحد أقصى، مع ضمان كامل.\nيتم تسجيل الدخول أولًا من تطبيق الهاتف، ثم تطبيق سطح المكتب عبر QR.","en_desc":"Private Pro account for 30 days.\nCan be used on up to 2 devices with full warranty.\nSign in on the mobile app first, then the desktop app using QR."},"capcut_7d":{"ar":"CapCut Pro — 7 أيام","en":"CapCut Pro — 7 Days","price":"6.38","ar_desc":"حساب CapCut لمدة 7 أيام.\nيمكن استخدامه على جهازين كحد أقصى، مع ضمان كامل.\nتتوفر طلبات مسبقة.","en_desc":"CapCut account for 7 days.\nCan be used on up to 2 devices with full warranty.\nPre-orders are available."}}
CLAUDE={"claude_pro":{"ar":"إعادة شحن Claude Pro الرسمية لمدة شهر","en":"Official Claude Pro Recharge — 1 Month"},"claude_api_500m":{"ar":"Claude API — 500 مليون توكن — 6 أيام","en":"Claude API — 500M Tokens — 6 Days"},"claude_api_100m":{"ar":"Claude API — 100 مليون توكن — 3 أيام","en":"Claude API — 100M Tokens — 3 Days"},"claude_api_50m":{"ar":"Claude API — 50 مليون توكن — يومان","en":"Claude API — 50M Tokens — 2 Days"},"claude_api_10m":{"ar":"Claude API — 10 ملايين توكن — يوم واحد","en":"Claude API — 10M Tokens — 1 Day"}}
def load_json(path,default):
 try:return json.loads(path.read_text(encoding="utf-8"))
 except Exception:return default
def save_user(cid):
 u=set(load_json(USERS_FILE,[]));u.add(cid)
 try:USERS_FILE.write_text(json.dumps(sorted(u)),encoding="utf-8")
 except Exception:pass
def lang(cid):return load_json(LANG_FILE,{}).get(str(cid),"ar")
def set_lang(cid,value):
 d=load_json(LANG_FILE,{});d[str(cid)]=value
 try:LANG_FILE.write_text(json.dumps(d),encoding="utf-8")
 except Exception:pass
def tr(cid,ar,en):return en if lang(cid)=="en" else ar
class TelegramAPI:
 def __init__(self,token):self.base_url=f"https://api.telegram.org/bot{token}/"
 def call(self,method,**data):
  req=urllib.request.Request(self.base_url+method,json.dumps(data).encode(),{"Content-Type":"application/json"})
  try:
   with urllib.request.urlopen(req,timeout=40) as r:return json.load(r).get("result")
  except Exception as e:print("Telegram API error:",type(e).__name__);time.sleep(1)
def button(text,cb):return {"text":text,"callback_data":cb}
def send(api,cid,text,kb=None):
 d={"chat_id":cid,"text":text,"parse_mode":"HTML"}
 if kb:d["reply_markup"]=kb
 return api.call("sendMessage",**d)
def home_keyboard(cid):
 en=lang(cid)=="en";return {"keyboard":[[{"text":"🚀 Start" if en else "🚀 ابدأ"},{"text":"🛍 Products" if en else "🛍 المنتجات"},{"text":"💬 Support" if en else "💬 الدعم"}],[{"text":"👛 Wallet" if en else "👛 المحفظة"},{"text":"🔗 API"}],[{"text":"🛡 Warranty" if en else "🛡 الضمان"},{"text":"🌐 Language" if en else "🌐 اللغة"}]],"resize_keyboard":True,"is_persistent":True}
def show_language(api,cid):send(api,cid,"🌐 <b>Choose your language | اختر لغتك</b>",{"inline_keyboard":[[button("🇸🇦 العربية","lang:ar"),button("🇺🇸 English","lang:en")]]})
def show_start(api,cid):send(api,cid,"👋 <b>Welcome to VEXA STORE | مرحباً بك في VEXA STORE</b>\n\n🌐 Choose your language | اختر لغتك",{"inline_keyboard":[[button("🇸🇦 العربية","lang:ar"),button("🇺🇸 English","lang:en")]]})
def product_button(cid,pid,p):
 name=PRODUCT_EN.get(pid,p["name"]) if lang(cid)=="en" else p["name"];b={"text":name,"callback_data":f"product:{pid}"};eid=p.get("custom_emoji_id")
 if CONFIG.get("custom_icons_enabled") and eid:b["icon_custom_emoji_id"]=str(eid)
 return b
def show_products(api,cid):
 items=list(PRODUCTS.items());rows=[]
 for i in range(0,len(items),3):rows.append([product_button(cid,pid,p) for pid,p in items[i:i+3]])
 rows.append([button(tr(cid,"🏠 الرئيسية","🏠 Home"),"home")]);send(api,cid,tr(cid,"🛍 <b>المنتجات</b>\n\nاختر الخدمة:","🛍 <b>Products</b>\n\nChoose a service:"),{"inline_keyboard":rows})
def show_home(api,cid):
 send(api,cid,tr(cid,"👋 أهلاً بك في <b>VEXA STORE</b>\n\n🛍 متجر الخدمات الرقمية\nاختر القسم المطلوب.\n\n💳 <b>لشحن النقاط والدعم:</b> @SOQ_ID","👋 Welcome to <b>VEXA STORE</b>\n\n🛍 Digital services store\nChoose a section.\n\n💳 <b>Top-ups & support:</b> @SOQ_ID"),home_keyboard(cid));show_products(api,cid)
def show_product(api,cid,pid):
 p=PRODUCTS.get(pid)
 if not p:return show_products(api,cid)
 if pid=="capcut":
  rows=[[button(f"{x['en' if lang(cid)=='en' else 'ar']} — {x['price']} SAR",f"capcut:{k}")] for k,x in CAPCUT.items()];rows.append([button(tr(cid,"↩️ العودة إلى المنتجات","↩️ Back to Products"),"products")]);return send(api,cid,tr(cid,"🎬 <b>CapCut Pro | كاب كات برو</b>\n\nاختر المنتج المطلوب 👇","🎬 <b>CapCut Pro</b>\n\nChoose a product 👇"),{"inline_keyboard":rows})
 if pid=="claude":
  rows=[[button(x["en" if lang(cid)=="en" else "ar"],f"claude:{k}")] for k,x in CLAUDE.items()];rows.append([button(tr(cid,"↩️ العودة إلى المنتجات","↩️ Back to Products"),"products")]);return send(api,cid,tr(cid,"✳️ <b>Claude | كلود</b>\n\nاختر المنتج المطلوب 👇","✳️ <b>Claude</b>\n\nChoose a product 👇"),{"inline_keyboard":rows})
 if pid=="chatgpt":
  rows=[[button(tr(cid,"🔐 بلس شهر • حساب خاص","🔐 Plus 1 Month • Private Account"),"chatgpt_private")],[button(tr(cid,"📧 بلس شهر • على إيميلك","📧 Plus 1 Month • Your Email"),"chatgpt_email")],[button(tr(cid,"↩️ العودة إلى المنتجات","↩️ Back to Products"),"products")]];return send(api,cid,tr(cid,"🤖 <b>ChatGPT Plus | شات جي بي تي بلس</b>\n\nاختر نوع الاشتراك المناسب لك.","🤖 <b>ChatGPT Plus</b>\n\nChoose your subscription type."),{"inline_keyboard":rows})
 name=PRODUCT_EN.get(pid,p["name"]) if lang(cid)=="en" else p["name"];desc=p.get("description","");price=f"{p['price']} {p.get('currency','SAR')}" if p.get("price") is not None else tr(cid,"يُضاف لاحقاً","Coming soon")
 send(api,cid,f"{p['icon']} <b>{name}</b>\n💵 {tr(cid,'السعر','Price')}: <b>{price}</b>\n\n💦 <b>{tr(cid,'الوصف','Description')}:</b>\n{desc}",{"inline_keyboard":[[button(tr(cid,"🛒 شراء الآن","🛒 Buy Now"),f"buy:{pid}")],[button(tr(cid,"💬 الدعم الفني","💬 Support"),"support")],[button(tr(cid,"↩️ العودة إلى المنتجات","↩️ Back to Products"),"products")]]})
def show_capcut(api,cid,pid):
 x=CAPCUT.get(pid)
 if not x:return show_product(api,cid,"capcut")
 en=lang(cid)=="en";send(api,cid,f"🎬 <b>{x['en' if en else 'ar']}</b>\n\n💵 {tr(cid,'السعر','Price')}: <b>{x['price']} SAR</b>\n\n💦 <b>{tr(cid,'الوصف','Description')}:</b>\n{x['en_desc' if en else 'ar_desc']}",{"inline_keyboard":[[button(tr(cid,"🛒 شراء الآن","🛒 Buy Now"),f"buy:{pid}")],[button(tr(cid,"↩️ رجوع إلى CapCut","↩️ Back to CapCut"),"product:capcut")]]})
def show_claude(api,cid,pid):
 x=CLAUDE.get(pid)
 if not x:return show_product(api,cid,"claude")
 if pid=="claude_pro":text=tr(cid,"🛍 <b>إعادة شحن Claude Pro الرسمية لمدة شهر</b>\n\nرمز تفعيل رسمي (CDK) لاشتراك Claude Pro لمدة شهر. يجب ألا يكون لديك اشتراك نشط أو فواتير غير مدفوعة. الضمان للاشتراك فقط ولا يشمل حظر الحساب.","🛍 <b>Official Claude Pro Recharge — 1 Month</b>\n\nOfficial CDK activation for one month of Claude Pro. Your account must not have an active subscription or unpaid invoices. Warranty covers the subscription only and does not cover account bans.")
 else:text=f"✳️ <b>{x['en' if lang(cid)=='en' else 'ar']}</b>\n\n{tr(cid,'متوافق مع Claude Dev وCursor وVS Code و9router وغيرها.','Compatible with Claude Dev, Cursor, VS Code, 9router and more.')}"
 send(api,cid,text,{"inline_keyboard":[[button(tr(cid,"🛒 طلب المنتج","🛒 Order Product"),f"buy:{pid}")],[button(tr(cid,"↩️ رجوع إلى Claude","↩️ Back to Claude"),"product:claude")]]})
def order_name(cid,pid):
 if pid in CAPCUT:return CAPCUT[pid]["en" if lang(cid)=="en" else "ar"]
 if pid in CLAUDE:return CLAUDE[pid]["en" if lang(cid)=="en" else "ar"]
 names={"chatgpt_private":tr(cid,"ChatGPT Plus — حساب خاص","ChatGPT Plus — Private Account"),"chatgpt_email":tr(cid,"ChatGPT Plus — على إيميلك","ChatGPT Plus — Your Email")};return names.get(pid,PRODUCT_EN.get(pid,pid) if lang(cid)=="en" else PRODUCTS.get(pid,{}).get("name",pid))
def back_action(pid):
 if pid.startswith("chatgpt_"):return "product:chatgpt"
 if pid.startswith("capcut_"):return "product:capcut"
 if pid.startswith("claude_"):return "product:claude"
 return f"product:{pid}"
def show_payment(api,cid,pid):
 bank={"text":tr(cid,"تحويل بنكي — الراجحي","Bank Transfer — Al Rajhi"),"callback_data":f"paybank:{pid}","icon_custom_emoji_id":"5452024291472196683"};bybit={"text":"USDT — Bybit","callback_data":f"paybybit:{pid}","icon_custom_emoji_id":"5472387796574418157"};send(api,cid,f"💳 <b>{tr(cid,'اختر طريقة الدفع','Choose Payment Method')}</b>\n\n🛒 {tr(cid,'المنتج','Product')}: <b>{order_name(cid,pid)}</b>",{"inline_keyboard":[[bank],[bybit],[button(tr(cid,"↩️ رجوع","↩️ Back"),back_action(pid))]]})
def pv(n):return os.getenv(n,"Not configured")
def show_bank(api,cid,pid):send(api,cid,tr(cid,f"🏦 <b>التحويل البنكي — مصرف الراجحي</b>\n\nاسم الحساب: <b>{pv('PAYMENT_BANK_HOLDER')}</b>\nرقم الحساب:\n<code>{pv('PAYMENT_BANK_ACCOUNT')}</code>\n\nالآيبان:\n<code>{pv('PAYMENT_BANK_IBAN')}</code>\n\n⚠️ تأكد من البيانات قبل التحويل.",f"🏦 <b>Bank Transfer — Al Rajhi</b>\n\nAccount holder: <b>{pv('PAYMENT_BANK_HOLDER')}</b>\nAccount number:\n<code>{pv('PAYMENT_BANK_ACCOUNT')}</code>\n\nIBAN:\n<code>{pv('PAYMENT_BANK_IBAN')}</code>\n\n⚠️ Verify the details before transferring."),{"inline_keyboard":[[button(tr(cid,"✅ تم التحويل","✅ Transfer Complete"),f"receipt:bank:{pid}")],[button(tr(cid,"↩️ طرق الدفع","↩️ Payment Methods"),f"buy:{pid}")]]})
def show_bybit(api,cid,pid):send(api,cid,tr(cid,"🪙 <b>USDT — Bybit</b>\n\nاختر طريقة إرسال USDT:","🪙 <b>USDT — Bybit</b>\n\nChoose how to send USDT:"),{"inline_keyboard":[[button("🟡 Bybit Pay",f"bybitid:{pid}")],[button("🔴 USDT • TRON (TRC20)",f"trc20:{pid}")],[button("🟡 USDT • BSC (BEP20)",f"bep20:{pid}")],[button(tr(cid,"↩️ طرق الدفع","↩️ Payment Methods"),f"buy:{pid}")]]})
def show_crypto(api,cid,pid,kind):
 key={"bybitid":"PAYMENT_BYBIT_PAY_ID","trc20":"PAYMENT_USDT_TRC20","bep20":"PAYMENT_USDT_BEP20"}[kind];label="Bybit Pay ID" if kind=="bybitid" else "Wallet Address";warn=tr(cid,"تأكد من الشبكة والمعرف قبل الإرسال.","Verify the network and address/ID before sending.");send(api,cid,f"🪙 <b>USDT</b>\n\n{label}:\n<code>{pv(key)}</code>\n\n⚠️ {warn}",{"inline_keyboard":[[button(tr(cid,"✅ تم التحويل","✅ Transfer Complete"),f"receipt:{kind}:{pid}")],[button("↩️ Bybit",f"paybybit:{pid}")]]})
def request_receipt(api,cid,pid,method):PENDING_RECEIPTS[cid]={"product":pid,"method":method};send(api,cid,tr(cid,"📸 <b>إرسال إثبات الدفع</b>\n\nأرسل الآن صورة إثبات التحويل.","📸 <b>Send Payment Proof</b>\n\nSend a photo of your payment receipt now."))
def handle_receipt(api,m):
 cid=m["chat"]["id"];x=PENDING_RECEIPTS.get(cid)
 if not x:return False
 if not m.get("photo"):send(api,cid,tr(cid,"📸 فضلاً أرسل صورة إثبات الدفع.","📸 Please send a photo of the payment receipt."));return True
 u=m.get("from",{});username="@"+u["username"] if u.get("username") else "No username";send(api,ADMIN_ID,f"🧾 <b>إثبات دفع جديد</b>\n🛒 {order_name(cid,x['product'])}\n👤 {username}\n🆔 <code>{cid}</code>");api.call("forwardMessage",chat_id=ADMIN_ID,from_chat_id=cid,message_id=m["message_id"]);PENDING_RECEIPTS.pop(cid,None);send(api,cid,tr(cid,"✅ تم استلام إثبات الدفع وإرساله للإدارة.","✅ Payment proof received and sent to the administration."),home_keyboard(cid));return True
def handle_action(api,cid,a):
 if a.startswith("lang:"):set_lang(cid,a.split(":")[1]);show_home(api,cid)
 elif a in ("home","enter_store"):show_home(api,cid)
 elif a=="start":show_start(api,cid)
 elif a=="products":show_products(api,cid)
 elif a=="language":show_language(api,cid)
 elif a.startswith("product:"):show_product(api,cid,a.split(":",1)[1])
 elif a.startswith("capcut:"):show_capcut(api,cid,a.split(":",1)[1])
 elif a.startswith("claude:"):show_claude(api,cid,a.split(":",1)[1])
 elif a in ("chatgpt_private","chatgpt_email"):
  title=order_name(cid,a);send(api,cid,f"🤖 <b>{title}</b>\n\n{tr(cid,'اشتراك Plus لمدة شهر. التفعيل بعد إتمام الطلب.','Plus subscription for one month. Activation after completing the order.')}",{"inline_keyboard":[[button(tr(cid,"🛒 طلب المنتج","🛒 Order Product"),f"buy:{a}")],[button(tr(cid,"↩️ رجوع","↩️ Back"),"product:chatgpt")]]})
 elif a.startswith("buy:"):PENDING_RECEIPTS.pop(cid,None);show_payment(api,cid,a.split(":",1)[1])
 elif a.startswith("paybank:"):show_bank(api,cid,a.split(":",1)[1])
 elif a.startswith("paybybit:"):show_bybit(api,cid,a.split(":",1)[1])
 elif a.startswith(("bybitid:","trc20:","bep20:")):kind,pid=a.split(":",1);show_crypto(api,cid,pid,kind)
 elif a.startswith("receipt:"):_,method,pid=a.split(":",2);request_receipt(api,cid,pid,method)
 elif a=="support":send(api,cid,tr(cid,"💬 <b>الدعم الفني</b>\n\nللدعم: @SOQ_ID","💬 <b>Support</b>\n\nSupport: @SOQ_ID"),home_keyboard(cid))
 elif a=="wallet":send(api,cid,tr(cid,"👛 المحفظة غير مفعّلة حاليًا.","👛 Wallet is currently unavailable."),home_keyboard(cid))
 elif a=="api":send(api,cid,tr(cid,"🔗 سيتم إضافة إعدادات API لاحقاً.","🔗 API settings will be added later."),home_keyboard(cid))
 elif a=="warranty":send(api,cid,tr(cid,"🛡 سيتم إضافة سياسة الضمان هنا.","🛡 Warranty policy will be added here."),home_keyboard(cid))
MENU={"🚀 ابدأ":"start","🚀 Start":"start","🛍 المنتجات":"products","🛍 Products":"products","💬 الدعم":"support","💬 Support":"support","👛 المحفظة":"wallet","👛 Wallet":"wallet","🔗 API":"api","🛡 الضمان":"warranty","🛡 Warranty":"warranty","🌐 اللغة":"language","🌐 Language":"language"}
def main():
 token=os.getenv("BOT_TOKEN")
 if not token:raise RuntimeError("BOT_TOKEN missing")
 api=TelegramAPI(token);print("Bot running...");offset=0
 while True:
  try:
   updates=api.call("getUpdates",offset=offset,timeout=25,allowed_updates=["message","callback_query"]) or []
   for u in updates:
    offset=u["update_id"]+1
    if "callback_query" in u:
     q=u["callback_query"];api.call("answerCallbackQuery",callback_query_id=q["id"]);cid=q.get("message",{}).get("chat",{}).get("id");
     if cid:save_user(cid);handle_action(api,cid,q.get("data","home"))
     continue
    m=u.get("message",{});
    if m.get("chat",{}).get("type")!="private":continue
    cid=m["chat"]["id"];t=m.get("text","");save_user(cid)
    if handle_receipt(api,m):continue
    if t.startswith("/start"):show_start(api,cid)
    elif t.startswith("/products"):show_products(api,cid)
    elif t in MENU:handle_action(api,cid,MENU[t])
    else:show_home(api,cid)
  except KeyboardInterrupt:break
  except Exception as e:print("Error:",e);time.sleep(3)
if __name__=="__main__":main()
