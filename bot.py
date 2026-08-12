import os
import logging
import re
import asyncio
import aiohttp
from aiohttp import web
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

# Logging configuration
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is not set!")

API_BASE_URL = "https://api.tempmail.fish/emails"

# Global Persistent HTTP Session for Maximum Speed
http_session: aiohttp.ClientSession = None


def get_reply_keyboard():
    keyboard = [
        [KeyboardButton("🌐 ডোমেইন সিলেক্ট করুন")],
        [KeyboardButton("✉️ ১টি ইমেইল"), KeyboardButton("📦 ২টি ইমেইল")],
        [KeyboardButton("📋 আমার ইমেইলসমূহ")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def get_domain_inline_keyboard():
    keyboard = [
        [InlineKeyboardButton("🎲 যেকোনো ডোমেইন (Default)", callback_data="domain_any")],
        [InlineKeyboardButton("🐟 tempmail.fish", callback_data="domain_tempmail.fish")],
        [InlineKeyboardButton("🌊 calmriver.info", callback_data="domain_calmriver.info")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_check_inbox_inline_keyboard():
    keyboard = [
        [InlineKeyboardButton("🔄 Check Inbox", callback_data="inline_check_inbox")]
    ]
    return InlineKeyboardMarkup(keyboard)


def extract_smart_otp(subject, body):
    subject_codes = re.findall(r'\b\d{4,8}\b', subject)
    if subject_codes:
        return f"`{subject_codes[0]}`"

    clean_body = re.sub(r'<[^>]+>', ' ', body)
    keyword_match = re.search(r'(?:code|otp|is|verification|confirm|is your code|code is)\s*[:\-\s]?\s*(\d{4,8})', clean_body, re.IGNORECASE)
    if keyword_match:
        return f"`{keyword_match.group(1)}`"

    body_codes = re.findall(r'\b\d{4,8}\b', clean_body)
    if body_codes:
        return f"`{body_codes[0]}`"

    return "পাওয়া যায়নি"


async def fetch_single_api_email():
    """Helper to perform a fast single API call with reduced timeout."""
    try:
        async with http_session.post(f"{API_BASE_URL}/new-email", timeout=aiohttp.ClientTimeout(total=4)) as response:
            if response.status in [200, 201]:
                data = await response.json()
                return {"email": data.get("email"), "auth_key": data.get("authKey")}
    except Exception as e:
        logging.error(f"API Fetch Error: {e}")
    return None


async def fetch_new_email(target_domain=None, max_attempts=6):
    """Ultra-fast concurrent attempt for target domain."""
    if not target_domain or target_domain == "any":
        return await fetch_single_api_email()

    for _ in range(max_attempts):
        res = await fetch_single_api_email()
        if res and res["email"].endswith(f"@{target_domain}"):
            return res

    return await fetch_single_api_email()


async def fetch_inbox(email, auth_key):
    headers = {"Authorization": auth_key}
    params = {"emailAddress": email}
    try:
        async with http_session.get(f"{API_BASE_URL}/emails", headers=headers, params=params, timeout=aiohttp.ClientTimeout(total=4)) as response:
            if response.status == 200:
                emails = await response.json()
                return {"email": email, "emails": emails}
    except Exception as e:
        logging.error(f"Error fetching inbox for {email}: {e}")
    return {"email": email, "emails": []}


async def process_inbox_check(context):
    email_list = context.user_data.get("email_list", [])

    if not email_list:
        return "📂 প্রথমে একটি ইমেইল জেনারেট করুন।"

    tasks = [fetch_inbox(item["email"], item["auth_key"]) for item in email_list]
    inbox_results = await asyncio.gather(*tasks)

    total_messages_found = False
    full_response_text = ""

    for res in inbox_results:
        email = res["email"]
        emails = res["emails"]

        if emails:
            total_messages_found = True
            full_response_text += (
                f"📬 **ইনবক্স:** `{email}`\n"
                "━━━━━━━━━━━━━━━━━━━\n"
            )
            
            for idx, mail in enumerate(emails, 1):
                sender = mail.get("from", "অজানা")
                subject = mail.get("subject", "বিষয় ছাড়া")
                body = mail.get("textBody", "")

                extracted_code = extract_smart_otp(subject, body)

                full_response_text += (
                    f"📩 **মেসেজ #{idx}**\n"
                    f"👤 **প্রাপক:** {sender}\n"
                    f"📌 **বিষয়:** {subject}\n"
                    f"🔑 **OTP Code:** {extracted_code}\n"
                    "───────────────────\n\n"
                )

    if not total_messages_found:
        return "📭 **কোনো নতুন মেসেজ পাওয়া যায়নি।**"
    
    return full_response_text


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "✨ ━━━━━━━━━━━━━━━━━━ ✨\n"
        "      📮 **TEMP MAIL SERVICE** 📮\n"
        "✨ ━━━━━━━━━━━━━━━━━━ ✨\n\n"
        "👋 **স্বাগতম!**\n"
        "নিচের অপশনগুলো থেকে প্রয়োজনীয় বাটন নির্বাচন করুন:"
    )
    await update.message.reply_text(
        welcome_text, 
        parse_mode="Markdown", 
        reply_markup=get_reply_keyboard()
    )


async def inline_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data

    if data.startswith("domain_"):
        selected_domain = data.replace("domain_", "")
        context.user_data["selected_domain"] = selected_domain
        domain_display = "Any Domain (Default)" if selected_domain == "any" else f"@{selected_domain}"

        await query.edit_message_text(
            f"✅ **পছন্দকৃত ডোমেইন সেট করা হয়েছে:** `{domain_display}`\n\n"
            "এখন ইমেইল নিলে এই ডোমেইন প্রাধান্য পাবে।",
            parse_mode="Markdown"
        )

    elif data == "inline_check_inbox":
        msg_text = await process_inbox_check(context)
        await query.message.reply_text(
            msg_text, 
            parse_mode="Markdown", 
            reply_markup=get_check_inbox_inline_keyboard()
        )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if "email_list" not in context.user_data:
        context.user_data["email_list"] = []

    selected_domain = context.user_data.get("selected_domain", "any")

    if text == "🌐 ডোমেইন সিলেক্ট করুন":
        current_dom = "Any Domain" if selected_domain == "any" else f"@{selected_domain}"
        msg = f"🌐 **আপনার পছন্দমত ডোমেইন বেছে নিন:**\nবর্তমান পছন্দ: `{current_dom}`"
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_domain_inline_keyboard())

    elif text == "✉️ ১টি ইমেইল":
        email_data = await fetch_new_email(target_domain=selected_domain)

        if email_data:
            context.user_data["email_list"].append(email_data)
            msg_text = (
                "🎯 **নতুন ইমেইল প্রস্তুত**\n"
                "───────────────────\n"
                f"📧 `{email_data['email']}`\n"
                "───────────────────\n"
                "💡 *কপি করতে ইমেইলের ওপর ট্যাপ করুন।*"
            )
            await update.message.reply_text(
                msg_text, 
                parse_mode="Markdown", 
                reply_markup=get_check_inbox_inline_keyboard()
            )
        else:
            await update.message.reply_text("❌ সার্ভার ব্যস্ত আছে। অনুগ্রহ করে কয়েক সেকেন্ড পর আবার চেষ্টা করুন।")

    elif text == "📦 ২টি ইমেইল":
        # Run 2 requests concurrently for extreme speed
        tasks = [fetch_new_email(target_domain=selected_domain) for _ in range(2)]
        results = await asyncio.gather(*tasks)

        created_emails = []
        for email_data in results:
            if email_data:
                context.user_data["email_list"].append(email_data)
                created_emails.append(email_data["email"])

        if created_emails:
            emails_formatted = "\n".join([f"🔹 `{e}`" for e in created_emails])
            msg_text = (
                "📦 **নতুন ২টি ইমেইল প্রস্তুত**\n"
                "───────────────────\n"
                f"{emails_formatted}\n"
                "───────────────────\n"
                "💡 *কপি করতে ইমেইলের ওপর ট্যাপ করুন।*"
            )
            await update.message.reply_text(
                msg_text, 
                parse_mode="Markdown", 
                reply_markup=get_check_inbox_inline_keyboard()
            )
        else:
            await update.message.reply_text("❌ ইমেইল তৈরি করতে সমস্যা হয়েছে। আবার চেষ্টা করুন।")

    elif text == "📋 আমার ইমেইলসমূহ":
        email_list = context.user_data.get("email_list", [])
        if not email_list:
            await update.message.reply_text("📂 আপনার কোনো সক্রিয় ইমেইল নেই।")
            return

        msg_text = f"📋 **সক্রিয় ইমেইল তালিকা** ({len(email_list)}টি)\n"
        msg_text += "───────────────────\n"
        for idx, item in enumerate(email_list, 1):
            msg_text += f"{idx}. `{item['email']}`\n"
        msg_text += "───────────────────"

        await update.message.reply_text(msg_text, parse_mode="Markdown", reply_markup=get_reply_keyboard())


async def health_check(request):
    return web.Response(text="Bot is running successfully!")


async def main():
    global http_session
    http_session = aiohttp.ClientSession()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(inline_callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    web_app = web.Application()
    web_app.router.add_get("/", health_check)

    port = int(os.getenv("PORT", 8080))
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    logging.info(f"Bot running on port {port}...")

    try:
        await asyncio.Event().wait()
    finally:
        await http_session.close()


if __name__ == "__main__":
    asyncio.run(main())
