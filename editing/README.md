# AI video editing prototype

Long-form footage goes in, and a styled highlight video comes out. The AI (Claude Code for now) reads the ingest output, writes a timeline JSON, and renders it.

```
source.mp4 ──ingest.py──▶ work/<id>/{proxy.mp4, scenes.json, frames/, sheets/}
                                   │  AI reviews sheets + scenes.json
                                   ▼
                          timelines/<name>.json ──render.py──▶ renders/<name>-<quality>.mp4
                                                   (cuts clips → Remotion)
```

## Setup

Requires ffmpeg, Node, and [uv](https://docs.astral.sh/uv/).

```sh
cd editing/remotion && npm install
```

## 1. Ingest

```sh
uv run editing/ingest.py renfest-b-roll.mp4 --id renfest
```

This decodes the 4K source once to produce a 720p proxy and detect scene cuts. Shots are split into segments of at most 10 s. Each segment gets a frame, a sharpness score and audio loudness. Labeled contact sheets (16 segments each) go in `work/<id>/sheets/`. A 92-minute 4K file takes about 20 minutes on an M5 Pro.

## 2. Write a timeline

`timelines/*.json` uses the TRD §6.1 edit spec with a few additions. Types live in `remotion/src/timeline.ts`.

| Field | Notes |
|---|---|
| `style` | `cinematic` (Cinzel, 2.39:1 letterbox, grade, slow push-in), `reel` (Montserrat/Anton social text), `vlog` |
| `output` | `{width, height, fps}`, e.g. 1920×1080 @ 24, or 1080×1920 for a vertical reel |
| `title` | `{text, subtitle, duration_s}`, overlaid on the opening shot |
| `clips[]` | `{id, source, in_s, out_s, transition_in, transition_s, caption, motion, volume, crop_x, crop_y}` |
| `crop_x`/`crop_y` | 0–1. Where to frame the shot when the output aspect differs from the source (default 0.5) |
| `transition_in` | `cut`, `fade`, `slide`, `wipe`. On the first clip, `fade` means fade from black |
| `motion` | `push_in`, `pull_out`, `punch` (zoom hit on the cut), `none`. Defaults to the style's setting |
| `music` | `{track, gain_db, duck_original_audio, duck_volume}`. Fades in over 1 s and out over 2 s |
| `overlays[]` | `{text, start_s, end_s, position: top/center/bottom, style: hook/caption/punch}`. Timed text, independent of clip boundaries |
| `effects[]` | `{type: "flash", at_s, duration_s}` or `{type: "fade_out", duration_s}` |
| `end_card` | `{text, duration_s}` |

`in_s`/`out_s` are seconds in the original source and match `start_s`/`end_s` in `scenes.json`.

For vertical output, check framing before rendering. This writes sheets showing each clip's crop window over the source:

```sh
uv run editing/cropcheck.py editing/timelines/renfest_reel.json
```

## Music

```sh
uv run editing/music.py editing/assets/music/renfest_reel.wav
```

This synthesizes an original 35 s, 120 BPM track, so there are no licensing issues. It also writes a `.json` beat grid with section times (intro 0–4 s, build 4–12 s, riser 12–16 s, drop 16–32 s, ending 32–35 s), so cuts can land on the beat.

## 3. Render

```sh
python3 editing/render.py editing/timelines/renfest.json                  # 720p preview from the proxy
python3 editing/render.py editing/timelines/renfest.json --quality final  # full res from the original
python3 editing/render.py editing/timelines/renfest.json --stage-only     # then: cd editing/remotion && npm run studio
```

The renderer validates the timeline and cuts only the used ranges into `remotion/public/clips/<name>/`. Cuts are cached, so changing text or transitions only re-runs Remotion.
