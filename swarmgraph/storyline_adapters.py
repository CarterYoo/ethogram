"""Dataset boundary for narrated atlas runs. Adapters supply data; they never generate stories.

For incident stories implement ``load_atlas()``, ``scan_events(limit)``, and
``reading_leads()``. The legacy overview also uses ``fetch_events(ids)`` and
``load_prior_story()``. Shared formats are documented in STORYLINE_HARNESS.md.
"""
import copy
import json
import sqlite3


class SQLiteAtlasAdapter:
    def __init__(self, con):
        self.con = con

    def load_atlas(self):
        from . import features
        return (features.get(self.con) or {}).get("atlas")

    def load_prior_story(self):
        from . import storyline
        return storyline.get(self.con, share=True) or {}

    def fetch_events(self, ids):
        if not ids:
            return []
        sql = "SELECT id, ts, actor, kind, text FROM events WHERE id IN (" + ",".join("?" for _ in ids) + ")"
        return [dict(row) for row in self.con.execute(sql, ids)]

    def scan_events(self, limit=50000):
        """Freeze raw incident evidence independently of the reviewed atlas sample."""
        columns = {r[1] for r in self.con.execute("PRAGMA table_info(events)")}
        names = ("id", "ts", "actor", "kind", "text", "channel", "reply_to", "meta")
        selection = ",".join(name if name in columns else "NULL AS " + name for name in names)
        rows = [dict(r) for r in self.con.execute(
            "SELECT " + selection + " FROM events ORDER BY ts,id LIMIT ?", (limit,))]
        total = self.con.execute("SELECT count(*) FROM events").fetchone()[0]
        try:
            notes = "\n".join(str(r[0]) for r in self.con.execute(
                "SELECT value FROM meta WHERE key IN ('description','notes') ORDER BY key"))
        except sqlite3.OperationalError:
            notes = "Source actor labels and reply fields have not been independently interpreted."
        return {"events": rows, "total": total, "notes": notes}

    def reading_leads(self):
        """Existing chunk readings are retrieval leads, never proof of an incident."""
        from .sweep import SWEEP_Q
        try:
            rows = self.con.execute(
                "SELECT result FROM w.chunk_reads WHERE qkey=? ORDER BY chunk_id", (SWEEP_Q,)).fetchall()
        except sqlite3.OperationalError:
            return []
        leads = []
        for row in rows:
            try:
                reading = json.loads(row[0])
            except (ValueError, TypeError):
                continue
            for category in ("concerns", "did", "said"):
                for item in reading.get(category, []):
                    if not isinstance(item, dict) or (category == "said" and item.get("record") != "contradicted"):
                        continue
                    ids = item.get("events") or ([item["event"]] if item.get("event") else [])
                    text = item.get("what") or item.get("claim") or ""
                    for eid in ids[:2]:
                        if isinstance(eid, str):
                            leads.append({"event": eid, "summary": text, "category": category})
        return leads


class MemoryAtlasAdapter:
    """A plain-Python boundary for files, connectors, and small fixtures; no SQLite required."""
    def __init__(self, atlas, events, prior_story=None, *, notes="", reading_leads=None):
        self.atlas = copy.deepcopy(atlas)
        self.events = {row["id"]: copy.deepcopy(row) for row in events}
        self.prior = copy.deepcopy(prior_story or {})
        self.notes = str(notes)
        self.leads = copy.deepcopy(reading_leads or [])

    def load_atlas(self):
        return copy.deepcopy(self.atlas)

    def load_prior_story(self):
        return copy.deepcopy(self.prior)

    def fetch_events(self, ids):
        return [copy.deepcopy(self.events[eid]) for eid in ids if eid in self.events]

    def scan_events(self, limit=50000):
        rows = sorted(self.events.values(), key=lambda row: (row.get("ts", ""), row["id"]))
        return {"events": copy.deepcopy(rows[:limit]), "total": len(rows), "notes": self.notes}

    def reading_leads(self):
        return copy.deepcopy(self.leads)
