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
from fastapi import FastAPI, Request, HTTPException, Query
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

class NutritionExtraction(BaseModel):
    is_meal: bool = Field(description="האם מדובר בדיווח על אוכל/שתייה")
    calories: int = Field(description="הערכת קלוריות כוללת")
    protein_g: float = Field(description="הערכת חלבון כוללת בגרמים")
    summary: str = Field(description="תיאור תמציתי וברור של המנה בעברית")


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


class RecipePromptRequest(BaseModel):
    remaining_calories: int = 600
    remaining_protein: float = 40.0
    preference: Optional[str] = ""


class MealUpdateRequest(BaseModel):
    description: str
    calories: int = Field(ge=0, le=10000)
    protein_g: float = Field(ge=0.0, le=500.0)


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
_LOCAL_FAVORITES: List[dict] = [dict(f, user_id="default") for f in DEFAULT_FAVORITES]
_LOCAL_WEIGHTS: List[dict] = []
_LOCAL_VAPID: Optional[dict] = None
_LOCAL_PUSH_SUBS: List[dict] = []
_LOCAL_USERS: dict = {}
_LOCAL_TG_LINKS: dict = {}
_LOCAL_AUTH_CONFIG: dict = {"google_client_id": ENV_GOOGLE_CLIENT_ID, "primary_user_id": "default"}


def _normalize_user_id(raw_uid: Optional[str]) -> str:
    if not raw_uid:
        return "default"
    cleaned = re.sub(r"[^a-zA-Z0-9_\-]", "", raw_uid.strip())[:64]
    return cleaned if cleaned else "default"


def get_request_user_id(request: Optional[Request] = None) -> str:
    if not request:
        return "default"
    header_uid = request.headers.get("X-User-Id") or request.query_params.get("user_id")
    return _normalize_user_id(header_uid)


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
    url = f"https://oauth2.googleapis.com/tokeninfo?id_token={urllib.parse.quote(id_token.strip())}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=8) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not data.get("email"):
        raise ValueError("Invalid Google token: missing email")
    cfg = get_auth_config_db()
    expected_cid = cfg.get("google_client_id", "").strip()
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
    log_date: Optional[str] = None,
    log_time: Optional[str] = None,
    source: str = "web",
    user_id: str = "default",
) -> Optional[dict]:
    uid = _normalize_user_id(user_id)
    d_str = log_date or str(date.today())
    t_str = log_time or datetime.now().strftime("%H:%M")
    if not db:
        entry = {
            "id": f"local-{int(datetime.now().timestamp() * 1000)}",
            "user_id": uid,
            "log_date": d_str,
            "log_time": t_str,
            "description": description,
            "calories": int(calories),
            "protein_g": round(float(protein), 1),
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
            "source": source,
        }
    except Exception as e:
        print(f"ERROR log_meal_db: {type(e).__name__} - {e}")
        return None


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

    targets = get_targets(d_str, user_id=uid)
    target_cal = targets["target_calories"]
    target_prot = targets["target_protein"]
    remaining_cal = target_cal - total_cal
    return {
        "date": d_str,
        "user_id": uid,
        "total_calories": total_cal,
        "total_protein": round(total_prot, 1),
        "target_calories": target_cal,
        "target_protein": target_prot,
        "remaining_calories": remaining_cal,
        "meal_count": len(meals_list),
        "meals": meals_list,
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
אם יש מספר פריטים בדיווח אחד, סכם את סך הקלוריות והחלבון של כולם יחד ותן תיאור תמציתי וברור בעברית.
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


def analyze_meal_image(image_bytes: bytes, mime_type: str = "image/jpeg", caption: str = "") -> NutritionExtraction:
    if not ai_client:
        label = caption.strip() if caption and caption.strip() else "ארוחה מצולמת (ניתוח תמונה)"
        return NutritionExtraction(is_meal=True, calories=420, protein_g=32.0, summary=label)

    norm_bytes, clean_mime = _normalize_image_bytes(image_bytes, mime_type)

    caption_note = f"\nהערת המשתמש לתמונה: '{caption.strip()}'." if caption and caption.strip() else ""
    prompt = f"""נתח את התמונה המצורפת עבור מעקב תזונה ישראלי.{caption_note}
התמונה יכולה להיות:
1. צילום של מנה / צלחת אוכל / משקה / קפה / חטיף / פרי / כריך / סלט / מרק / קינוח — זהה את המרכיבים, הערך את גודל המנה בצורה מציאותית, וחשב סך קלוריות וחלבון בגרמים.
2. תווית ערכים תזונתיים / אריזת מוצר / ברקוד / תפריט / צילום מסך של מתכון או מוצר — קרא את הערכים התזונתיים או שם המוצר וחשב את הקלוריות והחלבון למנה/יחידה שנצרכה (או לפי הערת המשתמש אם צוינה כמות).
חשוב מאוד: המשתמש העלה תמונה זו במיוחד כדי לתעד ארוחה או שתייה! אם יש בתמונה כל דבר שקשור לאוכל, שתייה, אריזת מזון, כוס, צלחת, קערה, פרי, ירק, או טבלת ערכים תזונתיים — סמן תמיד is_meal=True ותן הערכה מציאותית!
החזר תיאור קצר, טבעי וברור בעברית בשדה summary (למשל: "חזה עוף עם אורז וסלט ירקות" או "יוגורט חלבון PRO תנובה 200 ג'").
רק אם התמונה אינה קשורה בשום צורה לאוכל או שתייה, סמן is_meal=False."""

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
                ),
            )
            return NutritionExtraction.model_validate_json(response.text)
        except Exception as e:
            last_err = e
            print(f"ERROR analyze_meal_image ({model_name}): {type(e).__name__} - {e}")

    return NutritionExtraction(is_meal=False, calories=0, protein_g=0, summary=f"API_ERROR: {type(last_err).__name__}: {last_err}")


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
4. אם בהקלטה מוזכר כל פריט אוכל או שתייה (כולל קפה, נשנוש, פרי או ארוחה) סמן תמיד is_meal=True. רק אם ההקלטה ריקה לחלוטין או אינה קשורה לאוכל/שתייה סמן is_meal=False."""

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

        log_meal_db(res.summary, res.calories, res.protein_g, source="telegram", user_id=uid)
        s = get_day_summary(user_id=uid)

        await update.message.reply_text(
            f"✅ נרשם: {res.summary}\n"
            f"🔥 +{res.calories} קק\"ל | 💪 +{res.protein_g:.0f}g חלבון\n"
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
            res = analyze_meal_image(img_bytes, mime_type="image/jpeg", caption=caption)
            if not res.is_meal:
                await status_reply.edit_text("לא זיהיתי אוכל, משקה או תווית מזון בתמונה 🤔 נסה לצלם שוב או להוסיף כיתוב.")
                return

            log_meal_db(res.summary, res.calories, res.protein_g, source="telegram-photo", user_id=uid)
            s = get_day_summary(user_id=uid)
            await status_reply.edit_text(
                f"📸 נרשם מתמונה: {res.summary}\n"
                f"🔥 +{res.calories} קק\"ל | 💪 +{res.protein_g:.0f}g חלבון\n"
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

            log_meal_db(res.summary, res.calories, res.protein_g, source="telegram-voice", user_id=uid)
            s = get_day_summary(user_id=uid)
            await status_reply.edit_text(
                f"🎙️ נרשם מהקלטה קולית: {res.summary}\n"
                f"🔥 +{res.calories} קק\"ל | 💪 +{res.protein_g:.0f}g חלבון\n"
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
    if not db:
        global _LOCAL_FAVORITES
        _LOCAL_FAVORITES = [f for f in _LOCAL_FAVORITES if f.get("id") != fav_id]
        return True
    db.collection("favorites").document(fav_id).delete()
    return True


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
    if not db:
        global _LOCAL_WEIGHTS
        _LOCAL_WEIGHTS = [w for w in _LOCAL_WEIGHTS if w.get("id") != entry_id and w.get("log_date") != entry_id]
        return True
    db.collection("weight_logs").document(entry_id).delete()
    return True


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
                await tg_app.bot.set_webhook(url=f"{WEBHOOK_URL.rstrip('/')}/webhook")
                print(f"INFO: Telegram webhook set to {WEBHOOK_URL.rstrip('/')}/webhook")
            except Exception as e:
                print(f"WARN: Failed to set Telegram webhook: {e}")
    yield
    if tg_app:
        await tg_app.stop()
        await tg_app.shutdown()


app = FastAPI(title="NutriAgent AI", lifespan=lifespan)

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
    return get_auth_config_db()


@app.put("/api/auth/config")
def api_set_auth_config(req: AuthConfigRequest, request: Request):
    cfg = get_auth_config_db()
    # Allow initial setup when no client_id is set yet; afterwards require admin access
    if cfg.get("google_client_id"):
        require_admin_user(request)
    return set_auth_config_db(req.google_client_id)


@app.post("/api/auth/google")
def api_auth_google(req: GoogleAuthRequest):
    if not req.credential or not req.credential.strip():
        raise HTTPException(
            status_code=400,
            detail="נדרש אימות רשמי מול Google (לא התקבל אסימון אימות מ-Google)",
        )
    try:
        verified = verify_google_id_token(req.credential)
        return upsert_user_profile(
            email=verified["email"],
            name=verified["name"],
            picture=verified["picture"],
        )
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"אימות Google נכשל: {e}")


@app.get("/api/auth/me")
def api_auth_me(request: Request):
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
    }


@app.post("/api/auth/claim-legacy-data")
def api_claim_legacy_data(request: Request):
    uid = get_request_user_id(request)
    if uid == "default":
        raise HTTPException(status_code=400, detail="יש להתחבר לחשבון תחילה כדי לייבא נתונים")
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


@app.get("/api/summary")
def api_summary(request: Request, target_date: Optional[str] = Query(default=None, alias="date")):
    uid = get_request_user_id(request)
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

    targets = get_targets(user_id=uid)
    daily_trend = []
    for i in range(6, -1, -1):
        d = str(today_date - timedelta(days=i))
        day_meals = [m for m in all_meals if m.get("log_date") == d]
        cal = sum(m["calories"] for m in day_meals)
        prot = round(sum(m["protein_g"] for m in day_meals), 1)
        daily_trend.append({
            "date": d,
            "calories": cal,
            "protein_g": prot,
            "meal_count": len(day_meals),
        })

    return {
        "meals": all_meals,
        "daily_trend": daily_trend,
        "target_calories": targets["target_calories"],
        "target_protein": targets["target_protein"],
        "start_date": start_date_str,
    }


@app.get("/api/history")
def api_history(request: Request, days: int = Query(default=14, ge=1, le=90)):
    uid = get_request_user_id(request)
    return _fetch_user_history(uid=uid, days=days)


@app.get("/api/meals/")
def api_meals_legacy(request: Request):
    """Backwards-compatible endpoint returning flat list of meals."""
    uid = get_request_user_id(request)
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

    entry = log_meal_db(
        description=extraction.summary,
        calories=extraction.calories,
        protein=extraction.protein_g,
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
    extraction = analyze_meal_image(image_bytes, mime_type=mime_type, caption=req.caption or "")
    if extraction.summary and extraction.summary.startswith("API_ERROR:"):
        if "402" in extraction.summary or "prepayment" in extraction.summary.lower():
            raise HTTPException(
                status_code=402,
                detail="יתרת התשלום המוקדם (Prepayment Credits) ב-Google AI Studio אזלה. יש להיכנס ל-https://ai.studio/projects ולהטעין תקציב.",
            )
        raise HTTPException(status_code=502, detail=f"שגיאה בתקשורת עם Gemini Vision ({extraction.summary}). נסה שוב.")
    if not extraction.is_meal:
        raise HTTPException(status_code=422, detail="לא זוהו אוכל, משקה או תווית מזון בתמונה")

    entry = log_meal_db(
        description=extraction.summary,
        calories=extraction.calories,
        protein=extraction.protein_g,
        log_date=req.log_date,
        source="web-photo",
        user_id=uid,
    )
    if not entry:
        raise HTTPException(status_code=500, detail="Failed to save photo meal to Firestore")
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

    entry = log_meal_db(
        description=extraction.summary,
        calories=extraction.calories,
        protein=extraction.protein_g,
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
def api_update_meal(meal_id: str, req: MealUpdateRequest):
    desc = req.description.strip()
    if not desc:
        raise HTTPException(status_code=400, detail="נא להזין תיאור ארוחה")

    if not db:
        for m in _LOCAL_MEALS:
            if m.get("id") == meal_id:
                m["description"] = desc
                m["calories"] = int(req.calories)
                m["protein_g"] = round(float(req.protein_g), 1)
                return m
        raise HTTPException(status_code=404, detail="Meal not found")

    try:
        doc_ref = db.collection("meals").document(meal_id)
        doc = doc_ref.get()
        if not doc.exists:
            raise HTTPException(status_code=404, detail="Meal not found")
        doc_ref.update({
            "description": desc,
            "calories": int(req.calories),
            "protein_g": round(float(req.protein_g), 1),
        })
        updated = doc.to_dict()
        updated.update({
            "id": meal_id,
            "description": desc,
            "calories": int(req.calories),
            "protein_g": round(float(req.protein_g), 1),
        })
        updated.pop("created_at", None)
        return updated
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update meal: {e}")


@app.delete("/api/meals/{meal_id}")
def api_delete_meal(meal_id: str):
    if not db:
        global _LOCAL_MEALS
        _LOCAL_MEALS = [m for m in _LOCAL_MEALS if m.get("id") != meal_id]
        return {"status": "deleted", "id": meal_id}
    try:
        doc_ref = db.collection("meals").document(meal_id)
        doc = doc_ref.get()
        if not doc.exists:
            raise HTTPException(status_code=404, detail="Meal not found")
        doc_ref.delete()
        return {"status": "deleted", "id": meal_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete meal: {e}")


@app.get("/api/favorites")
def api_get_favorites(request: Request):
    uid = get_request_user_id(request)
    return {"favorites": get_favorites_db(user_id=uid)}


@app.post("/api/favorites")
def api_add_favorite(req: FavoriteMealRequest, request: Request):
    uid = get_request_user_id(request)
    if not req.label or not req.label.strip():
        raise HTTPException(status_code=400, detail="נא להזין שם לפריט המועדף")
    return add_favorite_db(req.label, req.calories, req.protein_g, req.icon or "⭐", user_id=uid)


@app.delete("/api/favorites/{fav_id}")
def api_delete_favorite(fav_id: str, request: Request):
    uid = get_request_user_id(request)
    delete_favorite_db(fav_id, user_id=uid)
    return {"status": "deleted", "id": fav_id}


@app.get("/api/weight")
def api_get_weight(request: Request):
    uid = get_request_user_id(request)
    return get_weights_db(user_id=uid)


@app.post("/api/weight")
def api_log_weight(req: WeightLogRequest, request: Request):
    uid = get_request_user_id(request)
    return log_weight_db(req.weight_kg, req.log_date, req.note or "", user_id=uid)


@app.delete("/api/weight/{entry_id}")
def api_delete_weight(entry_id: str, request: Request):
    uid = get_request_user_id(request)
    delete_weight_db(entry_id, user_id=uid)
    return {"status": "deleted", "id": entry_id}


@app.put("/api/targets")
def api_update_targets(req: TargetUpdateRequest, request: Request):
    uid = get_request_user_id(request)
    return set_targets(req.target_calories, req.target_protein, user_id=uid)


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
    uid = get_request_user_id(request)
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
    uid = get_request_user_id(request)
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
    uid = get_request_user_id(request)
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
    uid = get_request_user_id(request)
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
    if not tg_app:
        return {"error": "BOT_TOKEN not configured"}
    req_data = await request.json()
    update = Update.de_json(req_data, tg_app.bot)
    await tg_app.process_update(update)
    return {"status": "ok"}