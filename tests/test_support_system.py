import pytest


def ticket(actor, **kw):
    body = {"subject": "Cannot upload resume", "category": "technical", "message": "The upload spinner never stops."}
    body.update(kw)
    r = actor.post("/api/support/tickets", json=body)
    assert r.status_code == 201, r.text
    return r.json()


# ------------------------------------------------------------------ support desk
def test_ticket_lifecycle_between_user_and_admin(make_actor, admin):
    u, other = make_actor("user"), make_actor("recruiter")
    t = ticket(u)
    assert t["ticket_no"].startswith("CV-") and t["status"] == "open" and t["messages_count"] == 1 and "notes" not in t
    assert ticket(other)["ticket_no"] != t["ticket_no"]
    assert u.get("/api/support/tickets").json()["total"] == 1
    assert other.get(f"/api/support/tickets/{t['id']}").status_code == 404
    assert admin.get("/api/admin/support/tickets").json()["total"] == 2
    d = admin.post(f"/api/admin/support/tickets/{t['id']}/reply", json={"message": "Please try again after clearing cache."}).json()
    assert d["status"] == "waiting" and d["assignee"]["email"] == "admin@carevo.test" and d["messages"][-1]["staff"] is True
    assert any("Support replied" in n["title"] for n in u.get("/api/notifications").json()["items"])
    admin.post(f"/api/admin/support/tickets/{t['id']}/notes", json={"note": "Looks like a CDN issue"})
    assert "notes" not in u.get(f"/api/support/tickets/{t['id']}").json()
    assert admin.get(f"/api/admin/support/tickets/{t['id']}").json()["notes"][0]["note"].startswith("Looks like")
    r = u.post(f"/api/support/tickets/{t['id']}/reply", json={"message": "Still failing"}).json()
    assert r["status"] == "open" and r["messages_count"] == 3
    done = admin.patch(f"/api/admin/support/tickets/{t['id']}", json={"status": "resolved", "priority": "high"}).json()
    assert done["status"] == "resolved" and done["priority"] == "high" and done["resolution_hours"] is not None
    s = admin.get("/api/admin/support/summary").json()
    assert s["by_status"]["resolved"] == 1 and s["avg_resolution_hours"] is not None and s["total"] == 2
    assert u.post(f"/api/support/tickets/{t['id']}/close").json()["status"] == "closed"
    assert u.post(f"/api/support/tickets/{t['id']}/reply", json={"message": "hello again"}).status_code == 400


def test_ticket_filters_assignment_and_permissions(make_actor, admin):
    u = make_actor("user")
    a = ticket(u, category="billing", priority="urgent")
    ticket(u, subject="How do I change my email?", category="account")
    assert admin.get("/api/admin/support/tickets?category=billing").json()["total"] == 1
    assert admin.get("/api/admin/support/tickets?priority=urgent").json()["total"] == 1
    assert admin.get("/api/admin/support/tickets?assigned=unassigned").json()["total"] == 2
    assert admin.get("/api/admin/support/tickets?q=change+my").json()["total"] == 1
    assert admin.get("/api/admin/support/tickets?q=" + a["ticket_no"]).json()["total"] == 1
    agents = admin.get("/api/admin/support/agents").json()["items"]
    admin.patch(f"/api/admin/support/tickets/{a['id']}", json={"assigned_to": agents[0]["id"]})
    assert admin.get(f"/api/admin/support/tickets?assigned={agents[0]['id']}").json()["total"] == 1
    assert admin.patch(f"/api/admin/support/tickets/{a['id']}", json={"assigned_to": u.id}).status_code == 404
    assert admin.patch(f"/api/admin/support/tickets/{a['id']}", json={"status": "weird"}).status_code == 422
    assert u.get("/api/admin/support/tickets").status_code == 403
    assert u.post("/api/support/tickets", json={"subject": "x", "message": "y"}).status_code == 422


def test_feedback_collect_respond_and_report(make_actor, admin):
    u, r = make_actor("user"), make_actor("recruiter")
    assert u.post("/api/feedback", json={"category": "feature_request", "rating": 4, "message": "Please add dark mode"}).status_code == 201
    r.post("/api/feedback", json={"category": "bug", "rating": 2, "message": "Calendar is slow"})
    assert u.post("/api/feedback", json={"message": "ok", "rating": 9}).status_code == 422
    s = admin.get("/api/admin/feedback/summary").json()
    assert s["total"] == 2 and s["average_rating"] == 3.0 and s["by_category"] == {"bug": 1, "feature_request": 1} and s["by_role"] == {"recruiter": 1, "user": 1}
    items = admin.get("/api/admin/feedback?category=feature_request").json()["items"]
    assert len(items) == 1 and items[0]["user"]["email"] == u.user["email"]
    out = admin.patch(f"/api/admin/feedback/{items[0]['id']}", json={"status": "planned", "response": "Planned for next month"}).json()
    assert out["status"] == "planned"
    assert any("replied to your feedback" in n["title"] for n in u.get("/api/notifications").json()["items"])
    assert u.get("/api/feedback/mine").json()["items"][0]["response"] == "Planned for next month"
    assert "date,role" in admin.get("/api/admin/feedback/export").text
    assert u.get("/api/admin/feedback").status_code == 403


# ------------------------------------------------------------------ e-mail templates
def test_email_templates_crud_preview_and_toggle(admin, make_actor):
    items = admin.get("/api/admin/email-templates").json()["items"]
    keys = {t["key"] for t in items}
    assert {"welcome", "password_reset", "application_status", "interview_invite", "recruiter_verification", "subscription", "offer_letter", "otp", "verification"} <= keys
    p = admin.post("/api/admin/email-templates/preview", json={"subject": "Hi {{name}}", "body": "Job {{job_title}} {{unknown}}", "context": {"name": "Ravi"}}).json()
    assert p["subject"] == "Hi Ravi" and p["body"].startswith("Job Backend Engineer") and "unknown" in p["placeholders"]
    t = admin.put("/api/admin/email-templates/welcome", json={"subject": "Namaste {{name}}", "body": "Welcome aboard {{name}}"}).json()
    assert t["placeholders"] == ["name"]
    u = make_actor("user", name="Meera")
    from app.database import email_outbox_collection

    row = email_outbox_collection.find_one({"template": "welcome", "to": u.user["email"]})
    assert row["subject"] == "Namaste Meera" and row["status"] == "recorded"
    admin.put("/api/admin/email-templates/welcome", json={"enabled": False})
    before = email_outbox_collection.count_documents({"template": "welcome"})
    make_actor("user")
    assert email_outbox_collection.count_documents({"template": "welcome"}) == before
    assert admin.post("/api/admin/email-templates", json={"key": "promo", "name": "Promo", "subject": "Sale", "body": "20% off"}).status_code == 201
    assert admin.post("/api/admin/email-templates", json={"key": "promo", "name": "Promo", "subject": "Sale", "body": "again"}).status_code == 409
    assert admin.post("/api/admin/email-templates", json={"key": "Bad Key", "name": "Promo", "subject": "Sale", "body": "x"}).status_code == 422
    assert admin.delete("/api/admin/email-templates/welcome").status_code == 400
    assert admin.delete("/api/admin/email-templates/promo").status_code == 200
    assert admin.post("/api/admin/email-templates/otp/test").json()["status"] == "recorded"
    assert admin.get("/api/admin/emails?template=otp").json()["total"] >= 1
    assert u.get("/api/admin/email-templates").status_code == 403


def test_email_preferences_suppress_non_critical_mail(make_actor):
    u = make_actor("user")
    u.put("/api/account/notification-preferences", json={"email_enabled": False})
    from app.database import email_outbox_collection
    from app.services.email import send_template

    assert send_template("welcome", u.user["email"], {"name": "X"}, __import__("bson").ObjectId(u.id)) is None
    assert send_template("otp", u.user["email"], {"name": "X", "code": "1"}, __import__("bson").ObjectId(u.id), force=True)
    assert email_outbox_collection.count_documents({"template": "otp"}) == 1


# ------------------------------------------------------------------ roles + permissions
def make_admin(admin, role, email="mod@example.com"):
    r = admin.post("/api/admin/admins", json={"name": "Second Admin", "email": email, "password": "Secret@123", "admin_role": role})
    assert r.status_code == 201, r.text
    return r.json()


def login_as(client, email, pw="Secret@123"):
    from tests.conftest import Actor

    r = client.post("/api/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return Actor(client, r.json()["access_token"], r.json()["user"])


def test_default_roles_seeded_and_super_admin_can_do_everything(admin):
    d = admin.get("/api/admin/roles").json()
    assert {r["key"] for r in d["items"]} >= {"super_admin", "admin", "moderator", "support_manager", "content_manager"}
    assert "users" in d["modules"] and d["actions"] == ["read", "create", "update", "delete"]
    assert next(r for r in d["items"] if r["key"] == "super_admin")["admins"] >= 1


@pytest.mark.parametrize("role,allowed,denied", [
    ("support_manager", [("get", "/api/admin/support/tickets"), ("get", "/api/admin/feedback")], [("get", "/api/admin/payments"), ("get", "/api/admin/roles"), ("post", "/api/admin/banners")]),
    ("content_manager", [("get", "/api/admin/banners"), ("get", "/api/admin/site-content")], [("get", "/api/admin/users"), ("get", "/api/admin/support/tickets")]),
    ("moderator", [("get", "/api/admin/users"), ("get", "/api/admin/reports")], [("get", "/api/admin/payments"), ("delete", "/api/admin/users/000000000000000000000000")]),
])
def test_role_limits_are_enforced_on_the_server(admin, client, role, allowed, denied):
    make_admin(admin, role)
    a = login_as(client, "mod@example.com")
    for m, url in allowed:
        assert getattr(a, m)(url, **({"json": {"title": "x"}} if m == "post" else {})).status_code != 403, (role, url)
    for m, url in denied:
        assert getattr(a, m)(url, **({"json": {"title": "x y"}} if m == "post" else {})).status_code == 403, (role, url)


def test_custom_role_and_individual_overrides(admin, client):
    perms = {"jobs": {"read": True}, "users": {"read": True}}
    r = admin.post("/api/admin/roles", json={"key": "auditor", "name": "Auditor", "permissions": perms})
    assert r.status_code == 201 and r.json()["permissions"]["jobs"] == {"read": True, "create": False, "update": False, "delete": False}
    assert admin.post("/api/admin/roles", json={"key": "auditor", "name": "Auditor"}).status_code == 409
    acc = make_admin(admin, "auditor")
    a = login_as(client, "mod@example.com")
    assert a.get("/api/admin/jobs").status_code == 200 and a.patch("/api/admin/jobs/000000000000000000000000/status", json={"status": "approved"}).status_code == 403
    # a per-person override grants one extra permission without changing the role
    admin.patch(f"/api/admin/admins/{acc['id']}", json={"admin_permissions": {"jobs": {"update": True}}})
    assert a.patch("/api/admin/jobs/000000000000000000000000/status", json={"status": "approved"}).status_code != 403
    assert a.get("/api/admin/payments").status_code == 403
    assert admin.put("/api/admin/roles/auditor", json={"permissions": {"payments": {"read": True}}}).status_code == 200
    assert a.get("/api/admin/payments").status_code == 200
    hist = admin.get("/api/admin/roles/history").json()["items"]
    assert {"role.create", "role.update", "admin.create", "admin.update"} <= {h["action"] for h in hist}


def test_role_safety_rules(admin, client):
    assert admin.put("/api/admin/roles/super_admin", json={"name": "Hacked"}).status_code == 400
    assert admin.delete("/api/admin/roles/admin").status_code == 400
    acc = make_admin(admin, "moderator")
    admin.post("/api/admin/roles", json={"key": "tmp", "name": "Temp"})
    admin.patch(f"/api/admin/admins/{acc['id']}", json={"admin_role": "tmp"})
    assert admin.delete("/api/admin/roles/tmp").status_code == 409
    me = admin.get("/api/admin/admins").json()["items"]
    mine = next(x for x in me if x["email"] == "admin@carevo.test")
    assert admin.patch(f"/api/admin/admins/{mine['id']}", json={"is_active": False}).status_code == 400
    assert admin.patch(f"/api/admin/admins/{mine['id']}", json={"admin_role": "moderator"}).status_code == 400
    assert admin.post("/api/admin/admins", json={"name": "Bad", "email": "bad@example.com", "password": "short", "admin_role": "admin"}).status_code in (400, 422)
    assert admin.post("/api/admin/admins", json={"name": "Bad", "email": "bad2@example.com", "password": "Secret@123", "admin_role": "nope"}).status_code == 400
    # deactivating an admin ends their sessions at once
    b = login_as(client, "mod@example.com")
    admin.patch(f"/api/admin/admins/{acc['id']}", json={"is_active": False})
    assert b.get("/api/admin/users").status_code in (401, 403)


def test_regular_admin_cannot_manage_roles(admin, client):
    make_admin(admin, "admin")
    a = login_as(client, "mod@example.com")
    assert a.get("/api/admin/roles").status_code == 200
    assert a.post("/api/admin/roles", json={"key": "x1", "name": "X1"}).status_code == 403
    assert a.post("/api/admin/admins", json={"name": "Z Z", "email": "zz@example.com", "password": "Secret@123"}).status_code == 403
    assert a.get("/api/admin/users").status_code == 200
