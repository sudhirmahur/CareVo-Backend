"""Admin-editable landing / login page content."""
from app.schemas.content import SECTION_MODELS
from app.services.site_content import DEFAULTS


def test_defaults_are_valid_for_their_schemas():
    assert set(DEFAULTS) == set(SECTION_MODELS)
    for name, model in SECTION_MODELS.items():
        model.model_validate(DEFAULTS[name])


def test_public_content_without_login(client, make_actor):
    r = client.get("/api/public/site-content")
    assert r.status_code == 200
    body = r.json()
    assert body["content"]["hero"]["title"].startswith("Find Your Next Opportunity")
    assert set(body["live_stats"]) == {"jobs", "companies", "seekers", "recruiters", "applications"}
    assert body["social_login"]["google_client_id"] is None
    make_actor("user")
    assert client.get("/api/public/site-content").json()["live_stats"]["seekers"] == 1


def test_admin_edit_is_visible_publicly_and_can_be_reset(admin, client):
    hero = {**DEFAULTS["hero"], "title": "Hire Smarter with CareVo", "highlight": "Smarter"}
    r = admin.put("/api/admin/site-content", json={"hero": hero})
    assert r.status_code == 200, r.text
    assert client.get("/api/public/site-content").json()["content"]["hero"]["title"] == "Hire Smarter with CareVo"
    # untouched sections keep their defaults
    assert client.get("/api/public/site-content").json()["content"]["cta"] == DEFAULTS["cta"]
    assert admin.post("/api/admin/site-content/reset", json=["hero"]).status_code == 200
    assert client.get("/api/public/site-content").json()["content"]["hero"] == DEFAULTS["hero"]


def test_only_admin_can_edit(make_actor, client):
    body = {"cta": DEFAULTS["cta"]}
    assert client.put("/api/admin/site-content", json=body).status_code == 401
    for role in ("user", "recruiter"):
        a = make_actor(role)
        assert a.put("/api/admin/site-content", json=body).status_code == 403
        assert a.get("/api/admin/site-content").status_code == 403
        assert a.post("/api/admin/site-content/reset", json=["hero"]).status_code == 403


def test_dangerous_links_and_images_are_rejected(admin):
    bad_cta = lambda href: {"cta": {**DEFAULTS["cta"], "button_href": href}}  # noqa: E731
    for href in ("javascript:alert(1)", "data:text/html,<script>1</script>", "//evil.test", "vbscript:x", "/ok onclick=1"):
        assert admin.put("/api/admin/site-content", json=bad_cta(href)).status_code == 422, href
    for href in ("https://example.com/x", "/register", "#how", "mailto:hi@example.com"):
        assert admin.put("/api/admin/site-content", json=bad_cta(href)).status_code == 200, href
    for img in ("javascript:alert(1)", "data:image/png;base64,AAAA", "https://x.test/a.svg", "//x.test/a.png"):
        assert admin.put("/api/admin/site-content", json={"hero": {**DEFAULTS["hero"], "image_url": img}}).status_code == 422, img


def test_validation_limits_and_unknown_fields(admin):
    assert admin.put("/api/admin/site-content", json={"nonsense": {}}).status_code == 422
    assert admin.put("/api/admin/site-content", json={}).status_code == 400
    assert admin.put("/api/admin/site-content", json={"hero": {**DEFAULTS["hero"], "title": "x" * 500}}).status_code == 422
    assert admin.put("/api/admin/site-content", json={"hero": {**DEFAULTS["hero"], "evil": 1}}).status_code == 422
    too_many = {"testimonials": {**DEFAULTS["testimonials"], "items": DEFAULTS["testimonials"]["items"] * 5}}
    assert admin.put("/api/admin/site-content", json=too_many).status_code == 422
    assert admin.put("/api/admin/site-content", json={"stats": {"items": [{"icon": "<b>", "label": "x"}]}}).status_code == 422


def test_text_is_stored_verbatim_not_executed(admin, client):
    payload = "<img src=x onerror=alert(1)>"
    admin.put("/api/admin/site-content", json={"cta": {**DEFAULTS["cta"], "title": payload}})
    assert client.get("/api/public/site-content").json()["content"]["cta"]["title"] == payload  # React escapes it on render


def test_newsletter_subscribe_dedupes_and_validates(client, admin):
    assert client.post("/api/public/newsletter", json={"email": "Reader@Example.com"}).status_code == 201
    assert client.post("/api/public/newsletter", json={"email": "reader@example.com"}).status_code == 201  # same reply, no enumeration
    assert client.post("/api/public/newsletter", json={"email": "not-an-email"}).status_code == 422
    rows = admin.get("/api/admin/newsletter").json()
    assert rows["total"] == 1 and rows["items"][0]["email"] == "reader@example.com"
    assert admin.delete(f"/api/admin/newsletter/{rows['items'][0]['id']}").status_code == 200


def test_audit_log_records_admin_changes(admin):
    admin.put("/api/admin/site-content", json={"cta": DEFAULTS["cta"]})
    logs = admin.get("/api/admin/audit-logs").json()["items"]
    assert any(l["action"] == "site_content.update" for l in logs)
