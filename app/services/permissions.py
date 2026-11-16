"""Role based access for administrators: module x (read/create/update/delete) matrix."""
from typing import Optional

from app.database import admin_roles_collection
from app.utils import now

ACTIONS = ["read", "create", "update", "delete"]
MODULES = {
    "dashboard": "Dashboard", "users": "Users", "recruiters": "Recruiters", "companies": "Companies", "jobs": "Jobs", "applications": "Applications", "interviews": "Interviews",
    "videos": "Videos", "reports": "Reports", "skills": "Skills & categories", "content": "Landing, banners, popups, learning", "payments": "Plans & payments", "support": "Support & feedback",
    "settings": "Settings", "roles": "Roles & admin accounts", "emails": "E-mail templates", "analytics": "Analytics", "system": "System health, exports, backups, audit",
}
SEGMENT = {
    "stats": "dashboard", "users": "users", "premium-requests": "users", "deletion-requests": "users", "recruiters": "recruiters", "companies": "companies", "jobs": "jobs", "applications": "applications",
    "interviews": "interviews", "videos": "videos", "reports": "reports", "skills": "skills", "skill-categories": "skills", "banners": "content", "popups": "content", "site-content": "content",
    "newsletter": "content", "notifications": "content", "prep": "content", "assessments": "content", "events": "content", "plans": "payments", "payments": "payments", "subscriptions": "payments",
    "support": "support", "feedback": "support", "settings": "settings", "roles": "roles", "admins": "roles", "email-templates": "emails", "emails": "emails", "analytics": "analytics",
    "health": "system", "backups": "system", "exports": "system", "audit-logs": "system",
}
METHOD_ACTION = {"GET": "read", "HEAD": "read", "POST": "create", "PUT": "update", "PATCH": "update", "DELETE": "delete"}


def _all(read=True, create=True, update=True, delete=True):
    return {"read": read, "create": create, "update": update, "delete": delete}


def _matrix(spec: dict) -> dict:
    return {m: spec.get(m, _all(False, False, False, False)) for m in MODULES}


RW = _all(True, True, True, False)
RO = _all(True, False, False, False)
SEED_ROLES = [
    ("super_admin", "Super Admin", "Full access to everything, including roles and backups.", {m: _all() for m in MODULES}),
    ("admin", "Admin", "Runs the platform day to day. Cannot manage roles, backups or restore data.", {**{m: _all() for m in MODULES if m not in ("roles", "system")}, "roles": RO, "system": RO}),
    ("moderator", "Moderator", "Moderates users, jobs, videos and reports.", {"dashboard": RO, "users": RW, "recruiters": RW, "companies": RW, "jobs": RW, "applications": RO, "interviews": RO, "videos": RW, "reports": RW}),
    ("support_manager", "Support Manager", "Handles tickets and feedback.", {"dashboard": RO, "users": RO, "support": _all(), "reports": RO}),
    ("content_manager", "Content Manager", "Edits the landing page, banners, popups, learning content and skills.", {"dashboard": RO, "content": _all(), "skills": _all(), "emails": RW}),
]


def seed_roles() -> None:
    for key, name, desc, perms in SEED_ROLES:
        if not admin_roles_collection.find_one({"key": key}):
            admin_roles_collection.insert_one({"key": key, "name": name, "description": desc, "permissions": _matrix(perms), "system": True, "created_at": now(), "updated_at": now()})


def module_for_path(path: str) -> Optional[str]:
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 3 and parts[0] == "api" and parts[1] == "admin":
        return SEGMENT.get(parts[2])
    return None


def effective(user: dict) -> dict:
    """Role matrix, then per-administrator overrides on top."""
    key = user.get("admin_role") or "super_admin"
    role = admin_roles_collection.find_one({"key": key})
    perms = {m: dict(v) for m, v in (role["permissions"] if role else _matrix({})).items()}
    for m, v in (user.get("admin_permissions") or {}).items():
        if m in perms:
            perms[m].update({a: bool(x) for a, x in v.items() if a in ACTIONS})
    return perms


def can(user: dict, module: Optional[str], action: str) -> bool:
    if (user.get("admin_role") or "super_admin") == "super_admin" and not user.get("admin_permissions"):
        return True
    if module is None:
        return False
    return bool(effective(user).get(module, {}).get(action))
