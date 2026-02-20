// ============================================================================
// 🤖 TERMINATOR - NODE.JS VERSION (WEBHOOK MODE - DEBUG + REPLY FIXED)
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
    // Using gemini-1.5-flash as requested and disabling strict safety filters for Terminator persona
    model = genAI.getGenerativeModel({ 
      model: "gemini-1.5-flash",
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

async function generateAIResponse(text, role = "member") {
  if (!model) return "System Offline: AI core not initialized.";

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
    console.error("AI error:", err);
    // Now prints the exact error into Telegram so we can debug it!
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

bot.on("text", async (ctx) => {
  if (ctx.chat.type === "private") return; // Ignore DMs here, handled elsewhere if needed

  const userId = ctx.from.id;
  const text = ctx.message.text;
  const role = userId === OWNER_ID ? "owner" : "member";

  // FIX: Check if the reply is specifically to THIS bot, not just any bot.
  const botId = ctx.botInfo.id;
  const isReplyToMe = ctx.message.reply_to_message?.from?.id === botId;

  const startsWithTrigger = text.toLowerCase().startsWith("terminator");

  if (!startsWithTrigger && !isReplyToMe) return;

  console.log(`📩 TRIGGER RECEIVED in group from ${userId}: ${text}`);

  // Show "bot is typing..." action
  await ctx.sendChatAction("typing");

  const reply = await generateAIResponse(text, role);
  
  if (reply) {
    // FIX: Properly quote the user's message when replying in the group
    await ctx.reply(reply, { 
      reply_parameters: { message_id: ctx.message.message_id } 
    });

    if (supabase) {
      // Don't await the logging so the bot responds faster
      supabase.from("bot_logs").insert([
        {
          event_type: "ai_reply",
          details: { user_id: userId, text }
        }
      ]).catch(err => console.error("Supabase log error:", err.message));
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
