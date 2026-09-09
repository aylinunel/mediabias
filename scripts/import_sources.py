"""Import only public endpoints and dated observations, never ideological priors."""

import argparse
import json
from pathlib import Path

import openpyxl


def convert(path):
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    sources = {}
    for row in list(workbook["Kayıt Defteri"].values)[1:]:
        name, slug, url, *_ = row
        if slug:
            sources[slug] = {"id": slug, "name": name, "website": url, "feeds": []}
    for row in list(workbook["Canlı Akışlar"].values)[1:]:
        name, slug, category, url, status, count, recent, last = row
        if slug not in sources:
            sources[slug] = {"id": slug, "name": name, "website": None, "feeds": []}
        sources[slug]["feeds"].append(
            {
                "url": url,
                "category": category,
                "provided_status": status,
                "provided_latest": str(last) if last else None,
                "enabled": status == "canlı" and url.startswith("https://"),
            }
        )
    return {
        "version": 1,
        "observed_at": "2026-09-08T22:38:00Z",
        "origin": "Turk_Haber_Analizi_Kaynak_Listesi.xlsx",
        "note": "Provided observations, not a current feed audit. Political and visitor metadata intentionally excluded from model inputs.",
        "sources": list(sources.values()),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("workbook")
    parser.add_argument("output")
    args = parser.parse_args()
    Path(args.output).write_text(json.dumps(convert(args.workbook), ensure_ascii=False, indent=2) + "\n")
