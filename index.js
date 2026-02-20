// ============================================================================
// 🤖 TERMINATOR - NODE.JS VERSION (WEBHOOK MODE - RENDER READY)
// Stack: Node.js + Telegraf + Supabase + Gemini (google-genai)
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

// Supabase
const supabase = SUPABASE_URL && SUPABASE_KEY
  ? createClient(SUPABASE_URL, SUPABASE_KEY)
  : null;

// Gemini
const genAI = GEMINI_API_KEY ? new GoogleGenerativeAI(GEMINI_API_KEY) : null;
const model = genAI ? genAI.getGenerativeModel({ model: "gemini-1.5-flash" }) : null;

// ============================================================================
// 🧠 AI RESPONSE FUNCTION
// ============================================================================

async function generateAIResponse(text, role = "member") {
  if (!model) return null;

  const prompt = `
You are TERMINATOR, a powerful AI Telegram moderator.
User role: ${role}

Rules:
- Reply in same language as message
- If user is owner, speak respectfully
- Otherwise speak dominant and sharp

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
// 👤 START COMMAND (DM ONLY)
// ============================================================================

bot.start(async (ctx) => {
  if (ctx.chat.type !== "private") return;

  const keyboard = {
    inline_keyboard: [[
      {
        text: "➕ ADD TO GROUP",
        url: `https://t.me/${ctx.botInfo.username}?startgroup=true`
      }
    ]]
  };

  await ctx.reply(
    "Add me in your group with full admin rights then see MAGIC.",
    { reply_markup: keyboard }
  );
});

// ============================================================================
// 💬 MESSAGE HANDLER
// ============================================================================

bot.on("text", async (ctx) => {
  if (ctx.chat.type === "private") return;

  const userId = ctx.from.id;
  const text = ctx.message.text;
  const role = userId === OWNER_ID ? "owner" : "member";

  if (!text.toLowerCase().startsWith("terminator")) return;

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
// 🌐 WEBHOOK SETUP (RENDER)
// ============================================================================

if (!RENDER_EXTERNAL_HOSTNAME) {
  console.error("❌ RENDER_EXTERNAL_HOSTNAME missing");
  process.exit(1);
}

const webhookUrl = `https://${RENDER_EXTERNAL_HOSTNAME}/`;

app.use(bot.webhookCallback("/"));

bot.telegram.setWebhook(webhookUrl)
  .then(() => console.log("✅ Webhook set"))
  .catch(err => console.error("Webhook error:", err));

app.listen(PORT, () => {
  console.log(`🚀 TERMINATOR NODE BOT RUNNING ON PORT ${PORT}`);
});
