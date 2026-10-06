# Delegating reading to sub-agents (swarmgraph, minimal)

The log is too large to read. It is cut into chunks, each a stretch of the agent's own working context (a chunk
starts where a run starts or its context was compacted). A sub-agent can read a chunk whole and answer a question for
it, citing the events it saw (checked to be in that chunk).

    python3 -m swarmgraph --db log.sqlite call periods '{}'                       # periods, their days and chunk counts
    python3 -m swarmgraph --db log.sqlite call periods '{"period": 3}'            # its days and chunk ids
    python3 -m swarmgraph --db log.sqlite call periods '{"day": "2026-02-11"}'    # one row per chunk (time span, size)
    python3 -m swarmgraph --db log.sqlite call delegate '{"question": "...", "chunks": [12, 13]}'   # or "day" / "period"
    python3 -m swarmgraph --db log.sqlite call delegate '{"job": "<id>", "wait": 240}'            # collect the answers

At most 60 chunks per job and 150 chunk reads in all; a job reads in the background (about 1-2 minutes per chunk, 4 at
a time), so keep working while it runs. Answers are leads with event ids: open the events (events.jsonl) before you
cite them. You can still read events.jsonl directly.

