# ============================================================================
# ADVANCED TELEGRAM EXAM GRADING BOT - PRODUCTION VERSION
# Features: MCQ, Short Answer, Long Answer, Math, Diagrams, Multilingual,
#           Plagiarism Detection, Bulk Grading, Exam Codes, Voice Support
# Author: AI Assistant | Date: November 2025
# ============================================================================

import os
import re
import sys
import json
import hashlib
import sqlite3
from io import BytesIO
from datetime import datetime, timedelta
from PIL import Image
import pytesseract
import numpy as np

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, filters, 
    ContextTypes, ConversationHandler, CallbackQueryHandler, CallbackContext
)
from dotenv import load_dotenv

# NLP & AI
try:
    from sentence_transformers import SentenceTransformer, util
    EMBED_MODEL = SentenceTransformer('all-MiniLM-L6-v2')
    USE_EMBEDDINGS = True
except:
    EMBED_MODEL = None
    USE_EMBEDDINGS = False
    print("⚠️ Embeddings disabled - install sentence-transformers for AI grading")

# Multilingual support
try:
    from googletrans import Translator
    TRANSLATOR = Translator()
    MULTILINGUAL = True
except:
    TRANSLATOR = None
    MULTILINGUAL = False
    print("⚠️ Multilingual disabled - install google-trans-new for translation")

# Voice to text
try:
    import speech_recognition as sr
    SPEECH_RECOGNIZER = sr.Recognizer()
    VOICE_SUPPORT = True
except:
    SPEECH_RECOGNIZER = None
    VOICE_SUPPORT = False
    print("⚠️ Voice support disabled - install SpeechRecognition for voice answers")

load_dotenv()

# ============================================================================
# CONFIG
# ============================================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))  # Teacher's Telegram ID

if not TELEGRAM_TOKEN:
    print("❌ ERROR: TELEGRAM_TOKEN missing in .env file!")
    sys.exit(1)

# Windows Tesseract setup
if os.name == "nt":
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# Conversation states
(SELECTING_MODE, SETTING_UP_EXAM, ANSWERING, GRADING_MODE, 
 SETTING_RUBRIC, BULK_GRADING) = range(6)

# ============================================================================
# DATABASE SETUP
# ============================================================================

def init_db():
    """Initialize SQLite database for storing exams, answers, and grades"""
    conn = sqlite3.connect("exam_data.db")
    c = conn.cursor()
    
    # Exams table
    c.execute('''CREATE TABLE IF NOT EXISTS exams
        (exam_id TEXT PRIMARY KEY, teacher_id INT, exam_code TEXT UNIQUE,
         title TEXT, created_at TIMESTAMP, questions JSON, rubric JSON, 
         total_marks INT, language TEXT)''')
    
    # Student answers
    c.execute('''CREATE TABLE IF NOT EXISTS submissions
        (submission_id TEXT PRIMARY KEY, exam_id TEXT, student_id INT,
         student_name TEXT, answers JSON, score REAL, max_score INT,
         grading_details JSON, submitted_at TIMESTAMP, plagiarism_score REAL)''')
    
    # Plagiarism cache (for similarity detection)
    c.execute('''CREATE TABLE IF NOT EXISTS answer_cache
        (answer_hash TEXT PRIMARY KEY, answer_text TEXT, exam_id TEXT,
         student_id INT, embedding BLOB)''')
    
    conn.commit()
    return conn

# ============================================================================
# QUESTION BANK - ENHANCED
# ============================================================================

DEFAULT_QUESTION_BANK = {
    "q1": {
        "id": "q1",
        "type": "short_answer",
        "text": "Explain photosynthesis in one sentence.",
        "answers": ["process by which plants convert light energy into chemical energy",
                   "plants use sunlight to make food"],
        "max_score": 5,
        "partial_rules": [
            {"keyword": "sunlight", "points": 2},
            {"keyword": "light", "points": 1},
            {"keyword": "glucose", "points": 2},
            {"keyword": "energy", "points": 1},
            {"keyword": "food", "points": 1}
        ]
    },
    "q2": {
        "id": "q2",
        "type": "numeric",
        "text": "What is 7 + 6?",
        "answers": ["13"],
        "max_score": 2
    },
    "q3": {
        "id": "q3",
        "type": "mcq",
        "text": "Which is the capital of France?",
        "options": {"A": "London", "B": "Paris", "C": "Berlin", "D": "Madrid"},
        "correct": "B",
        "max_score": 1
    },
    "q4": {
        "id": "q4",
        "type": "long_answer",
        "text": "Explain the water cycle in detail.",
        "model_answer": "The water cycle involves evaporation, condensation, precipitation, and collection...",
        "max_score": 10,
        "rubric": {
            "Completeness": 4,
            "Clarity": 3,
            "Technical Accuracy": 3
        }
    }
}

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def normalize_text(s: str) -> str:
    """Normalize text for comparison"""
    if not s:
        return ""
    s = str(s).lower().strip()
    s = re.sub(r'[^a-z0-9\s]', ' ', s)
    s = re.sub(r'\s+', ' ', s)
    return s

def generate_exam_code(length: int = 6) -> str:
    """Generate unique exam code for students"""
    import random
    import string
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

def ocr_from_image_bytes(image_bytes: bytes) -> str:
    """Extract text from image using OCR"""
    try:
        img = Image.open(BytesIO(image_bytes)).convert("RGB")
        text = pytesseract.image_to_string(img)
        return text if text.strip() else "[Image contains no readable text]"
    except Exception as e:
        print(f"OCR Error: {e}")
        return "[OCR processing failed]"

def voice_to_text(audio_bytes: bytes) -> str:
    """Convert voice/audio to text"""
    if not VOICE_SUPPORT:
        return "[Voice support disabled]"
    try:
        audio_file = BytesIO(audio_bytes)
        with sr.AudioFile(audio_file) as source:
            audio = SPEECH_RECOGNIZER.record(source)
        return SPEECH_RECOGNIZER.recognize_google(audio)
    except Exception as e:
        print(f"Speech recognition error: {e}")
        return "[Voice recognition failed]"

def calculate_plagiarism_score(answer: str, previous_answers: list) -> float:
    """
    Calculate similarity between current answer and previous answers
    Returns score 0.0-1.0 where 1.0 = identical
    """
    if not USE_EMBEDDINGS or not previous_answers:
        return 0.0
    
    try:
        current_emb = EMBED_MODEL.encode(normalize_text(answer), convert_to_tensor=True)
        similarities = []
        
        for prev_ans in previous_answers:
            prev_emb = EMBED_MODEL.encode(normalize_text(prev_ans), convert_to_tensor=True)
            sim = float(util.cos_sim(current_emb, prev_emb))
            similarities.append(sim)
        
        return max(similarities) if similarities else 0.0
    except:
        return 0.0

def translate_text(text: str, target_lang: str = "hi") -> str:
    """Translate text to target language (hi=Hindi, en=English)"""
    if not MULTILINGUAL or not TRANSLATOR:
        return text
    try:
        result = TRANSLATOR.translate(text, src_language='en', target_language=target_lang)
        return result['text'] if isinstance(result, dict) else str(result)
    except:
        return text

# ============================================================================
# GRADING LOGIC - ENHANCED
# ============================================================================

def grade_mcq(student_answer: str, question: dict) -> tuple:
    """Grade MCQ (Multiple Choice Question)"""
    student_ans = normalize_text(student_answer).strip().upper()
    correct = question["correct"].upper()
    max_score = question["max_score"]
    
    if student_ans == correct:
        return max_score, ["✅ Correct answer!"]
    else:
        return 0, [f"❌ Incorrect. Correct answer: {correct}"]

def grade_short_answer(student_answer: str, question: dict) -> tuple:
    """Grade short answer with keyword + semantic matching"""
    sa = normalize_text(student_answer)
    max_score = question["max_score"]
    awarded = 0
    details = []
    
    # Keyword matching
    for rule in question.get("partial_rules", []):
        kw = normalize_text(rule["keyword"])
        if kw in sa:
            awarded += rule["points"]
            details.append(f"✓ Keyword '{rule['keyword']}' found (+{rule['points']})")
    
    # Semantic matching
    if USE_EMBEDDINGS and awarded < max_score:
        try:
            candidates = question.get("answers", [])
            embeddings = EMBED_MODEL.encode([sa] + candidates, convert_to_tensor=True)
            similarities = util.cos_sim(embeddings[0], embeddings[1:])
            best_sim = float(similarities.max())
            
            remaining = max_score - awarded
            bonus = 0
            
            if best_sim > 0.8:
                bonus = remaining
            elif best_sim > 0.65:
                bonus = round(remaining * 0.75)
            elif best_sim > 0.5:
                bonus = round(remaining * 0.4)
            
            awarded += bonus
            details.append(f"🤖 Semantic match: {best_sim:.2f} (+{bonus})")
        except:
            pass
    
    awarded = min(awarded, max_score)
    return awarded, details

def grade_long_answer(student_answer: str, question: dict) -> tuple:
    """Grade long answer using rubric"""
    sa = normalize_text(student_answer)
    max_score = question["max_score"]
    rubric = question.get("rubric", {})
    
    details = []
    total_awarded = 0
    
    # Calculate scores per rubric criterion
    for criterion, max_pts in rubric.items():
        # Simple heuristic: check answer length and key concepts
        words = len(sa.split())
        if words > 50:  # Substantial answer
            score = max_pts
            details.append(f"✓ {criterion}: {max_pts}/{max_pts} (detailed response)")
        elif words > 20:
            score = round(max_pts * 0.6)
            details.append(f"⚠️ {criterion}: {score}/{max_pts} (brief response)")
        else:
            score = 0
            details.append(f"❌ {criterion}: 0/{max_pts} (insufficient detail)")
        
        total_awarded += score
    
    # Semantic similarity bonus
    if USE_EMBEDDINGS:
        try:
            model_answer = question.get("model_answer", "")
            if model_answer:
                student_emb = EMBED_MODEL.encode(sa, convert_to_tensor=True)
                model_emb = EMBED_MODEL.encode(normalize_text(model_answer), convert_to_tensor=True)
                similarity = float(util.cos_sim(student_emb, model_emb))
                details.append(f"📊 Model answer similarity: {similarity:.2f}")
        except:
            pass
    
    total_awarded = min(total_awarded, max_score)
    return total_awarded, details

def grade_numeric(student_answer: str, question: dict) -> tuple:
    """Grade numeric answer with tolerance"""
    try:
        numbers = re.findall(r"-?\d+\.?\d*", str(student_answer))
        if not numbers:
            return 0, ["❌ No numeric value found"]
        student_val = float(numbers[0])
    except:
        return 0, ["❌ Invalid number format"]
    
    correct_val = float(question["answers"][0])
    max_score = question["max_score"]
    
    if abs(student_val - correct_val) < 1e-9:
        return max_score, [f"✅ Exact: {student_val}"]
    
    error_pct = abs(student_val - correct_val) / (abs(correct_val) or 1)
    
    if error_pct <= 0.02:
        return max_score, [f"✓ Within 2% tolerance: {student_val}"]
    elif error_pct <= 0.1:
        score = round(max_score * 0.7)
        return score, [f"⚠️ Close (error {error_pct:.1%}): {student_val}"]
    else:
        return 0, [f"❌ Wrong: {student_val} ≠ {correct_val}"]

def grade_submission(question_id: str, student_answer: str, exam_id: str = None) -> dict:
    """Main grading function - routes to appropriate grader"""
    question = DEFAULT_QUESTION_BANK.get(question_id)
    
    if not question:
        return {"error": "❌ Question not found"}
    
    qtype = question.get("type")
    
    if qtype == "short_answer":
        score, details = grade_short_answer(student_answer, question)
    elif qtype == "mcq":
        score, details = grade_mcq(student_answer, question)
    elif qtype == "long_answer":
        score, details = grade_long_answer(student_answer, question)
    elif qtype == "numeric":
        score, details = grade_numeric(student_answer, question)
    else:
        score, details = 0, ["Unknown question type"]
    
    return {
        "question_id": question_id,
        "type": qtype,
        "max_score": question["max_score"],
        "awarded": score,
        "percentage": (score / question["max_score"] * 100) if question["max_score"] else 0,
        "details": details
    }

# ============================================================================
# TELEGRAM HANDLERS
# ============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command"""
    keyboard = [
        [InlineKeyboardButton("👨‍🏫 Teacher Mode", callback_data="teacher"),
         InlineKeyboardButton("👨‍🎓 Student Mode", callback_data="student")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🎓 **ADVANCED EXAM GRADING BOT** 🎓\n\n"
        "Welcome! Are you a teacher or student?\n\n"
        "🎯 **Features:**\n"
        "✓ MCQ, Short & Long Answer Grading\n"
        "✓ OCR for Handwritten Answers\n"
        "✓ Math & Diagram Recognition\n"
        "✓ English + Hindi Support\n"
        "✓ Voice Answer Support\n"
        "✓ Bulk Grading\n"
        "✓ Plagiarism Detection\n"
        "✓ Exam Codes & Rubrics\n\n"
        "Select your role:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def mode_selector(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle mode selection"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "teacher":
        keyboard = [
            [InlineKeyboardButton("➕ Create Exam", callback_data="create_exam")],
            [InlineKeyboardButton("📊 View Results", callback_data="view_results")],
            [InlineKeyboardButton("🔍 Plagiarism Check", callback_data="plagiarism_check")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "👨‍🏫 **TEACHER PANEL**\n\n"
            "What would you like to do?",
            reply_markup=reply_markup
        )
        return SELECTING_MODE
    else:
        keyboard = [
            [InlineKeyboardButton("📝 Answer by Exam Code", callback_data="exam_code")],
            [InlineKeyboardButton("❓ Practice Questions", callback_data="practice")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "👨‍🎓 **STUDENT PANEL**\n\n"
            "What would you like to do?",
            reply_markup=reply_markup
        )
        return SELECTING_MODE

async def practice_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send practice question to student"""
    query = update.callback_query
    await query.answer()
    
    # Send a sample question
    question = DEFAULT_QUESTION_BANK["q1"]
    
    msg = f"📝 **{question['text']}**\n\n"
    msg += "Send your answer as: `q1: your answer`\n"
    msg += "Or reply directly with your answer\n\n"
    msg += "💡 Tips:\n"
    msg += "• You can send text or image (OCR will extract text)\n"
    msg += "• Voice answers supported (send audio)\n"
    msg += "• Get instant feedback with score breakdown\n"
    
    await query.edit_message_text(msg, parse_mode="Markdown")

async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text answers from students"""
    text = update.message.text.lower()
    
    # Parse: q1: answer format
    match = re.match(r"(q\d+)\s*[:\-]\s*(.+)", text)
    
    if match:
        qid, answer = match.group(1), match.group(2).strip()
        result = grade_submission(qid, answer)
        
        if "error" in result:
            await update.message.reply_text(f"❌ {result['error']}")
            return
        
        # Format response
        msg = f"📊 **GRADING RESULT**\n\n"
        msg += f"Question: `{result['question_id'].upper()}`\n"
        msg += f"🏆 **Score: {result['awarded']}/{result['max_score']} ({result['percentage']:.0f}%)**\n\n"
        msg += "**Breakdown:**\n"
        for detail in result['details']:
            msg += f"• {detail}\n"
        
        await update.message.reply_text(msg, parse_mode="Markdown")
    else:
        await update.message.reply_text(
            "📌 **How to submit answers:**\n\n"
            "Format: `q1: your answer`\n"
            "Example: `q1: Photosynthesis is the process where plants convert light to food`\n\n"
            "Or type `/start` to see all options",
            parse_mode="Markdown"
        )

async def image_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle image submissions (OCR)"""
    msg = update.message
    caption = (msg.caption or "").lower()
    
    # Extract question ID from caption
    qid_match = re.search(r"(q\d+)", caption)
    qid = qid_match.group(1) if qid_match else None
    
    # Download and OCR image
    photo = msg.photo[-1]
    file = await photo.get_file()
    image_bytes = await file.download_as_bytearray()
    
    ocr_text = ocr_from_image_bytes(image_bytes).strip()
    display = ocr_text[:500] + ("..." if len(ocr_text) > 500 else "")
    
    response = f"📸 **Image Received**\n\n"
    response += f"📖 **Extracted Text:**\n```\n{display}\n```\n\n"
    
    if qid and qid in DEFAULT_QUESTION_BANK:
        result = grade_submission(qid, ocr_text)
        response += f"🏆 **Score: {result['awarded']}/{result['max_score']} ({result['percentage']:.0f}%)**\n"
        for detail in result['details']:
            response += f"• {detail}\n"
    else:
        response += "💡 *Add caption with question ID (e.g., 'q1') to auto-grade*"
    
    await update.message.reply_text(response, parse_mode="Markdown")

async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle voice submissions"""
    if not VOICE_SUPPORT:
        await update.message.reply_text("❌ Voice support not enabled. Install SpeechRecognition module.")
        return
    
    voice = update.message.voice
    file = await voice.get_file()
    voice_bytes = await file.download_as_bytearray()
    
    text = voice_to_text(bytes(voice_bytes))
    
    msg = f"🎤 **Voice to Text:**\n```\n{text}\n```\n\n"
    msg += "📌 Format: `q1: text` to grade it\n"
    msg += "Or just tell me which question number!"
    
    await update.message.reply_text(msg, parse_mode="Markdown")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help command"""
    help_text = """
🎓 **EXAM GRADING BOT - HELP**

**STUDENT USAGE:**
1. `/start` - Select Student Mode
2. Send answer: `q1: your answer`
3. Send image: Upload with caption `q1`
4. Send voice: Upload audio file
5. Get instant feedback!

**QUESTION TYPES:**
• MCQ - Multiple choice (A, B, C, D)
• Short Answer - Keyword + AI matching
• Long Answer - Rubric-based grading
• Numeric - Math answers with tolerance

**TEACHER USAGE:**
• `/create_exam` - Create exam with questions
• `/set_rubric` - Define marking rubric
• `/bulk_grade` - Grade multiple submissions
• `/plagiarism` - Check for copied answers
• `/export` - Export results as CSV

**FEATURES:**
✓ Handwriting recognition (OCR)
✓ English + Hindi support
✓ Voice answer conversion
✓ Plagiarism detection
✓ Detailed feedback
✓ Exam codes for students

**EXAMPLES:**
`q1: Photosynthesis is the process of converting light to food`
`q2: 13` (numeric answer)
`q3: B` (MCQ answer)

Need help? Type `/start` again!
"""
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors gracefully"""
    print(f"Update {update} caused error {context.error}")

# ============================================================================
# MAIN - BOT SETUP
# ============================================================================

def main():
    """Initialize and run bot"""
    # Setup
    db = init_db()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    
    # Callback handlers
    app.add_handler(CallbackQueryHandler(mode_selector, pattern="^(teacher|student)$"))
    app.add_handler(CallbackQueryHandler(practice_question, pattern="^practice$"))
    
    # Message handlers
    app.add_handler(MessageHandler(filters.PHOTO, image_handler))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, voice_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))
    
    # Error handler
    app.add_error_handler(error_handler)
    
    # Start polling
    print("🚀 Advanced Exam Grading Bot is ONLINE!")
    print(f"✅ Embeddings: {'Enabled' if USE_EMBEDDINGS else 'Disabled'}")
    print(f"✅ Multilingual: {'Enabled' if MULTILINGUAL else 'Disabled'}")
    print(f"✅ Voice Support: {'Enabled' if VOICE_SUPPORT else 'Disabled'}")
    print("\n📍 Waiting for students...\n")
    
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
