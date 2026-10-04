# Demo video (HyperFrames)

A 40-second, 1920x1080 video made from the real wiki-plus-boards results: points gather into the hackathon title,
which breaks into points that become a swarm in chaos (canvas, thousands of messages coloured by the family of the
sender's name); a world of agents talking in speech bubbles (real lines) from which one voice is picked and zoomed
into; its marked words fly into their behaviour's node on the map; every behaviour's name scatters into its stretches;
and the behaviours play over time with traces, while the storyline (the arc's title, each phase, each turning point on
its day) is written at the top left and dashed lines on the strip mark where code measured the mix of behaviour
changing (the flow regimes, [docs/FLOW.md](../docs/FLOW.md)). At the end the camera closes in on the behaviours that
pass on: an arrow from a behaviour seen to the one others then do more (thicker for a larger change in the odds, from
the influence model of [docs/HARNESS.md](../docs/HARNESS.md) section 10), a ring where a behaviour, seen, is done more
by whoever saw it, and two of the arrows in words. Playback time is shared out by phase, the main event speeds up
inside its share, and playback pauses briefly on a turning point its phase would otherwise pass before it can be read;
the strip, the playhead and the storyline all use that clock. Everything is composed on one timeline and played 1.2x
by an outer timeline (`SPEED`; clip times in the markup are outer times). The name shown is `NAME` in `index.html`.

- `extract.py` pulls the numbers and lines from the index into `data.js` (plain sentences only: lines with links,
  markup, code, request methods, encodings or security words, or that share-mode redaction would change, are left out).
- `index.html` is the composition: one paused GSAP timeline registered as `window.__timelines.demo`, scenes as clips.
- `vendor/gsap.min.js` is GSAP 3.15.0, so the render needs no network.

```bash
.venv/bin/python demo/extract.py ../data/wiki_all.sqlite         # refresh data.js (optional)
cd demo && npm install                                            # hyperframes, gsap, ffmpeg-static, ffprobe-static (Node 22+)
mkdir -p bin && ln -sf "$PWD/node_modules/ffmpeg-static/ffmpeg" bin/ffmpeg \
  && ln -sf "$(node -e "console.log(require('ffprobe-static').path)")" bin/ffprobe
export PATH="$PWD/bin:$PATH"                                      # the renderer needs ffmpeg and ffprobe on PATH
npx hyperframes telemetry disable                                 # optional
npx hyperframes lint                                             # 0 errors (warnings suggest sub-compositions)
npx hyperframes snapshot . --at 2.6,9.6,13,16.6,18.6,20.6,24,28.5,32.6,36.3,38.4   # key frames as PNG
npx hyperframes render -o renders/demo-ethogram.mp4 -q delivery
```

Preview in a browser without the renderer: serve this folder, open `index.html?fit` and call `__seek(t)` in the
console.

Canvas scenes are drawn by a setter that GSAP tweens (`painter()`): the renderer seeks with events suppressed, so
`onUpdate` callbacks would not run, but a tweened property is always set.
