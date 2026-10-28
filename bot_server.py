import os
import io
import re
import json
import base64
import hashlib
import urllib.request
import urllib.parse
from datetime import date, datetime, timedelta
from typing import List, Optional
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from fastapi import FastAPI, Request, Response, HTTPException, Query
import time
import secrets
import hmac
try:
    from google.oauth2 import id_token as google_id_token
    from google.auth.transport import requests as google_requests
except ImportError:
    google_id_token = None
    google_requests = None
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
from pywebpush import webpush, WebPushException
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
try:
    from PIL import Image, ImageOps
except ImportError:
    Image = None
    ImageOps = None

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
ENV_GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()

project_id = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("PROJECT_ID") or "august-balancer-488508-k9"

def _has_gcp_credentials() -> bool:
    if os.environ.get("K_SERVICE") or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        return True
    appdata = os.environ.get("APPDATA", "")
    adc_path = os.path.join(appdata, "gcloud", "application_default_credentials.json")
    return os.path.exists(adc_path)

db = None
if _has_gcp_credentials():
    try:
        db = firestore.Client(project=project_id)
        print(f"INFO: Firestore initialized for project: {project_id}")
    except Exception as e:
        print(f"ERROR: Firestore initialization failed: {e}")
        db = None
else:
    print("INFO: No local GCP credentials found; using fast in-memory storage for local preview.")

ai_client = genai.Client(api_key=GEMINI_KEY) if GEMINI_KEY else None


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------

class MealItemDetail(BaseModel):
    name: str = Field(description="שם הפריט הספציפי, למשל 'חזה עוף 200 גרם', 'כוס אורז', או 'סלט ירקות'")
    calories: int = Field(description="הערכת קלוריות מדויקת לפריט זה")
    protein_g: float = Field(default=0.0, description="הערכת חלבון בגרמים לפריט זה")


class NutritionExtraction(BaseModel):
    is_meal: bool = Field(description="האם מדובר בדיווח על אוכל/שתייה")
    calories: int = Field(description="הערכת קלוריות כוללת")
    protein_g: float = Field(description="הערכת חלבון כוללת בגרמים")
    summary: str = Field(description="תיאור תמציתי וברור של המנה בעברית")
    items: List[MealItemDetail] = Field(default_factory=list, description="פירוט של כל פריט בארוחה עם הקלוריות והחלבון הנפרדים שלו")
    confidence_score: int = Field(default=88, description="ציון ביטחון של המודל בזיהוי המנה והכמויות בין 0 ל-100")
    portion_estimation_method: str = Field(default="", description="כיצד הוערך נפח/משקל המנה (למשל יחס לגודל צלחת סטנדרטית, כלי הגשה, סכו״ם, עומק קערה או משקל יחידה)")
    visual_cues: str = Field(default="", description="סימנים חזותיים שזוהו בתמונה: מרקם, ברק שמן/רוטב, השחמה, צפיפות מרכיבים וזווית צילום")
    hidden_calories_note: str = Field(default="", description="הערכת קלוריות סמויות שנלקחו בחשבון (כגון שמן בישול/טיגון, טחינה, רוטב או חמאה)")
    applied_calibration_rules: List[str] = Field(default_factory=list, description="אילו חוקי כיול של האדמין הופעלו בניתוח תמונה זו")


class WorkoutItem(BaseModel):
    activity_name: str = Field(description="שם הפעילות או האימון בעברית, למשל ריצה 5 ק״מ, אימון כוח משקולות, הליכה מהירה")
    activity_type: str = Field(default="other", description="סוג הפעילות: run, strength, walk, cycling, swimming, hiit, other")
    duration_min: int = Field(default=30, description="משך האימון בדקות")
    distance_km: Optional[float] = Field(default=None, description="מרחק בקילומטרים אם רלוונטי, או null")
    burned_calories: int = Field(description="הערכת קלוריות שנשרפו באימון על בסיס משקל המתאמן ועוצמת הפעילות")
    notes: Optional[str] = Field(default="", description="הערות או דגש קצר")
    is_workout: bool = Field(default=True, description="האם הטקסט אכן מתאר פעילות גופנית")


class WorkoutExtraction(BaseModel):
    workouts: List[WorkoutItem] = Field(default_factory=list, description="רשימת האימונים שזוהו בטקסט")
    total_burned_calories: int = Field(default=0, description="סך הקלוריות שנשרפו בכל האימונים שזוהו")
    summary: str = Field(description="סיכום תמציתי וברור של הפעילות הגופנית בעברית")
    is_workout: bool = Field(default=True, description="האם זוהתה פעילות גופנית")


class AIWorkoutRequest(BaseModel):
    text: str
    log_date: Optional[str] = None


class ManualWorkoutRequest(BaseModel):
    activity_name: str
    burned_calories: int = Field(ge=1, le=10000)
    duration_min: int = Field(default=30, ge=1, le=1440)
    distance_km: Optional[float] = Field(default=None, ge=0.0, le=500.0)
    activity_type: str = Field(default="other")
    notes: Optional[str] = ""
    log_date: Optional[str] = None
    log_time: Optional[str] = None


class PhotoWorkoutRequest(BaseModel):
    image_base64: str
    mime_type: str = "image/jpeg"
    caption: Optional[str] = ""
    log_date: Optional[str] = None


class GoogleFitSyncRequest(BaseModel):
    access_token: Optional[str] = ""
    log_date: Optional[str] = None


class DailyCoachInsight(BaseModel):
    score: int = Field(description="ציון תזונה יומי בין 0 ל-100 בהתאם לעמידה ביעד הקלוריות והחלבון")
    headline: str = Field(description="כותרת קצרה, אישית ומעודדת בעברית")
    feedback: str = Field(description="ניתוח תמציתי וחכם של היום הנוכחי והמגמה השבועית בעברית")
    next_meal_suggestion: str = Field(description="המלצה קונקרטית לארוחה הבאה או להמשך היום בהתאם ליתרת הקלוריות והחלבון")
    tip: str = Field(description="טיפ פרקטי אחד לשיפור או התמדה")


class RecipeItem(BaseModel):
    name: str = Field(description="שם המתכון בעברית")
    category: str = Field(description="קטגוריה: בוקר / צהריים / ערב / נשנוש")
    prep_time: str = Field(description="זמן הכנה (למשל: 10 דקות)")
    description: str = Field(description="תיאור קצר ומגרה של המנה")
    calories: int = Field(description="קלוריות למנה")
    protein_g: float = Field(description="גרם חלבון למנה")
    ingredients: List[str] = Field(description="רשימת מצרכים מדויקת")
    preparation_steps: List[str] = Field(description="שלבי הכנה קצרים וברורים")


class RecipeSuggestionList(BaseModel):
    recipes: List[RecipeItem]


class ManualMealRequest(BaseModel):
    description: str
    calories: int
    protein_g: float
    log_date: Optional[str] = None
    log_time: Optional[str] = None


class AIMealRequest(BaseModel):
    text: str
    log_date: Optional[str] = None


class PhotoMealRequest(BaseModel):
    image_base64: str
    mime_type: str = "image/jpeg"
    caption: Optional[str] = ""
    log_date: Optional[str] = None


class VoiceMealRequest(BaseModel):
    audio_base64: str
    mime_type: str = "audio/webm"
    caption: Optional[str] = ""
    log_date: Optional[str] = None


class CoachAnalyzeRequest(BaseModel):
    log_date: Optional[str] = None


class GoogleAuthRequest(BaseModel):
    credential: Optional[str] = None
    email: Optional[str] = None
    name: Optional[str] = None
    picture: Optional[str] = None


class AuthConfigRequest(BaseModel):
    google_client_id: str


class TargetUpdateRequest(BaseModel):
    target_calories: int = Field(ge=500, le=10000)
    target_protein: int = Field(default=120, ge=20, le=500)
    daily_ai_limit: Optional[int] = Field(default=None, ge=0, le=1000)


class WoltDishItem(BaseModel):
    dish_name: str = Field(description="שם המנה הספציפית בעברית")
    restaurant_name: str = Field(description="שם המסעדה או הרשת ב-Wolt בעברית")
    category: str = Field(description="קטגוריה: סלטים וקערות / סושי ואסיאתי / בשרים והמבורגר / ים-תיכוני / כריכים וחלבי")
    calories: int = Field(description="הערכת קלוריות למנה")
    protein_g: float = Field(description="הערכת חלבון בגרמים למנה")
    description: str = Field(description="פירוט קצר של מרכיבי המנה")
    smart_order_tip: str = Field(description="טיפ פרקטי מה לסמן או לבקש בהזמנה ב-Wolt כדי לחסוך קלוריות או להגדיל חלבון")
    search_query: str = Field(description="מילת החיפוש המדויקת לפתיחה ישירה ב-Wolt (שם המסעדה והמנה)")


class WoltDishList(BaseModel):
    dishes: List[WoltDishItem]


class WoltSuggestRequest(BaseModel):
    remaining_calories: int = 600
    remaining_protein: float = 40.0
    city_slug: Optional[str] = "tel-aviv"
    lat: Optional[float] = None
    lon: Optional[float] = None
    query: Optional[str] = ""


class WoltMenuScanRequest(BaseModel):
    image_base64: str
    mime_type: str = "image/jpeg"
    remaining_calories: int = 600
    remaining_protein: float = 40.0
    city_slug: Optional[str] = "tel-aviv"


class SupermarketScanResult(BaseModel):
    product_name: str = Field(description="שם המוצר והמותג בעברית")
    verdict: str = Field(description="פסק דין קצר: מומלץ מאוד ✅ / אפשרי במידה ⚖️ / פחות מומלץ ⚠️")
    score: int = Field(description="ציון התאמה בין 1 ל-100 עבור היתרה היומית ויחס חלבון-קלוריות")
    calories_per_100g: int = Field(description="קלוריות ל-100 גרם")
    protein_per_100g: float = Field(description="חלבון בגרמים ל-100 גרם")
    serving_description: str = Field(description="תיאור מנה מומלצת או יחידה (למשל גביע 200 גרם, חטיף 50 גרם, 2 פרוסות)")
    serving_calories: int = Field(description="קלוריות למנה/יחידה")
    serving_protein_g: float = Field(description="חלבון בגרמים למנה/יחידה")
    fits_today_budget: bool = Field(description="האם מנה אחת נכנסת ביתרת הקלוריות של המשתמש להיום")
    explanation: str = Field(description="הסבר קצר וחד בעברית למה כדאי או לא כדאי לקנות בהתחשב ביתרה היומית וברכיבים")
    better_alternative: Optional[str] = Field(default="", description="חלופה עדיפה בסופר הישראלי אם המוצר פחות מומלץ")


class SupermarketScanRequest(BaseModel):
    image_base64: str
    mime_type: str = "image/jpeg"
    remaining_calories: int = 600
    remaining_protein: float = 40.0


class FridgeRecipeRequest(BaseModel):
    image_base64: Optional[str] = None
    mime_type: str = "image/jpeg"
    ingredients_text: Optional[str] = ""
    remaining_calories: int = 600
    remaining_protein: float = 40.0


class RecipePromptRequest(BaseModel):
    remaining_calories: int = 600
    remaining_protein: float = 40.0
    preference: Optional[str] = ""


class MealUpdateRequest(BaseModel):
    description: str
    calories: int = Field(ge=0, le=10000)
    protein_g: float = Field(ge=0.0, le=500.0)
    items: Optional[List[MealItemDetail]] = None
    log_date: Optional[str] = None
    log_time: Optional[str] = None


class FavoriteMealRequest(BaseModel):
    label: str
    calories: int = Field(ge=0, le=10000)
    protein_g: float = Field(ge=0.0, le=500.0)
    icon: Optional[str] = "⭐"


class WeightLogRequest(BaseModel):
    weight_kg: float = Field(ge=25.0, le=350.0)
    log_date: Optional[str] = None
    note: Optional[str] = ""


class PushKeys(BaseModel):
    p256dh: str
    auth: str


class PushSubscribeRequest(BaseModel):
    endpoint: str
    keys: PushKeys
    user_agent: Optional[str] = ""


class PushUnsubscribeRequest(BaseModel):
    endpoint: str


class PushMessageRequest(BaseModel):
    title: Optional[str] = None
    body: Optional[str] = None


class AdminCreateUserRequest(BaseModel):
    email: str
    name: Optional[str] = ""
    target_calories: int = Field(default=1800, ge=500, le=10000)
    target_protein: int = Field(default=120, ge=20, le=500)
    daily_ai_limit: int = Field(default=15, ge=0, le=1000)


class AdminUpdateUserStatusRequest(BaseModel):
    is_blocked: Optional[bool] = None
    is_admin: Optional[bool] = None
    is_approved: Optional[bool] = None
    daily_ai_limit: Optional[int] = Field(default=None, ge=0, le=1000)


class VisionTestAnalyzeRequest(BaseModel):
    image_base64: str
    mime_type: str = "image/jpeg"
    caption: Optional[str] = ""


class VisionCalibrateRequest(BaseModel):
    log_id: Optional[str] = ""
    meal_id: Optional[str] = ""
    target_user_id: Optional[str] = ""
    rule_text: Optional[str] = ""
    corrected_summary: Optional[str] = ""
    corrected_calories: Optional[int] = None
    corrected_protein_g: Optional[float] = None
    update_customer_meal: bool = True


DEFAULT_VISION_CALIBRATION_RULES = [
    {
        "id": "rule-plate-scale",
        "rule_text": "צלחת מנה עיקרית ישראלית סטנדרטית היא בקוטר 26-28 ס״מ: ערימת אורז/פחמימה שמכסה שליש צלחת שטוחה שווה לכ-150-180 גרם מבושל (כ-200-240 קק״ל), ולא 350 גרם.",
        "category": "גודל מנה וצלחות",
        "created_by": "system",
    },
    {
        "id": "rule-hidden-oil-tahini",
        "rule_text": "ברק חזק על ירקות קלויים/מוקפצים או שניצל מטוגן מעיד על ספיגת שמן: הוסף 60-110 קק״ל לשמן בישול/טיגון. זילוף טחינה מעל מנה שווה לכ-70-100 קק״ל לכף.",
        "category": "שמן ורטבים סמויים",
        "created_by": "system",
    },
    {
        "id": "rule-israeli-packaging",
        "rule_text": "במוצרי חלב ישראליים באריזה (גביע קוטג׳ תנובה/טרה = 250 גרם קופסה שלמה, יוגורט PRO = 150-200 גרם): אם מצולם גביע פתוח ליד צלחת ללא הערה אחרת, הערך לפי הכמות שנלקחה לצלחת או חצי גביע (125 גרם) אלא אם הגביע נאכל כולו.",
        "category": "אריזות ומוצרים ישראליים",
        "created_by": "system",
    },
]


# ---------------------------------------------------------------------------
# Multi-User & Auth Helpers
# ---------------------------------------------------------------------------

DEFAULT_FAVORITES = [
    {"id": "fav-coffee", "icon": "☕", "label": "הפוך קטן", "calories": 80, "protein_g": 4.0},
    {"id": "fav-eggs", "icon": "🥚", "label": "2 ביצים וקוטג׳", "calories": 260, "protein_g": 26.0},
    {"id": "fav-chicken", "icon": "🍗", "label": "חזה עוף ואורז", "calories": 480, "protein_g": 45.0},
    {"id": "fav-tuna", "icon": "🐟", "label": "סלט טונה וביצה", "calories": 350, "protein_g": 36.0},
    {"id": "fav-pro", "icon": "🥤", "label": "יוגורט PRO / שייק", "calories": 160, "protein_g": 20.0},
]

# Local fallback state when running locally without GCP credentials
_LOCAL_TARGETS_BY_USER: dict = {"default": {"target_calories": 1800, "target_protein": 120}}
_LOCAL_MEALS: List[dict] = []
_LOCAL_WORKOUTS: List[dict] = []
_LOCAL_FAVORITES: List[dict] = [dict(f, user_id="default") for f in DEFAULT_FAVORITES]
_LOCAL_WEIGHTS: List[dict] = []
_LOCAL_VAPID: Optional[dict] = None
_LOCAL_PUSH_SUBS: List[dict] = []
_LOCAL_USERS: dict = {}
_LOCAL_TG_LINKS: dict = {}
_LOCAL_AUTH_CONFIG: dict = {"google_client_id": ENV_GOOGLE_CLIENT_ID, "primary_user_id": "default"}
_LOCAL_VISION_RULES: List[dict] = [dict(r) for r in DEFAULT_VISION_CALIBRATION_RULES]
_LOCAL_VISION_LOGS: List[dict] = []


def _normalize_user_id(raw_uid: Optional[str]) -> str:
    if not raw_uid:
        return "default"
    cleaned = re.sub(r"[^a-zA-Z0-9_\-]", "", raw_uid.strip())[:64]
    return cleaned if cleaned else "default"


SESSION_TOKEN_TTL_SECONDS = 60 * 86400  # 60 days
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()


def get_server_session_secret() -> str:
    env_sec = os.getenv("SESSION_SECRET", "").strip()
    if env_sec:
        return env_sec
    sec = _LOCAL_AUTH_CONFIG.get("session_secret", "")
    if sec:
        return sec
    if db:
        try:
            doc_ref = db.collection("app_config").document("auth")
            doc = doc_ref.get()
            if doc.exists and doc.to_dict().get("session_secret"):
                sec = doc.to_dict()["session_secret"].strip()
                _LOCAL_AUTH_CONFIG["session_secret"] = sec
                return sec
            new_sec = secrets.token_hex(32)
            doc_ref.set({"session_secret": new_sec}, merge=True)
            _LOCAL_AUTH_CONFIG["session_secret"] = new_sec
            return new_sec
        except Exception as e:
            print(f"ERROR get_server_session_secret: {e}")
    if not sec:
        sec = secrets.token_hex(32)
        _LOCAL_AUTH_CONFIG["session_secret"] = sec
    return sec


def create_session_token(user_id: str) -> str:
    uid = _normalize_user_id(user_id)
    now_ts = int(time.time())
    secret = get_server_session_secret()
    payload = f"{uid}:{now_ts}"
    sig = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    return f"v1.{uid}.{now_ts}.{sig}"


def verify_session_token(token: Optional[str]) -> Optional[str]:
    if not token or not isinstance(token, str):
        return None
    token = token.strip()
    if token.startswith("Bearer "):
        token = token[7:].strip()
    parts = token.split(".")
    if len(parts) != 4 or parts[0] != "v1":
        return None
    _, uid, ts_str, sig = parts
    try:
        ts = int(ts_str)
    except ValueError:
        return None
    if time.time() - ts > SESSION_TOKEN_TTL_SECONDS:
        return None
    secret = get_server_session_secret()
    payload = f"{uid}:{ts}"
    expected_sig = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    if hmac.compare_digest(sig, expected_sig):
        return _normalize_user_id(uid)
    return None


def get_telegram_webhook_secret() -> str:
    global TELEGRAM_WEBHOOK_SECRET
    if TELEGRAM_WEBHOOK_SECRET:
        return TELEGRAM_WEBHOOK_SECRET
    sec = _LOCAL_AUTH_CONFIG.get("tg_webhook_secret", "")
    if sec:
        TELEGRAM_WEBHOOK_SECRET = sec
        return sec
    if db:
        try:
            doc_ref = db.collection("app_config").document("telegram")
            doc = doc_ref.get()
            if doc.exists and doc.to_dict().get("webhook_secret"):
                sec = doc.to_dict()["webhook_secret"].strip()
                _LOCAL_AUTH_CONFIG["tg_webhook_secret"] = sec
                TELEGRAM_WEBHOOK_SECRET = sec
                return sec
            new_sec = secrets.token_hex(24)
            doc_ref.set({"webhook_secret": new_sec}, merge=True)
            _LOCAL_AUTH_CONFIG["tg_webhook_secret"] = new_sec
            TELEGRAM_WEBHOOK_SECRET = new_sec
            return new_sec
        except Exception as e:
            print(f"ERROR get_telegram_webhook_secret: {e}")
    if not sec:
        sec = secrets.token_hex(24)
        _LOCAL_AUTH_CONFIG["tg_webhook_secret"] = sec
        TELEGRAM_WEBHOOK_SECRET = sec
    return sec


def get_request_user_id(request: Optional[Request] = None) -> str:
    if not request:
        return "default"

    # 1. Check Authorization Bearer header
    auth_hdr = request.headers.get("Authorization", "")
    if auth_hdr.lower().startswith("bearer "):
        uid = verify_session_token(auth_hdr[7:])
        if uid and uid != "default":
            return uid

    # 2. Check X-Session-Token header
    session_hdr = request.headers.get("X-Session-Token", "")
    if session_hdr:
        uid = verify_session_token(session_hdr)
        if uid and uid != "default":
            return uid

    # 3. Check session_token cookie
    cookie_token = request.cookies.get("session_token", "")
    if cookie_token:
        uid = verify_session_token(cookie_token)
        if uid and uid != "default":
            return uid

    # 4. Check query param session_token fallback
    param_token = request.query_params.get("session_token", "")
    if param_token:
        uid = verify_session_token(param_token)
        if uid and uid != "default":
            return uid

    return "default"


def _doc_matches_user(doc_data: dict, user_id: str) -> bool:
    uid = _normalize_user_id(user_id)
    doc_uid = doc_data.get("user_id") or "default"
    return doc_uid == uid


def _make_user_id_from_email(email: str) -> str:
    clean_email = email.strip().lower()
    digest = hashlib.sha256(clean_email.encode("utf-8")).hexdigest()[:16]
    prefix = re.sub(r"[^a-z0-9]", "", clean_email.split("@")[0])[:12] or "user"
    return f"u_{prefix}_{digest}"


def _make_link_code(user_id: str, email: str) -> str:
    raw = f"{user_id}:{email.lower().strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:6].upper()


def get_auth_config_db() -> dict:
    client_id = ENV_GOOGLE_CLIENT_ID or _LOCAL_AUTH_CONFIG.get("google_client_id", "")
    primary_uid = _LOCAL_AUTH_CONFIG.get("primary_user_id", "default")
    if db:
        try:
            doc = db.collection("app_config").document("auth").get()
            if doc.exists:
                data = doc.to_dict()
                if not client_id and data.get("google_client_id"):
                    client_id = data["google_client_id"].strip()
                if data.get("primary_user_id"):
                    primary_uid = data["primary_user_id"]
        except Exception as e:
            print(f"ERROR get_auth_config_db: {e}")
    return {"google_client_id": client_id, "primary_user_id": primary_uid}


def set_auth_config_db(google_client_id: str) -> dict:
    clean_cid = (google_client_id or "").strip()
    _LOCAL_AUTH_CONFIG["google_client_id"] = clean_cid
    if db:
        try:
            db.collection("app_config").document("auth").set({
                "google_client_id": clean_cid,
                "updated_at": firestore.SERVER_TIMESTAMP,
            }, merge=True)
        except Exception as e:
            print(f"ERROR set_auth_config_db: {e}")
    return get_auth_config_db()


def verify_google_id_token(id_token: str) -> dict:
    cfg = get_auth_config_db()
    expected_cid = cfg.get("google_client_id", "").strip()

    if google_id_token and google_requests:
        try:
            req = google_requests.Request()
            id_info = google_id_token.verify_oauth2_token(
                id_token.strip(),
                req,
                audience=expected_cid if expected_cid else None,
            )
            if not id_info.get("email"):
                raise ValueError("Invalid Google token: missing email")
            return {
                "email": id_info["email"].strip().lower(),
                "name": id_info.get("name") or id_info["email"].split("@")[0],
                "picture": id_info.get("picture") or "",
                "sub": id_info.get("sub") or "",
            }
        except Exception as e:
            # Fall back to tokeninfo if local cert cache fails
            pass

    url = f"https://oauth2.googleapis.com/tokeninfo?id_token={urllib.parse.quote(id_token.strip())}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=8) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not data.get("email"):
        raise ValueError("Invalid Google token: missing email")
    if expected_cid and data.get("aud") and data.get("aud") != expected_cid:
        raise ValueError("Google token audience mismatch")
    return {
        "email": data["email"].strip().lower(),
        "name": data.get("name") or data["email"].split("@")[0],
        "picture": data.get("picture") or "",
        "sub": data.get("sub") or "",
    }


def claim_legacy_data_for_user(user_id: str) -> dict:
    uid = _normalize_user_id(user_id)
    if uid == "default":
        return {"claimed_meals": 0, "claimed_weights": 0, "claimed_favorites": 0}

    claimed_meals = 0
    claimed_weights = 0
    claimed_favorites = 0

    if not db:
        for m in _LOCAL_MEALS:
            if (m.get("user_id") or "default") == "default":
                m["user_id"] = uid
                claimed_meals += 1
        for w in _LOCAL_WEIGHTS:
            if (w.get("user_id") or "default") == "default":
                w["user_id"] = uid
                claimed_weights += 1
        for f in _LOCAL_FAVORITES:
            if (f.get("user_id") or "default") == "default":
                f["user_id"] = uid
                claimed_favorites += 1
        if "default" in _LOCAL_TARGETS_BY_USER and uid not in _LOCAL_TARGETS_BY_USER:
            _LOCAL_TARGETS_BY_USER[uid] = dict(_LOCAL_TARGETS_BY_USER["default"])
        _LOCAL_AUTH_CONFIG["primary_user_id"] = uid
        return {
            "claimed_meals": claimed_meals,
            "claimed_weights": claimed_weights,
            "claimed_favorites": claimed_favorites,
        }

    try:
        # 1. Migrate legacy meals (where user_id is missing or 'default')
        for doc in db.collection("meals").stream():
            d = doc.to_dict()
            if (d.get("user_id") or "default") == "default":
                doc.reference.set({"user_id": uid}, merge=True)
                claimed_meals += 1

        # 2. Migrate legacy weight_logs
        for doc in db.collection("weight_logs").stream():
            d = doc.to_dict()
            if (d.get("user_id") or "default") == "default":
                doc.reference.set({"user_id": uid}, merge=True)
                claimed_weights += 1

        # 3. Migrate legacy favorites
        for doc in db.collection("favorites").stream():
            d = doc.to_dict()
            if (d.get("user_id") or "default") == "default":
                doc.reference.set({"user_id": uid}, merge=True)
                claimed_favorites += 1

        # 4. Copy default daily_targets if user doesn't have custom targets yet
        user_target_ref = db.collection("daily_targets").document(f"user_{uid}")
        if not user_target_ref.get().exists:
            def_doc = db.collection("daily_targets").document("default").get()
            if def_doc.exists:
                def_data = def_doc.to_dict()
                user_target_ref.set({
                    "user_id": uid,
                    "target_calories": def_data.get("target_calories", 1800),
                    "target_protein": def_data.get("target_protein", 120),
                    "updated_at": firestore.SERVER_TIMESTAMP,
                }, merge=True)

        # 5. Migrate legacy push_subscriptions
        for doc in db.collection("push_subscriptions").stream():
            d = doc.to_dict()
            if (d.get("user_id") or "default") == "default":
                doc.reference.set({"user_id": uid}, merge=True)

        # Record primary user if not set
        auth_ref = db.collection("app_config").document("auth")
        auth_doc = auth_ref.get()
        if not auth_doc.exists or not auth_doc.to_dict().get("primary_user_id") or auth_doc.to_dict().get("primary_user_id") == "default":
            auth_ref.set({"primary_user_id": uid}, merge=True)
            _LOCAL_AUTH_CONFIG["primary_user_id"] = uid
    except Exception as e:
        print(f"ERROR claim_legacy_data_for_user: {e}")

    return {
        "claimed_meals": claimed_meals,
        "claimed_weights": claimed_weights,
        "claimed_favorites": claimed_favorites,
    }


OWNER_ADMIN_EMAILS = {
    "iftach.raftar@gmail.com",
    "iftachraftar@gmail.com",
    *[e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()],
}


def is_owner_email(email: str) -> bool:
    clean = (email or "").strip().lower()
    if not clean:
        return False
    if clean in OWNER_ADMIN_EMAILS:
        return True
    if clean.endswith("@gmail.com"):
        local_part = clean.split("@")[0].replace(".", "")
        if f"{local_part}@gmail.com" in OWNER_ADMIN_EMAILS:
            return True
    return False


def is_admin_user_id(user_id: str) -> bool:
    uid = _normalize_user_id(user_id)
    if not uid or uid == "default":
        return False
    user_doc = {}
    if not db:
        user_doc = _LOCAL_USERS.get(uid, {})
    else:
        try:
            doc = db.collection("users").document(uid).get()
            if doc.exists:
                user_doc = doc.to_dict() or {}
        except Exception as e:
            print(f"ERROR is_admin_user_id: {e}")

    if user_doc.get("admin_revoked") is True:
        return False
    if user_doc.get("admin_granted_by_owner") is True:
        return True
    if is_owner_email(user_doc.get("email", "")):
        return True
    for owner_em in OWNER_ADMIN_EMAILS:
        if _make_user_id_from_email(owner_em) == uid:
            return True
    return False


DEFAULT_DAILY_AI_LIMIT = 15


def require_admin_user(request: Request) -> str:
    uid = get_request_user_id(request)
    if not is_admin_user_id(uid):
        raise HTTPException(status_code=403, detail="גישה חסומה: עמוד זה מורשה למנהל המערכת בלבד")
    return uid


def get_user_doc_db(user_id: str) -> dict:
    uid = _normalize_user_id(user_id)
    if not uid or uid == "default":
        return {}
    if not db:
        return dict(_LOCAL_USERS.get(uid, {}))
    try:
        snap = db.collection("users").document(uid).get()
        if snap.exists:
            return snap.to_dict() or {}
    except Exception as e:
        print(f"ERROR get_user_doc_db: {e}")
    return {}


def require_approved_user(request: Request) -> str:
    uid = get_request_user_id(request)
    if uid == "default":
        raise HTTPException(status_code=401, detail="יש להתחבר עם חשבון Google תחילה")
    if is_admin_user_id(uid):
        return uid
    udoc = get_user_doc_db(uid)
    if udoc.get("is_blocked"):
        raise HTTPException(status_code=403, detail="החשבון שלך נחסם על ידי מנהל המערכת")
    if not udoc.get("approved_by_owner", False):
        raise HTTPException(status_code=403, detail="חשבונך ממתין לאישור מנהל המערכת")
    return uid


def check_and_increment_ai_quota(user_id: str) -> None:
    uid = _normalize_user_id(user_id)
    if is_admin_user_id(uid):
        # Owner / Admin has unlimited AI quota, still track usage count for visibility
        today_str = str(date.today())
        udoc = get_user_doc_db(uid)
        used = int(udoc.get("ai_calls_today") or 0) if udoc.get("ai_quota_date") == today_str else 0
        if not db:
            if uid in _LOCAL_USERS:
                _LOCAL_USERS[uid]["ai_quota_date"] = today_str
                _LOCAL_USERS[uid]["ai_calls_today"] = used + 1
        else:
            try:
                db.collection("users").document(uid).set({
                    "ai_quota_date": today_str,
                    "ai_calls_today": used + 1,
                }, merge=True)
            except Exception:
                pass
        return

    today_str = str(date.today())
    udoc = get_user_doc_db(uid)
    if udoc.get("is_blocked"):
        raise HTTPException(status_code=403, detail="החשבון שלך נחסם על ידי מנהל המערכת")
    if not udoc.get("approved_by_owner", False):
        raise HTTPException(status_code=403, detail="חשבונך ממתין לאישור מנהל המערכת")

    limit = int(udoc.get("daily_ai_limit") if udoc.get("daily_ai_limit") is not None else DEFAULT_DAILY_AI_LIMIT)
    used = int(udoc.get("ai_calls_today") or 0) if udoc.get("ai_quota_date") == today_str else 0

    if used >= limit:
        raise HTTPException(
            status_code=429,
            detail=f"הגעת למכסת ניתוחי ה-AI היומית שלך ({limit} פעולות ליום). תוכל להמשיך להזין ארוחות ידנית או מהמועדפים!",
        )

    if not db:
        if uid in _LOCAL_USERS:
            _LOCAL_USERS[uid]["ai_quota_date"] = today_str
            _LOCAL_USERS[uid]["ai_calls_today"] = used + 1
    else:
        try:
            db.collection("users").document(uid).set({
                "ai_quota_date": today_str,
                "ai_calls_today": used + 1,
            }, merge=True)
        except Exception as e:
            print(f"ERROR updating AI quota: {e}")


def upsert_user_profile(email: str, name: str = "", picture: str = "") -> dict:
    clean_email = email.strip().lower()
    uid = _make_user_id_from_email(clean_email)
    display_name = (name or "").strip() or clean_email.split("@")[0]
    link_code = _make_link_code(uid, clean_email)

    is_first_user = False
    existing_doc_data = {}
    if not db:
        if not _LOCAL_USERS:
            is_first_user = True
        existing_doc_data = _LOCAL_USERS.get(uid, {})
    else:
        try:
            existing_users = list(db.collection("users").limit(2).stream())
            if len(existing_users) == 0:
                is_first_user = True
            existing_snap = db.collection("users").document(uid).get()
            if existing_snap.exists:
                existing_doc_data = existing_snap.to_dict() or {}
        except Exception as e:
            print(f"ERROR checking existing user: {e}")

    owner_flag = is_owner_email(clean_email) and not bool(existing_doc_data.get("admin_revoked", False))
    is_blocked = bool(existing_doc_data.get("is_blocked", False)) and not owner_flag
    if is_blocked:
        raise ValueError("החשבון שלך נחסם על ידי מנהל המערכת")

    if existing_doc_data.get("admin_revoked") is True:
        is_admin = False
    else:
        is_admin = bool(owner_flag or existing_doc_data.get("admin_granted_by_owner", False))

    # Only admin/owner or users explicitly approved by owner get immediate access!
    is_approved = bool(is_admin or existing_doc_data.get("approved_by_owner", False))
    daily_ai_limit = int(existing_doc_data.get("daily_ai_limit") if existing_doc_data.get("daily_ai_limit") is not None else DEFAULT_DAILY_AI_LIMIT)
    today_str = str(date.today())
    ai_calls_today = int(existing_doc_data.get("ai_calls_today") or 0) if existing_doc_data.get("ai_quota_date") == today_str else 0

    was_already_known = bool(existing_doc_data)

    profile = {
        "id": uid,
        "email": clean_email,
        "name": display_name or existing_doc_data.get("name") or clean_email.split("@")[0],
        "picture": (picture or "").strip() or existing_doc_data.get("picture", ""),
        "link_code": link_code,
        "verified_oauth": True,
        "is_admin": is_admin,
        "is_approved": is_approved,
        "is_blocked": False,
        "daily_ai_limit": daily_ai_limit,
        "ai_quota_date": today_str,
        "ai_calls_today": ai_calls_today,
        "last_login_date": today_str,
    }

    if not db:
        _LOCAL_USERS[uid] = profile
    else:
        try:
            db.collection("users").document(uid).set({
                **profile,
                "updated_at": firestore.SERVER_TIMESTAMP,
            }, merge=True)
        except Exception as e:
            print(f"ERROR upsert_user_profile: {e}")

    # Notify admin via Web Push when a new unapproved user requests access
    if not is_approved and not was_already_known:
        try:
            for owner_em in OWNER_ADMIN_EMAILS:
                admin_uid = _make_user_id_from_email(owner_em)
                send_web_push_to_all(
                    title="🔔 בקשת הצטרפות חדשה ל-NutriAgent",
                    body=f"{profile['name']} ({clean_email}) מבקש גישה לאפליקציה. היכנס ללשונית אדמין לאישור.",
                    tag="nutriagent-new-user",
                    user_id=admin_uid,
                )
        except Exception as e:
            print(f"WARN admin push on signup: {e}")

    # Automatically claim unowned legacy data for the very first user or owner so nothing disappears
    claimed = {"claimed_meals": 0, "claimed_weights": 0, "claimed_favorites": 0}
    if is_first_user or owner_flag:
        claimed = claim_legacy_data_for_user(uid)

    return {**profile, "claimed": claimed}


def link_telegram_Account(tg_chat_id: int, code_or_email: str) -> Optional[dict]:
    query = (code_or_email or "").strip().lower()
    if not query:
        return None
    matched_user = None
    if not db:
        for u in _LOCAL_USERS.values():
            if u["email"].lower() == query or u["link_code"].lower() == query or u["id"].lower() == query:
                matched_user = u
                break
        if matched_user:
            _LOCAL_TG_LINKS[str(tg_chat_id)] = matched_user["id"]
        return matched_user

    try:
        for doc in db.collection("users").stream():
            u = doc.to_dict()
            if (
                (u.get("email") or "").lower() == query
                or (u.get("link_code") or "").lower() == query
                or doc.id.lower() == query
            ):
                matched_user = u
                matched_user["id"] = doc.id
                break
        if matched_user:
            db.collection("telegram_links").document(str(tg_chat_id)).set({
                "tg_chat_id": str(tg_chat_id),
                "user_id": matched_user["id"],
                "email": matched_user.get("email", ""),
                "name": matched_user.get("name", ""),
                "updated_at": firestore.SERVER_TIMESTAMP,
            }, merge=True)
            _LOCAL_TG_LINKS[str(tg_chat_id)] = matched_user["id"]
    except Exception as e:
        print(f"ERROR link_telegram_Account: {e}")
    return matched_user


def resolve_telegram_user_id(update: Update) -> str:
    tg_id = str(update.effective_user.id) if (update and update.effective_user) else ""
    if tg_id:
        if tg_id in _LOCAL_TG_LINKS:
            return _LOCAL_TG_LINKS[tg_id]
        if db:
            try:
                doc = db.collection("telegram_links").document(tg_id).get()
                if doc.exists and doc.to_dict().get("user_id"):
                    uid = doc.to_dict()["user_id"]
                    _LOCAL_TG_LINKS[tg_id] = uid
                    return uid
            except Exception as e:
                print(f"ERROR resolve_telegram_user_id: {e}")
    cfg = get_auth_config_db()
    return cfg.get("primary_user_id") or "default"


# ---------------------------------------------------------------------------
# Firestore Helpers (Multi-User Aware)
# ---------------------------------------------------------------------------

def get_targets(target_date: Optional[str] = None, user_id: str = "default") -> dict:
    uid = _normalize_user_id(user_id)
    local_t = _LOCAL_TARGETS_BY_USER.get(uid) or _LOCAL_TARGETS_BY_USER.get("default") or {"target_calories": 1800, "target_protein": 120}
    default_targets = dict(local_t)
    if not db:
        return default_targets
    try:
        user_global_id = "default" if uid == "default" else f"user_{uid}"
        global_doc = db.collection("daily_targets").document(user_global_id).get()
        if not global_doc.exists and uid != "default":
            global_doc = db.collection("daily_targets").document("default").get()
        if global_doc.exists:
            g_data = global_doc.to_dict()
            default_targets["target_calories"] = g_data.get("target_calories", 1800)
            default_targets["target_protein"] = g_data.get("target_protein", 120)

        d_str = target_date or str(date.today())
        date_doc_id = d_str if uid == "default" else f"{uid}_{d_str}"
        doc = db.collection("daily_targets").document(date_doc_id).get()
        if doc.exists:
            data = doc.to_dict()
            return {
                "target_calories": data.get("target_calories", default_targets["target_calories"]),
                "target_protein": data.get("target_protein", default_targets["target_protein"]),
            }
    except Exception as e:
        print(f"ERROR get_targets: {type(e).__name__} - {e}")
    return default_targets


def set_targets(target_calories: int, target_protein: int, target_date: Optional[str] = None, user_id: str = "default") -> dict:
    uid = _normalize_user_id(user_id)
    _LOCAL_TARGETS_BY_USER[uid] = {"target_calories": target_calories, "target_protein": target_protein}
    if not db:
        return dict(_LOCAL_TARGETS_BY_USER[uid])
    d_str = target_date or str(date.today())
    payload = {
        "user_id": uid,
        "target_calories": target_calories,
        "target_protein": target_protein,
        "updated_at": firestore.SERVER_TIMESTAMP,
    }
    user_global_id = "default" if uid == "default" else f"user_{uid}"
    date_doc_id = d_str if uid == "default" else f"{uid}_{d_str}"
    db.collection("daily_targets").document(user_global_id).set(payload, merge=True)
    db.collection("daily_targets").document(date_doc_id).set(payload, merge=True)
    return {"target_calories": target_calories, "target_protein": target_protein}


def log_meal_db(
    description: str,
    calories: int,
    protein: float,
    items: Optional[List[dict]] = None,
    log_date: Optional[str] = None,
    log_time: Optional[str] = None,
    source: str = "web",
    user_id: str = "default",
) -> Optional[dict]:
    uid = _normalize_user_id(user_id)
    d_str = log_date or str(date.today())
    t_str = log_time or datetime.now().strftime("%H:%M")
    clean_items = items or []
    if not db:
        entry = {
            "id": f"local-{int(datetime.now().timestamp() * 1000)}",
            "user_id": uid,
            "log_date": d_str,
            "log_time": t_str,
            "description": description,
            "calories": int(calories),
            "protein_g": round(float(protein), 1),
            "items": clean_items,
            "source": source,
        }
        _LOCAL_MEALS.insert(0, entry)
        return entry
    try:
        doc_data = {
            "user_id": uid,
            "log_date": d_str,
            "log_time": t_str,
            "description": description,
            "calories": int(calories),
            "protein_g": round(float(protein), 1),
            "items": clean_items,
            "source": source,
            "created_at": firestore.SERVER_TIMESTAMP,
        }
        _, doc_ref = db.collection("meals").add(doc_data)
        return {
            "id": doc_ref.id,
            "user_id": uid,
            "log_date": d_str,
            "log_time": t_str,
            "description": description,
            "calories": int(calories),
            "protein_g": round(float(protein), 1),
            "items": clean_items,
            "source": source,
        }
    except Exception as e:
        print(f"ERROR log_meal_db: {type(e).__name__} - {e}")
        return None


def _get_user_latest_weight(uid: str) -> float:
    try:
        w_data = get_weights_db(user_id=uid)
        if w_data and w_data.get("latest_weight"):
            return float(w_data["latest_weight"])
    except Exception:
        pass
    return 75.0


def log_workout_db(
    activity_name: str,
    burned_calories: int,
    duration_min: int = 30,
    distance_km: Optional[float] = None,
    activity_type: str = "other",
    log_date: Optional[str] = None,
    log_time: Optional[str] = None,
    source: str = "web-ai",
    user_id: str = "default",
    notes: str = "",
) -> Optional[dict]:
    uid = _normalize_user_id(user_id)
    d_str = log_date or str(date.today())
    t_str = log_time or datetime.now().strftime("%H:%M")

    if not db:
        entry = {
            "id": f"workout-local-{int(datetime.now().timestamp() * 1000)}",
            "user_id": uid,
            "log_date": d_str,
            "log_time": t_str,
            "activity_name": activity_name,
            "burned_calories": int(burned_calories),
            "duration_min": int(duration_min),
            "distance_km": float(distance_km) if distance_km is not None else None,
            "activity_type": activity_type,
            "source": source,
            "notes": notes,
        }
        _LOCAL_WORKOUTS.insert(0, entry)
        return entry

    try:
        doc_data = {
            "user_id": uid,
            "log_date": d_str,
            "log_time": t_str,
            "activity_name": activity_name,
            "burned_calories": int(burned_calories),
            "duration_min": int(duration_min),
            "distance_km": float(distance_km) if distance_km is not None else None,
            "activity_type": activity_type,
            "source": source,
            "notes": notes,
            "created_at": firestore.SERVER_TIMESTAMP,
        }
        _, doc_ref = db.collection("workouts").add(doc_data)
        return {
            "id": doc_ref.id,
            "user_id": uid,
            "log_date": d_str,
            "log_time": t_str,
            "activity_name": activity_name,
            "burned_calories": int(burned_calories),
            "duration_min": int(duration_min),
            "distance_km": float(distance_km) if distance_km is not None else None,
            "activity_type": activity_type,
            "source": source,
            "notes": notes,
        }
    except Exception as e:
        print(f"ERROR log_workout_db: {e}")
        return None


def upsert_health_sync_workout_db(
    activity_name: str,
    burned_calories: int,
    duration_min: int = 30,
    distance_km: Optional[float] = None,
    activity_type: str = "walk",
    log_date: Optional[str] = None,
    source: str = "google-fit",
    user_id: str = "default",
    notes: str = "",
) -> Optional[dict]:
    uid = _normalize_user_id(user_id)
    d_str = log_date or str(date.today())
    t_str = datetime.now().strftime("%H:%M")
    sync_doc_id = f"{uid}_health_sync_{d_str}"

    if not db:
        global _LOCAL_WORKOUTS
        _LOCAL_WORKOUTS = [
            w for w in _LOCAL_WORKOUTS
            if not (_doc_matches_user(w, uid) and w.get("log_date") == d_str and w.get("source") == source)
        ]
        entry = {
            "id": sync_doc_id,
            "user_id": uid,
            "log_date": d_str,
            "log_time": t_str,
            "activity_name": activity_name,
            "burned_calories": int(burned_calories),
            "duration_min": int(duration_min),
            "distance_km": float(distance_km) if distance_km is not None else None,
            "activity_type": activity_type,
            "source": source,
            "notes": notes,
        }
        _LOCAL_WORKOUTS.insert(0, entry)
        return entry

    try:
        # Remove any legacy duplicate google-fit docs for the same user & date
        existing_docs = db.collection("workouts").where("log_date", "==", d_str).stream()
        for doc in existing_docs:
            d = doc.to_dict() or {}
            if _doc_matches_user(d, uid) and d.get("source") == source and doc.id != sync_doc_id:
                doc.reference.delete()

        doc_data = {
            "user_id": uid,
            "log_date": d_str,
            "log_time": t_str,
            "activity_name": activity_name,
            "burned_calories": int(burned_calories),
            "duration_min": int(duration_min),
            "distance_km": float(distance_km) if distance_km is not None else None,
            "activity_type": activity_type,
            "source": source,
            "notes": notes,
            "created_at": firestore.SERVER_TIMESTAMP,
        }
        db.collection("workouts").document(sync_doc_id).set(doc_data, merge=True)
        return {
            "id": sync_doc_id,
            "user_id": uid,
            "log_date": d_str,
            "log_time": t_str,
            "activity_name": activity_name,
            "burned_calories": int(burned_calories),
            "duration_min": int(duration_min),
            "distance_km": float(distance_km) if distance_km is not None else None,
            "activity_type": activity_type,
            "source": source,
            "notes": notes,
        }
    except Exception as e:
        print(f"ERROR upsert_health_sync_workout_db: {e}")
        return None


def delete_workout_db(workout_id: str, user_id: str = "default") -> bool:
    uid = _normalize_user_id(user_id)
    if not db:
        global _LOCAL_WORKOUTS
        orig_len = len(_LOCAL_WORKOUTS)
        _LOCAL_WORKOUTS = [w for w in _LOCAL_WORKOUTS if not (w.get("id") == workout_id and (_doc_matches_user(w, uid) or is_admin_user_id(uid)))]
        return len(_LOCAL_WORKOUTS) < orig_len
    try:
        doc_ref = db.collection("workouts").document(workout_id)
        doc = doc_ref.get()
        if not doc.exists:
            return False
        doc_data = doc.to_dict() or {}
        if not _doc_matches_user(doc_data, uid) and not is_admin_user_id(uid):
            return False
        doc_ref.delete()
        return True
    except Exception as e:
        print(f"ERROR delete_workout_db: {e}")
        return False


def get_day_summary(target_date: Optional[str] = None, user_id: str = "default"):
    uid = _normalize_user_id(user_id)
    d_str = target_date or str(date.today())
    total_cal = 0
    total_prot = 0.0
    meals_list = []

    if db:
        try:
            meals_ref = db.collection("meals").where("log_date", "==", d_str).stream()
            for m in meals_ref:
                data = m.to_dict()
                if not _doc_matches_user(data, uid):
                    continue
                c = int(data.get("calories", 0))
                p = float(data.get("protein_g", 0.0))
                total_cal += c
                total_prot += p
                meals_list.append({
                    "id": m.id,
                    "description": data.get("description", ""),
                    "calories": c,
                    "protein_g": round(p, 1),
                    "items": data.get("items") or [],
                    "log_date": data.get("log_date", d_str),
                    "log_time": data.get("log_time", ""),
                    "source": data.get("source", "telegram"),
                })
            # Sort by log_time descending so newest is first
            meals_list.sort(key=lambda x: x.get("log_time", ""), reverse=True)
        except Exception as e:
            print(f"ERROR get_day_summary: {type(e).__name__} - {e}")
    else:
        for m in _LOCAL_MEALS:
            if m.get("log_date") == d_str and _doc_matches_user(m, uid):
                total_cal += m["calories"]
                total_prot += m["protein_g"]
                meals_list.append(m)

    burned_cal = 0
    workouts_list = []
    if db:
        try:
            w_ref = db.collection("workouts").where("log_date", "==", d_str).stream()
            for w in w_ref:
                w_data = w.to_dict()
                if not _doc_matches_user(w_data, uid):
                    continue
                bc = int(w_data.get("burned_calories", 0))
                burned_cal += bc
                workouts_list.append({
                    "id": w.id,
                    "activity_name": w_data.get("activity_name", ""),
                    "activity_type": w_data.get("activity_type", "other"),
                    "burned_calories": bc,
                    "duration_min": int(w_data.get("duration_min", 0)),
                    "distance_km": w_data.get("distance_km"),
                    "log_date": w_data.get("log_date", d_str),
                    "log_time": w_data.get("log_time", ""),
                    "source": w_data.get("source", "manual"),
                    "notes": w_data.get("notes", ""),
                })
            workouts_list.sort(key=lambda x: x.get("log_time", ""), reverse=True)
        except Exception as e:
            print(f"ERROR get_day_summary workouts: {e}")
    else:
        for w in _LOCAL_WORKOUTS:
            if w.get("log_date") == d_str and _doc_matches_user(w, uid):
                burned_cal += int(w.get("burned_calories", 0))
                workouts_list.append(w)

    targets = get_targets(d_str, user_id=uid)
    target_cal = targets["target_calories"]
    target_prot = targets["target_protein"]
    net_cal = total_cal - burned_cal
    remaining_cal = target_cal - total_cal
    return {
        "date": d_str,
        "user_id": uid,
        "total_calories": total_cal,
        "total_protein": round(total_prot, 1),
        "burned_calories": burned_cal,
        "net_calories": net_cal,
        "target_calories": target_cal,
        "target_protein": target_prot,
        "remaining_calories": remaining_cal,
        "meal_count": len(meals_list),
        "workout_count": len(workouts_list),
        "meals": meals_list,
        "workouts": workouts_list,
    }


def get_today_summary(user_id: str = "default"):
    s = get_day_summary(str(date.today()), user_id=user_id)
    return (
        s["total_calories"],
        s["total_protein"],
        s["target_calories"],
        s["remaining_calories"],
        s["meals"],
    )


# ---------------------------------------------------------------------------
# Gemini AI Functions
# ---------------------------------------------------------------------------

def analyze_meal(user_text: str) -> NutritionExtraction:
    if not ai_client:
        # Local preview estimation when GEMINI_API_KEY is not set in local environment
        txt = user_text.strip()
        cal, prot = 250, 15.0
        if any(w in txt for w in ["קפה", "הפוך", "קפוצ'ינו", "נס", "אמריקנו"]):
            cal, prot = 80, 4.0
        elif any(w in txt for w in ["ביצה", "ביצים", "חביתה", "שקשוקה"]):
            cal, prot = 320, 22.0
        elif any(w in txt for w in ["עוף", "שניצל", "בשר", "סטייק", "הודו"]):
            cal, prot = 480, 42.0
        elif any(w in txt for w in ["טונה", "סלמון", "דג"]):
            cal, prot = 360, 35.0
        elif any(w in txt for w in ["יוגורט", "שייק", "קוטג"]):
            cal, prot = 180, 20.0
        return NutritionExtraction(is_meal=True, calories=cal, protein_g=prot, summary=txt)
    prompt = f"""המשתמש דיווח: '{user_text}'. זה יכול להיות אוכל, שתיה, או כל דבר אחר שנצרך.
חשוב מאוד: קפה, קפוצ'ינו, וכל משקאות החלב/שתייה הם פריטי צריכה תקפים לחלוטין. עבור פריטים אלו, הציבו תמיד is_meal: true.
דוגמאות לשתיה וקפה שחייבים לקבל is_meal: true: "הפוך קטן", "קפוצ'ינו", "נס קפה", "אמריקנו עם חלב".
הערך קלוריות וחלבון במדויק לישראל. עבור הפוך קטן/קפוצ'ינו ישראלי סטנדרטי, הערך כ-70-90 קק"ל וכ-3-4 גרם חלבון (תלוי בחלב).
אם יש מספר פריטים בדיווח אחד, סכם את סך הקלוריות והחלבון של כולם יחד ותן תיאור תמציתי וברור בעברית בשדה summary.
בנוסף, ברשימת items, פרט כל פריט בנפרד עם השם שלו (name), הקלוריות המדויקות של אותו פריט (calories) וגרם החלבון שלו (protein_g).
אם יש פריט אחד בלבד, החזר פריט יחיד זה ב-items עם הערכים שלו. סכום הקלוריות והחלבון של ה-items צריך להיות תואם לסך הכולל (calories, protein_g).
אם זה משהו שאין לו שום קשר לאוכל או שתייה, סמן is_meal=False."""
    try:
        response = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=NutritionExtraction,
            ),
        )
        return NutritionExtraction.model_validate_json(response.text)
    except Exception as e:
        err_msg = str(e)
        print(f"ERROR analyze_meal: {type(e).__name__} - {err_msg}")
        if "402" in err_msg or "prepayment" in err_msg.lower() or "RESOURCE_EXHAUSTED" in err_msg:
            return NutritionExtraction(is_meal=False, calories=0, protein_g=0, summary="API_ERROR: 402_PREPAYMENT_DEPLETED")
        return NutritionExtraction(is_meal=False, calories=0, protein_g=0, summary="Analysis error")


def analyze_workout(text: str, user_weight_kg: float = 75.0) -> WorkoutExtraction:
    if not ai_client:
        return WorkoutExtraction(
            workouts=[WorkoutItem(activity_name=text[:40], activity_type="other", duration_min=30, burned_calories=200, is_workout=True)],
            total_burned_calories=200,
            summary=f"אימון: {text[:40]} (200 קק״ל)",
            is_workout=True
        )

    system_instruction = f"""אתה פיזיולוג מומחה ומאמן כושר בכיר.
תפקידך לנתח את טקסט האימון שהזין המשתמש בעברית, ולחשב בצורה מדעית ומדויקת את הקלוריות שנשרפו באימון.
משקל גוף המתאמן: {user_weight_kg:.1f} ק"ג.
השתמש בנוסחאות MET מקובלות:
- ריצה בקצב בינוני (כ-10 קמ"ש / קצב 6:00): MET ~ 9.8 (כ-1 קק"ל לק"ג לק"מ)
- אימון כוח משקולות בחדר כושר: MET ~ 5.0 - 6.0 (כ-300-450 קק"ל לשעה למשקל 75 ק"ג)
- הליכה מהירה: MET ~ 4.3 (כ-250-320 קק"ל לשעה)
- רכיבת אופניים: MET ~ 7.5 - 8.5
- אימון HIIT / קרוספיט: MET ~ 8.0 - 10.0
- שחייה: MET ~ 7.0 - 8.0

אם הטקסט אינו מתאר אימון גופני או פעילות ספורטיבית (למשל סתם שיחה או אוכל), החזר is_workout=False ו-total_burned_calories=0.
וודא ש-total_burned_calories תואם בדיוק לסכום ה-burned_calories של כל האימונים שברשימה."""

    try:
        response = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=text,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=WorkoutExtraction,
                system_instruction=system_instruction,
                temperature=0.2,
            ),
        )
        data = json.loads(response.text)
        return WorkoutExtraction(**data)
    except Exception as e:
        print(f"ERROR analyze_workout: {e}")
        return WorkoutExtraction(
            workouts=[WorkoutItem(activity_name=text[:40], activity_type="other", duration_min=30, burned_calories=200, is_workout=True)],
            total_burned_calories=200,
            summary=f"אימון: {text[:40]} (200 קק״ל)",
            is_workout=True
        )


def _normalize_image_bytes(image_bytes: bytes, fallback_mime: str = "image/jpeg") -> tuple[bytes, str]:
    detected_mime = (fallback_mime or "image/jpeg").lower().strip()
    if image_bytes.startswith(b"\xff\xd8\xff"):
        detected_mime = "image/jpeg"
    elif image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        detected_mime = "image/png"
    elif image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        detected_mime = "image/webp"
    elif len(image_bytes) > 12 and image_bytes[4:8] == b"ftyp":
        brand = image_bytes[8:12]
        if brand in (b"heic", b"heix", b"hevc", b"mif1", b"msf1"):
            detected_mime = "image/heic"

    if Image is not None:
        try:
            with Image.open(io.BytesIO(image_bytes)) as img:
                if ImageOps is not None:
                    img = ImageOps.exif_transpose(img)
                if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                    bg = Image.new("RGB", img.size, (255, 255, 255))
                    rgba = img.convert("RGBA")
                    bg.paste(rgba, mask=rgba.split()[3])
                    img = bg
                elif img.mode != "RGB":
                    img = img.convert("RGB")
                img.thumbnail((1280, 1280))
                out = io.BytesIO()
                img.save(out, format="JPEG", quality=85, optimize=True)
                return out.getvalue(), "image/jpeg"
        except Exception as e:
            print(f"WARN _normalize_image_bytes PIL fallback ({detected_mime}): {e}")

    if detected_mime not in ("image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"):
        detected_mime = "image/jpeg"
    return image_bytes, detected_mime


def analyze_workout_image(image_bytes: bytes, mime_type: str = "image/jpeg", caption: str = "", user_weight_kg: float = 75.0) -> WorkoutExtraction:
    if not ai_client:
        label = caption.strip() if caption and caption.strip() else "סנכרון צילום מסך (Samsung / Google Health)"
        return WorkoutExtraction(
            workouts=[WorkoutItem(activity_name=label, activity_type="other", duration_min=35, burned_calories=240, is_workout=True)],
            total_burned_calories=240,
            summary=f"{label} (240 קק״ל)",
            is_workout=True,
        )

    norm_bytes, clean_mime = _normalize_image_bytes(image_bytes, mime_type)
    caption_note = f"\nהערת המשתמש: '{caption.strip()}'." if caption and caption.strip() else ""
    prompt = f"""נתח את צילום המסך או התמונה המצורפת של אפליקציית כושר/בריאות (כגון Samsung Health, Google Fit, Google Health Connect, Apple Health / Fitness, Garmin, Strava, שעון חכם או צג הליכון/מכשיר כושר).{caption_note}
משקל גוף המתאמן להערכת חסר: {user_weight_kg:.1f} ק"ג.
1. חלץ מתוך התמונה את כל האימונים או סיכום הפעילות היומית (מספר צעדים, קלוריות פעילות שנשרפו, זמן פעילות, מרחק בק"מ וסוג הפעילות).
2. חשוב: אם מופיעות בתמונה גם 'קלוריות פעילות' (Active Calories) וגם 'סה"כ קלוריות כולל מנוחה' (Total Calories), קח אך ורק את הקלוריות הפעילות שנשרפו מהפעילות/צעדים/אימון! אם מופיעים רק צעדים ללא קלוריות, חשב לפי כ-0.045 קק"ל לצעד.
3. ציין בשדה activity_name את שם המקור והפעילות בעברית ברורה (למשל: 'Samsung Health - הליכה (8,420 צעדים)' או 'ריצת בוקר - Samsung Health').
4. החזר is_workout=True אם התמונה מציגה נתוני צעדים, אימון, שעון כושר או מכשיר כושר."""

    last_err = None
    for model_name in ("gemini-2.5-flash", "gemini-2.0-flash"):
        try:
            response = ai_client.models.generate_content(
                model=model_name,
                contents=[
                    types.Part.from_bytes(data=norm_bytes, mime_type=clean_mime),
                    prompt,
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=WorkoutExtraction,
                    temperature=0.1,
                ),
            )
            return WorkoutExtraction.model_validate_json(response.text)
        except Exception as e:
            last_err = e
            print(f"ERROR analyze_workout_image ({model_name}): {type(e).__name__} - {e}")

    return WorkoutExtraction(workouts=[], total_burned_calories=0, summary=f"API_ERROR: {last_err}", is_workout=False)


def get_vision_calibration_rules_db() -> List[dict]:
    if not db:
        return list(_LOCAL_VISION_RULES)
    try:
        docs = db.collection("vision_calibration_rules").limit(100).stream()
        rules = []
        for doc in docs:
            d = doc.to_dict() or {}
            d["id"] = doc.id
            if d.get("rule_text"):
                rules.append(d)
        if not rules:
            for r in DEFAULT_VISION_CALIBRATION_RULES:
                db.collection("vision_calibration_rules").document(r["id"]).set({
                    **r,
                    "created_at": firestore.SERVER_TIMESTAMP,
                }, merge=True)
            return list(DEFAULT_VISION_CALIBRATION_RULES)
        rules.sort(key=lambda x: str(x.get("created_at_iso") or ""), reverse=True)
        return rules
    except Exception as e:
        print(f"ERROR get_vision_calibration_rules_db: {e}")
        return list(_LOCAL_VISION_RULES)


def add_vision_calibration_rule_db(rule_text: str, category: str = "כיול אדמין", created_by: str = "admin") -> dict:
    clean_text = (rule_text or "").strip()
    if not clean_text:
        raise ValueError("נא להזין כלל כיול לסוכן")
    now_iso = datetime.now().strftime("%Y-%m-%d %H:%M")
    rule_id = f"rule-{int(datetime.now().timestamp() * 1000)}"
    entry = {
        "id": rule_id,
        "rule_text": clean_text,
        "category": category or "כיול אדמין",
        "created_by": created_by,
        "created_at_iso": now_iso,
    }
    if not db:
        _LOCAL_VISION_RULES.insert(0, entry)
        return entry
    try:
        db.collection("vision_calibration_rules").document(rule_id).set({
            **entry,
            "created_at": firestore.SERVER_TIMESTAMP,
        })
    except Exception as e:
        print(f"ERROR add_vision_calibration_rule_db: {e}")
    return entry


def delete_vision_calibration_rule_db(rule_id: str) -> bool:
    global _LOCAL_VISION_RULES
    before = len(_LOCAL_VISION_RULES)
    _LOCAL_VISION_RULES = [r for r in _LOCAL_VISION_RULES if r.get("id") != rule_id]
    if not db:
        return len(_LOCAL_VISION_RULES) < before
    try:
        db.collection("vision_calibration_rules").document(rule_id).delete()
        return True
    except Exception as e:
        print(f"ERROR delete_vision_calibration_rule_db: {e}")
        return False


def compute_image_forensics_and_histogram(image_bytes: bytes) -> dict:
    """Computes RGB & Luminance 16-bin histograms, exposure, contrast, sharpness, color balance, and a compact thumbnail."""
    size_kb = round(len(image_bytes) / 1024.0, 1)
    fallback = {
        "size_kb": size_kb,
        "orig_width": 0,
        "orig_height": 0,
        "aspect_ratio": "1:1",
        "format": "JPEG",
        "mean_brightness": 128.0,
        "brightness_label": "תאורה מאוזנת",
        "contrast_std": 48.0,
        "contrast_label": "ניגודיות תקינה",
        "dynamic_range": 140,
        "p10_luma": 45,
        "p50_luma": 128,
        "p90_luma": 195,
        "shadow_clipping_pct": 1.2,
        "highlight_clipping_pct": 0.8,
        "mean_r": 135.0,
        "mean_g": 125.0,
        "mean_b": 110.0,
        "warm_cool_ratio": 1.22,
        "lighting_temp_label": "תאורה חמה / פנים",
        "saturation_score": 42.0,
        "green_fresh_pct": 8.0,
        "warm_cooked_pct": 34.0,
        "sharpness_score": 68,
        "sharpness_label": "חד וברור",
        "hist_bins": [f"{i*16}-{i*16+15}" for i in range(16)],
        "hist_r": [6.25] * 16,
        "hist_g": [6.25] * 16,
        "hist_b": [6.25] * 16,
        "hist_luma": [6.25] * 16,
        "thumbnail_data_url": "",
        "optical_warnings": [],
    }
    if Image is None or not image_bytes:
        return fallback

    try:
        with Image.open(io.BytesIO(image_bytes)) as raw_img:
            img_format = raw_img.format or "JPEG"
            if ImageOps is not None:
                img = ImageOps.exif_transpose(raw_img)
            else:
                img = raw_img
            orig_w, orig_h = img.size
            rgb = img.convert("RGB")
            luma = rgb.convert("L")

            # 256-bin histograms
            rgb_hist_256 = rgb.histogram()
            r_256 = rgb_hist_256[0:256]
            g_256 = rgb_hist_256[256:512]
            b_256 = rgb_hist_256[512:768]
            l_256 = luma.histogram()[0:256]

            total_px = max(1, sum(l_256))

            def _to_16_bins(h256: list) -> List[float]:
                bins16 = []
                for bi in range(16):
                    chunk = sum(h256[bi * 16 : (bi + 1) * 16])
                    bins16.append(round((chunk * 100.0) / total_px, 2))
                return bins16

            hist_r = _to_16_bins(r_256)
            hist_g = _to_16_bins(g_256)
            hist_b = _to_16_bins(b_256)
            hist_luma = _to_16_bins(l_256)

            mean_r = round(sum(i * c for i, c in enumerate(r_256)) / total_px, 1)
            mean_g = round(sum(i * c for i, c in enumerate(g_256)) / total_px, 1)
            mean_b = round(sum(i * c for i, c in enumerate(b_256)) / total_px, 1)
            mean_l = round(sum(i * c for i, c in enumerate(l_256)) / total_px, 1)

            variance_l = sum(((i - mean_l) ** 2) * c for i, c in enumerate(l_256)) / total_px
            contrast_std = round(variance_l ** 0.5, 1)

            def _percentile(h256: list, pct: float) -> int:
                target = total_px * pct
                acc = 0
                for val, count in enumerate(h256):
                    acc += count
                    if acc >= target:
                        return val
                return 255

            p10 = _percentile(l_256, 0.10)
            p50 = _percentile(l_256, 0.50)
            p90 = _percentile(l_256, 0.90)
            dyn_range = max(0, p90 - p10)

            shadow_clip_pct = round((sum(l_256[:16]) * 100.0) / total_px, 1)
            highlight_clip_pct = round((sum(l_256[240:]) * 100.0) / total_px, 1)

            # Fast 64x64 grid analysis for sharpness, saturation, and food color cues
            small_rgb = rgb.resize((64, 64))
            small_l = luma.resize((64, 64))
            px_rgb = list(small_rgb.getdata())
            px_l = list(small_l.getdata())

            sat_sum = 0
            green_cnt = 0
            warm_cooked_cnt = 0
            for r_v, g_v, b_v in px_rgb:
                sat_sum += (max(r_v, g_v, b_v) - min(r_v, g_v, b_v))
                if g_v > r_v + 10 and g_v > b_v + 12 and g_v > 45:
                    green_cnt += 1
                if r_v > g_v > b_v and r_v > 85 and (r_v - b_v) > 25:
                    warm_cooked_cnt += 1

            n_small = max(1, len(px_rgb))
            saturation_score = round(sat_sum / n_small, 1)
            green_fresh_pct = round((green_cnt * 100.0) / n_small, 1)
            warm_cooked_pct = round((warm_cooked_cnt * 100.0) / n_small, 1)

            grad_sum = 0
            grad_cnt = 0
            for y in range(63):
                row_off = y * 64
                for x in range(63):
                    idx = row_off + x
                    c_val = px_l[idx]
                    grad_sum += abs(c_val - px_l[idx + 1]) + abs(c_val - px_l[idx + 64])
                    grad_cnt += 2
            avg_grad = grad_sum / max(1, grad_cnt)
            sharpness_score = int(min(100, max(5, round(avg_grad * 3.6))))

            warm_cool_ratio = round((mean_r + 1.0) / (mean_b + 1.0), 2)

            warnings = []
            if mean_l < 75 or shadow_clip_pct > 22:
                brightness_label = "כהה / תת-חשיפה"
                warnings.append("התמונה כהה יחסית (תת-חשיפה) — ייתכן שרטבים כהים או עומק הצלחת מוסתרים בצללים.")
            elif mean_l > 195 or highlight_clip_pct > 18:
                brightness_label = "בהירה מאוד / חשיפת יתר"
                warnings.append("חשיפת יתר באזורים בהירים (סינוור/פלאש) — עלול לטשטש מרקם של אורז, גבינות או ברק שמן.")
            else:
                brightness_label = "תאורה מאוזנת וטובה"

            if contrast_std < 32:
                contrast_label = "ניגודיות נמוכה (שטוחה)"
                warnings.append("ניגודיות נמוכה — הפרדה חלשה בין מרכיבים בעלי צבע דומה.")
            elif contrast_std > 72:
                contrast_label = "ניגודיות גבוהה (צללים חדים)"
            else:
                contrast_label = "ניגודיות מיטבית"

            if sharpness_score < 32:
                sharpness_label = "מטושטשת / תנועה"
                warnings.append("חדות נמוכה (טשטוש) — קשה יותר להעריך מרקם מדויק או כיתוב קטן על אריזה.")
            elif sharpness_score < 55:
                sharpness_label = "חדות בינונית"
            else:
                sharpness_label = "חדה וברורה"

            if warm_cool_ratio > 1.32:
                lighting_temp_label = "תאורה צהובה/חמה מאוד (פנים)"
                warnings.append("תאורת חדר חמה/צהובה (יחס R/B גבוה) — עלולה לגרום למזון מאודה/אפוי להיראות מטוגן או שומני יותר.")
            elif warm_cool_ratio < 0.92:
                lighting_temp_label = "תאורה קרה / פלורסנט"
            else:
                lighting_temp_label = "איזון לבן טבעי (Daylight)"

            # Compact thumbnail for Admin Vision Lab
            thumb = rgb.copy()
            thumb.thumbnail((220, 220))
            t_out = io.BytesIO()
            thumb.save(t_out, format="JPEG", quality=68, optimize=True)
            thumb_b64 = base64.b64encode(t_out.getvalue()).decode("ascii")
            thumb_data_url = f"data:image/jpeg;base64,{thumb_b64}"

            return {
                "size_kb": size_kb,
                "orig_width": orig_w,
                "orig_height": orig_h,
                "aspect_ratio": f"{round(orig_w / max(1, orig_h), 2)}:1",
                "format": img_format,
                "mean_brightness": mean_l,
                "brightness_label": brightness_label,
                "contrast_std": contrast_std,
                "contrast_label": contrast_label,
                "dynamic_range": dyn_range,
                "p10_luma": p10,
                "p50_luma": p50,
                "p90_luma": p90,
                "shadow_clipping_pct": shadow_clip_pct,
                "highlight_clipping_pct": highlight_clip_pct,
                "mean_r": mean_r,
                "mean_g": mean_g,
                "mean_b": mean_b,
                "warm_cool_ratio": warm_cool_ratio,
                "lighting_temp_label": lighting_temp_label,
                "saturation_score": saturation_score,
                "green_fresh_pct": green_fresh_pct,
                "warm_cooked_pct": warm_cooked_pct,
                "sharpness_score": sharpness_score,
                "sharpness_label": sharpness_label,
                "hist_bins": [f"{i*16}-{i*16+15}" for i in range(16)],
                "hist_r": hist_r,
                "hist_g": hist_g,
                "hist_b": hist_b,
                "hist_luma": hist_luma,
                "thumbnail_data_url": thumb_data_url,
                "optical_warnings": warnings,
            }
    except Exception as e:
        print(f"WARN compute_image_forensics_and_histogram: {e}")
        return fallback


def analyze_meal_image(image_bytes: bytes, mime_type: str = "image/jpeg", caption: str = "", forensics: Optional[dict] = None) -> NutritionExtraction:
    if not ai_client:
        label = caption.strip() if caption and caption.strip() else "ארוחה מצולמת (ניתוח תמונה)"
        return NutritionExtraction(
            is_meal=True,
            calories=420,
            protein_g=32.0,
            summary=label,
            confidence_score=85,
            portion_estimation_method="הערכת ברירת מחדל (ללא חיבור AI)",
            visual_cues="מצב הדגמה מקומי",
            hidden_calories_note="ללא",
            applied_calibration_rules=[],
        )

    norm_bytes, clean_mime = _normalize_image_bytes(image_bytes, mime_type)
    if forensics is None:
        forensics = compute_image_forensics_and_histogram(image_bytes)

    active_rules = get_vision_calibration_rules_db()
    rules_block = ""
    if active_rules:
        rules_lines = "\n".join(f"  - [{r.get('category', 'כיול')}]: {r.get('rule_text', '')}" for r in active_rules[:25])
        rules_block = f"\n\n🎯 חוקי כיול ודיוק מחייבים של סוכן ה-Vision (הוגדרו ע״י מנהל המערכת — חובה ליישם במידת הרלוונטיות):\n{rules_lines}"

    optical_context = (
        f"\n\n📊 טלמטריה אופטית והיסטוגרמת התמונה (מחושב ע״י חיישן התמונה לפני הניתוח):\n"
        f"- רזולוציה מקורית: {forensics.get('orig_width')}x{forensics.get('orig_height')} פיקסלים\n"
        f"- בהירות ממוצעת (Luminance): {forensics.get('mean_brightness')}/255 ({forensics.get('brightness_label')}), חציון: {forensics.get('p50_luma')}\n"
        f"- ניגודיות (StdDev): {forensics.get('contrast_std')} ({forensics.get('contrast_label')}), טווח דינמי P10-P90: {forensics.get('dynamic_range')}\n"
        f"- קטימת צללים כהים: {forensics.get('shadow_clipping_pct')}%, קטימת שיאי אור ( פלאש/סינוור): {forensics.get('highlight_clipping_pct')}%\n"
        f"- איזון צבעים ממוצע: R={forensics.get('mean_r')}, G={forensics.get('mean_g')}, B={forensics.get('mean_b')} | יחס חם/קר (R/B): {forensics.get('warm_cool_ratio')} ({forensics.get('lighting_temp_label')})\n"
        f"- חדות פרטים (Edge Sharpness): {forensics.get('sharpness_score')}/100 ({forensics.get('sharpness_label')})\n"
        f"- דומיננטיות ירוקים/טריים: {forensics.get('green_fresh_pct')}%, גוונים חמים/מבושלים/שחומים: {forensics.get('warm_cooked_pct')}%"
    )
    if forensics.get("optical_warnings"):
        optical_context += "\n- דגשים אופטיים למודל: " + " | ".join(forensics["optical_warnings"])

    caption_note = f"\nהערת המשתמש לתמונה: '{caption.strip()}'." if caption and caption.strip() else ""
    prompt = f"""אתה סוכן Vision AI מומחה לניתוח תמונות מזון, שקילת נפחים חזותית וזיהוי ערכים תזונתיים בישראל.{caption_note}{optical_context}{rules_block}

התמונה יכולה להיות:
1. צילום של מנה / צלחת אוכל / משקה / קפה / חטיף / פרי / כריך / סלט / מרק / קינוח — זהה את כל המרכיבים, הערך את גודל המנה בצורה מציאותית לפי קנה מידה חזותי (קוטר צלחת, סכו"ם, כוס, עומק קערה), התחשב בטלמטריה האופטית (למשל אל תבלבל בין תאורת חדר צהובה לבין טיגון עמוק), וחשב סך קלוריות וחלבון בגרמים.
2. תווית ערכים תזונתיים / אריזת מוצר / ברקוד / תפריט — קרא במדויק את הערכים התזונתיים או שם המוצר הישראלי וחשב את הקלוריות והחלבון למנה/יחידה שנצרכה.

חשוב מאוד:
- אם יש בתמונה כל דבר שקשור לאוכל, שתייה, אריזת מזון, כוס, צלחת, קערה, פרי, ירק, או טבלת ערכים תזונתיים — סמן תמיד is_meal=True!
- החזר תיאור קצר, טבעי וברור בעברית בשדה summary (למשל: "חזה עוף 180 ג' עם אורז לבן וסלט קצוץ").
- ברשימת items, פרט כל פריט או רכיב בנפרד (כולל שמן/רוטב משמעותי אם זוהה) עם השם שלו (name, כולל משקל משוער בגרמים/כפות), כמות הקלוריות שלו (calories) וגרם החלבון שלו (protein_g).
- מלא את שדות הבקרה של הסוכן:
  * confidence_score: ציון ביטחון (0-100) בזיהוי המנה והכמויות.
  * portion_estimation_method: הסבר קצר בעברית איך הערכת את המשקל/נפח (למשל: "לפי צלחת מנה עיקרית 26 ס״מ וגובה ערימת האורז").
  * visual_cues: סימנים חזותיים שזיהית בתמונה (ברק שמן, סימני צריבה על הגריל, מרקם, צפיפות, תאורה).
  * hidden_calories_note: אילו קלוריות סמויות הוספת לחישוב (שמן בישול, טחינה, רוטב, חמאה) או "ללא קלוריות סמויות".
  * applied_calibration_rules: רשימה קצרה של חוקי הכיול שהפעלת בתמונה זו (אם רלוונטי)."""

    last_err = None
    for model_name in ("gemini-2.5-flash", "gemini-2.0-flash"):
        try:
            response = ai_client.models.generate_content(
                model=model_name,
                contents=[
                    types.Part.from_bytes(data=norm_bytes, mime_type=clean_mime),
                    prompt,
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=NutritionExtraction,
                    temperature=0.15,
                ),
            )
            return NutritionExtraction.model_validate_json(response.text)
        except Exception as e:
            last_err = e
            print(f"ERROR analyze_meal_image ({model_name}): {type(e).__name__} - {e}")

    return NutritionExtraction(is_meal=False, calories=0, protein_g=0, summary=f"API_ERROR: {type(last_err).__name__}: {last_err}")


def save_vision_analysis_log_db(
    user_id: str,
    extraction: NutritionExtraction,
    forensics: dict,
    source: str = "web-photo",
    caption: str = "",
    meal_id: str = "",
) -> dict:
    uid = _normalize_user_id(user_id)
    user_prof = get_user_profile_db(uid) or {}
    now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_id = f"vlog-{int(datetime.now().timestamp() * 1000)}"
    items_list = [it.model_dump() for it in (extraction.items or [])]
    record = {
        "id": log_id,
        "user_id": uid,
        "user_name": user_prof.get("name") or uid,
        "user_email": user_prof.get("email") or "",
        "source": source,
        "meal_id": meal_id or "",
        "caption": caption or "",
        "created_at_iso": now_iso,
        "forensics": forensics,
        "ai_analysis": {
            "is_meal": extraction.is_meal,
            "summary": extraction.summary,
            "calories": int(extraction.calories),
            "protein_g": round(float(extraction.protein_g), 1),
            "items": items_list,
            "confidence_score": int(getattr(extraction, "confidence_score", 88) or 88),
            "portion_estimation_method": getattr(extraction, "portion_estimation_method", "") or "הערכה חזותית לפי פרופורציות הצלחת והמרכיבים",
            "visual_cues": getattr(extraction, "visual_cues", "") or "זיהוי צבעים, מרקם ונפח המנה",
            "hidden_calories_note": getattr(extraction, "hidden_calories_note", "") or "ללא הערות מיוחדות",
            "applied_calibration_rules": list(getattr(extraction, "applied_calibration_rules", []) or []),
        },
        "calibrated": False,
        "admin_rule_added": "",
        "corrected_calories": None,
        "corrected_protein_g": None,
    }
    _LOCAL_VISION_LOGS.insert(0, record)
    if len(_LOCAL_VISION_LOGS) > 60:
        del _LOCAL_VISION_LOGS[60:]
    if db:
        try:
            db.collection("vision_analysis_logs").document(log_id).set({
                **record,
                "created_at": firestore.SERVER_TIMESTAMP,
            })
        except Exception as e:
            print(f"ERROR save_vision_analysis_log_db: {e}")
    return record


def get_vision_analysis_logs_db(limit: int = 25) -> dict:
    rules = get_vision_calibration_rules_db()
    logs = []
    if db:
        try:
            docs = db.collection("vision_analysis_logs").order_by("created_at", direction=firestore.Query.DESCENDING).limit(limit).stream()
            for doc in docs:
                d = doc.to_dict() or {}
                d["id"] = doc.id
                d.pop("created_at", None)
                logs.append(d)
        except Exception as e:
            print(f"ERROR get_vision_analysis_logs_db: {e}")
            logs = list(_LOCAL_VISION_LOGS[:limit])
    else:
        logs = list(_LOCAL_VISION_LOGS[:limit])

    return {
        "logs": logs,
        "calibration_rules": rules,
    }


def calibrate_vision_log_db(req: VisionCalibrateRequest) -> dict:
    rule_saved = None
    custom_rule = (req.rule_text or "").strip()

    # Fetch log if log_id given
    target_log = None
    if req.log_id:
        for lg in _LOCAL_VISION_LOGS:
            if lg.get("id") == req.log_id:
                target_log = lg
                break
        if not target_log and db:
            try:
                snap = db.collection("vision_analysis_logs").document(req.log_id).get()
                if snap.exists:
                    target_log = snap.to_dict() or {}
                    target_log["id"] = snap.id
            except Exception as e:
                print(f"WARN fetch vision log: {e}")

    # Auto-synthesize a calibration rule if the admin corrected calories/protein/summary without writing a full rule
    if not custom_rule and target_log and (req.corrected_calories is not None or req.corrected_protein_g is not None or req.corrected_summary):
        orig_ai = target_log.get("ai_analysis") or {}
        orig_sum = orig_ai.get("summary", "מנה מצולמת")
        orig_cal = orig_ai.get("calories", 0)
        orig_prot = orig_ai.get("protein_g", 0)
        new_sum = (req.corrected_summary or orig_sum).strip()
        new_cal = req.corrected_calories if req.corrected_calories is not None else orig_cal
        new_prot = req.corrected_protein_g if req.corrected_protein_g is not None else orig_prot
        custom_rule = (
            f"בזיהוי מנה מסוג '{new_sum}' (שזוהתה תחילה כ-'{orig_sum}' עם {orig_cal} קק״ל ו-{orig_prot}g חלבון): "
            f"יש לדייק את ההערכה ל-{new_cal} קק״ל ו-{new_prot}g חלבון עבור גודל מנה דומה."
        )

    if custom_rule:
        rule_saved = add_vision_calibration_rule_db(rule_text=custom_rule, category="כיול אדמין מתמונה", created_by="admin")

    meal_updated = False
    meal_id = (req.meal_id or (target_log.get("meal_id") if target_log else "") or "").strip()
    target_uid = (req.target_user_id or (target_log.get("user_id") if target_log else "") or "").strip()

    if req.update_customer_meal and meal_id and (req.corrected_calories is not None or req.corrected_protein_g is not None or req.corrected_summary):
        orig_ai = (target_log.get("ai_analysis") if target_log else {}) or {}
        final_desc = (req.corrected_summary or orig_ai.get("summary") or "ארוחה מצולמת (מכוילת)").strip()
        final_cal = int(req.corrected_calories if req.corrected_calories is not None else orig_ai.get("calories", 0))
        final_prot = round(float(req.corrected_protein_g if req.corrected_protein_g is not None else orig_ai.get("protein_g", 0.0)), 1)
        if db:
            try:
                m_ref = db.collection("meals").document(meal_id)
                m_snap = m_ref.get()
                if m_snap.exists:
                    m_ref.set({
                        "description": final_desc,
                        "calories": final_cal,
                        "protein_g": final_prot,
                        "calibrated_by_admin": True,
                    }, merge=True)
                    meal_updated = True
            except Exception as e:
                print(f"ERROR updating meal from vision calibration: {e}")
        for m in _LOCAL_MEALS:
            if m.get("id") == meal_id:
                m["description"] = final_desc
                m["calories"] = final_cal
                m["protein_g"] = final_prot
                meal_updated = True

    if req.log_id:
        updates = {
            "calibrated": True,
            "admin_rule_added": custom_rule,
        }
        if req.corrected_calories is not None:
            updates["corrected_calories"] = int(req.corrected_calories)
        if req.corrected_protein_g is not None:
            updates["corrected_protein_g"] = round(float(req.corrected_protein_g), 1)
        if req.corrected_summary:
            updates["corrected_summary"] = req.corrected_summary.strip()
        for lg in _LOCAL_VISION_LOGS:
            if lg.get("id") == req.log_id:
                lg.update(updates)
                if "ai_analysis" in lg:
                    if req.corrected_calories is not None:
                        lg["ai_analysis"]["calories"] = int(req.corrected_calories)
                    if req.corrected_protein_g is not None:
                        lg["ai_analysis"]["protein_g"] = round(float(req.corrected_protein_g), 1)
                    if req.corrected_summary:
                        lg["ai_analysis"]["summary"] = req.corrected_summary.strip()
        if db:
            try:
                doc_ref = db.collection("vision_analysis_logs").document(req.log_id)
                doc_ref.set(updates, merge=True)
            except Exception as e:
                print(f"WARN updating vision_analysis_logs doc: {e}")

    return {
        "status": "calibrated",
        "rule": rule_saved,
        "meal_updated": meal_updated,
        "target_user_id": target_uid,
    }


def analyze_meal_audio(audio_bytes: bytes, mime_type: str = "audio/webm", caption: str = "") -> NutritionExtraction:
    if not ai_client:
        label = caption.strip() if caption and caption.strip() else "דיווח קולי על ארוחה"
        return NutritionExtraction(is_meal=True, calories=350, protein_g=28.0, summary=label)

    clean_mime = (mime_type or "audio/webm").split(";")[0].strip().lower()
    if clean_mime not in (
        "audio/webm",
        "audio/ogg",
        "audio/mp4",
        "audio/mpeg",
        "audio/mp3",
        "audio/wav",
        "audio/aac",
        "audio/x-m4a",
        "video/webm",
    ):
        clean_mime = "audio/webm"
    if clean_mime == "video/webm":
        clean_mime = "audio/webm"

    caption_note = f"\nהערת טקסט מצורפת: '{caption.strip()}'." if caption and caption.strip() else ""
    prompt = f"""האזן להקלטה הקולית של המשתמש (בעברית) שבה הוא מדווח מה אכל או שתה.{caption_note}
1. תמלל והבן בדיוק את המנות, המשקאות או המוצרים שהמשתמש ציין בהקלטה.
2. חשב הערכת קלוריות כוללת וחלבון בגרמים בצורה מציאותית ומדויקת לישראל.
3. החזר תיאור תמציתי, טבעי וברור בעברית בשדה summary (למשל: "שתי ביצים מקושקשות, חצי קוטג' והפוך קטן").
4. ברשימת items, פרט כל פריט בנפרד עם שמו (name), כמות הקלוריות שלו (calories) וגרם החלבון שלו (protein_g).
5. אם בהקלטה מוזכר כל פריט אוכל או שתייה (כולל קפה, נשנוש, פרי או ארוחה) סמן תמיד is_meal=True. רק אם ההקלטה ריקה לחלוטין או אינה קשורה לאוכל/שתייה סמן is_meal=False."""

    last_err = None
    for model_name in ("gemini-2.5-flash", "gemini-2.0-flash"):
        try:
            response = ai_client.models.generate_content(
                model=model_name,
                contents=[
                    types.Part.from_bytes(data=audio_bytes, mime_type=clean_mime),
                    prompt,
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=NutritionExtraction,
                ),
            )
            return NutritionExtraction.model_validate_json(response.text)
        except Exception as e:
            last_err = e
            print(f"ERROR analyze_meal_audio ({model_name}): {type(e).__name__} - {e}")

    return NutritionExtraction(is_meal=False, calories=0, protein_g=0, summary=f"API_ERROR: {type(last_err).__name__}: {last_err}")


def generate_daily_coach_insight(day_summary: dict, daily_trend: List[dict]) -> dict:
    total_cal = int(day_summary.get("total_calories", 0))
    target_cal = max(500, int(day_summary.get("target_calories", 1800)))
    total_prot = float(day_summary.get("total_protein", 0.0))
    target_prot = max(20.0, float(day_summary.get("target_protein", 120.0)))
    rem_cal = int(day_summary.get("remaining_calories", target_cal - total_cal))
    rem_prot = max(0.0, round(target_prot - total_prot, 1))
    meals = day_summary.get("meals", [])

    # Heuristic fallback calculation
    cal_ratio = total_cal / target_cal if target_cal else 0
    prot_ratio = min(1.15, total_prot / target_prot) if target_prot else 0
    if total_cal == 0:
        heuristic_score = 50
    else:
        cal_score = max(0, 100 - int(abs(1.0 - cal_ratio) * 90))
        prot_score = min(100, int(prot_ratio * 100))
        heuristic_score = max(15, min(100, int(cal_score * 0.55 + prot_score * 0.45)))

    if not ai_client:
        if total_cal == 0:
            return {
                "score": 60,
                "headline": "היום שלך מוכן להמראה! 🚀",
                "feedback": "עדיין לא תועדו ארוחות ליום זה. התחל בתיעוד ארוחת הבוקר או הקפה הראשון כדי שהמאמן יוכל לנתח את הקצב שלך.",
                "next_meal_suggestion": f"מומלץ לפתוח בארוחה עשירה בחלבון (כ-30 גרם חלבון) כמו חביתה מ-2 ביצים וקוטג׳ 5%.",
                "tip": "תיעוד בזמן אמת (מיד אחרי הארוחה או בהקלטה קולית קצרה) מעלה ב-80% את הסיכוי לעמוד ביעד היומי.",
            }
        return {
            "score": heuristic_score,
            "headline": "קצב מצוין! שומרים על מיקוד ביעדים 💪" if heuristic_score >= 75 else "ממשיכים לדייק את האיזון היומי 🎯",
            "feedback": f"צרכת עד כה {total_cal} מתוך {target_cal} קק״ל ו-{total_prot:.0f} מתוך {target_prot:.0f} גרם חלבון ({len(meals)} ארוחות מתועדות).",
            "next_meal_suggestion": (
                f"נותרו לך {rem_cal} קק״ל ו-{rem_prot:.0f} גרם חלבון — מומלץ לבחור מנה ממוקדת חלבון כמו סלט טונה וביצה או יוגורט PRO."
                if rem_cal > 150
                else "הגעת קרוב מאוד ליעד הקלורי היומי — אם אתה רעב, העדף ירקות ירוקים או חלבון רזה ושתה מים."
            ),
            "tip": "שילוב מנת חלבון בכל ארוחה שומר על שובע ממושך ומונע נשנושים בשעות הערב.",
        }

    meals_desc = "\n".join(
        f"- {m.get('log_time', '')}: {m.get('description', '')} ({m.get('calories', 0)} קק\"ל, {m.get('protein_g', 0)}g חלבון)"
        for m in meals
    ) or "טרם נרשמו ארוחות היום."

    active_days = [d for d in (daily_trend or []) if d.get("calories", 0) > 0]
    avg_cal = int(sum(d["calories"] for d in active_days) / len(active_days)) if active_days else 0
    avg_prot = round(sum(d["protein_g"] for d in active_days) / len(active_days), 1) if active_days else 0.0

    prompt = f"""אתה מאמן תזונה וכושר אישי חכם, מעודד ומקצועי בישראל.
נתח את הנתונים של המשתמש ליום {day_summary.get('date')} והחזר משוב מובנה בעברית:

נתוני היום:
- קלוריות שנצרכו: {total_cal} מתוך יעד של {target_cal} קק"ל (יתרה: {rem_cal} קק"ל)
- חלבון שנצרך: {total_prot:.1f} מתוך יעד של {target_prot:.0f} גרם (יתרה להשלמה: {rem_prot:.1f} גרם)
- פירוט ארוחות היום:
{meals_desc}

מגמה ב-7 הימים האחרונים ({len(active_days)} ימים מתועדים):
- ממוצע קלוריות יומי: {avg_cal} קק"ל
- ממוצע חלבון יומי: {avg_prot} גרם

הנחיות:
1. קבע ציון תזונה יומי (score) בין 0 ל-100 המשקף את איכות העמידה ביעד הקלוריות, יחס החלבון ואיכות הבחירות (אם היום רק התחיל ויש מעט ארוחות אך הן טובות, תן ציון מעודד בהתאם לקצב).
2. כתוב כותרת קצרה ואנרגטית (headline).
3. תן משוב חכם ומדויק (feedback) שמתייחס לארוחות שנאכלו היום וליחס בין קלוריות לחלבון (ולמגמה השבועית אם רלוונטי).
4. תן המלצה קונקרטית ומעשית לארוחה הבאה או להמשך היום (next_meal_suggestion) שתפגע בדיוק ביתרת הקלוריות ({rem_cal} קק"ל) והחלבון ({rem_prot:.0f}g) עם מאכלים ישראליים נפוצים.
5. תן טיפ קצר ומנצח (tip)."""

    try:
        response = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=DailyCoachInsight,
            ),
        )
        parsed = DailyCoachInsight.model_validate_json(response.text)
        data = parsed.model_dump()
        data["score"] = max(0, min(100, int(data.get("score", heuristic_score))))
        return data
    except Exception as e:
        print(f"ERROR generate_daily_coach_insight: {type(e).__name__} - {e}")
        return {
            "score": heuristic_score,
            "headline": "המאמן האישי שלך מוכן 💪",
            "feedback": f"עד כה תיעדת {total_cal}/{target_cal} קק״ל ו-{total_prot:.0f}/{target_prot:.0f} גרם חלבון.",
            "next_meal_suggestion": f"נותרו {rem_cal} קק״ל ו-{rem_prot:.0f} גרם חלבון להשלמת היעד היומי.",
            "tip": "הקפד על שתיית מים וחלוקת חלבון מאוזנת לאורך היום.",
        }


def generate_ai_recipes(remaining_cal: int, remaining_prot: float, preference: str = "") -> List[dict]:
    budget_cal = max(150, min(remaining_cal, 1200))
    budget_prot = max(10, round(remaining_prot))
    if not ai_client:
        # Local preview fallback when GEMINI_API_KEY is not set locally
        pref_label = f" ({preference})" if preference else ""
        return [
            {
                "name": f"ראפ טורטייה עתיר חלבון{pref_label}",
                "category": "מותאם אישית",
                "prep_time": "8 דקות",
                "description": f"טורטייה קלה במילוי חלבון מותאמת ליתרה של כ-{budget_cal} קק״ל.",
                "calories": min(budget_cal, 410),
                "protein_g": float(min(budget_prot, 36)),
                "ingredients": [
                    "טורטייה מחיטה מלאה / קלה",
                    "120 גרם חזה עוף צלוי או טונה במים",
                    "כף גבינת שמנת 5% או כפית טחינה",
                    "חסה, עגבנייה ומלפפון חמוץ",
                ],
                "preparation_steps": [
                    "מחממים קלות את הטורטייה על מחבת יבשה.",
                    "מורחים את הממרח, מניחים את מקור החלבון והירקות ומגלגלים הדוק.",
                ],
            },
            {
                "name": f"פנקייק חלבון שיבולת שועל{pref_label}",
                "category": "מותאם אישית",
                "prep_time": "10 דקות",
                "description": "פנקייק משביע וטעים מ-3 מרכיבים בלבד, מעולה לארוחה קלה או נשנוש.",
                "calories": min(budget_cal, 330),
                "protein_g": float(min(budget_prot, 28)),
                "ingredients": [
                    "2 ביצים (או ביצה + סקופ חלבון)",
                    "חצי גביע גבינה לבנה 5% / קוטג׳",
                    "3 כפות שיבולת שועל דקה",
                    "חצי כפית תמצית וניל וקינמון",
                ],
                "preparation_steps": [
                    "מערבבים את כל המרכיבים לבלילה אחידה.",
                    "מטגנים על מחבת נון-סטיק חמה כ-2 דקות מכל צד עד להזהבה.",
                ],
            },
        ]
    pref_text = f"העדפת המשתמש: {preference}." if preference else "מותאם למטבח הישראלי (מוצרים זמינים בסופר בישראל)."
    prompt = f"""הצע 3 מתכונים מעולים, טעימים ופרקטיים בעברית שמתאימים ליתרה היומית של המשתמש:
יתרת קלוריות רצויה למנה: עד סביבות {budget_cal} קק"ל (או חלוקה הגיונית של היתרה).
יתרת חלבון רצויה: דגש על חלבון גבוה (יעד: כ-{budget_prot} גרם חלבון).
{pref_text}
הקפד על ערכים תזונתיים מציאותיים ומדויקים למוצרים ישראליים (כגון קוטג' תנובה, טונה, חזה עוף, לחם קל, יוגורט PRO, ביצים וכו')."""
    try:
        response = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=RecipeSuggestionList,
            ),
        )
        parsed = RecipeSuggestionList.model_validate_json(response.text)
        return [r.model_dump() for r in parsed.recipes]
    except Exception as e:
        print(f"ERROR generate_ai_recipes: {type(e).__name__} - {e}")
        return []


# ---------------------------------------------------------------------------
# Curated Israeli High-Protein Recipes
# ---------------------------------------------------------------------------

CURATED_RECIPES = [
    {
        "name": "בוקר ישראלי עתיר חלבון",
        "category": "בוקר",
        "prep_time": "7 דקות",
        "description": "חביתה מ-2 ביצים עם קוטג' 5%, 2 פרוסות לחם כוסמין קל וירקות טריים.",
        "calories": 390,
        "protein_g": 34.0,
        "ingredients": [
            "2 ביצים (L)",
            "חצי גביע קוטג' 5% (125 גרם)",
            "2 פרוסות לחם קל מחיטה מלאה / כוסמין",
            "מלפפון, עגבנייה ופלפל חתוכים",
            "תרסיס שמן זית למחבת",
        ],
        "preparation_steps": [
            "מחממים מחבת נון-סטיק עם מעט תרסיס שמן ומכינים חביתה או מקושקשת מ-2 ביצים.",
            "מורחים את הקוטג' על פרוסות הלחם הקל או מגישים לצד החביתה.",
            "חותכים ירקות טריים, מתבלים במעט מלח ופלפל ומגישים.",
        ],
    },
    {
        "name": "קערת חזה עוף וקינואה ים-תיכונית",
        "category": "צהריים",
        "prep_time": "20 דקות",
        "description": "חזה עוף עסיסי על הפלנצ'ה עם קינואה, עשבי תיבול וסלט קצוץ בלימון ושמן זית.",
        "calories": 540,
        "protein_g": 48.0,
        "ingredients": [
            "160 גרם חזה עוף פרוס",
            "כוס קינואה מבושלת (כ-150 גרם)",
            "מלפפון, עגבנייה, בצל סגול ופטרוזיליה קצוצה",
            "כפית שמן זית ומיץ מחצי לימון",
            "תבלינים: פפריקה מתוקה, שום גבישי, מלח ופלפל",
        ],
        "preparation_steps": [
            "מתבלים את חזה העוף בפפריקה, שום גבישי, מלח ופלפל.",
            "צולים על מחבת פסים חמה כ-3-4 דקות מכל צד עד להזהבה עסיסית.",
            "מניחים בקערה את הקינואה החמה, הסלט הקצוץ ורצועות העוף ומתבלים בלימון ושמן זית.",
        ],
    },
    {
        "name": "שקשוקה ירוקה עשירה בחלבון",
        "category": "ערב",
        "prep_time": "15 דקות",
        "description": "שקשוקה מתרד, קישואים ו-2 ביצים עם קוביות גבינה בולגרית 5% ופרוסת לחם מחמצת.",
        "calories": 420,
        "protein_g": 31.0,
        "ingredients": [
            "2 ביצים",
            "2 כוסות עלי תרד טריים",
            "קישוא בינוני מגורד או חתוך לקוביות",
            "60 גרם גבינה בולגרית / פטה 5%",
            "פרוסת לחם מחמצת או 2 פרוסות לחם קל",
            "שן שום כתושה וכפית שמן זית",
        ],
        "preparation_steps": [
            "מקפיצים את הקישוא והשום בכפית שמן זית למשך 4 דקות ומוסיפים את התרד עד שהוא מתרכך.",
            "יוצרים 2 גומות, שוברים לתוכן את הביצים ומפזרים מעל את הבולגרית 5%.",
            "מכסים ומבשלים 5-6 דקות עד שהחלבון מתייצב והחלמון במידת העשייה האהובה.",
        ],
    },
    {
        "name": "סלט טונה וביצה קשה ב-5 דקות",
        "category": "ערב",
        "prep_time": "5 דקות",
        "description": "ארוחת ערב מהירה וסופר משביעה עם טונה במים, ביצה קשה, ירקות וטחינה גולמית.",
        "calories": 430,
        "protein_g": 41.0,
        "ingredients": [
            "קופסת טונה במים מסוננת (כ-110 גרם מסונן)",
            "ביצה קשה אחת",
            "כף טחינה מוכנה (או כפית טחינה גולמית)",
            "חסה, מלפפון, עגבניות שרי וגמבה",
            "2 פריכיות אורז מלא או 2 קרקרים מחיטה מלאה",
        ],
        "preparation_steps": [
            "קוצצים את הירקות לקערה גדולה ומוסיפים את הטונה המסוננת.",
            "חותכים את הביצה הקשה לרבעים ומניחים מעל.",
            "מזלפים כף טחינה ומיץ לימון טרי ומגישים לצד הפריכיות.",
        ],
    },
    {
        "name": "מוזלי יוגורט PRO ופירות יער",
        "category": "נשנוש",
        "prep_time": "3 דקות",
        "description": "נשנוש מתוק, מרענן ועשיר בחלבון שסוגר פינה אחרי אימון או אחר הצהריים.",
        "calories": 240,
        "protein_g": 23.0,
        "ingredients": [
            "גביע יוגורט חלבון טבעי / וניל (20 גרם חלבון)",
            "חצי כוס פירות יער קפואים/טריים או חצי בננה",
            "כף שיבולת שועל או גרנולה ללא סוכר",
            "קורט קינמון",
        ],
        "preparation_steps": [
            "מעבירים את היוגורט לקערה.",
            "מפזרים מעל את פירות היער, שיבולת השועל והקינמון ואוכלים מיד.",
        ],
    },
    {
        "name": "טוסט פיתה קלה עם צהובה 9% וביצה",
        "category": "בוקר",
        "prep_time": "6 דקות",
        "description": "טוסט ישראלי קלאסי בגרסה חטובה ומשביעה עם 30 גרם חלבון.",
        "calories": 340,
        "protein_g": 30.0,
        "ingredients": [
            "פיתה קלה (99 קק\"ל)",
            "2 פרוסות גבינה צהובה 9% (50 גרם)",
            "ביצה קשה פרוסה או כף קוטג' 5%",
            "פרוסות עגבנייה, זיתים ואורגנו",
        ],
        "preparation_steps": [
            "פותחים את הפיתה הקלה ומניחים בתוכה את הגבינה הצהובה, פרוסות העגבנייה והביצה.",
            "מתבלים באורגנו וקולים בטוסטר לחיצה 3-4 דקות עד להשחמה קריספית.",
        ],
    },
]


# ---------------------------------------------------------------------------
# Telegram Bot Handlers (Multi-User + Voice + Photo + Text)
# ---------------------------------------------------------------------------

tg_app = ApplicationBuilder().token(BOT_TOKEN).build() if BOT_TOKEN else None

if tg_app:
    async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "היי! הבוט של NutriAgent AI מחובר ומסונכרן 🚀\n"
            "• שלח בטקסט חופשי מה אכלת או שתית.\n"
            "• שלח 🎙️ הודעה קולית ודבר חופשי — ה-AI יתמלל ויחשב קלוריות וחלבון!\n"
            "• שלח 📸 תמונה של הצלחת או של תווית המוצר לניתוח אוטומטי.\n"
            "• שלח /link <קוד או אימייל> לקישור הבוט לחשבון האישי שלך באפליקציה.\n"
            "• שלח /weight 78.5 לתיעוד משקל יומי.\n"
            "• שלח /push לשליחת התראת פוש לנייד.\n"
            "• שלח /status לסיכום היומי שלך."
        )

    async def link_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text(
                "🔗 לקישור הטלגרם לחשבון שלך באפליקציה:\n"
                "שלח `/link` ואחריו האימייל שלך או קוד הקישור שמופיע בפרופיל באפליקציה.\n"
                "לדוגמה: `/link user@gmail.com`"
            )
            return
        code_or_email = " ".join(context.args).strip()
        tg_uid = update.effective_user.id if update.effective_user else 0
        matched = link_telegram_Account(tg_uid, code_or_email)
        if matched:
            await update.message.reply_text(
                f"✅ הטלגרם קושר בהצלחה לחשבון של {matched.get('name')} ({matched.get('email')})!\n"
                "מעכשיו כל הודעה, תמונה או הקלטה קול שתשלח כאן יישמרו ישירות בחשבון האישי שלך."
            )
        else:
            await update.message.reply_text(
                "❌ לא נמצא חשבון מתאים.\n"
                "היכנס תחילה לאפליקציית ה-Web, התחבר עם חשבון ה-Google שלך, ונסה שוב עם האימייל או קוד הקישור שלך."
            )

    async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = resolve_telegram_user_id(update)
        s = get_day_summary(user_id=uid)
        w = get_weights_db(limit=5, user_id=uid)
        w_line = f"\n⚖️ משקל נוכחי: {w['latest_weight']} ק\"ג" if w.get("latest_weight") else ""
        await update.message.reply_text(
            f"📊 סיכום להיום:\n"
            f"🔥 קלוריות: {s['total_calories']} / {s['target_calories']} קק\"ל (נותרו: {s['remaining_calories']})\n"
            f"💪 חלבון: {s['total_protein']:.0f} / {s['target_protein']} גרם{w_line}"
        )

    async def push_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = resolve_telegram_user_id(update)
        s = get_day_summary(user_id=uid)
        custom_msg = " ".join(context.args).strip() if context.args else ""
        title = "NutriAgent AI 🔔"
        body = custom_msg or (
            f"🔥 היום: {s['total_calories']}/{s['target_calories']} קק\"ל "
            f"(נותרו {s['remaining_calories']}) | 💪 חלבון: {s['total_protein']:.0f}g"
        )
        result = send_web_push_to_all(title=title, body=body, tag="nutriagent-tg-push", user_id=uid)
        if result["sent"] > 0:
            await update.message.reply_text(f"📲 נשלחה התראת פוש ל-{result['sent']} מכשירים מחוברים!")
        else:
            await update.message.reply_text(
                "לא נמצאו מכשירים רשומים להתראות פוש 📱\n"
                "היכנס לאפליקציה בנייד ולחץ על סמל הפעמון 🔔 בראש המסך כדי להפעיל התראות."
            )

    async def weight_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = resolve_telegram_user_id(update)
        if not context.args:
            w = get_weights_db(limit=5, user_id=uid)
            if w.get("latest_weight"):
                await update.message.reply_text(f"⚖️ המשקל האחרון שתועד: {w['latest_weight']} ק\"ג\nלתיעוד שקילה חדשה שלח למשל: /weight 78.5")
            else:
                await update.message.reply_text("לתיעוד שקילה חדשה שלח למשל: /weight 78.5")
            return
        try:
            val = float(context.args[0].replace(",", "."))
            if val < 25 or val > 350:
                raise ValueError()
            log_weight_db(val, user_id=uid)
            w = get_weights_db(limit=60, user_id=uid)
            diff_str = ""
            if w.get("weight_change"):
                sign = "+" if w["weight_change"] > 0 else ""
                diff_str = f" ({sign}{w['weight_change']} ק\"ג מההתחלה)"
            await update.message.reply_text(f"⚖️ המשקל עודכן בהצלחה: {val:.1f} ק\"ג{diff_str}")
            send_web_push_to_all(
                title="⚖️ שקילה חדשה נשמרה!",
                body=f"משקל נוכחי: {val:.1f} ק\"ג{diff_str}",
                tag="nutriagent-weight",
                user_id=uid,
            )
        except ValueError:
            await update.message.reply_text("נא להזין משקל תקין במספרים, למשל: /weight 78.5")

    async def text_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = resolve_telegram_user_id(update)
        res = analyze_meal(update.message.text)
        if not res.is_meal:
            await update.message.reply_text("לא זיהיתי ארוחה או שתייה בהודעה 🤔 נסה לתאר שוב מה אכלת.")
            return

        items_data = [item.model_dump() for item in res.items] if res.items else []
        log_meal_db(res.summary, res.calories, res.protein_g, items=items_data, source="telegram", user_id=uid)
        s = get_day_summary(user_id=uid)

        breakdown_text = ""
        if res.items and len(res.items) > 1:
            breakdown_lines = "\n".join(f"  • {it.name}: {it.calories} קק\"ל ({it.protein_g:.0f}g חלבון)" for it in res.items)
            breakdown_text = f"\n📋 פירוט רכיבים:\n{breakdown_lines}\n"

        await update.message.reply_text(
            f"✅ נרשם: {res.summary}\n"
            f"🔥 +{res.calories} קק\"ל | 💪 +{res.protein_g:.0f}g חלבון\n"
            f"{breakdown_text}"
            f"💰 נותר להיום: {s['remaining_calories']}/{s['target_calories']} קק\"ל (סה\"כ: {s['total_calories']})"
        )
        send_web_push_to_all(
            title=f"✅ נרשם: {res.summary}",
            body=f"🔥 +{res.calories} קק\"ל | 💪 +{res.protein_g:.0f}g חלבון • נותר להיום: {s['remaining_calories']} קק\"ל",
            tag="nutriagent-meal",
            user_id=uid,
        )

    async def photo_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.message.photo:
            return
        uid = resolve_telegram_user_id(update)
        status_reply = await update.message.reply_text("📸 מנתח את התמונה ב-AI...")
        try:
            photo_file = await update.message.photo[-1].get_file()
            img_bytes = bytes(await photo_file.download_as_bytearray())
            caption = update.message.caption or ""
            forensics = compute_image_forensics_and_histogram(img_bytes)
            res = analyze_meal_image(img_bytes, mime_type="image/jpeg", caption=caption, forensics=forensics)
            if not res.is_meal:
                save_vision_analysis_log_db(uid, res, forensics, source="telegram-photo", caption=caption, meal_id="")
                await status_reply.edit_text("לא זיהיתי אוכל, משקה או תווית מזון בתמונה 🤔 נסה לצלם שוב או להוסיף כיתוב.")
                return

            items_data = [item.model_dump() for item in res.items] if res.items else []
            entry = log_meal_db(res.summary, res.calories, res.protein_g, items=items_data, source="telegram-photo", user_id=uid)
            save_vision_analysis_log_db(uid, res, forensics, source="telegram-photo", caption=caption, meal_id=(entry or {}).get("id", ""))
            s = get_day_summary(user_id=uid)

            breakdown_text = ""
            if res.items and len(res.items) > 1:
                breakdown_lines = "\n".join(f"  • {it.name}: {it.calories} קק\"ל ({it.protein_g:.0f}g חלבון)" for it in res.items)
                breakdown_text = f"\n📋 פירוט רכיבים:\n{breakdown_lines}\n"

            await status_reply.edit_text(
                f"📸 נרשם מתמונה: {res.summary}\n"
                f"🔥 +{res.calories} קק\"ל | 💪 +{res.protein_g:.0f}g חלבון\n"
                f"{breakdown_text}"
                f"💰 נותר להיום: {s['remaining_calories']}/{s['target_calories']} קק\"ל (סה\"כ: {s['total_calories']})"
            )
            send_web_push_to_all(
                title=f"📸 נרשם מתמונה: {res.summary}",
                body=f"🔥 +{res.calories} קק\"ל | 💪 +{res.protein_g:.0f}g חלבון • נותר להיום: {s['remaining_calories']} קק\"ל",
                tag="nutriagent-meal",
                user_id=uid,
            )
        except Exception as e:
            print(f"ERROR photo_msg: {type(e).__name__} - {e}")
            await status_reply.edit_text("שגיאה בניתוח התמונה, נסה שוב מאוחר יותר.")

    async def voice_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message:
            return
        voice_obj = update.message.voice or update.message.audio
        if not voice_obj:
            return
        uid = resolve_telegram_user_id(update)
        status_reply = await update.message.reply_text("🎙️ מאזין להקלטה ומחשב ערכים תזונתיים ב-AI...")
        try:
            tg_file = await voice_obj.get_file()
            audio_bytes = bytes(await tg_file.download_as_bytearray())
            mime_type = getattr(voice_obj, "mime_type", None) or "audio/ogg"
            caption = update.message.caption or ""
            res = analyze_meal_audio(audio_bytes, mime_type=mime_type, caption=caption)
            if not res.is_meal:
                await status_reply.edit_text("לא זיהיתי דיווח על אוכל או שתייה בהקלטה 🤔 נסה להקליט שוב בבירור.")
                return

            items_data = [item.model_dump() for item in res.items] if res.items else []
            log_meal_db(res.summary, res.calories, res.protein_g, items=items_data, source="telegram-voice", user_id=uid)
            s = get_day_summary(user_id=uid)

            breakdown_text = ""
            if res.items and len(res.items) > 1:
                breakdown_lines = "\n".join(f"  • {it.name}: {it.calories} קק\"ל ({it.protein_g:.0f}g חלבון)" for it in res.items)
                breakdown_text = f"\n📋 פירוט רכיבים:\n{breakdown_lines}\n"

            await status_reply.edit_text(
                f"🎙️ נרשם מהקלטה קולית: {res.summary}\n"
                f"🔥 +{res.calories} קק\"ל | 💪 +{res.protein_g:.0f}g חלבון\n"
                f"{breakdown_text}"
                f"💰 נותר להיום: {s['remaining_calories']}/{s['target_calories']} קק\"ל (סה\"כ: {s['total_calories']})"
            )
            send_web_push_to_all(
                title=f"🎙️ נרשם מהקלטה: {res.summary}",
                body=f"🔥 +{res.calories} קק\"ל | 💪 +{res.protein_g:.0f}g חלבון • נותר להיום: {s['remaining_calories']} קק\"ל",
                tag="nutriagent-meal",
                user_id=uid,
            )
        except Exception as e:
            print(f"ERROR voice_msg: {type(e).__name__} - {e}")
            await status_reply.edit_text("שגיאה בפענוח ההקלטה הקולית, נסה שוב.")

    tg_app.add_handler(CommandHandler("start", start_cmd))
    tg_app.add_handler(CommandHandler("link", link_cmd))
    tg_app.add_handler(CommandHandler("status", status_cmd))
    tg_app.add_handler(CommandHandler("push", push_cmd))
    tg_app.add_handler(CommandHandler("weight", weight_cmd))
    tg_app.add_handler(MessageHandler(filters.PHOTO, photo_msg))
    tg_app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, voice_msg))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_msg))


# ---------------------------------------------------------------------------
# Favorites & Weight Firestore Helpers (Multi-User Aware)
# ---------------------------------------------------------------------------

def get_favorites_db(user_id: str = "default") -> List[dict]:
    uid = _normalize_user_id(user_id)
    if not db:
        user_favs = [f for f in _LOCAL_FAVORITES if _doc_matches_user(f, uid)]
        return user_favs if user_favs else [dict(f) for f in DEFAULT_FAVORITES]
    try:
        docs = list(db.collection("favorites").order_by("created_at").stream())
        user_docs = [doc for doc in docs if _doc_matches_user(doc.to_dict(), uid)]
        if not user_docs:
            # Seed default favorites for this user on first use
            seeded = []
            for f in DEFAULT_FAVORITES:
                doc_id = f["id"] if uid == "default" else f"{uid}_{f['id']}"
                doc_ref = db.collection("favorites").document(doc_id)
                doc_ref.set({
                    "user_id": uid,
                    "icon": f["icon"],
                    "label": f["label"],
                    "calories": int(f["calories"]),
                    "protein_g": float(f["protein_g"]),
                    "created_at": firestore.SERVER_TIMESTAMP,
                })
                seeded.append({**f, "id": doc_id})
            return seeded
        result = []
        for doc in user_docs:
            d = doc.to_dict()
            result.append({
                "id": doc.id,
                "icon": d.get("icon", "⭐"),
                "label": d.get("label", ""),
                "calories": int(d.get("calories", 0)),
                "protein_g": round(float(d.get("protein_g", 0.0)), 1),
            })
        return result
    except Exception as e:
        print(f"ERROR get_favorites_db: {e}")
        return list(DEFAULT_FAVORITES)


def add_favorite_db(label: str, calories: int, protein_g: float, icon: str = "⭐", user_id: str = "default") -> dict:
    uid = _normalize_user_id(user_id)
    clean_icon = (icon or "⭐").strip() or "⭐"
    if not db:
        entry = {
            "id": f"fav-{int(datetime.now().timestamp() * 1000)}",
            "user_id": uid,
            "icon": clean_icon,
            "label": label.strip(),
            "calories": int(calories),
            "protein_g": round(float(protein_g), 1),
        }
        _LOCAL_FAVORITES.append(entry)
        return entry
    doc_data = {
        "user_id": uid,
        "icon": clean_icon,
        "label": label.strip(),
        "calories": int(calories),
        "protein_g": round(float(protein_g), 1),
        "created_at": firestore.SERVER_TIMESTAMP,
    }
    _, doc_ref = db.collection("favorites").add(doc_data)
    return {
        "id": doc_ref.id,
        "icon": clean_icon,
        "label": label.strip(),
        "calories": int(calories),
        "protein_g": round(float(protein_g), 1),
    }


def delete_favorite_db(fav_id: str, user_id: str = "default") -> bool:
    uid = _normalize_user_id(user_id)
    if not db:
        global _LOCAL_FAVORITES
        orig_len = len(_LOCAL_FAVORITES)
        _LOCAL_FAVORITES = [f for f in _LOCAL_FAVORITES if not (f.get("id") == fav_id and (_doc_matches_user(f, uid) or is_admin_user_id(uid)))]
        return len(_LOCAL_FAVORITES) < orig_len
    try:
        doc_ref = db.collection("favorites").document(fav_id)
        doc = doc_ref.get()
        if not doc.exists:
            return False
        doc_data = doc.to_dict() or {}
        if not _doc_matches_user(doc_data, uid) and not is_admin_user_id(uid):
            return False
        doc_ref.delete()
        return True
    except Exception as e:
        print(f"ERROR delete_favorite_db: {e}")
        return False


def get_weights_db(limit: int = 60, user_id: str = "default") -> dict:
    uid = _normalize_user_id(user_id)
    entries = []
    if not db:
        user_w = [w for w in _LOCAL_WEIGHTS if _doc_matches_user(w, uid)]
        entries = sorted(user_w, key=lambda x: x["log_date"])
    else:
        try:
            docs = db.collection("weight_logs").order_by("log_date", direction=firestore.Query.DESCENDING).limit(200).stream()
            for doc in docs:
                d = doc.to_dict()
                if not _doc_matches_user(d, uid):
                    continue
                entries.append({
                    "id": doc.id,
                    "log_date": d.get("log_date", doc.id),
                    "weight_kg": round(float(d.get("weight_kg", 0.0)), 1),
                    "note": d.get("note", ""),
                })
                if len(entries) >= limit:
                    break
            entries.sort(key=lambda x: x["log_date"])
        except Exception as e:
            print(f"ERROR get_weights_db: {e}")

    latest = entries[-1]["weight_kg"] if entries else None
    start = entries[0]["weight_kg"] if entries else None
    change = round(latest - start, 1) if (latest is not None and start is not None) else 0.0
    return {
        "entries": entries,
        "latest_weight": latest,
        "start_weight": start,
        "weight_change": change,
    }


def log_weight_db(weight_kg: float, log_date: Optional[str] = None, note: str = "", user_id: str = "default") -> dict:
    uid = _normalize_user_id(user_id)
    d_str = log_date or str(date.today())
    w_val = round(float(weight_kg), 1)
    doc_id = d_str if uid == "default" else f"{uid}_{d_str}"
    entry = {
        "id": doc_id,
        "user_id": uid,
        "log_date": d_str,
        "weight_kg": w_val,
        "note": (note or "").strip(),
    }
    if not db:
        global _LOCAL_WEIGHTS
        _LOCAL_WEIGHTS = [w for w in _LOCAL_WEIGHTS if not (w.get("log_date") == d_str and _doc_matches_user(w, uid))]
        _LOCAL_WEIGHTS.append(entry)
        return entry

    db.collection("weight_logs").document(doc_id).set({
        "user_id": uid,
        "log_date": d_str,
        "weight_kg": w_val,
        "note": (note or "").strip(),
        "updated_at": firestore.SERVER_TIMESTAMP,
    }, merge=True)
    return entry


def delete_weight_db(entry_id: str, user_id: str = "default") -> bool:
    uid = _normalize_user_id(user_id)
    if not db:
        global _LOCAL_WEIGHTS
        orig_len = len(_LOCAL_WEIGHTS)
        _LOCAL_WEIGHTS = [w for w in _LOCAL_WEIGHTS if not ((w.get("id") == entry_id or w.get("log_date") == entry_id) and (_doc_matches_user(w, uid) or is_admin_user_id(uid)))]
        return len(_LOCAL_WEIGHTS) < orig_len
    try:
        doc_ref = db.collection("weight_logs").document(entry_id)
        doc = doc_ref.get()
        if not doc.exists:
            return False
        doc_data = doc.to_dict() or {}
        if not _doc_matches_user(doc_data, uid) and not is_admin_user_id(uid):
            return False
        doc_ref.delete()
        return True
    except Exception as e:
        print(f"ERROR delete_weight_db: {e}")
        return False


# ---------------------------------------------------------------------------
# Web Push (VAPID) Helpers
# ---------------------------------------------------------------------------

def _generate_vapid_keypair() -> dict:
    pk = ec.generate_private_key(ec.SECP256R1())
    raw_priv = pk.private_numbers().private_value.to_bytes(32, "big")
    raw_pub = pk.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    priv_b64 = base64.urlsafe_b64encode(raw_priv).rstrip(b"=").decode("ascii")
    pub_b64 = base64.urlsafe_b64encode(raw_pub).rstrip(b"=").decode("ascii")
    return {"private_key": priv_b64, "public_key": pub_b64}


def get_or_create_vapid_keys() -> dict:
    global _LOCAL_VAPID
    env_pub = os.getenv("VAPID_PUBLIC_KEY")
    env_priv = os.getenv("VAPID_PRIVATE_KEY")
    if env_pub and env_priv:
        return {"public_key": env_pub.strip(), "private_key": env_priv.strip()}

    if _LOCAL_VAPID:
        return _LOCAL_VAPID

    if db:
        try:
            doc_ref = db.collection("push_config").document("vapid")
            doc = doc_ref.get()
            if doc.exists:
                data = doc.to_dict()
                if data.get("public_key") and data.get("private_key"):
                    _LOCAL_VAPID = {
                        "public_key": data["public_key"],
                        "private_key": data["private_key"],
                    }
                    return _LOCAL_VAPID
            keys = _generate_vapid_keypair()
            doc_ref.set({
                "public_key": keys["public_key"],
                "private_key": keys["private_key"],
                "created_at": firestore.SERVER_TIMESTAMP,
            })
            _LOCAL_VAPID = keys
            return _LOCAL_VAPID
        except Exception as e:
            print(f"ERROR get_or_create_vapid_keys: {e}")

    _LOCAL_VAPID = _generate_vapid_keypair()
    return _LOCAL_VAPID


def _sub_doc_id(endpoint: str) -> str:
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()[:32]


def save_push_subscription_db(endpoint: str, p256dh: str, auth: str, user_agent: str = "", user_id: str = "default") -> dict:
    uid = _normalize_user_id(user_id)
    sub_id = _sub_doc_id(endpoint)
    record = {
        "id": sub_id,
        "user_id": uid,
        "endpoint": endpoint,
        "keys": {"p256dh": p256dh, "auth": auth},
        "user_agent": (user_agent or "")[:250],
    }
    global _LOCAL_PUSH_SUBS
    _LOCAL_PUSH_SUBS = [s for s in _LOCAL_PUSH_SUBS if s.get("endpoint") != endpoint]
    _LOCAL_PUSH_SUBS.append(record)

    if db:
        try:
            db.collection("push_subscriptions").document(sub_id).set({
                **record,
                "updated_at": firestore.SERVER_TIMESTAMP,
            }, merge=True)
        except Exception as e:
            print(f"ERROR save_push_subscription_db: {e}")
    return record


def remove_push_subscription_db(endpoint: str) -> bool:
    sub_id = _sub_doc_id(endpoint)
    global _LOCAL_PUSH_SUBS
    _LOCAL_PUSH_SUBS = [s for s in _LOCAL_PUSH_SUBS if s.get("endpoint") != endpoint]
    if db:
        try:
            db.collection("push_subscriptions").document(sub_id).delete()
        except Exception as e:
            print(f"ERROR remove_push_subscription_db: {e}")
    return True


def get_push_subscriptions_db(user_id: Optional[str] = None) -> List[dict]:
    uid = _normalize_user_id(user_id) if user_id else None
    if not db:
        if not uid:
            return list(_LOCAL_PUSH_SUBS)
        return [s for s in _LOCAL_PUSH_SUBS if _doc_matches_user(s, uid)]
    try:
        docs = db.collection("push_subscriptions").stream()
        subs = []
        for doc in docs:
            d = doc.to_dict()
            if uid and not _doc_matches_user(d, uid):
                continue
            if d.get("endpoint") and d.get("keys"):
                subs.append({
                    "id": doc.id,
                    "user_id": d.get("user_id", "default"),
                    "endpoint": d["endpoint"],
                    "keys": d["keys"],
                    "user_agent": d.get("user_agent", ""),
                })
        return subs
    except Exception as e:
        print(f"ERROR get_push_subscriptions_db: {e}")
        return list(_LOCAL_PUSH_SUBS)


def _is_mobile_ua(user_agent: str) -> bool:
    ua = (user_agent or "").lower()
    return any(k in ua for k in ("android", "iphone", "ipad", "ipod", "mobile"))


def _detect_device_label(user_agent: str) -> str:
    ua = (user_agent or "").lower()
    if "iphone" in ua or "ipad" in ua or "ipod" in ua:
        return "📱 אייפון / iOS"
    if "android" in ua:
        return "📱 נייד אנדרואיד"
    if "macintosh" in ua or "mac os" in ua:
        return "💻 מחשב Mac"
    return "💻 מחשב PC"


def send_web_push_to_all(title: str, body: str, tag: str = "nutriagent-push", url: str = "/", user_id: Optional[str] = None) -> dict:
    subs = get_push_subscriptions_db(user_id=user_id)
    if not subs:
        return {"sent": 0, "failed": 0, "mobile_sent": 0, "total_subscriptions": 0, "devices": []}

    vapid = get_or_create_vapid_keys()
    payload = json.dumps({
        "title": title,
        "body": body,
        "tag": tag,
        "url": url,
    }, ensure_ascii=False)

    sent = 0
    mobile_sent = 0
    failed = 0
    devices_report = []
    for sub in subs:
        ua = sub.get("user_agent", "")
        label = _detect_device_label(ua)
        is_mob = _is_mobile_ua(ua)
        try:
            webpush(
                subscription_info={
                    "endpoint": sub["endpoint"],
                    "keys": sub["keys"],
                },
                data=payload,
                vapid_private_key=vapid["private_key"],
                vapid_claims={"sub": "mailto:push@nutriagent.app"},
                ttl=3600,
                headers={"Urgency": "high"},
            )
            sent += 1
            if is_mob:
                mobile_sent += 1
            devices_report.append({"id": sub["id"], "label": label, "is_mobile": is_mob, "status": "sent"})
        except WebPushException as ex:
            failed += 1
            status_code = getattr(getattr(ex, "response", None), "status_code", None)
            if status_code in (400, 401, 403, 404, 410):
                remove_push_subscription_db(sub["endpoint"])
            else:
                print(f"WARN WebPushException ({status_code}): {ex}")
            devices_report.append({"id": sub["id"], "label": label, "is_mobile": is_mob, "status": f"error-{status_code}"})
        except Exception as e:
            failed += 1
            print(f"ERROR send_web_push_to_all: {type(e).__name__} - {e}")
            devices_report.append({"id": sub["id"], "label": label, "is_mobile": is_mob, "status": "error"})

    return {
        "sent": sent,
        "mobile_sent": mobile_sent,
        "failed": failed,
        "total_subscriptions": len(subs),
        "devices": devices_report,
    }


# ---------------------------------------------------------------------------
# FastAPI Lifespan & Routes
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    if tg_app:
        await tg_app.initialize()
        await tg_app.start()
        if WEBHOOK_URL:
            try:
                sec = get_telegram_webhook_secret()
                await tg_app.bot.set_webhook(
                    url=f"{WEBHOOK_URL.rstrip('/')}/webhook",
                    allowed_updates=Update.ALL_TYPES,
                    secret_token=sec,
                )
                print(f"INFO: Telegram webhook set to {WEBHOOK_URL.rstrip('/')}/webhook with secret_token")
            except Exception as e:
                print(f"WARN: Failed to set Telegram webhook: {e}")
    yield
    if tg_app:
        await tg_app.stop()
        await tg_app.shutdown()


app = FastAPI(title="NutriAgent AI", lifespan=lifespan)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response

if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def serve_pwa():
    if os.path.exists("static/index.html"):
        return FileResponse(
            "static/index.html",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )
    return {"status": "ok", "bot_configured": tg_app is not None}


@app.get("/sw.js")
def serve_service_worker():
    if os.path.exists("static/sw.js"):
        return FileResponse(
            "static/sw.js",
            media_type="application/javascript",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Service-Worker-Allowed": "/",
            },
        )
    raise HTTPException(status_code=404, detail="sw.js not found")


@app.get("/manifest.json")
def serve_manifest():
    if os.path.exists("static/manifest.json"):
        return FileResponse(
            "static/manifest.json",
            media_type="application/manifest+json",
            headers={"Cache-Control": "no-cache"},
        )
    raise HTTPException(status_code=404, detail="manifest.json not found")


@app.get("/api/auth/config")
def api_get_auth_config():
    cfg = get_auth_config_db()
    return {"google_client_id": cfg.get("google_client_id", "")}


@app.put("/api/auth/config")
def api_set_auth_config(req: AuthConfigRequest, request: Request):
    cfg = get_auth_config_db()
    # Allow initial setup when no client_id is set yet; afterwards require admin access
    if cfg.get("google_client_id"):
        require_admin_user(request)
    return set_auth_config_db(req.google_client_id)


@app.post("/api/auth/google")
def api_auth_google(req: GoogleAuthRequest, response: Response):
    if not req.credential or not req.credential.strip():
        raise HTTPException(
            status_code=400,
            detail="נדרש אימות רשמי מול Google (לא התקבל אסימון אימות מ-Google)",
        )
    try:
        verified = verify_google_id_token(req.credential)
        profile = upsert_user_profile(
            email=verified["email"],
            name=verified["name"],
            picture=verified["picture"],
        )
        token = create_session_token(profile["id"])
        profile["session_token"] = token
        response.set_cookie(
            key="session_token",
            value=token,
            max_age=SESSION_TOKEN_TTL_SECONDS,
            httponly=True,
            secure=True,
            samesite="lax",
        )
        return profile
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"אימות Google נכשל: {e}")


@app.get("/api/auth/me")
def api_auth_me(request: Request, response: Response):
    uid = get_request_user_id(request)
    if uid == "default":
        raise HTTPException(status_code=401, detail="Not authenticated")
    admin_flag = is_admin_user_id(uid)
    user_doc = {}
    if not db:
        user_doc = _LOCAL_USERS.get(uid, {})
    else:
        try:
            snap = db.collection("users").document(uid).get()
            if snap.exists:
                user_doc = snap.to_dict() or {}
        except Exception as e:
            print(f"ERROR api_auth_me: {e}")
    email = (user_doc.get("email") or "").strip().lower()
    owner_flag = is_owner_email(email) and not bool(user_doc.get("admin_revoked", False))
    if user_doc.get("is_blocked") and not owner_flag:
        raise HTTPException(status_code=403, detail="החשבון שלך נחסם על ידי מנהל המערכת")
    is_approved = bool(admin_flag or owner_flag or user_doc.get("approved_by_owner", False))
    today_str = str(date.today())
    daily_ai_limit = int(user_doc.get("daily_ai_limit") if user_doc.get("daily_ai_limit") is not None else DEFAULT_DAILY_AI_LIMIT)
    ai_calls_today = int(user_doc.get("ai_calls_today") or 0) if user_doc.get("ai_quota_date") == today_str else 0
    refreshed_token = create_session_token(uid)
    response.set_cookie(
        key="session_token",
        value=refreshed_token,
        max_age=SESSION_TOKEN_TTL_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
    )
    return {
        "id": uid,
        "email": email,
        "name": user_doc.get("name") or "",
        "picture": user_doc.get("picture") or "",
        "is_admin": bool(admin_flag),
        "is_approved": is_approved,
        "is_blocked": False,
        "daily_ai_limit": daily_ai_limit,
        "ai_calls_today": ai_calls_today,
        "verified_oauth": True,
        "session_token": refreshed_token,
    }


@app.post("/api/auth/claim-legacy-data")
def api_claim_legacy_data(request: Request):
    uid = require_admin_user(request)
    return claim_legacy_data_for_user(uid)


# ---------------------------------------------------------------------------
# Admin User Management Helpers & Routes (Owner Only)
# ---------------------------------------------------------------------------

def list_all_users_admin_db() -> dict:
    today_str = str(date.today())
    users_map = {}

    if not db:
        for uid, u in _LOCAL_USERS.items():
            users_map[uid] = dict(u)
        all_meals = list(_LOCAL_MEALS)
        all_weights = list(_LOCAL_WEIGHTS)
        all_subs = list(_LOCAL_PUSH_SUBS)
    else:
        try:
            for doc in db.collection("users").stream():
                d = doc.to_dict() or {}
                d["id"] = doc.id
                users_map[doc.id] = d
        except Exception as e:
            print(f"ERROR reading users in admin: {e}")

        all_meals = []
        try:
            for doc in db.collection("meals").stream():
                d = doc.to_dict() or {}
                d["id"] = doc.id
                all_meals.append(d)
        except Exception as e:
            print(f"ERROR reading meals in admin: {e}")

        all_weights = []
        try:
            for doc in db.collection("weight_logs").stream():
                d = doc.to_dict() or {}
                d["id"] = doc.id
                all_weights.append(d)
        except Exception as e:
            print(f"ERROR reading weights in admin: {e}")

        all_subs = get_push_subscriptions_db(user_id=None)

    # Ensure any user_id seen in meals/weights also appears in users_map
    for m in all_meals:
        muid = _normalize_user_id(m.get("user_id"))
        if muid != "default" and muid not in users_map:
            users_map[muid] = {
                "id": muid,
                "email": muid,
                "name": muid,
                "picture": "",
                "is_admin": is_admin_user_id(muid),
                "is_blocked": False,
            }

    users_list = []
    active_today_count = 0
    pending_approval_count = 0

    for uid, u in users_map.items():
        email = (u.get("email") or "").strip().lower()
        revoked = bool(u.get("admin_revoked", False))
        owner_flag = is_owner_email(email) and not revoked
        is_admin = False if revoked else bool(owner_flag or u.get("admin_granted_by_owner", False))
        is_blocked = bool(u.get("is_blocked", False)) and not is_admin
        is_approved = bool(is_admin or u.get("approved_by_owner", False))
        if not is_approved and not is_blocked:
            pending_approval_count += 1
        daily_ai_limit = int(u.get("daily_ai_limit") if u.get("daily_ai_limit") is not None else DEFAULT_DAILY_AI_LIMIT)
        ai_calls_today = int(u.get("ai_calls_today") or 0) if u.get("ai_quota_date") == today_str else 0
        targets = get_targets(user_id=uid)

        u_meals = [m for m in all_meals if _doc_matches_user(m, uid)]
        today_meals = [m for m in u_meals if str(m.get("log_date") or "") == today_str]
        today_cal = sum(int(m.get("calories") or 0) for m in today_meals)
        today_prot = round(sum(float(m.get("protein_g") or 0.0) for m in today_meals), 1)

        if today_meals or str(u.get("last_login_date") or "") == today_str:
            active_today_count += 1

        meal_dates = [str(m.get("log_date")) for m in u_meals if m.get("log_date")]
        last_active = max(meal_dates) if meal_dates else str(u.get("last_login_date") or "—")

        u_weights = [w for w in all_weights if _doc_matches_user(w, uid) and w.get("weight_kg") is not None]
        u_weights.sort(key=lambda x: str(x.get("log_date") or ""))
        latest_weight = round(float(u_weights[-1]["weight_kg"]), 1) if u_weights else None

        u_subs = [s for s in all_subs if _doc_matches_user(s, uid)]

        users_list.append({
            "id": uid,
            "email": email or uid,
            "name": u.get("name") or (email.split("@")[0] if email else uid),
            "picture": u.get("picture") or "",
            "is_owner": owner_flag,
            "is_admin": is_admin,
            "is_approved": is_approved,
            "is_blocked": is_blocked,
            "daily_ai_limit": daily_ai_limit,
            "ai_calls_today": ai_calls_today,
            "target_calories": int(targets.get("target_calories") or 1800),
            "target_protein": int(targets.get("target_protein") or 120),
            "today_calories": today_cal,
            "today_protein": today_prot,
            "today_meals_count": len(today_meals),
            "total_meals_count": len(u_meals),
            "latest_weight": latest_weight,
            "push_devices_count": len(u_subs),
            "last_active": last_active,
        })

    # Sort pending approval users first so the admin sees them immediately, then owner/admin
    users_list.sort(key=lambda x: (x["is_approved"] and not x["is_owner"], not x["is_owner"], not x["is_admin"], -x["total_meals_count"], str(x["email"] or "")))

    return {
        "stats": {
            "total_users": len(users_list),
            "pending_approval_users": pending_approval_count,
            "active_today_users": active_today_count,
            "total_meals_logged": len(all_meals),
            "total_push_devices": len(all_subs),
        },
        "users": users_list,
    }


def admin_create_user_db(email: str, name: str = "", target_calories: int = 1800, target_protein: int = 120, daily_ai_limit: int = DEFAULT_DAILY_AI_LIMIT) -> dict:
    clean_email = (email or "").strip().lower()
    if not clean_email or "@" not in clean_email:
        raise ValueError("נא להזין כתובת אימייל תקינה")
    uid = _make_user_id_from_email(clean_email)
    display_name = (name or "").strip() or clean_email.split("@")[0]
    link_code = _make_link_code(uid, clean_email)
    owner_flag = is_owner_email(clean_email)

    profile = {
        "id": uid,
        "email": clean_email,
        "name": display_name,
        "picture": "",
        "link_code": link_code,
        "is_admin": owner_flag,
        "is_approved": True,
        "approved_by_owner": True,
        "is_blocked": False,
        "daily_ai_limit": int(daily_ai_limit),
        "last_login_date": "טרם התחבר",
    }
    if not db:
        _LOCAL_USERS[uid] = profile
    else:
        db.collection("users").document(uid).set({
            **profile,
            "updated_at": firestore.SERVER_TIMESTAMP,
        }, merge=True)

    set_targets(target_calories, target_protein, user_id=uid)
    return profile


def get_user_details_admin_db(target_uid: str) -> dict:
    uid = _normalize_user_id(target_uid)
    targets = get_targets(user_id=uid)
    summary = get_day_summary(user_id=uid)
    history_data = _fetch_user_history(uid=uid, days=30)
    meals = (history_data.get("meals") or [])[:30]
    weights = get_weights_db(limit=20, user_id=uid)
    return {
        "user_id": uid,
        "targets": targets,
        "today_summary": summary,
        "recent_meals": meals,
        "weights": weights,
    }


def update_user_status_admin_db(
    target_uid: str,
    is_blocked: Optional[bool] = None,
    is_admin: Optional[bool] = None,
    is_approved: Optional[bool] = None,
    daily_ai_limit: Optional[int] = None,
    caller_uid: str = "",
) -> dict:
    uid = _normalize_user_id(target_uid)
    if caller_uid and uid == _normalize_user_id(caller_uid):
        if is_blocked:
            raise ValueError("לא ניתן לחסום את החשבון של עצמך")
        if is_admin is False:
            raise ValueError("לא ניתן להסיר הרשאת אדמין מהחשבון שממנו אתה מחובר כרגע")
        if is_approved is False:
            raise ValueError("לא ניתן לבטל אישור לחשבון של עצמך")

    updates = {}
    if is_blocked is not None:
        updates["is_blocked"] = bool(is_blocked)
        if not is_blocked:
            updates["is_approved"] = True
            updates["approved_by_owner"] = True
    if is_approved is not None:
        updates["is_approved"] = bool(is_approved)
        updates["approved_by_owner"] = bool(is_approved)
        if is_approved:
            updates["is_blocked"] = False
    if is_admin is not None:
        updates["is_admin"] = bool(is_admin)
        updates["admin_granted_by_owner"] = bool(is_admin)
        updates["admin_revoked"] = not bool(is_admin)
        if is_admin:
            updates["is_approved"] = True
            updates["approved_by_owner"] = True
    if daily_ai_limit is not None:
        updates["daily_ai_limit"] = max(0, int(daily_ai_limit))

    if not db:
        if uid in _LOCAL_USERS:
            _LOCAL_USERS[uid].update(updates)
        return _LOCAL_USERS.get(uid, {"id": uid, **updates})

    db.collection("users").document(uid).set({
        **updates,
        "updated_at": firestore.SERVER_TIMESTAMP,
    }, merge=True)
    snap = db.collection("users").document(uid).get()
    return snap.to_dict() if snap.exists else {"id": uid, **updates}


def delete_user_admin_db(target_uid: str, caller_uid: str = "") -> dict:
    uid = _normalize_user_id(target_uid)
    if caller_uid and uid == _normalize_user_id(caller_uid):
        raise ValueError("לא ניתן למחוק את החשבון שממנו אתה מחובר כרגע")

    deleted_meals = 0
    deleted_weights = 0
    if not db:
        global _LOCAL_MEALS, _LOCAL_WEIGHTS, _LOCAL_FAVORITES, _LOCAL_PUSH_SUBS
        _LOCAL_USERS.pop(uid, None)
        _LOCAL_TARGETS_BY_USER.pop(uid, None)
        before_m = len(_LOCAL_MEALS)
        _LOCAL_MEALS = [m for m in _LOCAL_MEALS if not _doc_matches_user(m, uid)]
        deleted_meals = before_m - len(_LOCAL_MEALS)
        before_w = len(_LOCAL_WEIGHTS)
        _LOCAL_WEIGHTS = [w for w in _LOCAL_WEIGHTS if not _doc_matches_user(w, uid)]
        deleted_weights = before_w - len(_LOCAL_WEIGHTS)
        _LOCAL_FAVORITES = [f for f in _LOCAL_FAVORITES if not _doc_matches_user(f, uid)]
        _LOCAL_PUSH_SUBS = [s for s in _LOCAL_PUSH_SUBS if not _doc_matches_user(s, uid)]
        return {"deleted_user_id": uid, "deleted_meals": deleted_meals, "deleted_weights": deleted_weights}

    try:
        db.collection("users").document(uid).delete()
        db.collection("daily_targets").document(f"user_{uid}").delete()
        for doc in db.collection("meals").stream():
            if _doc_matches_user(doc.to_dict() or {}, uid):
                doc.reference.delete()
                deleted_meals += 1
        for doc in db.collection("weight_logs").stream():
            if _doc_matches_user(doc.to_dict() or {}, uid):
                doc.reference.delete()
                deleted_weights += 1
        for doc in db.collection("favorites").stream():
            if _doc_matches_user(doc.to_dict() or {}, uid):
                doc.reference.delete()
        for doc in db.collection("push_subscriptions").stream():
            if _doc_matches_user(doc.to_dict() or {}, uid):
                doc.reference.delete()
    except Exception as e:
        print(f"ERROR delete_user_admin_db: {e}")

    return {"deleted_user_id": uid, "deleted_meals": deleted_meals, "deleted_weights": deleted_weights}


@app.get("/api/admin/users")
def api_admin_list_users(request: Request):
    require_admin_user(request)
    return list_all_users_admin_db()


@app.post("/api/admin/users")
def api_admin_create_user(req: AdminCreateUserRequest, request: Request):
    require_admin_user(request)
    try:
        return admin_create_user_db(
            email=req.email,
            name=req.name or "",
            target_calories=req.target_calories,
            target_protein=req.target_protein,
            daily_ai_limit=req.daily_ai_limit,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/admin/users/{target_uid}/details")
def api_admin_user_details(target_uid: str, request: Request):
    require_admin_user(request)
    return get_user_details_admin_db(target_uid)


@app.put("/api/admin/users/{target_uid}/targets")
def api_admin_update_user_targets(target_uid: str, req: TargetUpdateRequest, request: Request):
    admin_uid = require_admin_user(request)
    uid = _normalize_user_id(target_uid)
    if req.daily_ai_limit is not None:
        update_user_status_admin_db(uid, daily_ai_limit=req.daily_ai_limit, caller_uid=admin_uid)
    return set_targets(req.target_calories, req.target_protein, user_id=uid)


@app.put("/api/admin/users/{target_uid}/status")
def api_admin_update_user_status(target_uid: str, req: AdminUpdateUserStatusRequest, request: Request):
    admin_uid = require_admin_user(request)
    try:
        return update_user_status_admin_db(
            target_uid,
            is_blocked=req.is_blocked,
            is_admin=req.is_admin,
            is_approved=req.is_approved,
            daily_ai_limit=req.daily_ai_limit,
            caller_uid=admin_uid,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/admin/users/{target_uid}")
def api_admin_delete_user(target_uid: str, request: Request):
    admin_uid = require_admin_user(request)
    try:
        return delete_user_admin_db(target_uid, caller_uid=admin_uid)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/admin/vision-logs")
def api_admin_get_vision_logs(request: Request, limit: int = Query(default=25, ge=1, le=60)):
    require_admin_user(request)
    return get_vision_analysis_logs_db(limit=limit)


@app.post("/api/admin/vision-analyze-test")
def api_admin_vision_analyze_test(req: VisionTestAnalyzeRequest, request: Request):
    admin_uid = require_admin_user(request)
    raw_b64 = (req.image_base64 or "").strip()
    if not raw_b64:
        raise HTTPException(status_code=400, detail="נא להעלות תמונה לבדיקת הסוכן")

    mime_type = req.mime_type or "image/jpeg"
    if raw_b64.startswith("data:"):
        header, _, raw_b64 = raw_b64.partition(",")
        if ";" in header and ":" in header:
            mime_type = header.split(":")[1].split(";")[0] or mime_type

    raw_b64 = "".join(raw_b64.split())
    pad = (4 - len(raw_b64) % 4) % 4
    if pad:
        raw_b64 += "=" * pad

    try:
        image_bytes = base64.b64decode(raw_b64)
        if not image_bytes:
            raise ValueError("Empty image")
    except Exception:
        raise HTTPException(status_code=400, detail="קובץ תמונה לא תקין")

    forensics = compute_image_forensics_and_histogram(image_bytes)
    extraction = analyze_meal_image(image_bytes, mime_type=mime_type, caption=req.caption or "", forensics=forensics)
    log_record = save_vision_analysis_log_db(
        user_id=admin_uid,
        extraction=extraction,
        forensics=forensics,
        source="admin-vision-lab",
        caption=req.caption or "",
        meal_id="",
    )
    return log_record


@app.post("/api/admin/vision-calibrate")
def api_admin_vision_calibrate(req: VisionCalibrateRequest, request: Request):
    require_admin_user(request)
    try:
        return calibrate_vision_log_db(req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/admin/vision-calibrate/{rule_id}")
def api_admin_delete_vision_rule(rule_id: str, request: Request):
    require_admin_user(request)
    deleted = delete_vision_calibration_rule_db(rule_id)
    return {"status": "deleted" if deleted else "not_found", "id": rule_id}


@app.get("/api/summary")
def api_summary(request: Request, target_date: Optional[str] = Query(default=None, alias="date")):
    uid = require_approved_user(request)
    return get_day_summary(target_date, user_id=uid)


def _fetch_user_history(uid: str, days: int = 14) -> dict:
    today_date = date.today()
    start_date_str = str(today_date - timedelta(days=days - 1))

    all_meals = []
    if db:
        try:
            docs = db.collection("meals").order_by("created_at", direction=firestore.Query.DESCENDING).limit(500).stream()
            for doc in docs:
                data = doc.to_dict()
                if not _doc_matches_user(data, uid):
                    continue
                all_meals.append({
                    "id": doc.id,
                    "description": data.get("description", ""),
                    "calories": int(data.get("calories", 0)),
                    "protein_g": round(float(data.get("protein_g", 0.0)), 1),
                    "items": data.get("items") or [],
                    "log_date": data.get("log_date", ""),
                    "log_time": data.get("log_time", ""),
                    "source": data.get("source", "telegram"),
                })
                if len(all_meals) >= 300:
                    break
        except Exception as e:
            print(f"ERROR _fetch_user_history: {e}")
    else:
        all_meals = [m for m in _LOCAL_MEALS if _doc_matches_user(m, uid)]

    all_workouts = []
    if db:
        try:
            w_docs = db.collection("workouts").order_by("created_at", direction=firestore.Query.DESCENDING).limit(300).stream()
            for w in w_docs:
                w_data = w.to_dict()
                if not _doc_matches_user(w_data, uid):
                    continue
                all_workouts.append({
                    "id": w.id,
                    "activity_name": w_data.get("activity_name", ""),
                    "activity_type": w_data.get("activity_type", "other"),
                    "burned_calories": int(w_data.get("burned_calories", 0)),
                    "duration_min": int(w_data.get("duration_min", 0)),
                    "distance_km": w_data.get("distance_km"),
                    "log_date": w_data.get("log_date", ""),
                    "log_time": w_data.get("log_time", ""),
                    "source": w_data.get("source", "manual"),
                    "notes": w_data.get("notes", ""),
                })
        except Exception as e:
            print(f"ERROR _fetch_user_history workouts: {e}")
    else:
        all_workouts = [w for w in _LOCAL_WORKOUTS if _doc_matches_user(w, uid)]

    targets = get_targets(user_id=uid)
    daily_trend = []
    for i in range(6, -1, -1):
        d = str(today_date - timedelta(days=i))
        day_meals = [m for m in all_meals if m.get("log_date") == d]
        day_workouts = [w for w in all_workouts if w.get("log_date") == d]
        cal = sum(m["calories"] for m in day_meals)
        burned = sum(w["burned_calories"] for w in day_workouts)
        prot = round(sum(m["protein_g"] for m in day_meals), 1)
        daily_trend.append({
            "date": d,
            "calories": cal,
            "burned_calories": burned,
            "net_calories": cal - burned,
            "protein_g": prot,
            "meal_count": len(day_meals),
            "workout_count": len(day_workouts),
        })

    return {
        "meals": all_meals,
        "workouts": all_workouts,
        "daily_trend": daily_trend,
        "target_calories": targets["target_calories"],
        "target_protein": targets["target_protein"],
        "start_date": start_date_str,
    }


@app.get("/api/history")
def api_history(request: Request, days: int = Query(default=14, ge=1, le=90)):
    uid = require_approved_user(request)
    return _fetch_user_history(uid=uid, days=days)


@app.get("/api/meals/")
def api_meals_legacy(request: Request):
    """Backwards-compatible endpoint returning flat list of meals."""
    uid = require_approved_user(request)
    return _fetch_user_history(uid=uid, days=30)["meals"]


@app.post("/api/meals/")
def api_add_manual_meal(req: ManualMealRequest, request: Request):
    uid = require_approved_user(request)
    entry = log_meal_db(
        description=req.description,
        calories=req.calories,
        protein=req.protein_g,
        log_date=req.log_date,
        log_time=req.log_time,
        source="web",
        user_id=uid,
    )
    if not entry:
        raise HTTPException(status_code=500, detail="Failed to log meal to Firestore")
    return entry


@app.post("/api/meals/ai")
def api_add_ai_meal(req: AIMealRequest, request: Request):
    uid = require_approved_user(request)
    if not req.text or not req.text.strip():
        raise HTTPException(status_code=400, detail="נא להזין תיאור ארוחה")

    check_and_increment_ai_quota(uid)
    extraction = analyze_meal(req.text.strip())
    if extraction.summary and "402_PREPAYMENT_DEPLETED" in extraction.summary:
        raise HTTPException(
            status_code=402,
            detail="יתרת התשלום המוקדם (Prepayment Credits) בחשבון Google AI Studio אזלה. יש להיכנס ל-https://ai.studio/projects ולהטעין תקציב.",
        )
    if not extraction.is_meal:
        raise HTTPException(status_code=422, detail="לא זוהתה ארוחה או שתייה בטקסט שהוזן")

    items_data = [item.model_dump() for item in extraction.items] if extraction.items else []
    entry = log_meal_db(
        description=extraction.summary,
        calories=extraction.calories,
        protein=extraction.protein_g,
        items=items_data,
        log_date=req.log_date,
        source="web-ai",
        user_id=uid,
    )
    if not entry:
        raise HTTPException(status_code=500, detail="Failed to save meal to Firestore")
    return entry


@app.post("/api/meals/photo")
def api_add_photo_meal(req: PhotoMealRequest, request: Request):
    uid = require_approved_user(request)
    raw_b64 = (req.image_base64 or "").strip()
    if not raw_b64:
        raise HTTPException(status_code=400, detail="נא להעלות או לצלם תמונה")

    mime_type = req.mime_type or "image/jpeg"
    if raw_b64.startswith("data:"):
        header, _, raw_b64 = raw_b64.partition(",")
        if ";" in header and ":" in header:
            mime_type = header.split(":")[1].split(";")[0] or mime_type

    raw_b64 = "".join(raw_b64.split())
    pad = (4 - len(raw_b64) % 4) % 4
    if pad:
        raw_b64 += "=" * pad

    try:
        image_bytes = base64.b64decode(raw_b64)
        if not image_bytes:
            raise ValueError("Empty decoded image")
    except Exception:
        raise HTTPException(status_code=400, detail="קובץ תמונה לא תקין")

    check_and_increment_ai_quota(uid)
    forensics = compute_image_forensics_and_histogram(image_bytes)
    extraction = analyze_meal_image(image_bytes, mime_type=mime_type, caption=req.caption or "", forensics=forensics)
    if extraction.summary and extraction.summary.startswith("API_ERROR:"):
        if "402" in extraction.summary or "prepayment" in extraction.summary.lower():
            raise HTTPException(
                status_code=402,
                detail="יתרת התשלום המוקדם (Prepayment Credits) ב-Google AI Studio אזלה. יש להיכנס ל-https://ai.studio/projects ולהטעין תקציב.",
            )
        raise HTTPException(status_code=502, detail=f"שגיאה בתקשורת עם Gemini Vision ({extraction.summary}). נסה שוב.")
    if not extraction.is_meal:
        save_vision_analysis_log_db(uid, extraction, forensics, source="web-photo", caption=req.caption or "", meal_id="")
        raise HTTPException(status_code=422, detail="לא זוהו אוכל, משקה או תווית מזון בתמונה")

    items_data = [item.model_dump() for item in extraction.items] if extraction.items else []
    entry = log_meal_db(
        description=extraction.summary,
        calories=extraction.calories,
        protein=extraction.protein_g,
        items=items_data,
        log_date=req.log_date,
        source="web-photo",
        user_id=uid,
    )
    if not entry:
        raise HTTPException(status_code=500, detail="Failed to save photo meal to Firestore")
    vlog = save_vision_analysis_log_db(uid, extraction, forensics, source="web-photo", caption=req.caption or "", meal_id=entry.get("id", ""))
    entry["vision_log_id"] = vlog.get("id", "")
    entry["vision_confidence"] = vlog["ai_analysis"].get("confidence_score", 88)
    return entry


@app.post("/api/meals/voice")
def api_add_voice_meal(req: VoiceMealRequest, request: Request):
    uid = require_approved_user(request)
    raw_b64 = (req.audio_base64 or "").strip()
    if not raw_b64:
        raise HTTPException(status_code=400, detail="נא להקליט דיווח קולי תחילה")

    mime_type = req.mime_type or "audio/webm"
    if raw_b64.startswith("data:"):
        header, _, raw_b64 = raw_b64.partition(",")
        if ";" in header and ":" in header:
            mime_type = header.split(":")[1].split(";")[0] or mime_type

    raw_b64 = "".join(raw_b64.split())
    pad = (4 - len(raw_b64) % 4) % 4
    if pad:
        raw_b64 += "=" * pad

    try:
        audio_bytes = base64.b64decode(raw_b64)
        if not audio_bytes:
            raise ValueError("Empty decoded audio")
    except Exception:
        raise HTTPException(status_code=400, detail="קובץ הקלטה לא תקין")

    check_and_increment_ai_quota(uid)
    extraction = analyze_meal_audio(audio_bytes, mime_type=mime_type, caption=req.caption or "")
    if extraction.summary and extraction.summary.startswith("API_ERROR:"):
        if "402" in extraction.summary or "prepayment" in extraction.summary.lower():
            raise HTTPException(
                status_code=402,
                detail="יתרת התשלום המוקדם (Prepayment Credits) ב-Google AI Studio אזלה. יש להיכנס ל-https://ai.studio/projects ולהטעין תקציב.",
            )
        raise HTTPException(status_code=502, detail=f"שגיאה בפענוח ההקלטה מול Gemini ({extraction.summary}). נסה שוב.")
    if not extraction.is_meal:
        raise HTTPException(status_code=422, detail="לא זוהה דיווח על אוכל או שתייה בהקלטה")

    items_data = [item.model_dump() for item in extraction.items] if extraction.items else []
    entry = log_meal_db(
        description=extraction.summary,
        calories=extraction.calories,
        protein=extraction.protein_g,
        items=items_data,
        log_date=req.log_date,
        source="web-voice",
        user_id=uid,
    )
    if not entry:
        raise HTTPException(status_code=500, detail="Failed to save voice meal to Firestore")
    return entry


@app.post("/api/coach/analyze")
def api_coach_analyze(request: Request, req: Optional[CoachAnalyzeRequest] = None):
    uid = require_approved_user(request)
    check_and_increment_ai_quota(uid)
    d_str = (req.log_date if req and req.log_date else None) or str(date.today())
    day_summary = get_day_summary(d_str, user_id=uid)
    hist = _fetch_user_history(uid=uid, days=7)
    return generate_daily_coach_insight(day_summary=day_summary, daily_trend=hist.get("daily_trend", []))


@app.put("/api/meals/{meal_id}")
def api_update_meal(meal_id: str, req: MealUpdateRequest, request: Request):
    uid = require_approved_user(request)
    desc = req.description.strip()
    if not desc:
        raise HTTPException(status_code=400, detail="נא להזין תיאור ארוחה")

    cal = int(req.calories)
    prot = round(float(req.protein_g), 1)

    if not db:
        for m in _LOCAL_MEALS:
            if m.get("id") == meal_id:
                if not _doc_matches_user(m, uid) and not is_admin_user_id(uid):
                    raise HTTPException(status_code=403, detail="אין הרשאה לערוך ארוחה זו")
                m["description"] = desc
                m["calories"] = cal
                m["protein_g"] = prot
                if req.log_date:
                    m["log_date"] = req.log_date
                if req.log_time:
                    m["log_time"] = req.log_time
                if req.items is not None:
                    m["items"] = [it.model_dump() for it in req.items]
                elif len(m.get("items") or []) <= 1:
                    m["items"] = [{"name": desc, "calories": cal, "protein_g": prot}]
                return m
        raise HTTPException(status_code=404, detail="Meal not found")

    try:
        doc_ref = db.collection("meals").document(meal_id)
        doc = doc_ref.get()
        if not doc.exists:
            raise HTTPException(status_code=404, detail="Meal not found")

        doc_data = doc.to_dict() or {}
        if not _doc_matches_user(doc_data, uid) and not is_admin_user_id(uid):
            raise HTTPException(status_code=403, detail="אין הרשאה לערוך ארוחה זו")

        existing_items = doc_data.get("items") or []

        if req.items is not None:
            clean_items = [it.model_dump() for it in req.items]
        elif len(existing_items) <= 1:
            clean_items = [{"name": desc, "calories": cal, "protein_g": prot}]
        else:
            old_cal = sum(int(it.get("calories", 0)) for it in existing_items) or 1
            old_prot = sum(float(it.get("protein_g", 0)) for it in existing_items) or 1.0
            cal_ratio = cal / old_cal
            prot_ratio = (prot / old_prot) if old_prot > 0 else 1.0
            clean_items = []
            for it in existing_items:
                clean_items.append({
                    "name": it.get("name", ""),
                    "calories": int(round(it.get("calories", 0) * cal_ratio)),
                    "protein_g": round(float(it.get("protein_g", 0)) * prot_ratio, 1),
                })

        update_payload = {
            "description": desc,
            "calories": cal,
            "protein_g": prot,
            "items": clean_items,
        }
        if req.log_date:
            update_payload["log_date"] = req.log_date
        if req.log_time:
            update_payload["log_time"] = req.log_time
        doc_ref.update(update_payload)
        updated = dict(doc_data)
        updated.update(update_payload)
        updated["id"] = meal_id
        updated.pop("created_at", None)
        return updated
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update meal: {e}")


@app.delete("/api/meals/{meal_id}")
def api_delete_meal(meal_id: str, request: Request):
    uid = require_approved_user(request)
    if not db:
        global _LOCAL_MEALS
        for m in _LOCAL_MEALS:
            if m.get("id") == meal_id:
                if not _doc_matches_user(m, uid) and not is_admin_user_id(uid):
                    raise HTTPException(status_code=403, detail="אין הרשאה למחוק ארוחה זו")
        orig_len = len(_LOCAL_MEALS)
        _LOCAL_MEALS = [m for m in _LOCAL_MEALS if m.get("id") != meal_id]
        if len(_LOCAL_MEALS) == orig_len:
            raise HTTPException(status_code=404, detail="Meal not found")
        return {"status": "deleted", "id": meal_id}
    try:
        doc_ref = db.collection("meals").document(meal_id)
        doc = doc_ref.get()
        if not doc.exists:
            raise HTTPException(status_code=404, detail="Meal not found")
        doc_data = doc.to_dict() or {}
        if not _doc_matches_user(doc_data, uid) and not is_admin_user_id(uid):
            raise HTTPException(status_code=403, detail="אין הרשאה למחוק ארוחה זו")
        doc_ref.delete()
        return {"status": "deleted", "id": meal_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete meal: {e}")


@app.get("/api/favorites")
def api_get_favorites(request: Request):
    uid = require_approved_user(request)
    return {"favorites": get_favorites_db(user_id=uid)}


@app.post("/api/workouts/ai")
def api_add_ai_workout(req: AIWorkoutRequest, request: Request):
    uid = require_approved_user(request)
    if not req.text or not req.text.strip():
        raise HTTPException(status_code=400, detail="נא להזין תיאור אימון")

    check_and_increment_ai_quota(uid)
    weight_kg = _get_user_latest_weight(uid)
    extraction = analyze_workout(req.text.strip(), user_weight_kg=weight_kg)
    if not extraction.is_workout or extraction.total_burned_calories <= 0:
        raise HTTPException(status_code=422, detail="לא זוהתה פעילות גופנית או אימון בטקסט שהוזן")

    added_entries = []
    d_str = req.log_date or str(date.today())
    for item in extraction.workouts:
        entry = log_workout_db(
            activity_name=item.activity_name,
            burned_calories=item.burned_calories,
            duration_min=item.duration_min,
            distance_km=item.distance_km,
            activity_type=item.activity_type,
            log_date=d_str,
            source="web-ai",
            user_id=uid,
            notes=item.notes or "",
        )
        if entry:
            added_entries.append(entry)

    if not added_entries:
        raise HTTPException(status_code=500, detail="שגיאה בשמירת האימון")

    return {
        "status": "success",
        "summary": extraction.summary,
        "total_burned_calories": extraction.total_burned_calories,
        "workouts": added_entries,
    }


@app.post("/api/workouts/")
def api_add_manual_workout(req: ManualWorkoutRequest, request: Request):
    uid = require_approved_user(request)
    entry = log_workout_db(
        activity_name=req.activity_name.strip(),
        burned_calories=req.burned_calories,
        duration_min=req.duration_min,
        distance_km=req.distance_km,
        activity_type=req.activity_type,
        log_date=req.log_date,
        log_time=req.log_time,
        source="manual",
        user_id=uid,
        notes=req.notes or "",
    )
    if not entry:
        raise HTTPException(status_code=500, detail="שגיאה בשמירת האימון")
    return entry


@app.delete("/api/workouts/{workout_id}")
def api_delete_workout(workout_id: str, request: Request):
    uid = require_approved_user(request)
    deleted = delete_workout_db(workout_id, user_id=uid)
    if not deleted:
        raise HTTPException(status_code=404, detail="אימון לא נמצא")
    return {"status": "deleted", "id": workout_id}


@app.post("/api/workouts/photo")
def api_add_photo_workout(req: PhotoWorkoutRequest, request: Request):
    uid = require_approved_user(request)
    raw_b64 = (req.image_base64 or "").strip()
    if not raw_b64:
        raise HTTPException(status_code=400, detail="נא להעלות צילום מסך מאפליקציית הכושר / הבריאות")

    mime_type = req.mime_type or "image/jpeg"
    if raw_b64.startswith("data:"):
        header, _, raw_b64 = raw_b64.partition(",")
        if ";" in header and ":" in header:
            mime_type = header.split(":")[1].split(";")[0] or mime_type

    raw_b64 = "".join(raw_b64.split())
    pad = (4 - len(raw_b64) % 4) % 4
    if pad:
        raw_b64 += "=" * pad

    try:
        image_bytes = base64.b64decode(raw_b64)
        if not image_bytes:
            raise ValueError("Empty decoded image")
    except Exception:
        raise HTTPException(status_code=400, detail="קובץ תמונה לא תקין")

    check_and_increment_ai_quota(uid)
    w_info = get_weights_db(limit=5, user_id=uid)
    user_weight_kg = float(w_info.get("latest_weight") or 75.0)

    extraction = analyze_workout_image(
        image_bytes=image_bytes,
        mime_type=mime_type,
        caption=req.caption or "",
        user_weight_kg=user_weight_kg,
    )
    if extraction.summary and extraction.summary.startswith("API_ERROR:"):
        raise HTTPException(status_code=502, detail=f"שגיאה בפענוח צילום המסך ב-AI: {extraction.summary}")
    if not extraction.is_workout or not extraction.workouts:
        raise HTTPException(status_code=422, detail="לא זוהו נתוני צעדים, קלוריות או אימון בצילום המסך שהועלה")

    d_str = req.log_date or str(date.today())
    added_entries = []
    for item in extraction.workouts:
        entry = log_workout_db(
            activity_name=item.activity_name,
            burned_calories=max(1, int(item.burned_calories)),
            duration_min=max(1, int(item.duration_min or 30)),
            distance_km=item.distance_km,
            activity_type=item.activity_type or "other",
            log_date=d_str,
            source="health-screenshot",
            user_id=uid,
            notes=item.notes or "סונכרן מצילום מסך (Samsung / Google Health)",
        )
        if entry:
            added_entries.append(entry)

    if not added_entries:
        raise HTTPException(status_code=500, detail="שגיאה בשמירת הפעילות")

    return {
        "status": "success",
        "summary": extraction.summary,
        "total_burned_calories": sum(w["burned_calories"] for w in added_entries),
        "workouts": added_entries,
    }


@app.post("/api/google-fit/sync")
def api_sync_google_fit(req: GoogleFitSyncRequest, request: Request):
    import urllib.request
    import urllib.error
    import concurrent.futures
    from zoneinfo import ZoneInfo

    uid = require_approved_user(request)
    token = (req.access_token or "").strip()

    # Save or load cached token for seamless 1-click sync without repeated login popups
    if token:
        if db:
            try:
                db.collection("users").document(uid).set(
                    {"gfit_access_token": token, "gfit_token_ts": int(datetime.now().timestamp())},
                    merge=True,
                )
            except Exception:
                pass
        else:
            _LOCAL_USERS.setdefault(uid, {})["gfit_access_token"] = token
    else:
        if db:
            try:
                u_snap = db.collection("users").document(uid).get()
                if u_snap.exists:
                    u_data = u_snap.to_dict() or {}
                    saved_ts = int(u_data.get("gfit_token_ts") or 0)
                    if int(datetime.now().timestamp()) - saved_ts < 3300:
                        token = (u_data.get("gfit_access_token") or "").strip()
            except Exception:
                pass
        else:
            token = (_LOCAL_USERS.get(uid, {}).get("gfit_access_token") or "").strip()

    if not token:
        return {"status": "need_auth", "message": "נדרש חידוש הרשאה שקט מול Google Fit"}

    il_tz = ZoneInfo("Asia/Jerusalem")
    now_il = datetime.now(il_tz)
    target_date_str = req.log_date or str(now_il.date())
    try:
        parsed_dt = datetime.strptime(target_date_str, "%Y-%m-%d")
    except Exception:
        parsed_dt = now_il

    start_dt = datetime(parsed_dt.year, parsed_dt.month, parsed_dt.day, 0, 0, 0, tzinfo=il_tz)
    end_dt = datetime(parsed_dt.year, parsed_dt.month, parsed_dt.day, 23, 59, 59, tzinfo=il_tz)
    if end_dt > now_il + timedelta(minutes=2):
        end_dt = now_il + timedelta(minutes=2)
    if end_dt <= start_dt:
        end_dt = start_dt + timedelta(hours=23, minutes=59)

    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)
    duration_ms = max(1000, end_ms - start_ms)
    start_ns = start_ms * 1_000_000
    end_ns = end_ms * 1_000_000

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    # Quick auth check via dataSources (also gives us all user streams)
    ds_list = []
    try:
        ds_url = "https://fitness.googleapis.com/fitness/v1/users/me/dataSources"
        ds_req = urllib.request.Request(ds_url, headers=headers, method="GET")
        with urllib.request.urlopen(ds_req, timeout=6) as ds_resp:
            ds_list = json.loads(ds_resp.read().decode("utf-8")).get("dataSource", [])
    except urllib.error.HTTPError as he:
        if he.code in (401, 403):
            return {"status": "need_auth", "message": "פג תוקף החיבור ל-Google Fit, מחדש חיבור..."}
        print(f"WARN Google Fit dataSources HTTPError: {he.code} {he}")
    except Exception as dse:
        print(f"WARN Google Fit dataSources init: {dse}")

    ds_stream_count = len(ds_list)
    stream_Debug_names = [f"{(d.get('dataType') or {}).get('name','?')}::{d.get('dataStreamId','?')}" for d in ds_list]
    print(f"INFO GoogleFitStreams [{uid}] ({ds_stream_count}): {stream_Debug_names[:35]}")

    def _agg_single(agg_spec: dict) -> list:
        agg_url = "https://fitness.googleapis.com/fitness/v1/users/me/dataset:aggregate"
        agg_body = {
            "aggregateBy": [agg_spec],
            "bucketByTime": {"durationMillis": duration_ms},
            "startTimeMillis": start_ms,
            "endTimeMillis": end_ms,
        }
        r_obj = urllib.request.Request(agg_url, data=json.dumps(agg_body).encode("utf-8"), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(r_obj, timeout=8) as r:
                d = json.loads(r.read().decode("utf-8"))
                pts = []
                for b in d.get("bucket", []):
                    for ds in b.get("dataset", []):
                        pts.extend(ds.get("point", []))
                return pts
        except Exception as ex:
            print(f"WARN Google Fit agg ({agg_spec}): {ex}")
            return []

    specs = {
        "steps_est": {"dataTypeName": "com.google.step_count.delta", "dataSourceId": "derived:com.google.step_count.delta:com.google.android.gms:estimated_steps"},
        "steps_all": {"dataTypeName": "com.google.step_count.delta"},
        "calories": {"dataTypeName": "com.google.calories.expended"},
        "active_min": {"dataTypeName": "com.google.active_minutes"},
        "heart_min": {"dataTypeName": "com.google.heart_minutes"},
        "segments": {"dataTypeName": "com.google.activity.segment"},
    }

    agg_results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        fut_map = {ex.submit(_agg_single, sp): k for k, sp in specs.items()}
        for fut in concurrent.futures.as_completed(fut_map):
            k = fut_map[fut]
            agg_results[k] = fut.result()

    from_samsung = False

    def _sum_points_int(pts: list) -> int:
        nonlocal from_samsung
        total = 0
        for pt in pts:
            orig = (pt.get("originDataSourceId") or "").lower()
            if "shealth" in orig or "samsung" in orig:
                from_samsung = True
            for v in pt.get("value", []):
                iv = v.get("intVal")
                fv = v.get("fpVal")
                if iv is not None:
                    total += int(iv)
                elif fv is not None:
                    total += int(round(float(fv)))
        return total

    def _sum_points_float(pts: list) -> float:
        nonlocal from_samsung
        total = 0.0
        for pt in pts:
            orig = (pt.get("originDataSourceId") or "").lower()
            if "shealth" in orig or "samsung" in orig:
                from_samsung = True
            for v in pt.get("value", []):
                fv = v.get("fpVal")
                iv = v.get("intVal")
                if fv is not None:
                    total += float(fv)
                elif iv is not None:
                    total += float(iv)
        return total

    steps_est = _sum_points_int(agg_results.get("steps_est", []))
    steps_all = _sum_points_int(agg_results.get("steps_all", []))
    total_steps = max(steps_est, steps_all)
    raw_calories = _sum_points_float(agg_results.get("calories", []))
    active_mins_val = _sum_points_int(agg_results.get("active_min", []))
    heart_mins_val = _sum_points_int(agg_results.get("heart_min", []))

    active_segment_minutes = 0
    for pt in agg_results.get("segments", []):
        orig = (pt.get("originDataSourceId") or "").lower()
        if "shealth" in orig or "samsung" in orig:
            from_samsung = True
        val_list = pt.get("value", [])
        if val_list:
            act_code = int(val_list[0].get("intVal", 0) or 0)
            if act_code not in (3, 4, 72):
                s_ns = int(pt.get("startTimeNanos") or 0)
                e_ns = int(pt.get("endTimeNanos") or 0)
                if e_ns > s_ns:
                    active_segment_minutes += max(1, int((e_ns - s_ns) / 60_000_000_000))

    # Deep scan across all user dataSources over a 7-day window (with safe=":" so stream IDs work properly!)
    lookback_start_dt = start_dt - timedelta(days=6)
    lb_start_ns = int(lookback_start_dt.timestamp() * 1000) * 1_000_000
    by_date_steps = {}
    by_date_cal = {}
    by_date_active = {}

    if ds_list and (total_steps == 0 or raw_calories <= 0):
        target_dtypes = {
            "com.google.step_count.delta",
            "com.google.step_count.cumulative",
            "com.google.calories.expended",
            "com.google.active_minutes",
            "com.google.heart_minutes",
            "com.google.activity.segment",
        }
        candidate_streams = []
        for dsrc in ds_list:
            d_stream_id = dsrc.get("dataStreamId", "")
            dt_name = (dsrc.get("dataType") or {}).get("name", "")
            if "shealth" in d_stream_id.lower() or "samsung" in d_stream_id.lower():
                from_samsung = True
            if dt_name in target_dtypes and d_stream_id:
                candidate_streams.append((dt_name, d_stream_id))

        def _fetch_stream_7d(item_tuple):
            dt_n, s_id = item_tuple
            enc_stream = urllib.parse.quote(s_id, safe=":")
            dset_url = f"https://fitness.googleapis.com/fitness/v1/users/me/dataSources/{enc_stream}/datasets/{lb_start_ns}-{end_ns}"
            pts = []
            try:
                dset_req = urllib.request.Request(dset_url, headers=headers, method="GET")
                with urllib.request.urlopen(dset_req, timeout=5) as dset_resp:
                    pts = json.loads(dset_resp.read().decode("utf-8")).get("point", [])
            except Exception as ex1:
                print(f"WARN stream dataset ({s_id}): {ex1}")
            if not pts:
                # Also check dataPointChanges to see if stream has any recent points regardless of query window
                chg_url = f"https://fitness.googleapis.com/fitness/v1/users/me/dataSources/{enc_stream}/dataPointChanges"
                try:
                    chg_req = urllib.request.Request(chg_url, headers=headers, method="GET")
                    with urllib.request.urlopen(chg_req, timeout=4) as chg_resp:
                        chg_data = json.loads(chg_resp.read().decode("utf-8"))
                        ins_pts = chg_data.get("insertedDataPoint", [])
                        if ins_pts:
                            # Take last 50 inserted points and filter to last 14 days
                            recent_cutoff_ns = (start_ms - 14 * 86400000) * 1_000_000
                            sample_ts = ins_pts[-1].get("endTimeNanos") or ins_pts[-1].get("startTimeNanos")
                            print(f"INFO stream {s_id} has {len(ins_pts)} total changes (latest_ns={sample_ts})")
                            pts = [p for p in ins_pts if int(p.get("endTimeNanos") or p.get("startTimeNanos") or 0) >= recent_cutoff_ns]
                except Exception as ex2:
                    print(f"WARN stream changes ({s_id}): {ex2}")
            return dt_n, s_id, pts

        if candidate_streams:
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ds_ex:
                for dt_name, s_id, pts in ds_ex.map(_fetch_stream_7d, candidate_streams):
                    if not pts:
                        continue
                    print(f"INFO GoogleFit stream {s_id} ({dt_name}) returned {len(pts)} pts in recent window")
                    # Group points by date in Asia/Jerusalem
                    stream_daily_int = {}
                    stream_daily_float = {}
                    stream_cum_vals = {}
                    for pt in pts:
                        ts_ns = int(pt.get("endTimeNanos") or pt.get("startTimeNanos") or 0)
                        if ts_ns <= 0:
                            continue
                        pt_d_str = str(datetime.fromtimestamp(ts_ns / 1_000_000_000.0, tz=il_tz).date())
                        if dt_name == "com.google.step_count.cumulative":
                            for v in pt.get("value", []):
                                iv = v.get("intVal") or v.get("fpVal")
                                if iv is not None:
                                    stream_cum_vals.setdefault(pt_d_str, []).append(int(iv))
                        elif dt_name in ("com.google.step_count.delta", "com.google.active_minutes", "com.google.heart_minutes"):
                            stream_daily_int[pt_d_str] = stream_daily_int.get(pt_d_str, 0) + _sum_points_int([pt])
                        elif dt_name == "com.google.calories.expended":
                            stream_daily_float[pt_d_str] = stream_daily_float.get(pt_d_str, 0.0) + _sum_points_float([pt])

                    if dt_name == "com.google.step_count.cumulative":
                        for d_k, vals in stream_cum_vals.items():
                            if len(vals) >= 2:
                                diff_steps = max(vals) - min(vals)
                                if diff_steps > 0:
                                    by_date_steps[d_k] = max(by_date_steps.get(d_k, 0), diff_steps)
                    elif dt_name == "com.google.step_count.delta":
                        for d_k, v_int in stream_daily_int.items():
                            by_date_steps[d_k] = max(by_date_steps.get(d_k, 0), v_int)
                    elif dt_name in ("com.google.active_minutes", "com.google.heart_minutes"):
                        for d_k, v_int in stream_daily_int.items():
                            by_date_active[d_k] = max(by_date_active.get(d_k, 0), v_int)
                    elif dt_name == "com.google.calories.expended":
                        for d_k, v_flt in stream_daily_float.items():
                            by_date_cal[d_k] = max(by_date_cal.get(d_k, 0.0), v_flt)

        if target_date_str in by_date_steps:
            total_steps = max(total_steps, by_date_steps[target_date_str])
        if target_date_str in by_date_cal:
            raw_calories = max(raw_calories, by_date_cal[target_date_str])
        if target_date_str in by_date_active:
            active_mins_val = max(active_mins_val, by_date_active[target_date_str])

    # Query explicit workout sessions
    session_names = []
    session_minutes = 0
    try:
        utc_start = start_dt.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%S.00Z")
        utc_end = end_dt.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%S.99Z")
        sess_url = f"https://fitness.googleapis.com/fitness/v1/users/me/sessions?startTime={utc_start}&endTime={utc_end}"
        sess_req = urllib.request.Request(sess_url, headers=headers, method="GET")
        with urllib.request.urlopen(sess_req, timeout=6) as s_resp:
            sess_data = json.loads(s_resp.read().decode("utf-8"))
            for s in sess_data.get("session", []):
                act_type = int(s.get("activityType") or 0)
                if act_type in (3, 72):
                    continue
                pkg = ((s.get("application") or {}).get("packageName") or "").lower()
                if "shealth" in pkg or "samsung" in pkg:
                    from_samsung = True
                s_name = (s.get("name") or s.get("description") or "").strip()
                if s_name and s_name not in session_names:
                    session_names.append(s_name)
                s_ms = int(s.get("startTimeMillis") or 0)
                e_ms = int(s.get("endTimeMillis") or 0)
                if e_ms > s_ms:
                    session_minutes += max(1, int((e_ms - s_ms) / 60000))
    except Exception as se:
        print(f"WARN Google Fit sessions fetch: {se}")

    active_min = max(active_segment_minutes, session_minutes, active_mins_val, heart_mins_val)
    step_calories = int(round(total_steps * 0.045))
    session_est_calories = int(round(session_minutes * 6.5))
    raw_cal_int = int(round(raw_calories))

    print(
        f"INFO GoogleFitSync [{uid}] date={target_date_str}: "
        f"steps={total_steps} (est={steps_est}, all={steps_all}), "
        f"raw_cal={raw_cal_int}, active_min={active_min}, sessions={session_names}, streams={ds_stream_count}, "
        f"by_date_steps={by_date_steps}, by_date_cal={by_date_cal}"
    )

    max_reasonable_active = step_calories + session_est_calories + max(active_min * 8, 300)
    if raw_cal_int > max_reasonable_active and (total_steps > 0 or active_min > 0):
        total_calories = max(step_calories, step_calories + int(session_est_calories * 0.6), active_min * 5)
    elif raw_cal_int > 900 and total_steps == 0 and active_min == 0:
        # Google Fit returned full-day BMR + active calories without separate step points; extract active ~20%
        total_calories = max(120, int(round(raw_cal_int * 0.20)))
    else:
        total_calories = raw_cal_int

    if total_calories <= 0 and total_steps > 0:
        total_calories = max(1, step_calories)
    elif total_calories <= 0 and active_min > 0:
        total_calories = max(1, active_min * 6)

    # If the target date has 0 data, sync any other recent day found in by_date_steps / by_date_cal
    if total_calories <= 0 and total_steps <= 0 and active_min <= 0:
        recent_synced = None
        all_found_dates = sorted(set(list(by_date_steps.keys()) + list(by_date_cal.keys())))
        for b_date_str in all_found_dates:
            b_steps = by_date_steps.get(b_date_str, 0)
            b_cal = by_date_cal.get(b_date_str, 0.0)
            if b_steps > 0 or b_cal > 0:
                b_step_cal = int(round(b_steps * 0.045))
                b_cal_int = int(round(b_cal))
                if b_cal_int > b_step_cal + 300 and b_steps > 0:
                    b_final_cal = max(b_step_cal, 50)
                elif b_cal_int > 900 and b_steps == 0:
                    b_final_cal = max(120, int(round(b_cal_int * 0.20)))
                else:
                    b_final_cal = max(b_cal_int, b_step_cal, 1)
                b_dur = max(15, int(b_steps / 100)) if b_steps > 0 else 30
                b_dist = round(b_steps * 0.00075, 2) if b_steps >= 500 else None
                b_name = f"Google Health / Fit ({b_steps:,} צעדים)" if b_steps > 0 else f"פעילות Google Health / Fit ({b_final_cal} קק״ל)"
                b_entry = upsert_health_sync_workout_db(
                    activity_name=b_name,
                    burned_calories=b_final_cal,
                    duration_min=b_dur,
                    distance_km=b_dist,
                    activity_type="walk" if b_steps > 0 else "other",
                    log_date=b_date_str,
                    source="google-fit",
                    user_id=uid,
                    notes=f"סונכרן אוטומטית מ-Google Fit ({b_date_str}): {b_steps:,} צעדים",
                )
                recent_synced = {
                    "date": b_date_str,
                    "steps": b_steps,
                    "burned_calories": b_final_cal,
                    "duration_min": b_dur,
                    "workout": b_entry,
                }

        if recent_synced:
            return {
                "status": "success",
                "provider": f"Google Health / Fit ({recent_synced['date']})",
                "burned_calories": recent_synced["burned_calories"],
                "steps": recent_synced["steps"],
                "active_minutes": recent_synced["duration_min"],
                "sessions": [],
                "workout": recent_synced["workout"],
            }

        token_email = ""
        has_fit_scope = True
        try:
            ti_url = f"https://www.googleapis.com/oauth2/v1/tokeninfo?access_token={urllib.parse.quote(token)}"
            with urllib.request.urlopen(ti_url, timeout=4) as ti_resp:
                ti_data = json.loads(ti_resp.read().decode("utf-8"))
                token_email = ti_data.get("email", "")
                scopes_str = ti_data.get("scope", "")
                if "fitness" not in scopes_str:
                    has_fit_scope = False
        except Exception:
            pass

        if not has_fit_scope:
            msg = "לא סומנה תיבת ההרשאה לנתוני פעילות (Google Fit) בחלון האישור של גוגל. נסה שוב וסמן ☑️ בחלון ההרשאות."
        else:
            acct_note = f" בחשבון {token_email}" if token_email else ""
            msg = (
                f"בענן של Google Fit{acct_note} עדיין לא עלו צעדים מהטלפון להיום. "
                "באנדרואיד חדש (Health Connect / Samsung Health) הנתונים נשמרים מקומית בטלפון — מומלץ ללחוץ על '📸 סנכרון מהיר מצילום מסך' מעל!"
            )

        return {
            "status": "no_data",
            "message": msg,
            "burned_calories": 0,
            "steps": 0,
        }

    provider = "Samsung & Google Health" if from_samsung else "Google Health / Fit"
    if session_names and total_steps > 0:
        activity_name = f"{provider}: {', '.join(session_names[:2])} ({total_steps:,} צעדים)"
    elif session_names:
        activity_name = f"{provider}: {', '.join(session_names[:2])}"
    elif total_steps > 0:
        activity_name = f"{provider} ({total_steps:,} צעדים)"
    else:
        activity_name = f"פעילות {provider} ({total_calories} קק״ל)"

    duration_val = active_min if active_min > 0 else (max(15, int(total_steps / 100)) if total_steps > 0 else 30)
    dist_km = round(total_steps * 0.00075, 2) if total_steps >= 500 else None

    entry = upsert_health_sync_workout_db(
        activity_name=activity_name,
        burned_calories=total_calories,
        duration_min=duration_val,
        distance_km=dist_km,
        activity_type="walk" if total_steps > 0 and not session_names else "other",
        log_date=target_date_str,
        source="google-fit",
        user_id=uid,
        notes=f"סונכרן אוטומטית מ-{provider}: {total_steps:,} צעדים" + (f" • {', '.join(session_names)}" if session_names else ""),
    )
    return {
        "status": "success",
        "provider": provider,
        "burned_calories": total_calories,
        "steps": total_steps,
        "active_minutes": duration_val,
        "sessions": session_names,
        "workout": entry,
    }
def _legacy_sync_google_fit_unused(req: GoogleFitSyncRequest, request: Request):
    import urllib.request
    uid = require_approved_user(request)
    token = req.access_token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="נא לספק Google OAuth access token")

    target_date_str = req.log_date or str(date.today())
    try:
        target_dt = datetime.strptime(target_date_str, "%Y-%m-%d")
    except Exception:
        target_dt = datetime.today()

    start_ms = int(datetime(target_dt.year, target_dt.month, target_dt.day, 0, 0, 0).timestamp() * 1000)
    end_ms = int(datetime(target_dt.year, target_dt.month, target_dt.day, 23, 59, 59).timestamp() * 1000)

    url = "https://fitness.googleapis.com/fitness/v1/users/me/dataset:aggregate"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    body = {
        "aggregateBy": [
            {"dataTypeName": "com.google.calories.expended"},
            {"dataTypeName": "com.google.step_count.delta"},
            {"dataTypeName": "com.google.activity.segment"},
        ],
        "bucketByTime": {"durationMillis": 86400000},
        "startTimeMillis": start_ms,
        "endTimeMillis": end_ms,
    }

    req_obj = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req_obj, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"ERROR Google Fit API: {e}")
        raise HTTPException(status_code=502, detail=f"שגיאה בתקשורת עם Google Fit API: {e}")

    total_calories = 0
    total_steps = 0
    buckets = data.get("bucket", [])
    for b in buckets:
        for ds in b.get("dataset", []):
            dtype = ds.get("dataSourceId", "")
            for pt in ds.get("point", []):
                val_list = pt.get("value", [])
                if "calories" in dtype and val_list:
                    total_calories += val_list[0].get("fpVal", 0)
                elif "step_count" in dtype and val_list:
                    total_steps += val_list[0].get("intVal", 0)

    total_calories = int(round(total_calories))
    if total_calories <= 0 and total_steps <= 0:
        return {
            "status": "no_data",
            "message": f"לא נמצאו נתוני פעילות או צעדים מ-Google Fit לתאריך {target_date_str}",
            "burned_calories": 0,
            "steps": 0,
        }

    if total_calories <= 0 and total_steps > 0:
        total_calories = int(round(total_steps * 0.04))

    activity_name = f"Google Fit ({total_steps:,} צעדים)" if total_steps > 0 else "פעילות Google Fit"
    entry = log_workout_db(
        activity_name=activity_name,
        burned_calories=total_calories,
        duration_min=max(20, int(total_steps / 100)) if total_steps > 0 else 30,
        activity_type="walk" if total_steps > 0 else "other",
        log_date=target_date_str,
        source="google-fit",
        user_id=uid,
        notes=f"סונכרן מ-Google Fit: {total_steps} צעדים",
    )
    return {
        "status": "success",
        "burned_calories": total_calories,
        "steps": total_steps,
        "workout": entry,
    }


@app.post("/api/favorites")
def api_add_favorite(req: FavoriteMealRequest, request: Request):
    uid = require_approved_user(request)
    if not req.label or not req.label.strip():
        raise HTTPException(status_code=400, detail="נא להזין שם לפריט המועדף")
    return add_favorite_db(req.label, req.calories, req.protein_g, req.icon or "⭐", user_id=uid)


@app.delete("/api/favorites/{fav_id}")
def api_delete_favorite(fav_id: str, request: Request):
    uid = require_approved_user(request)
    deleted = delete_favorite_db(fav_id, user_id=uid)
    if not deleted:
        raise HTTPException(status_code=404, detail="פריט מועדף לא נמצא או שאינו שייך לך")
    return {"status": "deleted", "id": fav_id}


@app.get("/api/weight")
def api_get_weight(request: Request):
    uid = require_approved_user(request)
    return get_weights_db(user_id=uid)


@app.post("/api/weight")
def api_log_weight(req: WeightLogRequest, request: Request):
    uid = require_approved_user(request)
    return log_weight_db(req.weight_kg, req.log_date, req.note or "", user_id=uid)


@app.delete("/api/weight/{entry_id}")
def api_delete_weight(entry_id: str, request: Request):
    uid = require_approved_user(request)
    deleted = delete_weight_db(entry_id, user_id=uid)
    if not deleted:
        raise HTTPException(status_code=404, detail="רשומת משקל לא נמצאה או שאינה שייכת לך")
    return {"status": "deleted", "id": entry_id}


@app.put("/api/targets")
def api_update_targets(req: TargetUpdateRequest, request: Request):
    uid = require_approved_user(request)
    return set_targets(req.target_calories, req.target_protein, user_id=uid)


WOLT_CITIES = {
    "tel-aviv": {"label": "תל אביב והמרכז", "lat": 32.0853, "lon": 34.7818, "slug": "tel-aviv"},
    "rishon-lezion": {"label": "ראשון לציון, חולון ורחובות", "lat": 31.9730, "lon": 34.7925, "slug": "rishon-lezion"},
    "hasharon": {"label": "הרצליה, רעננה, כפ״ס ונתניה", "lat": 32.1663, "lon": 34.8436, "slug": "herzliya"},
    "jerusalem": {"label": "ירושלים והסביבה", "lat": 31.7683, "lon": 35.2137, "slug": "jerusalem"},
    "haifa": {"label": "חיפה והקריות", "lat": 32.7940, "lon": 34.9896, "slug": "haifa"},
    "beer-sheva": {"label": "באר שבע והדרום", "lat": 31.2530, "lon": 34.7915, "slug": "beer-sheva"},
}

CURATED_WOLT_DISHES = [
    {
        "dish_name": "סלט עוף חם / חזה עוף על הפלנצ׳ה",
        "restaurant_name": "ארומה (Aroma)",
        "category": "סלטים וקערות",
        "calories": 430,
        "protein_g": 42.0,
        "description": "נתחי חזה עוף חמים על מצע חסה, עגבנייה, מלפפון, בצל סגול ופטרוזיליה.",
        "smart_order_tip": "בקש רוטב שמן זית ולימון בצד, וותר על קרוטונים או החלף בלחם קל (חוסך כ-160 קק״ל).",
        "search_query": "ארומה סלט עוף",
    },
    {
        "dish_name": "קערת פוקי סלמון וטונה עשירה בחלבון",
        "restaurant_name": "פוקי (Poke Bowl)",
        "category": "סלטים וקערות",
        "calories": 510,
        "protein_g": 38.0,
        "description": "קוביות סלמון וטונה אדומה נאים, אדממה, מלפפון, גזר, כרוב סגול ובצל ירוק.",
        "smart_order_tip": "בחר בסיס של חצי אורז סושי וחצי עלים ירוקים/סלט, ובקש רוטב פונזו או סויה במקום מיונז ספייסי.",
        "search_query": "פוקי סלמון",
    },
    {
        "dish_name": "המבורגר קלאסי 200 גרם בלחמנייה קלה / חסה",
        "restaurant_name": "מוזס / וולפנייטס / בורגר",
        "category": "בשרים והמבורגר",
        "calories": 540,
        "protein_g": 44.0,
        "description": "המבורגר בקר 200 גרם על הגריל עם ירקות טריים, חמוצים ובצל.",
        "smart_order_tip": "הזמן בלחמניית חיטה קלה או עטוף בחסה (Lettuce Wrap), עם סלט ירוק בצד במקום צ׳יפס ורטבים בצד.",
        "search_query": "המבורגר",
    },
    {
        "dish_name": "קומבינציית סשימי ורול סלמון אבוקדו (8 יח׳ + סשימי)",
        "restaurant_name": "אושי אושי / ג׳פניקה / סושי",
        "category": "סושי ואסיאתי",
        "calories": 460,
        "protein_g": 34.0,
        "description": "רול מאקי/אינסייד אאוט סלמון ואבוקדו לצד פרוסות סשימי סלמון/טונה ואדממה.",
        "smart_order_tip": "הימנע מרולים מטוגנים (פנקו) ומספייסי מיונז — סויה וג׳ינג׳ר בצד שומרים על מנה נקייה ועתירת חלבון.",
        "search_query": "סושי סלמון",
    },
    {
        "dish_name": "המנה החריפה / מוקפץ חזה עוף וירקות",
        "restaurant_name": "ג׳ירף (Giraffe)",
        "category": "סושי ואסיאתי",
        "calories": 590,
        "protein_g": 41.0,
        "description": "נתחי חזה עוף מוקפצים בווק עם ירקות ירוקים, פלפלים, בצל ירוק ואורז מאודה.",
        "smart_order_tip": "בחר מנת ווק על בסיס אורז מאודה (ולא נודלס בשמן) ואכול כ-2/3 מכמות האורז לקבלת יחס חלבון מושלם.",
        "search_query": "ג'ירף המנה החריפה",
    },
    {
        "dish_name": "צמד שיפודי פרגית / פילה עוף בצלחת עם סלטים",
        "restaurant_name": "שיפודייה / גריל ישראלי",
        "category": "ים-תיכוני",
        "calories": 520,
        "protein_g": 49.0,
        "description": "2 שיפודי פרגית או חזה עוף על האש בצלחת (ללא לאפה) עם סלט ירקות קצוץ וירקות גריל.",
        "smart_order_tip": "הזמן בצלחת במקום בפיתה/לאפה, ובקש כף טחינה אחת בצד במקום צ׳יפס.",
        "search_query": "שיפודי פרגית בצלחת",
    },
    {
        "dish_name": "סלט בהרכבה אישית (חזה עוף + ביצה קשה / טונה)",
        "restaurant_name": "סלט בהרכבה / בהדונס / גרין",
        "category": "סלטים וקערות",
        "calories": 440,
        "protein_g": 45.0,
        "description": "סלט גדול מירקות טריים עם תוספת כפולה של חלבון: חזה עוף חם + ביצה קשה או טונה.",
        "smart_order_tip": "בחר רוטב לימון ושמן זית או ויניגרט הדרים קל בצד (הימנע מאלף האיים / פסטו שמנת).",
        "search_query": "סלט בהרכבה חזה עוף",
    },
    {
        "dish_name": "מק רויאל על גחלים (ללא מיונז) + סלט ירוק",
        "restaurant_name": "מקדונלד׳ס (McDonald's)",
        "category": "בשרים והמבורגר",
        "calories": 495,
        "protein_g": 35.0,
        "description": "מק רויאל על גחלים מ-100% בשר בקר עם ירקות, לצד סלט ירוק ושתייה זירו.",
        "smart_order_tip": "סמן באפליקציית Wolt 'ללא רוטב מק רויאל (מיונז)' והחלף את הצ׳יפס בסלט ירוק — חוסך מעל 400 קק״ל!",
        "search_query": "מקדונלדס מק רויאל",
    },
    {
        "dish_name": "כריך חביתה / סלמון / טונה בלחם קל",
        "restaurant_name": "ארומה / קפה קפה / ארקפה",
        "category": "כריכים וחלבי",
        "calories": 340,
        "protein_g": 27.0,
        "description": "כריך בלחם דגנים קל עם חביתה מ-2 ביצים וגבינת שמנת 5% או טונה וירקות.",
        "smart_order_tip": "סמן 'לחם קל' באפשרויות הכריך ב-Wolt ובקש סלט אישי בצד ללא רוטב שמנת.",
        "search_query": "ארומה כריך לחם קל",
    },
]


_WOLT_SEARCH_CACHE: dict = {}


def _build_wolt_deep_link(
    search_query: str,
    city_slug: str = "tel-aviv",
    venue_slug: Optional[str] = None,
    item_id: Optional[str] = None,
) -> tuple[str, str]:
    clean_city = (city_slug or "tel-aviv").strip().lower()
    wolt_region = WOLT_CITIES[clean_city]["slug"] if clean_city in WOLT_CITIES else clean_city
    if venue_slug and item_id:
        wolt_path = f"wolt.com/he/isr/{wolt_region}/restaurant/{venue_slug}/itemid-{item_id}"
    elif venue_slug:
        wolt_path = f"wolt.com/he/isr/{wolt_region}/restaurant/{venue_slug}"
    else:
        wolt_path = f"wolt.com/he/isr/{wolt_region}"
    web_url = f"https://{wolt_path}"
    fallback_enc = urllib.parse.quote(web_url, safe="")
    android_intent = f"intent://{wolt_path}#Intent;scheme=https;package=com.wolt.android;S.browser_fallback_url={fallback_enc};end"
    return web_url, android_intent


def _enrich_wolt_dish_live(item: dict, lat: float, lon: float, city_slug: str) -> dict:
    """Queries Wolt's Search API to resolve the exact restaurant slug + dish itemid for direct deep-linking."""
    enriched = dict(item)
    q_str = (enriched.get("search_query") or f"{enriched.get('restaurant_name', '')} {enriched.get('dish_name', '')}").strip()
    cache_key = (q_str.lower(), round(lat, 2), round(lon, 2))
    now_ts = time.time()

    cached = _WOLT_SEARCH_CACHE.get(cache_key)
    if cached and (now_ts - cached.get("ts", 0) < 1800):
        enriched.update(cached["data"])
        return enriched

    url = "https://restaurant-api.wolt.com/v1/pages/search"
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/124.0 Mobile Safari/537.36",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Accept-Language": "he-IL,he;q=0.9,en;q=0.8",
    }
    payload = json.dumps({"q": q_str, "lat": lat, "lon": lon}).encode("utf-8")

    resolved_data = {}
    try:
        req_obj = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req_obj, timeout=3.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        sections = data.get("sections", [])
        # 1. Try to match exact dish (menu_item_details) so Wolt opens the exact dish modal (itemid-...)
        for sec in sections:
            if sec.get("name") == "items" or sec.get("template") == "items":
                for it in sec.get("items", []):
                    mid = (it.get("link") or {}).get("menu_item_details") or {}
                    v_slug = (mid.get("venue_slug") or "").strip()
                    it_id = (mid.get("id") or "").strip()
                    if v_slug and it_id:
                        w_city = (mid.get("city_slug") or city_slug or "tel-aviv").strip()
                        web_url, android_intent = _build_wolt_deep_link(q_str, w_city, venue_slug=v_slug, item_id=it_id)
                        price_raw = int(mid.get("price") or 0)
                        resolved_data = {
                            "wolt_url": web_url,
                            "wolt_android_intent": android_intent,
                            "wolt_venue_slug": v_slug,
                            "wolt_item_id": it_id,
                            "live_venue_name": (mid.get("venue_name") or "").strip() or enriched.get("restaurant_name"),
                            "live_dish_name": (mid.get("name") or "").strip() or enriched.get("dish_name"),
                            "price_ils": round(price_raw / 100) if price_raw > 0 else None,
                            "image_url": (mid.get("image") or {}).get("url") or "",
                        }
                        break
            if resolved_data:
                break

        # 2. Fallback to top matching restaurant venue if no specific menu item returned
        if not resolved_data:
            for sec in sections:
                if sec.get("name") == "venues" or sec.get("template") == "venues":
                    for it in sec.get("items", []):
                        v = it.get("venue") or {}
                        v_slug = (v.get("slug") or "").strip()
                        if v_slug:
                            web_url, android_intent = _build_wolt_deep_link(q_str, city_slug, venue_slug=v_slug)
                            resolved_data = {
                                "wolt_url": web_url,
                                "wolt_android_intent": android_intent,
                                "wolt_venue_slug": v_slug,
                                "live_venue_name": (v.get("name") or "").strip() or enriched.get("restaurant_name"),
                            }
                            break
                if resolved_data:
                    break
    except Exception as e:
        print(f"WARN _enrich_wolt_dish_live ({q_str}): {e}")

    if not resolved_data:
        web_url, android_intent = _build_wolt_deep_link(q_str, city_slug)
        resolved_data = {
            "wolt_url": web_url,
            "wolt_android_intent": android_intent,
        }
    else:
        _WOLT_SEARCH_CACHE[cache_key] = {"ts": now_ts, "data": resolved_data}

    enriched.update(resolved_data)
    return enriched


def _enrich_wolt_dishes_parallel(dishes: List[dict], lat: float, lon: float, city_slug: str) -> List[dict]:
    import concurrent.futures
    if not dishes:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(9, len(dishes))) as executor:
        futures = [executor.submit(_enrich_wolt_dish_live, d, lat, lon, city_slug) for d in dishes]
        return [f.result() for f in futures]


def _fetch_live_wolt_venues(lat: float, lon: float, limit: int = 24) -> List[dict]:
    """Queries Wolt's live Consumer Storefront API for open restaurants at the user's coordinates."""
    url = f"https://restaurant-api.wolt.com/v1/pages/restaurants?lat={lat:.4f}&lon={lon:.4f}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/124.0 Mobile Safari/537.36",
        "Accept": "application/json",
        "Accept-Language": "he-IL,he;q=0.9,en;q=0.8",
    }
    venues = []
    try:
        req_obj = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req_obj, timeout=4.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        for sec in data.get("sections", []):
            for it in sec.get("items", []):
                v = it.get("venue")
                if not v or not v.get("online", True):
                    continue
                name = (v.get("name") or "").strip()
                slug = (v.get("slug") or "").strip()
                if not name or not slug:
                    continue
                rating_obj = v.get("rating") or {}
                venues.append({
                    "name": name,
                    "slug": slug,
                    "short_description": (v.get("short_description") or "").strip(),
                    "estimate_range": v.get("estimate_range") or "25-35",
                    "rating": rating_obj.get("score"),
                    "tags": v.get("tags") or [],
                })
                if len(venues) >= limit:
                    return venues
    except Exception as e:
        print(f"WARN _fetch_live_wolt_venues: {e}")
    return venues


@app.get("/api/wolt/dishes")
def api_get_wolt_dishes(
    request: Request,
    city_slug: str = Query(default="tel-aviv"),
    lat: Optional[float] = Query(default=None),
    lon: Optional[float] = Query(default=None),
):
    require_approved_user(request)
    city_info = WOLT_CITIES.get(city_slug, WOLT_CITIES["tel-aviv"])
    use_lat = lat if lat is not None else city_info["lat"]
    use_lon = lon if lon is not None else city_info["lon"]

    live_venues = _fetch_live_wolt_venues(use_lat, use_lon, limit=25)
    enriched = _enrich_wolt_dishes_parallel(CURATED_WOLT_DISHES, use_lat, use_lon, city_slug)

    return {
        "dishes": enriched,
        "live_venues_count": len(live_venues),
        "live_venues": live_venues[:12],
        "cities": [{"id": k, "label": v["label"]} for k, v in WOLT_CITIES.items()],
    }


@app.post("/api/wolt/suggest")
def api_suggest_wolt_dishes(req: WoltSuggestRequest, request: Request):
    uid = require_approved_user(request)
    check_and_increment_ai_quota(uid)

    city_slug = req.city_slug or "tel-aviv"
    city_info = WOLT_CITIES.get(city_slug, WOLT_CITIES["tel-aviv"])
    use_lat = req.lat if req.lat is not None else city_info["lat"]
    use_lon = req.lon if req.lon is not None else city_info["lon"]
    live_venues = _fetch_live_wolt_venues(use_lat, use_lon, limit=20)

    budget_cal = max(200, min(int(req.remaining_calories or 600), 1400))
    budget_prot = max(15, int(round(req.remaining_protein or 35)))
    user_q = (req.query or "").strip()

    if not ai_client:
        fallback = [dict(d, isAI=True) for d in CURATED_WOLT_DISHES if d["calories"] <= budget_cal + 80][:4] or [dict(d, isAI=True) for d in CURATED_WOLT_DISHES[:3]]
        enriched_fb = _enrich_wolt_dishes_parallel(fallback, use_lat, use_lon, city_slug)
        return {"dishes": enriched_fb, "live_venues_count": len(live_venues)}

    venues_context = ""
    if live_venues:
        v_names = ", ".join(f"{v['name']} ({v['short_description']})" for v in live_venues[:15])
        venues_context = f"\nמסעדות פתוחות כעת ב-Wolt באזור המשתמש ({city_info['label']}): {v_names}."

    query_context = f"\nבקשת המשתמש / מסעדה או סגנון מבוקש: '{user_q}'." if user_q else "\nהצע מגוון מנות פופולריות וטעימות להזמנה מ-Wolt בישראל."

    prompt = f"""אתה תזונאי קליני ומומחה למסעדות ומשלוחי Wolt בישראל.
יתרת הקלוריות של המשתמש להיום: עד {budget_cal} קק"ל (חשוב מאוד שהמנות יתאימו לתקציב זה ולא יחרגו ממנו משמעותית!).
יעד חלבון רצוי למנה: סביב {budget_prot} גרם חלבון (דגש על חלבון איכותי ומשביע).{venues_context}{query_context}

הצע 4 מנות ספציפיות, אמיתיות ומדויקות שאפשר להזמין עכשיו ב-Wolt בישראל:
1. אם המשתמש ציין שם מסעדה או סגנון ספציפי, הצע מנות מדויקות מאותה מסעדה/סגנון שמתאימות לתקציב ה-{budget_cal} קק"ל.
2. אם יש מסעדות פתוחות מהרשימה למעלה שמתאימות, שלב אותן בהמלצות.
3. לכל מנה ציין ב-smart_order_tip בדיוק מה לסמן בהזמנה ב-Wolt (למשל: לבקש רוטב בצד, להחליף פחמימה בירקות, לבחור לחם קל וכו') כדי לעמוד בקלוריות.
4. בשדה search_query כתוב את שם המסעדה + שם המנה הקצר לחיפוש ישיר ב-Wolt."""

    try:
        response = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=WoltDishList,
                temperature=0.25,
            ),
        )
        parsed = WoltDishList.model_validate_json(response.text)
        raw_dishes = [dict(d.model_dump(), isAI=True) for d in parsed.dishes]
        result_dishes = _enrich_wolt_dishes_parallel(raw_dishes, use_lat, use_lon, city_slug)
        return {"dishes": result_dishes, "live_venues_count": len(live_venues)}
    except Exception as e:
        print(f"ERROR api_suggest_wolt_dishes: {e}")
        raise HTTPException(status_code=500, detail="לא ניתן היה לייצר המלצות Wolt כרגע, נסה שוב")


@app.post("/api/wolt/scan-menu")
def api_scan_wolt_menu(req: WoltMenuScanRequest, request: Request):
    uid = require_approved_user(request)
    raw_b64 = (req.image_base64 or "").strip()
    if not raw_b64:
        raise HTTPException(status_code=400, detail="נא להעלות צילום מסך של תפריט מ-Wolt")

    mime_type = req.mime_type or "image/jpeg"
    if raw_b64.startswith("data:"):
        header, _, raw_b64 = raw_b64.partition(",")
        if ";" in header and ":" in header:
            mime_type = header.split(":")[1].split(";")[0] or mime_type

    raw_b64 = "".join(raw_b64.split())
    pad = (4 - len(raw_b64) % 4) % 4
    if pad:
        raw_b64 += "=" * pad

    try:
        image_bytes = base64.b64decode(raw_b64)
        if not image_bytes:
            raise ValueError("Empty image")
    except Exception:
        raise HTTPException(status_code=400, detail="קובץ תמונה לא תקין")

    check_and_increment_ai_quota(uid)
    norm_bytes, clean_mime = _normalize_image_bytes(image_bytes, mime_type)
    budget_cal = max(200, int(req.remaining_calories or 600))
    budget_prot = max(15, int(round(req.remaining_protein or 35)))
    city_slug = req.city_slug or "tel-aviv"
    city_info = WOLT_CITIES.get(city_slug, WOLT_CITIES["tel-aviv"])

    if not ai_client:
        raise HTTPException(status_code=503, detail="שירות ה-AI אינו זמין כרגע")

    prompt = f"""נתח את צילום המסך של תפריט המסעדה מתוך אפליקציית Wolt (או תפריט משלוחים).
יתרת הקלוריות של המשתמש להיום: {budget_cal} קק"ל, ויתרת החלבון: {budget_prot} גרם.
1. זהה את המנות המופיעות בצילום המסך (ואת שם המסעדה אם מופיע).
2. חשב הערכת קלוריות וחלבון מציאותית ומדויקת לכל מנה בתפריט שצולם.
3. דרג והחזר קודם כל את המנות שהכי מתאימות ליתרת ה-{budget_cal} קק"ל של המשתמש!
4. בשדה smart_order_tip ציין אילו שינויים כדאי לבקש באותה מנה כדי שתתאים בול ליתרה היומית."""

    try:
        response = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                types.Part.from_bytes(data=norm_bytes, mime_type=clean_mime),
                prompt,
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=WoltDishList,
                temperature=0.15,
            ),
        )
        parsed = WoltDishList.model_validate_json(response.text)
        raw_dishes = [dict(d.model_dump(), isAI=True, fromMenuScan=True) for d in parsed.dishes]
        result_dishes = _enrich_wolt_dishes_parallel(raw_dishes, city_info["lat"], city_info["lon"], city_slug)
        return {"dishes": result_dishes}
    except Exception as e:
        print(f"ERROR api_scan_wolt_menu: {e}")
        raise HTTPException(status_code=502, detail="שגיאה בפענוח תפריט ה-Wolt מתוך צילום המסך")


@app.post("/api/supermarket/scan")
def api_supermarket_scan(req: SupermarketScanRequest, request: Request):
    uid = require_approved_user(request)
    raw_b64 = (req.image_base64 or "").strip()
    if not raw_b64:
        raise HTTPException(status_code=400, detail="נא לצלם מוצר או תווית תזונתית מהסופר")

    mime_type = req.mime_type or "image/jpeg"
    if raw_b64.startswith("data:"):
        header, _, raw_b64 = raw_b64.partition(",")
        if ";" in header and ":" in header:
            mime_type = header.split(":")[1].split(";")[0] or mime_type

    raw_b64 = "".join(raw_b64.split())
    pad = (4 - len(raw_b64) % 4) % 4
    if pad:
        raw_b64 += "=" * pad

    try:
        image_bytes = base64.b64decode(raw_b64)
        if not image_bytes:
            raise ValueError("Empty image")
    except Exception:
        raise HTTPException(status_code=400, detail="קובץ תמונה לא תקין")

    check_and_increment_ai_quota(uid)
    norm_bytes, clean_mime = _normalize_image_bytes(image_bytes, mime_type)
    budget_cal = max(100, int(req.remaining_calories or 600))
    budget_prot = max(10, int(round(req.remaining_protein or 35)))

    if not ai_client:
        return {
            "product_name": "מוצר נסרק מהסופר",
            "verdict": "מומלץ מאוד ✅",
            "score": 85,
            "calories_per_100g": 120,
            "protein_per_100g": 12.0,
            "serving_description": "יחידה אחת (200 גרם)",
            "serving_calories": 240,
            "serving_protein_g": 24.0,
            "fits_today_budget": True,
            "explanation": "מוצר עשיר בחלבון שמתאים מצוין ליתרת הקלוריות היומית שלך.",
            "better_alternative": "",
        }

    prompt = f"""נתח את המוצר או התווית התזונתית שבתמונה מתוך הסופר הישראלי ("האם לקנות?").
יתרת הקלוריות של המשתמש להיום: {budget_cal} קק"ל, ויתרת החלבון להשלמה: {budget_prot} גרם.
1. זהה את שם המוצר והמותג (או סוג המזון), וחשב/קרא את הקלוריות והחלבון ל-100 גרם וגם למנה מומלצת או אריזה אישית (serving_calories, serving_protein_g).
2. קבע פסק דין ברור (verdict): 'מומלץ מאוד ✅', 'אפשרי במידה ⚖️', או 'פחות מומלץ ⚠️', ותן ציון התאמה (score) בין 1 ל-100.
3. בדוק אם מנה אחת נכנסת בתקציב של {budget_cal} קק"ל (fits_today_budget).
4. כתוב הסבר קצר, חד ופרקטי בעברית (explanation) ואם המוצר עתיר סוכר/שומן או דל חלבון הצע חלופה ישראלית עדיפה בסופר (better_alternative)."""

    try:
        response = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                types.Part.from_bytes(data=norm_bytes, mime_type=clean_mime),
                prompt,
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=SupermarketScanResult,
                temperature=0.15,
            ),
        )
        parsed = SupermarketScanResult.model_validate_json(response.text)
        return parsed.model_dump()
    except Exception as e:
        print(f"ERROR api_supermarket_scan: {e}")
        raise HTTPException(status_code=502, detail="שגיאה בפענוח המוצר מהתמונה, נסה שוב")


@app.post("/api/recipes/fridge")
def api_fridge_recipes(req: FridgeRecipeRequest, request: Request):
    uid = require_approved_user(request)
    raw_b64 = (req.image_base64 or "").strip()
    ing_text = (req.ingredients_text or "").strip()
    if not raw_b64 and not ing_text:
        raise HTTPException(status_code=400, detail="נא לצלם את המקרר/המצרכים או לבחור מצרכים שיש לך בבית")

    check_and_increment_ai_quota(uid)
    budget_cal = max(180, min(int(req.remaining_calories or 600), 1300))
    budget_prot = max(12, int(round(req.remaining_protein or 35)))

    if not ai_client:
        return {"recipes": generate_ai_recipes(budget_cal, budget_prot, ing_text or "מה שיש במקרר")}

    contents_list = []
    if raw_b64:
        mime_type = req.mime_type or "image/jpeg"
        if raw_b64.startswith("data:"):
            header, _, raw_b64 = raw_b64.partition(",")
            if ";" in header and ":" in header:
                mime_type = header.split(":")[1].split(";")[0] or mime_type
        raw_b64 = "".join(raw_b64.split())
        pad = (4 - len(raw_b64) % 4) % 4
        if pad:
            raw_b64 += "=" * pad
        try:
            image_bytes = base64.b64decode(raw_b64)
            norm_bytes, clean_mime = _normalize_image_bytes(image_bytes, mime_type)
            contents_list.append(types.Part.from_bytes(data=norm_bytes, mime_type=clean_mime))
        except Exception:
            raise HTTPException(status_code=400, detail="תמונת המקרר לא תקינה")

    ing_note = f"\nמצרכים שהמשתמש ציין שיש לו בבית: {ing_text}." if ing_text else ""
    photo_note = "\nזהה את המצרכים המופיעים בתמונה המצורפת של המקרר/השיש." if raw_b64 else ""

    prompt = f"""אתה שף ותזונאי ישראלי יצירתי.{photo_note}{ing_note}
יתרת הקלוריות של המשתמש להיום: סביב {budget_cal} קק"ל, ויתרת החלבון: {budget_prot} גרם חלבון.
הצע 3 ארוחות מהירות, טעימות ופרקטיות להכנה מידית מהמצרכים הקיימים בלבד (עם תבלינים בסיסיים שיש בכל בית):
1. כל מנה חייבת להתאים לתקציב הקלוריות ({budget_cal} קק"ל) ולתת חלבון גבוה ככל האפשר מהמצרכים הקיימים.
2. ציין זמן הכנה קצר, כמות מדויקת במצרכים ושלבי הכנה פשוטים וברורים."""

    contents_list.append(prompt)
    try:
        response = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=contents_list,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=RecipeSuggestionList,
                temperature=0.25,
            ),
        )
        parsed = RecipeSuggestionList.model_validate_json(response.text)
        return {"recipes": [r.model_dump() for r in parsed.recipes]}
    except Exception as e:
        print(f"ERROR api_fridge_recipes: {e}")
        raise HTTPException(status_code=502, detail="לא ניתן היה לייצר מתכונים מהמקרר כרגע, נסה שוב")


@app.get("/api/recipes")
def api_get_recipes():
    return {"recipes": CURATED_RECIPES}


@app.post("/api/recipes/suggest")
def api_suggest_recipes(req: RecipePromptRequest, request: Request):
    uid = require_approved_user(request)
    check_and_increment_ai_quota(uid)
    suggestions = generate_ai_recipes(
        remaining_cal=req.remaining_calories,
        remaining_prot=req.remaining_protein,
        preference=req.preference or "",
    )
    if not suggestions:
        raise HTTPException(status_code=500, detail="לא ניתן היה לייצר מתכונים כרגע (בדוק מפתח Gemini)")
    return {"recipes": suggestions}


@app.get("/api/push/vapid-public-key")
def api_push_vapid_public_key(request: Request):
    uid = require_approved_user(request)
    vapid = get_or_create_vapid_keys()
    subs = get_push_subscriptions_db(user_id=uid)
    devices = [
        {
            "id": s["id"],
            "label": _detect_device_label(s.get("user_agent", "")),
            "is_mobile": _is_mobile_ua(s.get("user_agent", "")),
        }
        for s in subs
    ]
    return {
        "public_key": vapid["public_key"],
        "subscribers_count": len(subs),
        "mobile_subscribers_count": sum(1 for d in devices if d["is_mobile"]),
        "devices": devices,
    }


@app.post("/api/push/subscribe")
def api_push_subscribe(req: PushSubscribeRequest, request: Request):
    uid = require_approved_user(request)
    if not req.endpoint or not req.keys.p256dh or not req.keys.auth:
        raise HTTPException(status_code=400, detail="נתוני הרשמה להתראות חסרים")
    sub = save_push_subscription_db(
        endpoint=req.endpoint,
        p256dh=req.keys.p256dh,
        auth=req.keys.auth,
        user_agent=req.user_agent or "",
        user_id=uid,
    )
    return {"status": "subscribed", "id": sub["id"]}


@app.post("/api/push/unsubscribe")
def api_push_unsubscribe(req: PushUnsubscribeRequest):
    if req.endpoint:
        remove_push_subscription_db(req.endpoint)
    return {"status": "unsubscribed"}


@app.post("/api/push/test")
def api_push_test(request: Request, req: Optional[PushMessageRequest] = None):
    uid = require_approved_user(request)
    s = get_day_summary(user_id=uid)
    title = (req.title if req and req.title else None) or "NutriAgent AI 🔔 התראת בדיקה!"
    body = (req.body if req and req.body else None) or (
        f"הפוש בנייד עובד מעולה! 🔥 היום: {s['total_calories']}/{s['target_calories']} קק\"ל "
        f"(נותרו {s['remaining_calories']}) | 💪 חלבון: {s['total_protein']:.0f}g"
    )
    res = send_web_push_to_all(title=title, body=body, tag="nutriagent-test", user_id=uid)
    if res["total_subscriptions"] == 0:
        raise HTTPException(status_code=404, detail="לא נמצאו מכשירים רשומים להתראות. הפעל תחילה התראות במכשיר זה.")
    return res


@app.post("/api/push/remind")
def api_push_daily_reminder(request: Request):
    """Can be triggered manually or via Cloud Scheduler to send a smart daily status push."""
    uid = require_approved_user(request)
    s = get_day_summary(user_id=uid)
    rem_cal = s["remaining_calories"]
    rem_prot = max(0.0, s["target_protein"] - s["total_protein"])
    if s["meal_count"] == 0:
        title = "🥗 תזכורת יומית מ-NutriAgent"
        body = f"עדיין לא תיעדת ארוחות היום! היעד שלך: {s['target_calories']} קק\"ל ו-{s['target_protein']}g חלבון."
    elif rem_cal > 0:
        title = "📊 סיכום ביניים להיום"
        body = f"נותרו לך עוד {rem_cal} קק\"ל ו-{rem_prot:.0f}g חלבון להשלמת היעד היומי 💪"
    else:
        title = "🎯 הגעת ליעד הקלורי היומי!"
        body = f"סה\"כ היום: {s['total_calories']} קק\"ל ו-{s['total_protein']:.0f}g חלבון. כל הכבוד!"
    return send_web_push_to_all(title=title, body=body, tag="nutriagent-reminder", user_id=uid)


@app.post("/webhook")
async def telegram_webhook(request: Request):
    sec = get_telegram_webhook_secret()
    if sec:
        incoming_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not incoming_token or not hmac.compare_digest(incoming_token, sec):
            raise HTTPException(status_code=403, detail="Invalid Telegram webhook secret token")
    if not tg_app:
        raise HTTPException(status_code=503, detail="BOT_TOKEN not configured")
    req_data = await request.json()
    update = Update.de_json(req_data, tg_app.bot)
    await tg_app.process_update(update)
    return {"status": "ok"}