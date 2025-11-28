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

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, filters, 
    ContextTypes, ConversationHandler, CallbackQueryHandler
)
from dotenv import load_dotenv

sqlite3.register_adapter(datetime, lambda val: val.isoformat() if val else None)

USE_EMBEDDINGS = False
EMBED_MODEL = None

try:
    from sentence_transformers import SentenceTransformer, util
    EMBED_MODEL = SentenceTransformer('all-MiniLM-L6-v2')
    USE_EMBEDDINGS = True
except:
    pass

load_dotenv()

# ============================================================================
# CONFIG
# ============================================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
SUPER_ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

if not TELEGRAM_TOKEN:
    print("ERROR: TELEGRAM_TOKEN missing in environment!")
    print("Please add TELEGRAM_TOKEN to your secrets.")
    sys.exit(1)

# Conversation states
(START, TEACHER_LOGIN, TEACHER_REGISTER, TEACHER_MENU, CREATE_QUESTION,
 STUDENT_MAIN, FIND_ASSIGNMENT, ANSWER_SUBMISSION, QUICK_GRADE_MENU,
 QUICK_GRADE_SETUP, QUICK_GRADE_ANSWER, TEACHER_DASHBOARD) = range(12)

# ============================================================================
# DATABASE SETUP
# ============================================================================

def init_db():
    """Initialize SQLite database with teacher accounts"""
    conn = sqlite3.connect("exam_data.db")
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS teachers
        (teacher_id INTEGER PRIMARY KEY, telegram_id INT UNIQUE, username TEXT UNIQUE,
         password TEXT, full_name TEXT, created_at TIMESTAMP, grading_scale INT DEFAULT 100)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS assignments
        (assignment_id TEXT PRIMARY KEY, teacher_id INT, code TEXT UNIQUE,
         title TEXT, question TEXT, question_type TEXT, 
         max_score INT, grading_scale INT, created_at TIMESTAMP, 
         answers JSON, rubric JSON, FOREIGN KEY(teacher_id) REFERENCES teachers(teacher_id))''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS submissions
        (submission_id TEXT PRIMARY KEY, assignment_id TEXT, student_name TEXT,
         student_id INT, answer TEXT, score REAL, max_score INT,
         grading_details JSON, submitted_at TIMESTAMP,
         FOREIGN KEY(assignment_id) REFERENCES assignments(assignment_id))''')
    
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

def login_teacher_by_telegram_id(telegram_id):
    """Login teacher by telegram ID (auto-login for existing accounts)"""
    conn = sqlite3.connect("exam_data.db")
    c = conn.cursor()
    c.execute("SELECT teacher_id, full_name FROM teachers WHERE telegram_id=?", (telegram_id,))
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

def get_teacher_assignments(teacher_id):
    """Get all assignments for a teacher"""
    conn = sqlite3.connect("exam_data.db")
    c = conn.cursor()
    c.execute('''SELECT assignment_id, code, title, question, question_type, max_score, created_at 
                 FROM assignments WHERE teacher_id=? ORDER BY created_at DESC''', (teacher_id,))
    results = c.fetchall()
    conn.close()
    return results

def get_assignment_submissions(assignment_id):
    """Get all submissions for an assignment"""
    conn = sqlite3.connect("exam_data.db")
    c = conn.cursor()
    c.execute('''SELECT submission_id, student_name, student_id, answer, score, max_score, submitted_at 
                 FROM submissions WHERE assignment_id=? ORDER BY submitted_at DESC''', (assignment_id,))
    results = c.fetchall()
    conn.close()
    return results

def normalize_text(s):
    """Normalize text"""
    if not s:
        return ""
    s = str(s).lower().strip()
    s = re.sub(r'[^a-z0-9\s]', ' ', s)
    s = re.sub(r'\s+', ' ', s)
    return s

# ============================================================================
# GRADING FUNCTIONS
# ============================================================================

def grade_answer(student_answer, expected_answer, max_score, question_type="short"):
    """Grade student answer"""
    sa = normalize_text(student_answer)
    ea = normalize_text(expected_answer)
    
    if question_type == "exact" or question_type == "exactmatch":
        score = max_score if sa == ea else 0
        detail = "Exact match!" if score == max_score else "Incorrect"
    
    elif question_type == "keyword" or question_type == "keywordbased":
        keywords = ea.split()
        if keywords:
            matched = sum(1 for kw in keywords if kw in sa)
            score = int((matched / len(keywords) * max_score))
            detail = f"Matched {matched}/{len(keywords)} keywords"
        else:
            score = 0
            detail = "No keywords to match"
    
    elif (question_type == "semantic" or question_type == "aisemantic") and USE_EMBEDDINGS:
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
        keywords = ea.split()
        if keywords:
            matched = sum(1 for kw in keywords if kw in sa)
            score = int((matched / len(keywords) * max_score))
            detail = f"Matched {matched}/{len(keywords)} keywords"
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
    
    context.user_data.clear()
    
    teacher_info = teacher_exists_by_telegram(user_id)
    
    keyboard = [
        [InlineKeyboardButton("Teacher Account", callback_data="teacher_mode")],
        [InlineKeyboardButton("Student", callback_data="student_mode")]
    ]
    
    if teacher_info:
        keyboard[0][0] = InlineKeyboardButton("Login to Teacher Account", callback_data="teacher_login")
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"Welcome {user_name}!\n\n"
        "**Smart Exam & Assignment System**\n\n"
        "Choose your role:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )
    
    return START

# ============================================================================
# TEACHER AUTHENTICATION HANDLERS
# ============================================================================

async def teacher_mode_selector(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Teacher: Register or Login - For new users"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    teacher_info = teacher_exists_by_telegram(user_id)
    
    if teacher_info:
        keyboard = [
            [InlineKeyboardButton("Login", callback_data="proceed_login")],
            [InlineKeyboardButton("Back", callback_data="back_to_start")]
        ]
        await query.edit_message_text(
            "**TEACHER LOGIN**\n\n"
            "You have an existing account!\n"
            "Click Login to continue.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    else:
        keyboard = [
            [InlineKeyboardButton("Create New Account", callback_data="proceed_register")],
            [InlineKeyboardButton("Back", callback_data="back_to_start")]
        ]
        await query.edit_message_text(
            "**NEW TEACHER ACCOUNT**\n\n"
            "No account found. Create one now!",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    
    context.user_data['auth_step'] = None
    return TEACHER_LOGIN

async def teacher_login_selector(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Teacher: Direct login for existing users"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    teacher_info = teacher_exists_by_telegram(user_id)
    
    if teacher_info:
        teacher_id, full_name = teacher_info
        context.user_data['teacher_id'] = teacher_id
        context.user_data['full_name'] = full_name
        
        await query.edit_message_text(
            f"Welcome back, {full_name}!\n\n"
            "Loading teacher menu..."
        )
        
        await show_teacher_menu_from_callback(query, context)
        return TEACHER_MENU
    else:
        keyboard = [
            [InlineKeyboardButton("Create New Account", callback_data="proceed_register")],
            [InlineKeyboardButton("Back", callback_data="back_to_start")]
        ]
        await query.edit_message_text(
            "**NO ACCOUNT FOUND**\n\n"
            "You need to create an account first.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        context.user_data['auth_step'] = None
        return TEACHER_LOGIN

async def proceed_teacher_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Login flow - username/password"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    teacher_info = teacher_exists_by_telegram(user_id)
    
    if teacher_info:
        teacher_id, full_name = teacher_info
        context.user_data['teacher_id'] = teacher_id
        context.user_data['full_name'] = full_name
        
        await query.edit_message_text(
            f"Welcome back, {full_name}!\n\n"
            "Loading teacher menu..."
        )
        
        await show_teacher_menu_from_callback(query, context)
        return TEACHER_MENU
    else:
        await query.edit_message_text(
            "**LOGIN**\n\n"
            "Send your username:",
            parse_mode="Markdown"
        )
        context.user_data['auth_step'] = 'username'
        return TEACHER_LOGIN

async def proceed_teacher_register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Register flow"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "**CREATE ACCOUNT**\n\n"
        "Step 1: Enter your full name",
        parse_mode="Markdown"
    )
    context.user_data['auth_step'] = 'register_name'
    return TEACHER_REGISTER

async def handle_teacher_auth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle authentication text input"""
    user_id = update.message.from_user.id
    text = update.message.text.strip()
    
    auth_step = context.user_data.get('auth_step')
    
    if not auth_step:
        await update.message.reply_text(
            "Please click a button first (Login or Register)\n\n"
            "Type /start to begin"
        )
        return TEACHER_LOGIN
    
    if auth_step == 'username':
        context.user_data['login_username'] = text
        await update.message.reply_text("Now send your password:")
        context.user_data['auth_step'] = 'password'
        return TEACHER_LOGIN
    
    elif auth_step == 'password':
        username = context.user_data.get('login_username')
        password = text
        
        teacher_id, full_name = login_teacher(username, password)
        
        if teacher_id:
            context.user_data['teacher_id'] = teacher_id
            context.user_data['full_name'] = full_name
            context.user_data['auth_step'] = None
            
            await update.message.reply_text(
                f"Welcome back, {full_name}!\n\n"
                "Loading teacher menu..."
            )
            await show_teacher_menu(update, context)
            return TEACHER_MENU
        else:
            keyboard = [
                [InlineKeyboardButton("Try Again", callback_data="proceed_login")],
                [InlineKeyboardButton("Back", callback_data="back_to_start")]
            ]
            await update.message.reply_text(
                "Invalid username or password.\n\n"
                "Please try again or go back.",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            context.user_data['auth_step'] = None
            return TEACHER_LOGIN
    
    return TEACHER_LOGIN

async def handle_teacher_register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle registration text input"""
    user_id = update.message.from_user.id
    text = update.message.text.strip()
    
    auth_step = context.user_data.get('auth_step')
    
    if not auth_step:
        await update.message.reply_text(
            "Please click 'Create New Account' first\n\n"
            "Type /start to begin"
        )
        return TEACHER_REGISTER
    
    if auth_step == 'register_name':
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
                await update.message.reply_text("Scale must be between 1-100. Try again:")
                return TEACHER_REGISTER
            
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
                context.user_data['auth_step'] = None
                
                await update.message.reply_text(
                    f"Account created successfully!\n\n"
                    f"Name: {context.user_data['reg_name']}\n"
                    f"Username: {context.user_data['reg_username']}\n"
                    f"Grading Scale: 0-{scale}\n\n"
                    "Loading teacher menu..."
                )
                await show_teacher_menu(update, context)
                return TEACHER_MENU
            else:
                keyboard = [
                    [InlineKeyboardButton("Try Again", callback_data="proceed_register")],
                    [InlineKeyboardButton("Back", callback_data="back_to_start")]
                ]
                await update.message.reply_text(
                    "Username already taken. Please try another.\n\n",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                context.user_data['auth_step'] = None
                return TEACHER_REGISTER
        except ValueError:
            await update.message.reply_text("Please enter a valid number (1-100):")
            return TEACHER_REGISTER
    
    return TEACHER_REGISTER

# ============================================================================
# TEACHER MENU & FEATURES
# ============================================================================

async def show_teacher_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show teacher main menu - from message"""
    full_name = context.user_data.get('full_name', 'Teacher')
    
    keyboard = [
        [InlineKeyboardButton("Create Assignment", callback_data="create_assignment")],
        [InlineKeyboardButton("My Assignments", callback_data="my_assignments")],
        [InlineKeyboardButton("Quick Grade", callback_data="quick_grade")],
        [InlineKeyboardButton("Results & Analytics", callback_data="view_results")],
        [InlineKeyboardButton("Logout", callback_data="logout")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"**TEACHER DASHBOARD**\n\n"
        f"Welcome, {full_name}!\n\n"
        "What would you like to do?",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )
    
    return TEACHER_MENU

async def show_teacher_menu_from_callback(query, context: ContextTypes.DEFAULT_TYPE):
    """Show teacher main menu - from callback query"""
    full_name = context.user_data.get('full_name', 'Teacher')
    
    keyboard = [
        [InlineKeyboardButton("Create Assignment", callback_data="create_assignment")],
        [InlineKeyboardButton("My Assignments", callback_data="my_assignments")],
        [InlineKeyboardButton("Quick Grade", callback_data="quick_grade")],
        [InlineKeyboardButton("Results & Analytics", callback_data="view_results")],
        [InlineKeyboardButton("Logout", callback_data="logout")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.message.reply_text(
        f"**TEACHER DASHBOARD**\n\n"
        f"Welcome, {full_name}!\n\n"
        "What would you like to do?",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )
    
    return TEACHER_MENU

async def back_to_teacher_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Back to teacher menu from callback"""
    query = update.callback_query
    await query.answer()
    
    full_name = context.user_data.get('full_name', 'Teacher')
    
    keyboard = [
        [InlineKeyboardButton("Create Assignment", callback_data="create_assignment")],
        [InlineKeyboardButton("My Assignments", callback_data="my_assignments")],
        [InlineKeyboardButton("Quick Grade", callback_data="quick_grade")],
        [InlineKeyboardButton("Results & Analytics", callback_data="view_results")],
        [InlineKeyboardButton("Logout", callback_data="logout")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"**TEACHER DASHBOARD**\n\n"
        f"Welcome, {full_name}!\n\n"
        "What would you like to do?",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )
    
    return TEACHER_MENU

async def create_assignment_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start creating assignment"""
    query = update.callback_query
    await query.answer()
    
    keyboard = [[InlineKeyboardButton("Back", callback_data="teacher_menu")]]
    await query.edit_message_text(
        "**CREATE NEW ASSIGNMENT**\n\n"
        "Step 1: Enter assignment title",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
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
            [InlineKeyboardButton("Short Answer", callback_data="type_short")],
            [InlineKeyboardButton("Exact Match", callback_data="type_exact")],
            [InlineKeyboardButton("Keyword Based", callback_data="type_keyword")],
            [InlineKeyboardButton("AI Semantic", callback_data="type_semantic")],
        ]
        await update.message.reply_text(
            "Step 3: Choose question type",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return CREATE_QUESTION
    
    elif assign_step == 'answer':
        context.user_data['assign_answer'] = text
        await update.message.reply_text(
            "Step 5: Enter the maximum score (e.g., 5, 10, 20, 100)"
        )
        context.user_data['assign_step'] = 'max_score'
        return CREATE_QUESTION
    
    elif assign_step == 'max_score':
        try:
            max_score = int(text)
            
            conn = sqlite3.connect("exam_data.db")
            c = conn.cursor()
            c.execute("SELECT grading_scale FROM teachers WHERE teacher_id=?", (teacher_id,))
            result = c.fetchone()
            scale = result[0] if result else 100
            conn.close()
            
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
            
            keyboard = [[InlineKeyboardButton("Back to Menu", callback_data="teacher_menu")]]
            
            await update.message.reply_text(
                f"**ASSIGNMENT CREATED!**\n\n"
                f"**Title:** {context.user_data['assign_title']}\n"
                f"**Assignment Code:** `{code}`\n"
                f"**Max Score:** {max_score}/{scale}\n"
                f"**Question Type:** {context.user_data['assign_type']}\n\n"
                f"Share the code with students so they can access this assignment!",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
            
            context.user_data['assign_step'] = None
            return TEACHER_MENU
        except ValueError:
            await update.message.reply_text("Please enter a valid number for max score:")
            return CREATE_QUESTION
    
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
    context.user_data['assign_step'] = 'answer'
    
    await query.edit_message_text(
        f"Question type: **{assign_type}**\n\n"
        f"Step 4: Now send the correct answer(s):",
        parse_mode="Markdown"
    )
    
    return CREATE_QUESTION

async def my_assignments_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show teacher's assignments"""
    query = update.callback_query
    await query.answer()
    
    teacher_id = context.user_data.get('teacher_id')
    assignments = get_teacher_assignments(teacher_id)
    
    if assignments:
        text = "**MY ASSIGNMENTS**\n\n"
        for i, (aid, code, title, question, qtype, max_score, created) in enumerate(assignments[:10], 1):
            submissions = get_assignment_submissions(aid)
            text += f"{i}. **{title}**\n"
            text += f"   Code: `{code}`\n"
            text += f"   Type: {qtype} | Max: {max_score}\n"
            text += f"   Submissions: {len(submissions)}\n\n"
    else:
        text = "**MY ASSIGNMENTS**\n\nNo assignments created yet.\n\nClick 'Create Assignment' to get started!"
    
    keyboard = [
        [InlineKeyboardButton("Create New", callback_data="create_assignment")],
        [InlineKeyboardButton("Back", callback_data="teacher_menu")]
    ]
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    
    return TEACHER_MENU

async def view_results_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show results and analytics"""
    query = update.callback_query
    await query.answer()
    
    teacher_id = context.user_data.get('teacher_id')
    assignments = get_teacher_assignments(teacher_id)
    
    total_submissions = 0
    total_score = 0
    total_max = 0
    
    text = "**RESULTS & ANALYTICS**\n\n"
    
    if assignments:
        for aid, code, title, question, qtype, max_score, created in assignments:
            submissions = get_assignment_submissions(aid)
            total_submissions += len(submissions)
            
            if submissions:
                for sub in submissions:
                    if sub[4] is not None:
                        total_score += sub[4]
                        total_max += sub[5]
        
        text += f"**Total Assignments:** {len(assignments)}\n"
        text += f"**Total Submissions:** {total_submissions}\n"
        
        if total_max > 0:
            avg_percent = (total_score / total_max) * 100
            text += f"**Average Score:** {avg_percent:.1f}%\n"
        
        text += "\n**Recent Submissions:**\n"
        
        count = 0
        for aid, code, title, question, qtype, max_score, created in assignments:
            submissions = get_assignment_submissions(aid)
            for sub in submissions[:3]:
                if count < 5:
                    sub_id, name, sid, answer, score, max_s, submitted = sub
                    score_str = f"{score}/{max_s}" if score is not None else "Pending"
                    text += f"- {name}: {title} = {score_str}\n"
                    count += 1
    else:
        text += "No assignments or submissions yet.\n\nCreate assignments to start collecting data!"
    
    keyboard = [[InlineKeyboardButton("Back", callback_data="teacher_menu")]]
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    
    return TEACHER_MENU

# ============================================================================
# STUDENT HANDLERS
# ============================================================================

async def student_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Student main menu"""
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("Find Assignment", callback_data="find_assignment")],
        [InlineKeyboardButton("Quick Grade", callback_data="quick_grade_student")],
        [InlineKeyboardButton("Back", callback_data="back_to_start")]
    ]
    
    await query.edit_message_text(
        "**STUDENT PORTAL**\n\n"
        "What would you like to do?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    
    return STUDENT_MAIN

async def find_assignment_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Find assignment by code"""
    query = update.callback_query
    await query.answer()
    
    keyboard = [[InlineKeyboardButton("Back", callback_data="student_menu")]]
    
    await query.edit_message_text(
        "**FIND ASSIGNMENT**\n\n"
        "Enter the assignment code (given by your teacher):",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    
    context.user_data['mode'] = 'find_assignment'
    return FIND_ASSIGNMENT

async def handle_assignment_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle student entering assignment code"""
    code = update.message.text.strip().upper()
    
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
            [InlineKeyboardButton("Submit Answer", callback_data="submit_answer")],
            [InlineKeyboardButton("Back", callback_data="student_menu")]
        ]
        
        await update.message.reply_text(
            f"**ASSIGNMENT FOUND!**\n\n"
            f"**Title:** {title}\n"
            f"**Question:** {question}\n"
            f"**Max Score:** {max_score}/{scale}\n\n"
            f"Ready to answer?",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        
        return FIND_ASSIGNMENT
    else:
        keyboard = [[InlineKeyboardButton("Back", callback_data="student_menu")]]
        await update.message.reply_text(
            "Assignment code not found.\n\n"
            "Please check the code and try again.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return FIND_ASSIGNMENT

async def submit_answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Student submits answer"""
    query = update.callback_query
    await query.answer()
    
    keyboard = [[InlineKeyboardButton("Back", callback_data="student_menu")]]
    
    await query.edit_message_text(
        "**SUBMIT YOUR ANSWER**\n\n"
        "Type your answer below:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    
    context.user_data['mode'] = 'submit_answer'
    return ANSWER_SUBMISSION

async def process_student_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process student answer submission"""
    student_id = update.message.from_user.id
    student_name = update.message.from_user.first_name
    
    if update.message.text:
        answer = update.message.text
    else:
        await update.message.reply_text("Please send a text answer")
        return ANSWER_SUBMISSION
    
    assignment_id = context.user_data.get('current_assignment_id')
    max_score = context.user_data.get('current_max_score', 10)
    qtype = context.user_data.get('current_qtype', 'keyword')
    correct_answers = context.user_data.get('correct_answers', '')
    
    qtype_normalized = qtype.lower().replace(' ', '')
    score, detail = grade_answer(answer, correct_answers, max_score, qtype_normalized)
    
    submission_id = str(uuid.uuid4())
    conn = sqlite3.connect("exam_data.db")
    c = conn.cursor()
    c.execute('''INSERT INTO submissions
                (submission_id, assignment_id, student_name, student_id, answer, score, max_score, submitted_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
              (submission_id, assignment_id, student_name, student_id, answer, score, max_score, datetime.now()))
    conn.commit()
    conn.close()
    
    keyboard = [
        [InlineKeyboardButton("Find Another", callback_data="find_assignment")],
        [InlineKeyboardButton("Back to Menu", callback_data="student_menu")]
    ]
    
    percentage = (score / max_score * 100) if max_score > 0 else 0
    
    await update.message.reply_text(
        f"**ANSWER SUBMITTED!**\n\n"
        f"**Your Score:** {score}/{max_score}\n"
        f"**Percentage:** {percentage:.1f}%\n"
        f"**Feedback:** {detail}\n\n"
        f"What's next?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    
    return STUDENT_MAIN

async def back_to_student_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Back to student menu"""
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("Find Assignment", callback_data="find_assignment")],
        [InlineKeyboardButton("Quick Grade", callback_data="quick_grade_student")],
        [InlineKeyboardButton("Back", callback_data="back_to_start")]
    ]
    
    await query.edit_message_text(
        "**STUDENT PORTAL**\n\n"
        "What would you like to do?",
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
    
    keyboard = [[InlineKeyboardButton("Back", callback_data="back_to_start")]]
    
    await query.edit_message_text(
        "**QUICK GRADE**\n\n"
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
            
            score, detail = grade_answer(
                context.user_data['qg_student_answer'],
                context.user_data['qg_correct'],
                max_score,
                'keyword'
            )
            
            keyboard = [
                [InlineKeyboardButton("Grade Another", callback_data="quick_grade")],
                [InlineKeyboardButton("Back", callback_data="back_to_start")]
            ]
            
            percentage = (score / max_score * 100) if max_score > 0 else 0
            
            await update.message.reply_text(
                f"**GRADING RESULT**\n\n"
                f"**Question:** {context.user_data['qg_question']}\n"
                f"**Student Answer:** {context.user_data['qg_student_answer']}\n"
                f"**Correct Answer:** {context.user_data['qg_correct']}\n\n"
                f"**Score:** {score}/{max_score}\n"
                f"**Percentage:** {percentage:.1f}%\n"
                f"**Detail:** {detail}",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
            
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
            await update.message.reply_text("Please enter a valid number:")
            return QUICK_GRADE_MENU
    
    return QUICK_GRADE_MENU

# ============================================================================
# NAVIGATION HANDLERS
# ============================================================================

async def back_to_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Go back to start"""
    query = update.callback_query
    await query.answer()
    
    context.user_data.clear()
    
    keyboard = [
        [InlineKeyboardButton("Teacher Account", callback_data="teacher_mode")],
        [InlineKeyboardButton("Student", callback_data="student_mode")]
    ]
    
    await query.edit_message_text(
        "**Smart Exam & Assignment System**\n\n"
        "Choose your role:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return START

async def logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Logout teacher"""
    query = update.callback_query
    await query.answer()
    
    context.user_data.clear()
    
    keyboard = [
        [InlineKeyboardButton("Teacher Account", callback_data="teacher_mode")],
        [InlineKeyboardButton("Student", callback_data="student_mode")]
    ]
    
    await query.edit_message_text(
        "**Logged out successfully!**\n\n"
        "Choose your role to continue:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return START

# ============================================================================
# HELP COMMAND
# ============================================================================

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show comprehensive help"""
    
    help_text = """
**EXAM GRADING BOT - HELP GUIDE**

**FOR TEACHERS:**
1. Click /start and select "Teacher Mode"
2. Register with your name, username, and password
3. Create assignments with unique codes
4. Share codes with students
5. View results and analytics

**FOR STUDENTS:**
1. Click /start and select "Student"
2. Enter assignment code from teacher
3. Submit your answer
4. Get instant grading

**QUICK GRADE:**
- Grade any answer instantly
- No login required
- Great for quick checks

**COMMANDS:**
/start - Start the bot
/help - Show this help

Need more help? Contact your teacher or administrator.
"""
    
    await update.message.reply_text(help_text, parse_mode='Markdown')

# ============================================================================
# ERROR HANDLER
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
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            START: [
                CallbackQueryHandler(teacher_mode_selector, pattern="^teacher_mode$"),
                CallbackQueryHandler(teacher_login_selector, pattern="^teacher_login$"),
                CallbackQueryHandler(student_mode, pattern="^student_mode$"),
            ],
            TEACHER_LOGIN: [
                CallbackQueryHandler(proceed_teacher_login, pattern="^proceed_login$"),
                CallbackQueryHandler(proceed_teacher_register, pattern="^proceed_register$"),
                CallbackQueryHandler(back_to_start, pattern="^back_to_start$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_teacher_auth),
            ],
            TEACHER_REGISTER: [
                CallbackQueryHandler(back_to_start, pattern="^back_to_start$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_teacher_register),
            ],
            TEACHER_MENU: [
                CallbackQueryHandler(create_assignment_start, pattern="^create_assignment$"),
                CallbackQueryHandler(my_assignments_handler, pattern="^my_assignments$"),
                CallbackQueryHandler(view_results_handler, pattern="^view_results$"),
                CallbackQueryHandler(quick_grade_start, pattern="^quick_grade$"),
                CallbackQueryHandler(logout, pattern="^logout$"),
                CallbackQueryHandler(back_to_teacher_menu, pattern="^teacher_menu$"),
            ],
            CREATE_QUESTION: [
                CallbackQueryHandler(handle_assignment_type, pattern="^type_"),
                CallbackQueryHandler(back_to_teacher_menu, pattern="^teacher_menu$"),
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
                CallbackQueryHandler(back_to_student_menu, pattern="^student_menu$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_student_answer),
            ],
            QUICK_GRADE_MENU: [
                CallbackQueryHandler(quick_grade_start, pattern="^quick_grade$"),
                CallbackQueryHandler(back_to_start, pattern="^back_to_start$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_quick_grade),
            ],
        },
        fallbacks=[
            CommandHandler("start", start),
            CommandHandler("help", help_command),
        ],
    )
    
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(conv_handler)
    app.add_error_handler(error_handler)
    
    print("Advanced Exam Grading Bot v2 is ONLINE!")
    print("Features: Teacher Accounts | Dynamic Questions | Student Answers")
    print("Features: Quick Grading | Customizable Scales | Proper Navigation")
    print("\nWaiting for users...\n")
    
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
