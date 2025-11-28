# 📚 COMPLETE SETUP GUIDE

## Prerequisites
- Python 3.8+ ✅
- Virtual environment created ✅
- Tesseract OCR installed ✅
- .env file with TELEGRAM_TOKEN & ADMIN_ID ✅

---

## Step-by-Step Setup

### Step 1: Activate Virtual Environment

```powershell
cd c:\Users\Joshuazaza\Desktop\telegram-bot
.\venv\Scripts\Activate.ps1
```

You should see `(venv)` in your prompt.

If error about execution policy:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

---

### Step 2: Update Python-Telegram-Bot

```bash
pip install --upgrade python-telegram-bot==22.5
```

Verify:
```bash
pip show python-telegram-bot
```
Should show version 22.5

---

### Step 3: Install Optional Voice Support (Recommended)

```bash
pip install SpeechRecognition==3.10.0
```

---

### Step 4: Verify Configuration

Check `.env` file:
```env
TELEGRAM_TOKEN=6363653586:AAG5nxTNTmEmW0hrMBZ5Vflj-mjXk617_fY  ✅
ADMIN_ID=6897545232  ✅
```

**Status:** ✅ Perfect! No changes needed.

---

### Step 5: Run the Bot

```bash
python bot_v2_teacher_accounts.py
```

Expected output:
```
✅ Tesseract OCR configured for Windows
⏳ Loading AI brain (all-MiniLM-L6-v2)...
✅ AI brain loaded — semantic understanding activated!
🚀 Advanced Exam Grading Bot v2 is ONLINE!
✅ Features: Teacher Accounts | Dynamic Questions | Student Answers
✅ Features: Quick Grading | Customizable Scales | Proper Navigation

📍 Waiting for users...
```

**Keep terminal open!** Bot must run continuously.

---

## Testing the Bot

### Test 1: Teacher Mode
```
/start → Teacher Mode → Register
Name: Your Name
Email: your@email.com
Password: anything
Create Assignment → Select Grading Type → Get Code
```

### Test 2: Student Mode
```
/start → Student Mode → Find Assignment
Enter code from Test 1 → Answer question → Get grade
```

### Test 3: Quick Grade
```
Click "Quick Grade" → Select method → Enter Q & answer → Grade
```

### Test 4: Help
```
Type /help → See comprehensive guide
```

---

## Bot Features

### Teacher Features
- Register & login with email/password
- Create dynamic assignments with 4 question types:
  - **Exact Match**: Answer must match exactly
  - **Keyword**: Answer must contain keywords
  - **AI Semantic**: AI checks meaning (free)
  - **Manual**: You grade manually
- Get unique 8-character codes
- View all student submissions
- Grade manually or auto-grade
- Customize grading scales (0-100+)

### Student Features
- Find assignment by entering code
- Answer questions
- Get instant grades (auto-graded)
- See feedback and scores

### Quick Grade (No Login)
- Grade single answers instantly
- No login required
- Multiple grading methods
- Perfect for demos

---

## Troubleshooting

### Issue: "ModuleNotFoundError: No module named 'telegram'"
```bash
pip install --upgrade python-telegram-bot==22.5
```

### Issue: "Tesseract is not installed"
Path should be: `C:\Program Files\Tesseract-OCR\tesseract.exe`
If different, edit line 20 in bot_v2_teacher_accounts.py

### Issue: "sentence_transformers ImportError"
```bash
pip install sentence-transformers==5.1.2 --upgrade
```

### Issue: Bot starts but doesn't respond in Telegram
- Check TELEGRAM_TOKEN in .env
- Check bot is still running in terminal
- Try /start command
- Wait 10 seconds and try again

### Issue: "Port already in use"
Close any other Python bot instances running

### Issue: Database error
```bash
# Delete old database and let it recreate
del exam_data.db
python bot_v2_teacher_accounts.py
```

---

## Package Information

All packages are **100% FREE** and open-source:
- python-telegram-bot==22.5 ✅
- sentence-transformers==5.1.2 ✅ (AI)
- pytesseract==0.3.13 ✅ (OCR)
- torch/torchaudio/torchvision ✅ (AI)
- SpeechRecognition==3.10.0 ✅ (Optional)
- All others ✅

**No paid services or API keys needed!**

---

## FAQ

**Q: Do I need SpeechRecognition?**
A: No, it's optional. Install for voice support.

**Q: Is .env file correct?**
A: Yes, it's perfect! No changes needed.

**Q: Which bot to run?**
A: ONLY `bot_v2_teacher_accounts.py` (not bot_advanced.py or bot.py)

**Q: Are all packages free?**
A: Yes, 100% free. No paid services.

**Q: Can I close the terminal?**
A: No, bot must keep running. You can minimize it.

**Q: Do I need internet?**
A: Yes, to connect to Telegram.

---

## Next Steps

1. Run the 3 commands from README.md
2. Test the bot in Telegram
3. Create an assignment and test with a student
4. Use `/help` for in-bot guidance

---

**Everything is ready to go!** 🚀
