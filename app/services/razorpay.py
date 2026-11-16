"""Minimal Razorpay REST client (orders, refunds) + signature helpers. No SDK needed."""
import base64
import hashlib
import hmac
import json
import urllib.error
import urllib.request

from fastapi import HTTPException

from app import config


def configured() -> bool:
    return bool(config.RAZORPAY_KEY_ID and config.RAZORPAY_KEY_SECRET)


def request(method: str, path: str, body: dict | None = None) -> dict:
    """Call the Razorpay API. Tests monkeypatch this function."""
    if not configured():
        raise HTTPException(503, "Online payments are not configured yet")
    token = base64.b64encode(f"{config.RAZORPAY_KEY_ID}:{config.RAZORPAY_KEY_SECRET}".encode()).decode()
    req = urllib.request.Request(
        config.RAZORPAY_API_BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310 - fixed https base from config
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error", {}).get("description")
        except Exception:
            msg = None
        raise HTTPException(502, f"Payment provider error: {msg or e.code}")
    except Exception:
        raise HTTPException(502, "Could not reach the payment provider. Please try again.")


def _hmac(secret: str, message: bytes) -> str:
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def checkout_signature(order_id: str, payment_id: str) -> str:
    return _hmac(config.RAZORPAY_KEY_SECRET, f"{order_id}|{payment_id}".encode())


def valid_checkout_signature(order_id: str, payment_id: str, signature: str) -> bool:
    if not configured():
        return False
    return hmac.compare_digest(checkout_signature(order_id, payment_id), signature or "")


def valid_webhook_signature(raw_body: bytes, signature: str) -> bool:
    if not config.RAZORPAY_WEBHOOK_SECRET:
        return False
    return hmac.compare_digest(_hmac(config.RAZORPAY_WEBHOOK_SECRET, raw_body), signature or "")
