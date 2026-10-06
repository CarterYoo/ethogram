"""Work store next to an index (`<index>.work`): LLM cache, node summaries, behaviour tags and the hypothesis ledger.
Kept separate so rebuilding the index never loses investigation work."""
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_cache(key TEXT PRIMARY KEY, model TEXT, effort TEXT, created TEXT, seconds REAL,
                                     response TEXT);
CREATE TABLE IF NOT EXISTS summaries(actor TEXT, segment_id INTEGER, created TEXT, model TEXT, refs TEXT, result TEXT,
                                     grounded REAL, PRIMARY KEY(actor, segment_id));
-- Hypothesis ledger. The plan is fixed when the hypothesis is proposed (pre-registration).
CREATE TABLE IF NOT EXISTS hypotheses(
  id INTEGER PRIMARY KEY, created TEXT, source TEXT, parent INTEGER, signal TEXT,
  statement TEXT, rationale TEXT, confirm_if TEXT, refute_if TEXT, alternatives TEXT,
  plan TEXT,                     -- JSON {metric, params, expect:{field?, op, value}}
  status TEXT,                   -- registered | measured | investigated | judged
  metric_result TEXT, metric_pass INTEGER,
  evidence TEXT,                 -- JSON [{event_id, stance, note, verified}]
  verdict TEXT, confidence TEXT, judge_notes TEXT, gate_notes TEXT, updated TEXT,
  round INTEGER, plain TEXT, plain_note TEXT, plain_reason TEXT,
  basis TEXT,                    -- JSON [event ids] that led to the hypothesis (from cards, storylines, change points)
  verifiers TEXT,                -- JSON {role: {summary, evidence}} from the independent verifier agents
  replication TEXT);             -- JSON: the metric re-run on each half of the window (code)
-- Event notes from the LLM (tags.py); addressed_to becomes llm_text relations on the next build.
CREATE TABLE IF NOT EXISTS tags(event_id TEXT PRIMARY KEY, tags TEXT, other TEXT, summary TEXT, addressed_to TEXT,
                                signed_as TEXT, confidence TEXT, model TEXT, created TEXT, responds_to TEXT,
                                continues TEXT, claims TEXT, stated_goal TEXT, version INTEGER);
CREATE TABLE IF NOT EXISTS event_tags(event_id TEXT, tag TEXT, PRIMARY KEY(event_id, tag));
CREATE INDEX IF NOT EXISTS ix_event_tags_tag ON event_tags(tag);
-- Cards written by the LLM from notes + code stats (cards.py); text is the fixed-format rendering, issues come from
-- code verification.
CREATE TABLE IF NOT EXISTS cards(kind TEXT, key TEXT, version INTEGER, created TEXT, stats TEXT, card TEXT, text TEXT,
                                 issues TEXT, PRIMARY KEY(kind, key));
-- Alignment lens (alignment.py): observable concern flags per event, and which events were read.
CREATE TABLE IF NOT EXISTS concerns(event_id TEXT, kind TEXT, quote TEXT, confidence TEXT);
CREATE INDEX IF NOT EXISTS ix_concerns_event ON concerns(event_id);
CREATE INDEX IF NOT EXISTS ix_concerns_kind ON concerns(kind);
CREATE TABLE IF NOT EXISTS concern_done(event_id TEXT PRIMARY KEY);
CREATE VIRTUAL TABLE IF NOT EXISTS cards_fts USING fts5(kind UNINDEXED, key UNINDEXED, text);
"""


def open_work(db_path):
    con = sqlite3.connect(db_path + ".work", timeout=60, check_same_thread=False)
    con.row_factory = sqlite3.Row
    # WAL: readers (query tools, the explorer, worker threads) never block the writer and vice versa
    if con.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
        con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    hcols = {r[1] for r in con.execute("PRAGMA table_info(hypotheses)")}
    for col, typ in (("round", "INTEGER"), ("plain", "TEXT"), ("plain_note", "TEXT"), ("plain_reason", "TEXT"), ("basis", "TEXT"),
                     ("verifiers", "TEXT"), ("replication", "TEXT")):
        if col not in hcols:  # ledgers made before multi-agent verification
            con.execute(f"ALTER TABLE hypotheses ADD COLUMN {col} {typ}")
    have = {r[1] for r in con.execute("PRAGMA table_info(tags)")}
    for col, typ in (("responds_to", "TEXT"), ("continues", "TEXT"), ("claims", "TEXT"), ("stated_goal", "TEXT"),
                     ("version", "INTEGER")):
        if col not in have:  # work stores made before notes v2
            con.execute(f"ALTER TABLE tags ADD COLUMN {col} {typ}")
    ccols = {r[1] for r in con.execute("PRAGMA table_info(concerns)")}
    for col in ("basis", "unconfirmed"):
        if col not in ccols:  # concern flags made before flags said how far their evidence goes
            con.execute(f"ALTER TABLE concerns ADD COLUMN {col} TEXT")
    return con

