# ============================================================================
# JOSHUAZAZA GRADE BOT v2.1 - WITH TEACHER ACCOUNTS
# FIXED VERSION - POSTGRESQL MIGRATION + NAVIGATION FIXES
# ============================================================================

import os
import re
import sys
import json
import hashlib
import uuid
from io import BytesIO
from datetime import datetime, timedelta
from PIL import Image
import pytesseract

# Use psycopg3 with connection pooling
try:
    import psycopg
    from psycopg_pool import ConnectionPool
    PSYCOPG_AVAILABLE = True
    USE_PSYCOPG3 = True
except ImportError:
    # Fallback to psycopg2 if psycopg3 not available
    try:
        import psycopg2
        PSYCOPG_AVAILABLE = True
        USE_PSYCOPG3 = False
    except ImportError:
        PSYCOPG_AVAILABLE = False
        USE_PSYCOPG3 = False

import logging

# Configure logging for better error tracking
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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

# Voice to text
try:
    import speech_recognition as sr
    SPEECH_RECOGNIZER = sr.Recognizer()
    VOICE_SUPPORT = True
except:
    SPEECH_RECOGNIZER = None
    VOICE_SUPPORT = False

# Gemini AI
try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except:
    GEMINI_AVAILABLE = False

load_dotenv()

# --- BEGIN: tiny HTTP health server so Render detects a bound port ---
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Respond 200 OK for any GET (so visiting root returns "OK")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"OK")

    # silence noisy logs
    def log_message(self, format, *args):
        return

def _start_health_server():
    port = int(os.environ.get("PORT", "10000"))  # Render sets PORT automatically
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    print(f"[health] Listening on 0.0.0.0:{port}")
    server.serve_forever()

# start server in daemon thread so it doesn't block bot
threading.Thread(target=_start_health_server, daemon=True).start()
# --- END health server ---


# ============================================================================
# CONFIG
# ============================================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
SUPER_ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# PostgreSQL Database URL from Render
DATABASE_URL = os.getenv("DATABASE_URL")

if not TELEGRAM_TOKEN:
    print("❌ ERROR: TELEGRAM_TOKEN missing in .env file!")
    sys.exit(1)

if not DATABASE_URL:
    print("❌ ERROR: DATABASE_URL missing in environment variables!")
    sys.exit(1)

# Initialize Gemini if API key available
GEMINI_MODEL = None
if GEMINI_API_KEY and GEMINI_AVAILABLE:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        GEMINI_MODEL = genai.GenerativeModel('gemini-2.0-flash')
        print("✅ Gemini AI initialized successfully!")
    except Exception as e:
        print(f"⚠️ Gemini initialization failed: {e}")
        GEMINI_MODEL = None


if os.name == "nt":
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# Conversation states
(START, TEACHER_LOGIN, TEACHER_REGISTER, TEACHER_MENU, CREATE_QUESTION,
 STUDENT_MAIN, FIND_ASSIGNMENT, ANSWER_SUBMISSION, QUICK_GRADE_MENU,
 QUICK_GRADE_SETUP, QUICK_GRADE_ANSWER, TEACHER_DASHBOARD, 
 ASSIGN_DEADLINE, ASSIGN_COLLECT_DETAILS, ASSIGN_ADD_FIELDS, 
 EDIT_ASSIGNMENT, VIEW_SUBMISSION_DETAILS, STUDENT_FILL_DETAILS) = range(18)

# ============================================================================
# DATABASE SETUP - POSTGRESQL WITH CONNECTION POOLING AND ERROR HANDLING
# ============================================================================

# Global connection pool - initialized on first use
DB_POOL = None

def init_db_pool():
    """Initialize connection pool for efficient database access"""
    global DB_POOL
    if DB_POOL is not None:
        return DB_POOL
    
    try:
        if USE_PSYCOPG3:
            DB_POOL = ConnectionPool(DATABASE_URL, open=False)
            DB_POOL.open()
            logger.info("✅ Database connection pool initialized successfully!")
        else:
            logger.warning("⚠️ Connection pooling only available with psycopg3. Using direct connections.")
        return DB_POOL
    except Exception as e:
        logger.error(f"❌ Failed to initialize connection pool: {e}")
        return None

def get_db_connection(retry_count=3):
    """Get PostgreSQL database connection with retry logic
    
    Args:
        retry_count: Number of times to retry on connection failure
    
    Returns:
        Connection object or None if failed
    """
    global DB_POOL
    
    # Try to use connection pool if available
    if USE_PSYCOPG3 and DB_POOL is not None:
        try:
            conn = DB_POOL.getconn()
            return conn
        except Exception as e:
            logger.warning(f"⚠️ Failed to get connection from pool: {e}")
    
    # Fallback: direct connection with retry logic
    for attempt in range(retry_count):
        try:
            if USE_PSYCOPG3:
                conn = psycopg.connect(DATABASE_URL, connect_timeout=10)
            else:
                import psycopg2
                conn = psycopg2.connect(DATABASE_URL)
            logger.debug(f"✅ Database connection established (attempt {attempt + 1})")
            return conn
        except Exception as e:
            if attempt < retry_count - 1:
                logger.warning(f"⚠️ Connection attempt {attempt + 1}/{retry_count} failed: {e}")
                import time
                time.sleep(2 ** attempt)  # Exponential backoff
            else:
                logger.error(f"❌ All {retry_count} connection attempts failed: {e}")
    
    return None

def close_db_connection(conn, cursor=None):
    """Safely close database connection and cursor
    
    Args:
        conn: Connection object to close
        cursor: Optional cursor object to close
    """
    try:
        if cursor:
            cursor.close()
    except Exception as e:
        logger.warning(f"⚠️ Error closing cursor: {e}")
    
    try:
        if conn:
            # Return to pool if from pool, otherwise close
            if USE_PSYCOPG3 and DB_POOL is not None:
                try:
                    DB_POOL.putconn(conn)
                except:
                    conn.close()
            else:
                conn.close()
    except Exception as e:
        logger.warning(f"⚠️ Error closing connection: {e}")

def init_db():
    """Initialize PostgreSQL database with teacher accounts"""
    try:
        # Initialize connection pool first
        init_db_pool()
        
        conn = get_db_connection()
        if not conn:
            logger.error("❌ Failed to connect to database for initialization")
            return False
        
        c = conn.cursor()
        
        try:
            # Teachers table
            c.execute('''CREATE TABLE IF NOT EXISTS teachers
                (teacher_id SERIAL PRIMARY KEY, telegram_id BIGINT UNIQUE, username TEXT UNIQUE,
                 password TEXT, full_name TEXT, created_at TIMESTAMP, grading_scale INT DEFAULT 100)''')
            
            # Questions/Assignments table - EXPANDED
            c.execute('''CREATE TABLE IF NOT EXISTS assignments
                (assignment_id TEXT PRIMARY KEY, teacher_id INT, code TEXT UNIQUE,
                 title TEXT, question TEXT, question_type TEXT, 
                 max_score INT, grading_scale INT, created_at TIMESTAMP, 
                 answers TEXT, rubric JSONB, deadline_at TIMESTAMP, 
                 required_fields JSONB, is_active INT DEFAULT 1,
                 FOREIGN KEY(teacher_id) REFERENCES teachers(teacher_id))''')
            
            # Student submissions - EXPANDED
            c.execute('''CREATE TABLE IF NOT EXISTS submissions
                (submission_id TEXT PRIMARY KEY, assignment_id TEXT, student_name TEXT,
                 student_id BIGINT, answer TEXT, score REAL, max_score INT,
                 grading_details JSONB, submitted_at TIMESTAMP, student_details JSONB,
                 FOREIGN KEY(assignment_id) REFERENCES assignments(assignment_id))''')
            
            # Quick grading cache
            c.execute('''CREATE TABLE IF NOT EXISTS quick_grades
                (grade_id TEXT PRIMARY KEY, teacher_id INT, question TEXT,
                 answer_given TEXT, score REAL, max_score INT,
                 graded_at TIMESTAMP, FOREIGN KEY(teacher_id) REFERENCES teachers(teacher_id))''')
            
            conn.commit()
            logger.info("✅ PostgreSQL database initialized successfully!")
            return True
            
        except Exception as e:
            logger.error(f"❌ Database initialization error: {e}")
            conn.rollback()
            return False
        finally:
            close_db_connection(conn, c)
            
    except Exception as e:
        logger.error(f"❌ Unexpected error during database initialization: {e}")
        return False

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
    """Register new teacher with improved error handling"""
    conn = get_db_connection()
    if not conn:
        logger.error(f"Failed to register teacher {username}: database connection failed")
        return False, None
    
    c = None
    try:
        c = conn.cursor()
        hashed_pass = hash_password(password)
        c.execute('''INSERT INTO teachers (telegram_id, username, password, full_name, grading_scale, created_at)
                     VALUES (%s, %s, %s, %s, %s, %s) RETURNING teacher_id''',
                  (telegram_id, username, hashed_pass, full_name, grading_scale, datetime.now()))
        teacher_id = c.fetchone()[0]
        conn.commit()
        logger.info(f"✅ Teacher {username} (ID: {teacher_id}) registered successfully")
        return True, teacher_id
    except Exception as e:
        conn.rollback()
        logger.error(f"❌ Registration error for {username}: {e}")
        return False, None
    finally:
        close_db_connection(conn, c)

def login_teacher(username, password):
    """Login teacher and return teacher_id with improved error handling"""
    conn = get_db_connection()
    if not conn:
        logger.error(f"Failed to login teacher {username}: database connection failed")
        return None, None
    
    c = None
    try:
        c = conn.cursor()
        hashed_pass = hash_password(password)
        c.execute("SELECT teacher_id, full_name FROM teachers WHERE username=%s AND password=%s",
                  (username, hashed_pass))
        result = c.fetchone()
        logger.info(f"✅ Teacher {username} logged in successfully" if result else f"⚠️ Login failed for {username}")
        return result if result else (None, None)
    except Exception as e:
        logger.error(f"❌ Login error for {username}: {e}")
        return None, None
    finally:
        close_db_connection(conn, c)

def teacher_exists_by_telegram(telegram_id):
    """Check if teacher account exists with improved error handling"""
    conn = get_db_connection()
    if not conn:
        logger.error(f"Failed to check teacher {telegram_id}: database connection failed")
        return None
    
    c = None
    try:
        c = conn.cursor()
        c.execute("SELECT teacher_id, full_name FROM teachers WHERE telegram_id=%s", (telegram_id,))
        result = c.fetchone()
        return result
    except Exception as e:
        logger.error(f"❌ Error checking teacher {telegram_id}: {e}")
        return None
    finally:
        close_db_connection(conn, c)

def normalize_text(s):
    """Normalize text"""
    if not s:
        return ""
    s = str(s).lower().strip()
    s = re.sub(r'[^a-z0-9\s]', ' ', s)
    s = re.sub(r'\s+', ' ', s)
    return s

def format_score_with_color(score, max_score):
    """Format score with color coding (emoji indicators)"""
    percentage = (score / max_score * 100) if max_score > 0 else 0
    if percentage >= 80:
        emoji = "🟢"
    elif percentage >= 60:
        emoji = "🟡"
    else:
        emoji = "🔴"
    return f"{emoji} {score}/{max_score} ({percentage:.1f}%)"

def is_assignment_expired(deadline_at):
    """Check if assignment deadline has passed"""
    if not deadline_at:
        return False
    deadline = deadline_at if isinstance(deadline_at, datetime) else datetime.fromisoformat(deadline_at)
    return datetime.now() > deadline

def get_deadline_string(deadline_at):
    """Format deadline for display"""
    if not deadline_at:
        return "No deadline"
    deadline = deadline_at if isinstance(deadline_at, datetime) else datetime.fromisoformat(deadline_at)
    return deadline.strftime("%B %d, %Y at %I:%M %p")

def ocr_from_image_bytes(image_bytes):
    """Extract text from image"""
    try:
        img = Image.open(BytesIO(image_bytes)).convert("RGB")
        return pytesseract.image_to_string(img)
    except:
        return "[OCR failed]"

# ============================================================================
# GEMINI AI GRADING FUNCTION
# ============================================================================

def grade_with_gemini(student_answer, expected_answer, max_score, question_type="semantic"):
    """Grade using Google Gemini AI with detailed feedback"""
    if not GEMINI_MODEL:
        return None, None
    
    try:
        prompt = f"""You are an expert exam grader. Score this student answer fairly and provide constructive feedback.

EXPECTED ANSWER: {expected_answer}
STUDENT ANSWER: {student_answer}
MAX SCORE: {max_score}
QUESTION TYPE: {question_type}

Your task: Grade the student's answer and provide brief feedback.

RESPOND WITH ONLY THIS JSON FORMAT (no markdown, no code blocks, no extra text):
{{"score": <number>, "feedback": "<feedback under 30 words>"}}

Scoring Rules:
- {max_score} points: Perfect answer, matches expected meaning exactly
- {int(max_score * 0.7)} points: Good answer, minor gaps or extra info
- {int(max_score * 0.5)} points: Acceptable, missing some key points
- {int(max_score * 0.3)} points: Partial understanding, major gaps
- 0 points: Wrong or irrelevant answer"""

        response = GEMINI_MODEL.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=0.2,
                max_output_tokens=100,
                top_p=0.8,
            )
        )
        
        response_text = response.text.strip()
        
        # Remove markdown code blocks if present
        if response_text.startswith("```json"):
            response_text = response_text[7:]
        if response_text.startswith("```"):
            response_text = response_text[3:]
        if response_text.endswith("```"):
            response_text = response_text[:-3]
        
        response_text = response_text.strip()
        
        # Try to extract JSON if it's embedded in text
        if "{" in response_text:
            response_text = response_text[response_text.find("{"):response_text.rfind("}")+1]
        
        result = json.loads(response_text)
        
        score = int(result.get('score', 0))
        # Ensure score is within bounds
        score = max(0, min(int(score), max_score))
        feedback = str(result.get('feedback', 'Answer graded by AI')).strip()
        
        return score, feedback
    except json.JSONDecodeError as e:
        print(f"⚠️ Gemini JSON parse error: {e}")
        return None, None
    except Exception as e:
        print(f"⚠️ Gemini grading error: {e}")
        return None, None

# ============================================================================
# GRADING FUNCTIONS
# ============================================================================

def grade_answer(student_answer, expected_answer, max_score, question_type="short"):
    """Grade student answer - uses Gemini AI if available for semantic mode"""
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
    
    elif question_type == "semantic":
        # Try Gemini first if available
        if GEMINI_MODEL:
            gemini_score, gemini_feedback = grade_with_gemini(student_answer, expected_answer, max_score, "semantic")
            if gemini_score is not None:
                return gemini_score, f"🤖 Gemini AI: {gemini_feedback}"
        
        # Fallback to sentence-transformers embeddings
        if USE_EMBEDDINGS:
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
                detail = f"📊 Semantic match: {similarity:.2f}"
            except:
                score = 0
                detail = "AI grading failed"
        else:
            score = 0
            detail = "AI unavailable"
    
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
        [InlineKeyboardButton("👨‍🎓 Student", callback_data="student_mode")],
        [InlineKeyboardButton("❓ Help", callback_data="show_help")]
    ]
    
    if teacher_info:
        keyboard[0][0] = InlineKeyboardButton("👨‍🏫 Login to Teacher Account", callback_data="teacher_login")
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "👋 **Welcome!**\n\n"
        "🎓 **Smart Exam & Assignment System**\n\n"
        "Choose your role:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )
    
    return START

# ============================================================================
# TEACHER AUTHENTICATION HANDLERS - FIXED
# ============================================================================

async def teacher_mode_selector(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Teacher: Register or Login"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    teacher_info = teacher_exists_by_telegram(user_id)
    
    if teacher_info:
        # Account exists - show login
        keyboard = [
            [InlineKeyboardButton("🔐 Login", callback_data="proceed_login")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_to_start")]
        ]
        await query.edit_message_text(
            "👨‍🏫 **TEACHER LOGIN**\n\n"
            "You have an existing account!\n"
            "Click Login to continue.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        context.user_data['auth_step'] = None
    else:
        # No account - show register
        keyboard = [
            [InlineKeyboardButton("📝 Create New Account", callback_data="proceed_register")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_to_start")]
        ]
        await query.edit_message_text(
            "👨‍🏫 **NEW TEACHER ACCOUNT**\n\n"
            "No account found. Create one now!",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        context.user_data['auth_step'] = None
    
    return TEACHER_LOGIN

async def direct_teacher_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct teacher login when they already have an account"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    teacher_info = teacher_exists_by_telegram(user_id)
    
    if teacher_info:
        # Direct login since account exists
        teacher_id, full_name = teacher_info
        context.user_data['teacher_id'] = teacher_id
        context.user_data['full_name'] = full_name
        
        await query.edit_message_text(
            f"✅ Welcome back, {full_name}!\n\n"
            "Loading teacher menu..."
        )
        await show_teacher_menu(update, context)
        return TEACHER_MENU
    else:
        await query.edit_message_text(
            "❌ No teacher account found. Please register first.\n\n"
            "Use /start to begin again."
        )
        return START

async def proceed_teacher_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Login flow"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "🔐 **LOGIN**\n\n"
        "Send your username:"
    )
    context.user_data['auth_step'] = 'username'
    return TEACHER_LOGIN

async def proceed_teacher_register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Register flow"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "📝 **CREATE ACCOUNT**\n\n"
        "Step 1: Enter your full name"
    )
    context.user_data['auth_step'] = 'register_name'
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
    context.user_data['required_fields'] = []
    return CREATE_QUESTION

async def finalize_assignment(update: Update, context: ContextTypes.DEFAULT_TYPE, teacher_id):
    """Finalize and save assignment to database"""
    max_score = context.user_data.get('assign_max_score')
    
    # Get teacher's grading scale
    conn = get_db_connection()
    if not conn:
        await update.message.reply_text("❌ Database connection error. Please try again.")
        return TEACHER_MENU
    
    c = conn.cursor()
    c.execute("SELECT grading_scale FROM teachers WHERE teacher_id=%s", (teacher_id,))
    result = c.fetchone()
    if not result:
        conn.close()
        await update.message.reply_text("❌ Teacher not found.")
        return TEACHER_MENU
    scale = result[0]
    
    # Create assignment
    assignment_id = str(uuid.uuid4())
    code = generate_assignment_code()
    
    # FIX: Properly format required_fields as JSON
    required_fields = context.user_data.get('required_fields', [])
    required_fields_json = json.dumps(required_fields) if required_fields else json.dumps([])
    
    deadline_at = context.user_data.get('assign_deadline')
    
    try:
        c.execute('''INSERT INTO assignments 
                    (assignment_id, teacher_id, code, title, question, 
                     question_type, max_score, grading_scale, created_at, answers, 
                     required_fields, deadline_at, is_active)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)''',
                  (assignment_id, teacher_id, code, context.user_data['assign_title'],
                   context.user_data['assign_question'], context.user_data['assign_type'],
                   max_score, scale, datetime.now(), context.user_data['assign_answer'],
                   required_fields_json, deadline_at, 1))
        conn.commit()
        conn.close()
        
        deadline_str = f"\n📅 **Deadline:** {get_deadline_string(deadline_at)}" if deadline_at else ""
        required_str = ""
        if required_fields:
            required_str = f"\n📋 **Required Details:** {', '.join(required_fields)}"
        
        keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="teacher_menu")]]
        
        await update.message.reply_text(
            f"✅ **ASSIGNMENT CREATED!**\n\n"
            f"📌 **Title:** {context.user_data['assign_title']}\n"
            f"🔑 **Assignment Code:** `{code}`\n"
            f"📊 **Max Score:** {max_score}/{scale}\n"
            f"❓ **Question Type:** {context.user_data['assign_type']}"
            f"{deadline_str}{required_str}\n\n"
            f"Share the code with students so they can access this assignment!",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        
        # Clear assignment data
        context.user_data['assign_step'] = None
        context.user_data['required_fields'] = []
        
    except Exception as e:
        conn.close()
        await update.message.reply_text(f"❌ Error creating assignment: {str(e)}")
        return TEACHER_MENU

async def handle_view_assign_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View assignment details and submissions"""
    query = update.callback_query
    await query.answer()
    
    # Extract assignment ID from callback
    callback_data = query.data
    assign_id_prefix = callback_data.replace("view_assign_", "")
    
    teacher_id = context.user_data.get('teacher_id')
    
    conn = get_db_connection()
    if not conn:
        await query.edit_message_text("❌ Database connection error.")
        return TEACHER_MENU
    
    c = conn.cursor()
    # Get assignment details
    c.execute('''SELECT assignment_id, code, title, question, question_type, max_score, 
                        deadline_at, required_fields, created_at, is_active
                 FROM assignments 
                 WHERE teacher_id=%s AND assignment_id LIKE %s''', 
              (teacher_id, f"{assign_id_prefix}%"))
    assign = c.fetchone()
    
    if not assign:
        conn.close()
        await query.edit_message_text("❌ Assignment not found.")
        return TEACHER_MENU
    
    assignment_id, code, title, question, qtype, max_score, deadline_at, required_fields_json, created_at, is_active = assign
    
    # Get all submissions
    c.execute('''SELECT submission_id, student_name, student_id, answer, score, max_score, submitted_at, student_details
                 FROM submissions 
                 WHERE assignment_id=%s
                 ORDER BY submitted_at DESC''', (assignment_id,))
    submissions = c.fetchall()
    conn.close()
    
    context.user_data['edit_assign_id'] = assignment_id
    
    deadline_str = f"\n📅 Deadline: {get_deadline_string(deadline_at)}" if deadline_at else ""
    required_str = ""
    if required_fields_json:
        try:
            fields = json.loads(required_fields_json)
            if fields:
                required_str = f"\n📋 Required Details: {', '.join(fields)}"
        except:
            pass
    
    status_emoji = "🟢" if is_active else "🔴"
    
    text = f"📌 **ASSIGNMENT DETAILS**\n\n"
    text += f"{status_emoji} **Title:** {title}\n"
    text += f"🔑 **Code:** `{code}`\n"
    text += f"❓ **Type:** {qtype}\n"
    text += f"📊 **Max Score:** {max_score}\n"
    text += f"❓ **Question:** {question}{deadline_str}{required_str}\n\n"
    text += f"📨 **Submissions:** {len(submissions)}\n"
    
    if submissions:
        text += f"\n**Recent Submissions:**\n"
        for subm_id, student_name, student_id, answer, score, subm_max, submitted_at, student_details in submissions[:5]:
            score_colored = format_score_with_color(score, subm_max)
            text += f"  {score_colored} - {student_name}\n"
    
    keyboard = []
    if submissions:
        keyboard.append([InlineKeyboardButton("👥 View All Submissions", callback_data=f"view_subs_{assignment_id[:8]}")])
    
    keyboard.append([InlineKeyboardButton("✏️ Edit Assignment", callback_data=f"edit_assign_{assignment_id[:8]}")])
    keyboard.append([InlineKeyboardButton("🗑️ Delete Assignment", callback_data=f"delete_assign_{assignment_id[:8]}")])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="my_assignments")])
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    
    return TEACHER_MENU

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
    
    elif assign_step == 'answer':
        context.user_data['assign_answer'] = text
        await update.message.reply_text(
            "Step 5: Enter the maximum score for this question"
        )
        context.user_data['assign_step'] = 'max_score'
        return CREATE_QUESTION
    
    elif assign_step == 'max_score':
        try:
            max_score = int(text)
            context.user_data['assign_max_score'] = max_score
            
            # Ask about required student details
            keyboard = [
                [InlineKeyboardButton("✅ Yes, collect details", callback_data="collect_details_yes")],
                [InlineKeyboardButton("❌ No, skip details", callback_data="collect_details_no")]
            ]
            await update.message.reply_text(
                "Step 6: Do you want to collect additional details from students when submitting answers?\n\n"
                "Examples: Name, Phone, Student ID, Email, Gender, etc.",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return CREATE_QUESTION
        except ValueError:
            await update.message.reply_text("❌ Please enter a valid number for max score")
            return CREATE_QUESTION
    
    elif assign_step == 'deadline_date':
        try:
            # Parse deadline date in format: YYYY-MM-DD HH:MM or YYYY-MM-DD
            deadline_str = text.strip()
            if len(deadline_str) == 10:  # Only date provided
                deadline_str += " 23:59"
            deadline_dt = datetime.strptime(deadline_str, "%Y-%m-%d %H:%M")
            if deadline_dt <= datetime.now():
                await update.message.reply_text("❌ Deadline must be in the future. Try again (format: YYYY-MM-DD HH:MM)")
                return CREATE_QUESTION
            context.user_data['assign_deadline'] = deadline_dt.isoformat()
            await finalize_assignment(update, context, teacher_id)
            return TEACHER_MENU
        except ValueError:
            await update.message.reply_text("❌ Invalid date format. Use: YYYY-MM-DD or YYYY-MM-DD HH:MM")
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
        f"✅ Question type: **{assign_type}**\n\n"
        f"Step 4: Now send the correct answer(s):",
        parse_mode="Markdown"
    )
    
    return CREATE_QUESTION

async def handle_collect_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle yes/no for collecting student details"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "collect_details_yes":
        context.user_data['collect_details'] = True
        context.user_data['required_fields'] = []
        keyboard = [
            [InlineKeyboardButton("👤 Name", callback_data="add_field_name")],
            [InlineKeyboardButton("📱 Phone", callback_data="add_field_phone")],
            [InlineKeyboardButton("🪪 Registration Number", callback_data="add_field_reg")],
            [InlineKeyboardButton("✉️ Email", callback_data="add_field_email")],
            [InlineKeyboardButton("🔢 Gender", callback_data="add_field_gender")],
            [InlineKeyboardButton("📚 Class/Grade", callback_data="add_field_class")],
            [InlineKeyboardButton("✅ Done Adding", callback_data="fields_done")]
        ]
        await query.edit_message_text(
            "Step 6.1: **Select required fields**\n\n"
            "Click each field you want students to provide:\n"
            "_(You can add multiple fields)_",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return CREATE_QUESTION
    else:
        context.user_data['collect_details'] = False
        context.user_data['required_fields'] = []
        keyboard = [[InlineKeyboardButton("Continue ➜", callback_data="proceed_deadline")]]
        await query.edit_message_text(
            "Step 6: No additional details will be collected.\n\n"
            "Next, set a deadline for this assignment:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return CREATE_QUESTION

async def handle_add_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle adding a field to required fields"""
    query = update.callback_query
    await query.answer()
    
    field_map = {
        'add_field_name': 'Name',
        'add_field_phone': 'Phone',
        'add_field_reg': 'Registration Number',
        'add_field_email': 'Email',
        'add_field_gender': 'Gender',
        'add_field_class': 'Class/Grade'
    }
    
    field_name = field_map.get(query.data)
    if field_name and field_name not in context.user_data.get('required_fields', []):
        context.user_data['required_fields'].append(field_name)
    
    fields_added = ", ".join(context.user_data.get('required_fields', []))
    if not fields_added:
        fields_added = "_None selected yet_"
    
    keyboard = [
        [InlineKeyboardButton("👤 Name", callback_data="add_field_name")],
        [InlineKeyboardButton("📱 Phone", callback_data="add_field_phone")],
        [InlineKeyboardButton("🪪 Registration Number", callback_data="add_field_reg")],
        [InlineKeyboardButton("✉️ Email", callback_data="add_field_email")],
        [InlineKeyboardButton("🔢 Gender", callback_data="add_field_gender")],
        [InlineKeyboardButton("📚 Class/Grade", callback_data="add_field_class")],
        [InlineKeyboardButton("✅ Done Adding", callback_data="fields_done")]
    ]
    await query.edit_message_text(
        f"Step 6.1: **Selected Fields:**\n`{fields_added}`\n\n"
        "Add more fields or click Done:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return CREATE_QUESTION

async def handle_fields_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle done adding fields, proceed to deadline"""
    query = update.callback_query
    await query.answer()
    
    fields_list = context.user_data.get('required_fields', [])
    fields_str = ", ".join(fields_list) if fields_list else "None"
    
    keyboard = [[InlineKeyboardButton("Continue ➜", callback_data="proceed_deadline")]]
    await query.edit_message_text(
        f"Step 6.2: **Required Fields Set**\n\n"
        f"Fields: {fields_str}\n\n"
        f"Next, set a deadline for this assignment:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return CREATE_QUESTION

async def handle_proceed_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Proceed to deadline setup"""
    query = update.callback_query
    await query.answer()
    
    keyboard = [[InlineKeyboardButton("⏭️ No Deadline", callback_data="no_deadline")]]
    await query.edit_message_text(
        "Step 7: **Set Assignment Deadline**\n\n"
        "Send deadline date and time:\n"
        "`YYYY-MM-DD` or `YYYY-MM-DD HH:MM`\n\n"
        "_Example: 2025-12-15 or 2025-12-15 18:00_",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    context.user_data['assign_step'] = 'deadline_date'
    return CREATE_QUESTION

async def handle_no_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Skip deadline setup - FIXED VERSION"""
    query = update.callback_query
    await query.answer()
    
    context.user_data['assign_deadline'] = None
    teacher_id = context.user_data.get('teacher_id')
    
    # Use the message from the query to finalize assignment
    if hasattr(update, 'message') and update.message:
        await finalize_assignment(update, context, teacher_id)
    else:
        # If called from callback, we need to send a message first
        await query.edit_message_text("Finalizing assignment without deadline...")
        # Create a mock update with message for finalize_assignment
        class MockMessage:
            async def reply_text(self, text, **kwargs):
                return await query.message.reply_text(text, **kwargs)
        
        class MockUpdate:
            def __init__(self, query):
                self.message = MockMessage()
        
        mock_update = MockUpdate(query)
        await finalize_assignment(mock_update, context, teacher_id)
    
    return TEACHER_MENU

async def view_my_assignments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View all assignments created by the teacher"""
    query = update.callback_query
    await query.answer()
    
    teacher_id = context.user_data.get('teacher_id')
    if not teacher_id:
        await query.edit_message_text("❌ Session expired. Please login again.")
        return TEACHER_MENU
    
    conn = get_db_connection()
    if not conn:
        await query.edit_message_text("❌ Database connection error.")
        return TEACHER_MENU
    
    c = conn.cursor()
    # Get assignments with submission counts
    c.execute('''SELECT a.assignment_id, a.code, a.title, a.question_type, a.max_score, a.created_at,
                        a.deadline_at, COUNT(s.submission_id) as submission_count
                 FROM assignments a
                 LEFT JOIN submissions s ON a.assignment_id = s.assignment_id
                 WHERE a.teacher_id=%s
                 GROUP BY a.assignment_id, a.code, a.title, a.question_type, a.max_score, a.created_at, a.deadline_at
                 ORDER BY a.created_at DESC''', (teacher_id,))
    assignments = c.fetchall()
    conn.close()
    
    if not assignments:
        keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="teacher_menu")]]
        await query.edit_message_text(
            "📭 **You haven't created any assignments yet.**\n\nCreate your first assignment to get started!",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return TEACHER_MENU
    
    # Format assignments list with clickable buttons
    keyboard = []
    for aid, code, title, qtype, max_score, created_at, deadline_at, submission_count in assignments:
        deadline_indicator = "⏰" if deadline_at and not is_assignment_expired(deadline_at) else ""
        status = "🟢" if not is_assignment_expired(deadline_at) or not deadline_at else "🔴"
        button_text = f"{status} {title} ({submission_count} submissions) {deadline_indicator}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"view_assign_{aid[:8]}")])
    
    keyboard.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="teacher_menu")])
    
    text = "📋 **YOUR ASSIGNMENTS**\n\n"
    text += f"Total: {len(assignments)} assignments\n\n"
    text += "_Click an assignment to view details and submissions_"
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    
    return TEACHER_MENU

async def handle_deactivate_assign(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Deactivate/Activate assignment"""
    query = update.callback_query
    await query.answer()
    
    assignment_id = context.user_data.get('edit_assign_id')
    action = query.data.replace("activate_assign", "").replace("deactivate_assign", "")
    is_active = 0 if "deactivate" in query.data else 1
    
    conn = get_db_connection()
    if not conn:
        await query.edit_message_text("❌ Database connection error.")
        return TEACHER_MENU
    
    c = conn.cursor()
    c.execute('UPDATE assignments SET is_active=%s WHERE assignment_id=%s', (is_active, assignment_id))
    conn.commit()
    conn.close()
    
    status = "✅ ACTIVATED" if is_active else "❌ DEACTIVATED"
    await query.edit_message_text(f"{status} successfully!")
    
    return TEACHER_MENU

async def handle_delete_assign(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete assignment"""
    query = update.callback_query
    await query.answer()
    
    assignment_id = context.user_data.get('edit_assign_id')
    
    conn = get_db_connection()
    if not conn:
        await query.edit_message_text("❌ Database connection error.")
        return TEACHER_MENU
    
    c = conn.cursor()
    # Delete related submissions first
    c.execute('DELETE FROM submissions WHERE assignment_id=%s', (assignment_id,))
    # Then delete assignment
    c.execute('DELETE FROM assignments WHERE assignment_id=%s', (assignment_id,))
    conn.commit()
    conn.close()
    
    keyboard = [[InlineKeyboardButton("Back to Assignments", callback_data="my_assignments")]]
    await query.edit_message_text(
        "🗑️ **Assignment deleted successfully!**",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    
    return TEACHER_MENU

async def handle_edit_assign(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle edit assignment button click"""
    query = update.callback_query
    await query.answer()
    
    assignment_id = context.user_data.get('edit_assign_id')
    
    conn = get_db_connection()
    if not conn:
        await query.edit_message_text("❌ Database connection error.")
        return TEACHER_MENU
    
    c = conn.cursor()
    c.execute('''SELECT title, question, answers, max_score, deadline_at, required_fields, is_active
                 FROM assignments WHERE assignment_id=%s''', (assignment_id,))
    assign = c.fetchone()
    conn.close()
    
    if not assign:
        await query.edit_message_text("❌ Assignment not found.")
        return TEACHER_MENU
    
    title, question, answers, max_score, deadline_at, required_fields_json, is_active = assign
    
    # Parse required fields
    required_fields = []
    try:
        if required_fields_json:
            required_fields = json.loads(required_fields_json)
    except:
        pass
    
    deadline_str = get_deadline_string(deadline_at) if deadline_at else "No deadline"
    required_str = ", ".join(required_fields) if required_fields else "None"
    
    keyboard = [
        [InlineKeyboardButton("✏️ Edit Title", callback_data=f"edit_title_{assignment_id[:8]}")],
        [InlineKeyboardButton("✏️ Edit Question", callback_data=f"edit_question_{assignment_id[:8]}")],
        [InlineKeyboardButton("✏️ Edit Answer", callback_data=f"edit_answer_{assignment_id[:8]}")],
        [InlineKeyboardButton("✏️ Edit Max Score", callback_data=f"edit_score_{assignment_id[:8]}")],
        [InlineKeyboardButton("✏️ Edit Deadline", callback_data=f"edit_deadline_{assignment_id[:8]}")],
        [InlineKeyboardButton("🔙 Back", callback_data="my_assignments")]
    ]
    
    text = f"✏️ **EDIT ASSIGNMENT**\n\n"
    text += f"📌 **Title:** {title}\n"
    text += f"❓ **Question:** {question}\n"
    text += f"📝 **Correct Answer:** {answers}\n"
    text += f"📊 **Max Score:** {max_score}\n"
    text += f"📅 **Deadline:** {deadline_str}\n"
    text += f"📋 **Required Fields:** {required_str}\n\n"
    text += f"Select what you want to edit:"
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    
    return TEACHER_MENU

async def handle_edit_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle edit title"""
    query = update.callback_query
    await query.answer()
    
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="my_assignments")]]
    await query.edit_message_text(
        "✏️ **EDIT TITLE**\n\n"
        "Send the new title for this assignment:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data['edit_mode'] = 'title'
    return CREATE_QUESTION

async def handle_edit_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle edit question"""
    query = update.callback_query
    await query.answer()
    
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="my_assignments")]]
    await query.edit_message_text(
        "✏️ **EDIT QUESTION**\n\n"
        "Send the new question text:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data['edit_mode'] = 'question'
    return CREATE_QUESTION

async def handle_edit_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle edit answer"""
    query = update.callback_query
    await query.answer()
    
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="my_assignments")]]
    await query.edit_message_text(
        "✏️ **EDIT CORRECT ANSWER**\n\n"
        "Send the new correct answer:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data['edit_mode'] = 'answer'
    return CREATE_QUESTION

async def handle_edit_score(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle edit max score"""
    query = update.callback_query
    await query.answer()
    
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="my_assignments")]]
    await query.edit_message_text(
        "✏️ **EDIT MAX SCORE**\n\n"
        "Send the new maximum score (e.g., 5, 10, 20, 100):",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data['edit_mode'] = 'score'
    return CREATE_QUESTION

async def handle_edit_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle edit deadline"""
    query = update.callback_query
    await query.answer()
    
    keyboard = [[InlineKeyboardButton("⏭️ No Deadline", callback_data="no_deadline_edit")]]
    await query.edit_message_text(
        "✏️ **EDIT DEADLINE**\n\n"
        "Send new deadline date and time:\n"
        "`YYYY-MM-DD` or `YYYY-MM-DD HH:MM`\n\n"
        "_Example: 2025-12-15 or 2025-12-15 18:00_",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    context.user_data['edit_mode'] = 'deadline'
    return CREATE_QUESTION

async def handle_edit_field_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text input for edit fields (title, question, answer)"""
    text = update.message.text.strip()
    edit_mode = context.user_data.get('edit_mode')
    assignment_id = context.user_data.get('edit_assign_id')
    
    if not assignment_id:
        await update.message.reply_text("❌ Session error. Please try again.")
        return TEACHER_MENU
    
    conn = get_db_connection()
    if not conn:
        await update.message.reply_text("❌ Database connection error.")
        return TEACHER_MENU
    
    c = conn.cursor()
    
    if edit_mode == 'title':
        # Update title
        c.execute('UPDATE assignments SET title=%s WHERE assignment_id=%s', (text, assignment_id))
        conn.commit()
        await update.message.reply_text("✅ Title updated successfully!")
        
    elif edit_mode == 'question':
        # Update question
        c.execute('UPDATE assignments SET question=%s WHERE assignment_id=%s', (text, assignment_id))
        conn.commit()
        await update.message.reply_text("✅ Question updated successfully!")
        
    elif edit_mode == 'answer':
        # Update answer
        c.execute('UPDATE assignments SET answers=%s WHERE assignment_id=%s', (text, assignment_id))
        conn.commit()
        await update.message.reply_text("✅ Correct answer updated successfully!")
        
    elif edit_mode == 'score':
        # Update max score
        try:
            score = int(text)
            c.execute('UPDATE assignments SET max_score=%s WHERE assignment_id=%s', (score, assignment_id))
            conn.commit()
            await update.message.reply_text(f"✅ Max score updated to {score}!")
        except ValueError:
            await update.message.reply_text("❌ Please enter a valid number for max score")
            conn.close()
            return CREATE_QUESTION
    
    elif edit_mode == 'deadline':
        try:
            # Parse deadline date in format: YYYY-MM-DD HH:MM or YYYY-MM-DD
            deadline_str = text.strip()
            if len(deadline_str) == 10:  # Only date provided
                deadline_str += " 23:59"
            deadline_dt = datetime.strptime(deadline_str, "%Y-%m-%d %H:%M")
            if deadline_dt <= datetime.now():
                await update.message.reply_text("❌ Deadline must be in the future. Try again (format: YYYY-MM-DD HH:MM)")
                conn.close()
                return CREATE_QUESTION
            
            c.execute('UPDATE assignments SET deadline_at=%s WHERE assignment_id=%s', (deadline_dt.isoformat(), assignment_id))
            conn.commit()
            await update.message.reply_text("✅ Deadline updated successfully!")
        except ValueError:
            await update.message.reply_text("❌ Invalid date format. Use: YYYY-MM-DD or YYYY-MM-DD HH:MM")
            conn.close()
            return CREATE_QUESTION
    
    conn.close()
    
    # Clear edit mode and return to menu
    context.user_data['edit_mode'] = None
    context.user_data['edit_assign_id'] = None
    return TEACHER_MENU

async def view_results_analytics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View results and analytics for all student submissions"""
    query = update.callback_query
    await query.answer()
    
    teacher_id = context.user_data.get('teacher_id')
    if not teacher_id:
        await query.edit_message_text("❌ Session expired. Please login again.")
        return TEACHER_MENU
    
    conn = get_db_connection()
    if not conn:
        await query.edit_message_text("❌ Database connection error.")
        return TEACHER_MENU
    
    c = conn.cursor()
    
    # Get all assignments and their submissions
    c.execute('''SELECT a.code, a.title, COUNT(s.submission_id) as student_count, AVG(s.score) as avg_score
                FROM assignments a
                LEFT JOIN submissions s ON a.assignment_id = s.assignment_id
                WHERE a.teacher_id=%s
                GROUP BY a.assignment_id, a.code, a.title
                ORDER BY a.created_at DESC''', (teacher_id,))
    results = c.fetchall()
    
    conn.close()
    
    if not results:
        keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="teacher_menu")]]
        await query.edit_message_text(
            "📊 **NO RESULTS YET**\n\nNo students have submitted answers to your assignments yet.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return TEACHER_MENU
    
    # Format results
    text = "📊 **RESULTS & ANALYTICS**\n\n"
    total_submissions = 0
    total_avg = 0
    valid_assignments = 0
    
    for code, title, student_count, avg_score in results:
        if student_count > 0:
            total_submissions += student_count
            total_avg += (avg_score or 0)
            valid_assignments += 1
            avg_score_rounded = f"{avg_score:.1f}" if avg_score else "0"
            text += f"📌 **{title}**\n"
            text += f"   🔑 Code: `{code}`\n"
            text += f"   👥 Submissions: {student_count}\n"
            text += f"   ⭐ Average Score: {avg_score_rounded}\n\n"
    
    if total_submissions > 0:
        overall_avg = total_avg / valid_assignments
        text += f"\n📈 **OVERALL STATS**\n"
        text += f"   Total Submissions: {total_submissions}\n"
        text += f"   Overall Average: {overall_avg:.1f}\n"
    else:
        text += "\n_No submissions yet for any assignment._"
    
    keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="teacher_menu")]]
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
    conn = get_db_connection()
    if not conn:
        await update.message.reply_text("❌ Database connection error. Please try again.")
        return FIND_ASSIGNMENT
    
    c = conn.cursor()
    c.execute('''SELECT assignment_id, title, question, question_type, max_score, grading_scale, answers, deadline_at, is_active, required_fields
                 FROM assignments WHERE code=%s''', (code,))
    result = c.fetchone()
    conn.close()
    
    if not result:
        await update.message.reply_text(
            "❌ Assignment code not found.\n\n"
            "Please check the code and try again. /start to go back"
        )
        return FIND_ASSIGNMENT
    
    assignment_id, title, question, qtype, max_score, scale, answers, deadline_at, is_active, required_fields_json = result
    
    # Check if assignment is active
    if not is_active:
        await update.message.reply_text(
            "❌ **This assignment is no longer active.**\n\n"
            "Please contact your teacher.\n\n/start to go back"
        )
        return FIND_ASSIGNMENT
    
    # Check if deadline passed
    if deadline_at and is_assignment_expired(deadline_at):
        await update.message.reply_text(
            "❌ **DEADLINE PASSED**\n\n"
            f"This assignment closed on {get_deadline_string(deadline_at)}\n\n"
            "No more submissions are allowed.\n\n/start to go back"
        )
        return FIND_ASSIGNMENT
    
    context.user_data['current_assignment_id'] = assignment_id
    context.user_data['current_assignment_code'] = code
    context.user_data['current_max_score'] = max_score
    context.user_data['current_scale'] = scale
    context.user_data['current_qtype'] = qtype
    context.user_data['correct_answers'] = answers
    
    # Parse required fields from database
    try:
        if required_fields_json:
            context.user_data['required_fields'] = json.loads(required_fields_json)
        else:
            context.user_data['required_fields'] = []
    except:
        context.user_data['required_fields'] = []
    
    deadline_info = f"\n⏰ **Deadline:** {get_deadline_string(deadline_at)}" if deadline_at else ""
    
    keyboard = [
        [InlineKeyboardButton("📝 Submit Answer", callback_data="submit_answer")],
        [InlineKeyboardButton("🔙 Back", callback_data="student_menu")]
    ]
    
    await update.message.reply_text(
        f"✅ **ASSIGNMENT FOUND!**\n\n"
        f"📌 **Title:** {title}\n"
        f"❓ **Question:** {question}\n"
        f"📊 **Max Score:** {max_score}/{scale}{deadline_info}\n\n"
        f"Ready to answer?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    
    return FIND_ASSIGNMENT

async def submit_answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Student submits answer"""
    query = update.callback_query
    await query.answer()
    
    # Check if there are required fields
    required_fields = context.user_data.get('required_fields', [])
    
    if required_fields:
        # Collect required details first
        context.user_data['fill_details_step'] = 0
        context.user_data['student_details'] = {}
        context.user_data['fields_to_fill'] = required_fields
        
        # Ask for first field
        first_field = required_fields[0]
        await query.edit_message_text(
            f"📋 **REQUIRED INFORMATION**\n\n"
            f"Question 1/{len(required_fields)}\n"
            f"Enter your **{first_field}**:"
        )
        return STUDENT_FILL_DETAILS
    else:
        # No required details, go straight to answer submission
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

async def handle_student_fill_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle student filling required details"""
    text = update.message.text.strip()
    step = context.user_data.get('fill_details_step', 0)
    fields_to_fill = context.user_data.get('fields_to_fill', [])
    
    if step < len(fields_to_fill):
        field_name = fields_to_fill[step]
        context.user_data['student_details'][field_name] = text
        step += 1
        context.user_data['fill_details_step'] = step
        
        if step < len(fields_to_fill):
            next_field = fields_to_fill[step]
            await update.message.reply_text(
                f"✅ Saved!\n\n"
                f"Question {step + 1}/{len(fields_to_fill)}\n"
                f"Enter your **{next_field}**:"
            )
            return STUDENT_FILL_DETAILS
        else:
            # All details collected, now ask for answer
            keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="student_menu")]]
            await update.message.reply_text(
                f"✅ **All information saved!**\n\n"
                "📝 **Now, submit your answer:**",
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
    student_details = context.user_data.get('student_details', {})
    
    # Map display names to grade_answer function parameter names
    qtype_map = {
        'Short Answer': 'short',
        'Exact Match': 'exact',
        'Keyword Based': 'keyword',
        'AI Semantic': 'semantic'
    }
    qtype_param = qtype_map.get(qtype, 'short')
    
    score, detail = grade_answer(answer, correct_answers, max_score, qtype_param)
    
    # Save submission
    submission_id = str(uuid.uuid4())
    conn = get_db_connection()
    if not conn:
        await update.message.reply_text("❌ Database connection error. Please try again.")
        return ANSWER_SUBMISSION
    
    c = conn.cursor()
    try:
        # FIX: Properly format student_details as JSON
        student_details_json = json.dumps(student_details) if student_details else json.dumps({})
        
        c.execute('''INSERT INTO submissions
                    (submission_id, assignment_id, student_name, student_id, answer, score, max_score, submitted_at, student_details)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)''',
                  (submission_id, assignment_id, student_name, student_id, answer, score, max_score, datetime.now(), student_details_json))
        conn.commit()
        conn.close()
        
        score_colored = format_score_with_color(score, max_score)
        
        keyboard = [[InlineKeyboardButton("🔍 Find Another", callback_data="find_assignment")],
                    [InlineKeyboardButton("🏠 Back to Menu", callback_data="student_menu")]]
        
        await update.message.reply_text(
            f"✅ **ANSWER SUBMITTED!**\n\n"
            f"📊 **Your Score:** {score_colored}\n"
            f"💡 **Feedback:** {detail}\n\n"
            f"What's next?",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        
        return STUDENT_MAIN
    except Exception as e:
        conn.close()
        await update.message.reply_text(f"❌ Error submitting answer: {str(e)}")
        return ANSWER_SUBMISSION

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
                conn = get_db_connection()
                if conn:
                    c = conn.cursor()
                    c.execute('''INSERT INTO quick_grades
                                (grade_id, teacher_id, question, answer_given, score, max_score, graded_at)
                                VALUES (%s, %s, %s, %s, %s, %s, %s)''',
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
# NAVIGATION HANDLERS - FIXED
# ============================================================================

async def back_to_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Go back to start"""
    query = update.callback_query
    await query.answer()
    
    # Clear user data
    context.user_data.clear()
    
    await query.edit_message_text(
        "👋 Back to Start\n\n"
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
        "👨‍🎓 STUDENT PORTAL\n\n"
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
        "👋 Logged out successfully!\n\n"
        "Type /start to login again"
    )
    return START

# ============================================================================
# HELP COMMAND HANDLER - FIXED NAVIGATION
# ============================================================================

async def show_help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help from callback button - FIXED VERSION"""
    query = update.callback_query
    await query.answer()
    
    keyboard = [[InlineKeyboardButton("🔙 Back to Start", callback_data="back_to_start")]]
    
    help_text = get_comprehensive_help_text()
    
    # Split into chunks if too long (Telegram limit)
    chunks = [help_text[i:i+4096] for i in range(0, len(help_text), 4096)]
    
    for i, chunk in enumerate(chunks):
        if i == 0:
            await query.edit_message_text(
                chunk,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard) if i == len(chunks) - 1 else None
            )
        else:
            await query.message.reply_text(
                chunk,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard) if i == len(chunks) - 1 else None
            )
    
    return START

def get_comprehensive_help_text():
    """Get comprehensive help text with detailed bot information"""
    return """
🤖 **JOSHUAZAZA GRADE BOT v2.1**

═══════════════════════════════════════════════════════════════

📊 **BOT OVERVIEW**

This is an intelligent examination and assignment management system designed for educators and students. Features include:

✨ **Key Features:**
• 🔐 Secure teacher accounts with password protection
• 📝 Multiple question types with AI grading
• ⏰ Assignment deadlines with automatic lockout
• 👥 Student information collection (customizable fields)
• 🎨 Color-coded score display (🟢🟡🔴)
• 🤖 Google Gemini AI for semantic grading
• 📊 Real-time analytics and results
• ✏️ Edit assignments after creation
• 🗑️ Delete assignments and manage submissions

═══════════════════════════════════════════════════════════════

👨‍🏫 **FOR TEACHERS**

**🔑 Account & Login:**
1. Click /start → Select "Teacher Account"
2. Choose "Create New Account" or "Login"
3. Register with: Full Name, Username, Password, Grading Scale
4. Your account is secured and permanently saved

**📝 Creating Assignments:**
1. Click "Create Assignment" from menu
2. Enter: Title → Question → Choose Type → Answer
3. Set maximum score (e.g., 5, 10, 20, 100)
4. Optional: Collect student details (Name, Phone, Email, ID, etc.)
5. Optional: Set deadline (future date/time required)
6. Get unique 8-character code to share with students

**❓ Question Types:**
• **Exact Match**: Student answer must match exactly (✅ Best for definitions)
• **Keyword Based**: Answer must contain key terms (✅ Good for essays)
• **AI Semantic**: Google Gemini AI evaluates meaning (✅ Most flexible)
• **Short Answer**: Teacher grades manually (✅ For complex answers)

**📋 Collecting Student Details:**
• Choose which fields to collect: Name, Phone, Registration, Email, Gender, Class
• Students MUST fill all required details before submitting
• Details are stored with each submission for reference
• View student info in assignment submission list

**⏰ Setting Deadlines:**
• Format: YYYY-MM-DD or YYYY-MM-DD HH:MM (e.g., 2025-12-15 18:00)
• Must be a FUTURE date/time
• Students automatically blocked after deadline
• Prevents late submissions

**📊 Managing Assignments:**
• Click "My Assignments" to see all created assignments
• 🟢 Green = Active | 🔴 Red = Expired deadline
• Click assignment to view details and submissions
• ✏️ Edit: Change title, question, answer, max score, deadline
• 🗑️ Delete: Remove assignment and all student submissions

**📈 Viewing Results:**
• Click "Results & Analytics"
• See submission count per assignment
• View average scores
• Check overall statistics

**⚡ Quick Grade:**
• One-off grading without creating assignments
• Useful for quick assessments or demos

═══════════════════════════════════════════════════════════════

👨‍🎓 **FOR STUDENTS**

**🔍 Finding Assignments:**
1. Click /start → Select "Student"
2. Tap "Find Assignment"
3. Enter 8-character code from your teacher
4. See assignment details

**📋 Required Information:**
• If teacher set required fields, fill them FIRST
• Answer questions one by one
• Cannot skip required fields

**✍️ Submitting Answers:**
• Type your answer (text format)
• Answer is auto-graded instantly (if using AI/Keyword/Exact)
• Receive immediate score and feedback
• Can attempt again with same code

**📊 Your Score:**
• 🟢 GREEN (80%+): Excellent
• 🟡 YELLOW (60-80%): Good
• 🔴 RED (<60%): Needs improvement

**❌ Deadline Warning:**
• ❌ Assignments closed after deadline
• No late submissions allowed
• Contact teacher if deadline issues

═══════════════════════════════════════════════════════════════

⚙️ **SETTINGS & CUSTOMIZATION**

**Teacher Grading Scale:**
• Choose during account creation: 5, 10, 20, 30, or 100 points max
• All assignments use your selected scale

**Question Types:**
• Exact Match: Case-insensitive exact comparison
• Keyword: Checks presence of key terms
• Semantic: AI evaluates meaning and context
• Short Answer: Manual teacher grading

**Required Fields:**
• Name, Phone, Registration Number, Email, Gender, Class/Grade
• Select multiple or none during assignment creation
• Students fill one field at a time

═══════════════════════════════════════════════════════════════

🚨 **TROUBLESHOOTING**

**❓ "Assignment code not found"**
→ Check code spelling and case (codes are uppercase)
→ Ask teacher to verify code is correct
→ Ensure assignment is active (not expired)

**❌ "Deadline passed"**
→ Assignment deadline has closed
→ Contact teacher for extension or new assignment

**🔐 "Forgot password"**
→ Create new teacher account with /start
→ Use different username

**⚠️ "Session expired"**
→ Log in again with /start
→ Use your teacher username and password

**📊 "Score not showing"**
→ Check if using manual grading type
→ Teacher may not have graded yet
→ Auto-graded types show instantly

**🤖 "Gemini error"**
→ AI grading temporarily unavailable
→ System falls back to semantic similarity
→ Answer will be graded with embeddings

═══════════════════════════════════════════════════════════════

📞 **SUPPORT**

**Available Commands:**
/start      - Begin or restart the bot
/help       - Show this comprehensive guide
/logout     - Logout from teacher account

**For issues:**
• Review "My Assignments" in teacher dashboard
• Check submission list for student details
• Verify deadline and required fields
• Re-read assignment details for clarity

═══════════════════════════════════════════════════════════════

✅ **BOT STATUS: v2.1 - FULLY OPERATIONAL**

All features working:
✅ Teacher accounts & authentication
✅ Dynamic question creation
✅ Student details collection
✅ Deadline enforcement
✅ Color-coded scoring
✅ Assignment editing
✅ AI grading with Gemini
✅ Fallback semantic grading
✅ Real-time results
✅ Quick grading mode

═══════════════════════════════════════════════════════════════

Thank you for using the Advanced Telegram Exam Grading Bot! 🎓
"""

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show comprehensive help via /help command"""
    
    help_text = get_comprehensive_help_text()
    
    # Split into chunks if too long (Telegram limit is 4096 chars)
    chunks = [help_text[i:i+4096] for i in range(0, len(help_text), 4096)]
    
    for i, chunk in enumerate(chunks):
        await update.message.reply_text(chunk, parse_mode='Markdown')


# ============================================================================

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors gracefully"""
    print(f"Error: {context.error}")

# ============================================================================
# SIGNAL HANDLERS - GRACEFUL SHUTDOWN
# ============================================================================

def cleanup_db_pool():
    """Cleanup database pool on shutdown"""
    global DB_POOL
    try:
        if DB_POOL is not None:
            DB_POOL.close()
            logger.info("✅ Database pool closed gracefully")
    except Exception as e:
        logger.warning(f"⚠️ Error closing database pool: {e}")

async def post_stop(app):
    """Handle graceful shutdown"""
    logger.info("🛑 Bot shutting down...")
    cleanup_db_pool()
    logger.info("✅ Shutdown complete")

def on_sigterm(signum, frame):
    """Handle SIGTERM signal"""
    logger.info("📍 SIGTERM received, initiating shutdown...")
    cleanup_db_pool()
    sys.exit(0)

def on_sigint(signum, frame):
    """Handle SIGINT signal"""
    logger.info("📍 SIGINT received, initiating shutdown...")
    cleanup_db_pool()
    sys.exit(0)

# ============================================================================
# MAIN - BOT SETUP - FIXED CONVERSATION HANDLER
# ============================================================================

def main():
    """Initialize and run bot"""
    import signal
    
    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, on_sigterm)
    signal.signal(signal.SIGINT, on_sigint)
    
    if not init_db():
        logger.error("❌ Failed to initialize database. Exiting.")
        return
    
    try:
        app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
        
        # Add post-stop callback
        app.post_stop(post_stop)
        
        # Main conversation handler - EXPANDED
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler("start", start)],
            states={
                START: [
                    CallbackQueryHandler(teacher_mode_selector, pattern="^teacher_mode$"),
                    CallbackQueryHandler(direct_teacher_login, pattern="^teacher_login$"),
                    CallbackQueryHandler(student_mode, pattern="^student_mode$"),
                    CallbackQueryHandler(show_help_callback, pattern="^show_help$"),
                    CallbackQueryHandler(back_to_start, pattern="^back_to_start$"),
                ],
                TEACHER_LOGIN: [
                    CallbackQueryHandler(proceed_teacher_login, pattern="^proceed_login$"),
                    CallbackQueryHandler(proceed_teacher_register, pattern="^proceed_register$"),
                    CallbackQueryHandler(back_to_start, pattern="^back_to_start$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_teacher_auth),
            ],
            TEACHER_REGISTER: [
                CallbackQueryHandler(back_to_start, pattern="^back_to_start$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_teacher_auth),
            ],
            TEACHER_MENU: [
                CallbackQueryHandler(create_assignment_start, pattern="^create_assignment$"),
                CallbackQueryHandler(quick_grade_start, pattern="^quick_grade$"),
                CallbackQueryHandler(view_my_assignments, pattern="^my_assignments$"),
                CallbackQueryHandler(view_results_analytics, pattern="^view_results$"),
                CallbackQueryHandler(logout, pattern="^logout$"),
                CallbackQueryHandler(back_to_teacher_menu, pattern="^teacher_menu$"),
                CallbackQueryHandler(handle_view_assign_details, pattern="^view_assign_"),
                CallbackQueryHandler(handle_edit_assign, pattern="^edit_assign_"),
                CallbackQueryHandler(handle_delete_assign, pattern="^delete_assign_"),
                CallbackQueryHandler(handle_deactivate_assign, pattern="^deactivate_assign_"),
                CallbackQueryHandler(handle_deactivate_assign, pattern="^activate_assign_"),
                CallbackQueryHandler(back_to_start, pattern="^back_to_start$"),
            ],
            CREATE_QUESTION: [
                CallbackQueryHandler(handle_assignment_type, pattern="^type_"),
                CallbackQueryHandler(handle_collect_details, pattern="^collect_details_"),
                CallbackQueryHandler(handle_add_field, pattern="^add_field_"),
                CallbackQueryHandler(handle_fields_done, pattern="^fields_done$"),
                CallbackQueryHandler(handle_proceed_deadline, pattern="^proceed_deadline$"),
                CallbackQueryHandler(handle_no_deadline, pattern="^no_deadline$"),
                CallbackQueryHandler(handle_edit_title, pattern="^edit_title_"),
                CallbackQueryHandler(handle_edit_question, pattern="^edit_question_"),
                CallbackQueryHandler(handle_edit_answer, pattern="^edit_answer_"),
                CallbackQueryHandler(handle_edit_score, pattern="^edit_score_"),
                CallbackQueryHandler(handle_edit_deadline, pattern="^edit_deadline_"),
                CallbackQueryHandler(back_to_teacher_menu, pattern="^teacher_menu$"),
                CallbackQueryHandler(back_to_start, pattern="^back_to_start$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_assignment_creation),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_field_text),
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
                CallbackQueryHandler(back_to_start, pattern="^back_to_start$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_assignment_code),
            ],
            STUDENT_FILL_DETAILS: [
                CallbackQueryHandler(back_to_start, pattern="^back_to_start$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_student_fill_details),
            ],
            ANSWER_SUBMISSION: [
                CallbackQueryHandler(back_to_start, pattern="^back_to_start$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_student_answer),
            ],
            QUICK_GRADE_MENU: [
                CallbackQueryHandler(back_to_start, pattern="^back_to_start$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_quick_grade),
            ],
        },
        fallbacks=[CommandHandler("start", start), CommandHandler("help", help_command)],
    )
    
        app.add_handler(CommandHandler("help", help_command))
        app.add_handler(conv_handler)
        app.add_error_handler(error_handler)
        
        logger.info("🚀 Advanced Exam Grading Bot v2 is ONLINE!")
        logger.info("✅ Features: Teacher Accounts | Dynamic Questions | Student Answers")
        logger.info("✅ Features: Quick Grading | Customizable Scales | Proper Navigation")
        logger.info("✅ FIXED: PostgreSQL Database | Teacher login now working properly!")
        logger.info("✅ NEW: Assignment Deadlines | Student Details | Color-Coded Scores")
        logger.info("✅ FIXED: Navigation back buttons now working correctly!")
        logger.info("✅ FIXED: JSON database issues resolved!")
        logger.info("✅ FIXED: Help button now working!")
        logger.info("✅ NEW: Connection pooling and improved error handling!")
        logger.info("\n📍 Waiting for users...\n")
        
        app.run_polling(allowed_updates=Update.ALL_TYPES)
    
    except Exception as e:
        logger.error(f"❌ Unexpected error during bot startup: {e}", exc_info=True)
        cleanup_db_pool()
        raise
    except KeyboardInterrupt:
        logger.info("Bot interrupted by user")
        cleanup_db_pool()
    finally:
        cleanup_db_pool()
        logger.info("✅ Bot shutdown complete")

if __name__ == "__main__":
    main()
