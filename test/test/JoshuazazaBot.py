# ============================================================================
# ADVANCED TELEGRAM EXAM GRADING BOT v2 - WITH TEACHER ACCOUNTS
# Features: Teacher Registration/Login, Dynamic Questions, Student Answers,
#           Quick Grading, Customizable Grading Scales, Proper Navigation
# Author: AI Assistant | Date: November 2025
# ============================================================================

import os
import re
import sys
import json
import hashlib
import sqlite3
import uuid
from io import BytesIO
from datetime import datetime, timedelta
from PIL import Image
import pytesseract

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, filters, 
    ContextTypes, ConversationHandler, CallbackQueryHandler, CallbackContext
)
from dotenv import load_dotenv

# Fix deprecation warning for Python 3.12+ SQLite datetime
sqlite3.register_adapter(datetime, lambda val: val.isoformat() if val else None)

# NLP & AI
try:
    from sentence_transformers import SentenceTransformer, util
    EMBED_MODEL = SentenceTransformer('all-MiniLM-L6-v2')
    USE_EMBEDDINGS = True
except:
    EMBED_MODEL = None
    USE_EMBEDDINGS = False

# Note: Multilingual support removed (Google Translate is rate-limited/paid)
# Use Telegram's built-in translation or ask for manual translation

# Voice to text
try:
    import speech_recognition as sr
    SPEECH_RECOGNIZER = sr.Recognizer()
    VOICE_SUPPORT = True
except:
    SPEECH_RECOGNIZER = None
    VOICE_SUPPORT = False

load_dotenv()

# ============================================================================
# CONFIG
# ============================================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
SUPER_ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

if not TELEGRAM_TOKEN:
    print("❌ ERROR: TELEGRAM_TOKEN missing in .env file!")
    sys.exit(1)

if os.name == "nt":
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# Conversation states
(START, TEACHER_LOGIN, TEACHER_REGISTER, TEACHER_MENU, CREATE_QUESTION,
 STUDENT_MAIN, FIND_ASSIGNMENT, ANSWER_SUBMISSION, QUICK_GRADE_MENU,
 QUICK_GRADE_SETUP, QUICK_GRADE_ANSWER, TEACHER_DASHBOARD) = range(12)

# ============================================================================
# DATABASE SETUP - ENHANCED WITH TEACHER ACCOUNTS
# ============================================================================

def init_db():
    """Initialize SQLite database with teacher accounts"""
    conn = sqlite3.connect("exam_data.db")
    c = conn.cursor()
    
    # Teachers table
    c.execute('''CREATE TABLE IF NOT EXISTS teachers
        (teacher_id INTEGER PRIMARY KEY, telegram_id INT UNIQUE, username TEXT UNIQUE,
         password TEXT, full_name TEXT, created_at TIMESTAMP, grading_scale INT DEFAULT 100)''')
    
    # Questions/Assignments table
    c.execute('''CREATE TABLE IF NOT EXISTS assignments
        (assignment_id TEXT PRIMARY KEY, teacher_id INT, code TEXT UNIQUE,
         title TEXT, question TEXT, question_type TEXT, 
         max_score INT, grading_scale INT, created_at TIMESTAMP, 
         answers JSON, rubric JSON, FOREIGN KEY(teacher_id) REFERENCES teachers(teacher_id))''')
    
    # Student submissions
    c.execute('''CREATE TABLE IF NOT EXISTS submissions
        (submission_id TEXT PRIMARY KEY, assignment_id TEXT, student_name TEXT,
         student_id INT, answer TEXT, score REAL, max_score INT,
         grading_details JSON, submitted_at TIMESTAMP,
         FOREIGN KEY(assignment_id) REFERENCES assignments(assignment_id))''')
    
    # Quick grading cache
    c.execute('''CREATE TABLE IF NOT EXISTS quick_grades
        (grade_id TEXT PRIMARY KEY, teacher_id INT, question TEXT,
         answer_given TEXT, score REAL, max_score INT,
         graded_at TIMESTAMP, FOREIGN KEY(teacher_id) REFERENCES teachers(teacher_id))''')
    
    conn.commit()
    return conn

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def hash_password(password):
    """Hash password for security"""
    return hashlib.sha256(password.encode()).hexdigest()

def generate_assignment_code():
    """Generate unique assignment code"""
    return str(uuid.uuid4())[:8].upper()

def register_teacher(telegram_id, username, password, full_name, grading_scale=100):
    """Register new teacher"""
    conn = sqlite3.connect("exam_data.db")
    c = conn.cursor()
    
    try:
        hashed_pass = hash_password(password)
        c.execute('''INSERT INTO teachers (telegram_id, username, password, full_name, grading_scale, created_at)
                     VALUES (?, ?, ?, ?, ?, ?)''',
                  (telegram_id, username, hashed_pass, full_name, grading_scale, datetime.now()))
        conn.commit()
        teacher_id = c.lastrowid
        return True, teacher_id
    except sqlite3.IntegrityError:
        return False, None
    finally:
        conn.close()

def login_teacher(username, password):
    """Login teacher and return teacher_id"""
    conn = sqlite3.connect("exam_data.db")
    c = conn.cursor()
    
    hashed_pass = hash_password(password)
    c.execute("SELECT teacher_id, full_name FROM teachers WHERE username=? AND password=?",
              (username, hashed_pass))
    result = c.fetchone()
    conn.close()
    
    return result if result else (None, None)

def teacher_exists_by_telegram(telegram_id):
    """Check if teacher account exists"""
    conn = sqlite3.connect("exam_data.db")
    c = conn.cursor()
    c.execute("SELECT teacher_id, full_name FROM teachers WHERE telegram_id=?", (telegram_id,))
    result = c.fetchone()
    conn.close()
    return result

def normalize_text(s):
    """Normalize text"""
    if not s:
        return ""
    s = str(s).lower().strip()
    s = re.sub(r'[^a-z0-9\s]', ' ', s)
    s = re.sub(r'\s+', ' ', s)
    return s

def ocr_from_image_bytes(image_bytes):
    """Extract text from image"""
    try:
        img = Image.open(BytesIO(image_bytes)).convert("RGB")
        return pytesseract.image_to_string(img)
    except:
        return "[OCR failed]"

# ============================================================================
# GRADING FUNCTIONS
# ============================================================================

def grade_answer(student_answer, expected_answer, max_score, question_type="short"):
    """Grade student answer"""
    sa = normalize_text(student_answer)
    ea = normalize_text(expected_answer)
    
    if question_type == "exact":
        score = max_score if sa == ea else 0
        detail = "✅ Exact match!" if score == max_score else "❌ Incorrect"
    
    elif question_type == "keyword":
        score = 0
        details = []
        keywords = ea.split()
        matched = sum(1 for kw in keywords if kw in sa)
        score = int((matched / len(keywords) * max_score)) if keywords else 0
        detail = f"Matched {matched}/{len(keywords)} keywords"
    
    elif question_type == "semantic" and USE_EMBEDDINGS:
        try:
            student_emb = EMBED_MODEL.encode(sa, convert_to_tensor=True)
            expected_emb = EMBED_MODEL.encode(ea, convert_to_tensor=True)
            similarity = float(util.cos_sim(student_emb, expected_emb))
            if similarity > 0.8:
                score = max_score
            elif similarity > 0.6:
                score = int(max_score * 0.7)
            elif similarity > 0.4:
                score = int(max_score * 0.4)
            else:
                score = 0
            detail = f"Semantic match: {similarity:.2f}"
        except:
            score = 0
            detail = "AI grading failed"
    
    else:
        score = 0
        detail = "Manual grading needed"
    
    return score, detail

# ============================================================================
# TELEGRAM HANDLERS - MAIN FLOW
# ============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command - Check if teacher exists"""
    user_id = update.message.from_user.id
    user_name = update.message.from_user.first_name
    
    # Check if already logged in
    if 'teacher_id' in context.user_data:
        await show_teacher_menu(update, context)
        return TEACHER_MENU
    
    # Check if teacher account exists
    teacher_info = teacher_exists_by_telegram(user_id)
    
    keyboard = [
        [InlineKeyboardButton("👨‍🏫 Teacher Account", callback_data="teacher_mode")],
        [InlineKeyboardButton("👨‍🎓 Student", callback_data="student_mode")]
    ]
    
    if teacher_info:
        keyboard[0][0] = InlineKeyboardButton("👨‍🏫 Login to Teacher Account", callback_data="teacher_login")
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"👋 Welcome {user_name}!\n\n"
        "🎓 **Smart Exam & Assignment System**\n\n"
        "Choose your role:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )
    
    return START

# ============================================================================
# TEACHER AUTHENTICATION HANDLERS
# ============================================================================

async def teacher_mode_selector(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Teacher: Register or Login"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    teacher_info = teacher_exists_by_telegram(user_id)
    
    if teacher_info:
        # Account exists - show login
        teacher_name = teacher_info[1]
        keyboard = [
            [InlineKeyboardButton("🔐 Login Now", callback_data="proceed_login")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_to_start")]
        ]
        await query.edit_message_text(
            f"👨‍🏫 **WELCOME BACK!**\n\n"
            f"We found your existing account:\n"
            f"**{teacher_name}**\n\n"
            f"Click below to login.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    else:
        # No account - show register
        keyboard = [
            [InlineKeyboardButton("📝 Create Teacher Account", callback_data="proceed_register")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_to_start")]
        ]
        await query.edit_message_text(
            "👨‍🏫 **NO ACCOUNT FOUND**\n\n"
            "Create a new teacher account — takes 1 minute!\n\n"
            "You'll be able to:\n"
            "✅ Create assignments\n"
            "✅ Manage students\n"
            "✅ Grade answers automatically\n"
            "✅ View analytics",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    
    return TEACHER_LOGIN

async def proceed_teacher_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Login flow"""
    query = update.callback_query
    await query.answer()
    
    context.user_data['auth_step'] = 'username'
    
    keyboard = [[InlineKeyboardButton("🔙 Cancel", callback_data="back_to_start")]]
    await query.edit_message_text(
        "🔐 **LOGIN**\n\n"
        "Send your username:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return TEACHER_LOGIN

async def proceed_teacher_register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Register flow"""
    query = update.callback_query
    await query.answer()
    
    context.user_data['auth_step'] = 'register_name'
    
    keyboard = [[InlineKeyboardButton("🔙 Cancel", callback_data="back_to_start")]]
    await query.edit_message_text(
        "📝 **CREATE ACCOUNT**\n\n"
        "Step 1: Enter your full name",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return TEACHER_REGISTER

async def handle_teacher_auth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle authentication text input"""
    user_id = update.message.from_user.id
    text = update.message.text.strip()
    
    auth_step = context.user_data.get('auth_step')
    
    # If no auth step is set, ask user to click a button first
    if not auth_step:
        await update.message.reply_text(
            "❌ Please click a button first (Login or Register)\n\n"
            "Type /start to begin"
        )
        return TEACHER_LOGIN
    
    # LOGIN FLOW
    if auth_step == 'username':
        context.user_data['login_username'] = text
        await update.message.reply_text("🔐 Now send your password:")
        context.user_data['auth_step'] = 'password'
        return TEACHER_LOGIN
    
    elif auth_step == 'password':
        username = context.user_data.get('login_username')
        password = text
        
        teacher_id, full_name = login_teacher(username, password)
        
        if teacher_id:
            context.user_data['teacher_id'] = teacher_id
            context.user_data['full_name'] = full_name
            await update.message.reply_text(
                f"✅ Welcome back, {full_name}!\n\n"
                "Loading teacher menu..."
            )
            await show_teacher_menu(update, context)
            return TEACHER_MENU
        else:
            await update.message.reply_text(
                "❌ Invalid username or password.\n\n"
                "Try again or /start to go back"
            )
            return TEACHER_LOGIN
    
    # REGISTER FLOW
    elif auth_step == 'register_name':
        context.user_data['reg_name'] = text
        await update.message.reply_text("Step 2: Choose a username (for login)")
        context.user_data['auth_step'] = 'register_username'
        return TEACHER_REGISTER
    
    elif auth_step == 'register_username':
        context.user_data['reg_username'] = text
        await update.message.reply_text("Step 3: Create a password")
        context.user_data['auth_step'] = 'register_password'
        return TEACHER_REGISTER
    
    elif auth_step == 'register_password':
        context.user_data['reg_password'] = text
        await update.message.reply_text("Step 4: Choose your grading scale (e.g., 5, 10, 20, 30, 100)")
        context.user_data['auth_step'] = 'register_scale'
        return TEACHER_REGISTER
    
    elif auth_step == 'register_scale':
        try:
            scale = int(text)
            if scale < 1 or scale > 100:
                await update.message.reply_text("❌ Scale must be between 1-100")
                return TEACHER_REGISTER
            
            # Register teacher
            success, teacher_id = register_teacher(
                user_id,
                context.user_data['reg_username'],
                context.user_data['reg_password'],
                context.user_data['reg_name'],
                scale
            )
            
            if success:
                context.user_data['teacher_id'] = teacher_id
                context.user_data['full_name'] = context.user_data['reg_name']
                await update.message.reply_text(
                    f"✅ Account created successfully!\n\n"
                    f"Name: {context.user_data['reg_name']}\n"
                    f"Username: {context.user_data['reg_username']}\n"
                    f"Grading Scale: 0-{scale}\n\n"
                    "Loading teacher menu..."
                )
                await show_teacher_menu(update, context)
                return TEACHER_MENU
            else:
                await update.message.reply_text(
                    "❌ Username already taken. Try another.\n\n"
                    "/start to begin again"
                )
                return TEACHER_REGISTER
        except ValueError:
            await update.message.reply_text("❌ Please enter a valid number")
            return TEACHER_REGISTER

# ============================================================================
# TEACHER MENU & FEATURES
# ============================================================================

async def show_teacher_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show teacher main menu"""
    teacher_id = context.user_data.get('teacher_id')
    full_name = context.user_data.get('full_name', 'Teacher')
    
    keyboard = [
        [InlineKeyboardButton("➕ Create Assignment", callback_data="create_assignment")],
        [InlineKeyboardButton("📋 My Assignments", callback_data="my_assignments")],
        [InlineKeyboardButton("⚡ Quick Grade", callback_data="quick_grade")],
        [InlineKeyboardButton("📊 Results & Analytics", callback_data="view_results")],
        [InlineKeyboardButton("🚪 Logout", callback_data="logout")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if isinstance(update, Update) and update.callback_query:
        await update.callback_query.edit_message_text(
            f"👨‍🏫 **TEACHER DASHBOARD**\n\n"
            f"Welcome back, {full_name}! 👋\n\n"
            "What would you like to do?",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            f"👨‍🏫 **TEACHER DASHBOARD**\n\n"
            f"Welcome back, {full_name}! 👋\n\n"
            "What would you like to do?",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
    
    return TEACHER_MENU

async def create_assignment_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start creating assignment"""
    query = update.callback_query
    await query.answer()
    
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="teacher_menu")]]
    await query.edit_message_text(
        "📝 **CREATE NEW ASSIGNMENT**\n\n"
        "Step 1: Enter assignment title",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    
    context.user_data['assign_step'] = 'title'
    return CREATE_QUESTION

async def handle_assignment_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle assignment creation text input"""
    text = update.message.text.strip()
    assign_step = context.user_data.get('assign_step')
    teacher_id = context.user_data.get('teacher_id')
    
    if assign_step == 'title':
        context.user_data['assign_title'] = text
        await update.message.reply_text(
            "Step 2: Enter the question/assignment text"
        )
        context.user_data['assign_step'] = 'question'
        return CREATE_QUESTION
    
    elif assign_step == 'question':
        context.user_data['assign_question'] = text
        keyboard = [
            [InlineKeyboardButton("✏️ Short Answer", callback_data="type_short")],
            [InlineKeyboardButton("🎯 Exact Match", callback_data="type_exact")],
            [InlineKeyboardButton("🧠 Keyword Based", callback_data="type_keyword")],
            [InlineKeyboardButton("🤖 AI Semantic", callback_data="type_semantic")],
        ]
        await update.message.reply_text(
            "Step 3: Choose question type",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return CREATE_QUESTION
    
    elif assign_step == 'type_selected':
        context.user_data['assign_answer'] = text
        await update.message.reply_text(
            "Step 4: Enter the correct answer(s)"
        )
        context.user_data['assign_step'] = 'max_score'
        return CREATE_QUESTION
    
    elif assign_step == 'max_score':
        try:
            max_score = int(text)
            
            # Get teacher's grading scale
            conn = sqlite3.connect("exam_data.db")
            c = conn.cursor()
            c.execute("SELECT grading_scale FROM teachers WHERE teacher_id=?", (teacher_id,))
            scale = c.fetchone()[0]
            conn.close()
            
            # Create assignment
            assignment_id = str(uuid.uuid4())
            code = generate_assignment_code()
            
            conn = sqlite3.connect("exam_data.db")
            c = conn.cursor()
            c.execute('''INSERT INTO assignments 
                        (assignment_id, teacher_id, code, title, question, 
                         question_type, max_score, grading_scale, created_at, answers)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                      (assignment_id, teacher_id, code, context.user_data['assign_title'],
                       context.user_data['assign_question'], context.user_data['assign_type'],
                       max_score, scale, datetime.now(), context.user_data['assign_answer']))
            conn.commit()
            conn.close()
            
            keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="teacher_menu")]]
            
            await update.message.reply_text(
                f"✅ **ASSIGNMENT CREATED!**\n\n"
                f"📌 **Title:** {context.user_data['assign_title']}\n"
                f"🔑 **Assignment Code:** `{code}`\n"
                f"📊 **Max Score:** {max_score}/{scale}\n"
                f"❓ **Question Type:** {context.user_data['assign_type']}\n\n"
                f"Share the code with students so they can access this assignment!",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
            
            context.user_data['assign_step'] = None
            return TEACHER_MENU
        except ValueError:
            await update.message.reply_text("❌ Please enter a valid number for max score")
            return CREATE_QUESTION

async def handle_assignment_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle assignment type selection"""
    query = update.callback_query
    await query.answer()
    
    type_map = {
        'type_short': 'Short Answer',
        'type_exact': 'Exact Match',
        'type_keyword': 'Keyword Based',
        'type_semantic': 'AI Semantic'
    }
    
    assign_type = type_map.get(query.data, 'Short Answer')
    context.user_data['assign_type'] = assign_type
    context.user_data['assign_step'] = 'type_selected'
    
    await query.edit_message_text(
        f"✅ Question type: **{assign_type}**\n\n"
        f"Now send the correct answer(s):",
        parse_mode="Markdown"
    )
    
    return CREATE_QUESTION

# ============================================================================
# STUDENT HANDLERS
# ============================================================================

async def student_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Student main menu"""
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("🔍 Find Assignment", callback_data="find_assignment")],
        [InlineKeyboardButton("⚡ Quick Grade", callback_data="quick_grade_student")],
        [InlineKeyboardButton("🔙 Back", callback_data="back_to_start")]
    ]
    
    await query.edit_message_text(
        "👨‍🎓 **STUDENT PORTAL**\n\n"
        "What would you like to do?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    
    return STUDENT_MAIN

async def find_assignment_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Find assignment by code"""
    query = update.callback_query
    await query.answer()
    
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="student_menu")]]
    
    await query.edit_message_text(
        "🔍 **FIND ASSIGNMENT**\n\n"
        "Enter the assignment code (given by your teacher):",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    
    context.user_data['mode'] = 'find_assignment'
    return FIND_ASSIGNMENT

async def handle_assignment_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle student entering assignment code"""
    code = update.message.text.strip().upper()
    
    # Find assignment
    conn = sqlite3.connect("exam_data.db")
    c = conn.cursor()
    c.execute('''SELECT assignment_id, title, question, question_type, max_score, grading_scale, answers
                 FROM assignments WHERE code=?''', (code,))
    result = c.fetchone()
    conn.close()
    
    if result:
        assignment_id, title, question, qtype, max_score, scale, answers = result
        context.user_data['current_assignment_id'] = assignment_id
        context.user_data['current_assignment_code'] = code
        context.user_data['current_max_score'] = max_score
        context.user_data['current_scale'] = scale
        context.user_data['current_qtype'] = qtype
        context.user_data['correct_answers'] = answers
        
        keyboard = [
            [InlineKeyboardButton("📝 Submit Answer", callback_data="submit_answer")],
            [InlineKeyboardButton("🔙 Back", callback_data="student_menu")]
        ]
        
        await update.message.reply_text(
            f"✅ **ASSIGNMENT FOUND!**\n\n"
            f"📌 **Title:** {title}\n"
            f"❓ **Question:** {question}\n"
            f"📊 **Max Score:** {max_score}/{scale}\n\n"
            f"Ready to answer?",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        
        return FIND_ASSIGNMENT
    else:
        await update.message.reply_text(
            "❌ Assignment code not found.\n\n"
            "Please check the code and try again. /start to go back"
        )
        return FIND_ASSIGNMENT

async def submit_answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Student submits answer"""
    query = update.callback_query
    await query.answer()
    
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="student_menu")]]
    
    await query.edit_message_text(
        "📝 **SUBMIT YOUR ANSWER**\n\n"
        "Type your answer:\n"
        "(You can also send images or voice notes)",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    
    context.user_data['mode'] = 'submit_answer'
    return ANSWER_SUBMISSION

async def process_student_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process student answer submission"""
    student_id = update.message.from_user.id
    student_name = update.message.from_user.first_name
    
    # Get answer
    if update.message.text:
        answer = update.message.text
    else:
        await update.message.reply_text("❌ Please send a text answer")
        return ANSWER_SUBMISSION
    
    # Grade answer
    assignment_id = context.user_data['current_assignment_id']
    max_score = context.user_data['current_max_score']
    qtype = context.user_data['current_qtype']
    correct_answers = context.user_data.get('correct_answers', answer)
    
    score, detail = grade_answer(answer, correct_answers, max_score, qtype.lower().replace(' ', ''))
    
    # Save submission
    submission_id = str(uuid.uuid4())
    conn = sqlite3.connect("exam_data.db")
    c = conn.cursor()
    c.execute('''INSERT INTO submissions
                (submission_id, assignment_id, student_name, student_id, answer, score, max_score, submitted_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
              (submission_id, assignment_id, student_name, student_id, answer, score, max_score, datetime.now()))
    conn.commit()
    conn.close()
    
    keyboard = [[InlineKeyboardButton("🔍 Find Another", callback_data="find_assignment")],
                [InlineKeyboardButton("🏠 Back to Menu", callback_data="student_menu")]]
    
    await update.message.reply_text(
        f"✅ **ANSWER SUBMITTED!**\n\n"
        f"📊 **Your Score:** {score}/{max_score}\n"
        f"📈 **Percentage:** {(score/max_score*100):.1f}%\n"
        f"💡 **Feedback:** {detail}\n\n"
        f"What's next?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    
    return STUDENT_MAIN

# ============================================================================
# QUICK GRADE (FOR ANYONE)
# ============================================================================

async def quick_grade_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Quick grade entry point"""
    query = update.callback_query
    await query.answer()
    
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="back_to_start")]]
    
    await query.edit_message_text(
        "⚡ **QUICK GRADE**\n\n"
        "This is quick grading for anyone (teachers/students)\n\n"
        "Step 1: Enter the question",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    
    context.user_data['quick_grade_step'] = 'question'
    return QUICK_GRADE_MENU

async def handle_quick_grade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle quick grading input"""
    text = update.message.text.strip()
    step = context.user_data.get('quick_grade_step')
    
    if step == 'question':
        context.user_data['qg_question'] = text
        await update.message.reply_text(
            "Step 2: Enter the correct answer(s)"
        )
        context.user_data['quick_grade_step'] = 'correct_answer'
        return QUICK_GRADE_MENU
    
    elif step == 'correct_answer':
        context.user_data['qg_correct'] = text
        await update.message.reply_text(
            "Step 3: Enter the student's answer"
        )
        context.user_data['quick_grade_step'] = 'student_answer'
        return QUICK_GRADE_MENU
    
    elif step == 'student_answer':
        context.user_data['qg_student_answer'] = text
        await update.message.reply_text(
            "Step 4: Enter max score (e.g., 5, 10, 20, 100)"
        )
        context.user_data['quick_grade_step'] = 'max_score'
        return QUICK_GRADE_MENU
    
    elif step == 'max_score':
        try:
            max_score = int(text)
            
            # Grade it
            score, detail = grade_answer(
                context.user_data['qg_student_answer'],
                context.user_data['qg_correct'],
                max_score,
                'keyword'
            )
            
            keyboard = [
                [InlineKeyboardButton("⚡ Grade Another", callback_data="quick_grade")],
                [InlineKeyboardButton("🏠 Back", callback_data="back_to_start")]
            ]
            
            await update.message.reply_text(
                f"✅ **GRADING RESULT**\n\n"
                f"❓ **Question:** {context.user_data['qg_question']}\n"
                f"📝 **Student Answer:** {context.user_data['qg_student_answer']}\n"
                f"✏️ **Correct Answer:** {context.user_data['qg_correct']}\n\n"
                f"🏆 **Score:** {score}/{max_score}\n"
                f"📊 **Percentage:** {(score/max_score*100):.1f}%\n"
                f"💡 **Detail:** {detail}",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
            
            # Save to quick grades
            teacher_id = context.user_data.get('teacher_id')
            if teacher_id:
                conn = sqlite3.connect("exam_data.db")
                c = conn.cursor()
                c.execute('''INSERT INTO quick_grades
                            (grade_id, teacher_id, question, answer_given, score, max_score, graded_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?)''',
                          (str(uuid.uuid4()), teacher_id, context.user_data['qg_question'],
                           context.user_data['qg_student_answer'], score, max_score, datetime.now()))
                conn.commit()
                conn.close()
            
            context.user_data['quick_grade_step'] = None
            return QUICK_GRADE_MENU
            
        except ValueError:
            await update.message.reply_text("❌ Please enter a valid number")
            return QUICK_GRADE_MENU

# ============================================================================
# NAVIGATION HANDLERS
# ============================================================================

async def back_to_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Go back to start"""
    query = update.callback_query
    await query.answer()
    
    # Clear user data
    context.user_data.clear()
    
    await query.edit_message_text(
        "👋 **Back to Start**\n\n"
        "Type /start to begin again"
    )
    return START

async def back_to_teacher_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Back to teacher menu"""
    query = update.callback_query
    await query.answer()
    await show_teacher_menu(update, context)
    return TEACHER_MENU

async def back_to_student_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Back to student menu"""
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("🔍 Find Assignment", callback_data="find_assignment")],
        [InlineKeyboardButton("⚡ Quick Grade", callback_data="quick_grade_student")],
        [InlineKeyboardButton("🔙 Back", callback_data="back_to_start")]
    ]
    
    await query.edit_message_text(
        "👨‍🎓 **STUDENT PORTAL**\n\n"
        "What would you like to do?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    
    return STUDENT_MAIN

async def logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Logout teacher"""
    query = update.callback_query
    await query.answer()
    
    context.user_data.clear()
    
    await query.edit_message_text(
        "👋 **Logged out successfully!**\n\n"
        "Type /start to login again"
    )
    return START

# ============================================================================
# ERROR HANDLER
# ============================================================================
# HELP COMMAND HANDLER
# ============================================================================

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show comprehensive help for all user types"""
    
    help_text = """
🤖 **EXAM GRADING BOT - HELP GUIDE**

═══════════════════════════════════════════════════════════════

📚 **FOR TEACHERS** (Create & Grade Assignments)

✅ **Getting Started:**
1. Click /start
2. Select "Teacher Mode"
3. Register with your name, email, and password
4. Your account is ready!

✅ **Create Assignment:**
1. Tap "Create Assignment" from menu
2. Enter assignment name & instructions
3. Choose question type:
   • **Exact Match**: Answer must match exactly
   • **Keyword Match**: Answer must contain keywords
   • **Semantic**: AI checks meaning (flexible)
   • **Manual Grade**: You grade manually later
4. Add your question(s) and correct answer(s)
5. Get a **unique 8-character code** to share

✅ **Grade Student Answers:**
1. Tap "View Results"
2. See all student submissions
3. For manual questions, enter a grade/score
4. All responses are saved

✅ **View Dashboard:**
• Tap "Dashboard" to see all your assignments
• Check student count and status
• Track grading progress

⏱️ **Logout When Done:**
Tap "Logout" to securely exit

═══════════════════════════════════════════════════════════════

👨‍🎓 **FOR STUDENTS** (Answer & Get Grades)

✅ **Answer Assignment:**
1. Click /start
2. Select "Student Mode"
3. Tap "Find Assignment"
4. Enter the **8-character code** from your teacher
5. Answer the questions
6. Submit and wait for your grade!

✅ **View Your Grade:**
• You'll see your grade immediately (if auto-graded)
• Or wait for teacher to grade manually
• You can try again with a new code

═══════════════════════════════════════════════════════════════

⚡ **QUICK GRADING** (No Login Needed!)

✅ **Use Quick Grade Button:**
1. Tap "Quick Grade" from main menu
2. Select grading method (Exact/Keyword/Semantic)
3. Enter question & correct answer
4. Submit text to grade
5. Get instant result!

✅ **Perfect For:**
• Testing single answers quickly
• Grading without creating assignments
• Demos & presentations
• One-off assessments

═══════════════════════════════════════════════════════════════

🎮 **AVAILABLE COMMANDS**

/start      - Restart the bot
/help       - Show this help message
/logout     - Logout from your account (Teachers)

═══════════════════════════════════════════════════════════════

❓ **TROUBLESHOOTING**

**Can't find assignment?**
→ Ask your teacher for the correct 8-character code
→ Check that you copied it exactly

**Grade not showing?**
→ Check if assignment uses manual grading
→ Teacher may not have graded yet

**Forgot password?**
→ Use /start and register with a new account

**Code doesn't work?**
→ Code might be inactive
→ Contact your teacher

═══════════════════════════════════════════════════════════════

Need more help? Contact your teacher or bot administrator.
"""
    
    await update.message.reply_text(help_text, parse_mode='Markdown')

# ============================================================================

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors gracefully"""
    print(f"Error: {context.error}")

# ============================================================================
# MAIN - BOT SETUP
# ============================================================================

def main():
    """Initialize and run bot"""
    db = init_db()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    # Main conversation handler
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            START: [
                CallbackQueryHandler(teacher_mode_selector, pattern="^teacher_mode$"),
                CallbackQueryHandler(student_mode, pattern="^student_mode$"),
            ],
            TEACHER_LOGIN: [
                CallbackQueryHandler(proceed_teacher_login, pattern="^proceed_login$"),
                CallbackQueryHandler(proceed_teacher_register, pattern="^proceed_register$"),
                CallbackQueryHandler(back_to_start, pattern="^back_to_start$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_teacher_auth),
            ],
            TEACHER_REGISTER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_teacher_auth),
            ],
            TEACHER_MENU: [
                CallbackQueryHandler(create_assignment_start, pattern="^create_assignment$"),
                CallbackQueryHandler(quick_grade_start, pattern="^quick_grade$"),
                CallbackQueryHandler(logout, pattern="^logout$"),
                CallbackQueryHandler(back_to_teacher_menu, pattern="^teacher_menu$"),
            ],
            CREATE_QUESTION: [
                CallbackQueryHandler(handle_assignment_type, pattern="^type_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_assignment_creation),
            ],
            STUDENT_MAIN: [
                CallbackQueryHandler(find_assignment_start, pattern="^find_assignment$"),
                CallbackQueryHandler(quick_grade_start, pattern="^quick_grade_student$"),
                CallbackQueryHandler(back_to_student_menu, pattern="^student_menu$"),
                CallbackQueryHandler(back_to_start, pattern="^back_to_start$"),
            ],
            FIND_ASSIGNMENT: [
                CallbackQueryHandler(submit_answer_handler, pattern="^submit_answer$"),
                CallbackQueryHandler(back_to_student_menu, pattern="^student_menu$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_assignment_code),
            ],
            ANSWER_SUBMISSION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_student_answer),
            ],
            QUICK_GRADE_MENU: [
                CallbackQueryHandler(back_to_start, pattern="^back_to_start$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_quick_grade),
            ],
        },
        fallbacks=[CommandHandler("start", start)],
    )
    
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(conv_handler)
    app.add_error_handler(error_handler)
    
    print("🚀 Advanced Exam Grading Bot v2 is ONLINE!")
    print("✅ Features: Teacher Accounts | Dynamic Questions | Student Answers")
    print("✅ Features: Quick Grading | Customizable Scales | Proper Navigation")
    print("\n📍 Waiting for users...\n")
    
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
