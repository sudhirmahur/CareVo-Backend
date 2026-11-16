"""Razorpay subscriptions: plans, checkout orders, signature verification, webhook, refunds, reports."""
import csv
import io
import json
from datetime import timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from app import config
from app.database import payments_collection, plans_collection, subscriptions_collection, users_collection
from app.dependencies.auth import get_current_user, require_admin
from app.schemas.payments import OrderIn, PlanIn, PlanUpdate, RefundIn, VerifyIn
from app.services import razorpay as rzp
from app.services.audit import audit
from app.services.notify import notify
from app.utils import get_or_404, now, oid, paginate, rx, ser

router = APIRouter()
admin_router = APIRouter(dependencies=[Depends(require_admin)])


def _plan_out(p: dict) -> dict:
    out = ser(p)
    out["price_paise"] = int(round(p["price"] * 100))
    return out


def _aware(dt):
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- activation
def _activate(payment: dict) -> None:
    """Idempotently turn a captured payment into Premium time."""
    fresh = payments_collection.find_one_and_update(
        {"_id": payment["_id"], "status": {"$in": ["created", "failed"]}},
        {"$set": {"status": "paid", "paid_at": now()}},
    )
    if not fresh:  # already processed (verify + webhook race, or replay)
        return
    user = users_collection.find_one({"_id": payment["user_id"]})
    if not user:
        return
    start = now()
    cur = _aware(user.get("premium_until"))
    if user.get("is_premium") and cur and cur > start:
        start = cur  # renewing: add time on top of what is left
    end = start + timedelta(days=payment["duration_days"])
    users_collection.update_one({"_id": user["_id"]}, {"$set": {"is_premium": True, "premium_until": end, "updated_at": now()}})
    subscriptions_collection.insert_one(
        {"user_id": user["_id"], "payment_id": payment["_id"], "plan_id": payment["plan_id"], "plan_name": payment["plan_name"], "starts_at": start, "ends_at": end, "created_at": now()}
    )
    notify(user["_id"], "Premium activated", f"Your {payment['plan_name']} plan is active until {end:%d %b %Y}.", "system", "/videos")


def _revoke(payment: dict) -> None:
    sub = subscriptions_collection.find_one_and_delete({"payment_id": payment["_id"]})
    user = users_collection.find_one({"_id": payment["user_id"]})
    if not user:
        return
    other = subscriptions_collection.find_one({"user_id": user["_id"], "ends_at": {"$gt": now()}}, sort=[("ends_at", -1)])
    if other:
        users_collection.update_one({"_id": user["_id"]}, {"$set": {"premium_until": other["ends_at"]}})
    elif sub:
        users_collection.update_one({"_id": user["_id"]}, {"$set": {"is_premium": False, "premium_until": None}})


# ---------------------------------------------------------------- public / user
@router.get("/plans")
def list_plans():
    return {
        "items": [_plan_out(p) for p in plans_collection.find({"is_active": True}).sort("price", 1)],
        "payments_enabled": rzp.configured(),
        "key_id": config.RAZORPAY_KEY_ID or None,
    }


@router.post("/payments/order", status_code=201)
def create_order(data: OrderIn, user=Depends(get_current_user)):
    if user["role"] != "user":
        raise HTTPException(400, "Premium plans are for candidate accounts")
    plan = get_or_404(plans_collection, oid(data.plan_id, "plan ID"), "Plan", {"is_active": True})
    amount = int(round(plan["price"] * 100))  # always from the server-side plan, never from the client
    receipt = f"cv_{str(user['_id'])[-8:]}_{int(now().timestamp())}"
    order = rzp.request("POST", "/orders", {"amount": amount, "currency": "INR", "receipt": receipt, "notes": {"user_id": str(user["_id"]), "plan_id": str(plan["_id"])}})
    if not order.get("id") or order.get("amount") != amount:
        raise HTTPException(502, "Payment provider returned an unexpected order")
    doc = {
        "order_id": order["id"], "user_id": user["_id"], "plan_id": plan["_id"], "plan_name": plan["name"], "duration_days": plan["duration_days"],
        "amount": amount, "currency": "INR", "status": "created", "payment_ref": None, "created_at": now(), "updated_at": now(),
    }
    doc["_id"] = payments_collection.insert_one(dict(doc)).inserted_id
    return {"order_id": order["id"], "amount": amount, "currency": "INR", "key_id": config.RAZORPAY_KEY_ID, "plan": _plan_out(plan), "name": user["name"], "email": user["email"]}


@router.post("/payments/verify")
def verify_payment(data: VerifyIn, user=Depends(get_current_user)):
    pay = payments_collection.find_one({"order_id": data.razorpay_order_id, "user_id": user["_id"]})
    if not pay:
        raise HTTPException(404, "Order not found")
    if not rzp.valid_checkout_signature(data.razorpay_order_id, data.razorpay_payment_id, data.razorpay_signature):
        payments_collection.update_one({"_id": pay["_id"], "status": "created"}, {"$set": {"status": "failed", "failure": "bad signature", "updated_at": now()}})
        raise HTTPException(400, "Payment verification failed")
    payments_collection.update_one({"_id": pay["_id"]}, {"$set": {"payment_ref": data.razorpay_payment_id, "updated_at": now()}})
    _activate(pay)
    u = users_collection.find_one({"_id": user["_id"]})
    return {"status": "paid", "is_premium": bool(u.get("is_premium")), "premium_until": ser(u.get("premium_until"))}


@router.get("/payments/me")
def my_payments(user=Depends(get_current_user)):
    rows = payments_collection.find({"user_id": user["_id"]}).sort("created_at", -1).limit(50)
    return {"items": [_pay_out(p) for p in rows], "premium_until": ser(user.get("premium_until")), "is_premium": bool(user.get("is_premium"))}


@router.post("/payments/webhook")
async def webhook(request: Request):
    raw = await request.body()
    if not rzp.valid_webhook_signature(raw, request.headers.get("x-razorpay-signature", "")):
        raise HTTPException(400, "Invalid signature")
    try:
        event = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "Invalid payload")
    kind = event.get("event")
    ent = (event.get("payload", {}).get("payment") or {}).get("entity", {}) or {}
    order_id = ent.get("order_id") or ((event.get("payload", {}).get("order") or {}).get("entity", {}) or {}).get("id")
    pay = payments_collection.find_one({"order_id": order_id}) if order_id else None
    if pay:
        if kind in ("payment.captured", "order.paid") and ent.get("amount", pay["amount"]) == pay["amount"]:
            payments_collection.update_one({"_id": pay["_id"]}, {"$set": {"payment_ref": ent.get("id") or pay.get("payment_ref"), "updated_at": now()}})
            _activate(pay)
        elif kind == "payment.failed":
            payments_collection.update_one({"_id": pay["_id"], "status": "created"}, {"$set": {"status": "failed", "failure": (ent.get("error_description") or "failed")[:200], "updated_at": now()}})
        elif kind in ("refund.processed", "payment.refunded") and pay["status"] == "paid":
            payments_collection.update_one({"_id": pay["_id"]}, {"$set": {"status": "refunded", "refunded_at": now(), "updated_at": now()}})
            _revoke(pay)
    return {"ok": True}


def _pay_out(p: dict, with_user: bool = False) -> dict:
    out = ser(p)
    out["amount_rupees"] = p["amount"] / 100
    if with_user:
        u = users_collection.find_one({"_id": p["user_id"]}, {"name": 1, "email": 1})
        out["user"] = {"name": u["name"], "email": u["email"]} if u else None
    return out


# ---------------------------------------------------------------- admin: plans
@admin_router.get("/plans")
def admin_plans():
    out = []
    for p in plans_collection.find({}).sort("price", 1):
        row = _plan_out(p)
        row["sales"] = payments_collection.count_documents({"plan_id": p["_id"], "status": {"$in": ["paid", "refunded"]}})
        out.append(row)
    return {"items": out}


@admin_router.post("/plans", status_code=201)
def create_plan(data: PlanIn, admin=Depends(require_admin)):
    doc = {**data.model_dump(), "features": [f.strip() for f in data.features if f.strip()], "created_at": now(), "updated_at": now()}
    doc["_id"] = plans_collection.insert_one(dict(doc)).inserted_id
    audit(admin, "plan.create", str(doc["_id"]), f"{doc['name']} {doc['price']}")
    return _plan_out(doc)


@admin_router.put("/plans/{plan_id}")
def update_plan(plan_id: str, data: PlanUpdate, admin=Depends(require_admin)):
    plan = get_or_404(plans_collection, oid(plan_id, "plan ID"), "Plan")
    values = data.model_dump(exclude_unset=True)
    if not values:
        raise HTTPException(400, "No data provided")
    if "features" in values:
        values["features"] = [f.strip() for f in values["features"] if f.strip()]
    values["updated_at"] = now()
    plans_collection.update_one({"_id": plan["_id"]}, {"$set": values})
    audit(admin, "plan.update", str(plan["_id"]))
    return _plan_out(plans_collection.find_one({"_id": plan["_id"]}))


@admin_router.delete("/plans/{plan_id}")
def delete_plan(plan_id: str, admin=Depends(require_admin)):
    plan = get_or_404(plans_collection, oid(plan_id, "plan ID"), "Plan")
    if payments_collection.count_documents({"plan_id": plan["_id"]}):
        plans_collection.update_one({"_id": plan["_id"]}, {"$set": {"is_active": False}})
        audit(admin, "plan.deactivate", str(plan["_id"]))
        return {"message": "Plan has payments, so it was deactivated instead of deleted", "deactivated": True}
    plans_collection.delete_one({"_id": plan["_id"]})
    audit(admin, "plan.delete", str(plan["_id"]))
    return {"message": "Plan deleted", "deactivated": False}


# ---------------------------------------------------------------- admin: payments
def _pay_query(status: Optional[str], q: Optional[str]) -> dict:
    query: dict = {}
    if status:
        query["status"] = status
    if q:
        ids = [u["_id"] for u in users_collection.find({"$or": [{"email": rx(q)}, {"name": rx(q)}]}, {"_id": 1}).limit(100)]
        query["$or"] = [{"order_id": rx(q)}, {"payment_ref": rx(q)}, {"user_id": {"$in": ids}}]
    return query


@admin_router.get("/payments")
def admin_payments(status: Optional[str] = Query(default=None, pattern="^(created|paid|failed|refunded)$"), q: Optional[str] = Query(default=None, max_length=80), page: int = Query(1, ge=1), limit: int = Query(15, ge=1, le=100)):
    return paginate(payments_collection, _pay_query(status, q), page, limit, [("created_at", -1)], mapper=lambda p: _pay_out(p, True))


@admin_router.get("/payments/summary")
def payments_summary(days: int = Query(30, ge=1, le=365)):
    t = now()
    paid = list(payments_collection.find({"status": {"$in": ["paid", "refunded"]}}))
    gross = sum(p["amount"] for p in paid if p["status"] == "paid")
    refunded = sum(p["amount"] for p in paid if p["status"] == "refunded")
    since = t - timedelta(days=days)
    daily: dict[str, int] = {}
    for p in paid:
        pa = _aware(p.get("paid_at"))
        if p["status"] == "paid" and pa and pa >= since:
            daily[pa.strftime("%Y-%m-%d")] = daily.get(pa.strftime("%Y-%m-%d"), 0) + p["amount"]
    series = [{"date": (since + timedelta(days=i + 1)).strftime("%Y-%m-%d")} for i in range(days)]
    for s in series:
        s["revenue"] = daily.get(s["date"], 0) / 100
    active = users_collection.count_documents({"role": "user", "is_premium": True, "$or": [{"premium_until": None}, {"premium_until": {"$gt": t}}]})
    expired = users_collection.count_documents({"role": "user", "is_premium": True, "premium_until": {"$lte": t}})
    return {
        "revenue": gross / 100, "refunded": refunded / 100, "paid_count": sum(1 for p in paid if p["status"] == "paid"), "refund_count": sum(1 for p in paid if p["status"] == "refunded"),
        "failed_count": payments_collection.count_documents({"status": "failed"}), "pending_count": payments_collection.count_documents({"status": "created"}),
        "active_subscriptions": active, "expired_subscriptions": expired, "series": series, "days": days,
    }


@admin_router.get("/payments/export")
def payments_export(admin=Depends(require_admin)):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "order_id", "payment_id", "user", "email", "plan", "amount_inr", "status"])
    for p in payments_collection.find({}).sort("created_at", -1):
        pr = _pay_out(p, True)
        w.writerow([pr["created_at"][:19], p["order_id"], p.get("payment_ref") or "", (pr["user"] or {}).get("name", ""), (pr["user"] or {}).get("email", ""), p["plan_name"], f"{p['amount'] / 100:.2f}", p["status"]])
    audit(admin, "payments.export")
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=payments.csv"})


@admin_router.post("/payments/{payment_id}/refund")
def refund(payment_id: str, data: RefundIn, admin=Depends(require_admin)):
    pay = get_or_404(payments_collection, oid(payment_id, "payment ID"), "Payment")
    if pay["status"] != "paid" or not pay.get("payment_ref"):
        raise HTTPException(400, "Only paid payments can be refunded")
    res = rzp.request("POST", f"/payments/{pay['payment_ref']}/refund", {"amount": pay["amount"], "notes": {"reason": (data.reason or "admin refund")[:200]}})
    payments_collection.update_one({"_id": pay["_id"]}, {"$set": {"status": "refunded", "refund_id": res.get("id"), "refund_reason": data.reason, "refunded_at": now(), "updated_at": now()}})
    _revoke(pay)
    notify(pay["user_id"], "Payment refunded", f"Your payment of ₹{pay['amount'] / 100:.2f} was refunded.", "system", "/settings")
    audit(admin, "payment.refund", str(pay["_id"]), data.reason)
    return _pay_out(payments_collection.find_one({"_id": pay["_id"]}), True)


@admin_router.get("/subscriptions")
def admin_subscriptions(state: Optional[str] = Query(default=None, pattern="^(active|expired)$"), page: int = Query(1, ge=1), limit: int = Query(15, ge=1, le=100)):
    t = now()
    query: dict = {}
    if state == "active":
        query["ends_at"] = {"$gt": t}
    elif state == "expired":
        query["ends_at"] = {"$lte": t}

    def mapper(s):
        out = ser(s)
        u = users_collection.find_one({"_id": s["user_id"]}, {"name": 1, "email": 1})
        out["user"] = {"name": u["name"], "email": u["email"]} if u else None
        out["state"] = "active" if _aware(s["ends_at"]) > t else "expired"
        return out

    return paginate(subscriptions_collection, query, page, limit, [("ends_at", -1)], mapper=mapper)
