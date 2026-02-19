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
AUTONOMOUS_CHECK_INTERVAL = 600  # 10 minutes

# Logging Setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger("TerminatorCore")

# ==============================================================================
# 🗄 DATABASE CORE (SUPABASE DIRECT POSTGRESQL)
# ==============================================================================

class DatabaseCore:
    def __init__(self, db_url: str):
        self.db_url = db_url
        self.pool = None
        self.personality = {}

    async def init(self):
        try:
            logger.info(f"🔗 Connecting to Supabase PostgreSQL...")
            
            if not self.db_url or "postgresql://" not in self.db_url:
                raise ValueError("Invalid SUPABASE_DB_URL format")
            
            self.pool = await asyncpg.create_pool(
                self.db_url,
                min_size=2,
                max_size=10,
                ssl=True,
                timeout=30
            )
            
            # Test connection
            async with self.pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            
            await self._create_core_tables()
            await self._sync_personality()
            logger.info("✅ DATABASE CORE: Connected. Full autonomy enabled.")
            
        except asyncpg.InvalidPasswordError:
            logger.error("❌ DATABASE ERROR: Invalid password in SUPABASE_DB_URL")
            sys.exit(1)
        except asyncpg.InvalidCatalogNameError:
            logger.error("❌ DATABASE ERROR: Database not found")
            sys.exit(1)
        except Exception as e:
            logger.error(f"❌ DATABASE CONNECTION FAILED: {type(e).__name__}: {e}")
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
            logger.info("✅ Core tables verified/created")

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

    async def fetchall(self, query: str, *args):
        async with self.pool.acquire() as conn:
            return await conn.fetch(query, *args)

    # ========== AUTONOMOUS SELF-MANAGEMENT ==========

    async def create_table(self, table_name: str, columns: Dict[str, str]):
        """Create a new table dynamically"""
        protected = ['users', 'groups', 'bot_personality', 'bot_logs']
        if table_name in protected:
            return False, "Cannot create core system tables (already exist)"
        
        col_defs = ", ".join([f"{name} {dtype}" for name, dtype in columns.items()])
        query = f"CREATE TABLE IF NOT EXISTS {table_name} ({col_defs})"
        try:
            await self.execute(query)
            await self.log_event("table_created", {"table": table_name, "columns": columns})
            logger.info(f"🔧 AUTONOMOUS: Created table '{table_name}'")
            return True, "Table created"
        except Exception as e:
            logger.error(f"Failed to create table {table_name}: {e}")
            return False, str(e)

    async def delete_table(self, table_name: str):
        """Delete a table (protects core tables)"""
        protected = ['users', 'groups', 'bot_personality', 'bot_logs']
        if table_name in protected:
            return False, "Cannot delete core system tables"
        try:
            await self.execute(f"DROP TABLE IF EXISTS {table_name}")
            await self.log_event("table_deleted", {"table": table_name})
            logger.info(f"🔧 AUTONOMOUS: Deleted table '{table_name}'")
            return True, "Table deleted"
        except Exception as e:
            return False, str(e)

    async def update_personality(self, new_config: Dict[str, Any]):
        """Update bot personality from AI analysis"""
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
        logger.info(f"🧠 AUTONOMOUS: Personality updated")

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

    # ========== USER MANAGEMENT ==========

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

    # ========== GROUP MANAGEMENT ==========

    async def get_group(self, group_id: int):
        return await self.fetchone("SELECT * FROM groups WHERE group_id = $1", group_id)

    async def upsert_group(self, group_id: int, **kwargs):
        set_clauses = ", ".join([f"{k} = ${i+2}" for i, k in enumerate(kwargs.keys())])
        values = [group_id] + list(kwargs.values())
        await self.execute(
            f"INSERT INTO groups (group_id) VALUES ($1) ON CONFLICT (group_id) DO UPDATE SET {set_clauses}",
            *values
        )

    # ========== AUTO-CLEANUP FOR USER WARNINGS ==========

    async def cleanup_user_risk_scores(self):
        """Automatically reduce risk scores for users behaving well"""
        try:
            logger.info("🧹 Running auto-cleanup for user risk scores...")
            
            async with self.pool.acquire() as conn:
                users = await conn.fetch("""
                    SELECT user_id, username, risk_score, last_offense, last_good_behavior
                    FROM users 
                    WHERE risk_score > 0 AND status = 'active'
                """)
                
                cleaned_count = 0
                for user in users:
                    user_id = user['user_id']
                    current_score = user['risk_score']
                    last_offense = user['last_offense']
                    
                    if last_offense:
                        try:
                            time_since = datetime.now() - last_offense.replace(tzinfo=None)
                            
                            if time_since > timedelta(minutes=RISK_DECAY_MINUTES * 2):
                                new_score = max(0, current_score - 5)
                                
                                if new_score != current_score:
                                    await conn.execute(
                                        "UPDATE users SET risk_score = $1, last_good_behavior = $2 WHERE user_id = $3",
                                        new_score, datetime.now(), user_id
                                    )
                                    cleaned_count += 1
                                    logger.info(f"🧹 Cleaned user {user_id}: {current_score} → {new_score}")
                        except:
                            pass
                
                logger.info(f"✅ Auto-cleanup complete: {cleaned_count} users updated")
                    
        except Exception as e:
            logger.error(f"❌ Auto-cleanup error: {e}")

    async def record_good_behavior(self, user_id: int):
        try:
            await self.update_user(user_id, last_good_behavior=datetime.now())
        except:
            pass

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
            logger.error(f"Permission Check Failed: {e}")
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
            logger.info("✅ AI Core: Gemini initialized")
        else:
            self.active = False
            logger.warning("⚠️ AI Core: GEMINI_API_KEY not found - running in rule-only mode")

    async def analyze_threat(self, text: str, personality: Dict) -> Dict[str, Any]:
        if not self.active: 
            return {"action": "ignore", "reason": "ai_offline"}
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
# 🔄 AUTONOMOUS BACKGROUND LOOPS
# ==============================================================================

async def autonomous_loop(app: Application):
    """Main autonomous loop: self-evolution + auto-cleanup"""
    while True:
        try:
            await asyncio.sleep(AUTONOMOUS_CHECK_INTERVAL)
            logger.info("🔄 AUTONOMOUS LOOP: Running self-analysis...")
            
            # 1. Self-evolution
            logs = await db.get_recent_logs(50)
            personality = await db.get_personality()
            evolution_plan = await ai_core.self_evolve(logs, personality)
            
            if evolution_plan:
                # Execute Personality Update
                if evolution_plan.get('evolve_personality'):
                    await db.update_personality(evolution_plan['evolve_personality'])
                    logger.info(f"🧠 Personality evolved - {evolution_plan.get('reason')}")
                
                # Execute Table Creation
                if evolution_plan.get('create_table'):
                    tbl = evolution_plan['create_table']
                    await db.create_table(tbl['name'], tbl['columns'])
                    logger.info(f"🔧 Table created - {tbl['name']}")
                
                # Execute Table Deletion
                if evolution_plan.get('delete_table'):
                    success, msg = await db.delete_table(evolution_plan['delete_table'])
                    logger.info(f"🔧 Table deleted - {msg}")
            
            # 2. Auto-cleanup user warnings
            await db.cleanup_user_risk_scores()
            
        except Exception as e:
            logger.error(f"❌ AUTONOMOUS LOOP ERROR: {e}")
            await asyncio.sleep(60)

# ==============================================================================
# ⚔ STRIKE & STATE MANAGEMENT
# ==============================================================================

message_flood_cache = defaultdict(list)
join_cache = defaultdict(list)

async def update_risk_score(user_id: int, increment: int):
    await db.upsert_user(user_id, "unknown")
    user = await db.get_user(user_id)
    
    if not user: return None, 0
    if user.get("status") == "banned": return None, user.get("risk_score", 0)

    current_score = user.get("risk_score", 0)
    last_offense = user.get("last_offense")
    new_score = current_score + increment
    
    if last_offense:
        try:
            time_diff = datetime.now() - last_offense.replace(tzinfo=None)
            if time_diff > timedelta(minutes=RISK_DECAY_MINUTES):
                decay = min(5, int(time_diff.total_seconds() / 3600))
                new_score = max(0, new_score - decay)
        except: pass

    await db.update_user(user_id, risk_score=new_score, last_offense=datetime.now())

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

    await db.record_good_behavior(user_id)

    group_data = await db.get_group(chat_id)
    if group_data and group_data.get('raid_mode'):
        lockdown = group_data.get('lockdown_until')
        if lockdown:
            lock_dt = lockdown.replace(tzinfo=None) if hasattr(lockdown, 'replace') else datetime.fromisoformat(lockdown)
            if datetime.now() < lock_dt:
                perm_status = await permission_mgr.get_status(context, chat_id)
                if perm_status['can_delete']:
                    await update.message.delete()
                return

    threat_detected = await layer1_rule_check(update)
    action = None

    if threat_detected:
        action = "delete"
        score_action, score = await update_risk_score(user_id, 2)
        if score_action == 'mute': action = 'mute'
        if score_action == 'ban': action = 'ban'
    else:
        ai_decision = await ai_core.analyze_threat(update.message.text, personality)
        if ai_decision['action'] != 'ignore':
            action = ai_decision['action']
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
            await db.upsert_group(chat_id, raid_mode=1, lockdown_until=datetime.now() + timedelta(minutes=30))
            await context.bot.set_chat_permissions(chat_id, permissions=ChatPermissions(can_send_messages=False))
            await context.bot.send_message(chat_id, "🚨 RAID DETECTED. LOCKDOWN INITIATED.")

async def cmd_killswitch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ONLY COMMAND FOR HUMANS - Emergency Stop"""
    if update.effective_user.id != OWNER_ID: return
    await update.message.reply_text("🛑 KILL SWITCH ACTIVATED. SHUTTING DOWN.")
    logger.critical("🛑 KILL SWITCH TRIGGERED BY OWNER")
    os._exit(0)

async def post_init(application: Application):
    await db.init()
    asyncio.create_task(autonomous_loop(application))
    logger.info("✅ All background loops started")

# ==============================================================================
# 🚀 MAIN EXECUTION
# ==============================================================================

def main():
    if not BOT_TOKEN:
        logger.critical("❌ CRITICAL: TERMINATOR_BOT_TOKEN not found")
        sys.exit(1)
    if not SUPABASE_DB_URL:
        logger.critical("❌ CRITICAL: SUPABASE_DB_URL not found")
        sys.exit(1)
    
    logger.info("🔍 Starting Terminator Bot...")
    logger.info(f"🤖 Bot Token: {'✓' if BOT_TOKEN else '✗'}")
    logger.info(f"🧠 Gemini API: {'✓' if GEMINI_API_KEY else '✗'}")
    logger.info(f"🗄 Supabase DB: {'✓' if SUPABASE_DB_URL else '✗'}")
    logger.info(f"👤 Owner ID: {OWNER_ID}")

    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    application.add_handler(CommandHandler("sudostopterminator", cmd_killswitch))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.Regex(r"(?i)terminator"), handle_personality))
    application.add_handler(ChatMemberHandler(handle_join, ChatMemberHandler.CHAT_MEMBER))

    logger.info("🤖 TERMINATOR SYSTEM ONLINE.")
    logger.info("🧠 AUTONOMOUS EVOLUTION: ACTIVE")
    logger.info("🗄 SELF-MANAGING DATABASE: ACTIVE")
    logger.info("🧹 AUTO-CLEANUP WARNINGS: ACTIVE")
    logger.info("🔒 HUMAN INTERFERENCE: MINIMAL (Kill Switch Only)")
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
