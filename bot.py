import os
import logging
import re
import random
import string
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

# API Endpoints
API_FISH_URL = "https://api.tempmail.fish/emails"
API_MAILTM_URL = "https://api.mail.tm"
API_MAILGW_URL = "https://api.mail.gw"
API_GUERRILLA_URL = "https://api.guerrillamail.com/ajax.php"

# Global Persistent HTTP Session
http_session: aiohttp.ClientSession = None


def generate_random_string(length=10):
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))


def get_reply_keyboard():
    keyboard = [
        [KeyboardButton("⚙️ সার্ভার সিলেক্ট করুন"), KeyboardButton("🌐 ডোমেইন সিলেক্ট")],
        [KeyboardButton("✉️ ১টি ইমেইল"), KeyboardButton("📦 ২টি ইমেইল")],
        [KeyboardButton("📋 আমার ইমেইলসমূহ")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def get_server_inline_keyboard():
    keyboard = [
        [InlineKeyboardButton("⚡ Server 1 (TempMail Fish)", callback_data="srv_fish")],
        [InlineKeyboardButton("🚀 Server 2 (Mail.tm)", callback_data="srv_mailtm")],
        [InlineKeyboardButton("🔥 Server 3 (Mail.gw)", callback_data="srv_mailgw")],
        [InlineKeyboardButton("🦎 Server 4 (Guerrilla Mail)", callback_data="srv_guerrilla")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_domain_inline_keyboard():
    keyboard = [
        [InlineKeyboardButton("🎲 যেকোনো ডোমেইন (Default)", callback_data="domain_any")],
        [InlineKeyboardButton("🐟 tempmail.fish", callback_data="domain_tempmail.fish")],
        [InlineKeyboardButton("🌊 calmriver.info", callback_data="domain_calmriver.info")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_check_inbox_inline_keyboard(email):
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


# --- Server 1: TempMail.fish ---
async def fetch_fish_email():
    try:
        async with http_session.post(f"{API_FISH_URL}/new-email", timeout=aiohttp.ClientTimeout(total=6)) as response:
            if response.status in [200, 201]:
                data = await response.json()
                return {
                    "email": data.get("email"),
                    "auth_key": data.get("authKey"),
                    "provider": "fish"
                }
    except Exception as e:
        logging.error(f"Fish API Error: {e}")
    return None


# --- Server 2 & 3: Hydra Spec APIs (Mail.tm & Mail.gw) ---
async def fetch_hydra_spec_email(base_url, provider_name):
    try:
        async with http_session.get(f"{base_url}/domains", timeout=aiohttp.ClientTimeout(total=5)) as resp:
            if resp.status == 200:
                domains_data = await resp.json()
                active_domains = [d["domain"] for d in domains_data.get("hydra:member", []) if d.get("isActive")]
                if not active_domains:
                    return None
                selected_domain = random.choice(active_domains)
            else:
                return None

        username = generate_random_string(10)
        password = generate_random_string(12)
        email_address = f"{username}@{selected_domain}"

        payload = {"address": email_address, "password": password}
        headers = {"Content-Type": "application/json"}

        async with http_session.post(f"{base_url}/accounts", json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as acc_resp:
            if acc_resp.status not in [200, 201]:
                return None

        async with http_session.post(f"{base_url}/token", json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as token_resp:
            if token_resp.status in [200, 201]:
                token_data = await token_resp.json()
                token = token_data.get("token")
                return {
                    "email": email_address,
                    "auth_key": f"Bearer {token}",
                    "provider": provider_name
                }
    except Exception as e:
        logging.error(f"{provider_name} API Error: {e}")
    return None


# --- Server 4: Guerrilla Mail ---
async def fetch_guerrilla_email():
    try:
        params = {"f": "get_email_address"}
        async with http_session.get(API_GUERRILLA_URL, params=params, timeout=aiohttp.ClientTimeout(total=6)) as resp:
            if resp.status == 200:
                data = await resp.json()
                return {
                    "email": data.get("email_addr"),
                    "auth_key": data.get("sid_token"),
                    "provider": "guerrilla"
                }
    except Exception as e:
        logging.error(f"Guerrilla API Error: {e}")
    return None


async def fetch_new_email(selected_server="fish", target_domain=None):
    """Router to fetch email based on user's selected server."""
    if selected_server == "mailtm":
        return await fetch_hydra_spec_email(API_MAILTM_URL, "mailtm")
    elif selected_server == "mailgw":
        return await fetch_hydra_spec_email(API_MAILGW_URL, "mailgw")
    elif selected_server == "guerrilla":
        return await fetch_guerrilla_email()
    else:
        # Default Server 1 (fish)
        if target_domain and target_domain != "any":
            for _ in range(3):
                res = await fetch_fish_email()
                if res and res["email"].endswith(f"@{target_domain}"):
                    return res
        return await fetch_fish_email()


# --- Inbox Fetchers ---
async def fetch_inbox_fish(email, auth_key):
    headers = {"Authorization": auth_key}
    params = {"emailAddress": email}
    try:
        async with http_session.get(f"{API_FISH_URL}/emails", headers=headers, params=params, timeout=aiohttp.ClientTimeout(total=6)) as response:
            if response.status == 200:
                emails = await response.json()
                return {"email": email, "emails": emails}
    except Exception as e:
        logging.error(f"Error fetching Fish inbox: {e}")
    return {"email": email, "emails": []}


async def fetch_inbox_hydra(base_url, email, auth_key):
    headers = {"Authorization": auth_key}
    try:
        async with http_session.get(f"{base_url}/messages", headers=headers, timeout=aiohttp.ClientTimeout(total=6)) as response:
            if response.status == 200:
                data = await response.json()
                messages_list = data.get("hydra:member", [])
                
                parsed_messages = []
                for msg in messages_list:
                    msg_id = msg.get("id")
                    async with http_session.get(f"{base_url}/messages/{msg_id}", headers=headers, timeout=aiohttp.ClientTimeout(total=4)) as detail_resp:
                        if detail_resp.status == 200:
                            detail = await detail_resp.json()
                            sender_info = detail.get("from", {})
                            
                            sender_str = "অজানা"
                            if isinstance(sender_info, dict):
                                sender_str = sender_info.get("address") or sender_info.get("name", "অজানা")
                            elif isinstance(sender_info, list) and len(sender_info) > 0:
                                sender_str = sender_info[0]

                            parsed_messages.append({
                                "from": sender_str,
                                "subject": detail.get("subject", "বিষয় ছাড়া"),
                                "textBody": detail.get("text") or detail.get("intro", "")
                            })
                return {"email": email, "emails": parsed_messages}
    except Exception as e:
        logging.error(f"Error fetching Hydra inbox: {e}")
    return {"email": email, "emails": []}


async def fetch_inbox_guerrilla(email, sid_token):
    params = {"f": "get_email_list", "offset": "0", "sid_token": sid_token}
    try:
        async with http_session.get(API_GUERRILLA_URL, params=params, timeout=aiohttp.ClientTimeout(total=6)) as response:
            if response.status == 200:
                data = await response.json()
                messages_list = data.get("list", [])
                
                parsed_messages = []
                for msg in messages_list:
                    # Ignore standard Guerrilla welcome message if needed
                    if msg.get("mail_from") == "no-reply@guerrillamail.com" and "Welcome" in msg.get("mail_subject", ""):
                        continue
                    parsed_messages.append({
                        "from": msg.get("mail_from", "অজানা"),
                        "subject": msg.get("mail_subject", "বিষয় ছাড়া"),
                        "textBody": msg.get("mail_excerpt", "")
                    })
                return {"email": email, "emails": parsed_messages}
    except Exception as e:
        logging.error(f"Error fetching Guerrilla inbox: {e}")
    return {"email": email, "emails": []}


async def process_single_inbox_check(context, target_email):
    """Checks inbox only for the specific targeted email."""
    email_list = context.user_data.get("email_list", [])
    email_item = next((item for item in email_list if item["email"] == target_email), None)

    if not email_item:
        return f"❌ **`{target_email}`** ইমেইলটি বর্তমানে সক্রিয় তালিকায় নেই।"

    provider = email_item.get("provider", "fish")
    if provider == "mailtm":
        res = await fetch_inbox_hydra(API_MAILTM_URL, email_item["email"], email_item["auth_key"])
    elif provider == "mailgw":
        res = await fetch_inbox_hydra(API_MAILGW_URL, email_item["email"], email_item["auth_key"])
    elif provider == "guerrilla":
        res = await fetch_inbox_guerrilla(email_item["email"], email_item["auth_key"])
    else:
        res = await fetch_inbox_fish(email_item["email"], email_item["auth_key"])

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

    if data.startswith("srv_"):
        server_key = data.replace("srv_", "")
        context.user_data["selected_server"] = server_key
        server_names = {
            "fish": "Server 1 (TempMail Fish)",
            "mailtm": "Server 2 (Mail.tm)",
            "mailgw": "Server 3 (Mail.gw)",
            "guerrilla": "Server 4 (Guerrilla Mail)"
        }
        name = server_names.get(server_key, "Server 1")
        await query.edit_message_text(
            f"✅ **সার্ভার সিলেক্ট করা হয়েছে:** `{name}`\n\n"
            "এখন ইমেইল তৈরির বাটন চাপলে এই সার্ভার ব্যবহার হবে।",
            parse_mode="Markdown"
        )

    elif data.startswith("domain_"):
        selected_domain = data.replace("domain_", "")
        context.user_data["selected_domain"] = selected_domain
        domain_display = "Any Domain (Default)" if selected_domain == "any" else f"@{selected_domain}"

        await query.edit_message_text(
            f"✅ **পছন্দকৃত ডোমেইন সেট করা হয়েছে:** `{domain_display}`\n\n"
            "*(শুধুমাত্র Server 1 এর ক্ষেত্রে প্রযোজ্য)*",
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

    selected_server = context.user_data.get("selected_server", "fish")
    selected_domain = context.user_data.get("selected_domain", "any")

    if text == "⚙️ সার্ভার সিলেক্ট করুন":
        msg = f"⚙️ **আপনার পছন্দের সার্ভার নির্বাচন করুন:**\nবর্তমান সক্রিয় সার্ভার: `{selected_server.upper()}`"
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_server_inline_keyboard())

    elif text == "🌐 ডোমেইন সিলেক্ট":
        current_dom = "Any Domain" if selected_domain == "any" else f"@{selected_domain}"
        msg = f"🌐 **আপনার পছন্দমত ডোমেইন বেছে নিন:**\nবর্তমান পছন্দ: `{current_dom}`"
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_domain_inline_keyboard())

    elif text == "✉️ ১টি ইমেইল":
        email_data = await fetch_new_email(selected_server=selected_server, target_domain=selected_domain)

        if email_data:
            context.user_data["email_list"].append(email_data)
            msg_text = (
                f"🎯 **নতুন ইমেইল প্রস্তুত ({selected_server.upper()})**\n"
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
        tasks = [fetch_new_email(selected_server=selected_server, target_domain=selected_domain) for _ in range(2)]
        results = await asyncio.gather(*tasks)

        for email_data in results:
            if email_data:
                context.user_data["email_list"].append(email_data)
                msg_text = (
                    f"🎯 **নতুন ইমেইল প্রস্তুত ({selected_server.upper()})**\n"
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
            msg_text += f"{idx}. `{item['email']}` ({item.get('provider', 'fish')})\n"
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

    logging.info(f"Multi-Server Bot active on port {port}...")

    try:
        await asyncio.Event().wait()
    finally:
        await http_session.close()


if __name__ == "__main__":
    asyncio.run(main())
