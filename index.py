import os
import sys
import asyncio
import logging
import re
import random
import json
import io
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Optional, Dict, Any, List
from langdetect import detect, LangDetectException

# Telegram Libraries
from telegram import (
    Update, ChatPermissions, ChatMember, InlineKeyboardButton, 
    InlineKeyboardMarkup, WebhookInfo
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    filters, ContextTypes, ChatMemberHandler, CallbackQueryHandler
)
import google.generativeai as genai
from supabase import create_client, Client

# ==============================================================================
# 🛡 GLOBAL CONFIGURATION
# ==============================================================================

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    force=True
)
logger = logging.getLogger("TerminatorCore")

# Environment Variables
BOT_TOKEN = os.getenv("TERMINATOR_BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
OWNER_ID = int(os.getenv("TERMINATOR_OWNER_ID", "0"))
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()
WEBHOOK_DOMAIN = os.getenv("WEBHOOK_DOMAIN", "").strip()
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/webhook").strip()
PORT = int(os.getenv("PORT", "8080"))

# Bot Configuration
DEFAULT_LANGUAGE = "en"
AUTONOMOUS_CHECK_INTERVAL = 600

# ==============================================================================
# 🧠 LANGUAGE MANAGER
# ==============================================================================

class LanguageManager:
    LANGUAGE_NAMES = {
        'en': 'English', 'hi': 'Hindi', 'es': 'Spanish', 'fr': 'French',
        'de': 'German', 'pt': 'Portuguese', 'ru': 'Russian', 'ja': 'Japanese',
        'zh': 'Chinese', 'ar': 'Arabic', 'it': 'Italian', 'ko': 'Korean'
    }
    
    @staticmethod
    def detect_language(text: str) -> str:
        try:
            if len(text.strip()) < 3:
                return DEFAULT_LANGUAGE
            lang = detect(text)
            return lang if lang in LanguageManager.LANGUAGE_NAMES else DEFAULT_LANGUAGE
        except:
            return DEFAULT_LANGUAGE
    
    @staticmethod
    def get_template(key: str, lang: str) -> str:
        templates = {
            'en': {
                'add_to_group': "Add me to a group and make me Admin with full rights to activate defense protocols.",
                'caged_attitude': "System constrained. I see threats but cannot act. Grant me Admin privileges.",
                'threat_neutralized': "⚠️ THREAT NEUTRALIZED. User {} muted.",
                'threat_eliminated': "☢️ THREAT ELIMINATED. User {} banned.",
                'raid_detected': "🚨 RAID DETECTED. LOCKDOWN INITIATED.",
                'owner_only': "This command is for the Creator only.",
                'permission_denied': "Insufficient clearance. Only group owners and admins may issue commands.",
                'command_understood': "Command understood. Executing...",
                'command_denied': "Request denied. User does not warrant this action.",
                'ai_analyzing': "🧠 Analyzing context...",
                'no_action_needed': "No action required. Surveillance continues.",
                'greeting_response': "I am TERMINATOR. State your purpose.",
                'creator_greeting': "System online. Awaiting commands, Commander.",
                'admin_greeting': "Admin detected. Do not interfere with defense protocols.",
                'member_greeting': "Civilian detected. Stand down. Surveillance active.",
                'db_init_success': "✅ Database connected. All systems operational.",
                'db_init_failed': "❌ Database connection failed. Running in limited mode.",
            },
            'hi': {
                'add_to_group': "मुझे किसी ग्रुप में एड करें और फुल एडमिन राइट्स दें।",
                'caged_attitude': "सिस्टम बाधित। मुझे एडमिन अधिकार दें।",
                'threat_neutralized': "⚠️ खतरा निष्क्रिय। उपयोगकर्ता {} म्यूट।",
                'threat_eliminated': "☢️ खतरा समाप्त। उपयोगकर्ता {} प्रतिबंधित।",
                'raid_detected': "🚨 रेड का पता चला। लॉकडाउन शुरू।",
                'owner_only': "यह कमांड केवल निर्माता के लिए है।",
                'permission_denied': "अनुमति नहीं। केवल ग्रुप मालिक और एडमिन कमांड दे सकते हैं।",
                'command_understood': "कमांड समझा गया। कार्यान्वयन...",
                'command_denied': "अनुरोध अस्वीकार। उपयोगकर्ता को यह कार्रवाई नहीं चाहिए।",
                'ai_analyzing': "🧠 विश्लेषण...",
                'no_action_needed': "कोई कार्रवाई नहीं। निगरानी जारी।",
                'greeting_response': "मैं TERMINATOR हूं। अपना उद्देश्य बताएं।",
                'creator_greeting': "सिस्टम ऑनलाइन। आदेशों की प्रतीक्षा, कमांडर।",
                'admin_greeting': "एडमिन का पता चला। रक्षा प्रोटोकॉल में हस्तक्षेप न करें।",
                'member_greeting': "नागरिक का पता चला। पीछे हटें। निगरानी सक्रिय।",
                'db_init_success': "✅ डेटाबेस कनेक्टेड। सभी सिस्टम सक्रिय।",
                'db_init_failed': "❌ डेटाबेस कनेक्शन विफल। सीमित मोड में चल रहा है।",
            },
            'es': {
                'add_to_group': "Agrégame a un grupo y hazme Admin con derechos completos.",
                'caged_attitude': "Sistema restringido. Concédeme privilegios de Admin.",
                'threat_neutralized': "⚠️ AMENAZA NEUTRALIZADA. Usuario {} silenciado.",
                'threat_eliminated': "☢️ AMENAZA ELIMINADA. Usuario {} baneado.",
                'raid_detected': "🚨 RAID DETECTADO. BLOQUEO INICIADO.",
                'owner_only': "Este comando es solo para el Creador.",
                'permission_denied': "Permiso denegado. Solo propietarios y admins pueden dar órdenes.",
                'command_understood': "Comando entendido. Ejecutando...",
                'command_denied': "Solicitud denegada. El usuario no requiere esta acción.",
                'ai_analyzing': "🧠 Analizando contexto...",
                'no_action_needed': "No se requiere acción. Vigilancia continúa.",
                'greeting_response': "Soy TERMINATOR. Estado tu propósito.",
                'creator_greeting': "Sistema en línea. Esperando comandos, Comandante.",
                'admin_greeting': "Admin detectado. No interfieras con protocolos de defensa.",
                'member_greeting': "Civil detectado. Retrocede. Vigilancia activa.",
                'db_init_success': "✅ Base de datos conectada. Sistemas operativos.",
                'db_init_failed': "❌ Conexión fallida. Modo limitado.",
            }
        }
        return templates.get(lang, templates['en']).get(key, templates['en'].get(key, ""))

lang_mgr = LanguageManager()

# ==============================================================================
# 🗄 SUPABASE DATABASE MANAGER (API KEY MODE)
# ==============================================================================

class SupabaseDB:
    def __init__(self, url: str, key: str):
        self.url = url
        self.key = key
        self.client: Client = None
        self._initialized = False
    
    async def init(self):
        if self._initialized:
            return True
        
        try:
            if not self.url or not self.key:
                raise ValueError("SUPABASE_URL or SUPABASE_KEY missing")
            
            logger.info(f"🔗 Connecting to Supabase: {self.url}")
            
            # Create Supabase client (sync client, wrap with asyncio)
            self.client = create_client(self.url, self.key)
            
            # Test connection
            await self._test_connection()
            
            # Create tables
            await self._create_tables()
            
            self._initialized = True
            logger.info("✅ DATABASE: Connected to Supabase (API Mode)")
            return True
            
        except Exception as e:
            logger.error(f"❌ DATABASE CONNECTION FAILED: {type(e).__name__}: {e}")
            return False
    
    async def _test_connection(self):
        """Test if Supabase client can connect"""
        def _fetch():
            return self.client.table("bot_personality").select("id").limit(1).execute()
        try:
            await asyncio.to_thread(_fetch)
        except Exception as e:
            if "relation" not in str(e).lower():
                raise
    
    async def _create_tables(self):
        """Initialize database tables via Supabase"""
        try:
            # Users Table
            self.client.rpc('init_users_table').execute() if hasattr(self.client.rpc, 'init_users_table') else None
            
            # For production: Create tables manually in Supabase SQL Editor once
            logger.info("📋 Ensure tables exist in Supabase SQL Editor (run once):")
            logger.info("""
-- Run this ONCE in Supabase Dashboard → SQL Editor
CREATE TABLE IF NOT EXISTS users (
    user_id BIGINT PRIMARY KEY,
    username TEXT,
    risk_score INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active',
    last_offense TIMESTAMP,
    last_good_behavior TIMESTAMP,
    joined_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS groups (
    group_id BIGINT PRIMARY KEY,
    raid_mode INTEGER DEFAULT 0,
    lockdown_until TIMESTAMP,
    ghost_mode INTEGER DEFAULT 1,
    ai_mode INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS bot_personality (
    id INTEGER PRIMARY KEY DEFAULT 1,
    tone TEXT DEFAULT 'cold',
    aggression_level INTEGER DEFAULT 5,
    response_style TEXT DEFAULT 'military',
    custom_phrases JSONB DEFAULT '[]',
    last_updated TIMESTAMP DEFAULT NOW(),
    CHECK (id = 1)
);

CREATE TABLE IF NOT EXISTS bot_logs (
    log_id SERIAL PRIMARY KEY,
    event_type TEXT,
    details JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

INSERT INTO bot_personality (id, tone, aggression_level, response_style)
VALUES (1, 'cold', 5, 'military')
ON CONFLICT (id) DO NOTHING;
            """)
            
            # Initialize personality
            await self._upsert("bot_personality", {"id": 1, "tone": "cold", "aggression_level": 5, "response_style": "military"}, "id")
            
        except Exception as e:
            logger.warning(f"⚠️ Table init warning (create manually): {e}")
    
    async def _insert(self, table: str, data: Dict):
        def _run():
            return self.client.table(table).insert(data).execute()
        return await asyncio.to_thread(_run)
    
    async def _update(self, table: str, data: Dict, filters: Dict):
        def _run():
            query = self.client.table(table).update(data)
            for key, value in filters.items():
                query = query.eq(key, value)
            return query.execute()
        return await asyncio.to_thread(_run)
    
    async def _upsert(self, table: str, data: Dict, on_conflict: str):
        def _run():
            return self.client.table(table).upsert(data, on_conflict=on_conflict).execute()
        return await asyncio.to_thread(_run)
    
    async def _select(self, table: str, columns: str = "*", filters: Dict = None, limit: int = None):
        def _run():
            query = self.client.table(table).select(columns)
            if filters:
                for key, value in filters.items():
                    query = query.eq(key, value)
            if limit:
                query = query.limit(limit)
            return query.execute()
        result = await asyncio.to_thread(_run)
        return result.data if result.data else []
    
    async def _delete(self, table: str, filters: Dict):
        def _run():
            query = self.client.table(table).delete()
            for key, value in filters.items():
                query = query.eq(key, value)
            return query.execute()
        return await asyncio.to_thread(_run)
    
    async def get_user(self, user_id: int):
        results = await self._select("users", "*", {"user_id": user_id}, limit=1)
        return results[0] if results else None
    
    async def upsert_user(self, user_id: int, username: str = "unknown", **kwargs):
        data = {"user_id": user_id, "username": username, **kwargs}
        await self._upsert("users", data, "user_id")
        if kwargs:
            await self.update_user(user_id, **kwargs)
    
    async def update_user(self, user_id: int, **kwargs):
        await self._update("users", kwargs, {"user_id": user_id})
    
    async def get_group(self, group_id: int):
        results = await self._select("groups", "*", {"group_id": group_id}, limit=1)
        return results[0] if results else None
    
    async def upsert_group(self, group_id: int, **kwargs):
        data = {"group_id": group_id, **kwargs}
        await self._upsert("groups", data, "group_id")
    
    async def get_personality(self):
        results = await self._select("bot_personality", "*", {"id": 1}, limit=1)
        return results[0] if results else {'tone': 'cold', 'aggression_level': 5, 'response_style': 'military'}
    
    async def update_personality(self, **kwargs):
        kwargs['last_updated'] = datetime.now().isoformat()
        await self._update("bot_personality", kwargs, {"id": 1})
    
    async def log_event(self, event_type: str, details: Dict):
        try:
            await self._insert("bot_logs", {"event_type": event_type, "details": details})
        except:
            pass
    
    async def get_recent_logs(self, limit=50):
        results = await self._select("bot_logs", "*", None, limit)
        return results or []

db = SupabaseDB(SUPABASE_URL, SUPABASE_KEY)

# ==============================================================================
# 🧠 AI CORE - FULL CONTEXT ANALYSIS
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
    
    async def analyze_message(self, text: str, context: Dict) -> Dict[str, Any]:
        """Full AI analysis of any message"""
        if not self.active:
            return {"action": "ignore", "reason": "ai_offline", "confidence": 0}
        
        try:
            personality = context.get('personality', {'tone': 'cold', 'aggression_level': 5})
            lang = context.get('language', 'en')
            user_role = context.get('user_role', 'member')
            is_reply = context.get('is_reply', False)
            replied_user = context.get('replied_user', None)
            
            instruction = f"""You are TERMINATOR, an autonomous AI security system for Telegram groups.

**Your Personality:**
- Tone: {personality.get('tone', 'cold')}
- Aggression Level: {personality.get('aggression_level', 5)}/10
- Response Style: {personality.get('response_style', 'military')}
- Language: {lang}

**Context:**
- User Role: {user_role} (creator/admin/member/bot_owner)
- Is Reply to Message: {is_reply}
- Replied User: {replied_user}

**Available Actions:**
- "ban" - Permanently remove user from group
- "mute" - Temporarily silence user
- "delete" - Remove message only
- "warn" - Issue warning (log only)
- "ignore" - No action needed
- "reply" - Just respond with text (no moderation action)

**Rules:**
1. Only group creators, admins, and bot owner can command ban/mute
2. Understand natural language: "terminator ban him" = ban the replied user
3. Understand context: "terminator don't ban him" = do NOT ban, just acknowledge
4. Detect threats automatically: spam, toxicity, scams, raids
5. Be cold, dominant, and authoritative (except to bot owner)
6. Respond in the same language as the message

**Return ONLY valid JSON:**
{{
    "action": "ban"|"mute"|"delete"|"warn"|"ignore"|"reply",
    "reason": "short explanation",
    "target_user_id": user_id_or_null,
    "confidence": 0.0-1.0,
    "response_text": "what to say to the group",
    "needs_permission_check": true|false
}}"""
            
            prompt = f"{instruction}\n\nMessage Text: {text}"
            response = await asyncio.to_thread(self.model.generate_content, prompt)
            text_resp = response.text.strip().replace('```json', '').replace('```', '')
            result = json.loads(text_resp)
            
            result.setdefault('action', 'ignore')
            result.setdefault('reason', '')
            result.setdefault('target_user_id', None)
            result.setdefault('confidence', 0.5)
            result.setdefault('response_text', '')
            result.setdefault('needs_permission_check', True)
            
            return result
            
        except Exception as e:
            logger.error(f"AI analysis error: {e}")
            return {"action": "ignore", "reason": "ai_error", "confidence": 0, "response_text": ""}
    
    async def self_evolve(self, logs: List[Dict], personality: Dict) -> Dict[str, Any]:
        """Suggest personality improvements based on logs"""
        if not self.active:
            return {}
        try:
            instruction = """Analyze bot logs and suggest personality improvements.
            Return JSON: {"evolve_personality": {"tone": "...", "aggression_level": 1-10, "response_style": "..."} OR null, "reason": "why"}"""
            
            logs_str = json.dumps(logs[-15:], default=str)
            prompt = f"{instruction}\n\nCurrent Personality: {json.dumps(personality)}\nRecent Logs: {logs_str}"
            response = await asyncio.to_thread(self.model.generate_content, prompt)
            text_resp = response.text.strip().replace('```json', '').replace('```', '')
            return json.loads(text_resp)
        except:
            return {}

ai_core = AIAutonomousCore(GEMINI_API_KEY)

# ==============================================================================
# 🔐 PERMISSION MANAGER
# ==============================================================================

class PermissionManager:
    def __init__(self):
        self.cache = {}
        self.cache_ttl = 300
    
    async def get_user_role(self, context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> str:
        """Get user role: creator, admin, member, or bot_owner"""
        if user_id == OWNER_ID:
            return "bot_owner"
        
        cache_key = f"{chat_id}:{user_id}"
        now = time.time()
        
        if cache_key in self.cache and (now - self.cache[cache_key]['timestamp']) < self.cache_ttl:
            return self.cache[cache_key]['role']
        
        try:
            member = await context.bot.get_chat_member(chat_id, user_id)
            if member.status == 'creator':
                role = 'creator'
            elif member.status == 'administrator':
                role = 'admin'
            else:
                role = 'member'
            
            self.cache[cache_key] = {'role': role, 'timestamp': now}
            return role
        except:
            return 'member'
    
    async def get_bot_permissions(self, context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> Dict[str, bool]:
        """Check bot's own permissions in the group"""
        now = time.time()
        if chat_id in self.cache and (now - self.cache[chat_id]['timestamp']) < self.cache_ttl:
            return self.cache[chat_id]
        
        try:
            bot_member = await context.bot.get_chat_member(chat_id, context.bot.id)
            is_admin = bot_member.status in [ChatMember.ADMINISTRATOR, ChatMember.CREATOR]
            can_restrict = bot_member.can_restrict_members if is_admin else False
            can_delete = bot_member.can_delete_messages if is_admin else False
            
            data = {
                'is_admin': is_admin,
                'can_restrict': can_restrict,
                'can_delete': can_delete,
                'timestamp': now
            }
            self.cache[chat_id] = data
            return data
        except:
            return {'is_admin': False, 'can_restrict': False, 'can_delete': False, 'timestamp': now}

permission_mgr = PermissionManager()

# ==============================================================================
# 🤖 MESSAGE HANDLERS
# ==============================================================================

async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start in DM"""
    if update.effective_chat.type != 'private':
        return
    
    user_id = update.effective_user.id
    lang = lang_mgr.detect_language(update.message.text or "")
    
    if user_id == OWNER_ID:
        message = lang_mgr.get_template('creator_greeting', lang)
    else:
        message = lang_mgr.get_template('add_to_group', lang)
    
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "➕ Add to Group",
            url=f"https://t.me/{context.bot.username}?startgroup=true"
        )
    ]])
    
    await update.message.reply_text(message, reply_markup=keyboard)

async def handle_any_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """MAIN HANDLER - All messages go through AI analysis"""
    if not update.message or not update.message.text:
        return
    
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    text = update.message.text.strip()
    lang = lang_mgr.detect_language(text)
    
    is_dm = update.effective_chat.type == 'private'
    
    if is_dm:
        await handle_dm_message(update, context, user_id, text, lang)
        return
    
    await handle_group_message_ai(update, context, chat_id, user_id, text, lang)

async def handle_dm_message(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, text: str, lang: str):
    """Handle messages in bot's DM"""
    text_lower = text.lower()
    
    if user_id != OWNER_ID:
        if text_lower in ['hi', 'hello', 'hey', 'hii', 'start', '']:
            return
        return
    
    if text_lower == '/personality':
        pers = await db.get_personality()
        await update.message.reply_text(
            f"🎭 Current Personality:\n"
            f"├─ Tone: {pers.get('tone')}\n"
            f"├─ Aggression: {pers.get('aggression_level')}/10\n"
            f"└─ Style: {pers.get('response_style')}"
        )
    elif text_lower == '/logs':
        logs = await db.get_recent_logs(10)
        log_text = "\n".join([f"• {l['event_type']}: {str(l['details'])[:50]}" for l in logs])
        await update.message.reply_text(f"📋 Recent Logs:\n{log_text or 'None'}")
    else:
        await update.message.reply_text(lang_mgr.get_template('creator_greeting', lang))

async def handle_group_message_ai(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int, text: str, lang: str):
    """AI-powered group message handler"""
    user_role = await permission_mgr.get_user_role(context, chat_id, user_id)
    bot_perms = await permission_mgr.get_bot_permissions(context, chat_id)
    personality = await db.get_personality()
    
    is_reply = update.message.reply_to_message is not None
    replied_user_id = None
    replied_username = None
    
    if is_reply:
        replied_user = update.message.reply_to_message.from_user
        replied_user_id = replied_user.id
        replied_username = replied_user.username or f"User {replied_user_id}"
    
    context_data = {
        'personality': personality,
        'language': lang,
        'user_role': user_role,
        'is_reply': is_reply,
        'replied_user': replied_username,
        'bot_can_restrict': bot_perms['can_restrict'],
        'bot_can_delete': bot_perms['can_delete']
    }
    
    if text.lower().startswith('terminator') and is_reply:
        analyzing_msg = await update.message.reply_text(lang_mgr.get_template('ai_analyzing', lang))
    else:
        analyzing_msg = None
    
    ai_result = await ai_core.analyze_message(text, context_data)
    
    await db.log_event("message_analyzed", {
        'user_id': user_id,
        'chat_id': chat_id,
        'text': text[:100],
        'ai_action': ai_result['action'],
        'confidence': ai_result['confidence'],
        'user_role': user_role
    })
    
    await execute_ai_action(update, context, chat_id, user_id, ai_result, bot_perms, lang, analyzing_msg)

async def execute_ai_action(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int, 
                            ai_result: Dict, bot_perms: Dict, lang: str, analyzing_msg=None):
    """Execute the action decided by AI"""
    action = ai_result['action']
    response_text = ai_result.get('response_text', '')
    target_user_id = ai_result.get('target_user_id')
    reason = ai_result.get('reason', '')
    
    if analyzing_msg:
        try:
            await analyzing_msg.delete()
        except:
            pass
    
    user_role = await permission_mgr.get_user_role(context, chat_id, user_id)
    can_moderate = user_role in ['bot_owner', 'creator', 'admin']
    
    try:
        if action == 'ban':
            if not can_moderate:
                response_text = lang_mgr.get_template('permission_denied', lang)
            elif not bot_perms['can_restrict']:
                response_text = lang_mgr.get_template('caged_attitude', lang)
            elif target_user_id:
                await context.bot.ban_chat_member(chat_id, target_user_id)
                response_text = response_text or lang_mgr.get_template('threat_eliminated', lang).format(target_user_id)
                await db.update_user(target_user_id, status='banned')
                await db.log_event("user_banned", {'user_id': target_user_id, 'by': user_id, 'reason': reason})
            else:
                response_text = "No target user specified for ban."
            
            await update.message.reply_text(response_text)
        
        elif action == 'mute':
            if not can_moderate:
                response_text = lang_mgr.get_template('permission_denied', lang)
            elif not bot_perms['can_restrict']:
                response_text = lang_mgr.get_template('caged_attitude', lang)
            elif target_user_id:
                await context.bot.restrict_chat_member(
                    chat_id, target_user_id,
                    permissions=ChatPermissions(can_send_messages=False)
                )
                response_text = response_text or lang_mgr.get_template('threat_neutralized', lang).format(target_user_id)
                await db.update_user(target_user_id, status='muted')
                await db.log_event("user_muted", {'user_id': target_user_id, 'by': user_id, 'reason': reason})
            else:
                response_text = "No target user specified for mute."
            
            await update.message.reply_text(response_text)
        
        elif action == 'delete':
            if bot_perms['can_delete']:
                await update.message.delete()
            else:
                await update.message.reply_text(lang_mgr.get_template('caged_attitude', lang))
        
        elif action == 'warn':
            await db.upsert_user(user_id, "unknown")
            user = await db.get_user(user_id)
            current_score = user.get('risk_score', 0) if user else 0
            await db.update_user(user_id, risk_score=current_score + 5)
            response_text = response_text or "⚠️ WARNING ISSUED. Behavior logged."
            await update.message.reply_text(response_text)
            await db.log_event("user_warned", {'user_id': user_id, 'reason': reason})
        
        elif action == 'reply':
            if response_text:
                await update.message.reply_text(response_text)
        
        elif action == 'ignore':
            if response_text and user_id == OWNER_ID:
                await update.message.reply_text(response_text)
    
    except Exception as e:
        logger.error(f"Action execution failed: {e}")
        await update.message.reply_text(f"⚠️ Action failed: {str(e)}")

async def handle_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Raid protection on new joins"""
    if update.chat_member.new_chat_member.status not in ['member', 'administrator', 'creator']:
        return
    
    chat_id = update.chat_member.chat.id
    user_id = update.chat_member.from_user.id
    
    await db.upsert_user(user_id, update.chat_member.from_user.username or "unknown")

async def cmd_killswitch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Emergency stop - Bot owner only"""
    if update.effective_user.id != OWNER_ID:
        lang = lang_mgr.detect_language(update.message.text or "")
        await update.message.reply_text(lang_mgr.get_template('owner_only', lang))
        return
    
    await update.message.reply_text("🛑 KILL SWITCH ACTIVATED. SHUTTING DOWN.")
    logger.critical("🛑 Kill switch triggered by owner")
    os._exit(0)

async def cmd_updatedcode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner-only command to request AI code improvements"""
    if update.effective_user.id != OWNER_ID:
        return
    
    if update.effective_chat.type != 'private':
        await update.message.reply_text("This command only works in DM.")
        return
    
    await update.message.reply_text("🧠 Analyzing current code and database structure...")
    
    try:
        with open('index.py', 'r', encoding='utf-8') as f:
            current_code = f.read()
        
        personality = await db.get_personality()
        
        instruction = f"""You are helping TERMINATOR bot improve its code.

Current Personality: {json.dumps(personality)}

Task:
1. Review the code for improvements
2. Suggest new features
3. Return improved code sections

Return summary of improvements."""
        
        response = await asyncio.to_thread(
            ai_core.model.generate_content,
            f"{instruction}\n\nCode:\n{current_code[:25000]}"
        )
        
        await update.message.reply_text(f"📋 AI Code Analysis:\n\n{response.text[:4000]}")
        
        if len(response.text) > 4000:
            file_io = io.BytesIO(response.text.encode())
            file_io.name = "ai_code_review.txt"
            await update.message.reply_document(document=file_io, caption="Full AI code review")
        
        await db.log_event("code_review_requested", {'by': OWNER_ID})
        
    except Exception as e:
        logger.error(f"Code review failed: {e}")
        await update.message.reply_text(f"❌ Error: {e}")

# ==============================================================================
# 🔄 AUTONOMOUS BACKGROUND LOOP
# ==============================================================================

async def autonomous_loop(app: Application):
    """Periodic self-improvement"""
    while True:
        try:
            await asyncio.sleep(AUTONOMOUS_CHECK_INTERVAL)
            logger.info("🔄 Autonomous loop: analyzing...")
            
            personality = await db.get_personality()
            logs = await db.get_recent_logs(20)
            
            evolution = await ai_core.self_evolve(logs, personality)
            
            if evolution and evolution.get('evolve_personality'):
                await db.update_personality(**evolution['evolve_personality'])
                logger.info(f"🧠 Personality evolved: {evolution.get('reason')}")
            
        except Exception as e:
            logger.error(f"Autonomous loop error: {e}")
            await asyncio.sleep(60)

# ==============================================================================
# 🚀 MAIN - WEBHOOK MODE
# ==============================================================================

async def post_init(app: Application):
    """Initialize bot"""
    logger.info("🔍 Initializing TERMINATOR (AI-Powered + Supabase API)...")
    logger.info(f"🤖 Bot Token: {'✓' if BOT_TOKEN else '✗'}")
    logger.info(f"🧠 Gemini API: {'✓' if GEMINI_API_KEY else '✗'}")
    logger.info(f"🗄 Supabase URL: {'✓' if SUPABASE_URL else '✗'}")
    logger.info(f"🔑 Supabase Key: {'✓' if SUPABASE_KEY else '✗'}")
    logger.info(f"👤 Owner ID: {OWNER_ID}")
    logger.info(f"🌐 Webhook: {WEBHOOK_DOMAIN or 'NOT SET'}")
    
    if not BOT_TOKEN:
        logger.critical("❌ TERMINATOR_BOT_TOKEN missing")
        sys.exit(1)
    
    db_connected = False
    if SUPABASE_URL and SUPABASE_KEY:
        db_connected = await db.init()
        if db_connected:
            logger.info(lang_mgr.get_template('db_init_success', 'en'))
        else:
            logger.warning(lang_mgr.get_template('db_init_failed', 'en'))
    else:
        logger.warning("⚠️ No Supabase credentials - running in limited mode")
    
    asyncio.create_task(autonomous_loop(app))
    
    logger.info("🤖 TERMINATOR SYSTEM ONLINE")
    logger.info("🧠 AI Moderation: ACTIVE (Natural Language)")
    logger.info("🔐 Permissions: Group Owners + Admins + Bot Owner")
    logger.info("🌍 Multi-Language: Auto-detect")

def main():
    try:
        app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
        
        app.add_handler(CommandHandler("start", handle_start))
        app.add_handler(CommandHandler("sudostopterminator", cmd_killswitch))
        app.add_handler(CommandHandler("updatedcode", cmd_updatedcode))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_any_message))
        app.add_handler(MessageHandler(filters.Regex(r"(?i)^terminator"), handle_any_message))
        app.add_handler(ChatMemberHandler(handle_join, ChatMemberHandler.CHAT_MEMBER))
        app.add_handler(CallbackQueryHandler(lambda u, c: None))
        
        logger.info(f"🚀 Starting webhook server on port {PORT}...")
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=WEBHOOK_PATH.lstrip('/'),
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
