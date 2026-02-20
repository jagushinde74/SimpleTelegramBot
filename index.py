# ============================================================================
# 🤖 TERMINATOR - RENDER WEBHOOK FIXED VERSION
# Webhook Mode | No WEBHOOK_PATH env | Root path only
# ============================================================================

import os
import sys
import asyncio
import logging
import json
from datetime import datetime
from typing import Dict, Any, List

from langdetect import detect

from telegram import (
    Update, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters, ChatMemberHandler
)

import google.generativeai as genai
from supabase import create_client

# ============================================================================
# 🔐 ENV CONFIG
# ============================================================================

BOT_TOKEN = os.getenv("TERMINATOR_BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
OWNER_ID = int(os.getenv("TERMINATOR_OWNER_ID", "0"))
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()
RENDER_EXTERNAL_HOSTNAME = os.getenv("RENDER_EXTERNAL_HOSTNAME")
PORT = int(os.getenv("PORT", "10000"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TERMINATOR")

# ============================================================================
# 🗄 SUPABASE INIT
# ============================================================================

class Database:
    def __init__(self, url: str, key: str):
        self.client = None
        self.url = url
        self.key = key

    async def init(self):
        if not self.url or not self.key:
            logger.warning("⚠️ Supabase credentials missing. Limited mode.")
            return False
        try:
            self.client = create_client(self.url, self.key)
            logger.info("✅ Supabase Connected")
            return True
        except Exception as e:
            logger.error(f"❌ Supabase connection failed: {e}")
            return False

    async def log_event(self, event_type: str, details: Dict):
        if not self.client:
            return
        def _run():
            return self.client.table("bot_logs").insert({
                "event_type": event_type,
                "details": details
            }).execute()
        await asyncio.to_thread(_run)

    async def get_personality(self):
        if not self.client:
            return {"tone": "cold", "aggression_level": 7, "response_style": "military"}
        def _run():
            return self.client.table("bot_personality").select("*").eq("id",1).limit(1).execute()
        result = await asyncio.to_thread(_run)
        if result.data:
            return result.data[0]
        return {"tone": "cold", "aggression_level": 7, "response_style": "military"}


db = Database(SUPABASE_URL, SUPABASE_KEY)

# ============================================================================
# 🧠 AI CORE
# ============================================================================

class TerminatorAI:
    def __init__(self, key: str):
        self.active = False
        if key:
            try:
                genai.configure(api_key=key)
                self.model = genai.GenerativeModel("gemini-pro")
                self.active = True
                logger.info("✅ Gemini AI Ready")
            except Exception as e:
                logger.error(f"AI init failed: {e}")

    async def analyze(self, text: str, role: str, personality: Dict) -> Dict[str, Any]:
        if not self.active:
            return {"action": "ignore"}

        prompt = f"""
You are TERMINATOR AI moderator.
Tone: {personality.get('tone')}
Aggression: {personality.get('aggression_level')}/10
User role: {role}

Respond in same language.
Return JSON:
{{"action":"reply|ignore","response":"text"}}

Message: {text}
"""
        try:
            response = await asyncio.to_thread(self.model.generate_content, prompt)
            cleaned = response.text.strip().replace("```json","").replace("```","")
            return json.loads(cleaned)
        except:
            return {"action": "ignore"}

ai_core = TerminatorAI(GEMINI_API_KEY)

# ============================================================================
# 🤖 HANDLERS
# ============================================================================

async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return

    keyboard = InlineKeyboardMarkup([[ 
        InlineKeyboardButton(
            "➕ ADD TO GROUP",
            url=f"https://t.me/{context.bot.username}?startgroup=true"
        )
    ]])

    await update.message.reply_text(
        "Add me in your group with full admin rights then see MAGIC.",
        reply_markup=keyboard
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    if update.effective_chat.type == "private":
        return

    user_id = update.effective_user.id
    role = "bot_owner" if user_id == OWNER_ID else "member"

    personality = await db.get_personality()
    ai_result = await ai_core.analyze(update.message.text, role, personality)

    if ai_result.get("action") == "reply":
        await update.message.reply_text(ai_result.get("response"))

async def post_init(app: Application):
    if not BOT_TOKEN:
        logger.critical("❌ BOT TOKEN MISSING")
        sys.exit(1)

    await db.init()
    logger.info("🤖 TERMINATOR ONLINE (Webhook Mode)")

# ============================================================================
# 🚀 MAIN (RENDER WEBHOOK ROOT MODE)
# ============================================================================

def main():
    if not BOT_TOKEN:
        sys.exit(1)

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    if not RENDER_EXTERNAL_HOSTNAME:
        logger.critical("❌ RENDER_EXTERNAL_HOSTNAME not found")
        sys.exit(1)

    webhook_url = f"https://{RENDER_EXTERNAL_HOSTNAME}/"

    logger.info(f"🚀 Starting webhook on port {PORT}")

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        webhook_url=webhook_url,
        url_path="",
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES
    )

if __name__ == "__main__":
    main()
