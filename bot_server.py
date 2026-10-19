import os
import json
import base64
import hashlib
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

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

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


class TargetUpdateRequest(BaseModel):
    target_calories: int = Field(ge=500, le=10000)
    target_protein: int = Field(default=120, ge=20, le=500)


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


# ---------------------------------------------------------------------------
# Firestore Helpers
# ---------------------------------------------------------------------------

DEFAULT_FAVORITES = [
    {"id": "fav-coffee", "icon": "☕", "label": "הפוך קטן", "calories": 80, "protein_g": 4.0},
    {"id": "fav-eggs", "icon": "🥚", "label": "2 ביצים וקוטג׳", "calories": 260, "protein_g": 26.0},
    {"id": "fav-chicken", "icon": "🍗", "label": "חזה עוף ואורז", "calories": 480, "protein_g": 45.0},
    {"id": "fav-tuna", "icon": "🐟", "label": "סלט טונה וביצה", "calories": 350, "protein_g": 36.0},
    {"id": "fav-pro", "icon": "🥤", "label": "יוגורט PRO / שייק", "calories": 160, "protein_g": 20.0},
]

# Local fallback state when running locally without GCP credentials
_LOCAL_TARGETS = {"target_calories": 1800, "target_protein": 120}
_LOCAL_MEALS: List[dict] = []
_LOCAL_FAVORITES: List[dict] = [dict(f) for f in DEFAULT_FAVORITES]
_LOCAL_WEIGHTS: List[dict] = []
_LOCAL_VAPID: Optional[dict] = None
_LOCAL_PUSH_SUBS: List[dict] = []


def get_targets(target_date: Optional[str] = None) -> dict:
    default_targets = dict(_LOCAL_TARGETS)
    if not db:
        return default_targets
    try:
        # Check global settings first, then date-specific override
        global_doc = db.collection("daily_targets").document("default").get()
        if global_doc.exists:
            g_data = global_doc.to_dict()
            default_targets["target_calories"] = g_data.get("target_calories", 1800)
            default_targets["target_protein"] = g_data.get("target_protein", 120)

        d_str = target_date or str(date.today())
        doc = db.collection("daily_targets").document(d_str).get()
        if doc.exists:
            data = doc.to_dict()
            return {
                "target_calories": data.get("target_calories", default_targets["target_calories"]),
                "target_protein": data.get("target_protein", default_targets["target_protein"]),
            }
    except Exception as e:
        print(f"ERROR get_targets: {type(e).__name__} - {e}")
    return default_targets


def set_targets(target_calories: int, target_protein: int, target_date: Optional[str] = None) -> dict:
    _LOCAL_TARGETS["target_calories"] = target_calories
    _LOCAL_TARGETS["target_protein"] = target_protein
    if not db:
        return dict(_LOCAL_TARGETS)
    d_str = target_date or str(date.today())
    payload = {
        "target_calories": target_calories,
        "target_protein": target_protein,
        "updated_at": firestore.SERVER_TIMESTAMP,
    }
    db.collection("daily_targets").document("default").set(payload, merge=True)
    db.collection("daily_targets").document(d_str).set(payload, merge=True)
    return {"target_calories": target_calories, "target_protein": target_protein}


def log_meal_db(
    description: str,
    calories: int,
    protein: float,
    log_date: Optional[str] = None,
    log_time: Optional[str] = None,
    source: str = "web",
) -> Optional[dict]:
    d_str = log_date or str(date.today())
    t_str = log_time or datetime.now().strftime("%H:%M")
    if not db:
        entry = {
            "id": f"local-{int(datetime.now().timestamp() * 1000)}",
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


def get_day_summary(target_date: Optional[str] = None):
    d_str = target_date or str(date.today())
    total_cal = 0
    total_prot = 0.0
    meals_list = []

    if db:
        try:
            meals_ref = db.collection("meals").where("log_date", "==", d_str).stream()
            for m in meals_ref:
                data = m.to_dict()
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
            if m.get("log_date") == d_str:
                total_cal += m["calories"]
                total_prot += m["protein_g"]
                meals_list.append(m)

    targets = get_targets(d_str)
    target_cal = targets["target_calories"]
    target_prot = targets["target_protein"]
    remaining_cal = target_cal - total_cal
    return {
        "date": d_str,
        "total_calories": total_cal,
        "total_protein": round(total_prot, 1),
        "target_calories": target_cal,
        "target_protein": target_prot,
        "remaining_calories": remaining_cal,
        "meals": meals_list,
    }


def get_today_summary():
    s = get_day_summary(str(date.today()))
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
        print(f"ERROR analyze_meal: {type(e).__name__} - {e}")
        return NutritionExtraction(is_meal=False, calories=0, protein_g=0, summary="Analysis error")


def analyze_meal_image(image_bytes: bytes, mime_type: str = "image/jpeg", caption: str = "") -> NutritionExtraction:
    if not ai_client:
        label = caption.strip() if caption and caption.strip() else "ארוחה מצולמת (ניתוח תמונה)"
        return NutritionExtraction(is_meal=True, calories=420, protein_g=32.0, summary=label)

    caption_note = f"\nהערת המשתמש לתמונה: '{caption.strip()}'." if caption and caption.strip() else ""
    prompt = f"""נתח את התמונה המצורפת עבור מעקב תזונה ישראלי.{caption_note}
התמונה יכולה להיות:
1. צילום של מנה / צלחת אוכל / משקה / חטיף — זהה את המרכיבים, הערך את גודל המנה בצורה מציאותית, וחשב סך קלוריות וחלבון בגרמים.
2. תווית ערכים תזונתיים / אריזת מוצר / ברקוד — קרא את טבלת הערכים התזונתיים או שם המוצר וחשב את הקלוריות והחלבון למנה/יחידה שנצרכה (או לפי הערת המשתמש אם צוינה כמות).
החזר תיאור קצר, טבעי וברור בעברית בשדה summary (למשל: "חזה עוף עם אורז וסלט ירקות" או "יוגורט חלבון PRO תנובה 200 ג'").
אם התמונה אינה קשורה כלל לאוכל, שתייה או מוצר מזון, סמן is_meal=False."""
    try:
        response = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type=mime_type or "image/jpeg"),
                prompt,
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=NutritionExtraction,
            ),
        )
        return NutritionExtraction.model_validate_json(response.text)
    except Exception as e:
        print(f"ERROR analyze_meal_image: {type(e).__name__} - {e}")
        return NutritionExtraction(is_meal=False, calories=0, protein_g=0, summary="Image analysis error")


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
# Telegram Bot Handlers
# ---------------------------------------------------------------------------

tg_app = ApplicationBuilder().token(BOT_TOKEN).build() if BOT_TOKEN else None

if tg_app:
    async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "היי! הבוט מחובר ומסונכרן עם האפליקציה 🚀\n"
            "• שלח בטקסט חופשי מה אכלת או שתית כדי לתעד.\n"
            "• שלח 📸 תמונה של הצלחת או של תווית המוצר לניתוח אוטומטי.\n"
            "• שלח /weight 78.5 לתיעוד משקל יומי.\n"
            "• שלח /push לשליחת התראת פוש לנייד.\n"
            "• שלח /status לסיכום היומי שלך."
        )

    async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        s = get_day_summary()
        w = get_weights_db(limit=5)
        w_line = f"\n⚖️ משקל נוכחי: {w['latest_weight']} ק\"ג" if w.get("latest_weight") else ""
        await update.message.reply_text(
            f"📊 סיכום להיום:\n"
            f"🔥 קלוריות: {s['total_calories']} / {s['target_calories']} קק\"ל (נותרו: {s['remaining_calories']})\n"
            f"💪 חלבון: {s['total_protein']:.0f} / {s['target_protein']} גרם{w_line}"
        )

    async def push_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        s = get_day_summary()
        custom_msg = " ".join(context.args).strip() if context.args else ""
        title = "NutriAgent AI 🔔"
        body = custom_msg or (
            f"🔥 היום: {s['total_calories']}/{s['target_calories']} קק\"ל "
            f"(נותרו {s['remaining_calories']}) | 💪 חלבון: {s['total_protein']:.0f}g"
        )
        result = send_web_push_to_all(title=title, body=body, tag="nutriagent-tg-push")
        if result["sent"] > 0:
            await update.message.reply_text(f"📲 נשלחה התראת פוש ל-{result['sent']} מכשירים מחוברים!")
        else:
            await update.message.reply_text(
                "לא נמצאו מכשירים רשומים להתראות פוש 📱\n"
                "היכנס לאפליקציה בנייד ולחץ על סמל הפעמון 🔔 בראש המסך כדי להפעיל התראות."
            )

    async def weight_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            w = get_weights_db(limit=5)
            if w.get("latest_weight"):
                await update.message.reply_text(f"⚖️ המשקל האחרון שתועד: {w['latest_weight']} ק\"ג\nלתיעוד שקילה חדשה שלח למשל: /weight 78.5")
            else:
                await update.message.reply_text("לתיעוד שקילה חדשה שלח למשל: /weight 78.5")
            return
        try:
            val = float(context.args[0].replace(",", "."))
            if val < 25 or val > 350:
                raise ValueError()
            log_weight_db(val)
            w = get_weights_db(limit=60)
            diff_str = ""
            if w.get("weight_change"):
                sign = "+" if w["weight_change"] > 0 else ""
                diff_str = f" ({sign}{w['weight_change']} ק\"ג מההתחלה)"
            await update.message.reply_text(f"⚖️ המשקל עודכן בהצלחה: {val:.1f} ק\"ג{diff_str}")
            send_web_push_to_all(
                title="⚖️ שקילה חדשה נשמרה!",
                body=f"משקל נוכחי: {val:.1f} ק\"ג{diff_str}",
                tag="nutriagent-weight",
            )
        except ValueError:
            await update.message.reply_text("נא להזין משקל תקין במספרים, למשל: /weight 78.5")

    async def text_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
        res = analyze_meal(update.message.text)
        if not res.is_meal:
            await update.message.reply_text("לא זיהיתי ארוחה או שתייה בהודעה 🤔 נסה לתאר שוב מה אכלת.")
            return

        log_meal_db(res.summary, res.calories, res.protein_g, source="telegram")
        s = get_day_summary()

        await update.message.reply_text(
            f"✅ נרשם: {res.summary}\n"
            f"🔥 +{res.calories} קק\"ל | 💪 +{res.protein_g:.0f}g חלבון\n"
            f"💰 נותר להיום: {s['remaining_calories']}/{s['target_calories']} קק\"ל (סה\"כ: {s['total_calories']})"
        )
        send_web_push_to_all(
            title=f"✅ נרשם: {res.summary}",
            body=f"🔥 +{res.calories} קק\"ל | 💪 +{res.protein_g:.0f}g חלבון • נותר להיום: {s['remaining_calories']} קק\"ל",
            tag="nutriagent-meal",
        )

    async def photo_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.message.photo:
            return
        status_reply = await update.message.reply_text("📸 מנתח את התמונה ב-AI...")
        try:
            photo_file = await update.message.photo[-1].get_file()
            img_bytes = bytes(await photo_file.download_as_bytearray())
            caption = update.message.caption or ""
            res = analyze_meal_image(img_bytes, mime_type="image/jpeg", caption=caption)
            if not res.is_meal:
                await status_reply.edit_text("לא זיהיתי אוכל, משקה או תווית מזון בתמונה 🤔 נסה לצלם שוב או להוסיף כיתוב.")
                return

            log_meal_db(res.summary, res.calories, res.protein_g, source="telegram-photo")
            s = get_day_summary()
            await status_reply.edit_text(
                f"📸 נרשם מתמונה: {res.summary}\n"
                f"🔥 +{res.calories} קק\"ל | 💪 +{res.protein_g:.0f}g חלבון\n"
                f"💰 נותר להיום: {s['remaining_calories']}/{s['target_calories']} קק\"ל (סה\"כ: {s['total_calories']})"
            )
            send_web_push_to_all(
                title=f"📸 נרשם מתמונה: {res.summary}",
                body=f"🔥 +{res.calories} קק\"ל | 💪 +{res.protein_g:.0f}g חלבון • נותר להיום: {s['remaining_calories']} קק\"ל",
                tag="nutriagent-meal",
            )
        except Exception as e:
            print(f"ERROR photo_msg: {type(e).__name__} - {e}")
            await status_reply.edit_text("שגיאה בניתוח התמונה, נסה שוב מאוחר יותר.")

    tg_app.add_handler(CommandHandler("start", start_cmd))
    tg_app.add_handler(CommandHandler("status", status_cmd))
    tg_app.add_handler(CommandHandler("push", push_cmd))
    tg_app.add_handler(CommandHandler("weight", weight_cmd))
    tg_app.add_handler(MessageHandler(filters.PHOTO, photo_msg))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_msg))


# ---------------------------------------------------------------------------
# Favorites & Weight Firestore Helpers
# ---------------------------------------------------------------------------

def get_favorites_db() -> List[dict]:
    if not db:
        return list(_LOCAL_FAVORITES)
    try:
        docs = list(db.collection("favorites").order_by("created_at").stream())
        if not docs:
            # Seed default favorites on first use
            seeded = []
            for f in DEFAULT_FAVORITES:
                doc_ref = db.collection("favorites").document(f["id"])
                doc_ref.set({
                    "icon": f["icon"],
                    "label": f["label"],
                    "calories": int(f["calories"]),
                    "protein_g": float(f["protein_g"]),
                    "created_at": firestore.SERVER_TIMESTAMP,
                })
                seeded.append(dict(f))
            return seeded
        result = []
        for doc in docs:
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


def add_favorite_db(label: str, calories: int, protein_g: float, icon: str = "⭐") -> dict:
    clean_icon = (icon or "⭐").strip() or "⭐"
    if not db:
        entry = {
            "id": f"fav-{int(datetime.now().timestamp() * 1000)}",
            "icon": clean_icon,
            "label": label.strip(),
            "calories": int(calories),
            "protein_g": round(float(protein_g), 1),
        }
        _LOCAL_FAVORITES.append(entry)
        return entry
    doc_data = {
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


def delete_favorite_db(fav_id: str) -> bool:
    if not db:
        global _LOCAL_FAVORITES
        _LOCAL_FAVORITES = [f for f in _LOCAL_FAVORITES if f.get("id") != fav_id]
        return True
    db.collection("favorites").document(fav_id).delete()
    return True


def get_weights_db(limit: int = 60) -> dict:
    entries = []
    if not db:
        entries = sorted(_LOCAL_WEIGHTS, key=lambda x: x["log_date"])
    else:
        try:
            docs = db.collection("weight_logs").order_by("log_date", direction=firestore.Query.DESCENDING).limit(limit).stream()
            for doc in docs:
                d = doc.to_dict()
                entries.append({
                    "id": doc.id,
                    "log_date": d.get("log_date", doc.id),
                    "weight_kg": round(float(d.get("weight_kg", 0.0)), 1),
                    "note": d.get("note", ""),
                })
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


def log_weight_db(weight_kg: float, log_date: Optional[str] = None, note: str = "") -> dict:
    d_str = log_date or str(date.today())
    w_val = round(float(weight_kg), 1)
    entry = {
        "id": d_str,
        "log_date": d_str,
        "weight_kg": w_val,
        "note": (note or "").strip(),
    }
    if not db:
        global _LOCAL_WEIGHTS
        _LOCAL_WEIGHTS = [w for w in _LOCAL_WEIGHTS if w.get("log_date") != d_str]
        _LOCAL_WEIGHTS.append(entry)
        return entry

    db.collection("weight_logs").document(d_str).set({
        "log_date": d_str,
        "weight_kg": w_val,
        "note": (note or "").strip(),
        "updated_at": firestore.SERVER_TIMESTAMP,
    }, merge=True)
    return entry


def delete_weight_db(entry_id: str) -> bool:
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


def save_push_subscription_db(endpoint: str, p256dh: str, auth: str, user_agent: str = "") -> dict:
    sub_id = _sub_doc_id(endpoint)
    record = {
        "id": sub_id,
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


def get_push_subscriptions_db() -> List[dict]:
    if not db:
        return list(_LOCAL_PUSH_SUBS)
    try:
        docs = db.collection("push_subscriptions").stream()
        subs = []
        for doc in docs:
            d = doc.to_dict()
            if d.get("endpoint") and d.get("keys"):
                subs.append({
                    "id": doc.id,
                    "endpoint": d["endpoint"],
                    "keys": d["keys"],
                    "user_agent": d.get("user_agent", ""),
                })
        return subs
    except Exception as e:
        print(f"ERROR get_push_subscriptions_db: {e}")
        return list(_LOCAL_PUSH_SUBS)


def send_web_push_to_all(title: str, body: str, tag: str = "nutriagent-push", url: str = "/") -> dict:
    subs = get_push_subscriptions_db()
    if not subs:
        return {"sent": 0, "failed": 0, "total_subscriptions": 0}

    vapid = get_or_create_vapid_keys()
    payload = json.dumps({
        "title": title,
        "body": body,
        "tag": tag,
        "url": url,
    }, ensure_ascii=False)

    sent = 0
    failed = 0
    for sub in subs:
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
            )
            sent += 1
        except WebPushException as ex:
            failed += 1
            status_code = getattr(getattr(ex, "response", None), "status_code", None)
            if status_code in (404, 410):
                remove_push_subscription_db(sub["endpoint"])
            else:
                print(f"WARN WebPushException ({status_code}): {ex}")
        except Exception as e:
            failed += 1
            print(f"ERROR send_web_push_to_all: {type(e).__name__} - {e}")

    return {"sent": sent, "failed": failed, "total_subscriptions": len(subs)}


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


@app.get("/api/summary")
def api_summary(target_date: Optional[str] = Query(default=None, alias="date")):
    return get_day_summary(target_date)


@app.get("/api/history")
def api_history(days: int = Query(default=14, ge=1, le=90)):
    today_date = date.today()
    start_date_str = str(today_date - timedelta(days=days - 1))

    all_meals = []
    if db:
        try:
            docs = db.collection("meals").order_by("created_at", direction=firestore.Query.DESCENDING).limit(300).stream()
            for doc in docs:
                data = doc.to_dict()
                all_meals.append({
                    "id": doc.id,
                    "description": data.get("description", ""),
                    "calories": int(data.get("calories", 0)),
                    "protein_g": round(float(data.get("protein_g", 0.0)), 1),
                    "log_date": data.get("log_date", ""),
                    "log_time": data.get("log_time", ""),
                    "source": data.get("source", "telegram"),
                })
        except Exception as e:
            print(f"ERROR api_history: {e}")
    else:
        all_meals = list(_LOCAL_MEALS)

    targets = get_targets()
    # Build daily trend for last 7 days
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


@app.get("/api/meals/")
def api_meals_legacy():
    """Backwards-compatible endpoint returning flat list of meals."""
    return api_history(days=30)["meals"]


@app.post("/api/meals/")
def api_add_manual_meal(req: ManualMealRequest):
    entry = log_meal_db(
        description=req.description,
        calories=req.calories,
        protein=req.protein_g,
        log_date=req.log_date,
        log_time=req.log_time,
        source="web",
    )
    if not entry:
        raise HTTPException(status_code=500, detail="Failed to log meal to Firestore")
    return entry


@app.post("/api/meals/ai")
def api_add_ai_meal(req: AIMealRequest):
    if not req.text or not req.text.strip():
        raise HTTPException(status_code=400, detail="נא להזין תיאור ארוחה")

    extraction = analyze_meal(req.text.strip())
    if not extraction.is_meal:
        raise HTTPException(status_code=422, detail="לא זוהתה ארוחה או שתייה בטקסט שהוזן")

    entry = log_meal_db(
        description=extraction.summary,
        calories=extraction.calories,
        protein=extraction.protein_g,
        log_date=req.log_date,
        source="web-ai",
    )
    if not entry:
        raise HTTPException(status_code=500, detail="Failed to save meal to Firestore")
    return entry


@app.post("/api/meals/photo")
def api_add_photo_meal(req: PhotoMealRequest):
    raw_b64 = (req.image_base64 or "").strip()
    if not raw_b64:
        raise HTTPException(status_code=400, detail="נא להעלות או לצלם תמונה")

    mime_type = req.mime_type or "image/jpeg"
    if raw_b64.startswith("data:"):
        header, _, raw_b64 = raw_b64.partition(",")
        if ";" in header and ":" in header:
            mime_type = header.split(":")[1].split(";")[0] or mime_type

    try:
        image_bytes = base64.b64decode(raw_b64)
    except Exception:
        raise HTTPException(status_code=400, detail="קובץ תמונה לא תקין")

    extraction = analyze_meal_image(image_bytes, mime_type=mime_type, caption=req.caption or "")
    if not extraction.is_meal:
        raise HTTPException(status_code=422, detail="לא זוהו אוכל, משקה או תווית מזון בתמונה")

    entry = log_meal_db(
        description=extraction.summary,
        calories=extraction.calories,
        protein=extraction.protein_g,
        log_date=req.log_date,
        source="web-photo",
    )
    if not entry:
        raise HTTPException(status_code=500, detail="Failed to save photo meal to Firestore")
    return entry


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
def api_get_favorites():
    return {"favorites": get_favorites_db()}


@app.post("/api/favorites")
def api_add_favorite(req: FavoriteMealRequest):
    if not req.label or not req.label.strip():
        raise HTTPException(status_code=400, detail="נא להזין שם לפריט המועדף")
    return add_favorite_db(req.label, req.calories, req.protein_g, req.icon or "⭐")


@app.delete("/api/favorites/{fav_id}")
def api_delete_favorite(fav_id: str):
    delete_favorite_db(fav_id)
    return {"status": "deleted", "id": fav_id}


@app.get("/api/weight")
def api_get_weight():
    return get_weights_db()


@app.post("/api/weight")
def api_log_weight(req: WeightLogRequest):
    return log_weight_db(req.weight_kg, req.log_date, req.note or "")


@app.delete("/api/weight/{entry_id}")
def api_delete_weight(entry_id: str):
    delete_weight_db(entry_id)
    return {"status": "deleted", "id": entry_id}


@app.put("/api/targets")
def api_update_targets(req: TargetUpdateRequest):
    return set_targets(req.target_calories, req.target_protein)


@app.get("/api/recipes")
def api_get_recipes():
    return {"recipes": CURATED_RECIPES}


@app.post("/api/recipes/suggest")
def api_suggest_recipes(req: RecipePromptRequest):
    suggestions = generate_ai_recipes(
        remaining_cal=req.remaining_calories,
        remaining_prot=req.remaining_protein,
        preference=req.preference or "",
    )
    if not suggestions:
        raise HTTPException(status_code=500, detail="לא ניתן היה לייצר מתכונים כרגע (בדוק מפתח Gemini)")
    return {"recipes": suggestions}


@app.get("/api/push/vapid-public-key")
def api_push_vapid_public_key():
    vapid = get_or_create_vapid_keys()
    subs = get_push_subscriptions_db()
    return {
        "public_key": vapid["public_key"],
        "subscribers_count": len(subs),
    }


@app.post("/api/push/subscribe")
def api_push_subscribe(req: PushSubscribeRequest):
    if not req.endpoint or not req.keys.p256dh or not req.keys.auth:
        raise HTTPException(status_code=400, detail="נתוני הרשמה להתראות חסרים")
    sub = save_push_subscription_db(
        endpoint=req.endpoint,
        p256dh=req.keys.p256dh,
        auth=req.keys.auth,
        user_agent=req.user_agent or "",
    )
    return {"status": "subscribed", "id": sub["id"]}


@app.post("/api/push/unsubscribe")
def api_push_unsubscribe(req: PushUnsubscribeRequest):
    if req.endpoint:
        remove_push_subscription_db(req.endpoint)
    return {"status": "unsubscribed"}


@app.post("/api/push/test")
def api_push_test(req: Optional[PushMessageRequest] = None):
    s = get_day_summary()
    title = (req.title if req and req.title else None) or "NutriAgent AI 🔔 התראת בדיקה!"
    body = (req.body if req and req.body else None) or (
        f"הפוש בנייד עובד מעולה! 🔥 היום: {s['total_calories']}/{s['target_calories']} קק\"ל "
        f"(נותרו {s['remaining_calories']}) | 💪 חלבון: {s['total_protein']:.0f}g"
    )
    res = send_web_push_to_all(title=title, body=body, tag="nutriagent-test")
    if res["total_subscriptions"] == 0:
        raise HTTPException(status_code=404, detail="לא נמצאו מכשירים רשומים להתראות. הפעל תחילה התראות במכשיר זה.")
    return res


@app.post("/api/push/remind")
def api_push_daily_reminder():
    """Can be triggered manually or via Cloud Scheduler to send a smart daily status push."""
    s = get_day_summary()
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
    return send_web_push_to_all(title=title, body=body, tag="nutriagent-reminder")


@app.post("/webhook")
async def telegram_webhook(request: Request):
    if not tg_app:
        return {"error": "BOT_TOKEN not configured"}
    req_data = await request.json()
    update = Update.de_json(req_data, tg_app.bot)
    await tg_app.process_update(update)
    return {"status": "ok"}