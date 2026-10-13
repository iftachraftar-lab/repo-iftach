import os
import sqlite3
from datetime import date, datetime
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from google import genai
from google.genai import types

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # ה-URL ש-Cloud Run יספק

ai_client = genai.Client(api_key=GEMINI_KEY) if GEMINI_KEY else None

# --- DB מקומי ---
DB_FILE = "nutrition.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS daily_targets (
            log_date TEXT PRIMARY KEY,
            target_calories INTEGER DEFAULT 1800
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS meals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            log_date TEXT,
            log_time TEXT,
            description TEXT,
            calories INTEGER,
            protein_g REAL
        )
    """)
    conn.commit()
    conn.close()

def get_today_target() -> int:
    today = str(date.today())
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT target_calories FROM daily_targets WHERE log_date = ?", (today,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 1800

def set_today_target(calories: int):
    today = str(date.today())
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        INSERT INTO daily_targets (log_date, target_calories)
        VALUES (?, ?)
        ON CONFLICT(log_date) DO UPDATE SET target_calories = excluded.target_calories
    """, (today, calories))
    conn.commit()
    conn.close()

def log_meal_db(description: str, calories: int, protein: float):
    today = str(date.today())
    now_time = datetime.now().strftime("%H:%M")
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        INSERT INTO meals (log_date, log_time, description, calories, protein_g)
        VALUES (?, ?, ?, ?, ?)
    """, (today, now_time, description, calories, protein))
    conn.commit()
    conn.close()

def get_today_summary():
    today = str(date.today())
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT SUM(calories), SUM(protein_g) FROM meals WHERE log_date = ?", (today,))
    row = c.fetchone()
    conn.close()
    total_cal = row[0] if row and row[0] else 0
    total_prot = row[1] if row and row[1] else 0.0
    target = get_today_target()
    remaining = target - total_cal
    return total_cal, total_prot, target, remaining

class NutritionExtraction(BaseModel):
    is_meal: bool = Field(description="האם מדובר בדיווח על אוכל/שתייה")
    calories: int = Field(description="הערכת קלוריות כוללת")
    protein_g: float = Field(description="הערכת חלבון כוללת")
    summary: str = Field(description="תיאור תמציתי של המנה")

def analyze_meal(user_text: str) -> NutritionExtraction:
    prompt = f"המשתמש דיווח: '{user_text}'. הערך קלוריות וחלבון במדויק לישראל. אם זה לא אוכל, סמן is_meal=False."
    response = ai_client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=NutritionExtraction,
        )
    )
    return NutritionExtraction.model_validate_json(response.text)

# --- הגדרת אפליקציית הבוט בטלגרם ---
tg_app = ApplicationBuilder().token(BOT_TOKEN).build()

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("היי! הבוט מחובר דרך Cloud Run 🚀\nשלח מה אכלת או השתמש ב-/status.")

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cal, prot, target, rem = get_today_summary()
    await update.message.reply_text(f"📊 צרכת: {cal}/{target} קק\"ל ({prot:.0f}g חלבון)\nנשאר: {rem} קק\"ל")

async def text_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    res = analyze_meal(update.message.text)
    if not res.is_meal:
        await update.message.reply_text("לא זיהיתי ארוחה. נסה שוב.")
        return
    log_meal_db(res.summary, res.calories, res.protein_g)
    _, _, target, rem = get_today_summary()
    await update.message.reply_text(
        f"✅ נרשם: {res.summary}\n🔥 +{res.calories} קק\"ל | 💪 +{res.protein_g:.0f}g חלבון\n💰 נותר להיום: {rem}/{target} קק\"ל"
    )

tg_app.add_handler(CommandHandler("start", start_cmd))
tg_app.add_handler(CommandHandler("status", status_cmd))
tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_msg))

# --- שרת FastAPI ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    await tg_app.initialize()
    await tg_app.start()
    if WEBHOOK_URL:
        await tg_app.bot.set_webhook(url=f"{WEBHOOK_URL}/webhook")
    yield
    await tg_app.stop()
    await tg_app.shutdown()

app = FastAPI(lifespan=lifespan)

@app.post("/webhook")
async def telegram_webhook(request: Request):
    req_data = await request.json()
    update = Update.de_json(req_data, tg_app.bot)
    await tg_app.process_update(update)
    return {"status": "ok"}

@app.get("/")
def health_check():
    return {"status": "running"}