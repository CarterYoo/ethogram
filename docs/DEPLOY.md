# Deploying Ethogram (the behaviour atlas)

What is deployed: the web pages (`/features` first, the other pages beside it) over two datasets (wiki plus other
boards, the default; AI Village July-August), read from stored results. The server is the Python standard library; it makes
no LLM calls.

## Make the bundle

```bash
scripts/bundle.sh                     # → dist/ethogram-deploy (about 230 MB), from the committed code only
```

The bundle holds `app/` (code), `data/` (consistent, vacuumed copies of the two indexes and their work stores),
`serve.sh`, a `Dockerfile` and `MANIFEST.txt` (commit and SHA-256 of every data file as made).

## Run it

Any machine with Python 3.9+:

```bash
sh dist/ethogram-deploy/serve.sh    # http://localhost:8080 → /features
```

As a container:

```bash
docker build -t ethogram dist/ethogram-deploy
docker run -p 8080:8080 ethogram
```

| variable | default | meaning |
|---|---|---|
| `PORT` | 8080 | port to listen on (platforms that set `PORT` are followed) |
| `SWARMGRAPH_HOST` | 0.0.0.0 | address to listen on |
| `SWARMGRAPH_SHARE` | 1 | share mode: methods shown only by kind (links cut to their domain, encoded strings, markup, commands, addresses and secret-like tokens withheld), email addresses withheld, and no agent runs can be started (POST answers 403). Set 0 only for trusted viewers. |
| `SWARMGRAPH_DATA` | `data/` beside `serve.sh` | folder with the indexes |

`/healthz` answers `{"ok": true, "datasets": [...]}` (the container's health check uses it). The server refuses to
start when an index is missing. It writes into `data/` when it opens a work store (write-ahead log files), so the
folder must be writable; the manifest's checksums describe the bundle as made.

## The static demo (GitHub Pages)

The same atlas in share mode, as static files: every answer `/features` asks the server for is written to a file, and
a small script in the page (`scripts/pages_shim.js`) answers from them, so no server is needed. It is live at
https://carteryoo.github.io/ethogram/.

```bash
.venv/bin/python scripts/build_pages.py     # → dist/pages/ethogram (about 33 MB) from ../data, which it never writes to
```

Copy the folder to any static host; it works from any path. For each dataset it holds the answers the page loads at
start, every behaviour card's flow, and each stretch's first 12 events with their text, redacted as in share mode and
then cut to 900 characters, split over 32 files so reading a stretch loads one or two of them. The other pages and
agent runs are not part of it.

## Checked (2026-10-04)

Run from the bundle with the system Python: `/healthz` lists the datasets, `/` redirects to `/features`, the
dataset picker switches between them, POST to `/api/feature_storylines` answers 403 and reports `can_create: false`, and
the sources behind a scene come back with link details withheld. The Docker image was not built here (no Docker on
this machine).

Not done: no image was pushed and nothing was published; choosing a host is left to the team.
