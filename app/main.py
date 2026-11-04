from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import CORS_ORIGINS, IS_PRODUCTION, UPLOAD_DIR
from app.middleware import SecurityMiddleware
from app.database import ensure_indexes
from app.routes import (
    admin,
    admin_content,
    admin_learning,
    applications,
    auth,
    interviews,
    jobs,
    learning,
    media,
    messages,
    notifications,
    public,
    recruiter,
    recruiters,
    resumes,
    skills,
    social,
    users,
    videos,
)
from app.services.seed import run_startup_seed

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_indexes()
    run_startup_seed()
    yield


app = FastAPI(
    title="Carevo API",
    version="3.0.0",
    lifespan=lifespan,
    docs_url=None if IS_PRODUCTION else "/docs",
    redoc_url=None if IS_PRODUCTION else "/redoc",
    openapi_url=None if IS_PRODUCTION else "/openapi.json",
)
app.add_middleware(SecurityMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
)

class PublicUploads(StaticFiles):
    """Serves images/videos/audio only. Resumes are private and use signed links (/api/files/resume/...)."""

    async def get_response(self, path, scope):
        if path.replace("\\", "/").lstrip("/").startswith("resumes"):
            raise HTTPException(404)
        return await super().get_response(path, scope)


app.mount("/uploads", PublicUploads(directory=str(UPLOAD_DIR)), name="uploads")


@app.exception_handler(RequestValidationError)
async def validation_handler(_: Request, exc: RequestValidationError):
    """Return a short, human-readable `detail` string (the UI shows it as-is)."""
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"] if p not in ("body", "query", "path"))
        msg = err["msg"].replace("Value error, ", "")
        parts.append(f"{loc}: {msg}" if loc else msg)
    return JSONResponse(status_code=422, content={"detail": "; ".join(parts) or "Invalid request", "errors": jsonable(exc)})


def jsonable(exc: RequestValidationError):
    return [{"field": ".".join(str(p) for p in e["loc"]), "message": e["msg"]} for e in exc.errors()]


API = "/api"
app.include_router(auth.router, prefix=f"{API}/auth", tags=["Auth"])
app.include_router(users.router, prefix=f"{API}/users", tags=["Users"])
app.include_router(resumes.router, prefix=f"{API}/resumes", tags=["Resumes"])
app.include_router(skills.router, prefix=f"{API}/skills", tags=["Skills"])
app.include_router(jobs.router, prefix=f"{API}/jobs", tags=["Jobs"])
app.include_router(applications.router, prefix=f"{API}/applications", tags=["Applications"])
app.include_router(interviews.router, prefix=f"{API}/interviews", tags=["Interviews"])
app.include_router(notifications.router, prefix=f"{API}/notifications", tags=["Notifications"])
app.include_router(messages.router, prefix=f"{API}/messages", tags=["Messages"])
app.include_router(videos.router, prefix=f"{API}/videos", tags=["Videos"])
app.include_router(social.router, prefix=API, tags=["Companies / Follow / Reports"])
app.include_router(recruiter.router, prefix=f"{API}/recruiter", tags=["Recruiter"])
app.include_router(admin.router, prefix=f"{API}/admin", tags=["Admin"])


@app.get("/")
def home():
    return {"message": "Carevo API is running", "version": "2.0.0", "docs": "/docs"}


@app.get("/api/health")
def health():
    return {"status": "ok"}
app.include_router(media.router, prefix=f"{API}/media", tags=["Media"])
app.include_router(public.router, prefix=f"{API}/public", tags=["Public"])
app.include_router(recruiters.router, prefix=f"{API}/recruiters", tags=["Recruiters"])
app.include_router(admin_content.router, prefix=f"{API}/admin", tags=["Admin - content"])
app.include_router(learning.router, prefix=API, tags=["Learning"])
app.include_router(admin_learning.router, prefix=f"{API}/admin", tags=["Admin - learning"])
app.include_router(resumes.files_router, prefix=f"{API}/files", tags=["Files"])
