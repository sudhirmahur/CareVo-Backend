def test_register_login_me(client):
    r = client.post("/api/auth/register", json={"name": "Asha Rao", "email": "Asha@Example.com", "password": "secret123"})
    assert r.status_code == 201
    body = r.json()
    assert body["user"]["email"] == "asha@example.com"  # lower-cased
    assert body["user"]["role"] == "user"
    assert "password_hash" not in str(body)

    r = client.post("/api/auth/login", json={"email": "asha@example.com", "password": "secret123"})
    assert r.status_code == 200
    token = r.json()["access_token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200 and me.json()["name"] == "Asha Rao"


def test_duplicate_email_conflict(client):
    p = {"name": "Asha Rao", "email": "dup@example.com", "password": "secret123"}
    assert client.post("/api/auth/register", json=p).status_code == 201
    assert client.post("/api/auth/register", json=p).status_code == 409


def test_register_validation(client):
    bad_email = client.post("/api/auth/register", json={"name": "Asha", "email": "nope", "password": "secret123"})
    assert bad_email.status_code == 422
    assert isinstance(bad_email.json()["detail"], str)
    short_pw = client.post("/api/auth/register", json={"name": "Asha", "email": "a@b.co", "password": "123"})
    assert short_pw.status_code == 422


def test_cannot_self_register_as_admin(client):
    r = client.post(
        "/api/auth/register", json={"name": "Evil", "email": "evil@example.com", "password": "secret123", "role": "admin"}
    )
    assert r.status_code == 422


def test_wrong_password_and_unknown_user(client, make_actor):
    u = make_actor("user")
    assert client.post("/api/auth/login", json={"email": u.user["email"], "password": "wrongpass"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "ghost@example.com", "password": "secret123"}).status_code == 401


def test_protected_routes_need_token(client):
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/applications").status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_forgot_and_reset_password(client, make_actor):
    u = make_actor("user")
    r = client.post("/api/auth/forgot-password", json={"email": u.user["email"]})
    token = r.json()["reset_token"]
    # unknown e-mail gives the same message and no token
    ghost = client.post("/api/auth/forgot-password", json={"email": "ghost@example.com"}).json()
    assert "reset_token" not in ghost
    assert client.post("/api/auth/reset-password", json={"token": token, "password": "newpass123"}).status_code == 200
    # token is single use
    assert client.post("/api/auth/reset-password", json={"token": token, "password": "another123"}).status_code == 400
    assert client.post("/api/auth/login", json={"email": u.user["email"], "password": "newpass123"}).status_code == 200
    assert client.post("/api/auth/login", json={"email": u.user["email"], "password": "secret123"}).status_code == 401


def test_change_password(client, make_actor):
    u = make_actor("user")
    assert u.post("/api/auth/change-password", json={"current_password": "bad", "new_password": "newpass123"}).status_code == 400
    assert u.post("/api/auth/change-password", json={"current_password": "secret123", "new_password": "newpass123"}).status_code == 200
    assert client.post("/api/auth/login", json={"email": u.user["email"], "password": "newpass123"}).status_code == 200


def test_blocked_user_cannot_login_or_use_token(client, make_actor, admin):
    u = make_actor("user")
    assert admin.patch(f"/api/admin/users/{u.id}", json={"is_active": False}).status_code == 200
    assert client.post("/api/auth/login", json={"email": u.user["email"], "password": "secret123"}).status_code == 403
    assert u.get("/api/auth/me").status_code == 403  # existing token stops working immediately
    admin.patch(f"/api/admin/users/{u.id}", json={"is_active": True})
    assert u.get("/api/auth/me").status_code == 200


def test_registration_can_be_disabled(client, admin):
    admin.put("/api/admin/settings", json={"allow_registration": False})
    r = client.post("/api/auth/register", json={"name": "Late", "email": "late@example.com", "password": "secret123"})
    assert r.status_code == 403


def test_profile_update(make_actor):
    u = make_actor("user")
    r = u.put(
        "/api/users/me/profile",
        json={"bio": "Dev", "experience_years": 3, "skills": ["React", "react", " Node "], "github_url": "https://github.com/x"},
    )
    assert r.status_code == 200
    p = u.get("/api/users/me/profile").json()
    assert p["skills"] == ["React", "Node"] and p["experience_years"] == 3
    assert u.put("/api/users/me/profile", json={"github_url": "javascript:alert(1)"}).status_code == 422
    assert u.put("/api/users/me/profile", json={}).status_code == 400
    assert u.put("/api/users/me", json={"name": "New Name"}).status_code == 200
    assert u.get("/api/users/me").json()["name"] == "New Name"
