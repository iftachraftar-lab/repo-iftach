from fastapi import APIRouter, Depends
from web_app_dashboard.backend.nutrition.schemas import NutritionAgentConfig

router = APIRouter()

# Placeholder for a dependency to get the current authenticated user (for demonstration)
async def get_current_user():
    # In a real application, this would decode a JWT and fetch user data
    return {"username": "testuser", "id": "123"}

# In-memory placeholder for configuration (in a real app, this would be persisted)
_nutrition_agent_config = NutritionAgentConfig(
    setting1="default_value",
    setting2=100,
    is_enabled=True
)

@router.get("/config", response_model=NutritionAgentConfig)
async def get_nutrition_agent_config(current_user: dict = Depends(get_current_user)):
    return _nutrition_agent_config

@router.put("/config", response_model=NutritionAgentConfig)
async def update_nutrition_agent_config(new_config: NutritionAgentConfig, current_user: dict = Depends(get_current_user)):
    global _nutrition_agent_config
    _nutrition_agent_config = new_config
    return _nutrition_agent_config
