import os
import sys
import time
import subprocess
import re
from datetime import datetime
from pathlib import Path
import telebot
from groq import Groq
from google import genai as google_genai

# ──────────────────────────────────────────────
# KONFIGURATSIYA — Railway Variables'dan olinadi
# ──────────────────────────────────────────────
BOT_TOKEN      = os.environ.get("TELEGRAM_BOT_TOKEN", "")
GROQ_API_KEY   = os.environ.get("GROQ_API_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

PROJECT_ROOT   = Path(__file__).parent.resolve()
OBSIDIAN_VAULT = PROJECT_ROOT / "content" / "Agent_Logs"
OBSIDIAN_VAULT.mkdir(parents=True, exist_ok=True)

GROQ_CONTEXT_LIMIT = 4000
GROQ_MODEL    = "llama3-70b-8192"
GEMINI_MODEL  = "gemini-2.5-flash"

# ──────────────────────────────────────────────
# BOT YARATISH + WEBHOOK O'CHIRISH
# ──────────────────────────────────────────────
bot = telebot.TeleBot(BOT_TOKEN)

def init_bot():
    """Ishga tushishdan oldin eski webhook va conflict'larni tozalaydi"""
    try:
        bot.delete_webhook(drop_pending_updates=True)
        print("✅ Webhook o'chirildi, polling rejimiga o'tildi.")
        time.sleep(2)  # Railway eski instanceni to'liq o'chirishi uchun kutamiz
    except Exception as e:
        print(f"[WEBHOOK] {e}")

# ──────────────────────────────────────────────
# GIT: GitHub'ga avtomatik push
# ──────────────────────────────────────────────
def git_push_changes(commit_message: str) -> bool:
    try:
        subprocess.run(["git", "config", "--global", "user.name",  "AI-Agent"],    check=True)
        subprocess.run(["git", "config", "--global", "user.email", "agent@ai.com"], check=True)
        subprocess.run(["git", "add", "."],                                          check=True)
        subprocess.run(["git", "commit", "-m", commit_message],                      check=True)
        subprocess.run(["git", "push", "origin", "main"],                            check=True)
        return True
    except Exception as e:
        print(f"[GIT XATO] {e}")
        return False

# ──────────────────────────────────────────────
# O'TMISHDAGI TAJRIBALARNI O'QISH
# ──────────────────────────────────────────────
def get_past_knowledge() -> str:
    knowledge = []
    try:
        notes = sorted(OBSIDIAN_VAULT.glob("*.md"))
        for note in list(notes)[-5:]:
            lines = note.read_text(encoding="utf-8").split("\n")
            title = lines[1] if len(lines) > 1 else note.name
            knowledge.append(f"- {title} (Fayl: {note.name})")
        if knowledge:
            return "\n=== O'TMISHDAGI TAJRIBALAR ===\n" + "\n".join(knowledge)
    except Exception as e:
        print(f"[OBSIDIAN O'QISH XATO] {e}")
    return ""

# ──────────────────────────────────────────────
# AI ROUTER — Groq (qisqa) / Gemini (uzun)
# YANGI: google.genai paketi ishlatiladi
# ──────────────────────────────────────────────
def _parse_response(raw: str) -> tuple[str, str]:
    raw = raw.strip()
    thinking = ""
    match = re.search(r"<thinking>(.*?)</thinking>", raw, re.DOTALL)
    if match:
        thinking = match.group(1).strip()
        raw = re.sub(r"<thinking>.*?</thinking>", "", raw, flags=re.DOTALL).strip()
    if "```" in raw:
        for part in raw.split("```"):
            stripped = part.strip().lstrip("python").strip()
            if stripped and any(kw in stripped for kw in ("import", "def ", "=")):
                return thinking, stripped
    return thinking, raw

def ask_ai(prompt: str) -> tuple[str, str, str]:
    """(model_name, thinking, clean_response) qaytaradi"""
    system = (
        "Siz avtonom AI muhandissiz. Har javobdan oldin <thinking>...</thinking> "
        "blokida fikrlang. Kod so'ralsa, faqat toza Python kodi qaytaring."
    )
    full = f"{system}\n\n{prompt}"

    try:
        if len(full) > GROQ_CONTEXT_LIMIT:
            # YANGI google.genai paketi
            client = google_genai.Client(api_key=GEMINI_API_KEY)
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=full
            )
            text = response.text or ""
            t, c = _parse_response(text)
            return GEMINI_MODEL, t, c
        else:
            client = Groq(api_key=GROQ_API_KEY)
            resp = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": full}],
                temperature=0.2,
            )
            text = resp.choices[0].message.content or ""
            t, c = _parse_response(text)
            return GROQ_MODEL, t, c
    except Exception as e:
        return "error", "", f"AI xatosi: {e}"

# ──────────────────────────────────────────────
# SUHBATDAN BILIM SAQLAB OLISH
# ──────────────────────────────────────────────
def extract_and_save_knowledge(user_msg: str, ai_reply: str):
    prompt = (
        f"Quyidagi suhbatda eslab qolishga arziydigan yangi bilim bormi?\n"
        f"Agar bor bo'lsa, Obsidian Markdown formatida yoz.\n"
        f"Agar yo'q bo'lsa, faqat 'SKIP' deb yoz.\n\n"
        f"Foydalanuvchi: {user_msg}\nAI: {ai_reply}"
    )
    _, _, decision = ask_ai(prompt)
    if "SKIP" in decision.upper():
        return
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    note_path = OBSIDIAN_VAULT / f"learned_{ts}.md"
    note_path.write_text(
        f"""---
title: "O'rganilgan bilim — {ts}"
date: {datetime.now().strftime("%Y-%m-%d")}
tags: [learned, auto-brain, telegram-chat]
---

# 🧠 Suhbatdan o'rganilgan yangi ma'lumot

{decision}

---
*Ushbu maqola AI Agent tomonidan avtomatik saqlandi.*
""",
        encoding="utf-8",
    )
    pushed = git_push_changes(f"Auto-Brain: Yangi bilim ({ts})")
    print(f"[BILIM SAQLANDI] Push: {pushed}")

# ──────────────────────────────────────────────
# TELEGRAM BUYRUQLARI
# ──────────────────────────────────────────────
@bot.message_handler(commands=["start", "help"])
def cmd_help(message):
    bot.reply_to(message, (
        "🤖 *Avtonom AI-Agent Botga xush kelibsiz!*\n\n"
        "Shunchaki xabar yozing — men o'ylab javob beraman.\n\n"
        "📌 Buyruqlar:\n"
        "/status — Server holati\n"
        "/help   — Yordam"
    ), parse_mode="Markdown")

@bot.message_handler(commands=["status"])
def cmd_status(message):
    notes = list(OBSIDIAN_VAULT.glob("*.md"))
    bot.reply_to(message, (
        f"📊 *Agent holati:*\n\n"
        f"• Saqlangan qaydlar: {len(notes)} ta\n"
        f"• Server vaqti: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"• Platforma: Railway ☁️"
    ), parse_mode="Markdown")

@bot.message_handler(func=lambda m: True)
def handle_message(message):
    user_text = message.text
    past      = get_past_knowledge()
    prompt    = f"{past}\n\nFoydalanuvchi: {user_text}"

    model, thinking, reply = ask_ai(prompt)

    if thinking:
        print(f"[THINKING — {model}]\n{thinking}\n")

    bot.reply_to(message, reply or "❌ AI javob qaytarmadi.")
    extract_and_save_knowledge(user_text, reply)

# ──────────────────────────────────────────────
# ISHGA TUSHIRISH
# ──────────────────────────────────────────────
if __name__ == "__main__":
    if not BOT_TOKEN:
        print("[XATO] TELEGRAM_BOT_TOKEN topilmadi!")
        sys.exit(1)
    init_bot()  # Webhook o'chiradi va conflict'ni hal qiladi
    print("🤖 Bot muvaffaqiyatli ishga tushdi...")
    bot.infinity_polling(allowed_updates=telebot.util.update_types)
