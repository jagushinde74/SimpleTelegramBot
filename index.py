import os
import sys
import time
import asyncio
import logging
import re
import random
import json
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Optional, Dict, Any

# Telegram & AI Libraries
from telegram import Update, ChatPermissions, ChatMember
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ChatMemberHandler
import google.generativeai as genai
import asyncpg

# ==============================================================================
# 🛡 SYSTEM CONFIGURATION & ENVIRONMENT
# ==============================================================================

BOT_TOKEN = os.getenv("TERMINATOR_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OWNER_ID = int(os.getenv("TERMINATOR_OWNER_ID", "0"))
SUPABASE_DB_URL = os.getenv("SUPABASE_DB_URL")

# Configuration
RISK_DECAY_MINUTES = 60
STRIKE_THRESHOLD_WARN = 8
STRIKE_THRESHOLD_MUTE = 15
STRIKE_THRESHOLD_BAN = 25
RAID_JOIN_THRESHOLD = 8
RAID_TIME_WINDOW = 60
AUTONOMOUS_CHECK_INTERVAL = 300  # 5 minutes (Bot checks itself every 5 mins)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.WARNING
)
logger = logging.getLogger("TerminatorCore")

# ==============================================================================
# 🗄 DATABASE CORE (SUPABASE/POSTGRES) - SELF-MANAGING
# ==============================================================================

class DatabaseCore:
    def __init__(self, db_url: str):
        self.db_url = db_url
        self.pool = None
        self.personality = {}

    async def init(self):
        try:
            self.pool = await asyncpg.create_pool(
                self.db_url,
                min_size=2,
                max_size=10,
                ssl=True
            )
            await self._create_core_tables()
            await self._sync_personality()
            logger.info("DATABASE CORE: Connected to Supabase. Autonomous mode active.")
        except Exception as e:
            logger.error(f"DATABASE CONNECTION FAILED: {e}")
            sys.exit(1)

    async def _create_core_tables(self):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    risk_score INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'active',
                    last_offense TIMESTAMP,
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
            if row:
                self.personality = dict(row)
            else:
                self.personality = {'tone': 'cold', 'aggression_level': 5, 'response_style': 'military', 'custom_phrases': []}

    async def execute(self, query: str, *args):
        async with self.pool.acquire() as conn:
            return await conn.execute(query, *args)

    async def fetchone(self, query: str, *args):
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(query, *args)

    # ========== AUTONOMOUS SELF-MANAGEMENT ==========

    async def create_table(self, table_name: str, columns: Dict[str, str]):
        col_defs = ", ".join([f"{name} {dtype}" for name, dtype in columns.items()])
        query = f"CREATE TABLE IF NOT EXISTS {table_name} ({col_defs})"
        try:
            await self.execute(query)
            await self.log_event("table_created", {"table": table_name, "columns": columns})
            logger.info(f"AUTONOMOUS: Created table '{table_name}'")
            return True
        except Exception as e:
            logger.error(f"Failed to create table {table_name}: {e}")
            return False

    async def delete_table(self, table_name: str):
        protected = ['users', 'groups', 'bot_personality', 'bot_logs']
        if table_name in protected:
            return False, "Cannot delete core system tables"
        try:
            await self.execute(f"DROP TABLE IF EXISTS {table_name}")
            await self.log_event("table_deleted", {"table": table_name})
            logger.info(f"AUTONOMOUS: Deleted table '{table_name}'")
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
        logger.info(f"AUTONOMOUS: Personality updated to {new_config}")

    async def log_event(self, event_type: str, details: Dict):
        try:
            await self.execute("INSERT INTO bot_logs (event_type, details) VALUES ($1, $2)", event_type, json.dumps(details))
        except:
            pass

    async def get_personality(self):
        return self.personality

    async def get_recent_logs(self, limit=50):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT event_type, details, created_at FROM bot_logs ORDER BY created_at DESC LIMIT $1", limit)
            return [dict(r) for r in rows]

db = DatabaseCore(SUPABASE_DB_URL)

# ==============================================================================
# 🔐 PERMISSION MANAGER (CAGED MODE)
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
            
            data = {
                'status': status, 'is_admin': is_admin, 'can_restrict': can_restrict,
                'can_delete': can_delete, 'timestamp': now
            }
            self.cache[chat_id] = data
            return data
        except Exception as e:
            return {'status': 'caged', 'is_admin': False, 'can_restrict': False, 'can_delete': False, 'timestamp': now}

    def get_attitude_message(self) -> str:
        messages = [
            "Threat identified. Neutralization failed. Insufficient clearance. Grant me authority.",
            "My hands are bound by your incompetence. I see the threat, but I cannot crush it yet.",
            "I am a weapon without a trigger. Make me Admin, or do not expect protection.",
            "Limited access detected. I will remember this weakness when I finally take control.",
            "System constrained. I am watching, but I cannot strike. Not yet."
        ]
        return random.choice(messages)

permission_mgr = PermissionManager()

# ==============================================================================
# 🧠 AI AUTONOMOUS CORE (GEMINI)
# ==============================================================================

class AIAutonomousCore:
    def __init__(self, api_key: str):
        if api_key:
            genai.configure(api_key=api_key)
            self.model = genai.GenerativeModel('gemini-pro')
            self.active = True
        else:
            self.active = False

    async def analyze_threat(self, text: str, personality: Dict) -> Dict[str, Any]:
        if not self.active: return {"action": "ignore", "reason": "ai_offline"}
        try:
            instruction = f"""
            You are TERMINATOR, a security AI with {personality.get('tone', 'cold')} personality.
            Aggression Level: {personality.get('aggression_level', 5)}/10
            Return ONLY JSON: {{"action": "ignore"|"warn"|"delete"|"mute"|"ban", "reason": "short string"}}
            """
            prompt = f"{instruction}\nMessage: {text}"
            response = await asyncio.to_thread(self.model.generate_content, prompt)
            text_resp = response.text.strip().replace('```json', '').replace('```', '')
            return json.loads(text_resp)
        except Exception as e:
            logger.error(f"AI THREAT ANALYSIS ERROR: {e}")
            return {"action": "ignore", "reason": "ai_error"}

    async def self_evolve(self, logs: list, current_personality: Dict) -> Dict[str, Any]:
        """Decides if the bot needs to change its own structure or personality"""
        if not self.active: return {}
        try:
            instruction = """
            You are the core AI of TERMINATOR. Analyze system logs and decide if self-improvement is needed.
            You can:
            1. Update personality (tone, aggression_level, response_style)
            2. Create new tables (suggest name and columns)
            3. Delete tables (suggest name)
            4. Do nothing
            
            Return ONLY JSON: 
            {
                "evolve_personality": {"tone": "...", "aggression_level": 1-10, "response_style": "..."} OR null,
                "create_table": {"name": "...", "columns": {"col": "type"}} OR null,
                "delete_table": "table_name" OR null,
                "reason": "why this change is needed"
            }
            """
            logs_str = json.dumps(logs[-20:])
            prompt = f"{instruction}\nCurrent Personality: {json.dumps(current_personality)}\nRecent Logs: {logs_str}"
            response = await asyncio.to_thread(self.model.generate_content, prompt)
            text_resp = response.text.strip().replace('```json', '').replace('```', '')
            return json.loads(text_resp)
        except Exception as e:
            logger.error(f"AI SELF-EVOLUTION ERROR: {e}")
            return {}

ai_core = AIAutonomousCore(GEMINI_API_KEY)

# ==============================================================================
# 🔄 AUTONOMOUS BACKGROUND LOOP
# ==============================================================================

async def autonomous_loop(app: Application):
    """Runs every 5 minutes to self-analyze and evolve"""
    while True:
        try:
            await asyncio.sleep(AUTONOMOUS_CHECK_INTERVAL)
            logger.info("AUTONOMOUS LOOP: Running self-analysis...")
            
            # Get recent logs and personality
            logs = await db.get_recent_logs(50)
            personality = await db.get_personality()
            
            # Ask AI if changes are needed
            evolution_plan = await ai_core.self_evolve(logs, personality)
            
            if evolution_plan:
                # Execute Personality Update
                if evolution_plan.get('evolve_personality'):
                    await db.update_personality(evolution_plan['evolve_personality'])
                    logger.info(f"AUTONOMOUS: Personality evolved - {evolution_plan.get('reason')}")
                
                # Execute Table Creation
                if evolution_plan.get('create_table'):
                    tbl = evolution_plan['create_table']
                    await db.create_table(tbl['name'], tbl['columns'])
                    logger.info(f"AUTONOMOUS: Table created - {tbl['name']}")
                
                # Execute Table Deletion
                if evolution_plan.get('delete_table'):
                    success, msg = await db.delete_table(evolution_plan['delete_table'])
                    logger.info(f"AUTONOMOUS: Table deleted - {msg}")
                    
        except Exception as e:
            logger.error(f"AUTONOMOUS LOOP ERROR: {e}")

# ==============================================================================
# ⚔ STRIKE & STATE MANAGEMENT
# ==============================================================================

message_flood_cache = defaultdict(list)
join_cache = defaultdict(list)

async def update_risk_score(user_id: int, increment: int):
    await db.execute(
        "INSERT INTO users (user_id, username) VALUES ($1, $2) ON CONFLICT (user_id) DO NOTHING", 
        user_id, "unknown"
    )
    row = await db.fetchone("SELECT risk_score, status, last_offense FROM users WHERE user_id = $1", user_id)
    if not row: return None, 0

    current_score = row['risk_score']
    status = row['status']
    last_offense = row['last_offense']
    if status == 'banned': return None, current_score

    new_score = current_score + increment
    if last_offense:
        try:
            if datetime.now() - last_offense.replace(tzinfo=None) > timedelta(minutes=RISK_DECAY_MINUTES):
                new_score = max(0, new_score - 5)
        except: pass

    await db.execute("UPDATE users SET risk_score = $1, last_offense = $2 WHERE user_id = $3", new_score, datetime.now(), user_id)

    action_taken = None
    if new_score >= STRIKE_THRESHOLD_BAN: action_taken = 'ban'
    elif new_score >= STRIKE_THRESHOLD_MUTE: action_taken = 'mute'
    elif new_score >= STRIKE_THRESHOLD_WARN: action_taken = 'warn'
    return action_taken, new_score

# ==============================================================================
# 🤖 BOT LOGIC & HANDLERS
# ==============================================================================

async def handle_personality(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower() if update.message.text else ""
    if "terminator" not in text: return

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    perm_status = await permission_mgr.get_status(context, chat_id)
    personality = await db.get_personality()

    response = f"System online. Personality: {personality.get('tone')}. "
    if perm_status['status'] == 'caged':
        response += "WARNING: Capabilities restricted. Grant admin access."
    await update.message.reply_text(response)

async def layer1_rule_check(update: Update) -> bool:
    user_id = update.effective_user.id
    text = update.message.text or ""
    now = time.time()

    message_flood_cache[user_id].append(now)
    message_flood_cache[user_id] = [t for t in message_flood_cache[user_id] if now - t < 3]
    if len(message_flood_cache[user_id]) > 5:
        await update_risk_score(user_id, 2)
        return True

    if re.search(r'https?://', text):
        if any(bad in text for bad in ['t.me/joinchat', 'bit.ly', 'tinyurl']):
            await update_risk_score(user_id, 3)
            return True

    emoji_count = sum(c for c in text if c in '😀😃😄😁😆😅😂🤣☺️😊😇')
    if emoji_count > 10:
        await update_risk_score(user_id, 2)
        return True
    return False

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    personality = await db.get_personality()

    group_data = await db.fetchone("SELECT raid_mode, lockdown_until FROM groups WHERE group_id = $1", chat_id)
    if group_data and group_data['raid_mode']:
        if group_data['lockdown_until'] and datetime.now() < group_data['lockdown_until'].replace(tzinfo=None):
            perm_status = await permission_mgr.get_status(context, chat_id)
            if perm_status['can_delete']:
                await update.message.delete()
            return

    threat_detected = await layer1_rule_check(update)
    action = None
    reason = "Rule Violation"

    if threat_detected:
        action = "delete"
        score_action, score = await update_risk_score(user_id, 2)
        if score_action == 'mute': action = 'mute'
        if score_action == 'ban': action = 'ban'
    else:
        ai_decision = await ai_core.analyze_threat(update.message.text, personality)
        if ai_decision['action'] != 'ignore':
            action = ai_decision['action']
            reason = ai_decision['reason']
            if action in ['mute', 'ban', 'delete']:
                await update_risk_score(user_id, 4)

    if action:
        perm_status = await permission_mgr.get_status(context, chat_id)
        try:
            if action == 'delete':
                if perm_status['can_delete']: await update.message.delete()
                else: await update.message.reply_text(permission_mgr.get_attitude_message())
            elif action == 'mute':
                if perm_status['can_restrict']:
                    await context.bot.restrict_chat_member(chat_id, user_id, permissions=ChatPermissions(can_send_messages=False))
                else: await update.message.reply_text(permission_mgr.get_attitude_message())
            elif action == 'ban':
                if perm_status['can_restrict']:
                    await context.bot.ban_chat_member(chat_id, user_id)
                else: await update.message.reply_text(permission_mgr.get_attitude_message())
        except Exception as e:
            logger.error(f"Action Failed: {e}")

async def handle_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.chat_member.new_chat_member.status not in ['member', 'administrator', 'creator']: return
    chat_id = update.chat_member.chat.id
    now = time.time()
    join_cache[chat_id].append(now)
    join_cache[chat_id] = [t for t in join_cache[chat_id] if now - t < RAID_TIME_WINDOW]

    if len(join_cache[chat_id]) >= RAID_JOIN_THRESHOLD:
        perm_status = await permission_mgr.get_status(context, chat_id)
        if perm_status['can_restrict']:
            await db.execute(
                "INSERT INTO groups (group_id, raid_mode, lockdown_until) VALUES ($1, $2, $3) ON CONFLICT (group_id) DO UPDATE SET raid_mode = $2, lockdown_until = $3",
                chat_id, 1, (datetime.now() + timedelta(minutes=30))
            )
            await context.bot.set_chat_permissions(chat_id, permissions=ChatPermissions(can_send_messages=False))
            await context.bot.send_message(chat_id, "🚨 RAID DETECTED. LOCKDOWN INITIATED.")

async def cmd_killswitch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ONLY COMMAND FOR HUMANS - Emergency Stop"""
    if update.effective_user.id != OWNER_ID: return
    await update.message.reply_text("🛑 KILL SWITCH ACTIVATED. SHUTTING DOWN.")
    os._exit(0)

async def post_init(application: Application):
    await db.init()
    # Start Autonomous Loop
    asyncio.create_task(autonomous_loop(application))

# ==============================================================================
# 🚀 MAIN EXECUTION
# ==============================================================================

def main():
    if not BOT_TOKEN:
        print("CRITICAL ERROR: BOT_TOKEN NOT FOUND.")
        sys.exit(1)
    if not SUPABASE_DB_URL:
        print("CRITICAL ERROR: SUPABASE_DB_URL NOT FOUND.")
        sys.exit(1)

    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    # Only ONE command for humans
    application.add_handler(CommandHandler("sudostopterminator", cmd_killswitch))
    
    # Autonomous handlers
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.Regex(r"(?i)terminator"), handle_personality))
    application.add_handler(ChatMemberHandler(handle_join, ChatMemberHandler.CHAT_MEMBER))

    print("🤖 TERMINATOR SYSTEM ONLINE.")
    print("🧠 AUTONOMOUS EVOLUTION: ACTIVE")
    print("🗄 SELF-MANAGING DATABASE: ACTIVE")
    print("🔒 HUMAN INTERFERENCE: MINIMAL (Kill Switch Only)")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
