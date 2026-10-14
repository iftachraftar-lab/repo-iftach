from fastapi import APIRouter, Depends, HTTPException, status
from typing import List, Optional
from datetime import date, datetime
import os

from google.cloud import firestore

from web_app_dashboard.backend.nutrition.schemas import (
    NutritionSummary, 
    NutritionTrend, 
    NutritionItem,
    MealLogRequest,
    MealEntry,
    DailyPlanMeal,
    DailyPlanRecommendation
)

router = APIRouter()

PROJECT_ID = os.getenv("PROJECT_ID", "august-balancer-488508-k9")

try:
    db = firestore.Client(project=PROJECT_ID, database="(default)")
    print(f"Firestore initialized in nutrition.py for project: {PROJECT_ID}")
except Exception as e:
    print(f"CRITICAL: Firestore initialization failed in nutrition.py: {e}")
    db = None

async def get_current_user():
    # Placeholder for a dependency to get the current authenticated user
    # In a real application, this would decode a JWT and fetch user data
    return {"username": "testuser", "id": "user123"}

@router.get("/", response_model=List[MealEntry])
async def get_meal_history(current_user: dict = Depends(get_current_user)):
    if not db:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    meals_list = []
    try:
        meals_ref = db.collection("meals").order_by("created_at", direction=firestore.Query.DESCENDING).stream()
        for doc in meals_ref:
            data = doc.to_dict()
            meals_list.append(
                MealEntry(
                    id=doc.id,
                    description=data.get("description", ""),
                    calories=data.get("calories", 0),
                    protein_g=data.get("protein_g", 0.0),
                    log_date=data.get("log_date", ""),
                    log_time=data.get("log_time", ""),
                    created_at=data.get("created_at")
                )
            )
    except Exception as e:
        print(f"ERROR get_meal_history: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch meal history: {e}")
    
    return meals_list

@router.post("/", response_model=MealEntry, status_code=status.HTTP_201_CREATED)
async def log_meal(meal_request: MealLogRequest, current_user: dict = Depends(get_current_user)):
    if not db:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    try:
        log_date = meal_request.log_date if meal_request.log_date else str(date.today())
        log_time = meal_request.log_time if meal_request.log_time else datetime.now().strftime("%H:%M")

        meal_data = {
            "description": meal_request.description,
            "calories": meal_request.calories,
            "protein_g": meal_request.protein_g,
            "log_date": log_date,
            "log_time": log_time,
            "created_at": firestore.SERVER_TIMESTAMP
        }
        _, doc_ref = db.collection("meals").add(meal_data)
        
        # Fetch the created document to get the server-generated timestamp and ID
        created_doc = doc_ref.get()
        if created_doc.exists:
            data = created_doc.to_dict()
            return MealEntry(
                id=created_doc.id,
                description=data.get("description", ""),
                calories=data.get("calories", 0),
                protein_g=data.get("protein_g", 0.0),
                log_date=data.get("log_date", ""),
                log_time=data.get("log_time", ""),
                created_at=data.get("created_at")
            )
        raise HTTPException(status_code=500, detail="Failed to retrieve created meal entry")

    except Exception as e:
        print(f"ERROR log_meal: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to log meal: {e}")

@router.delete("/{meal_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_meal(meal_id: str, current_user: dict = Depends(get_current_user)):
    if not db:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    try:
        meal_ref = db.collection("meals").document(meal_id)
        doc = meal_ref.get()
        if not doc.exists:
            raise HTTPException(status_code=404, detail="Meal not found")
        
        meal_ref.delete()
        return
    except Exception as e:
        print(f"ERROR delete_meal: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete meal: {e}")

@router.get("/recommendations/daily-plan", response_model=DailyPlanRecommendation)
async def get_daily_plan_recommendations(current_user: dict = Depends(get_current_user)):
    # Static 1800 kcal daily plan tailored for Israeli products
    daily_plan = [
        DailyPlanMeal(
            name="ארוחת בוקר",
            description="גביע קוטג\' (250 גרם, 5%), 2 ביצים קשות, 2 פרוסות לחם מלא קל, ירקות חתוכים (מלפפון, עגבנייה, פלפל).",
            calories=400,
            protein_g=35.0,
            ingredients=[
                "גביע קוטג\' 5% (250 גרם)",
                "2 ביצים קשות",
                "2 פרוסות לחם מלא קל",
                "ירקות חתוכים (מלפפון, עגבנייה, פלפל)"
            ],
            preparation_steps=[
                "הרתיחו ביצים למשך 7-10 דקות לקבלת ביצים קשות.",
                "חתכו ירקות והגישו עם הקוטג\' והלחם."
            ]
        ),
        DailyPlanMeal(
            name="ארוחת צהריים",
            description="150 גרם חזה עוף בגריל/אפוי, כוס קינואה/בורגול מבושל, סלט גדול עם שמן זית ולימון (חסה, עגבנייה, מלפפון, בצל, פלפל).",
            calories=600,
            protein_g=50.0,
            ingredients=[
                "150 גרם חזה עוף (ללא עור ועצמות)",
                "1 כוס קינואה/בורגול (מבושל)",
                "ירקות לסלט (חסה, עגבנייה, מלפפון, בצל, פלפל)",
                "1 כף שמן זית",
                "מיץ מלימון טרי"
            ],
            preparation_steps=[
                "תיבלו את חזה העוף וצלו על גריל או אפו בתנור עד למוכנות.",
                "בשלו קינואה/בורגול לפי הוראות היצרן.",
                "ערבבו את מרכיבי הסלט ותבלו בשמן זית ולימון. הגישו הכל יחד."
            ]
        ),
        DailyPlanMeal(
            name="נשנוש ביניים",
            description="יוגורט טבעי (150 גרם, 3%) עם כף גרנולה לייט וחצי בננה.",
            calories=250,
            protein_g=15.0,
            ingredients=[
                "1 גביע יוגורט טבעי 3% (150 גרם)",
                "1 כף גרנולה לייט",
                "חצי בננה"
            ],
            preparation_steps=[
                "ערבבו את היוגורט עם הגרנולה והבננה הפרוסה. הגישו מיד."
            ]
        ),
        DailyPlanMeal(
            name="ארוחת ערב",
            description="סלט טונה גדול: קופסת טונה במים (מסוננת), ירקות חתוכים (חסה, מלפפון, עגבנייה), כף מיונז קל, 2 קרקרים מחיטה מלאה.",
            calories=550,
            protein_g=40.0,
            ingredients=[
                "1 קופסת טונה במים (מסוננת)",
                "ירקות חתוכים (חסה, מלפפון, עגבנייה)",
                "1 כף מיונז קל",
                "2 קרקרים מחיטה מלאה"
            ],
            preparation_steps=[
                "ערבבו את הטונה המסוננת עם הירקות החתוכים והמיונז הקל.",
                "הגישו עם הקרקרים מחיטה מלאה."
            ]
        )
    ]

    return DailyPlanRecommendation(plan=daily_plan)

# Existing endpoints (removed or modified based on needs)
# @router.get("/analytics/summary", response_model=NutritionSummary)
# async def get_nutrition_summary(current_user: dict = Depends(get_current_user)):
#     return NutritionSummary(
#         total_calories=2000.5,
#         total_protein=120.0,
#         total_carbs=250.0,
#         total_fat=70.0,
#         average_calories_per_day=1800.0
#     )

# @router.get("/analytics/trends", response_model=List[NutritionTrend])
# async def get_nutrition_trends(current_user: dict = Depends(get_current_user)):
#     return [
#         NutritionTrend(date="2026-10-01", calories=1800.0, protein=100.0, carbs=200.0, fat=60.0),
#         NutritionTrend(date="2026-10-02", calories=2100.0, protein=130.0, carbs=280.0, fat=80.0),
#     ]

# @router.get("/data/{item_id}", response_model=NutritionItem)
# async def get_nutrition_item(item_id: str, current_user: dict = Depends(get_current_user)):
#     if item_id == "item1":
#         return NutritionItem(
#             id="item1",
#             name="Apple",
#             calories=95.0,
#             protein=0.5,
#             carbs=25.0,
#             fat=0.3,
#             quantity=1.0,
#             unit="piece",
#             timestamp="2026-10-14T10:00:00Z"
#         )
#     raise HTTPException(status_code=404, detail="Nutrition item not found")

# @router.post("/data", response_model=NutritionItem, status_code=status.HTTP_201_CREATED)
# async def add_nutrition_data(item: NutritionItem, current_user: dict = Depends(get_current_user)):
#     return item
