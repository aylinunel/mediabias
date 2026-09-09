"""Fail CI on silent source/taxonomy loss or teaching/gold confusion."""

import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
data = root / "src/mediabias/data"
register = json.loads((data / "sources.json").read_text())
sources = register["sources"]
feeds = [f for s in sources for f in s["feeds"]]
assert len(sources) == 53 and len({s["id"] for s in sources}) == 53
assert len(feeds) == 118
assert sum(f["enabled"] for f in feeds) == 71
assert sum(any(f["enabled"] for f in s["feeds"]) for s in sources) == 26
assert all(f["url"].startswith("https://") for f in feeds if f["enabled"])
assert all(set(s) == {"id", "name", "website", "feeds"} for s in sources)
taxonomy = json.loads((data / "taxonomy.json").read_text())["entries"]
assert len(taxonomy) == len({e["id"] for e in taxonomy}) == 122
teaching = [json.loads(line) for line in (data / "teaching_examples.jsonl").read_text().splitlines()]
assert len(teaching) == 244 and all(r["synthetic"] and not r["adjudicated"] for r in teaching)
print("53 outlets, 118 feeds, 122 concepts, 244 explicitly synthetic teaching examples validated.")
