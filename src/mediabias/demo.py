"""Fictional, deterministic demonstration. Never an accuracy benchmark."""

import hashlib

from sqlalchemy import select

from .db import Analysis, Article, NewsEvent, snapshot
from .evidence import validate_comparison


def seed(sessions, limit):
    with sessions.begin() as session:
        if session.scalar(select(NewsEvent.id).limit(1)):
            return
        event = NewsEvent(
            id="demo-event", title="Aynı tarife, üç farklı çerçeve", is_demo=True, grouping="editor_reviewed"
        )
        session.add(event)
        session.flush()
        texts = [
            (
                "Kent Bülteni",
                "Ulaşımda kalite hamlesi",
                "Belediye, 1 Ekim’de bilet ücretini 25 liradan 30 liraya çıkaracağını açıkladı. Belediye Başkanı, artışın seferleri iyileştireceğini söyledi. Yeni sefer planı henüz yayımlanmadı.",
            ),
            (
                "Sokak Gazetesi",
                "Vatandaşın cebine yeni yük",
                "Belediye, 1 Ekim’de bilet ücretini 25 liradan 30 liraya çıkaracağını açıkladı. Günde iki kez, ayda 22 gün binen bir yolcu için ek maliyet 220 lira olacak. Başkan hizmetin iyileşeceğini söylüyor.",
            ),
            (
                "Veri Masası",
                "Bilet 30 lira: hizmet planı bekleniyor",
                "Belediye, 1 Ekim’de bilet ücretini 25 liradan 30 liraya çıkaracağını açıkladı. Artış yüzde 20. Belediye hizmetin iyileşeceğini belirtiyor; yeni sefer planı henüz yayımlanmadı.",
            ),
        ]
        articles = []
        for i, (name, title, text) in enumerate(texts):
            a = Article(
                id=f"demo-{i}",
                event_id=event.id,
                source_id=f"fictional-{i}",
                source_name=name,
                title=title,
                text=text,
                url=f"https://example.invalid/demo/{i}",
                content_scope="full_text",
                content_hash=hashlib.sha256((title + "\n" + text).encode()).hexdigest(),
            )
            session.add(a)
            articles.append(snapshot(a, limit))

        def ev(i, quote):
            return {"article_id": f"demo-{i}", "quote": quote}

        shared = [ev(i, "bilet ücretini 25 liradan 30 liraya çıkaracağını açıkladı.") for i in range(3)]
        findings = [
            {
                "id": "f1",
                "concept_id": "emphasis_framing",
                "layer": "media_technique",
                "status": "supported",
                "attribution": "journalist",
                "explanation_tr": "İlk başlık hizmet yararını, ikinci başlık yolcunun maliyetini öne çıkarıyor.",
                "alternative_explanation_tr": "Hedef kitlenin farklı bilgi ihtiyaçları başlık seçimini açıklayabilir.",
                "missing_context_tr": "",
                "reader_impact_tr": "Aynı artış hizmet yatırımı veya hane gideri olarak değerlendirilebilir.",
                "evidence": [ev(0, "Ulaşımda kalite hamlesi"), ev(1, "Vatandaşın cebine yeni yük")],
            },
            {
                "id": "f2",
                "concept_id": "loaded_language",
                "layer": "media_technique",
                "status": "supported",
                "attribution": "journalist",
                "explanation_tr": "“Yeni yük” ifadesi maliyeti olumsuz değerlendiren bir başlık tercihidir.",
                "alternative_explanation_tr": "Ücret artışının gerçek bütçe etkisini kısa anlatma amacı taşıyabilir.",
                "missing_context_tr": "",
                "reader_impact_tr": "Dikkati hizmet beklentisinden ödeme güçlüğüne taşıyabilir.",
                "evidence": [ev(1, "Vatandaşın cebine yeni yük")],
            },
            {
                "id": "f3",
                "concept_id": "affect_heuristic",
                "layer": "cognitive_concept",
                "status": "candidate",
                "attribution": "unclear",
                "explanation_tr": "Duygusal başlık değerlendirmeyi duygulara dayandırmaya davet edebilir; gerçek okur tepkisi gözlenmedi.",
                "alternative_explanation_tr": "Okur ayrıntılı veriyi inceleyip bağımsız bir değerlendirme yapabilir.",
                "missing_context_tr": "Okurun karar sürecine ilişkin kanıt veya deney gerekli.",
                "reader_impact_tr": "Olası duygusal tepki maliyet ve yarar tartımını etkileyebilir.",
                "evidence": [ev(1, "Vatandaşın cebine yeni yük")],
            },
        ]
        result = {
            "what_happened": [
                {
                    "text_tr": "Üç kurgu kaynak, belediyenin bilet ücretini 1 Ekim’de 25 liradan 30 liraya çıkaracağını açıkladığını aktarıyor.",
                    "evidence": shared,
                }
            ],
            "agreements": [
                {
                    "text_tr": "Kaynaklar başlangıç tarihi ve yeni ücret üzerinde aynı bilgiyi veriyor.",
                    "evidence": shared,
                }
            ],
            "differences": [
                {
                    "text_tr": "Kent Bülteni hizmeti, Sokak Gazetesi bütçe etkisini öne çıkarıyor.",
                    "evidence": findings[0]["evidence"],
                    "finding_ids": ["f1", "f2"],
                }
            ],
            "findings": findings,
            "why_care_tr": "Aylık bütçe etkisi ile vaat edilen hizmet artışı farklı karar soruları doğurur. Ücret biliniyor; hizmet kazanımını değerlendirmek için sefer planı gerekiyor.",
            "limitations_tr": [
                "Tüm kaynaklar ve olay eğitim amacıyla kurgulanmıştır.",
                "Bu örnek canlı model çıktısı veya doğruluk ölçümü değildir.",
                "Kaynakların anlaşması bağımsız doğrulama değildir.",
            ],
        }
        payload = validate_comparison(result, articles).model_dump()
        session.add(
            Analysis(
                event_id=event.id,
                cache_key="demo",
                payload=payload,
                snapshots=articles,
                provenance={"mode": "demo", "synthetic": True, "model": None},
            )
        )
