# 🚀 QUICK START - RUN BOT IN 5 MINUTES

## Your Current Status
✅ Virtual environment created  
✅ Tesseract installed  
✅ .env configured (valid TOKEN & ADMIN_ID)  
⚠️ Need to update python-telegram-bot to v22.5  

---

## 3 COMMANDS TO RUN BOT

```powershell
# 1. Activate virtual environment
.\venv\Scripts\Activate.ps1

# 2. Update telegram-bot (CRITICAL!)
pip install --upgrade python-telegram-bot==22.5

# 3. Run the bot
python bot_v2_teacher_accounts.py
```

**Expected output:**
```
🚀 Advanced Exam Grading Bot v2 is ONLINE!
📍 Waiting for users...
```

---

## Test in Telegram

1. Open Telegram
2. Find your bot
3. Type `/start`
4. Choose mode (Teacher/Student)
5. Test features!

---

## Troubleshooting

**Bot won't start?**
```bash
pip install --upgrade python-telegram-bot==22.5
```

**No response in Telegram?**
- Check TELEGRAM_TOKEN in .env
- Make sure bot is running in terminal
- Try `/start` command

**Module error?**
```bash
pip install -r requirements.txt
```

---

## Next Time

Just run these 3 commands (packages already installed):
```powershell
.\venv\Scripts\Activate.ps1
python bot_v2_teacher_accounts.py
```

---

## Questions?

- **Your .env?** ✅ PERFECT - no changes needed
- **SpeechRecognition package?** Optional (install if you want voice support): `pip install SpeechRecognition==3.10.0`
- **All packages free?** ✅ YES - 100% free, no paid APIs
- **Which bot to run?** ONLY `bot_v2_teacher_accounts.py`

---

**Ready? Run the 3 commands above!** 🎉
