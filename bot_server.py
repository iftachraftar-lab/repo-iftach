@app.get("/")
def serve_pwa():
    if os.path.exists("static/index.html"):
        return FileResponse("static/index.html")
    return {"status": "ok", "bot_configured": tg_app is not None}