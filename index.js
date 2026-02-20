// ============================================================================
// 🤖 TERMINATOR - NODE.JS VERSION (WEBHOOK MODE - DATABASE & MEMORY FIXED)
// ============================================================================

import express from "express";
import { Telegraf } from "telegraf";
import { createClient } from "@supabase/supabase-js";
import { GoogleGenerativeAI, HarmCategory, HarmBlockThreshold } from "@google/generative-ai";

// ============================================================================
// 🔐 ENV CONFIG
// ============================================================================

const BOT_TOKEN = process.env.TERMINATOR_BOT_TOKEN;
const GEMINI_API_KEY = process.env.GEMINI_API_KEY;
const OWNER_ID = Number(process.env.TERMINATOR_OWNER_ID || 0);
const SUPABASE_URL = process.env.SUPABASE_URL;
const SUPABASE_KEY = process.env.SUPABASE_KEY;
const RENDER_EXTERNAL_HOSTNAME = process.env.RENDER_EXTERNAL_HOSTNAME;
const PORT = process.env.PORT || 10000;

console.log("🔍 ENV CHECK:");
console.log("BOT_TOKEN:", BOT_TOKEN ? "OK" : "MISSING");
console.log("GEMINI_API_KEY:", GEMINI_API_KEY ? "OK" : "MISSING");
console.log("SUPABASE_URL:", SUPABASE_URL ? "OK" : "MISSING");
console.log("SUPABASE_KEY:", SUPABASE_KEY ? "OK" : "MISSING");
console.log("RENDER_EXTERNAL_HOSTNAME:", RENDER_EXTERNAL_HOSTNAME ? "OK" : "MISSING");

if (!BOT_TOKEN) {
  console.error("❌ BOT TOKEN MISSING");
  process.exit(1);
}

// ============================================================================
// 🚀 INIT
// ============================================================================

const bot = new Telegraf(BOT_TOKEN);
const app = express();
app.use(express.json());

// Supabase Init
let supabase = null;
if (SUPABASE_URL && SUPABASE_KEY) {
  try {
    supabase = createClient(SUPABASE_URL, SUPABASE_KEY);
    console.log("✅ Supabase client created");
  } catch (err) {
    console.error("❌ Supabase init failed:", err.message);
  }
}

// Gemini Init
let model = null;
if (GEMINI_API_KEY) {
  try {
    const genAI = new GoogleGenerativeAI(GEMINI_API_KEY);
    // Updated to the most recent stable model version
    model = genAI.getGenerativeModel({ 
      model: "gemini-2.5-flash",
      safetySettings: [
        { category: HarmCategory.HARM_CATEGORY_HARASSMENT, threshold: HarmBlockThreshold.BLOCK_NONE },
        { category: HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold: HarmBlockThreshold.BLOCK_NONE },
        { category: HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold: HarmBlockThreshold.BLOCK_NONE },
        { category: HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold: HarmBlockThreshold.BLOCK_NONE },
      ]
    });
    console.log("✅ Gemini model ready");
  } catch (err) {
    console.error("❌ Gemini init failed:", err.message);
  }
}

// ============================================================================
// 🧠 AI RESPONSE FUNCTION
// ============================================================================

async function generateAIResponse(userId, text, role = "member") {
  if (!model) return "System Offline: AI core not initialized.";

  let contents = [];
  let history = [];
  let botPersona = { tone: 'cold', aggression_level: 5, response_style: 'military', custom_phrases: [] };
  let userInfo = { risk_score: 0, status: 'active' };

  let sevenDaysAgo = new Date();
  sevenDaysAgo.setDate(sevenDaysAgo.getDate() - 7);

  if (supabase) {
    // 1. Concurrently fetch chat history, bot personality, and user info for maximum speed
    const [memReq, personaReq, userReq] = await Promise.all([
      supabase.from("chat_memory")
        .select("role, content")
        .eq("user_id", userId)
        .gte("created_at", sevenDaysAgo.toISOString())
        .order("created_at", { ascending: true }),
      supabase.from("bot_personality").select("*").eq("id", 1).maybeSingle(),
      supabase.from("users").select("*").eq("user_id", userId).maybeSingle()
    ]);

    if (memReq.data) history = memReq.data;
    if (personaReq.data) botPersona = personaReq.data;
    if (userReq.data) userInfo = userReq.data;

    // FIX: Gemini crashes if roles don't alternate. We combine consecutive messages from the same role.
    for (const msg of history) {
      const last = contents[contents.length - 1];
      if (last && last.role === msg.role) {
        last.parts[0].text += `\n\n${msg.content}`;
      } else {
        contents.push({ role: msg.role, parts: [{ text: msg.content }] });
      }
    }

    // 2. Save the NEW user message to the database (fire & forget, NO .catch() chaining)
    supabase.from("chat_memory")
      .insert([{ user_id: userId, role: "user", content: text }])
      .then(({ error }) => { if (error) console.error("DB Error:", error.message) });
  }

  // 3. Add the current message to the Gemini contents array (checking for alternating roles)
  const lastContent = contents[contents.length - 1];
  if (lastContent && lastContent.role === "user") {
    lastContent.parts[0].text += `\n\n${text}`;
  } else {
    contents.push({ role: "user", parts: [{ text }] });
  }

  // 4. Dynamic System Prompt using database personality and user context
  const systemPrompt = `
You are TERMINATOR, a powerful AI Telegram moderator.

Current Persona Settings:
- Tone: ${botPersona.tone}
- Aggression Level: ${botPersona.aggression_level}/10
- Style: ${botPersona.response_style}
${botPersona.custom_phrases && botPersona.custom_phrases.length > 0 ? `- Custom Catchphrases to use occasionally: ${botPersona.custom_phrases.join(', ')}` : ''}

User Context:
- Role: ${role}
- Risk Score: ${userInfo.risk_score} (Higher = more suspicious/dangerous)
- Status: ${userInfo.status}

Rules:
- Reply in the same language as the user.
- If user is owner → be respectful.
- If user is member with high risk score → be extremely dominant and threatening.
- If user is member with low risk score → be strict but standard.
`;

  try {
    const result = await model.generateContent({
      systemInstruction: { parts: [{ text: systemPrompt }] },
      contents: contents
    });
    
    const replyText = result.response.text();

    if (supabase) {
      // 5. Save the Bot's reply to memory
      supabase.from("chat_memory")
        .insert([{ user_id: userId, role: "model", content: replyText }])
        .then(({ error }) => { if (error) console.error("Memory Save Error:", error.message) });
      
      // 6. Delete messages older than 7 days for this user to save database storage
      supabase.from("chat_memory")
        .delete()
        .lt("created_at", sevenDaysAgo.toISOString())
        .eq("user_id", userId)
        .then(({ error }) => { if (error) console.error("Memory Cleanup Error:", error.message) });
    }

    return replyText;
  } catch (err) {
    console.error("AI error:", err);
    // Prints the exact error into Telegram so we can debug it
    return `Error processing query. My neural net is temporarily disrupted.\n\n[DEBUG REASON]: ${err.message}`;
  }
}

// ============================================================================
// 👤 START (DM)
// ============================================================================

bot.start(async (ctx) => {
  if (ctx.chat.type !== "private") return;
  await ctx.reply("Add me in your group with full admin rights, disable my group privacy in @BotFather, then see MAGIC.");
});

// ============================================================================
// 💬 MESSAGE HANDLER
// Now replies when:
// 1. Message starts with "terminator"
// 2. OR someone replies to THIS bot's message
// ============================================================================

bot.on("text", (ctx) => {
  if (ctx.chat.type === "private") return; // Ignore DMs here, handled elsewhere if needed

  const userId = ctx.from.id;
  const groupId = ctx.chat.id;
  const text = ctx.message.text || ctx.message.caption || "";
  if (!text) return; // ignore non-text empty messages

  const role = userId === OWNER_ID ? "owner" : "member";

  const botId = ctx.botInfo.id;
  const isReplyToMe = ctx.message.reply_to_message?.from?.id === botId;
  const startsWithTrigger = text.toLowerCase().startsWith("terminator");

  if (!startsWithTrigger && !isReplyToMe) return;

  console.log(`📩 TRIGGER RECEIVED in group from ${userId}: ${text}`);

  // Show "bot is typing..." action (catch error if it fails like lacking permissions)
  ctx.sendChatAction("typing").catch(() => {});

  // Background the AI task so the Webhook replies to Telegram INSTANTLY
  (async () => {
    if (supabase) {
      // Check Group AI Mode and Upsert Group if missing
      const { data: groupData } = await supabase.from('groups').select('ai_mode').eq('group_id', groupId).maybeSingle();
      if (groupData && groupData.ai_mode === 0) return; // Abort if AI is disabled in this group
      if (!groupData) {
        // No .catch() needed on awaited Supabase calls anymore
        await supabase.from('groups').insert([{ group_id: groupId }]);
      }

      // Upsert User (silently register them to track risk_score)
      const { data: userData } = await supabase.from('users').select('user_id').eq('user_id', userId).maybeSingle();
      if (!userData) {
        await supabase.from('users').insert([{ user_id: userId, username: ctx.from.username || "Unknown" }]);
      }
    }

    // Pass the userId into the function so it can fetch their memory and context
    const reply = await generateAIResponse(userId, text, role);
    
    if (reply) {
      // Reply to the user specifically
      await ctx.reply(reply, { 
        reply_parameters: { message_id: ctx.message.message_id } 
      }).catch(err => console.error("Reply sending error:", err.message));

      if (supabase) {
        // Log the AI reply with the group ID included
        supabase.from("bot_logs")
          .insert([{ event_type: "ai_reply", details: { user_id: userId, group_id: groupId, text } }])
          .then(({ error }) => { if (error) console.error("Supabase log error:", error.message) });
      }
    }
  })();

  // Returning immediately sends a 200 OK back to Telegram instantly
  return;
});

// ============================================================================
// 🌐 WEBHOOK SETUP
// ============================================================================

if (!RENDER_EXTERNAL_HOSTNAME) {
  console.error("❌ RENDER_EXTERNAL_HOSTNAME missing");
  process.exit(1);
}

// Using a slightly more secure path for the webhook callback
const webhookPath = `/webhook`;
const webhookUrl = `https://${RENDER_EXTERNAL_HOSTNAME}${webhookPath}`;

app.use(bot.webhookCallback(webhookPath));

bot.telegram.setWebhook(webhookUrl)
  .then(() => console.log(`✅ Webhook securely set to ${webhookUrl}`))
  .catch(err => console.error("Webhook error:", err));

app.listen(PORT, () => {
  console.log(`🚀 TERMINATOR RUNNING ON PORT ${PORT}`);
});
