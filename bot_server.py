import os
from datetime import date, datetime
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
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
from google.cloud import firestore

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

ai_client = genai.Client(api_key=GEMINI_KEY) if GEMINI_KEY else None

# אתחול בטוח של Firestore
try:
    db = firestore.Client()
except Exception as e:
    print(f"Firestore init warning: {e}")
    db = None

def get_today_target() -> int:
    if not db:
        return 1800
    try:
        today = str(date.today())
        doc = db.collection("daily_targets").document(today).get()
        if doc.exists:
            return doc.to_dict().get("target_calories", 1800)
    except Exception as e:
        print(f"Error reading target: {e}")
    return 1800

def set_today_target(calories: int):
    if not db:
        return
    try:
        today = str(date.today())
        db.collection("daily_targets").document(today).set({"target_calories": calories})
    except Exception as e:
        print(f"Error setting target: {e}")

def log_meal_db(description: str, calories: int, protein: float):
    if not db:
        return
    try:
        today = str(date.today())
        now_time = datetime.now().strftime("%H:%M")
        db.collection("meals").add({
            "log_date": today,
            "log_time": now_time,
            "description": description,
            "calories": calories,
            "protein_g": protein,
            "created_at": firestore.SERVER_TIMESTAMP
        })
    except Exception as e:
        print(f"Error logging meal: {e}")

def get_today_summary():
    today = str(date.today())
    total_cal = 0
    total_prot = 0.0
    meals_list = []
    
    if db:
        try:
            meals_ref = db.collection("meals").where("log_date", "==", today).stream()
            for m in meals_ref:
                data = m.to_dict()
                total_cal += data.get("calories", 0)
                total_prot += data.get("protein_g", 0.0)
                meals_list.append({
                    "description": data.get("description", ""),
                    "calories": data.get("calories", 0),
                    "protein_g": data.get("protein_g", 0.0),
                    "time": data.get("log_time", "")
                })
        except Exception as e:
            print(f"Error reading summary: {e}")
            
    target = get_today_target()
    remaining = target - total_cal
    return total_cal, total_prot, target, remaining, meals_list

class NutritionExtraction(BaseModel):
    is_meal: bool = Field(description="האם מדובר בדיווח על אוכל/שתייה")
    calories: int = Field(description="הערכת קלוריות כוללת")
    protein_g: float = Field(description="הערכת חלבון כוללת")
    summary: str = Field(description="תיאור תמציתי של המנה")

def analyze_meal(user_text: str) -> NutritionExtraction:
    if not ai_client:
        return NutritionExtraction(is_meal=False, calories=0, protein_g=0, summary="API Key missing")
    prompt = f"המשתמש דיווח: '{user_text}'. הערך קלוריות וחלבון במדויק לישראל. אם זה לא אוכל, סמן is_meal=False."
    try:
        response = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=NutritionExtraction,
            )
        )
        return NutritionExtraction.model_validate_json(response.text)
    except Exception as e:
        print(f"AI error: {e}")
        return NutritionExtraction(is_meal=False, calories=0, protein_g=0, summary="Analysis error")

tg_app = ApplicationBuilder().token(BOT_TOKEN).build() if BOT_TOKEN else None

if tg_app:
    async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("היי! הבוט מחובר דרך Cloud Run 🚀\nשלח מה אכלת או השתמש ב-/status.")

    async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        cal, prot, target, rem, _ = get_today_summary()
        await update.message.reply_text(f"📊 צרכת: {cal}/{target} קק\"ל ({prot:.0f}g חלבון)\nנשאר: {rem} קק\"ל")

    async def text_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
        res = analyze_meal(update.message.text)
        if not res.is_meal:
            await update.message.reply_text("לא זיהיתי ארוחה. נסה שוב.")
            return
        log_meal_db(res.summary, res.calories, res.protein_g)
        _, _, target, rem, _ = get_today_summary()
        await update.message.reply_text(
            f"✅ נרשם: {res.summary}\n🔥 +{res.calories} קק\"ל | 💪 +{res.protein_g:.0f}g חלבון\n💰 נותר להיום: {rem}/{target} קק\"ל"
        )

    tg_app.add_handler(CommandHandler("start", start_cmd))
    tg_app.add_handler(CommandHandler("status", status_cmd))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_msg))

@asynccontextmanager
async def lifespan(app: FastAPI):
    if tg_app:
        await tg_app.initialize()
        await tg_app.start()
        if WEBHOOK_URL:
            await tg_app.bot.set_webhook(url=f"{WEBHOOK_URL}/webhook")
    yield
    if tg_app:
        await tg_app.stop()
        await tg_app.shutdown()

app = FastAPI(lifespan=lifespan)

if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def serve_pwa():
    if os.path.exists("static/index.html"):
        return FileResponse("static/index.html")
    return {"status": "ok", "bot_configured": tg_app is not None}

@app.get("/api/today")
def api_today():
    cal, prot, target, rem, meals = get_today_summary()
    return {
        "total_calories": cal,
        "total_protein": prot,
        "target": target,
        "remaining": rem,
        "meals": meals
    }

@app.post("/webhook")
async def telegram_webhook(request: Request):
    if not tg_app:
        return {"error": "BOT_TOKEN not configured"}
    req_data = await request.json()
    update = Update.de_json(req_data, tg_app.bot)
    await tg_app.process_update(update)
    return {"status": "ok"}