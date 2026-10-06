# Checking claims on this log

Three tools, run as `python3 -m swarmgraph --db log.sqlite call <tool> '<json>'` (JSON out). They search the events
for you and measure a claim; nothing here interprets the log for you.

**grep** `{"patterns": ["regex", ...], "text": "text|raw|summary", "examples": 8}`
Events matching ANY pattern (Python regex, case-insensitive; "text" = note summary || raw text, "raw" = raw text only).
Gives the exact count, how many actors and days, the busiest day/actor share, and examples spread evenly over time with
different actors — not the first ones. Use it to see whether something recurs, and across what, before you build on it.

**context** `{"event_id": "...", "before": 4, "after": 4, "scope": "actor|place"}`
The events around one event: the same actor's neighbours, or the same place's. Read a sequence, not a single event.

**test_claim** `{"shape": "after_then", "a_patterns": [...], "b_patterns": [...], "scope": "same_actor|target|others_same_place", "window_minutes": 90}`
After an event matching A, does an event matching B follow within the window more often than in comparable windows
elsewhere (same actors, only windows where they are active)? Other shapes: `spreads` (A moves between actors along
contact; a_patterns only) and `co_occurs` (actors who do A also do B). Returns a plain `reading` with the number of
cases, the two halves (earlier/later in time; for co_occurs two fixed groups of actors) and a confidence bound, `pairs_to_read` (event pairs behind the number),
`pattern_problems` (a pattern too narrow or too broad) and a verdict: **holds**, **not_holds** or **insufficient**
(too little data; not a pass).

How to use: turn a hypothesis into A and B patterns, run `test_claim`, read `reading` and a few `pairs_to_read`, fix
patterns with `grep` when it reports a problem. A claim that comes back `not_holds` is usually about something that
happened once or twice, or is weaker than it sounds: narrow it, replace it, or report it with that caveat. `holds` is a
statement about this log, in-sample; whoever grades it later may use events you did not see.

