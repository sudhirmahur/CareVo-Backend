import hashlib
import hmac
import json

import pytest

from app import config
from app.services import razorpay as rzp

SECRET, WH = "test_secret_value_123", "whsec_test_123"


@pytest.fixture(autouse=True)
def razor(monkeypatch):
    monkeypatch.setattr(config, "RAZORPAY_KEY_ID", "rzp_test_abc")
    monkeypatch.setattr(config, "RAZORPAY_KEY_SECRET", SECRET)
    monkeypatch.setattr(config, "RAZORPAY_WEBHOOK_SECRET", WH)
    calls = []

    def fake(method, path, body=None):
        calls.append((method, path, body))
        if path == "/orders":
            return {"id": f"order_{len(calls)}x{body['amount']}", "amount": body["amount"], "currency": "INR"}
        if path.endswith("/refund"):
            return {"id": "rfnd_1", "amount": body["amount"]}
        raise AssertionError(path)

    monkeypatch.setattr(rzp, "request", fake)
    return calls


def sig(order, pay):
    return hmac.new(SECRET.encode(), f"{order}|{pay}".encode(), hashlib.sha256).hexdigest()


def plan(admin, **kw):
    body = {"name": "Premium Monthly", "price": 499, "duration_days": 30, "features": ["Apply from Reels", " ", "Recruiter profiles"]}
    body.update(kw)
    r = admin.post("/api/admin/plans", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def buy(admin, u, **kw):
    p = plan(admin, **kw)
    o = u.post("/api/payments/order", json={"plan_id": p["id"]}).json()
    return p, o


def test_plans_admin_and_public(admin, make_actor, client):
    p = plan(admin)
    assert p["price_paise"] == 49900 and p["features"] == ["Apply from Reels", "Recruiter profiles"]
    assert admin.post("/api/admin/plans", json={"name": "x", "price": 0, "duration_days": 30}).status_code == 422
    assert admin.post("/api/admin/plans", json={"name": "Bad", "price": 10, "duration_days": 0}).status_code == 422
    assert make_actor("user").post("/api/admin/plans", json={"name": "Hack", "price": 1, "duration_days": 1}).status_code == 403
    pub = client.get("/api/plans").json()
    assert pub["payments_enabled"] is True and pub["key_id"] == "rzp_test_abc" and len(pub["items"]) == 1
    assert "key_secret" not in json.dumps(pub)
    admin.put(f"/api/admin/plans/{p['id']}", json={"price": 599, "is_active": False})
    assert client.get("/api/plans").json()["items"] == []
    assert admin.delete(f"/api/admin/plans/{p['id']}").json()["deactivated"] is False


def test_order_amount_comes_from_plan_not_client(admin, make_actor, razor):
    u = make_actor("user")
    p = plan(admin)
    r = u.post("/api/payments/order", json={"plan_id": p["id"], "amount": 1})
    assert r.status_code == 201 and r.json()["amount"] == 49900
    assert razor[0][2]["amount"] == 49900
    assert make_actor("recruiter").post("/api/payments/order", json={"plan_id": p["id"]}).status_code == 400
    assert u.post("/api/payments/order", json={"plan_id": "bad"}).status_code == 400
    admin.put(f"/api/admin/plans/{p['id']}", json={"is_active": False})
    assert u.post("/api/payments/order", json={"plan_id": p["id"]}).status_code == 404


def test_verify_activates_premium_once_and_rejects_forgery(admin, make_actor):
    u, other = make_actor("user"), make_actor("user")
    _, o = buy(admin, u)
    bad = u.post("/api/payments/verify", json={"razorpay_order_id": o["order_id"], "razorpay_payment_id": "pay_1", "razorpay_signature": "0" * 64})
    assert bad.status_code == 400
    assert u.get("/api/users/me/premium").json()["is_premium"] is False
    # someone else cannot verify my order
    assert other.post("/api/payments/verify", json={"razorpay_order_id": o["order_id"], "razorpay_payment_id": "pay_1", "razorpay_signature": sig(o["order_id"], "pay_1")}).status_code == 404
    # after a bad attempt the order is marked failed, but a genuine signature still works (retry)
    good = u.post("/api/payments/verify", json={"razorpay_order_id": o["order_id"], "razorpay_payment_id": "pay_1", "razorpay_signature": sig(o["order_id"], "pay_1")})
    assert good.status_code == 200 and good.json()["is_premium"] is True
    until = u.get("/api/users/me/premium").json()["premium_until"]
    again = u.post("/api/payments/verify", json={"razorpay_order_id": o["order_id"], "razorpay_payment_id": "pay_1", "razorpay_signature": sig(o["order_id"], "pay_1")})
    assert again.status_code == 200
    assert u.get("/api/users/me/premium").json()["premium_until"] == until  # no double extension
    subs = admin.get("/api/admin/subscriptions?state=active").json()
    assert subs["total"] == 1
    assert any("Premium activated" in n["title"] for n in u.get("/api/notifications").json()["items"])


def test_renewal_extends_remaining_time(admin, make_actor):
    u = make_actor("user")
    for i in (1, 2):
        _, o = buy(admin, u, name=f"P{i}")
        assert u.post("/api/payments/verify", json={"razorpay_order_id": o["order_id"], "razorpay_payment_id": f"pay_{i}", "razorpay_signature": sig(o["order_id"], f"pay_{i}")}).status_code == 200
    from datetime import datetime, timezone
    until = datetime.fromisoformat(u.get("/api/users/me/premium").json()["premium_until"])
    days = (until - datetime.now(timezone.utc)).days
    assert 58 <= days <= 60


def test_webhook_signature_and_idempotency(admin, make_actor, client):
    u = make_actor("user")
    _, o = buy(admin, u)
    ev = {"event": "payment.captured", "payload": {"payment": {"entity": {"id": "pay_w1", "order_id": o["order_id"], "amount": o["amount"]}}}}
    raw = json.dumps(ev).encode()
    good = hmac.new(WH.encode(), raw, hashlib.sha256).hexdigest()
    assert client.post("/api/payments/webhook", content=raw, headers={"x-razorpay-signature": "bad"}).status_code == 400
    assert client.post("/api/payments/webhook", content=raw).status_code == 400
    assert client.post("/api/payments/webhook", content=raw, headers={"x-razorpay-signature": good}).status_code == 200
    assert u.get("/api/users/me/premium").json()["is_premium"] is True
    until = u.get("/api/users/me/premium").json()["premium_until"]
    assert client.post("/api/payments/webhook", content=raw, headers={"x-razorpay-signature": good}).status_code == 200
    assert u.get("/api/users/me/premium").json()["premium_until"] == until
    # wrong amount must not activate
    u2 = make_actor("user")
    _, o2 = buy(admin, u2, name="Other")
    ev2 = {"event": "payment.captured", "payload": {"payment": {"entity": {"id": "pay_w2", "order_id": o2["order_id"], "amount": 100}}}}
    raw2 = json.dumps(ev2).encode()
    client.post("/api/payments/webhook", content=raw2, headers={"x-razorpay-signature": hmac.new(WH.encode(), raw2, hashlib.sha256).hexdigest()})
    assert u2.get("/api/users/me/premium").json()["is_premium"] is False


def test_failed_event_marks_payment_failed(admin, make_actor, client):
    u = make_actor("user")
    _, o = buy(admin, u)
    ev = {"event": "payment.failed", "payload": {"payment": {"entity": {"order_id": o["order_id"], "error_description": "Card declined"}}}}
    raw = json.dumps(ev).encode()
    client.post("/api/payments/webhook", content=raw, headers={"x-razorpay-signature": hmac.new(WH.encode(), raw, hashlib.sha256).hexdigest()})
    assert admin.get("/api/admin/payments?status=failed").json()["total"] == 1


def test_unconfigured_returns_503(admin, make_actor, monkeypatch):
    p = plan(admin)
    monkeypatch.undo()  # drop the fake provider and the test keys -> real code path
    assert not rzp.configured()
    assert make_actor("user").post("/api/payments/order", json={"plan_id": p["id"]}).status_code == 503


def test_admin_refund_revokes_premium_and_reports(admin, make_actor):
    u = make_actor("user")
    _, o = buy(admin, u)
    u.post("/api/payments/verify", json={"razorpay_order_id": o["order_id"], "razorpay_payment_id": "pay_r", "razorpay_signature": sig(o["order_id"], "pay_r")})
    rows = admin.get("/api/admin/payments").json()
    assert rows["total"] == 1 and rows["items"][0]["status"] == "paid" and rows["items"][0]["user"]["email"] == u.user["email"]
    s = admin.get("/api/admin/payments/summary").json()
    assert s["revenue"] == 499 and s["paid_count"] == 1 and s["active_subscriptions"] == 1 and len(s["series"]) == 30
    assert sum(x["revenue"] for x in s["series"]) == 499
    pid = rows["items"][0]["id"]
    assert u.post(f"/api/admin/payments/{pid}/refund", json={}).status_code == 403
    r = admin.post(f"/api/admin/payments/{pid}/refund", json={"reason": "duplicate"})
    assert r.status_code == 200 and r.json()["status"] == "refunded"
    assert u.get("/api/users/me/premium").json()["is_premium"] is False
    assert admin.post(f"/api/admin/payments/{pid}/refund", json={}).status_code == 400
    s = admin.get("/api/admin/payments/summary").json()
    assert s["revenue"] == 0 and s["refunded"] == 499 and s["refund_count"] == 1
    csv_text = admin.get("/api/admin/payments/export").text
    assert "order_id" in csv_text and "refunded" in csv_text
    assert admin.get("/api/admin/payments?q=" + u.user["email"][:6]).json()["total"] == 1
    assert admin.delete(f"/api/admin/plans/{admin.get('/api/admin/plans').json()['items'][0]['id']}").json()["deactivated"] is True


def test_my_payments_visible_only_to_owner(admin, make_actor):
    u, other = make_actor("user"), make_actor("user")
    buy(admin, u)
    assert len(u.get("/api/payments/me").json()["items"]) == 1
    assert other.get("/api/payments/me").json()["items"] == []
