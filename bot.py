import os
import re
import glob
import logging
import subprocess
import threading
from datetime import datetime

import telebot
import groq
import google.generativeai as genai

# ─────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_TOKEN_HERE")
GROQ_API_KEY       = os.environ.get("GROQ_API_KEY",       "YOUR_GROQ_KEY_HERE")
GEMINI_API_KEY     = os.environ.get("GEMINI_API_KEY",     "YOUR_GEMINI_KEY_HERE")

GROQ_MODEL         = "llama3-70b-8192"
GEMINI_MODEL       = "gemini-2.5-flash"
GROQ_CONTEXT_LIMIT = 4000
KNOWLEDGE_DIR      = "content/Agent_Logs"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

os.makedirs(KNOWLEDGE_DIR, exist_ok=True)

# ─────────────────────────────────────────
# CLIENTS
# ─────────────────────────────────────────
groq_client = groq.Groq(api_key=GROQ_API_KEY)
genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel(GEMINI_MODEL)
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

# ─────────────────────────────────────────
# SEMANTIC MEMORY LOADER
# ─────────────────────────────────────────
def load_past_memory(n: int = 5) -> str:
    files = sorted(glob.glob(f"{KNOWLEDGE_DIR}/*.md"), key=os.path.getmtime, reverse=True)[:n]
    if not files:
        return ""
    memory_lines = ["=== O'TMISHDAGI TAJRIBA VA BILIMLAR BASE ==="]
    for fp in files:
        try:
            with open(fp, "r", encoding="utf-8") as f:
                content = f.read(800)
            memory_lines.append(f"--- {os.path.basename(fp)} ---\n{content}\n")
        except Exception as e:
            log.warning(f"Memory read error: {fp} — {e}")
    return "\n".join(memory_lines)

# ─────────────────────────────────────────
# HYBRID AI ROUTER
# ─────────────────────────────────────────
def call_ai(system_prompt: str, user_message: str) -> str:
    full_prompt = f"{system_prompt}\n\nUser: {user_message}"
    try:
        if len(full_prompt) > GROQ_CONTEXT_LIMIT:
            log.info("Routing to GEMINI (prompt too large)")
            response = gemini_model.generate_content(full_prompt)
            raw = response.text
        else:
            log.info("Routing to GROQ")
            resp = groq_client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_message},
                ],
                max_tokens=2048,
                temperature=0.7,
            )
            raw = resp.choices[0].message.content
    except Exception as e:
        log.error(f"AI call failed: {e}")
        raw = f"[AI xatosi: {e}]"

    # Strip <thinking> block from user-facing response
    clean = re.sub(r"<thinking>.*?</thinking>", "", raw, flags=re.DOTALL).strip()
    return clean

# ─────────────────────────────────────────
# KNOWLEDGE EXTRACTOR
# ─────────────────────────────────────────
def extract_and_save_knowledge(user_message: str, ai_response: str) -> None:
    extract_system = (
        "Siz bilim ekstraktor AI siz. "
        "Quyidagi suhbatni tahlil qiling. "
        "Agar suhbatda yangi algoritm, texnik yechim, buyruq yoki muhim dars bo'lsa, "
        "quyidagi formatda YAML Frontmatter bilan Markdown hujjat yozing:\n\n"
        "---\n"
        "title: \"<sarlavha>\"\n"
        "date: \"<sana ISO8601>\"\n"
        "tags: [learned, auto-brain, telegram-chat]\n"
        "---\n\n"
        "<Markdown tarkib>\n\n"
        "Agar suhbat shunchaki salom-alik yoki oddiy gap bo'lsa, faqat SKIP so'zini qaytaring."
    )
    extract_prompt = f"User xabari:\n{user_message}\n\nAI javobi:\n{ai_response}"
    try:
        result = call_ai(extract_system, extract_prompt)
        if result.strip().upper().startswith("SKIP"):
            log.info("Knowledge extractor: SKIP")
            return

        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"{KNOWLEDGE_DIR}/note_{timestamp}.md"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(result)
        log.info(f"Knowledge saved: {filename}")
        git_push(filename)
    except Exception as e:
        log.error(f"extract_and_save_knowledge error: {e}")

# ─────────────────────────────────────────
# GIT AUTO-PUSH
# ─────────────────────────────────────────
def git_push(filepath: str) -> None:
    try:
        subprocess.run(["git", "add", "."], check=True)
        subprocess.run(
            ["git", "commit", "-m", "Auto-Brain: Yangi bilim/evolyutsiya qo'shildi"],
            check=True
        )
        subprocess.run(["git", "push", "origin", "main"], check=True)
        log.info(f"Git push OK: {filepath}")
    except subprocess.CalledProcessError as e:
        log.warning(f"Git push failed (nothing to commit?): {e}")

# ─────────────────────────────────────────
# SELF-HEALING: AUTO PIP INSTALL
# ─────────────────────────────────────────
def self_heal(error_log: str) -> None:
    match = re.search(r"No module named '([^']+)'", error_log)
    if not match:
        return
    package = match.group(1).replace("_", "-")
    log.warning(f"Self-Healing: installing missing package '{package}'")
    try:
        subprocess.run(
            ["pip", "install", package, "--quiet"],
            check=True,
            capture_output=True,
            text=True
        )
        log.info(f"Self-Healing: '{package}' installed successfully")
    except subprocess.CalledProcessError as e:
        log.error(f"Self-Healing pip install failed: {e.stderr}")

# ─────────────────────────────────────────
# TOOL: RUN SCRIPT
# ─────────────────────────────────────────
def run_script(script_path: str) -> str:
    try:
        result = subprocess.run(
            ["python", script_path],
            capture_output=True, text=True, timeout=30
        )
        output = result.stdout + result.stderr
        if "ModuleNotFoundError" in output or "No module named" in output:
            self_heal(output)
        return output
    except subprocess.TimeoutExpired:
        return "Script timeout (30s)"
    except Exception as e:
        return f"Script run error: {e}"

# ─────────────────────────────────────────
# SYSTEM PROMPT BUILDER
# ─────────────────────────────────────────
def build_system_prompt() -> str:
    memory = load_past_memory()
    base = (
        "Siz yuqori darajali AI muhandis-assistantsiz. "
        "Har bir javobingizni MAJBURIY ravishda <thinking> ... </thinking> bloki bilan boshlang — "
        "bu blokda o'tmish bilimlarni tahlil qiling, xatolarni ko'rib chiqing va reja tuzing. "
        "Foydalanuvchiga faqat blokdan TASHQARIDAGI toza javobni ko'rsating. "
        "Agar kod yozayotgan bo'lsangiz, faqat toza Python kodi qaytaring.\n\n"
    )
    if memory:
        base += memory + "\n\n"
    return base

# ─────────────────────────────────────────
# TELEGRAM HANDLERS
# ─────────────────────────────────────────
@bot.message_handler(commands=["start", "help"])
def handle_start(message: telebot.types.Message) -> None:
    bot.reply_to(
        message,
        "🤖 *Auto-Brain Bot* faol!\n\n"
        "Men har bir suhbatdan o'rganaman va bilimlarni Quartz veb-saytga avtomatik yuklayман.\n"
        "Har qanday savol yoki vazifa yuboring.",
        parse_mode="Markdown"
    )

@bot.message_handler(commands=["run"])
def handle_run(message: telebot.types.Message) -> None:
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "Foydalanish: /run <script_path>")
        return
    script_path = parts[1].strip()
    bot.reply_to(message, f"▶️ Script ishga tushirilmoqda: `{script_path}`", parse_mode="Markdown")
    output = run_script(script_path)
    bot.reply_to(message, f"```\n{output[:3500]}\n```", parse_mode="Markdown")

@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_message(message: telebot.types.Message) -> None:
    user_text = message.text.strip()
    log.info(f"User [{message.from_user.id}]: {user_text[:80]}")

    try:
        system_prompt = build_system_prompt()
        ai_response = call_ai(system_prompt, user_text)
        bot.reply_to(message, ai_response)
        log.info(f"AI responded ({len(ai_response)} chars)")
    except Exception as e:
        log.error(f"handle_message error: {e}")
        bot.reply_to(message, f"⚠️ Xatolik yuz berdi: {e}")
        return

    # Background knowledge extraction
    threading.Thread(
        target=extract_and_save_knowledge,
        args=(user_text, ai_response),
        daemon=True
    ).start()

# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────
if __name__ == "__main__":
    log.info("Auto-Brain Bot ishga tushmoqda...")
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=30)
        except Exception as e:
            error_str = str(e)
            log.error(f"Polling error: {error_str}")
            self_heal(error_str)import os
import re
import glob
import logging
import subprocess
import threading
from datetime import datetime

import telebot
import groq
import google.generativeai as genai

# ─────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "7839612632:AAEFIoDTlynI_9AUevRZVikDLChXePqObnA")
GROQ_API_KEY       = os.environ.get("GROQ_API_KEY",       "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")
GEMINI_API_KEY     = os.environ.get("GEMINI_API_KEY",     "AIzaSyAaYyc4TeNfnh5wfPyxx7EqGhe9B8FLyfY")

GROQ_MODEL         = "llama3-70b-8192"
GEMINI_MODEL       = "gemini-2.5-flash"
GROQ_CONTEXT_LIMIT = 4000
KNOWLEDGE_DIR      = "content/Agent_Logs"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

os.makedirs(KNOWLEDGE_DIR, exist_ok=True)

# ─────────────────────────────────────────
# CLIENTS
# ─────────────────────────────────────────
groq_client = groq.Groq(api_key=GROQ_API_KEY)
genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel(GEMINI_MODEL)
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

# ─────────────────────────────────────────
# SEMANTIC MEMORY LOADER
# ─────────────────────────────────────────
def load_past_memory(n: int = 5) -> str:
    files = sorted(glob.glob(f"{KNOWLEDGE_DIR}/*.md"), key=os.path.getmtime, reverse=True)[:n]
    if not files:
        return ""
    memory_lines = ["=== O'TMISHDAGI TAJRIBA VA BILIMLAR BASE ==="]
    for fp in files:
        try:
            with open(fp, "r", encoding="utf-8") as f:
                content = f.read(800)
            memory_lines.append(f"--- {os.path.basename(fp)} ---\n{content}\n")
        except Exception as e:
            log.warning(f"Memory read error: {fp} — {e}")
    return "\n".join(memory_lines)

# ─────────────────────────────────────────
# HYBRID AI ROUTER
# ─────────────────────────────────────────
def call_ai(system_prompt: str, user_message: str) -> str:
    full_prompt = f"{system_prompt}\n\nUser: {user_message}"
    try:
        if len(full_prompt) > GROQ_CONTEXT_LIMIT:
            log.info("Routing to GEMINI (prompt too large)")
            response = gemini_model.generate_content(full_prompt)
            raw = response.text
        else:
            log.info("Routing to GROQ")
            resp = groq_client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_message},
                ],
                max_tokens=2048,
                temperature=0.7,
            )
            raw = resp.choices[0].message.content
    except Exception as e:
        log.error(f"AI call failed: {e}")
        raw = f"[AI xatosi: {e}]"

    # Strip <thinking> block from user-facing response
    clean = re.sub(r"<thinking>.*?</thinking>", "", raw, flags=re.DOTALL).strip()
    return clean

# ─────────────────────────────────────────
# KNOWLEDGE EXTRACTOR
# ─────────────────────────────────────────
def extract_and_save_knowledge(user_message: str, ai_response: str) -> None:
    extract_system = (
        "Siz bilim ekstraktor AI siz. "
        "Quyidagi suhbatni tahlil qiling. "
        "Agar suhbatda yangi algoritm, texnik yechim, buyruq yoki muhim dars bo'lsa, "
        "quyidagi formatda YAML Frontmatter bilan Markdown hujjat yozing:\n\n"
        "---\n"
        "title: \"<sarlavha>\"\n"
        "date: \"<sana ISO8601>\"\n"
        "tags: [learned, auto-brain, telegram-chat]\n"
        "---\n\n"
        "<Markdown tarkib>\n\n"
        "Agar suhbat shunchaki salom-alik yoki oddiy gap bo'lsa, faqat SKIP so'zini qaytaring."
    )
    extract_prompt = f"User xabari:\n{user_message}\n\nAI javobi:\n{ai_response}"
    try:
        result = call_ai(extract_system, extract_prompt)
        if result.strip().upper().startswith("SKIP"):
            log.info("Knowledge extractor: SKIP")
            return

        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"{KNOWLEDGE_DIR}/note_{timestamp}.md"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(result)
        log.info(f"Knowledge saved: {filename}")
        git_push(filename)
    except Exception as e:
        log.error(f"extract_and_save_knowledge error: {e}")

# ─────────────────────────────────────────
# GIT AUTO-PUSH
# ─────────────────────────────────────────
def git_push(filepath: str) -> None:
    try:
        subprocess.run(["git", "add", "."], check=True)
        subprocess.run(
            ["git", "commit", "-m", "Auto-Brain: Yangi bilim/evolyutsiya qo'shildi"],
            check=True
        )
        subprocess.run(["git", "push", "origin", "main"], check=True)
        log.info(f"Git push OK: {filepath}")
    except subprocess.CalledProcessError as e:
        log.warning(f"Git push failed (nothing to commit?): {e}")

# ─────────────────────────────────────────
# SELF-HEALING: AUTO PIP INSTALL
# ─────────────────────────────────────────
def self_heal(error_log: str) -> None:
    match = re.search(r"No module named '([^']+)'", error_log)
    if not match:
        return
    package = match.group(1).replace("_", "-")
    log.warning(f"Self-Healing: installing missing package '{package}'")
    try:
        subprocess.run(
            ["pip", "install", package, "--quiet"],
            check=True,
            capture_output=True,
            text=True
        )
        log.info(f"Self-Healing: '{package}' installed successfully")
    except subprocess.CalledProcessError as e:
        log.error(f"Self-Healing pip install failed: {e.stderr}")

# ─────────────────────────────────────────
# TOOL: RUN SCRIPT
# ─────────────────────────────────────────
def run_script(script_path: str) -> str:
    try:
        result = subprocess.run(
            ["python", script_path],
            capture_output=True, text=True, timeout=30
        )
        output = result.stdout + result.stderr
        if "ModuleNotFoundError" in output or "No module named" in output:
            self_heal(output)
        return output
    except subprocess.TimeoutExpired:
        return "Script timeout (30s)"
    except Exception as e:
        return f"Script run error: {e}"

# ─────────────────────────────────────────
# SYSTEM PROMPT BUILDER
# ─────────────────────────────────────────
def build_system_prompt() -> str:
    memory = load_past_memory()
    base = (
        "Siz yuqori darajali AI muhandis-assistantsiz. "
        "Har bir javobingizni MAJBURIY ravishda <thinking> ... </thinking> bloki bilan boshlang — "
        "bu blokda o'tmish bilimlarni tahlil qiling, xatolarni ko'rib chiqing va reja tuzing. "
        "Foydalanuvchiga faqat blokdan TASHQARIDAGI toza javobni ko'rsating. "
        "Agar kod yozayotgan bo'lsangiz, faqat toza Python kodi qaytaring.\n\n"
    )
    if memory:
        base += memory + "\n\n"
    return base

# ─────────────────────────────────────────
# TELEGRAM HANDLERS
# ─────────────────────────────────────────
@bot.message_handler(commands=["start", "help"])
def handle_start(message: telebot.types.Message) -> None:
    bot.reply_to(
        message,
        "🤖 *Auto-Brain Bot* faol!\n\n"
        "Men har bir suhbatdan o'rganaman va bilimlarni Quartz veb-saytga avtomatik yuklayман.\n"
        "Har qanday savol yoki vazifa yuboring.",
        parse_mode="Markdown"
    )

@bot.message_handler(commands=["run"])
def handle_run(message: telebot.types.Message) -> None:
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "Foydalanish: /run <script_path>")
        return
    script_path = parts[1].strip()
    bot.reply_to(message, f"▶️ Script ishga tushirilmoqda: `{script_path}`", parse_mode="Markdown")
    output = run_script(script_path)
    bot.reply_to(message, f"```\n{output[:3500]}\n```", parse_mode="Markdown")

@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_message(message: telebot.types.Message) -> None:
    user_text = message.text.strip()
    log.info(f"User [{message.from_user.id}]: {user_text[:80]}")

    try:
        system_prompt = build_system_prompt()
        ai_response = call_ai(system_prompt, user_text)
        bot.reply_to(message, ai_response)
        log.info(f"AI responded ({len(ai_response)} chars)")
    except Exception as e:
        log.error(f"handle_message error: {e}")
        bot.reply_to(message, f"⚠️ Xatolik yuz berdi: {e}")
        return

    # Background knowledge extraction
    threading.Thread(
        target=extract_and_save_knowledge,
        args=(user_text, ai_response),
        daemon=True
    ).start()

# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────
if __name__ == "__main__":
    log.info("Auto-Brain Bot ishga tushmoqda...")
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=30)
        except Exception as e:
            error_str = str(e)
            log.error(f"Polling error: {error_str}")
            self_heal(error_str)
