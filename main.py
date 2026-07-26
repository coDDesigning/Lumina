from fastapi import FastAPI
from routes import auth

app = FastAPI(
    title="Lumina API",
    description="Lumina AI Study Platform Backend API",
    version="1.0.0"
)

app.include_router(auth.router)

@app.get("/")
def read_root():
    return {"status": "ok", "message": "Lumina API Core is running!"}