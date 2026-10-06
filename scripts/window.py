"""Cut a time window out of a dataset directory (swarmgraph format).

    python3 scripts/window.py SRC_DATASET_DIR OUT_DIR SINCE UNTIL

Keeps events with SINCE <= ts < UNTIL (ISO dates, e.g. 2026-07-01 2026-09-01) and copies actors.jsonl, phases.jsonl
and dataset.json unchanged, so actors and goal phases outside the window stay known. This is how the AI Village
July-August dataset was cut from the full conversion.
"""
import json
import os
import shutil
import sys


def window(src, out, since, until):
    os.makedirs(out, exist_ok=True)
    n = 0
    with open(os.path.join(src, "events.jsonl"), encoding="utf-8") as f, \
            open(os.path.join(out, "events.jsonl"), "w", encoding="utf-8") as o:
        for line in f:
            if line.strip() and since <= json.loads(line)["ts"][:len(since)] < until:
                o.write(line)
                n += 1
    for name in ("actors.jsonl", "phases.jsonl", "dataset.json"):
        if os.path.exists(os.path.join(src, name)):
            shutil.copy(os.path.join(src, name), out)
    return n


if __name__ == "__main__":
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    print(f"{window(*sys.argv[1:5]):,} events kept in {sys.argv[2]}")

