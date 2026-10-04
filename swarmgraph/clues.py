"""Clues for an analyst: a one-page map of the whole log and a clue sheet per node (actor, place, day, behaviour,
concern kind), computed by code from what the LLM recorded per event (notes, behaviour tags, concern flags with
their basis). No prose verdicts: every line is a neutral observation with numbers, a baseline where one exists, the
event ids to open, and the node to open next.

The division of labour: the LLM reads single events (it understands text); code counts, compares and orders in time
(it does not round numbers or adopt a framing); the analyst — another LLM or a person — draws the conclusions.

    python3 -m swarmgraph --db INDEX call map '{}'
    python3 -m swarmgraph --db INDEX call node '{"ref": "actor:NAME"}'   # also place: day: tag: concern: authority
"""
import bisect
import collections
import json
import math
import time

from . import query as Q
from .build import epoch

QUIET = {"other", "filler"}  # behaviour tags too unspecific to be clues
DAY = 86400


def _neutral():
    from .alignment import NEUTRAL
    return NEUTRAL


# ---------------------------------------------------------------------------- data, loaded once per call

class Data:
    def __init__(self, con):
        self.con = con
        self.ev = {}  # id → (ts, actor, channel, kind)
        for i, ts, a, ch, k in con.execute("SELECT id, ts, actor, channel, kind FROM events ORDER BY ts"):
            self.ev[i] = (ts, a, ch, k)
        self.order = list(self.ev)
        self.tags = collections.defaultdict(set)
        self.summary, self.claims = {}, {}
        try:
            for i, t in con.execute("SELECT event_id, tag FROM w.event_tags"):
                self.tags[i].add(t)
            for i, s, c in con.execute("SELECT event_id, summary, claims FROM w.tags"):
                self.summary[i] = s or ""
                self.claims[i] = json.loads(c) if c else []
        except Exception:
            pass
        self.flags = collections.defaultdict(list)  # id → [(kind, basis, confidence)]
        try:
            cols = {r[1] for r in con.execute("PRAGMA w.table_info(concerns)")}
            b = "basis" if "basis" in cols else "NULL"
            for i, k, bs, c in con.execute(f"SELECT event_id, kind, {b}, confidence FROM w.concerns "
                                           "WHERE confidence <> 'low'"):
                self.flags[i].append((k, bs or "unknown", c))
        except Exception:
            pass
        self.links = []
        try:
            self.links = con.execute("SELECT src, dst, type, event_id, ts FROM w.links ORDER BY ts").fetchall()
        except Exception:
            pass
        self.n = len(self.ev)

    def feats(self, i):
        """Behaviour tags and concern kinds of one event, as one vocabulary ('tag' / 'concern:kind')."""
        return {t for t in self.tags.get(i, ()) if t not in QUIET} | {"concern:" + k for k, _, _ in self.flags.get(i, ())}

    def note(self, i, n=110):
        return Q.clip(self.summary.get