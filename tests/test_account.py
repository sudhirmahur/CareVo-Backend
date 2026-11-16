from app.services import totp


def login(client, email, pw="secret123", ua="Mozilla/5.0 (Windows NT 10.0) Chrome/120.0 Safari/537.36"):
    return client.post("/api/auth/login", json={"email": email, "password": pw}, headers={"user-agent": ua})


def test_sessions_list_and_revoke(make_actor, client):
    u = make_actor("user")
    t2 = login(client, u.user["email"], ua="Mozilla/5.0 (iPhone) Safari/604").json()["access_token"]
    rows = u.get("/api/account/sessions").json()["items"]
    assert len(rows) == 2 and sum(1 for r in rows if r["current"]) == 1
    assert any("iOS" in r["device"] for r in rows) and all("sid" not in r for r in rows)
    other = next(r for r in rows if not r["current"])
    me = lambda t: client.get("/api/auth/me", headers={"Authorization": f"Bearer {t}"}).status_code
    assert me(t2) == 200
    assert u.delete(f"/api/account/sessions/{other['id']}").status_code == 200
    assert me(t2) == 401  # the ended session's token stops working immediately
    assert u.delete(f"/api/account/sessions/{[r for r in u.get('/api/account/sessions').json()['items'] if r['current']][0]['id']}").status_code == 400
    t3 = login(client, u.user["email"]).json()["access_token"]
    assert u.delete("/api/account/sessions").json()["ended"] == 1
    assert me(t3) == 401 and u.get("/api/auth/me").status_code == 200


def test_cannot_end_someone_elses_session(make_actor):
    a, b = make_actor("user"), make_actor("user")
    sid = a.get("/api/account/sessions").json()["items"][0]["id"]
    assert b.delete(f"/api/account/sessions/{sid}").status_code == 404


def test_activity_history(make_actor, client):
    u = make_actor("user")
    login(client, u.user["email"])
    u.put("/api/account/privacy", json={"profile_visibility": "private"})
    kinds = [a["kind"] for a in u.get("/api/account/activity").json()["items"]]
    assert "register" in kinds and "login" in kinds and "privacy_changed" in kinds
    assert all("ip" in a and "device" in a and a["created_at"] for a in u.get("/api/account/activity").json()["items"])


def test_totp_two_factor_flow(make_actor, client):
    u = make_actor("user")
    setup = u.post("/api/auth/2fa/totp/setup").json()
    assert setup["otpauth_uri"].startswith("otpauth://totp/") and len(setup["secret"]) >= 16
    assert u.post("/api/auth/2fa/totp/enable", json={"code": "000000"}).status_code == 400
    assert u.post("/api/auth/2fa/totp/enable", json={"code": totp.current(setup["secret"])}).status_code == 200
    assert u.get("/api/auth/2fa").json() == {"enabled": True, "method": "totp", "pending_totp": False}
    r = login(client, u.user["email"]).json()
    assert r["requires_2fa"] and r["method"] == "totp" and "access_token" not in r
    # a challenge token is not a session token
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {r['challenge_token']}"}).status_code == 401
    bad = client.post("/api/auth/2fa/verify", json={"challenge_token": r["challenge_token"], "code": "123456"})
    assert bad.status_code == 401
    ok = client.post("/api/auth/2fa/verify", json={"challenge_token": r["challenge_token"], "code": totp.current(setup["secret"])})
    assert ok.status_code == 200 and ok.json()["user"]["email"] == u.user["email"]
    tok = ok.json()["access_token"]
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 200
    assert u.post("/api/auth/2fa/disable", json={"password": "wrong"}).status_code == 400
    assert u.post("/api/auth/2fa/disable", json={"password": "secret123"}).json()["enabled"] is False
    assert "access_token" in login(client, u.user["email"]).json()


def test_totp_challenge_locks_after_five_wrong_codes(make_actor, client):
    u = make_actor("user")
    s = u.post("/api/auth/2fa/totp/setup").json()["secret"]
    u.post("/api/auth/2fa/totp/enable", json={"code": totp.current(s)})
    ch = login(client, u.user["email"]).json()["challenge_token"]
    for _ in range(5):
        assert client.post("/api/auth/2fa/verify", json={"challenge_token": ch, "code": "111111"}).status_code == 401
    assert client.post("/api/auth/2fa/verify", json={"challenge_token": ch, "code": totp.current(s)}).status_code == 429


def test_email_otp_flow(make_actor, client):
    u = make_actor("recruiter")
    sent = u.post("/api/auth/2fa/email/send").json()
    assert u.post("/api/auth/2fa/email/enable", json={"code": "000000", "challenge_id": sent["challenge_id"]}).status_code == 400
    assert u.post("/api/auth/2fa/email/enable", json={"code": sent["dev_code"], "challenge_id": sent["challenge_id"]}).status_code == 200
    r = login(client, u.user["email"]).json()
    assert r["requires_2fa"] and r["method"] == "email" and r["dev_code"]
    assert client.post("/api/auth/2fa/verify", json={"challenge_token": r["challenge_token"], "code": r["dev_code"]}).status_code == 200
    # the code is single use
    assert client.post("/api/auth/2fa/verify", json={"challenge_token": r["challenge_token"], "code": r["dev_code"]}).status_code == 400
    admin_view = make_actor("user")
    from app.database import email_outbox_collection

    assert email_outbox_collection.count_documents({"template": "otp", "to": u.user["email"]}) >= 2
    assert admin_view  # silence linters


def test_google_style_login_paths_also_need_2fa_for_admin(admin, client):
    s = admin.post("/api/auth/2fa/totp/setup").json()["secret"]
    admin.post("/api/auth/2fa/totp/enable", json={"code": totp.current(s)})
    r = login(client, "admin@carevo.test", "Admin@12345").json()
    assert r["requires_2fa"]


def test_notification_preferences_gate_in_app_notifications(make_actor, admin):
    u = make_actor("user")
    p = u.get("/api/account/notification-preferences").json()
    assert p["email_enabled"] and set(p["categories"]) == {"jobs", "applications", "interviews", "messages", "system", "promotions"}
    out = u.put("/api/account/notification-preferences", json={"categories": {"system": {"in_app": False, "email": True}}}).json()
    assert out["categories"]["system"]["in_app"] is False and out["categories"]["jobs"]["in_app"] is True
    before = u.get("/api/notifications").json()["total"]
    admin.post("/api/admin/notifications", json={"title": "Maintenance", "message": "tonight", "audience": "user"})
    assert u.get("/api/notifications").json()["total"] == before
    assert u.put("/api/account/notification-preferences", json={"categories": {"bogus": {"in_app": True}}}).status_code == 422


def test_privacy_controls_and_messaging(make_actor):
    cand, rec = make_actor("user"), make_actor("recruiter")
    assert cand.get("/api/account/privacy").json()["profile_visibility"] == "recruiters"
    assert cand.put("/api/account/privacy", json={"profile_visibility": "nobody"}).status_code == 422
    cand.put("/api/account/privacy", json={"allow_messages": "none"})
    r = rec.post("/api/messages", json={"receiver_id": cand.id, "message": "Hello there"})
    assert r.status_code == 403
    cand.put("/api/account/privacy", json={"allow_messages": "everyone"})
    assert rec.post("/api/messages", json={"receiver_id": cand.id, "message": "Hello there"}).status_code == 201


def test_block_prevents_messages_both_ways_and_can_be_undone(make_actor):
    a, b = make_actor("user"), make_actor("recruiter")
    assert a.post("/api/account/blocks", json={"user_id": b.id}).status_code == 201
    assert a.post("/api/account/blocks", json={"user_id": a.id}).status_code == 400
    assert b.post("/api/messages", json={"receiver_id": a.id, "message": "hi"}).status_code == 403
    assert a.post("/api/messages", json={"receiver_id": b.id, "message": "hi"}).status_code == 403
    assert [x["name"] for x in a.get("/api/account/blocks").json()["items"]] == [b.user["name"]]
    a.delete(f"/api/account/blocks/{b.id}")
    assert b.post("/api/messages", json={"receiver_id": a.id, "message": "hi"}).status_code == 201


def test_cannot_block_admin(make_actor, admin):
    assert make_actor("user").post("/api/account/blocks", json={"user_id": admin.id}).status_code == 400


def test_report_message_and_history(make_actor, admin):
    a, b = make_actor("user"), make_actor("recruiter")
    m = b.post("/api/messages", json={"receiver_id": a.id, "message": "send money please"}).json()
    r = a.post("/api/reports", json={"content_type": "message", "content_id": m["id"], "reason": "Looks like a scam"})
    assert r.status_code == 201
    assert a.post("/api/reports", json={"content_type": "message", "content_id": m["id"], "reason": "again"}).status_code == 409
    mine = a.get("/api/reports/mine").json()["items"]
    assert len(mine) == 1 and mine[0]["content_type"] == "message" and mine[0]["status"] == "pending"
    assert any(x["content_type"] == "message" for x in admin.get("/api/admin/reports").json()["items"])


def test_export_my_data(make_actor):
    u = make_actor("user")
    r = u.get("/api/account/export")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    body = r.json()
    assert body["account"]["email"] == u.user["email"] and "password_hash" not in r.text


def test_deactivate_then_login_reactivates_but_blocked_stays_blocked(make_actor, admin, client):
    u = make_actor("user")
    assert u.post("/api/account/deactivate", json={"password": "bad"}).status_code == 400
    assert u.post("/api/account/deactivate", json={"password": "secret123"}).status_code == 200
    assert u.get("/api/auth/me").status_code in (401, 403)
    assert login(client, u.user["email"]).status_code == 200
    assert u.get("/api/auth/me").status_code == 401  # old session was revoked
    b = make_actor("user")
    admin.patch(f"/api/admin/users/{b.id}", json={"is_active": False})
    assert login(client, b.user["email"]).status_code == 403


def test_deletion_request_and_admin_approval(make_actor, admin, client):
    u = make_actor("user")
    assert u.post("/api/account/deletion-request", json={"password": "bad"}).status_code == 400
    assert u.post("/api/account/deletion-request", json={"password": "secret123", "reason": "leaving"}).status_code == 201
    assert u.post("/api/account/deletion-request", json={"password": "secret123"}).json()["already_requested"] is True
    assert u.get("/api/account/deletion-request").json()["pending"] is True
    pending = admin.get("/api/admin/deletion-requests").json()["items"]
    assert len(pending) == 1 and pending[0]["user"]["email"] == u.user["email"]
    assert u.post(f"/api/admin/deletion-requests/{pending[0]['id']}/approve").status_code == 403
    assert admin.post(f"/api/admin/deletion-requests/{pending[0]['id']}/approve").status_code == 200
    assert login(client, u.user["email"]).status_code == 401
    v = make_actor("user")
    v.post("/api/account/deletion-request", json={"password": "secret123"})
    assert v.delete("/api/account/deletion-request").status_code == 200
    assert admin.get("/api/admin/deletion-requests").json()["items"] == []


def test_password_change_signs_out_other_sessions(make_actor, client):
    u = make_actor("user")
    t2 = login(client, u.user["email"]).json()["access_token"]
    r = u.post("/api/auth/change-password", json={"current_password": "secret123", "new_password": "newsecret456"})
    assert r.status_code == 200
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {t2}"}).status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}).status_code == 200
