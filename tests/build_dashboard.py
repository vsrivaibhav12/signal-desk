"""Rebuilds index.html with fresh sample data from the replay test.  Run:  python -m tests.build_dashboard"""
import json
import os

from tests.simulate import ROOT, main

feed, _ = main(verbose=False)
feed["status"]["day"].pop("markets", None)   # the page only needs the tiles, log and trades
feed["status"]["day"]["stocks"] = {}
page = open(os.path.join(ROOT, "index.template.html"), encoding="utf-8").read()
blob = json.dumps(feed, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
open(os.path.join(ROOT, "index.html"), "w", encoding="utf-8").write(page.replace("/*__DEMO__*/null", blob))
print("index.html written,", len(blob) // 1024, "KB of sample data,", len(feed["trades"]), "sample trades")
