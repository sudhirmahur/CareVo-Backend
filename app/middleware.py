"""Security middleware: per-IP rate limiting and hardening headers."""
import time
from collections import defaultdict, deque

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app import config

# (path prefix, method or None) -> (max requests, window seconds). First match wins.
RULES = [
    ("/api/auth/login", "POST", 10, 60),
    ("/api/auth/google", "POST", 10, 60),
    ("/api/auth/register", "POST", 10, 3600),
    ("/api/auth/forgot-password", "POST", 5, 900),
    ("/api/auth/reset-password", "POST", 10, 900),
    ("/api/public/newsletter", "POST", 5, 3600),
    ("/api/media/upload", "POST", 30, 60),
    ("/api/", None, 600, 60),  # everything else, per IP
]


class RateLimiter:
    def __init__(self):
        self.hits: dict[str, deque] = defaultdict(deque)
        self.last_gc = time.time()

    def check(self, key: str, limit: int, window: int) -> int:
        """Return 0 if allowed, otherwise the seconds to wait."""
        t = time.time()
        q = self.hits[key]
        while q and q[0] <= t - window:
            q.popleft()
        if len(q) >= limit:
            return max(1, int(q[0] + window - t) + 1)
        q.append(t)
        if t - self.last_gc > 300:  # drop idle keys
            self.last_gc = t
            for k in [k for k, v in self.hits.items() if not v or v[-1] <= t - 3600]:
                self.hits.pop(k, None)
        return 0

    def reset(self):
        self.hits.clear()


limiter = RateLimiter()


def client_ip(request: Request) -> str:
    if config.TRUST_PROXY:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if config.RATE_LIMIT_ENABLED and path.startswith("/api/") and request.method != "OPTIONS":
            ip = client_ip(request)
            for prefix, method, limit, window in RULES:
                if path.startswith(prefix) and (method is None or method == request.method):
                    wait = limiter.check(f"{ip}|{prefix}|{method}", limit, window)
                    if wait:
                        return JSONResponse(
                            status_code=429,
                            content={"detail": f"Too many requests. Please wait {wait} seconds and try again."},
                            headers={"Retry-After": str(wait)},
                        )
                    break
        response = await call_next(request)
        h = response.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        h.setdefault("Cross-Origin-Resource-Policy", "cross-origin")
        if path.startswith("/api/"):
            h.setdefault("Cache-Control", "no-store")
            h.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        elif path.startswith("/uploads/"):
            # uploaded files can never run scripts, even if opened directly
            h["Content-Security-Policy"] = "default-src 'none'; img-src 'self'; media-src 'self'; style-src 'unsafe-inline'; sandbox"
        if config.IS_PRODUCTION or request.headers.get("x-forwarded-proto") == "https":
            h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response
