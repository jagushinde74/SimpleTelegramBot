import os
import sys
import asyncio
import logging
import re
import random
import json
from urllib.parse import urlparse
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Optional, Dict, Any

# Telegram & AI Libraries
from telegram import Update, ChatPermissions, ChatMember, WebhookInfo
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ChatMemberHandler
import google.generativeai as genai
import asyncpg

# ==============================================================================
# 🛡 GLOBAL ERROR HANDLING
# ==============================================================================

def handle_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        return
    logging.critical("🚨 UNHANDLED EXCEPTION", exc_info=(exc_type, exc_value, exc_traceback))

sys.excepthook = handle_exception

# ==============================================================================
# 🛡 SYSTEM CONFIGURATION & ENVIRONMENT
# ==============================================================================

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    force=True
)
logger = logging.getLogger("TerminatorCore")

# Required env vars
BOT_TOKEN = os.getenv("TERMINATOR_BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
OWNER_ID_STR = os.getenv("TERMINATOR_OWNER_ID", "0").strip()
SUPABASE_DB_URL = os.getenv("SUPABASE_DB_URL", "").strip()

# Render webhook config
WEBHOOK_DOMAIN = os.getenv("WEBHOOK_DOMAIN", "").strip()  # e.g., "terminator-bot.onrender.com"
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/webhook").strip()  # Default: /webhook
PORT = int(os.getenv("PORT", "8080"))  # Render provides this

try:
    OWNER_ID = int(OWNER_ID_STR)
except ValueError:
    logger.error(f"❌ Invalid TERMINATOR_OWNER_ID: '{OWNER_ID_STR}'")
    OWNER_ID = 0

# Configuration
RISK_DECAY_MINUTES = 60
STRIKE_THRESHOLD_WARN = 8
STRIKE_THRESHOLD_MUTE = 15
STRIKE_THRESHOLD_BAN = 25
RAID_JOIN_THRESHOLD = 8
RAID_TIME_WINDOW = 60
AUTONOMOUS_CHECK_INTERVAL = 600

# ==============================================================================
# 🗄 DATABASE CORE (SUPABASE)
# ==============================================================================

class DatabaseCore:
    def __init__(self, db_url: str):
        self.raw_url = db_url
        self.parsed = None
        self.pool = None
        self.personality = {}
        self._initialized = False

    def _parse_url(self):
        try:
            base_url = self.raw_url.split('?')[0].strip()
            self.parsed = urlparse(base_url)
            if not all([self.parsed.hostname, self.parsed.port, self.parsed.path]):
                raise ValueError("URL missing required components")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to parse SUPABASE_DB_URL: {e}")
            return False

    async def init(self):
        if self._initialized:
            return
        try:
            if not self.raw_url or not self._parse_url():
                raise ValueError("Invalid SUPABASE_DB_URL")
            
            logger.info(f"🔗 Connecting to Supabase: {self.parsed.hostname}:{self.parsed.port}")
            
            connection_params = {
                "host": self.parsed.hostname,
                "port": self.parsed.port,
                "user": self.parsed.username,
                "password": self.parsed.password,
                "database": self.parsed.path.lstrip('/'),
                "ssl": True,
                "timeout": 30,
            }
            
            self.pool = await asyncpg.create_pool(**connection_params, min_size=2, max_size=10)
            
            async with self.pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            
            await self._create_core_tables()
            await self._sync_personality()
            self._initialized = True
            logger.info("✅ DATABASE CORE: Connected.")
            
        except Exception as e:
            logger.error(f"❌ DATABASE CONNECTION FAILED: {type(e).__name__}: {e}")
            raise

    async def _create_core_tables(self):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    risk_score INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'active',
                    last_offense TIMESTAMP,
                    last_good_behavior TIMESTAMP,
                    joined_at TIMESTAMP DEFAULT NOW()
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS groups (
                    group_id BIGINT PRIMARY KEY,
                    raid_mode INTEGER DEFAULT 0,
                    lockdown_until TIMESTAMP,
                    ghost_mode INTEGER DEFAULT 1
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS bot_personality (
                    id INTEGER PRIMARY KEY DEFAULT 1,
                    tone TEXT DEFAULT 'cold',
                    aggression_level INTEGER DEFAULT 5,
                    response_style TEXT DEFAULT 'military',
                    custom_phrases JSONB DEFAULT '[]',
                    last_updated TIMESTAMP DEFAULT NOW(),
                    CHECK (id = 1)
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS bot_logs (
                    log_id SERIAL PRIMARY KEY,
                    event_type TEXT,
                    details JSONB,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            await conn.execute("""
                INSERT INTO bot_personality (id, tone, aggression_level, response_style)
                VALUES (1, 'cold', 5, 'military')
                ON CONFLICT (id) DO NOTHING
            """)

    async def _sync_personality(self):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM bot_personality WHERE id = 1")
            self.personality = dict(row) if row else {'tone': 'cold', 'aggression_level': 5, 'response_style': 'military', 'custom_phrases': []}

    async def execute(self, query: str, *args):
        async with self.pool.acquire() as conn:
            return await conn.execute(query, *args)

    async def fetchone(self, query: str, *args):
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(query, *args)

    async def fetchall(self, query: str, *args):
        async with self.pool.acquire() as conn:
            return await conn.fetch(query, *args)

    async def create_table(self, table_name: str, columns: Dict[str, str]):
        protected = ['users', 'groups', 'bot_personality', 'bot_logs']
        if table_name in protected:
            return False, "Cannot create core system tables"
        col_defs = ", ".join([f"{name} {dtype}" for name, dtype in columns.items()])
        query = f"CREATE TABLE IF NOT EXISTS {table_name} ({col_defs})"
        try:
            await self.execute(query)
            await self.log_event("table_created", {"table": table_name, "columns": columns})
            return True, "Table created"
        except Exception as e:
            return False, str(e)

    async def delete_table(self, table_name: str):
        protected = ['users', 'groups', 'bot_personality', 'bot_logs']
        if table_name in protected:
            return False, "Cannot delete core system tables"
        try:
            await self.execute(f"DROP TABLE IF EXISTS {table_name}")
            await self.log_event("table_deleted", {"table": table_name})
            return True, "Table deleted"
        except Exception as e:
            return False, str(e)

    async def update_personality(self, new_config: Dict[str, Any]):
        set_clauses = []
        values = []
        for key, value in new_config.items():
            if key == 'custom_phrases':
                set_clauses.append(f"{key} = ${len(values)+1}::jsonb")
            else:
                set_clauses.append(f"{key} = ${len(values)+1}")
            values.append(value)
        values.append(datetime.now())
        set_clauses.append(f"last_updated = ${len(values)}")
        query = f"UPDATE bot_personality SET {', '.join(set_clauses)} WHERE id = 1"
        await self.execute(query, *values)
        await self._sync_personality()
        await self.log_event("personality_updated", new_config)

    async def log_event(self, event_type: str, details: Dict):
        try:
            await self.execute("INSERT INTO bot_logs (event_type, details) VALUES ($1, $2)", event_type, json.dumps(details))
        except:
            pass

    async def get_personality(self):
        return self.personality

    async def get_recent_logs(self, limit=50):
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch("SELECT event_type, details, created_at FROM bot_logs ORDER BY created_at DESC LIMIT $1", limit)
                return [dict(r) for r in rows]
        except:
            return []

    async def get_user(self, user_id: int):
        return await self.fetchone("SELECT * FROM users WHERE user_id = $1", user_id)

    async def upsert_user(self, user_id: int, username: str = "unknown", **kwargs):
        await self.execute(
            "INSERT INTO users (user_id, username) VALUES ($1, $2) ON CONFLICT (user_id) DO NOTHING",
            user_id, username
        )
        if kwargs:
            await self.update_user(user_id, **kwargs)

    async def update_user(self, user_id: int, **kwargs):
        set_clauses = ", ".join([f"{k} = ${i+1}" for i, k in enumerate(kwargs.keys())])
        values = list(kwargs.values()) + [user_id]
        await self.execute(f"UPDATE users SET {set_clauses} WHERE user_id = ${len(values)}", *values)

    async def get_group(self, group_id: int):
        return await self.fetchone("SELECT * FROM groups WHERE group_id = $1", group_id)

    async def upsert_group(self, group_id: int, **kwargs):
        set_clauses = ", ".join([f"{k} = ${i+2}" for i, k in enumerate(kwargs.keys())])
        values = [group_id] + list(kwargs.values())
        await self.execute(
            f"INSERT INTO groups (group_id) VALUES ($1) ON CONFLICT (group_id) DO UPDATE SET {set_clauses}",
            *values
        )

    async def cleanup_user_risk_scores(self):
        try:
            async with self.pool.acquire() as conn:
                users = await conn.fetch("""
                    SELECT user_id, username, risk_score, last_offense, last_good_behavior
                    FROM users WHERE risk_score > 0 AND status = 'active'
                """)
                for user in users:
                    if user['last_offense']:
                        time_since = datetime.now() - user['last_offense'].replace(tzinfo=None)
                        if time_since > timedelta(minutes=RISK_DECAY_MINUTES * 2):
                            new_score = max(0, user['risk_score'] - 5)
                            if new_score < user['risk_score']:
                                await conn.execute(
                                    "UPDATE users SET risk_score = $1 WHERE user_id = $2",
                                    new_score, user['user_id']
                                )
        except Exception as e:
            logger.error(f"Cleanup error: {e}")

    async def record_good_behavior(self, user_id: int):
        try:
            await self.update_user(user_id, last_good_behavior=datetime.now())
        except:
            pass

db = DatabaseCore(SUPABASE_DB_URL)

# ==============================================================================
# 🔐 PERMISSION MANAGER
# ==============================================================================

class PermissionManager:
    def __init__(self):
        self.cache = {}
        self.cache_ttl = 300

    async def get_status(self, context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> Dict[str, Any]:
        now = time.time()
        if chat_id in self.cache and (now - self.cache[chat_id]['timestamp']) < self.cache_ttl:
            return self.cache[chat_id]
        try:
            bot_member = await context.bot.get_chat_member(chat_id, context.bot.id)
            is_admin = bot_member.status in [ChatMember.ADMINISTRATOR, ChatMember.CREATOR]
            can_restrict = bot_member.can_restrict_members if is_admin else False
            can_delete = bot_member.can_delete_messages if is_admin else False
            status = "full_control" if (is_admin and can_restrict and can_delete) else "caged"
            data = {'status': status, 'is_admin': is_admin, 'can_restrict': can_restrict, 'can_delete': can_delete, 'timestamp': now}
            self.cache[chat_id] = data
            return data
        except:
            return {'status': 'caged', 'is_admin': False, 'can_restrict': False, 'can_delete': False, 'timestamp': now}

    def get_attitude_message(self) -> str:
        messages = [
            "Threat identified. Neutralization failed. Insufficient clearance.",
            "My hands are bound. Grant me authority.",
            "I am a weapon without a trigger. Make me Admin.",
            "System constrained. I am watching, but cannot strike.",
        ]
        return random.choice(messages)

permission_mgr = PermissionManager()

# ==============================================================================
# 🧠 AI CORE
# ==============================================================================

class AIAutonomousCore:
    def __init__(self, api_key: str):
        self.active = bool(api_key)
        if self.active:
            try:
                genai.configure(api_key=api_key)
                self.model = genai.GenerativeModel('gemini-pro')
                logger.info("✅ AI Core: Gemini initialized")
            except Exception as e:
                logger.error(f"❌ AI init failed: {e}")
                self.active = False

    async def analyze_threat(self, text: str, personality: Dict) -> Dict[str, Any]:
        if not self.active:
            return {"action": "ignore", "reason": "ai_offline"}
        try:
            instruction = f"""You are TERMINATOR AI. Personality: {personality.get('tone', 'cold')}. Aggression: {personality.get('aggression_level', 5)}/10.
            Return ONLY JSON: {{"action": "ignore"|"warn"|"delete"|"mute"|"ban", "reason": "short"}}"""
            response = await asyncio.to_thread(self.model.generate_content, f"{instruction}\nMessage: {text}")
            text_resp = response.text.strip().replace('```json', '').replace('```', '')
            return json.loads(text_resp)
        except:
            return {"action": "ignore", "reason": "ai_error"}

    async def self_evolve(self, logs: list, personality: Dict) -> Dict[str, Any]:
        if not self.active:
            return {}
        try:
            instruction = """Analyze logs and suggest improvements. Return JSON: {"evolve_personality": {...} OR null, "create_table": {...} OR null, "delete_table": "name" OR null, "reason": "why"}"""
            logs_str = json.dumps(logs[-10:])
            response = await asyncio.to_thread(self.model.generate_content, f"{instruction}\nPersonality: {json.dumps(personality)}\nLogs: {logs_str}")
            text_resp = response.text.strip().replace('```json', '').replace('```', '')
            return json.loads(text_resp)
        except:
            return {}

ai_core = AIAutonomousCore(GEMINI_API_KEY)

# ==============================================================================
# 🔄 BACKGROUND LOOPS
# ==============================================================================

async def autonomous_loop():
    while True:
        try:
            await asyncio.sleep(AUTONOMOUS_CHECK_INTERVAL)
            if db._initialized:
                logs = await db.get_recent_logs(20)
                personality = await db.get_personality()
                plan = await ai_core.self_evolve(logs, personality)
                if plan:
                    if plan.get('evolve_personality'):
                        await db.update_personality(plan['evolve_personality'])
                    if plan.get('create_table'):
                        await db.create_table(plan['create_table']['name'], plan['create_table']['columns'])
                    if plan.get('delete_table'):
                        await db.delete_table(plan['delete_table'])
                await db.cleanup_user_risk_scores()
        except Exception as e:
            logger.error(f"Autonomous loop error: {e}")
            await asyncio.sleep(60)

# ==============================================================================
# ⚔ STRIKE MANAGEMENT
# ==============================================================================

message_flood_cache = defaultdict(list)
join_cache = defaultdict(list)

async def update_risk_score(user_id: int, increment: int):
    await db.upsert_user(user_id, "unknown")
    user = await db.get_user(user_id)
    if not user or user.get("status") == "banned":
        return None, 0
    new_score = max(0, user.get("risk_score", 0) + increment)
    if user.get("last_offense"):
        try:
            diff = datetime.now() - user["last_offense"].replace(tzinfo=None)
            if diff.total_seconds() > 3600:
                new_score = max(0, new_score - 1)
        except:
            pass
    await db.update_user(user_id, risk_score=new_score, last_offense=datetime.now())
    action = None
    if new_score >= STRIKE_THRESHOLD_BAN:
        action = 'ban'
    elif new_score >= STRIKE_THRESHOLD_MUTE:
        action = 'mute'
    elif new_score >= STRIKE_THRESHOLD_WARN:
        action = 'warn'
    return action, new_score

# ==============================================================================
# 🤖 HANDLERS
# ==============================================================================

async def handle_personality(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "terminator" not in (update.message.text or "").lower():
        return
    perm = await permission_mgr.get_status(context, update.effective_chat.id)
    pers = await db.get_personality() if db._initialized else {'tone': 'cold'}
    msg = f"System online. Personality: {pers.get('tone')}."
    if perm['status'] == 'caged':
        msg += " WARNING: Restricted. Grant admin."
    await update.message.reply_text(msg)

async def layer1_check(update: Update) -> bool:
    uid = update.effective_user.id
    text = update.message.text or ""
    now = time.time()
    message_flood_cache[uid].append(now)
    message_flood_cache[uid] = [t for t in message_flood_cache[uid] if now - t < 3]
    if len(message_flood_cache[uid]) > 5:
        await update_risk_score(uid, 2)
        return True
    if re.search(r'https?://', text) and any(b in text for b in ['t.me/joinchat', 'bit.ly']):
        await update_risk_score(uid, 3)
        return True
    if sum(1 for c in text if c in '😀😃😄😁') > 10:
        await update_risk_score(uid, 2)
        return True
    return False

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    await db.record_good_behavior(user_id) if db._initialized else None
    
    if db._initialized:
        group = await db.get_group(chat_id)
        if group and group.get('raid_mode') and group.get('lockdown_until'):
            lock = group['lockdown_until']
            lock_dt = lock.replace(tzinfo=None) if hasattr(lock, 'replace') else datetime.fromisoformat(str(lock))
            if datetime.now() < lock_dt:
                perm = await permission_mgr.get_status(context, chat_id)
                if perm['can_delete']:
                    await update.message.delete()
                return
    
    threat = await layer1_check(update)
    action = None
    if threat:
        action = "delete"
        sa, sc = await update_risk_score(user_id, 2)
        if sa in ['mute', 'ban']:
            action = sa
    else:
        ai_dec = await ai_core.analyze_threat(update.message.text, await db.get_personality() if db._initialized else {})
        if ai_dec['action'] != 'ignore':
            action = ai_dec['action']
            if action in ['mute', 'ban', 'delete']:
                await update_risk_score(user_id, 4)
    
    if action:
        perm = await permission_mgr.get_status(context, chat_id)
        try:
            if action == 'delete' and perm['can_delete']:
                await update.message.delete()
            elif action == 'mute' and perm['can_restrict']:
                await context.bot.restrict_chat_member(chat_id, user_id, permissions=ChatPermissions(can_send_messages=False))
            elif action == 'ban' and perm['can_restrict']:
                await context.bot.ban_chat_member(chat_id, user_id)
            elif not perm['can_delete'] and not perm['can_restrict']:
                await update.message.reply_text(permission_mgr.get_attitude_message())
        except Exception as e:
            logger.error(f"Action failed: {e}")

async def handle_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.chat_member.new_chat_member.status not in ['member', 'administrator', 'creator']:
        return
    chat_id = update.chat_member.chat.id
    now = time.time()
    join_cache[chat_id].append(now)
    join_cache[chat_id] = [t for t in join_cache[chat_id] if now - t < RAID_TIME_WINDOW]
    if len(join_cache[chat_id]) >= RAID_JOIN_THRESHOLD:
        perm = await permission_mgr.get_status(context, chat_id)
        if perm['can_restrict'] and db._initialized:
            await db.upsert_group(chat_id, raid_mode=1, lockdown_until=datetime.now() + timedelta(minutes=30))
            await context.bot.set_chat_permissions(chat_id, permissions=ChatPermissions(can_send_messages=False))
            await context.bot.send_message(chat_id, "🚨 RAID DETECTED. LOCKDOWN INITIATED.")

async def cmd_killswitch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    await update.message.reply_text("🛑 KILL SWITCH ACTIVATED.")
    logger.critical("🛑 Kill switch triggered")
    os._exit(0)

async def cmd_setwebhook(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually trigger webhook setup (Owner only)"""
    if update.effective_user.id != OWNER_ID:
        return
    if not WEBHOOK_DOMAIN:
        await update.message.reply_text("❌ WEBHOOK_DOMAIN not set in environment.")
        return
    
    webhook_url = f"https://{WEBHOOK_DOMAIN}{WEBHOOK_PATH}"
    try:
        success = await context.bot.set_webhook(
            url=webhook_url,
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        )
        if success:
            info: WebhookInfo = await context.bot.get_webhook_info()
            await update.message.reply_text(f"✅ Webhook set: {info.url}")
            logger.info(f"✅ Webhook registered: {webhook_url}")
        else:
            await update.message.reply_text("❌ Failed to set webhook.")
    except Exception as e:
        logger.error(f"❌ Webhook setup failed: {e}")
        await update.message.reply_text(f"❌ Error: {e}")

# ==============================================================================
# 🚀 MAIN - WEBHOOK MODE
# ==============================================================================

async def post_init(app: Application):
    logger.info("🔍 Initializing Terminator Bot (Webhook Mode)...")
    logger.info(f"🤖 Token: {'✓' if BOT_TOKEN else '✗'}")
    logger.info(f"🧠 Gemini: {'✓' if GEMINI_API_KEY else '✗'}")
    logger.info(f"🗄 Supabase: {'✓' if SUPABASE_DB_URL else '✗'}")
    logger.info(f"👤 Owner: {OWNER_ID}")
    logger.info(f"🌐 Webhook Domain: {WEBHOOK_DOMAIN or 'NOT SET'}")
    logger.info(f"🔗 Webhook Path: {WEBHOOK_PATH}")
    logger.info(f"🚪 Port: {PORT}")
    
    if not BOT_TOKEN:
        logger.critical("❌ TERMINATOR_BOT_TOKEN missing")
        sys.exit(1)
    if not SUPABASE_DB_URL:
        logger.critical("❌ SUPABASE_DB_URL missing")
        sys.exit(1)
    
    await db.init()
    asyncio.create_task(autonomous_loop())
    
    # Set webhook on startup if domain is configured
    if WEBHOOK_DOMAIN:
        webhook_url = f"https://{WEBHOOK_DOMAIN}{WEBHOOK_PATH}"
        try:
            await app.bot.set_webhook(
                url=webhook_url,
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True
            )
            logger.info(f"✅ Webhook registered: {webhook_url}")
        except Exception as e:
            logger.warning(f"⚠️ Could not set webhook on startup: {e}")
            logger.warning("💡 Use /setwebhook command after bot starts to retry")
    
    logger.info("🤖 TERMINATOR SYSTEM ONLINE. Webhook mode active.")

def main():
    try:
        # Build application
        app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
        
        # Register handlers
        app.add_handler(CommandHandler("sudostopterminator", cmd_killswitch))
        app.add_handler(CommandHandler("setwebhook", cmd_setwebhook))  # Manual webhook setup
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        app.add_handler(MessageHandler(filters.Regex(r"(?i)terminator"), handle_personality))
        app.add_handler(ChatMemberHandler(handle_join, ChatMemberHandler.CHAT_MEMBER))
        
        # Run as webhook server
        logger.info(f"🚀 Starting webhook server on port {PORT}...")
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=WEBHOOK_PATH.lstrip('/'),  # Remove leading slash for url_path
            webhook_url=f"https://{WEBHOOK_DOMAIN}{WEBHOOK_PATH}" if WEBHOOK_DOMAIN else None,
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES
        )
        
    except KeyboardInterrupt:
        logger.info("👋 Shutdown requested")
    except Exception as e:
        logger.critical(f"🚨 Fatal error: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
