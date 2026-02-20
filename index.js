// ============================================================================
// 🤖 TERMINATOR - NODE.JS VERSION (WEBHOOK MODE - DEBUG + REPLY FIXED)
// ============================================================================

import express from "express";
import { Telegraf } from "telegraf";
import { createClient } from "@supabase/supabase-js";
import { GoogleGenerativeAI } from "@google/generative-ai";

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
    model = genAI.getGenerativeModel({ model: "gemini-1.5-flash" });
    console.log("✅ Gemini model ready");
  } catch (err) {
    console.error("❌ Gemini init failed:", err.message);
  }
}

// ============================================================================
// 🧠 AI RESPONSE FUNCTION
// ============================================================================

async function generateAIResponse(text, role = "member") {
  if (!model) return null;

  const prompt = `
You are TERMINATOR, a powerful AI Telegram moderator.
User role: ${role}

Rules:
- Reply in same language
- If owner → respectful
- Else → dominant

Message:
${text}
`;

  try {
    const result = await model.generateContent(prompt);
    return result.response.text();
  } catch (err) {
    console.error("AI error:", err.message);
    return null;
  }
}

// ============================================================================
// 👤 START (DM)
// ============================================================================

bot.start(async (ctx) => {
  if (ctx.chat.type !== "private") return;

  await ctx.reply("Add me in your group with full admin rights then see MAGIC.");
});

// ============================================================================
// 💬 MESSAGE HANDLER
// Now replies when:
// 1. Message starts with "terminator"
// 2. OR someone replies to bot message
// ============================================================================

bot.on("text", async (ctx) => {
  console.log("📩 MESSAGE RECEIVED:", ctx.message.text);

  if (ctx.chat.type === "private") return;

  const userId = ctx.from.id;
  const text = ctx.message.text;
  const role = userId === OWNER_ID ? "owner" : "member";

  const isReplyToBot = ctx.message.reply_to_message &&
    ctx.message.reply_to_message.from &&
    ctx.message.reply_to_message.from.is_bot;

  const startsWithTrigger = text.toLowerCase().startsWith("terminator");

  if (!startsWithTrigger && !isReplyToBot) return;

  const reply = await generateAIResponse(text, role);
  if (reply) {
    await ctx.reply(reply);

    if (supabase) {
      await supabase.from("bot_logs").insert([
        {
          event_type: "ai_reply",
          details: { user_id: userId, text }
        }
      ]);
    }
  }
});

// ============================================================================
// 🌐 WEBHOOK SETUP
// ============================================================================

if (!RENDER_EXTERNAL_HOSTNAME) {
  console.error("❌ RENDER_EXTERNAL_HOSTNAME missing");
  process.exit(1);
}

const webhookUrl = `https://${RENDER_EXTERNAL_HOSTNAME}/`;

app.use(bot.webhookCallback("/"));

bot.telegram.setWebhook(webhookUrl)
  .then(() => console.log("✅ Webhook set to", webhookUrl))
  .catch(err => console.error("Webhook error:", err));

app.listen(PORT, () => {
  console.log(`🚀 TERMINATOR RUNNING ON PORT ${PORT}`);
});
