import hashlib
from copy import deepcopy

from conftest import auth
from sqlalchemy import select

from mediabias.config import Settings
from mediabias.db import Analysis, Article, NewsEvent
from mediabias.model import fingerprint


def install_analysis(client, secured):
    data = client.get("/api/events/demo-event").json()
    with secured.app.state.sessions.begin() as s:
        s.add(NewsEvent(id="event", title="Gerçek haber testi", is_demo=False))
        s.flush()
        a = Analysis(
            id="analysis",
            event_id="event",
            cache_key="x",
            snapshots=data["analysis"]["snapshots"],
            payload=data["analysis"]["payload"],
            provenance={"mode": "test"},
        )
        s.add(a)
    return data


def test_authentication_and_roles(secured):
    assert secured.get("/api/events").status_code == 401
    assert secured.get("/api/events", headers=auth("r")).status_code == 200
    assert secured.post("/api/collect", headers=auth("r")).status_code == 403
    assert secured.get("/api/export", headers=auth("r")).status_code == 403
    assert secured.get("/api/me", headers=auth("e")).json()["id"] == "editor"


def test_demo_is_not_training_gold(client):
    assert client.get("/api/export").text == ""
    assert client.post("/api/collect").status_code == 409
    assert client.get("/").status_code == 200


def test_review_correct_adjudicate_export(client, secured):
    data = install_analysis(client, secured)
    finding = deepcopy(data["analysis"]["payload"]["findings"][0])
    finding["explanation_tr"] = "İnsan incelemesi: aynı olayın farklı yönleri başlıklarda öne çıkarılmış."
    r = secured.post(
        "/api/analyses/analysis/reviews",
        headers=auth("e"),
        json={
            "finding_id": "f1",
            "verdict": "correct",
            "replacement": finding,
            "notes": "Metinsel kanıtları kontrol edip açıklamayı düzelttim.",
        },
    )
    assert r.status_code == 201, r.text
    review_id = r.json()["id"]
    assert secured.get("/api/export", headers=auth("e")).text == ""
    body = {"review_id": review_id, "notes": "Kanıt ve alternatif açıklama bağımsız olarak incelendi."}
    assert secured.post("/api/analyses/analysis/decisions", headers=auth("e"), json=body).status_code == 409
    assert secured.post("/api/analyses/analysis/decisions", headers=auth("r"), json=body).status_code == 403
    assert secured.post("/api/analyses/analysis/decisions", headers=auth("f"), json=body).status_code == 201
    exported = secured.get("/api/export", headers=auth("e")).json()
    assert exported["target"]["finding"]["explanation_tr"] == finding["explanation_tr"]
    assert exported["review"]["reviewer"] != exported["review"]["editor"]
    with secured.app.state.sessions() as s:
        original = s.get(Analysis, "analysis")
        assert original.payload["findings"][0]["explanation_tr"] != finding["explanation_tr"]


def test_missed_finding_and_fabricated_replacement(client, secured):
    data = install_analysis(client, secured)
    finding = deepcopy(data["analysis"]["payload"]["findings"][1])
    finding["id"] = "missed"
    body = {
        "finding_id": "missed",
        "verdict": "add",
        "replacement": finding,
        "notes": "Modelin kaçırdığı ifadeyi ekledim.",
    }
    r = secured.post("/api/analyses/analysis/reviews", json=body, headers=auth("r"))
    assert r.status_code == 201
    finding["evidence"][0]["quote"] = "Uydurulmuş kanıt"
    assert secured.post("/api/analyses/analysis/reviews", json=body, headers=auth("r")).status_code == 422


def test_rejection_overrides_old_approval(client, secured):
    install_analysis(client, secured)
    for verdict in ["accept", "reject"]:
        r = secured.post(
            "/api/analyses/analysis/reviews",
            headers=auth("r"),
            json={
                "finding_id": "f1",
                "verdict": verdict,
                "notes": "Kanıt incelemesi sonrasında verilen karar.",
            },
        )
        assert r.status_code == 201
        d = secured.post(
            "/api/analyses/analysis/decisions",
            headers=auth("e"),
            json={"review_id": r.json()["id"], "notes": "Son incelemeyi yeniden değerlendirdim."},
        )
        assert d.status_code == 201
    rows = secured.get("/api/export", headers=auth("e")).text.strip().splitlines()
    assert len(rows) == 1
    import json

    assert json.loads(rows[0])["target"]["finding"] is None


def test_candidates_and_uncertain_not_positive_gold(client, secured):
    install_analysis(client, secured)
    for fid, verdict in [("f3", "accept"), ("f1", "uncertain")]:
        r = secured.post(
            "/api/analyses/analysis/reviews",
            headers=auth("r"),
            json={"finding_id": fid, "verdict": verdict, "notes": "Ek bilgi gerekli; belirsizlik korunmalı."},
        )
        secured.post(
            "/api/analyses/analysis/decisions",
            headers=auth("e"),
            json={"review_id": r.json()["id"], "notes": "Kanıtın yeterli olmadığını değerlendirdim."},
        )
    assert secured.get("/api/export", headers=auth("e")).text == ""


def test_wrong_analysis_decision_and_unknown_review(secured, client):
    install_analysis(client, secured)
    r = secured.post(
        "/api/analyses/analysis/reviews",
        headers=auth("r"),
        json={"finding_id": "f1", "verdict": "accept", "notes": "Kanıt bulguyu destekliyor."},
    )
    body = {"review_id": r.json()["id"], "notes": "Farklı bir analize bağlanamaz."}
    assert secured.post("/api/analyses/wrong/decisions", headers=auth("e"), json=body).status_code == 422
    assert (
        secured.post(
            "/api/analyses/missing/reviews",
            headers=auth("r"),
            json={"finding_id": "f1", "verdict": "accept", "notes": "Kanıt bulguyu destekliyor."},
        ).status_code
        == 404
    )


def test_csrf_and_body_limit(secured):
    assert (
        secured.post(
            "/api/events", headers={**auth("e"), "Origin": "https://evil.example"}, json={}
        ).status_code
        == 403
    )
    assert secured.post("/api/events", headers=auth("e"), content=b"x" * 3_000_001).status_code == 413


def test_manual_import_preserves_scope_and_deduplicates(secured):
    article = {
        "source_id": "one",
        "source_name": "Birinci",
        "title": "Örnek haber",
        "text": "Bu haber bir olayın açıklamasını içeriyor.",
        "url": "https://example.org/a",
    }
    body = {
        "title": "Ortak olay",
        "articles": [article, {**article, "source_id": "two", "url": "https://example.org/b"}],
    }
    r = secured.post("/api/events", headers=auth("e"), json=body)
    assert r.status_code == 201
    event = secured.get("/api/events/" + r.json()["id"], headers=auth("e")).json()
    assert event["articles"][0]["content_scope"] == "provided_excerpt"
    assert secured.post("/api/events", headers=auth("e"), json=body).status_code == 409
    with secured.app.state.sessions() as s:
        assert len(s.scalars(select(NewsEvent)).all()) == 1  # failed import rolled back


def test_content_revision_changes_cache_and_marks_stale(client):
    old = client.get("/api/events/demo-event").json()
    settings = Settings()
    key = fingerprint(old["articles"], settings, "sensitive")
    with client.app.state.sessions.begin() as s:
        a = s.get(Article, "demo-0")
        a.text += " Güncellenen haber."
        a.content_hash = hashlib.sha256(a.text.encode()).hexdigest()
    new = client.get("/api/events/demo-event").json()
    assert new["analysis"]["stale"] is True
    assert fingerprint(new["articles"], settings, "sensitive") != key
    assert "Güncellenen haber." not in new["analysis"]["snapshots"][0]["text"]
