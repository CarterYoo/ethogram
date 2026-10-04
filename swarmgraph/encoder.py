"""Encoder: behaviours for the stretches nobody judged, learned from the judges (docs/FLOW.md section 9).

The judges are the teacher and a cheap model is the student, as an SAE's encoder is: each stretch, rendered as the
judges read it (the lines before it in the same place, its own lines, the facts code found), becomes a vector of word
weights (TF-IDF) and a sentence embedding (BAAI/bge-small-en-v1.5, run locally through fastembed; no LLM). One
logistic regression per behaviour is fitted on the judged stretches. A behaviour is encoded only when, in five-fold
cross-validation, the student agrees with the judges at kappa >= 0.6 (the two judges agree with each other at about
0.83-0.89); its stretches are then marked where the student's probability passes the cut that reproduces the
behaviour's rate among the judged. Encoded marks are estimates: the flow and influence models use them only to see
more of what a stretch could have seen (its sources), never as the outcome they measure.

    .venv/bin/python -m swarmgraph --db INDEX features encode FEATURE_FOLDER
"""
import hashlib
import json
import os
import time

MODEL = "BAAI/bge-small-en-v1.5"
CLIP = 1500  # characters embedded (about 380 tokens): the stretch's own lines and facts, not the context before it
PASS = 0.6
MIN_POS = 40


def texts(con):
    """{stretch: (own lines and facts as the judges read them, the same plus the raw text of its events)}: the context
    lines (what others did before) are left out, since cut to fit they crowded out the stretch itself (median kappa
    0.31 with the judge's whole view cut at 1,200 characters; 0.53 with the own lines and facts, TF-IDF alone)"""
    import re
    from . import features as FE
    rows, us = FE.units(con)
    R = FE.record(con, rows)
    raw = {e["id"]: (e["kind"] + ": " + (e["text"] or "")) for e in rows}
    out = {}
    for u in us:
        own = "\n".join(l for l in FE.render(R, u, "U")[0].split("\n")[1:] if not re.match(r"\s+b\d+ ", l))
        out[u["id"]] = (own, own + "\n" + " \n".join(raw.get(i, "")[:600] for i in u["ids"][:14]))
    return out


def embed(con, folder, log=print, models_dir=None, chunk=1000):
    """embeddings of every stretch, cached in FOLDER/embeddings.npz by stretch id and text hash (resumable)"""
    import numpy as np
    from fastembed import TextEmbedding
    path = os.path.join(folder, "embeddings.npz")
    tx = {i: t[0][:CLIP] for i, t in texts(con).items()}
    ids = sorted(tx)
    h = {i: hashlib.md5(tx[i].encode()).hexdigest()[:12] for i in ids}
    have = {}
    if os.path.exists(path):
        z = np.load(path, allow_pickle=False)
        have = {int(i): (str(hh), v) for i, hh, v in zip(z["ids"], z["hash"], z["vec"]) if h.get(int(i)) == str(hh)}
    todo = [i for i in ids if i not in have]
    log(f"encoder: {len(ids)} stretches, {len(have)} embedded already, {len(todo)} to go")
    model = TextEmbedding(MODEL, cache_dir=models_dir or os.path.join(os.path.dirname(folder), "models"))
    for k in range(0, len(todo), chunk):
        part = todo[k:k + chunk]
        t = time.time()
        for i, v in zip(part, model.embed([tx[i] for i in part], batch_size=64)):
            have[i] = (h[i], np.asarray(v, np.float32))
        keep = sorted(have)
        np.savez(path, ids=np.array(keep), hash=np.array([have[i][0] for i in keep]),
                 vec=np.stack([have[i][1] for i in keep]))
        log(f"encoder: embedded {min(k + chunk, len(todo))}/{len(todo)} ({time.time() - t:.0f} s for {len(part)})")
    return ids, tx, np.stack([have[i][1] for i in ids])


def fit(db, folder, log=print):
    """cross-validated agreement with the judges per behaviour; the behaviours that pass are encoded for every
    stretch and stored as feature_atlas['encoded']"""
    import numpy as np
    from scipy import sparse
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import cohen_kappa_score, roc_auc_score
    from sklearn.model_selection import StratifiedKFold
    from . import features as FE, query as Q
    from .store import open_work
    con = Q.connect(db)
    a = FE.get(con)["atlas"]
    ids, _, E = embed(con, folder, log)
    full = {i: t[1] for i, t in texts(con).items()}
    pos = {i: k for k, i in enumerate(ids)}
    T = TfidfVectorizer(min_df=3, max_df=0.5, ngram_range=(1, 2), sublinear_tf=True,
                        max_features=80000).fit_transform([full[i] for i in ids])
    X = sparse.hstack([T, sparse.csr_matrix(E * 0.6)]).tocsr()
    judged = [u["id"] for u in a["units"]]
    J = np.array([pos[i] for i in judged])
    rest = np.array([pos[i] for i in ids if i not in set(judged)])
    unit_f = {u["id"]: u["f"] for u in a["units"]}
    kappa_j = {f["id"]: (f.get("reliability") or {}).get("kappa") for f in a["features"]}
    report, marks = {}, {}
    for f in a["features"]:
        y = np.array([1 if unit_f[i].get(f["id"]) else 0 for i in judged])
        if y.sum() < MIN_POS:
            continue
        p = np.zeros(len(y))
        for tr, te in StratifiedKFold(5, shuffle=True, random_state=0).split(J, y):
            clf = LogisticRegression(C=4.0, max_iter=3000, class_weight="balanced").fit(X[J[tr]], y[tr])
            p[te] = clf.predict_proba(X[J[te]])[:, 1]
        cut = float(np.sort(p)[-int(y.sum())])
        kap = float(cohen_kappa_score(y, (p >= cut).astype(int)))
        p2 = np.zeros(len(y))  # the same with word weights only: what the embedding adds
        for tr, te in StratifiedKFold(5, shuffle=True, random_state=0).split(J, y):
            p2[te] = LogisticRegression(C=4.0, max_iter=3000, class_weight="balanced").fit(
                T[J[tr]], y[tr]).predict_proba(T[J[te]])[:, 1]
        kap_words = float(cohen_kappa_score(y, (p2 >= np.sort(p2)[-int(y.sum())]).astype(int)))
        report[f["id"]] = {"positives": int(y.sum()), "kappa": round(kap, 3), "kappa_words_only": round(kap_words, 3),
                           "auc": round(float(roc_auc_score(y, p)), 3),
                           "judges_kappa": kappa_j.get(f["id"]), "encoded": kap >= PASS, "cut": round(cut, 4)}
        if kap >= PASS and len(rest):
            clf = LogisticRegression(C=4.0, max_iter=3000, class_weight="balanced").fit(X[J], y)
            q = clf.predict_proba(X[rest])[:, 1]
            for k in np.nonzero(q >= cut)[0]:
                marks.setdefault(int(ids[rest[k]]), []).append(f["id"])
        log(f"encoder: {f['id']} kappa {kap:.2f} auc {report[f['id']]['auc']}{' encoded' if kap >= PASS else ''}")
    out = {"model": MODEL, "made": time.strftime("%Y-%m-%d %H:%M:%S"), "pass_kappa": PASS, "behaviours": report,
           "units": {str(k): v for k, v in marks.items()},
           "unjudged": len(rest), "unjudged_marked": len(marks)}
    FE.save(open_work(db), "encoded", out)
    ok = [k for k, r in report.items() if r["encoded"]]
    return {"tested": len(report), "encoded": len(ok),
            "median_kappa": round(float(np.median([r["kappa"] for r in report.values()])), 3) if report else None,
            "median_kappa_words_only": round(float(np.median([r["kappa_words_only"] for r in report.values()])), 3)
            if report else None,
            "unjudged_marked": len(marks), "of": len(rest)}
