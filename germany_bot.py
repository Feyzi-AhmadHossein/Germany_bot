import os
import re
import html
import requests
import asyncio

from dotenv import load_dotenv
from bs4 import BeautifulSoup

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)


# ============================================================
# تنظیمات
# ============================================================

load_dotenv()

# توکن تلگرام به صورت مستقیم مقداردهی شد
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8955604982:AAHmhggb4eqeZLSjc9wchxl7yYczMPX6BHY")

if not TELEGRAM_BOT_TOKEN:
    raise ValueError(
        "TELEGRAM_BOT_TOKEN تنظیم نشده است."
    )


WIKTIONARY_API = "https://de.wiktionary.org/w/api.php"

HEADERS = {
    "User-Agent": "GermanDictionaryTelegramBot/1.0"
}


# ============================================================
# دریافت صفحه از German Wiktionary
# ============================================================

def get_wiktionary_page(word: str):
    params = {
        "action": "parse",
        "page": word,
        "prop": "wikitext",
        "format": "json",
        "formatversion": "2",
    }

    try:
        response = requests.get(
            WIKTIONARY_API,
            params=params,
            headers=HEADERS,
            timeout=15,
        )

        response.raise_for_status()

        data = response.json()

        if "error" in data:
            return None

        return data.get("parse", {}).get("wikitext")

    except Exception as e:
        print("Wiktionary error:", e)
        return None


# ============================================================
# استخراج پارامترهای یک Template از Wiktionary
# ============================================================

def extract_template(text, template_name):
    pattern = r"\{\{\s*" + re.escape(template_name) + r"\s*(.*?)\}\}"

    matches = re.findall(
        pattern,
        text,
        flags=re.DOTALL | re.IGNORECASE
    )

    if not matches:
        return None

    content = matches[0]
    result = {}

    for line in content.split("\n"):
        line = line.strip()

        if not line.startswith("|"):
            continue

        line = line[1:]

        if "=" not in line:
            continue

        key, value = line.split("=", 1)

        key = key.strip()
        value = value.strip()

        result[key] = clean_wikitext(value)

    return result


# ============================================================
# تمیز کردن Wikitext
# ============================================================

def clean_wikitext(value):

    if not value:
        return None

    value = re.sub(
        r"\[\[([^|\]]+)\|([^\]]+)\]\]",
        r"\2",
        value
    )

    value = re.sub(
        r"\[\[([^\]]+)\]\]",
        r"\1",
        value
    )

    value = re.sub(
        r"<[^>]+>",
        "",
        value
    )

    value = re.sub(
        r"\{\{[^{}]*\}\}",
        "",
        value
    )

    value = value.replace("'''", "")
    value = value.replace("''", "")

    value = html.unescape(value)

    value = re.sub(
        r"\s+",
        " ",
        value
    )

    return value.strip()


# ============================================================
# استخراج معنی فارسی
# ============================================================

def extract_persian_meaning(text):

    patterns = [
        r"===\s*معنی\s*===\s*(.*?)(?=\n===|\n==|$)",
        r"===\s*فارسی\s*===\s*(.*?)(?=\n===|\n==|$)",
        r"{{Bedeutungen}}\s*(.*?)(?=\n===|\n==|$)",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            flags=re.DOTALL | re.IGNORECASE
        )

        if match:

            result = clean_wikitext(match.group(1))

            if result:
                return result[:500]

    return None


# ============================================================
# تشخیص اسم
# ============================================================

def parse_noun(text, word):

    data = extract_template(
        text,
        "Deutsch Substantiv Übersicht"
    )

    if not data:
        return None

    genus = data.get("Genus")

    article_map = {
        "m": "der",
        "f": "die",
        "n": "das",
    }

    article = article_map.get(genus)

    singular = data.get(
        "Nominativ Singular"
    )

    plural = data.get(
        "Nominativ Plural"
    )

    return {
        "type": "noun",
        "lemma": singular or word,
        "article": article,
        "plural": plural,
        "meaning": extract_persian_meaning(text),
    }


# ============================================================
# استخراج فعل
# ============================================================

def parse_verb(text, word):

    data = extract_template(
        text,
        "Deutsch Verb Übersicht"
    )

    if not data:
        return None

    lemma = word

    praesens_ich = data.get("Präsens_ich")
    praesens_du = data.get("Präsens_du")
    praesens_er = data.get("Präsens_er, sie, es")
    praeteritum_ich = data.get("Präteritum_ich")
    partizip_II = data.get("Partizip II")
    hilfsverb = data.get("Hilfsverb")

    return {
        "type": "verb",
        "lemma": lemma,

        "praesens": {
            "ich": praesens_ich,
            "du": praesens_du,
            "er_sie_es": praesens_er,
        },

        "praeteritum": {
            "ich": praeteritum_ich,
        },

        "partizip_II": partizip_II,
        "hilfsverb": hilfsverb,
        "meaning": extract_persian_meaning(text),
    }


def is_german_verb(text):

    patterns = [
        "{{Deutsch Verb Übersicht",
        "{{de-verb",
        "{{de-conj",
        "Kategorie:Verb (Deutsch)",
    ]

    text_lower = text.lower()

    for pattern in patterns:
        if pattern.lower() in text_lower:
            return True

    return False


def is_german_noun(text):

    patterns = [
        "{{Deutsch Substantiv Übersicht",
        "{{de-noun",
        "Kategorie:Substantiv (Deutsch)",
    ]

    text_lower = text.lower()

    for pattern in patterns:
        if pattern.lower() in text_lower:
            return True

    return False


def complete_regular_present(verb_data):

    p = verb_data["praesens"]

    ich = p.get("ich")
    du = p.get("du")
    er = p.get("er_sie_es")

    if not ich:
        return p

    if ich.endswith("e"):
        stem = ich[:-1]
    else:
        stem = ich

    if not du:
        du = stem + "st"

    if not er:
        er = stem + "t"

    if not p.get("wir"):
        p["wir"] = stem + "en"

    if not p.get("ihr"):
        p["ihr"] = stem + "t"

    if not p.get("sie_Sie"):
        p["sie_Sie"] = stem + "en"

    p["du"] = du
    p["er_sie_es"] = er

    return p


def analyze_word(word):

    text = get_wiktionary_page(word)

    if not text:
        return {
            "found": False
        }

    if is_german_verb(text):
        result = parse_verb(text, word)
        if result:
            result["found"] = True
            result["praesens"] = complete_regular_present(result)
            return result

    if is_german_noun(text):
        result = parse_noun(text, word)
        if result:
            result["found"] = True
            return result

    return {
        "found": True,
        "type": "other",
        "lemma": word,
        "meaning": extract_persian_meaning(text),
    }


def format_result(data):

    if not data.get("found"):
        return (
            "❌ <b>کلمه پیدا نشد.</b>\n\n"
            "املای کلمه را بررسی کنید."
        )

    word_type = data.get("type")
    output = []

    output.append(
        f"🔎 <b>{html.escape(data.get('lemma', ''))}</b>"
    )

    if word_type == "noun":

        output.append("\n📚 نوع کلمه: <b>اسم (Nomen)</b>")

        article = data.get("article")
        if article:
            output.append(f"🔹 آرتیکل: <b>{article}</b>")
            output.append(f"➡️ <b>{article} {html.escape(data.get('lemma', ''))}</b>")

        plural = data.get("plural")
        if plural:
            output.append(f"🔢 جمع: <b>{html.escape(plural)}</b>")

        meaning = data.get("meaning")
        if meaning:
            output.append(f"🇮🇷 معنی: {html.escape(meaning)}")
        else:
            output.append("\n🇮🇷 معنی فارسی در Wiktionary پیدا نشد.")

    elif word_type == "verb":

        output.append("\n📚 نوع کلمه: <b>فعل (Verb)</b>")

        lemma = data.get("lemma")
        output.append(f"🔤 مصدر: <b>{html.escape(lemma)}</b>")

        meaning = data.get("meaning")
        if meaning:
            output.append(f"🇮🇷 معنی: {html.escape(meaning)}")

        p = data.get("praesens", {})
        if any(p.values()):
            output.append("\n🟢 <b>Präsens</b>")
            if p.get("ich"):
                output.append(f"ich {html.escape(p['ich'])}")
            if p.get("du"):
                output.append(f"du {html.escape(p['du'])}")
            if p.get("er_sie_es"):
                output.append(f"er/sie/es {html.escape(p['er_sie_es'])}")
            if p.get("wir"):
                output.append(f"wir {html.escape(p['wir'])}")
            if p.get("ihr"):
                output.append(f"ihr {html.escape(p['ihr'])}")
            if p.get("sie_Sie"):
                output.append(f"sie/Sie {html.escape(p['sie_Sie'])}")

        past = data.get("praeteritum", {})
        if past.get("ich"):
            output.append("\n🟠 <b>Präteritum</b>")
            output.append(f"ich {html.escape(past['ich'])}")

        partizip = data.get("partizip_II")
        if partizip:
            output.append(f"\n🟣 Partizip II: <b>{html.escape(partizip)}</b>")

        hilfsverb = data.get("hilfsverb")
        if hilfsverb:
            output.append(f"🕐 Hilfsverb: <b>{html.escape(hilfsverb)}</b>")

    else:

        output.append("\n📚 نوع کلمه: <b>اسم یا فعل نیست</b>")

        meaning = data.get("meaning")
        if meaning:
            output.append(f"🇮🇷 معنی: {html.escape(meaning)}")

    return "\n".join(output)


# ============================================================
# COMMAND HANDLERS
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = """
🇩🇪 <b>German Dictionary Bot</b>

یک کلمه آلمانی برای من ارسال کن.

مثال:
<code>Haus</code>
یا:
<code>gehen</code>

ربات اطلاعات موجود در German Wiktionary را بررسی می‌کند.
"""

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = """
<b>راهنمای ربات</b>

فقط کلمه آلمانی را ارسال کنید.

این ربات بدون نیاز به OpenAI و مستقیماً از Wiktionary اطلاعات را استخراج می‌کند.
"""

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML
    )


async def handle_word(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not update.message:
        return

    word = update.message.text.strip()

    if not word:
        return

    if len(word) > 100:
        await update.message.reply_text("❌ لطفاً فقط یک کلمه یا عبارت کوتاه ارسال کنید.")
        return

    waiting = await update.message.reply_text("🔍 در حال بررسی...")

    try:
        result = await asyncio.to_thread(analyze_word, word)
        answer = format_result(result)

        await waiting.edit_text(
            answer,
            parse_mode=ParseMode.HTML
        )

    except Exception as e:
        print("BOT ERROR:", e)
        await waiting.edit_text("❌ خطایی هنگام بررسی کلمه رخ داد.\nلطفاً دوباره امتحان کنید.")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    print("ERROR:", context.error)


# ============================================================
# MAIN
# ============================================================

def main():

    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_word))
    application.add_error_handler(error_handler)

    print("====================================")
    print("German Dictionary Bot Started")
    print("OpenAI: DISABLED")
    print("====================================")

    application.run_polling()


if __name__ == "__main__":
    main()
