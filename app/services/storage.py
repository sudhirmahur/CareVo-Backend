"""Media storage: Cloudinary (production) with a local-disk fallback (development).

* Files are validated by their *content* (magic bytes), never by file name or the browser's MIME type.
* SVG/HTML are rejected on purpose (script injection).
* Cloudinary is called over its signed REST API with the standard library only (no extra dependency).
  Audio is uploaded as Cloudinary resource_type "video" (that is how Cloudinary stores audio).
"""
import hashlib
import json
import mimetypes
import os
import re
import secrets
import time
import urllib.error
import urllib.request
import uuid
from typing import Optional

from fastapi import HTTPException

from app import config
from app.database import media_collection
from app.utils import now

LIMITS_MB = {"image": config.MAX_IMAGE_MB, "video": config.MAX_VIDEO_MB, "audio": config.MAX_AUDIO_MB}
EXT_BY_FORMAT = {
    "jpeg": "jpg", "png": "png", "gif": "gif", "webp": "webp",
    "mp4": "mp4", "webm": "webm", "mov": "mov",
    "mp3": "mp3", "wav": "wav", "ogg": "ogg", "m4a": "m4a", "aac": "aac",
}


def cloudinary_enabled() -> bool:
    return bool(config.CLOUDINARY_CLOUD_NAME and config.CLOUDINARY_API_KEY and config.CLOUDINARY_API_SECRET)


def provider() -> str:
    return "cloudinary" if cloudinary_enabled() else "local"


def detect(content: bytes) -> Optional[tuple[str, str]]:
    """Return (kind, format) from magic bytes, or None when the type is not allowed."""
    h = content[:16]
    if h.startswith(b"\xff\xd8\xff"):
        return "image", "jpeg"
    if h.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image", "png"
    if h.startswith((b"GIF87a", b"GIF89a")):
        return "image", "gif"
    if h[:4] == b"RIFF" and h[8:12] == b"WEBP":
        return "image", "webp"
    if h[:4] == b"RIFF" and h[8:12] == b"WAVE":
        return "audio", "wav"
    if h.startswith(b"OggS"):
        return "audio", "ogg"
    if h.startswith(b"ID3") or h[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "audio", "mp3"
    if h[:2] in (b"\xff\xf1", b"\xff\xf9"):
        return "audio", "aac"
    if h[4:8] == b"ftyp":
        brand = h[8:12]
        if brand in (b"M4A ", b"M4B ", b"M4P "):
            return "audio", "m4a"
        if brand == b"qt  ":
            return "video", "mov"
        return "video", "mp4"
    if h.startswith(b"\x1a\x45\xdf\xa3"):
        return "video", "webm"  # webm may also hold audio only; treated as video (plays in <video> and <audio>)
    return None


def validate(content: bytes, expected: Optional[str] = None) -> tuple[str, str]:
    if not content:
        raise HTTPException(400, "The file is empty")
    found = detect(content)
    if not found:
        raise HTTPException(400, "Unsupported file. Allowed: JPG, PNG, GIF, WebP images; MP4, WebM, MOV video; MP3, WAV, OGG, M4A audio")
    kind, fmt = found
    if expected == "audio" and kind == "video" and fmt == "webm":
        kind = "audio"
    if expected and expected != kind:
        raise HTTPException(400, f"Expected a {expected} file but got {kind}")
    limit = LIMITS_MB[kind]
    if len(content) > limit * 1024 * 1024:
        raise HTTPException(400, f"{kind.capitalize()} is too large (max {limit} MB)")
    return kind, fmt


def _sign(params: dict) -> str:
    to_sign = "&".join(f"{k}={params[k]}" for k in sorted(params) if params[k] not in (None, ""))
    return hashlib.sha1((to_sign + config.CLOUDINARY_API_SECRET).encode()).hexdigest()


def _multipart(fields: dict, file_field: str, filename: str, content: bytes, mime: str) -> tuple[bytes, str]:
    boundary = "----carevo" + secrets.token_hex(12)
    out = bytearray()
    for k, v in fields.items():
        out += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    out += f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n'.encode()
    out += content + f"\r\n--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def _post(url: str, body: bytes, content_type: str, timeout: int = 120) -> dict:
    req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": content_type})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https endpoint from config
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read().decode()).get("error", {}).get("message", "")
        except Exception:  # pragma: no cover
            pass
        raise HTTPException(502, f"Media storage rejected the upload{': ' + detail if detail else ''}")
    except Exception:
        raise HTTPException(502, "Media storage is unreachable. Please try again.")


def _resource_type(kind: str) -> str:
    return "image" if kind == "image" else "video"  # Cloudinary keeps audio under "video"


def store(content: bytes, kind: str, fmt: str, purpose: str, owner_id) -> dict:
    """Persist already-validated bytes. Returns {url, public_id, provider, kind, bytes}."""
    ext = EXT_BY_FORMAT.get(fmt, fmt)
    if cloudinary_enabled():
        folder = f"{config.CLOUDINARY_FOLDER}/{purpose}"
        ts = str(int(time.time()))
        params = {"folder": folder, "timestamp": ts}
        fields = {**params, "api_key": config.CLOUDINARY_API_KEY, "signature": _sign(params)}
        mime = mimetypes.types_map.get("." + ext, "application/octet-stream")
        body, ctype = _multipart(fields, "file", f"upload.{ext}", content, mime)
        data = _post(f"{config.CLOUDINARY_API_BASE}/{config.CLOUDINARY_CLOUD_NAME}/{_resource_type(kind)}/upload", body, ctype)
        url = data.get("secure_url")
        if not url or not str(url).startswith("https://"):
            raise HTTPException(502, "Media storage returned an unexpected response")
        result = {"url": url, "public_id": data.get("public_id"), "provider": "cloudinary"}
    else:
        directory = config.UPLOAD_DIR / "media" / kind
        directory.mkdir(parents=True, exist_ok=True)
        name = f"{uuid.uuid4().hex}.{ext}"
        (directory / name).write_bytes(content)
        result = {"url": f"/uploads/media/{kind}/{name}", "public_id": None, "provider": "local"}
    result.update(kind=kind, bytes=len(content))
    media_collection.insert_one({**result, "purpose": purpose, "owner_id": owner_id, "created_at": now()})
    return result


def delete_by_url(url: Optional[str]) -> None:
    """Best-effort removal of a file we stored earlier (never raises)."""
    if not url:
        return
    try:
        rec = media_collection.find_one({"url": url})
        if not rec:
            return
        if rec.get("provider") == "cloudinary" and cloudinary_enabled() and rec.get("public_id"):
            ts = str(int(time.time()))
            params = {"public_id": rec["public_id"], "timestamp": ts}
            fields = {**params, "api_key": config.CLOUDINARY_API_KEY, "signature": _sign(params)}
            body = "&".join(f"{k}={urllib.request.quote(str(v), safe='')}" for k, v in fields.items()).encode()
            _post(f"{config.CLOUDINARY_API_BASE}/{config.CLOUDINARY_CLOUD_NAME}/{_resource_type(rec['kind'])}/destroy", body, "application/x-www-form-urlencoded", 30)
        elif rec.get("provider") == "local" and url.startswith("/uploads/media/"):
            rel = re.sub(r"^/uploads/", "", url)
            path = (config.UPLOAD_DIR / rel).resolve()
            if str(path).startswith(str(config.UPLOAD_DIR.resolve())) and path.is_file():
                os.remove(path)
        media_collection.delete_one({"_id": rec["_id"]})
    except Exception:  # pragma: no cover - cleanup must never break the request
        pass


# ======================================================================== private files
# Resumes, vault documents and file answers are personal data. They are never public:
#   * Cloudinary: uploaded as resource_type "raw" with type "authenticated" (no public URL exists); the API
#     downloads them with a signed request only after our own signed, short-lived link has been checked.
#   * Local disk (development): stored under UPLOAD_DIR/<folder>, which the public /uploads mount refuses to serve.
PRIVATE_MAX_BYTES = 25 * 1024 * 1024


def _get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https endpoint from config
            data = resp.read(PRIVATE_MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        raise HTTPException(404 if e.code == 404 else 502, "File not found" if e.code == 404 else "Media storage rejected the request")
    except Exception:
        raise HTTPException(502, "Media storage is unreachable. Please try again.")
    if len(data) > PRIVATE_MAX_BYTES:
        raise HTTPException(502, "Stored file is too large")
    return data


def store_private(content: bytes, ext: str, folder: str) -> dict:
    """Persist bytes privately. Returns {"provider": "cloudinary"|"local", "ref": <public_id or relative path>}."""
    ext = ext.lower().lstrip(".")
    if cloudinary_enabled():
        public_id = f"{config.CLOUDINARY_FOLDER}/{folder}/{uuid.uuid4().hex}.{ext}"
        params = {"public_id": public_id, "timestamp": str(int(time.time())), "type": "authenticated"}
        fields = {**params, "api_key": config.CLOUDINARY_API_KEY, "signature": _sign(params)}
        body, ctype = _multipart(fields, "file", f"file.{ext}", content, "application/octet-stream")
        data = _post(f"{config.CLOUDINARY_API_BASE}/{config.CLOUDINARY_CLOUD_NAME}/raw/upload", body, ctype)
        ref = data.get("public_id")
        if not ref:
            raise HTTPException(502, "Media storage returned an unexpected response")
        return {"provider": "cloudinary", "ref": ref}
    directory = config.UPLOAD_DIR / folder
    directory.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.{ext}"
    (directory / name).write_bytes(content)
    return {"provider": "local", "ref": f"{folder}/{name}"}


def read_private(provider: Optional[str], ref: Optional[str]) -> bytes:
    if not ref:
        raise HTTPException(404, "File not found")
    if provider == "cloudinary":
        if not cloudinary_enabled():
            raise HTTPException(503, "Media storage is not configured")
        params = {"public_id": ref, "timestamp": str(int(time.time())), "type": "authenticated"}
        q = {**params, "api_key": config.CLOUDINARY_API_KEY, "signature": _sign(params)}
        qs = "&".join(f"{k}={urllib.request.quote(str(v), safe='')}" for k, v in q.items())
        return _get(f"{config.CLOUDINARY_API_BASE}/{config.CLOUDINARY_CLOUD_NAME}/raw/download?{qs}")
    path = (config.UPLOAD_DIR / ref).resolve()
    if not str(path).startswith(str(config.UPLOAD_DIR.resolve())) or not path.is_file():
        raise HTTPException(404, "File not found")
    return path.read_bytes()


def delete_private(provider: Optional[str], ref: Optional[str]) -> None:
    """Best-effort removal (never raises)."""
    if not ref:
        return
    try:
        if provider == "cloudinary" and cloudinary_enabled():
            params = {"public_id": ref, "timestamp": str(int(time.time())), "type": "authenticated"}
            fields = {**params, "api_key": config.CLOUDINARY_API_KEY, "signature": _sign(params)}
            body = "&".join(f"{k}={urllib.request.quote(str(v), safe='')}" for k, v in fields.items()).encode()
            _post(f"{config.CLOUDINARY_API_BASE}/{config.CLOUDINARY_CLOUD_NAME}/raw/destroy", body, "application/x-www-form-urlencoded", 30)
        elif provider in (None, "local"):
            path = (config.UPLOAD_DIR / ref).resolve()
            if str(path).startswith(str(config.UPLOAD_DIR.resolve())) and path.is_file():
                os.remove(path)
    except Exception:  # pragma: no cover
        pass


# ======================================================================== video delivery
_CLD_VIDEO = re.compile(r"^(https://res\.cloudinary\.com/[^/]+/video/upload/)(v\d+/.+?)(\.[A-Za-z0-9]+)?$")


def playback_url(url: Optional[str]) -> Optional[str]:
    """URL the <video> element should use.

    Cloudinary originals are often HEVC .mov (iPhone) or .webm, which Chrome/Firefox/Android cannot all play.
    Asking Cloudinary for f_mp4 + H.264 + auto quality makes every browser able to play the same upload.
    """
    m = _CLD_VIDEO.match(url or "")
    if not m:
        return url
    return f"{m.group(1)}f_mp4,vc_h264,ac_aac,q_auto/{m.group(2)}.mp4"


def poster_url(url: Optional[str]) -> Optional[str]:
    """First-frame poster for a Cloudinary video (so_0), None for other hosts."""
    m = _CLD_VIDEO.match(url or "")
    if not m:
        return None
    return f"{m.group(1)}so_0,f_jpg,w_720,q_auto/{m.group(2)}.jpg"
