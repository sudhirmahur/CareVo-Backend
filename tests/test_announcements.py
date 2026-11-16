from datetime import datetime, timedelta, timezone


def iso(delta_hours):
    return (datetime.now(timezone.utc) + timedelta(hours=delta_hours)).isoformat()


def test_banner_schedule_and_audience(admin, make_actor, client):
    u, r = make_actor("user"), make_actor("recruiter")
    admin.post("/api/admin/banners", json={"title": "Everyone", "audience": "all"})
    admin.post("/api/admin/banners", json={"title": "Only candidates", "audience": "user"})
    admin.post("/api/admin/banners", json={"title": "Only guests", "audience": "guest"})
    admin.post("/api/admin/banners", json={"title": "Future", "starts_at": iso(5)})
    admin.post("/api/admin/banners", json={"title": "Past", "starts_at": iso(-10), "ends_at": iso(-5)})
    admin.post("/api/admin/banners", json={"title": "Running", "starts_at": iso(-1), "ends_at": iso(5)})
    names = lambda rows: sorted(b["title"] for b in rows)
    assert names(client.get("/api/banners").json()) == ["Everyone", "Only guests", "Running"]
    assert names(u.get("/api/banners").json()) == ["Everyone", "Only candidates", "Running"]
    assert names(r.get("/api/banners").json()) == ["Everyone", "Running"]
    rows = {b["title"]: b["status"] for b in admin.get("/api/admin/banners").json()["items"]}
    assert rows["Future"] == "scheduled" and rows["Past"] == "expired" and rows["Running"] == "live"


def test_banner_validation_and_edit(admin, make_actor):
    assert admin.post("/api/admin/banners", json={"title": "x"}).status_code == 422
    assert admin.post("/api/admin/banners", json={"title": "Bad window", "starts_at": iso(5), "ends_at": iso(1)}).status_code == 422
    assert admin.post("/api/admin/banners", json={"title": "Bad link", "link_url": "javascript:alert(1)"}).status_code == 422
    b = admin.post("/api/admin/banners", json={"title": "Sale"}).json()
    e = admin.put(f"/api/admin/banners/{b['id']}", json={"title": "Big sale", "audience": "user"}).json()
    assert e["title"] == "Big sale" and e["audience"] == "user"
    assert admin.put(f"/api/admin/banners/{b['id']}", json={}).status_code == 400
    assert admin.put(f"/api/admin/banners/{b['id']}", json={"starts_at": iso(5), "ends_at": iso(1)}).status_code == 422
    assert admin.put("/api/admin/banners/zzz", json={"title": "ok ok"}).status_code == 400
    assert make_actor("user").post("/api/admin/banners", json={"title": "Nope"}).status_code == 403


def test_popup_lifecycle(admin, make_actor, client):
    u = make_actor("user")
    p = admin.post("/api/admin/popups", json={"title": "Go Premium", "message": "50% off this week", "button_label": "See plans", "button_link": "https://example.com/p", "audience": "user", "frequency": "daily", "starts_at": iso(-1), "ends_at": iso(48)})
    assert p.status_code == 201, p.text
    pid = p.json()["id"]
    assert client.get("/api/popups").json() == []
    got = u.get("/api/popups").json()
    assert len(got) == 1 and got[0]["frequency"] == "daily"
    admin.post("/api/admin/popups", json={"title": "Later", "message": "Starts tomorrow", "starts_at": iso(24)})
    assert len(u.get("/api/popups").json()) == 1
    admin.patch(f"/api/admin/popups/{pid}")
    assert u.get("/api/popups").json() == []
    admin.put(f"/api/admin/popups/{pid}", json={"is_active": True, "message": "Updated text"})
    assert u.get("/api/popups").json()[0]["message"] == "Updated text"
    assert admin.post("/api/admin/popups", json={"title": "No label", "message": "hello", "button_link": "https://x.com"}).status_code == 422
    assert admin.post("/api/admin/popups", json={"title": "Bad freq", "message": "hello", "frequency": "hourly"}).status_code == 422
    assert u.post("/api/admin/popups", json={"title": "Nope", "message": "nope nope"}).status_code == 403
    assert admin.delete(f"/api/admin/popups/{pid}").status_code == 200
    assert admin.delete(f"/api/admin/popups/{pid}").status_code == 404
