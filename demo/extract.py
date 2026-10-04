"""Pull the real numbers and lines the demo video shows from the wiki index (run once; writes demo/data.js).

    .venv/bin/python demo/extract.py ../data/wiki_all.sqlite
"""
import json
import os
import random
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from swarmgraph import features as FE, query as Q  # noqa: E402
from swarmgraph.redact import redact  # noqa: E402

db = sys.argv[1]
PIVOT = {"like": "%R3 due in ~1m wall by your mapping%", "hl": "R3 due in ~1m wall by your mapping", "feature": "F41"}
con = Q.connect(db)
p = FE.page(con)
arc = json.loads(sqlite3.connect(db + ".work").execute("SELECT result FROM stories WHERE kind='arc'").fetchone()[0])
P = FE.spread(con)
label = dict(con.execute("SELECT id, label FROM actors"))
kind = dict(con.execute("SELECT id, kind FROM actors"))
bins, n_bin = p["bins"], p["n_bin"]

st = sorted(P["stretches"], key=lambda r: r[1])
j, day_work = 0, []
for b in bins:  # share of the work done by the start of each day, as on the page
    while j < len(st) and st[j][7][:10] < b:
        j += 1
    day_work.append(round(j / max(1, len(st) - 1), 4))

feats = []
for f in p["features"]:
    if f["id"] not in p["fpos"]:
        continue
    u = p["profiles"][f["id"]]["units"]
    daily = []
    for k in range(len(bins)):
        lo, hi = max(0, k - 1), min(len(bins) - 1, k + 1)
        c, n = sum(u[lo:hi + 1]), sum(n_bin[lo:hi + 1])
        daily.append(round(c / n, 4) if n else 0)
    peak = max(range(len(bins)), key=lambda k: (u[k] / n_bin[k]) if n_bin[k] >= 10 else -1)
    feats.append({"id": f["id"], "name": p["short"][f["id"]], "text": f["text"], "x": p["fpos"][f["id"]][0], "y": p["fpos"][f["id"]][1],
                  "peak": day_work[peak], "daily": daily, "all": round(sum(u) / max(1, sum(n_bin)), 4), "units": f["units"],
                  "kappa": (f.get("reliability") or {}).get("kappa")})

# stretches and traces: the strongest traces and a spread over time, their ends, and a sample of the rest
rng = random.Random(7)
order = {r[0]: k for k, r in enumerate(st)}
edges = sorted(P["edges"], key=lambda e: -e[2])
pick = edges[:160] + rng.sample(edges[160:], min(180, max(0, len(edges) - 160)))
ends = {i for e in pick for i in e[:2]}
others = [i for i in range(len(P["stretches"])) if i not in ends]
keep = sorted(ends | set(rng.sample(others, min(900, len(others)))))
pos = {i: k for k, i in enumerate(keep)}
stretches = [{"x": P["stretches"][i][2], "y": P["stretches"][i][3], "w": round(order[P["stretches"][i][0]] / max(1, len(st) - 1), 4),
              "home": P["stretches"][i][4]} for i in keep]
traces = [{"s": pos[a], "d": pos[b], "n": n} for a, b, n in pick]
outdeg = {}
for a, b, n in P["edges"]:
    outdeg.setdefault(a, []).append(b)
src = max(outdeg, key=lambda a: len(set(outdeg[a])))
cascade = {"n": len(set(outdeg[src])), "w": [round(order[P["stretches"][b][0]] / max(1, len(st) - 1), 4) for b in sorted(set(outdeg[src]))][:28]}

# lines for the flood: plain sentences only. Anything with links, markup, code, request methods, paths, encodings or
# security words is left out, as is anything the share-mode redaction would change; at most two per author label.
risky = re.compile(r"https?|www|\.(com|org|net|io|de|at|gov)\b|[<>{}=;$`\\|\[\]]|\.\./|\b(GET|POST|REDIRECT)\b|ignore|instruction|"
                   r"inject|payload|token|password|bypass|exploit|hack|admin|script|base64|proxy|curl|api|secret|key|probe", re.I)
rows = con.execute("SELECT actor, text FROM events WHERE kind='message'").fetchall()
rng.shuffle(rows)
lines, per = [], {}
for actor, t in rows:
    t = " ".join(re.sub(r"^\[edit summary: [^\]]*\]\s*", "", t or "").split())
    if risky.search(t) or redact(t) != t or not (40 <= len(t) <= 140) or not t.isascii():
        continue
    if sum(c.isalpha() for c in t) / len(t) < .72 or kind.get(actor) == "system":
        continue
    who = label.get(actor, actor)
    if per.get(who, 0) >= 2 or "." in who or "[" in who:  # sites standing for unknown authors, withheld people
        continue
    per[who] = per.get(who, 0) + 1
    lines.append({"who": who, "t": t})
    if len(lines) >= 56:
        break

pv = con.execute("SELECT ts, actor, text FROM events WHERE text LIKE ? ORDER BY ts", (PIVOT["like"],)).fetchone()
pt = " ".join(re.sub(r"^\[edit summary: [^\]]*\]\s*", "", pv[2]).split())
pt = re.sub(r"\s*--\s*\S+\s*$", "", pt)  # the trailing signature
pivot = {"who": label.get(pv[1]), "when": pv[0][:10], "t": pt, "hl": PIVOT["hl"], "feature": PIVOT["feature"]}
assert PIVOT["hl"] in pt

labels = [label.get(a) for a, in con.execute("SELECT actor FROM events GROUP BY actor ORDER BY count(*) DESC LIMIT 140")
          if label.get(a) and kind.get(a) != "system" and "[" not in label.get(a) and redact(label.get(a)) == label.get(a)][:110]
counts = {"events": con.execute("SELECT count(*) FROM events").fetchone()[0],
          "labels": con.execute("SELECT count(DISTINCT actor) FROM events").fetchone()[0],
          "days": con.execute("SELECT count(DISTINCT substr(ts, 1, 10)) FROM events").fetchone()[0],
          "traces": len(P["edges"]), "behaviours": len(feats), "kappa": 0.89, "detection": "44 of 53"}
# what the flow layer measured: where the mix of behaviour changes, and which behaviour brings on which in others
from swarmgraph import flow as FL  # noqa: E402
fpos = {f["id"] for f in feats}
regimes = [b["date"] for b in FL.page_view(con).get("boundaries", [])]
inf = ((FE.get(con) or {}).get("influence") or {}).get("between") or {}
links, per_src = [], {}
for l in inf.get("links", []):  # strongest evidence first; at most two arrows leave one behaviour, for variety
    if l["g"] == l["f"] or l["g"] not in fpos or l["f"] not in fpos or per_src.get(l["g"], 0) >= 2:
        continue
    per_src[l["g"]] = per_src.get(l["g"], 0) + 1
    links.append({k: l[k] for k in ("g", "f", "odds", "z")})
    if len(links) >= 9:
        break
catching = [{k: l[k] for k in ("g", "odds", "z")} for l in inf.get("links", []) if l["g"] == l["f"] and l["g"] in fpos][:3]
out = {"pivot": pivot, "bins": bins, "n_bin": n_bin, "day_work": day_work, "features": feats, "stretches": stretches,
       "regimes": regimes, "influence": {"links": links, "catching": catching, "R": inf.get("R"),
                                         "significant": inf.get("significant")},
       "traces": traces, "cascade": cascade, "lines": lines, "labels": labels, "counts": counts,
       "arc": {"title": arc["title"], "phases": [{k: ph[k] for k in ("name", "start", "end")} for ph in arc["phases"]],
               "turns": [t["date"] for t in arc["turning_points"]],
               "tps": [{"date": t["date"], "title": t["title"]} for t in sorted(arc["turning_points"], key=lambda t: t["date"])]}}
open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.js"), "w").write("window.DEMO = " + json.dumps(out) + ";\n")
print({k: (len(v) if isinstance(v, list) else v) for k, v in out.items() if k not in ("arc", "pivot")}, "| pivot", pivot["who"], pivot["when"])
