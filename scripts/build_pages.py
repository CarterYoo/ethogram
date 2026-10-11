"""Build the web demo as static files for GitHub Pages or any static host: the behaviour atlas (`/features`) of the
datasets deploy/serve.sh serves, as `serve --share` shows them.

    .venv/bin/python scripts/build_pages.py [DATA_DIR] [OUT_DIR]      (defaults: ../data, dist/pages/ethogram)

Every answer the page asks the server for is written to a file under OUT_DIR/data, and a small script added to the
page (scripts/pages_shim.js) answers its requests from those files, so the atlas runs with no server, from any path.
Share mode applies to all of it, as on a shared server: technical detail in the records is withheld, and nothing can
start an agent run. The indexes are copied to a temporary folder first, so building never writes to DATA_DIR.
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from swarmgraph import agentview, features as FE, query as Q, web  # noqa: E402
from swarmgraph.redact import redact_obj  # noqa: E402

DATASETS = ("wiki_all", "aiv-julaug")  # as deploy/serve.sh serves them; the first is the default
SHARDS = 32  # event texts are split over this many files, so reading a stretch loads one or two small ones
TEXT = 900  # characters kept of an event's text, after redaction (the server shows up to 1500)
REPO = "https://github.com/CarterYoo/ethogram"


def copy(src, dst):
    """a consistent copy of an SQLite file (whatever is still in its write-ahead log included)"""
    a, b = sqlite3.connect(src), sqlite3.connect(dst)
    a.backup(b)
    a.close()
    b.close()


def shard(event_id):
    """FNV-1a over the id's characters, as pages_shim.js computes it"""
    h = 2166136261
    for ch in event_id:
        h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
    return h % SHARDS


def write(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f, separators=(",", ":"), ensure_ascii=False, default=str)


def answer(db, route, **q):
    """what `serve --share` answers for /api/ROUTE?Q"""
    con = Q.connect(db)
    work = None if route == "feature_storylines" else web.open_work(db)
    try:
        out = web.ROUTES["/api/" + route](con, work, {k: [str(v)] for k, v in q.items()})
        return redact_obj(json.loads(json.dumps(out, default=str)))
    finally:
        con.close()
        if work is not None:
            work.close()


def dataset(db, out):
    """write one dataset's answers to OUT; returns its label"""
    os.makedirs(os.path.join(out, "events"))
    for route in ("features", "feature_storylines", "spread", "influence", "flow"):
        write(os.path.join(out, route + ".json"), answer(db, route))
    with open(os.path.join(out, "features.json")) as f:
        ids = [x["id"] for x in json.load(f)["features"]]
    write(os.path.join(out, "flow_cards.json"), {i: answer(db, "flow", id=i) for i in ids})

    con = Q.connect(db)
    try:
        label = (con.execute("SELECT value FROM meta WHERE key='name'").fetchone() or [None])[0]
        # the map's click-through: each stretch's first events, as /api/spread?id= gives them
        stretches = {str(u["id"]): {"ids": u["ids"][:12], "n": len(u["ids"])} for u in FE.units(con)[1]}
        with open(os.path.join(out, "feature_storylines.json")) as f:
            story = json.load(f)
        scenes = [e for s in story.get("stories") or [] for c in s.get("scenes") or [] for e in (c.get("events") or [])[:12]]
        need = list(dict.fromkeys([i for s in stretches.values() for i in s["ids"]] + scenes))
        parts = [{} for _ in range(SHARDS)]
        for i in need:
            if not i.isascii():
                raise SystemExit(f"event id {i!r} is not ASCII: the page's shard() would not find it")
            src = agentview.sources(con, [i])
            if not src:
                continue
            s = redact_obj(src[0])  # redact the whole text first, as the server does, then shorten it
            if len(s["text"]) > TEXT:
                s["text"] = s["text"][:TEXT].rstrip() + "…"
            parts[shard(i)][i] = s
    finally:
        con.close()
    write(os.path.join(out, "stretches.json"), stretches)
    for k, part in enumerate(parts):
        write(os.path.join(out, "events", f"{k}.json"), part)
    print(f"  {os.path.basename(out)}: {len(ids)} behaviours, {len(stretches)} stretches, "
          f"{sum(map(len, parts))} event texts", flush=True)
    return label


def page(out):
    """the atlas page, with the static answers wired in and a link to the code"""
    with open(os.path.join(ROOT, "swarmgraph", "features.html")) as f:
        html = f.read()
    with open(os.path.join(HERE, "pages_shim.js")) as f:
        shim = f.read()
    link = (f'<a href="{REPO}" target="_blank" rel="noopener" style="font-size:13px;color:var(--fg);text-decoration:none;'
            'border:1px solid var(--line);background:var(--panel);border-radius:8px;padding:4px 10px">Code</a>')
    for old, new in (('<button id="info"', link + '<button id="info"'),
                     ("<script>", "<script>\n" + shim + "</script>\n<script>"),
                     ("<title>", '<meta name="description" content="Ethogram: the behavior atlas of two multi-agent '
                                 'records, with the behaviors in natural language, how they rise and fall, and which '
                                 'bring on which.">\n<title>')):
        if old not in html:
            raise SystemExit(f"features.html has changed: no {old!r} to build on")
        html = html.replace(old, new, 1)
    with open(os.path.join(out, "index.html"), "w") as f:
        f.write(html)
    open(os.path.join(out, ".nojekyll"), "w").close()


def main(data, out):
    web.SHARE = True  # as `serve --share`: reviewed storylines only, no agent runs
    if os.path.exists(out):
        shutil.rmtree(out)
    os.makedirs(os.path.join(out, "data"))
    listing = []
    with tempfile.TemporaryDirectory() as tmp:
        for name in DATASETS:
            db = os.path.join(tmp, name + ".sqlite")
            for suffix in ("", ".work"):
                copy(os.path.join(data, name + ".sqlite" + suffix), db + suffix)
            label = dataset(db, os.path.join(out, "data", name))
            listing.append({"id": name, "label": label or name, "shards": SHARDS})
    write(os.path.join(out, "data", "datasets.json"), listing)
    page(out)
    size = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(out) for f in fs)
    print(f"demo: {out} ({size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main(os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "..", "data")),
         os.path.abspath(sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, "dist", "pages", "ethogram")))
