import asyncio
import json
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from mediabias.config import Settings
from mediabias.model import Analyzer


def test_production_pipeline_two_passes_and_identity_blinding(client):
    a = client.get("/api/events/demo-event").json()["analysis"]
    analyzer = Analyzer(Settings(llm_model="configured-model", llm_api_key="test-key"))
    analyzer.complete = AsyncMock(side_effect=[deepcopy(a["payload"]), deepcopy(a["payload"])])
    result = asyncio.run(analyzer.analyze(a["snapshots"]))
    assert analyzer.complete.await_count == 2
    sent = json.loads(analyzer.complete.call_args_list[0].args[0][1]["content"])
    assert all("source_name" not in item and "url" not in item for item in sent["articles"])
    assert all(item["source"].startswith("Kaynak ") for item in sent["articles"])
    assert result["findings"][0]["evidence"][0]["start"] is not None


def test_verifier_cannot_invent_evidence(client):
    a = client.get("/api/events/demo-event").json()["analysis"]
    bad = deepcopy(a["payload"])
    bad["findings"][0]["evidence"][0]["quote"] = "Hiçbir kaynakta bulunmayan söz"
    analyzer = Analyzer(Settings(llm_model="configured-model", llm_api_key="test-key"))
    analyzer.complete = AsyncMock(side_effect=[a["payload"], bad])
    with pytest.raises(ValueError):
        asyncio.run(analyzer.analyze(a["snapshots"]))


def test_balanced_mode_keeps_links_consistent(client):
    a = client.get("/api/events/demo-event").json()["analysis"]
    analyzer = Analyzer(Settings())
    analyzer.complete = AsyncMock(return_value=a["payload"])
    result = asyncio.run(analyzer.analyze(a["snapshots"], "balanced"))
    assert all(f["status"] == "supported" for f in result["findings"])
    assert all(set(d["finding_ids"]) <= {f["id"] for f in result["findings"]} for d in result["differences"])
