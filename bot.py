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

# Global Persistent HTTP Session
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


def get_check_inbox_inline_keyboard(email):
    """Generates an Inline Check Inbox button carrying the specific email address."""
    keyboard = [
        [InlineKeyboardButton("🔄 Check Inbox", callback_data=f"chk_inbox_{email}")]
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


async def single_api_call():
    """Ultra-fast single API query."""
    try:
        async with http_session.post(f"{API_BASE_URL}/new-email", timeout=aiohttp.ClientTimeout(total=8)) as response:
            if response.status in [200, 201]:
                data = await response.json()
                return {"email": data.get("email"), "auth_key": data.get("authKey")}
    except Exception as e:
        logging.error(f"API Call Failed: {e}")
    return None


async def fetch_new_email(target_domain=None):
    """Parallel domain fetching for instantaneous response."""
    if not target_domain or target_domain == "any":
        res = await single_api_call()
        if not res:
            res = await single_api_call() # Retry once
        return res

    tasks = [single_api_call() for _ in range(3)]
    results = await asyncio.gather(*tasks)

    for res in results:
        if res and res["email"].endswith(f"@{target_domain}"):
            return res

    for res in results:
        if res:
            return res

    return await single_api_call()


async def fetch_inbox(email, auth_key):
    headers = {"Authorization": auth_key}
    params = {"emailAddress": email}
    try:
        async with http_session.get(f"{API_BASE_URL}/emails", headers=headers, params=params, timeout=aiohttp.ClientTimeout(total=8)) as response:
            if response.status == 200:
                emails = await response.json()
                return {"email": email, "emails": emails}
    except Exception as e:
        logging.error(f"Error fetching inbox for {email}: {e}")
    return {"email": email, "emails": []}


async def process_single_inbox_check(context, target_email):
    """Checks inbox only for the requested specific email."""
    email_list = context.user_data.get("email_list", [])

    # Find the targeted email data from saved list
    email_item = next((item for item in email_list if item["email"] == target_email), None)

    if not email_item:
        return f"❌ **`{target_email}`** ইমেইলটি বর্তমানে সক্রিয় তালিকায় নেই।"

    res = await fetch_inbox(email_item["email"], email_item["auth_key"])
    emails = res["emails"]

    if emails:
        full_response_text = f"📬 **ইনবক্স:** `{target_email}`\n━━━━━━━━━━━━━━━━━━━\n"
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
        return full_response_text
    else:
        return f"📭 **`{target_email}`**\n\nকোনো নতুন মেসেজ পাওয়া যায়নি।"


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

    elif data.startswith("chk_inbox_"):
        target_email = data.replace("chk_inbox_", "")
        msg_text = await process_single_inbox_check(context, target_email)
        await query.message.reply_text(
            msg_text, 
            parse_mode="Markdown", 
            reply_markup=get_check_inbox_inline_keyboard(target_email)
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
                reply_markup=get_check_inbox_inline_keyboard(email_data['email'])
            )
        else:
            await update.message.reply_text("❌ ইমেইল তৈরি করতে সমস্যা হয়েছে। দয়া করে আবার চেষ্টা করুন।")

    elif text == "📦 ২টি ইমেইল":
        tasks = [fetch_new_email(target_domain=selected_domain) for _ in range(2)]
        results = await asyncio.gather(*tasks)

        for email_data in results:
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
                    reply_markup=get_check_inbox_inline_keyboard(email_data['email'])
                )

    elif text == "📋 আমার ইমেইলসমূহ":
        email_list = context.user_data.get("email_list", [])
        if not email_list:
            await update.message.reply_text("📂 আপনার কোনো সক্রিয় ইমেইল নেই।")
            return

        msg_text = f"📋 **সক্রিয় ইমেইল তালিকা** ({len(email_list)}টি)\n"
        msg_text += "───────────────────\n"
        for idx, item in enumerate(email_list[-10:], 1):
            msg_text += f"{idx}. `{item['email']}`\n"
        msg_text += "───────────────────"

        await update.message.reply_text(msg_text, parse_mode="Markdown", reply_markup=get_reply_keyboard())


async def health_check(request):
    return web.Response(text="Bot is running successfully!")


async def main():
    global http_session
    conn = aiohttp.TCPConnector(limit=100, ttl_dns_cache=300)
    http_session = aiohttp.ClientSession(connector=conn)

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

    logging.info(f"Ultra-Fast Bot active on port {port}...")

    try:
        await asyncio.Event().wait()
    finally:
        await http_session.close()


if __name__ == "__main__":
    asyncio.run(main())
