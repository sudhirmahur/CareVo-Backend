from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.routes import auth
from app.routes import users
from app.routes import resumes
from app.routes import skills
from app.routes import applications


app = FastAPI(
    title="Carevo API",
    version="1.0.0"
)


# =========================
# CORS CONFIGURATION
# =========================

origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]


app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================
# STATIC FILES
# =========================

app.mount(
    "/uploads",
    StaticFiles(directory="uploads"),
    name="uploads"
)


# =========================
# ROUTES
# =========================

app.include_router(
    auth.router,
    prefix="/api/auth",
    tags=["Authentication"]
)

app.include_router(
    users.router,
    prefix="/api/users",
    tags=["Users"]
)

app.include_router(
    resumes.router,
    prefix="/api/resumes",
    tags=["Resumes"]
)

app.include_router(
    skills.router,
    prefix="/api/skills",
    tags=["Skills"]
)

app.include_router(
    applications.router,
    prefix="/api/applications",
    tags=["Applications"]
)


# =========================
# ROOT
# =========================

@app.get("/")
def home():
    return {
        "message": "🚀 Carevo Backend Server is Running Successfully! ❤️",
        "version": "1.0.0",
        "status": "running"
    }