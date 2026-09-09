import json
from pathlib import Path

import pytest

from mediabias.evaluation import assess
from mediabias.reviews import split_for


def test_missing_required_label_is_failure():
    result = assess({"expect": {"present": ["loaded_language"]}}, {"findings": []}, {})
    assert result == [{"check": "present:loaded_language", "passed": False}]


def test_invariance_requires_successful_baseline():
    result = assess({"expect": {"same_labels_as": "missing"}}, {"findings": []}, {})
    assert not result[0]["passed"]


def test_no_vacuous_pass():
    with pytest.raises(ValueError):
        assess({"expect": {}}, {"findings": []}, {})


def test_checklist_coverage_and_split_unit():
    rows = [json.loads(x) for x in Path("eval/checklist_tr.jsonl").read_text().splitlines()]
    assert {r["type"] for r in rows} == {"MFT", "INV", "DIR"}
    assert len({r["capability"] for r in rows}) >= 8
    assert split_for("same-event") == split_for("same-event")


def test_migration_upgrade_idempotent(tmp_path):
    from sqlalchemy import inspect

    from mediabias.cli import migrate
    from mediabias.config import Settings
    from mediabias.db import database

    s = Settings(database_url=f"sqlite:///{tmp_path}/migrated.db")
    migrate(s)
    migrate(s)
    engine, _ = database(s.database_url)
    assert {"alembic_version", "articles", "analyses", "reviews", "decisions"} <= set(
        inspect(engine).get_table_names()
    )
    engine.dispose()
