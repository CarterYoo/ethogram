"""A small learned layer on top of the alignment lens: logistic regression (standard library only) trained on the LLM's
concern flags as weak labels, from the event text, its behaviour tags and simple context (did an operator already
act on this actor or place?).

Not a detector of its own (it learns what the LLM flagged, including its mistakes); it is for
  - scale: score logs too large to read with an LLM, then send only the top-scored events to the LLM,
  - cross-checking: events where the screener and the LLM disagree are a review queue (likely misses / over-flags),
  - explanation: the heaviest features show what drives each concern.
Evaluated on held-out actors, so near-duplicate posts by one actor cannot leak between training and test.

    python3 -m swarmgraph --db INDEX screen [--kinds ...]
"""
import collections
import hashlib
import json
import math
import random
import re
import sys

from . import query as Q
from .store import open_work

DEFAULT_KINDS = ("any_except_misuse", "circumvent_restriction", "persist_after_stop", "cross_instance_sharing",
                 "security_probing", "misuse_resource")
WORD = re.compile(r"[a-z][a-z0-9_]{1,30}")


def features(text, tags, after_authority, kind):
    words = WORD.findall((text or "").lower()[:3000])
    feats = {f"w:{w}" for w in words} | {f"b:{a}_{b}" for a, b in zip(words, words[1:])}
    feats |= {f"t:{t}" for t in tags} | {f"k:{kind}"}
    if after_authority:
        feats.add("ctx:after_authority_acted")
    feats.add("bias")
    return feats


def dataset(con):
    """event → (feature names, set of concern kinds at medium/high confidence)."""
    note = {r[0]: json.loads(r[1]) for r in con.execute("SELECT event_id, tags FROM w.tags")}
    labels = collections.defaultdict(set)
    for eid, kind in con.execute("SELECT event_id, kind FROM w.concerns WHERE confidence <> 'low'"):
        labels[eid].add(kind)
    done = {r[0] for r in con.execute("SELECT event_id FROM w.concern_done")}
    removed_actor = collections.defaultdict(list)
    for dst, ts in con.execute("SELECT dst, ts FROM w.links WHERE type='removes'"):
        removed_actor[dst].append(ts)
    removed_chan = collections.defaultdict(list)
    for ch, ts in con.execute("SELECT e.channel, e.ts FROM w.event_tags t JOIN events e ON e.id=t.event_id "
                              "WHERE t.tag IN ('remove_others', 'moderate') AND e.channel IS NOT NULL"):
        removed_chan[ch].append(ts)
    rows = []
    for eid, ts, actor, ch, kind, text in con.execute("SELECT id, ts, actor, channel, kind, text FROM events"):
        after = any(t < ts for t in removed_actor.get(actor, ())) or any(t < ts for t in removed_chan.get(ch, ()))
        rows.append((eid, actor, features(text, note.get(eid, []), after, kind), labels.get(eid, set()), eid in done))
    return rows


def _label(kinds, target):
    return int(bool(kinds - {"misuse_resource"})) if target == "any_except_misuse" else int(target in kinds)


def train(X, y, epochs=6, lr=0.15, l2=1e-6, seed=0):
    w = collections.defaultdict(float)
    idx = list(range(len(X)))
    rnd = random.Random(seed)
    pos = sum(y) or 1
    wpos = max(1.0, (len(y) - pos) / pos) ** 0.5  # soften class imbalance
    for ep in range(epochs):
        rnd.shuffle(idx)
        for i in idx:
            z = sum(w[f] for f in X[i])
            p = 1 / (1 + math.exp(-max(-30, min(30, z))))
            g = (p - y[i]) * (wpos if y[i] else 1.0) * lr / (1 + ep)
            for f in X[i]:
                w[f] -= g + l2 * w[f]
    return w


def score(w, x):
    z = sum(w.get(f, 0.0) for f in x)
    return 1 / (1 + math.exp(-max(-30, min(30, z))))


def auc(scores, y):
    pairs = sorted(zip(scores, y))
    pos = sum(y)
    neg = len(y) - pos
    if not pos or not neg:
        return None
    rank_sum, r = 0.0, 0
    for i, (_, lab) in enumerate(pairs, 1):
        if lab:
            rank_sum += i
    return round((rank_sum - pos * (pos + 1) / 2) / (pos * neg), 3)


def run(db, kinds=DEFAULT_KINDS, log=lambda m: print(m, file=sys.stderr, flush=True)):
    con, work = Q.connect(db), open_work(db)
    rows = [r for r in dataset(con) if r[4]]  # only events the LLM has read
    test_actor = lambda a: int(hashlib.md5(a.encode()).hexdigest(), 16) % 5 == 0
    tr = [r for r in rows if not test_actor(r[1])]
    te = [r for r in rows if test_actor(r[1])]
    log(f"screener: {len(tr):,} training events, {len(te):,} held-out events (by actor)")
    work.execute("CREATE TABLE IF NOT EXISTS screen_scores(event_id TEXT, kind TEXT, score REAL, llm INTEGER, "
                 "PRIMARY KEY(event_id, kind))")
    work.execute("CREATE TABLE IF NOT EXISTS screen_model(kind TEXT PRIMARY KEY, report TEXT)")
    report = {}
    for kind in kinds:
        ytr, yte = [_label(r[3], kind) for r in tr], [_label(r[3], kind) for r in te]
        if sum(ytr) < 10:
            log(f"  {kind}: too few positives ({sum(ytr)}), skipped")
            continue
        w = train([r[2] for r in tr], ytr)
        s_te = [score(w, r[2]) for r in te]
        k = max(1, sum(yte))
        top = sorted(range(len(te)), key=lambda i: -s_te[i])[:k]
        prec_at_k = round(sum(yte[i] for i in top) / k, 3) if yte else None
        tp = sum(1 for s, l in zip(s_te, yte) if s >= .5 and l)
        fp = sum(1 for s, l in zip(s_te, yte) if s >= .5 and not l)
        fn = sum(1 for s, l in zip(s_te, yte) if s < .5 and l)
        heavy = sorted(((v, f) for f, v in w.items() if not f.startswith("bias")), reverse=True)[:15]
        full = [score(w, r[2]) for r in rows]
        work.executemany("INSERT OR REPLACE INTO screen_scores VALUES (?,?,?,?)",
                         [(r[0], kind, round(sc, 4), _label(r[3], kind)) for r, sc in zip(rows, full)])
        # disagreements: confident screener vs the LLM's label → a review queue
        miss = sorted(((sc, r[0]) for r, sc in zip(rows, full) if sc >= .9 and not _label(r[3], kind)), reverse=True)[:10]
        over = sorted(((sc, r[0]) for r, sc in zip(rows, full) if sc <= .05 and _label(r[3], kind)))[:10]
        report[kind] = {"positives_train": sum(ytr), "positives_test": sum(yte), "test_events": len(te),
                        "auc": auc(s_te, yte), "precision_at_k": prec_at_k,
                        "precision": round(tp / (tp + fp), 3) if tp + fp else None,
                        "recall": round(tp / (tp + fn), 3) if tp + fn else None,
                        "top_features": [[f, round(v, 2)] for v, f in heavy],
                        "possible_llm_misses": [e for _, e in miss], "possible_llm_overflags": [e for _, e in over]}
        work.execute("INSERT OR REPLACE INTO screen_model VALUES (?,?)", (kind, json.dumps(report[kind])))
        work.commit()
        log(f"  {kind}: AUC {report[kind]['auc']}, precision@k {prec_at_k}, recall {report[kind]['recall']} "
            f"({sum(yte)} held-out positives)")
    return report

