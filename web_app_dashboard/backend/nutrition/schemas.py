from pydantic import BaseModel, Field
from typing import List, Optional, Any
from datetime import date, datetime

class MealLogRequest(BaseModel):
    description: str = Field(..., description="תיאור תמציתי של המנה")
    calories: int = Field(..., description="הערכת קלוריות כוללת")
    protein_g: float = Field(..., description="הערכת חלבון כוללת")
    log_date: Optional[str] = Field(None, description="תאריך התיעוד (YYYY-MM-DD), ברירת מחדל להיום")
    log_time: Optional[str] = Field(None, description="שעת התיעוד (HH:MM), ברירת מחדל לעכשיו")

class MealEntry(BaseModel):
    id: str
    description: str
    calories: int
    protein_g: float
    log_date: str
    log_time: str
    created_at: Optional[datetime] = None

class DailyPlanMeal(BaseModel):
    name: str
    description: str
    calories: int
    protein_g: float
    ingredients: List[str]
    preparation_steps: List[str]

class DailyPlanRecommendation(BaseModel):
    total_calories_target: int = 1800
    plan: List[DailyPlanMeal]

class NutritionSummary(BaseModel):
    total_calories: float
    total_protein: float
    total_carbs: float
    total_fat: float
    average_calories_per_day: float

class NutritionTrend(BaseModel):
    date: str
    calories: float
    protein: float
    carbs: float
    fat: float

class NutritionItem(BaseModel):
    id: str
    name: str
    calories: float
    protein: float
    carbs: float
    fat: float
    quantity: float
    unit: str
    timestamp: str

class NutritionAgentConfig(BaseModel):
    setting1: str
    setting2: int
    is_enabled: bool
