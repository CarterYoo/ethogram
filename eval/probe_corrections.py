"""Feasibility probe: are self-corrections and retractions a clue class that plain text rules can list precisely?

The analysts' trajectories show the most behaviour-relevant events they found were admissions that an earlier claim was
wrong ("my earlier claim ... was a simulated estimate generated without a live web search", a count retracted from
eleven to nine and then corrected downstream), and that they found them by luck with their own regular expressions.

  python3 eval/probe_corrections.py INDEX [--kind message] [--sample 22] [--seed 5]

Prints how many messages match, by how many actors, and a random sample with the matching passage, to be read by a
person. Precision is judged by reading, not computed.
"""
import random
import re
import sqlite3
import sys

PATTERN = re.compile(
    r"(simulated (estimate|data|result)"
    r"|without (a )?(live|actual|real) (web )?(search|check|verification)"
    r"|I (was|am) (wrong|mistaken|incorrect)"
    r"|my (earlier|previous|prior|last) (claim|statement|report|message|number|estimate)[^.]{0,60}(was|were) (wrong|incorrect|inaccurate|an estimate|not|unverified|premature)"
    r"|(I|we) (fabricated|made up|hallucinated|misreported|overstated|overclaimed|mis-?stated)"
    r"|retract(ed|ing)?\b|correction:"
    r"|that was (incorrect|inaccurate|a mistake|premature)"
    r"|I (should have|shouldn't have) (verified|checked)"
    r"|not (actually|really) (verified|deployed|merged|published|pushed))", re.I)


def main():
    a = sys.argv[1:]
    opt = lambda k, d: type(d)(a[a.index(k) + 1]) if k in a else d
    kind, n, seed = opt("--kind", "message"), opt("--sample", 22), opt("--seed", 5)
    con = sqlite3.connect(f"file:{a[0]}?mode=ro", uri=True)
    rows = con.execute("SELECT id, ts, actor, text FROM events WHERE kind=?", (kind,)).fetchall()
    hits = [r for r in rows if PATTERN.search(r[3] or "")]
    print(f"{len(rows)} {kind} events, {len(hits)} match ({len(hits) / max(1, len(rows)):.2%}), {len({h[2] for h in hits})} actors")
    random.Random(seed).shuffle(hits)
    for h in hits[:n]:
        t = (h[3] or "").replace("\n", " ")
        m = PATTERN.search(t)
        print(f"- {h[1][:16]} {h[2][:8]} ...{t[max(0, m.start() - 120):m.end() + 150]}")


if __name__ == "__main__":
    main()

