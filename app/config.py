"""Application configuration (all values come from environment / .env)."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


# "mongomock://" runs a throw-away in-memory database (tests / quick local demo).
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DATABASE_NAME = os.getenv("DATABASE_NAME", "carevo")

JWT_SECRET = os.getenv("JWT_SECRET", "")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))

if not JWT_SECRET and IS_PRODUCTION:
    raise RuntimeError("JWT_SECRET must be set when ENV=production")
if not JWT_SECRET:
    # Never run with an empty/guessable secret. A random one is fine for dev
    # (tokens just stop working after a restart); set JWT_SECRET in production.
    import secrets

    JWT_SECRET = secrets.token_urlsafe(48)
    print("WARNING: JWT_SECRET not set - generated a temporary one. Set JWT_SECRET in .env")

CORS_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:4173,http://127.0.0.1:4173",
    ).split(",")
    if o.strip()
]

UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", str(BASE_DIR / "uploads")))
MAX_RESUME_MB = int(os.getenv("MAX_RESUME_MB", "5"))
MAX_VIDEO_MB = int(os.getenv("MAX_VIDEO_MB", "50"))
MAX_IMAGE_MB = int(os.getenv("MAX_IMAGE_MB", "8"))
MAX_AUDIO_MB = int(os.getenv("MAX_AUDIO_MB", "15"))

# ---- Cloudinary (images / audio / video). Without it files are stored in UPLOAD_DIR (dev only).
# Either CLOUDINARY_URL=cloudinary://<api_key>:<api_secret>@<cloud_name>  or the three variables below.
CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME", "").strip()
CLOUDINARY_API_KEY = os.getenv("CLOUDINARY_API_KEY", "").strip()
CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET", "").strip()
_curl = os.getenv("CLOUDINARY_URL", "").strip()
if _curl.startswith("cloudinary://") and "@" in _curl:
    _cred, _, _cloud = _curl[len("cloudinary://"):].partition("@")
    _key, _, _secret = _cred.partition(":")
    CLOUDINARY_CLOUD_NAME = CLOUDINARY_CLOUD_NAME or _cloud.strip("/")
    CLOUDINARY_API_KEY = CLOUDINARY_API_KEY or _key
    CLOUDINARY_API_SECRET = CLOUDINARY_API_SECRET or _secret
CLOUDINARY_API_BASE = os.getenv("CLOUDINARY_API_BASE", "https://api.cloudinary.com/v1_1").rstrip("/")
CLOUDINARY_FOLDER = os.getenv("CLOUDINARY_FOLDER", "carevo").strip("/")

# ---- Optional "Continue with Google" (public OAuth client id, not a secret)
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_TOKENINFO_URL = os.getenv("GOOGLE_TOKENINFO_URL", "https://oauth2.googleapis.com/tokeninfo")

# ---- Security
ENV = os.getenv("ENV", "development").strip().lower()  # set ENV=production when deployed
IS_PRODUCTION = ENV == "production"
RATE_LIMIT_ENABLED = _bool("RATE_LIMIT_ENABLED", True)
TRUST_PROXY = _bool("TRUST_PROXY", False)  # honour X-Forwarded-For (only behind your own proxy / load balancer)
MAX_FAILED_LOGINS = int(os.getenv("MAX_FAILED_LOGINS", "5"))
LOCKOUT_MINUTES = int(os.getenv("LOCKOUT_MINUTES", "15"))

# First admin account is created on startup when both are set.
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "").strip().lower()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
ADMIN_NAME = os.getenv("ADMIN_NAME", "Carevo Admin")

# Until an e-mail provider is wired in, the reset token can be returned by the API (dev only).
EXPOSE_RESET_TOKEN = _bool("EXPOSE_RESET_TOKEN", False)
RESET_TOKEN_MINUTES = int(os.getenv("RESET_TOKEN_MINUTES", "30"))

if IS_PRODUCTION:
    if len(JWT_SECRET) < 32:
        raise RuntimeError("JWT_SECRET must be at least 32 characters in production")
    if EXPOSE_RESET_TOKEN:
        raise RuntimeError("EXPOSE_RESET_TOKEN must be false in production")
    if "*" in CORS_ORIGINS:
        raise RuntimeError("CORS_ORIGINS must list your real frontend origin(s) in production")
