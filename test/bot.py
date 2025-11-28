# bot.py
import os
import re
import sys
from io import BytesIO
from PIL import Image
import pytesseract
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv

# Load .env
load_dotenv()

# --- CONFIG ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    print("❌ ERROR: TELEGRAM_TOKEN missing. Add it to your .env file.")
    sys.exit(1)

# Tesseract path
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# --- SENTENCE TRANSFORMERS SETUP ---
EMBED_MODEL = None
USE_EMBEDDINGS = False
try:
    from sentence_transformers import SentenceTransformer, util
    try:
        print("⏳ Loading embedding model (all-MiniLM-L6-v2)...")
        EMBED_MODEL = SentenceTransformer('all-MiniLM-L6-v2')
        USE_EMBEDDINGS = True
        print("✅ Embedding model loaded successfully.")
    except Exception as e:
        print("⚠️ Could not load embedding model. Semantic grading disabled.")
        print("Reason:", e)
except Exception:
    print("⚠️ sentence-transformers not installed — semantic grading disabled.")
    USE_EMBEDDINGS = False


# --- QUESTION BANK ---
QUESTION_BANK = {
    "q1": {
        "id": "q1",
        "type": "short_answer",
        "text": "Explain photosynthesis in one sentence.",
        "answers": [
            "process by which plants convert light energy into chemical energy glucose",
            "plants use sunlight to make food"
        ],
        "max_score": 5,
        "partial_rules": [
            {"keyword": "sunlight", "points": 2},
            {"keyword": "glucose", "points": 2},
            {"keyword": "convert light", "points": 1}
        ]
    },
    "q2": {
        "id": "q2",
        "type": "numeric",
        "text": "What is 7 + 6?",
        "answers": ["13"],
        "max_score": 2
    }
}


# --- UTILITIES ---
def normalize_text(s):
    if not s:
        return ""
    s = str(s).lower().strip()
    s = re.sub(r'[^a-z0-9\s]', ' ', s)
    s = re.sub(r'\s+', ' ', s)
    return s


def ocr_from_image_bytes(image_bytes):
    try:
        img = Image.open(BytesIO(image_bytes)).convert("RGB")
        return pytesseract.image_to_string(img)
    except Exception as e:
        print("OCR error:", e)
        return ""


# --- GRADING ---
def grade_short_answer(student_answer, q):
    sa = normalize_text(student_answer)
    max_score = q["max_score"]
    awarded = 0
    details = []

    # 1️⃣ Keyword rules
    for r in q.get("partial_rules", []):
        kw = normalize_text(r["keyword"])
        if kw in sa:
            awarded += r["points"]
            details.append(f"🔍 Keyword matched: **{r['keyword']}** (+{r['points']})")

    # 2️⃣ Semantic similarity
    if USE_EMBEDDINGS and awarded < max_score:
        try:
            cand = q["answers"]
            embeddings = EMBED_MODEL.encode([sa] + cand, convert_to_tensor=True)
            cos = util.cos_sim(embeddings[0], embeddings[1:])
            best = float(cos.max())

            remaining = max_score - awarded

            if best > 0.75:
                add = remaining
            elif best > 0.6:
                add = round(remaining * 0.7)
            elif best > 0.45:
                add = round(remaining * 0.4)
            else:
                add = 0

            awarded += add
            details.append(f"🤖 Semantic similarity: **{best:.2f}** (+{add})")

        except Exception as e:
            print("Embedding error:", e)
            details.append("⚠️ Semantic grading failed — only keyword grading used.")
    else:
        if not USE_EMBEDDINGS:
            details.append("ℹ️ Semantic grading skipped (no embedding model).")

    awarded = min(awarded, max_score)
    return awarded, details


def grade_numeric(student_answer, q):
    try:
        numbers = re.findall(r"-?\d+\.?\d*", str(student_answer))
        if not numbers:
            return 0, ["❌ No numeric value detected"]
        s_val = float(numbers[0])
    except Exception:
        return 0, ["❌ Invalid number format"]

    correct = float(q["answers"][0])
    max_score = q["max_score"]

    if abs(s_val - correct) < 1e-9:
        return max_score, [f"✅ Exact match: **{s_val}**"]

    pct_error = abs(s_val - correct) / (abs(correct) if correct != 0 else 1)

    if pct_error <= 0.02:
        return max_score, [f"👌 Within 2% tolerance: **{s_val}**"]

    if pct_error <= 0.1:
        awarded = round(max_score * 0.7)
        return awarded, [f"⚠️ Close: **{s_val}** (error {pct_error:.1%})"]

    return 0, [f"❌ Incorrect: **{s_val}** (expected {correct})"]


def grade_submission(qid, answer):
    q = QUESTION_BANK.get(qid)
    if not q:
        return {"error": "❌ Question ID not found."}

    if q["type"] == "short_answer":
        score, details = grade_short_answer(answer, q)
    elif q["type"] == "numeric":
        score, details = grade_numeric(answer, q)
    else:
        score, details = 0, ["Unknown question type"]

    return {
        "question_id": qid,
        "max_score": q["max_score"],
        "awarded": score,
        "details": details,
    }


# --- TELEGRAM HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 **Welcome to Joshua's Auto-Grader Bot!**\n\n"
        "Here's what I can do:\n"
        "📌 Grade questions using text\n"
        "📸 Extract answers from images using OCR\n"
        "🤖 Use AI to understand your responses\n\n"
        "**Commands:**\n"
        "• `/grade q1 your answer`\n"
        "• Send an image with caption `q1`\n"
        "• Send message like `q1: answer`\n\n"
        "Let's begin! 🚀",
        parse_mode="Markdown"
    )


async def grade_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text("❗ Usage: `/grade q1 your answer`", parse_mode="Markdown")
        return

    parts = text.split()
    qid = parts[0]
    answer = " ".join(parts[1:])

    result = grade_submission(qid, answer)

    if "error" in result:
        await update.message.reply_text(result["error"])
        return

    pct = result["awarded"] / result["max_score"] * 100 if result["max_score"] else 0

    reply = (
        f"📝 **Grading Result**\n"
        f"🔢 Question: `{qid}`\n"
        f"🏆 Score: **{result['awarded']} / {result['max_score']}** ({pct:.0f}%)\n\n"
        f"📊 **Details:**\n- " + "\n- ".join(result["details"])
    )

    await update.message.reply_text(reply, parse_mode="Markdown")


async def image_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    caption = msg.caption or ""
    qid = None

    m = re.search(r"(q\d+)", caption.lower())
    if m:
        qid = m.group(1)

    if not msg.photo:
        await update.message.reply_text("❗ No photo found in the message.")
        return

    photo = msg.photo[-1]
    f = await photo.get_file()
    image_bytes = await f.download_as_bytearray()

    ocr_text = ocr_from_image_bytes(image_bytes).strip()

    # Limit displayed text to avoid huge messages
    display_text = ocr_text[:400] + ("..." if len(ocr_text) > 400 else "")

    reply = (
        "📸 **Image Received!**\n"
        "🔍 Extracting text...\n\n"
        f"📝 **Detected Text:**\n`{display_text}`"
    )

    if qid:
        result = grade_submission(qid, ocr_text)
        if "error" in result:
            reply += f"\n\n❌ {result['error']}"
        else:
            pct = result["awarded"] / result["max_score"] * 100 if result["max_score"] else 0
            reply += (
                f"\n\n🏆 **Grading Result for `{qid}`**\n"
                f"Score: **{result['awarded']} / {result['max_score']}** ({pct:.0f}%)\n"
                "Details:\n- " + "\n- ".join(result["details"])
            )
    else:
        reply += "\n\n⚠️ No question ID detected. Add caption like: `q1`"

    await update.message.reply_text(reply, parse_mode="Markdown")


async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text or ""
    m = re.match(r"(q\d+)\s*[:\-]\s*(.+)", t.lower())

    if m:
        qid, answer = m.group(1), m.group(2)
        result = grade_submission(qid, answer)
        pct = result["awarded"] / result["max_score"] * 100 if result["max_score"] else 0

        reply = (
            f"📝 `{qid}` graded!\n"
            f"🏆 **{result['awarded']} / {result['max_score']}** ({pct:.0f}%)\n\n"
            "📊 **Details:**\n- " + "\n- ".join(result["details"])
        )

        await update.message.reply_text(reply, parse_mode="Markdown")
        return

    await update.message.reply_text(
        "🤖 I didn’t understand that.\nSend something like:\n\n"
        "• `q1: your answer`\n"
        "• `/grade q1 answer`\n"
        "• Or send an image 📸"
    )


# --- MAIN ---
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("grade", grade_command))
    app.add_handler(MessageHandler(filters.PHOTO, image_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message))

    print("🤖 Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
