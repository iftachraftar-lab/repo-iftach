
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from web_app_dashboard.backend.auth.auth import router as auth_router
from web_app_dashboard.backend.nutrition.nutrition import router as nutrition_router
from web_app_dashboard.backend.nutrition.config import router as config_router

app = FastAPI()

# Mount static files for the frontend
app.mount("/static", StaticFiles(directory="web_app_dashboard/frontend/assets"), name="static")

app.include_router(auth_router, prefix="/auth", tags=["auth"])
app.include_router(nutrition_router, prefix="/api/meals", tags=["meals"])
app.include_router(config_router, prefix="/nutrition-agent", tags=["nutrition-agent"])

@app.get("/")
async def serve_frontend():
    with open("web_app_dashboard/frontend/index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())
