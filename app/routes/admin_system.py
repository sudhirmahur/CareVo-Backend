"""Admin: e-mail templates, roles & administrator accounts, account-deletion requests."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.database import (
    admin_roles_collection,
    deletion_requests_collection,
    email_outbox_collection,
    email_templates_collection,
    users_collection,
)
from app.dependencies.auth import require_admin
from app.routes.admin import delete_user_cascade
from app.schemas.common import Email
from app.schemas.system import AdminAccountIn, AdminAccountUpdate, PreviewIn, RoleIn, RoleUpdate, TemplateIn, TemplateUpdate
from app.services import email as mail
from app.services.audit import audit
from app.services.auth import hash_password
from app.services.permissions import ACTIONS, MODULES, effective, seed_roles
from app.services.sessions import revoke_all
from app.utils import get_or_404, now, oid, paginate, ser
from app.models.users import create_profile_document, create_user_document
from app.database import user_profiles_collection

router = APIRouter(dependencies=[Depends(require_admin)])


# ------------------------------------------------------------------ e-mail templates
def _tpl(t: dict) -> dict:
    out = ser(t)
    out["placeholders"] = mail.placeholders(t["subject"] + " " + t["body"])
    return out


@router.get("/email-templates")
def templates():
    return {"items": [_tpl(t) for t in email_templates_collection.find({}).sort("name", 1)], "known_placeholders": ["name", "app_url", "code", "link", "job_title", "company", "status", "when", "details", "plan", "until", "amount"]}


@router.post("/email-templates", status_code=201)
def template_create(data: TemplateIn, admin=Depends(require_admin)):
    if email_templates_collection.find_one({"key": data.key}):
        raise HTTPException(409, "A template with this key already exists")
    doc = {**data.model_dump(), "created_at": now(), "updated_at": now()}
    doc["_id"] = email_templates_collection.insert_one(dict(doc)).inserted_id
    audit(admin, "email_template.create", data.key)
    return _tpl(doc)


@router.put("/email-templates/{key}")
def template_update(key: str, data: TemplateUpdate, admin=Depends(require_admin)):
    t = email_templates_collection.find_one({"key": key})
    if not t:
        raise HTTPException(404, "Template not found")
    v = data.model_dump(exclude_unset=True)
    if not v:
        raise HTTPException(400, "No data provided")
    v["updated_at"] = now()
    email_templates_collection.update_one({"_id": t["_id"]}, {"$set": v})
    audit(admin, "email_template.update", key)
    return _tpl(email_templates_collection.find_one({"_id": t["_id"]}))


@router.delete("/email-templates/{key}")
def template_delete(key: str, admin=Depends(require_admin)):
    if key in {k for k, *_ in mail.DEFAULT_TEMPLATES}:
        raise HTTPException(400, "Built-in templates cannot be deleted. Disable it instead.")
    n = email_templates_collection.delete_one({"key": key}).deleted_count
    if not n:
        raise HTTPException(404, "Template not found")
    audit(admin, "email_template.delete", key)
    return {"message": "Template deleted"}


@router.post("/email-templates/preview")
def template_preview(data: PreviewIn):
    sample = {"name": "Asha", "app_url": "https://carevo.example", "code": "123456", "link": "https://carevo.example/x", "job_title": "Backend Engineer", "company": "Acme", "status": "shortlisted", "when": "Mon 10:00", "details": "", "plan": "Premium", "until": "01 Jan 2027", "amount": "₹499"}
    ctx = {**sample, **data.context}
    return {"subject": mail.render(data.subject, ctx), "body": mail.render(data.body, ctx), "placeholders": mail.placeholders(data.subject + " " + data.body)}


@router.post("/email-templates/{key}/test")
def template_send_test(key: str, admin=Depends(require_admin)):
    row = mail.send_template(key, admin["email"], {"name": admin["name"], "code": "123456", "link": "https://example.com", "job_title": "Sample role", "company": "Sample Co", "status": "sample", "when": "tomorrow", "details": "", "plan": "Premium", "until": "soon", "amount": "₹0"}, force=True)
    if not row:
        raise HTTPException(404, "Template not found")
    return {"message": f"Test e-mail {'sent to' if row['status'] != 'recorded' else 'recorded for'} {admin['email']}", "status": row["status"]}


@router.get("/emails")
def outbox(page: int = Query(1, ge=1), limit: int = Query(15, ge=1, le=100), template: Optional[str] = None):
    q = {"template": template} if template else {}
    return paginate(email_outbox_collection, q, page, limit, [("created_at", -1)])


# ------------------------------------------------------------------ roles + permissions
def _role(r: dict) -> dict:
    out = ser(r)
    out["admins"] = users_collection.count_documents({"role": "admin", "admin_role": r["key"]}) + (users_collection.count_documents({"role": "admin", "admin_role": None}) if r["key"] == "super_admin" else 0)
    return out


def _clean_perms(p: dict) -> dict:
    return {m: {a: bool(p.get(m, {}).get(a, False)) for a in ACTIONS} for m in MODULES}


@router.get("/roles")
def roles():
    return {"items": [_role(r) for r in admin_roles_collection.find({}).sort("created_at", 1)], "modules": MODULES, "actions": ACTIONS}


@router.post("/roles", status_code=201)
def role_create(data: RoleIn, admin=Depends(require_admin)):
    if admin_roles_collection.find_one({"key": data.key}):
        raise HTTPException(409, "A role with this key already exists")
    doc = {"key": data.key, "name": data.name, "description": data.description, "permissions": _clean_perms(data.permissions), "system": False, "created_at": now(), "updated_at": now()}
    doc["_id"] = admin_roles_collection.insert_one(dict(doc)).inserted_id
    audit(admin, "role.create", data.key)
    return _role(doc)


@router.put("/roles/{key}")
def role_update(key: str, data: RoleUpdate, admin=Depends(require_admin)):
    r = admin_roles_collection.find_one({"key": key})
    if not r:
        raise HTTPException(404, "Role not found")
    if key == "super_admin":
        raise HTTPException(400, "The Super Admin role cannot be changed")
    v = data.model_dump(exclude_unset=True)
    if "permissions" in v and v["permissions"] is not None:
        v["permissions"] = _clean_perms(v["permissions"])
    v["updated_at"] = now()
    admin_roles_collection.update_one({"_id": r["_id"]}, {"$set": v})
    audit(admin, "role.update", key, "permissions changed" if "permissions" in v else "details changed")
    return _role(admin_roles_collection.find_one({"_id": r["_id"]}))


@router.delete("/roles/{key}")
def role_delete(key: str, admin=Depends(require_admin)):
    r = admin_roles_collection.find_one({"key": key})
    if not r:
        raise HTTPException(404, "Role not found")
    if r.get("system"):
        raise HTTPException(400, "Built-in roles cannot be deleted")
    if users_collection.count_documents({"role": "admin", "admin_role": key}):
        raise HTTPException(409, "Move the administrators using this role to another role first")
    admin_roles_collection.delete_one({"_id": r["_id"]})
    audit(admin, "role.delete", key)
    return {"message": "Role deleted"}


def _admin_out(u: dict) -> dict:
    return {
        "id": str(u["_id"]), "name": u["name"], "email": u["email"], "is_active": u.get("is_active", True), "admin_role": u.get("admin_role") or "super_admin",
        "admin_permissions": u.get("admin_permissions") or {}, "created_at": ser(u.get("created_at")), "permissions": effective(u),
    }


@router.get("/admins")
def admins():
    return {"items": [_admin_out(u) for u in users_collection.find({"role": "admin"}).sort("created_at", 1)]}


@router.post("/admins", status_code=201)
def admin_create(data: AdminAccountIn, admin=Depends(require_admin)):
    from pydantic import TypeAdapter

    email = TypeAdapter(Email).validate_python(data.email)
    from app.schemas.common import _password

    _password(data.password)
    if not admin_roles_collection.find_one({"key": data.admin_role}):
        raise HTTPException(400, "Unknown role")
    if users_collection.find_one({"email": email}):
        raise HTTPException(409, "Email already registered")
    doc = create_user_document(name=data.name.strip(), email=email, password_hash=hash_password(data.password), role="admin", phone=None)
    doc["admin_role"] = data.admin_role
    doc["_id"] = users_collection.insert_one(doc).inserted_id
    user_profiles_collection.insert_one(create_profile_document(doc["_id"]))
    audit(admin, "admin.create", str(doc["_id"]), f"{email} as {data.admin_role}")
    return _admin_out(doc)


@router.patch("/admins/{user_id}")
def admin_update(user_id: str, data: AdminAccountUpdate, admin=Depends(require_admin)):
    u = get_or_404(users_collection, oid(user_id, "admin ID"), "Administrator", {"role": "admin"})
    v = data.model_dump(exclude_unset=True)
    if not v:
        raise HTTPException(400, "No data provided")
    if u["_id"] == admin["_id"] and ("admin_role" in v or v.get("is_active") is False):
        raise HTTPException(400, "You cannot change your own role or deactivate yourself")
    if "admin_role" in v and not admin_roles_collection.find_one({"key": v["admin_role"]}):
        raise HTTPException(400, "Unknown role")
    if "admin_permissions" in v and v["admin_permissions"] is not None:
        v["admin_permissions"] = {m: {a: bool(x) for a, x in p.items() if a in ACTIONS} for m, p in v["admin_permissions"].items() if m in MODULES}
    losing = ("admin_role" in v and v["admin_role"] != "super_admin") or v.get("is_active") is False
    was_super = (u.get("admin_role") or "super_admin") == "super_admin" and u.get("is_active", True)
    if was_super and losing and users_collection.count_documents({"role": "admin", "is_active": True, "$or": [{"admin_role": "super_admin"}, {"admin_role": None}]}) <= 1:
        raise HTTPException(400, "There must be at least one active Super Admin")
    v["updated_at"] = now()
    users_collection.update_one({"_id": u["_id"]}, {"$set": v})
    if v.get("is_active") is False:
        revoke_all(u["_id"])
    audit(admin, "admin.update", str(u["_id"]), ", ".join(f"{k}={'…' if k == 'admin_permissions' else val}" for k, val in v.items() if k != "updated_at"))
    return _admin_out(users_collection.find_one({"_id": u["_id"]}))


@router.get("/roles/history")
def permission_history(page: int = Query(1, ge=1), limit: int = Query(15, ge=1, le=100)):
    from app.database import audit_logs_collection

    return paginate(audit_logs_collection, {"action": {"$regex": "^(role|admin)\\."}}, page, limit, [("created_at", -1)])


# ------------------------------------------------------------------ account deletion requests
@router.get("/deletion-requests")
def deletion_requests(status: Optional[str] = Query(default="pending", pattern="^(pending|approved|rejected|cancelled)$")):
    out = []
    for r in deletion_requests_collection.find({"status": status}).sort("created_at", 1):
        u = users_collection.find_one({"_id": r["user_id"]}, {"name": 1, "email": 1, "role": 1})
        out.append({**ser(r), "user": {"id": str(u["_id"]), "name": u["name"], "email": u["email"], "role": u["role"]} if u else None})
    return {"items": out}


@router.post("/deletion-requests/{request_id}/approve")
def deletion_approve(request_id: str, admin=Depends(require_admin)):
    r = get_or_404(deletion_requests_collection, oid(request_id, "request ID"), "Request", {"status": "pending"})
    u = users_collection.find_one({"_id": r["user_id"]})
    if u and u["role"] != "admin":
        delete_user_cascade(u)
    deletion_requests_collection.update_one({"_id": r["_id"]}, {"$set": {"status": "approved", "resolved_at": now(), "resolved_by": admin["_id"]}})
    audit(admin, "user.delete_request_approved", str(r["user_id"]), u.get("email") if u else "")
    return {"message": "Account and data deleted"}


@router.post("/deletion-requests/{request_id}/reject")
def deletion_reject(request_id: str, admin=Depends(require_admin)):
    r = get_or_404(deletion_requests_collection, oid(request_id, "request ID"), "Request", {"status": "pending"})
    deletion_requests_collection.update_one({"_id": r["_id"]}, {"$set": {"status": "rejected", "resolved_at": now(), "resolved_by": admin["_id"]}})
    audit(admin, "user.delete_request_rejected", str(r["user_id"]))
    return {"message": "Request rejected"}
