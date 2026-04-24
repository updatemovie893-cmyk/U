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
BOT_TOKEN = "8766340099:AAHtotYPQg84aOIN4iT0Pqr4nkbkvfPGeXo"          # <-- REPLACE
ADMIN_USER_ID = 1930138915                 # <-- YOUR TELEGRAM USER ID

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
        registered_at TIMESTAMP
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

def register_user(user_id, username, first_name, referrer_code=None):
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
    c.execute("INSERT INTO users (user_id, username, first_name, referral_code, referrer_id, registered_at) VALUES (?, ?, ?, ?, ?, ?)",
              (user_id, username, first_name, referral_code, referrer_id, datetime.now()))
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

def get_user_phones(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT phone_number FROM user_phones WHERE user_id = ? ORDER BY added_at", (user_id,))
    phones = [row[0] for row in c.fetchall()]
    conn.close()
    return phones

def add_user_phone(user_id, phone_number):
    phones = get_user_phones(user_id)
    if len(phones) >= 5:
        return False, "You already have 5 phone numbers. Remove one first."
    if phone_number in phones:
        return False, "This phone number is already registered."
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO user_phones (user_id, phone_number, added_at) VALUES (?, ?, ?)",
              (user_id, phone_number, datetime.now()))
    conn.commit()
    conn.close()
    return True, "Phone number added."

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
        return f"https://t.me/{(BOT_TOKEN.split(':')[0])}?start={row[0]}"
    return None

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
def registration_keyboard():
    button = KeyboardButton("📞 Share Contact to Register", request_contact=True)
    return ReplyKeyboardMarkup([[button]], resize_keyboard=True, one_time_keyboard=True)

def main_keyboard(is_admin=False):
    buttons = [
        ["⚡ Start 50x3 (50 codes/3min)"],
        ["📞 My Phones", "➕ Add Phone via Contact"],
        ["🔗 Referral Link", "💰 My Points"],
        ["❓ Help", "🛑 Stop Task"]
    ]
    if is_admin:
        buttons.append(["👑 Admin Panel"])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def contact_addition_keyboard():
    button = KeyboardButton("📞 Share a New Phone Number", request_contact=True)
    return ReplyKeyboardMarkup([[button], ["❌ Cancel"]], resize_keyboard=True, one_time_keyboard=True)

def phone_selection_keyboard(phones):
    buttons = [[phone] for phone in phones]
    buttons.append(["❌ Cancel"])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True, one_time_keyboard=True)

def admin_inline_keyboard():
    keyboard = [
        [InlineKeyboardButton("➕ Add Points", callback_data="admin_addpoints")],
        [InlineKeyboardButton("➖ Remove Points", callback_data="admin_removepoints")],
        [InlineKeyboardButton("📊 Set Points", callback_data="admin_setpoints")],
        [InlineKeyboardButton("📋 List Users", callback_data="admin_listusers")],
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

# ------------------ Helper to notify admin about phone addition ------------------
async def notify_admin_phone_added(context: ContextTypes.DEFAULT_TYPE, user_id, username, first_name, phone):
    try:
        msg = f"📞 *New phone added by user*\n"
        msg += f"User ID: `{user_id}`\n"
        msg += f"Name: {first_name}\n"
        msg += f"Username: @{username}\n"
        msg += f"Phone: `{phone}`\n"
        msg += f"Total phones now: {len(get_user_phones(user_id))}"
        await context.bot.send_message(chat_id=ADMIN_USER_ID, text=msg, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Failed to notify admin: {e}")

# ------------------ Handlers ------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_registered(user_id):
        is_admin = (user_id == ADMIN_USER_ID)
        await update.message.reply_text(
            f"👋 Welcome back!\nYou have {get_points(user_id)} points.\nUse the buttons below.",
            reply_markup=main_keyboard(is_admin)
        )
        return

    # Handle referral
    ref_code = None
    if context.args and len(context.args) > 0:
        ref_code = context.args[0]

    username = update.effective_user.username
    first_name = update.effective_user.first_name
    register_user(user_id, username, first_name, ref_code)

    await update.message.reply_text(
        "📱 *Welcome to 50x3 OTP Bot*\n\n"
        "To start using the bot, please share your contact by pressing the button below.\n"
        "Your phone number will be stored (up to 5 numbers).\n\n"
        "After sharing, you can start the 50x3 feature.",
        reply_markup=registration_keyboard(),
        parse_mode="Markdown"
    )

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

    # If user is not yet registered in the database (should not happen because start already registers, but safe)
    if not is_registered(user_id):
        # This case should not occur, but handle gracefully
        await update.message.reply_text("Please use /start first.", reply_markup=registration_keyboard())
        return

    # Add the phone number (first or additional)
    success, msg = add_user_phone(user_id, phone)
    if success:
        # Notify admin about this phone addition (first or extra)
        await notify_admin_phone_added(context, user_id, username, first_name, phone)
        is_admin = (user_id == ADMIN_USER_ID)
        await update.message.reply_text(
            f"✅ {msg}\n\nYou now have {len(get_user_phones(user_id))} phone number(s).",
            reply_markup=main_keyboard(is_admin)
        )
    else:
        await update.message.reply_text(f"❌ {msg}", reply_markup=main_keyboard(user_id == ADMIN_USER_ID))

async def add_phone_contact_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show contact sharing keyboard to add a new phone number."""
    user_id = update.effective_user.id
    if not is_registered(user_id):
        await update.message.reply_text("You must register first. Send /start", reply_markup=registration_keyboard())
        return
    await update.message.reply_text(
        "📱 Please share your **new** phone number using the button below.\n"
        "You can add up to 5 numbers in total.",
        reply_markup=contact_addition_keyboard(),
        parse_mode="Markdown"
    )
    return WAITING_CONTACT_ADD

async def cancel_addition(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text("Addition cancelled.", reply_markup=main_keyboard(user_id == ADMIN_USER_ID))
    return ConversationHandler.END

async def start_50x3(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_registered(user_id):
        await update.message.reply_text("Please register first by sharing your contact.", reply_markup=registration_keyboard())
        return

    phones = get_user_phones(user_id)
    if not phones:
        await update.message.reply_text("You have no phone numbers. Use '➕ Add Phone via Contact' to add one first.")
        return

    if len(phones) == 1:
        phone = phones[0]
        await update.message.reply_text(
            f"⚡ Starting 50x3 OTP Service for {phone}\n\n"
            f"• 50 OTP codes every 3 minutes\n"
            f"• Daily reset at midnight\n"
            f"• Use 'Stop Task' button to stop.\n\n"
            f"First batch starting now...",
            reply_markup=main_keyboard(user_id == ADMIN_USER_ID)
        )
        task = asyncio.create_task(run_fifty_x_three_otp(user_id, phone, context))
        context.user_data["active_task"] = task
        context.user_data["stop_task_flag"] = False
    else:
        await update.message.reply_text(
            "Select a phone number:",
            reply_markup=phone_selection_keyboard(phones)
        )
        return WAITING_PHONE_SELECTION

async def phone_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "❌ Cancel":
        user_id = update.effective_user.id
        await update.message.reply_text("Cancelled.", reply_markup=main_keyboard(user_id == ADMIN_USER_ID))
        return ConversationHandler.END

    user_id = update.effective_user.id
    phones = get_user_phones(user_id)
    if text not in phones:
        await update.message.reply_text("Invalid selection. Please choose from the buttons.")
        return WAITING_PHONE_SELECTION

    phone = text
    await update.message.reply_text(
        f"⚡ Starting 50x3 OTP Service for {phone}\n\n"
        f"• 50 OTP codes every 3 minutes\n"
        f"• Daily reset at midnight\n"
        f"• Use 'Stop Task' button to stop.\n\n"
        f"First batch starting now...",
        reply_markup=main_keyboard(user_id == ADMIN_USER_ID)
    )
    task = asyncio.create_task(run_fifty_x_three_otp(user_id, phone, context))
    context.user_data["active_task"] = task
    context.user_data["stop_task_flag"] = False
    return ConversationHandler.END

async def stop_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["stop_task_flag"] = True
    user_id = update.effective_user.id
    await update.message.reply_text("⏹️ Stopping all active tasks...", reply_markup=main_keyboard(user_id == ADMIN_USER_ID))

async def my_phones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    phones = get_user_phones(user_id)
    if not phones:
        await update.message.reply_text("No phone numbers. Use '➕ Add Phone via Contact' to add.")
        return
    text = "📞 Your registered phone numbers:\n"
    for idx, p in enumerate(phones, 1):
        text += f"{idx}. {p}\n"
    text += "\nTo remove a number, use /removephone <index>"
    await update.message.reply_text(text)

async def my_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    points = get_points(user_id)
    await update.message.reply_text(
        f"💰 Your points: {points}\n\n"
        f"Points ဝယ်ယူလိုပါက ADMIN @KOEKOE4 ထံဆက်သွယ်ပါ.\n\n"
        f"Invite friends using your referral link to earn +10 points each!"
    )

async def referral_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    link = get_referral_link(user_id)
    if not link:
        await update.message.reply_text("Error generating link.")
        return
    # Inline keyboard with a URL button to open the link (easy sharing)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Share Link", url=link)],
        [InlineKeyboardButton("👤 Contact Admin", url="https://t.me/KOEKOE4")]
    ])
    await update.message.reply_text(
        f"🔗 *Your referral link:*\n{link}\n\nShare it with friends! When they register, you get +10 points.",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text(
        "📖 *Help Menu*\n\n"
        "*50x3 Feature*\n"
        "• Sends 50 OTP codes every 3 minutes (180 seconds).\n"
        "• Automatically resets at midnight (00:00).\n"
        "• Runs continuously until you press 'Stop Task'.\n"
        "• If you have multiple phone numbers, you can choose which one to use.\n\n"
        "*Points & Referral*\n"
        "• Each new user who registers using your referral link gives you +10 points.\n"
        "• Points can be purchased by contacting @KOEKOE4.\n\n"
        "*Phone Management*\n"
        "• You can store up to 5 phone numbers.\n"
        "• The first phone is added during registration.\n"
        "• Use '➕ Add Phone via Contact' to add more numbers (each shared contact is auto-forwarded to admin).\n"
        "• Use /removephone <index> to remove (see 'My Phones' for indexes).\n"
        "• Use 'My Phones' button to list all.\n\n"
        "*Admin*\n"
        "• Admin has a special 'Admin Panel' button to manage points and users.",
        reply_markup=main_keyboard(user_id == ADMIN_USER_ID),
        parse_mode="Markdown"
    )

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_USER_ID:
        await update.message.reply_text("⛔ You are not an admin.")
        return
    await update.message.reply_text(
        "👑 *Admin Panel*\nChoose an action:",
        reply_markup=admin_inline_keyboard(),
        parse_mode="Markdown"
    )

# ------------------ Admin Callback Handlers ------------------
async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id
    if user_id != ADMIN_USER_ID:
        await query.edit_message_text("⛔ Unauthorized.")
        return

    if data == "admin_addpoints":
        await query.edit_message_text("Send the user ID and points to add, separated by space.\nExample: `123456 10`")
        context.user_data["admin_action"] = "add"
        return
    elif data == "admin_removepoints":
        await query.edit_message_text("Send the user ID and points to remove, separated by space.\nExample: `123456 5`")
        context.user_data["admin_action"] = "remove"
        return
    elif data == "admin_setpoints":
        await query.edit_message_text("Send the user ID and new points value, separated by space.\nExample: `123456 100`")
        context.user_data["admin_action"] = "set"
        return
    elif data == "admin_listusers":
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT user_id, first_name, points FROM users ORDER BY points DESC LIMIT 50")
        rows = c.fetchall()
        conn.close()
        if not rows:
            msg = "No users."
        else:
            msg = "📊 *User List (points)*:\n"
            for row in rows:
                msg += f"ID: `{row[0]}` | {row[1]} | {row[2]} pts\n"
        await query.edit_message_text(msg, parse_mode="Markdown")
        return
    elif data == "admin_close":
        await query.edit_message_text("Admin panel closed.", reply_markup=main_keyboard(True))
        return

async def admin_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    action = context.user_data.get("admin_action")
    if not action:
        return
    text = update.message.text.strip()
    parts = text.split()
    if len(parts) != 2:
        await update.message.reply_text("Invalid format. Please send: `user_id points`", parse_mode="Markdown")
        return
    try:
        target_id = int(parts[0])
        points = int(parts[1])
    except:
        await update.message.reply_text("Invalid numbers.")
        return
    if action == "add":
        update_points(target_id, points)
        await update.message.reply_text(f"✅ Added {points} points to user {target_id}.")
    elif action == "remove":
        update_points(target_id, -points)
        await update.message.reply_text(f"✅ Removed {points} points from user {target_id}.")
    elif action == "set":
        set_points(target_id, points)
        await update.message.reply_text(f"✅ Set points of user {target_id} to {points}.")
    context.user_data["admin_action"] = None
    # Return to main keyboard
    user_id = update.effective_user.id
    await update.message.reply_text("Admin action completed.", reply_markup=main_keyboard(True))

# ------------------ Remove Phone Command ------------------
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
    await update.message.reply_text(msg)

# ------------------ Main ------------------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Conversation for phone selection (multiple numbers)
    phone_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^⚡ Start 50x3 \(50 codes/3min\)$"), start_50x3)],
        states={
            WAITING_PHONE_SELECTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, phone_selection)]
        },
        fallbacks=[CommandHandler("cancel", stop_task)],
        allow_reentry=True
    )

    # Conversation for adding phone via contact
    add_contact_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^➕ Add Phone via Contact$"), add_phone_contact_button)],
        states={
            WAITING_CONTACT_ADD: [MessageHandler(filters.CONTACT, contact_handler),
                                  MessageHandler(filters.Regex(r"^❌ Cancel$"), cancel_addition)]
        },
        fallbacks=[CommandHandler("cancel", cancel_addition)],
        allow_reentry=True
    )

    # Admin text input handler
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_text_input), group=1)

    # Command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop_task", stop_task))
    app.add_handler(CommandHandler("removephone", remove_phone_command))
    app.add_handler(CommandHandler("myphones", my_phones))
    app.add_handler(CommandHandler("points", my_points))
    app.add_handler(CommandHandler("referral", referral_link))
    app.add_handler(CommandHandler("help", help_command))

    # Message handlers for buttons
    app.add_handler(MessageHandler(filters.CONTACT, contact_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^⚡ Start 50x3 \(50 codes/3min\)$"), start_50x3))
    app.add_handler(MessageHandler(filters.Regex(r"^📞 My Phones$"), my_phones))
    app.add_handler(MessageHandler(filters.Regex(r"^➕ Add Phone via Contact$"), add_phone_contact_button))
    app.add_handler(MessageHandler(filters.Regex(r"^🔗 Referral Link$"), referral_link))
    app.add_handler(MessageHandler(filters.Regex(r"^💰 My Points$"), my_points))
    app.add_handler(MessageHandler(filters.Regex(r"^❓ Help$"), help_command))
    app.add_handler(MessageHandler(filters.Regex(r"^🛑 Stop Task$"), stop_task))
    app.add_handler(MessageHandler(filters.Regex(r"^👑 Admin Panel$"), admin_panel))

    app.add_handler(phone_conv)
    app.add_handler(add_contact_conv)

    # Callback query handler for admin inline keyboard
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin_"))

    logger.info("Bot started with full contact sharing, automatic admin forwarding, and 50x3 only.")
    app.run_polling()

if __name__ == "__main__":
    main()