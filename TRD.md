# AI Travel Videographer — Technical Requirements Document

Oct 20, 2026 · @Ian Stewart

## 1. Overview

This TRD specifies how to build the AI Travel Videographer MVP described in PRD v1.0 within 36 hours: a Raspberry Pi camera wearable with a 60-second segmented ring buffer, a FastAPI backend running a four-stage gated model cascade, an FFmpeg render engine, and a mobile web app. Every requirement here traces to a PRD functional requirement (FR-xxx) or non-functional section.

**In scope:** wearable capture and retention, frame sampling, the detection cascade (Stage 0 → 3), clip transfer, automatic editing to a 30–60 s MP4, trip control UI.

**Out of scope (per PRD §3.3):** all-day battery, miniaturized hardware, offline frontier inference, manual timeline editing, multi-user, native mobile background execution.

**Design constraints that drive most decisions:**

- Recording must never wait on the network or AI. The device records and buffers locally; the backend only sends retention decisions.
- Frontier vision calls are slow (1–5 s) and metered. They are reached only after cheap filters agree a frame is worth asking about.
- The ring buffer must hold footage longer than the worst-case decision latency, so a late "save" still finds its pre-roll on disk.
- Rendering is deterministic. The LLM produces a JSON edit spec; FFmpeg executes it.

**Terms used below**

| Term | Meaning |
| --- | --- |
| Segment | One 2-second H.264 file in the ring buffer, named by trip-relative start time |
| Trip clock | Milliseconds since trip start, `t_ms`, shared by device and backend |
| Sample | A downscaled JPEG frame the device posts for analysis |
| Candidate | A sample that passed Stage 1 and may be escalated |
| Moment | A retention decision covering `[T−15 s, T+15 s]` |
| Clip | The concatenated, finalized MP4 for one moment |
| Edit spec | JSON plan from Stage 3 that the renderer executes |

## 2. System architecture

Four components on one LAN plus one cloud API: the glasses record and hold footage, a laptop backend decides what to keep and renders the video, a cloud vision model is consulted only for escalated frames, and the phone is a thin control UI.

&#91;embedded content: system architecture · device, backend, cloud, app\]

The only continuous traffic is one \~30 KB JPEG every 2 s from glasses to backend; clips move once, after they are finalized.

**Deployment for the demo**

- A dedicated travel router (e.g. GL.iNet) creates the LAN; the Pi, laptop and phone join it. Phone hotspot is the fallback.
- Backend: laptop at a static LAN IP, `uvicorn --workers 1` (one process owns the in-memory detection state), web app served from the same host.
- Cloud: one outbound HTTPS dependency, the vision-model API. Everything else works offline.
- Repos: `glasses-agent/` (Pi), `backend/`, `web/`, with shared JSON schemas in `contracts/` agreed in hours 0–4.

## 3. Wearable device

The Pi Zero 2 W records continuously to 2-second segments, keeps the newest 90 seconds unless protected, and posts one low-res sample every 2 s; it never blocks on the backend.

### 3.1 Hardware

| Component | Spec / note |
| --- | --- |
| Controller | Raspberry Pi Zero 2 W, Raspberry Pi OS Lite (64-bit, Bookworm) |
| Camera | Camera Module 3 (wide preferred for POV), 22→15-pin Zero ribbon |
| Storage | 32 GB+ A2 microSD; buffer on a tmpfs-free ext4 partition |
| Power | USB power bank ≥ 10,000 mAh, 5 V / 2.5 A |
| Inputs | Momentary button on GPIO17 (pull-up, 50 ms debounce) |
| Outputs | Red LED on GPIO27: solid = recording, blink = saving moment |
| Thermal | Small heatsink; throttling observed above \~80 °C must be tested in hours 0–4 |

### 3.2 Software processes

One Python 3.11 service, `glasses-agent`, run by systemd, with four threads/tasks:

1. **Recorder** — Picamera2 `H264Encoder` at 1280×720, 30 fps, 4 Mbps, GOP = 60 (one IDR per 2 s) writing via `FfmpegOutput` with the segment muxer: `-f segment -segment_time 2 -reset_timestamps 1 seg_%010d.ts`. MPEG-TS segments survive power loss better than MP4.
2. **Sampler** — every 2 s grabs a lores-stream frame (`640×360` YUV) from the same camera, JPEG-encodes at q=70 (\~25–40 KB), and POSTs it with `t_ms`. Queue depth 3; on overflow drop the oldest sample, never block.
3. **Retention manager** — owns the segment index, protection set, and cleanup loop (§3.3).
4. **Control server** — small FastAPI/uvicorn on port 8081 for start/stop, retain commands, health, and clip download.

An audio stream is off by default. If a USB mic is added, record it into the same segment muxer so A/V stay aligned.

### 3.3 Segmented ring buffer (FR-004 – FR-006)

Each segment file is named by its index `i`; its trip-clock span is `[i·2000, (i+1)·2000) ms`, anchored when the recorder starts. An in-memory index (persisted to `index.json` every 5 s) holds:

```
Segment { idx, path, t_start_ms, t_end_ms, bytes, protected_by: set[moment_id] }
```

**Cleanup rule** (runs every 1 s): delete segment `i` only if `protected_by` is empty AND `now_ms − t_end_ms > BUFFER_RETENTION_MS`.

**Buffer size.** PRD says 60 s; this TRD sets `BUFFER_RETENTION_MS = 90,000`. Pre-roll must survive the worst-case decision path: sample interval (2 s) + upload (≤1 s) + Stage 1 (≤0.2 s) + Stage 2 timeout (8 s) + command delivery (≤1 s) ≈ 12 s, plus the 15 s pre-roll = 27 s. 90 s gives >3× headroom for retries at \~45 MB of disk at 4 Mbps.

**Retain command** from backend: `{moment_id, from_ms, to_ms}`. The manager:

1. Adds `moment_id` to `protected_by` on every existing segment overlapping `[from_ms, to_ms]`.
2. Registers a pending protection so segments created later inside the window are protected on creation.
3. When `now_ms > to_ms + 2000` (last segment closed), concatenates the protected segments with `ffmpeg -f concat -c copy` into `clips/{moment_id}.mp4`, writes a sidecar JSON, and releases `moment_id` from each segment.

Overlapping moments share segments naturally through the set; the backend merges them before clip extraction (§4.7). Because cuts land on 2-s boundaries, the clip may run up to 2 s longer each side; Stage 3 trims precisely.

**Manual capture (FR-007).** A button press creates a moment locally with `T = now_ms`, `source = manual`, protects immediately, and notifies the backend. It works with the backend offline.

### 3.4 Device ↔ backend protocol

All HTTP over the demo LAN, with a shared bearer token `DEVICE_TOKEN`.

| Direction | Endpoint | Payload |
| --- | --- | --- |
| Device → backend | `POST /device/samples` | multipart: JPEG + `{trip_id, t_ms, seg_idx, local_signals}` |
| Device → backend | `POST /device/heartbeat` (every 5 s) | `{trip_id, t_ms, temp_c, disk_free_mb, buffer_s, queue_depth}` |
| Device → backend | `POST /device/moments` | manual capture notice |
| Backend → device | `POST :8081/retain` | `{moment_id, from_ms, to_ms}` (idempotent by `moment_id`) |
| Backend → device | `POST :8081/trip/start`, `/trip/stop` | `{trip_id}` |
| Backend → device | `GET :8081/clips/{moment_id}.mp4` | pulled after finalization |

If heartbeats stop for 15 s, the backend marks the device `DISCONNECTED`; the device keeps recording and resends queued manual moments on reconnect.

## 4. Hierarchical AI analysis

Every sample passes a free on-device check and a \~50 ms local embedding model; only frames that score high, are new, and fit the budget reach a paid vision model, and only that model's "uncertain" answers reach a stronger one. Target: fewer than 1 in 10 samples escalated, under 1 paid call per 20 s of trip.

| Stage | Where | Runs on | Latency target | Cost | Decides |
| --- | --- | --- | --- | --- | --- |
| 0 — Signals | Device | Every sample (0.5 Hz) | < 30 ms | Free | Quality flags, change spikes |
| 1 — Candidate filter | Backend | Every sample | < 100 ms | Free (local) | Discard / watch / escalate |
| 2a — Fast VLM | Cloud API | Escalated candidates only | < 4 s | Low | Save / skip / uncertain |
| 2b — Strong VLM | Cloud API | Stage 2a "uncertain" only | < 8 s | Higher | Final save / skip |
| 3 — Edit planner | Cloud API | Once per trip | < 60 s | One call | Selection, order, trims |

&#91;embedded content: detection cascade · Stages 0–2 per sample\]

The hold is sent before any paid call, so model latency never costs pre-roll; if the VLM is down or over budget, the Stage 1 score decides alone (§4.3, §4.5).

### 4.1 Stage 0 — on-device signals

Computed with NumPy/OpenCV on the 640×360 lores frame, downscaled to 160×90 grayscale. No ML on the Pi.

| Signal | Computation | Use |
| --- | --- | --- |
| `sharpness` | Variance of Laplacian | Blur rejection |
| `exposure` | Mean luma; % pixels < 16 or > 240 | Dark / blown-out rejection |
| `change` | Mean abs diff vs previous sample | Scene-change spike |
| `motion` | Mean of last 3 `change` values | Walking vs standing still |
| `dhash` | 64-bit difference hash | Cheap near-duplicate check |
| `forced` | True every 10th sample | Guarantees static scenery is still evaluated |

The device sends every sample with these values attached (\~160 kbps total), so Stage 1 thresholds can be tuned on the backend without redeploying the Pi. An optional `DEVICE_GATE=true` drops frames that are both blurry and unchanged to save bandwidth.

### 4.2 Stage 1 — candidate filter

Runs in a backend worker on every sample. Model: OpenCLIP ViT-B/32 (`laion2b_s34b_b79k`), loaded once, \~30–80 ms per image on a laptop CPU, faster on GPU or Apple MPS.

**At trip start**, the backend compiles the trip config into text prompts and caches their embeddings:

- **Positive prompts per preference.** "Scenery" → "a scenic landscape view", "a sunset over the water". "Architecture" → "a striking building facade". "Food" → "a plated meal on a restaurant table". "Friends" → "a group of friends smiling together". Each itinerary item becomes a prompt too: "the Golden Gate Bridge".
- **Negative prompts (fixed).** "a blank wall", "a close-up of pavement", "the inside of a pocket", "a car dashboard", "a phone screen", "a blurry dark photo".
- **Preference weights** `w_p` from the user's ranking (default equal; first-ranked preference 1.5×).

**Per sample**, compute the image embedding `e_t`, then:

- `relevance` = softmax over all prompts (temperature 0.01), then `max_p(w_p · P(positive_p)) − P(best negative)`, clamped to \[0, 1\]. Category = the argmax prompt's group.
- `novelty` = `1 − max cos(e_t, E)` where `E` is the last 30 sample embeddings plus all saved-moment embeddings. Saved moments count double so the system doesn't re-save the same viewpoint.
- `quality` = 0 if `sharpness` or `exposure` fails, else normalized sharpness.
- `dwell` = 1 if `motion` is low and `relevance ≥ 0.4` for ≥ 3 consecutive samples (the user stopped to look).

```latex
S_1 = 0.45\,\text{relevance} + 0.25\,\text{novelty} + 0.15\,\text{quality} + 0.15\,\max(\text{change\_spike}, \text{dwell})
```

`S_1` is smoothed with an EMA (α = 0.5) so one odd frame can't trigger a call.

### 4.3 Escalation gate

A candidate goes to Stage 2 only if all of these hold:

1. Smoothed `S_1 ≥ τ_escalate` (start at 0.55; tune on recorded footage).
2. It is a local peak: current `S_1` ≥ both neighbors, so a 30-s view triggers once, not 15 times.
3. Cooldown: ≥ 10 s since the last escalation, or ≥ 30 s since the last *save in the same category*.
4. Not a near-duplicate: `dhash` Hamming distance ≥ 10 from the last escalated frame.
5. Budget allows it (§4.6).

Outcomes below the gate:

- `S_1 < 0.30` → discard; footage ages out normally.
- `0.30 ≤ S_1 < τ_escalate` → watch; logged for tuning only.
- **Stage 1 auto-save:** if `S_1 ≥ 0.85` but the API is down or the budget is spent, save on Stage 1 alone with `source = stage1_fallback`. Capture never depends on the cloud.

### 4.4 Speculative hold

On escalation the backend immediately sends a **provisional** `retain` for `[T−15 s, T+15 s]` with `provisional: true`. If Stage 2 returns skip, the backend sends `release`; the segments age out on the next cleanup pass. This removes VLM latency from the retention race entirely: a slow answer can't cost the pre-roll.

### 4.5 Stage 2 — multimodal evaluation

**Input packet**

- 3 frames: `t−4 s`, `t−2 s`, `t`, resized to 512 px long edge (the device's last samples, already on the backend).
- Trip context: destination, itinerary, ranked preferences, style.
- Stage 1 metadata: category guess, `S_1`, novelty, dwell.
- Last 5 saved-moment descriptions, so the model can judge redundancy.

**Model tiering** (model IDs are config, not code):

- **2a — fast tier** (e.g. a Haiku-class vision model) handles every escalation.
- **2b — strong tier** (e.g. a Sonnet-class model) runs only when 2a returns `uncertain`, or when its `final_score` lands within ±0.08 of the save threshold, and budget allows.

**Output schema** — enforced through tool use / JSON schema and validated with Pydantic:

```json
{
  "event_type": "scenery | landmark | activity | food | social | other",
  "matched_preferences": ["scenery"],
  "preference_alignment": 0.0,
  "visual_interest": 0.0,
  "significance": 0.0,
  "visual_quality": 0.0,
  "save_decision": "save | skip | uncertain",
  "best_frame_index": 2,
  "window_hint_s": {"pre": 15, "post": 15},
  "description": "≤ 20 words, only what is visible",
  "reason": "≤ 25 words"
}
```

**Prompt rules** (system prompt excerpt): score only what is visible in the frames; never name a place unless it is recognizable or in the itinerary; prefer `skip` for transit, screens, and redundant views of a saved moment; return `uncertain` rather than guessing.

**Final score** uses the PRD §6.6 weights, with novelty carried from Stage 1:

```latex
S_2 = 0.35\,\text{pref} + 0.25\,\text{interest} + 0.20\,\text{significance} + 0.10\,\text{quality} + 0.10\,\text{novelty}
```

Save when `save_decision = save` and `S_2 ≥ 0.60`, or when `S_2 ≥ 0.75` regardless. Manual captures skip Stages 1–2 entirely and always save; they are still sent to Stage 2a later, off the critical path, to get a description and category.

**Failure handling:** 8-s hard timeout; one retry on invalid JSON; then fall back to the Stage 1 decision (save if `S_1 ≥ 0.70`, else release). Max 2 Stage 2 calls in flight; extra candidates queue (depth 3) and the oldest is dropped, since its hold already protects the footage.

### 4.6 Budget and pacing

A token bucket per trip limits paid calls: capacity 5, refill 1 per 20 s, hard cap 200 Stage 2a and 30 Stage 2b calls per trip (configurable). As the bucket drains, the gate tightens:

```latex
\tau_{escalate} = \tau_{base} + 0.20 \times (1 - \text{tokens} / \text{capacity})
```

So a busy, beautiful stretch escalates only its best peaks instead of exhausting the budget in the first ten minutes. A `DEMO_MODE` profile shortens cooldowns to 5 s and lowers `τ_base` to 0.45 for short scripted trips.

### 4.7 Moment merging

When a new save's window overlaps an existing moment's window by more than 5 s, the backend extends the existing moment instead of creating one, keeps the higher score, and appends the description. Merged windows are capped at 60 s; beyond that a new moment starts.

### 4.8 Stage 3 — post-trip edit planning

Runs once after all clips are on the backend. A deterministic pre-pass shrinks the problem before any model call:

1. **Dedupe:** CLIP embeddings of each clip's best frame; pairs with cosine > 0.90 keep the higher `S_2`.
2. **Best sub-window:** per clip, score each 1-s window on sharpness and stability (variance of frame-to-frame optical flow); propose a trim around the best run.
3. **Thumbnails:** 4 keyframes per surviving clip at 384 px.

The planner LLM (strong tier) gets the clip table (id, times, category, description, score, proposed trims), the thumbnails, the style preset, and the target duration. It returns an **edit spec** (§6.1). The backend validates it: every clip id exists, trims fall inside clip bounds, total duration within ±10 % of target. On failure, a greedy fallback planner sorts clips by score, takes the top clips until the duration budget is filled, reorders chronologically, and applies preset defaults.

### 4.9 Observability and tuning

Every sample writes a `sample_scores` row: Stage 0 signals, Stage 1 sub-scores, gate outcome, and any Stage 2 response. A `/debug/timeline` page plots `S_1` over the trip with escalations and saves marked. Thresholds live in `config/detection.yaml` and hot-reload. A replay script runs recorded sample folders through Stages 1–2 to tune thresholds offline before the demo.

## 5. Backend services

One Python 3.11 FastAPI process with three in-process worker pools (detection, transfer, render) and SQLite in WAL mode; no message broker is needed at MVP scale.

### 5.1 Modules

| Module | Responsibility | Key dependencies |
| --- | --- | --- |
| `api/` | REST + WebSocket for app and device | FastAPI, uvicorn |
| `trips/` | Trip CRUD and state machine | SQLModel |
| `device/` | Device registry, heartbeats, command client | httpx |
| `detect/stage1.py` | CLIP embeddings, scoring, gate | open\_clip, torch, numpy |
| `detect/stage2.py` | VLM calls, schema validation, tiering | Anthropic SDK (or other provider), pydantic |
| `detect/budget.py` | Token bucket, cooldowns | — |
| `moments/` | Hold / retain / release, merging | — |
| `media/transfer.py` | Pull finalized clips from device, verify with ffprobe | httpx, ffprobe |
| `edit/planner.py` | Stage 3 pre-pass + LLM edit spec + greedy fallback | opencv, open\_clip |
| `edit/render.py` | Edit spec → FFmpeg commands | ffmpeg 6.x |

Detection work runs in a `ThreadPoolExecutor(max_workers=2)` for Stage 1 (torch releases the GIL) and an `asyncio.Semaphore(2)` around Stage 2 calls. Rendering runs in a separate process so it can't stall the API.

### 5.2 Data model (SQLite)

| Table | Columns |
| --- | --- |
| `trips` | `id`, `destination`, `name`, `itinerary` (JSON), `preferences` (JSON, ranked), `style`, `status`, `started_at`, `ended_at`, `device_id` |
| `samples` | `id`, `trip_id`, `t_ms`, `seg_idx`, `path`, `signals` (JSON) |
| `sample_scores` | `sample_id`, `relevance`, `novelty`, `quality`, `dwell`, `s1`, `s1_smoothed`, `category`, `gate_outcome` |
| `evaluations` | `id`, `sample_id`, `tier` (2a/2b), `model`, `latency_ms`, `response` (JSON), `s2`, `error` |
| `moments` | `id`, `trip_id`, `t_trigger_ms`, `from_ms`, `to_ms`, `status` (held/retained/released/finalized/transferred), `source` (auto/manual/stage1\_fallback), `event_type`, `description`, `score`, `clip_path`, `embedding` (BLOB) |
| `videos` | `id`, `trip_id`, `status`, `edit_spec` (JSON), `output_path`, `duration_s`, `style`, `created_at`, `error` |

Sample JPEGs are kept on disk for the trip only and deleted when the trip completes, unless `KEEP_DEBUG_SAMPLES=true`.

### 5.3 Trip state machine

`CREATED → ACTIVE → FINALIZING → PROCESSING → COMPLETED`, with `ERROR` reachable from any state carrying `error_stage` and a retry action.

| Transition | Trigger | Guard / side effects |
| --- | --- | --- |
| CREATED → ACTIVE | `POST /trips/{id}/start` | Device online; device ack received; Stage 1 prompt cache built |
| ACTIVE → FINALIZING | `POST /trips/{id}/stop` | Device stops recorder; backend waits for in-flight Stage 2 calls (≤ 10 s) and pending post-rolls |
| FINALIZING → PROCESSING | All moments `transferred` or `failed` | At least 1 clip transferred, else ERROR(no\_moments) |
| PROCESSING → COMPLETED | Render exits 0 and ffprobe validates output | Video row written |

### 5.4 App-facing API

All PRD §9.3 endpoints, plus:

| Endpoint | Purpose |
| --- | --- |
| `POST /trips` | Create trip (body: destination, name, itinerary\[\], preferences\[\], style) |
| `POST /trips/{id}/start` · `/stop` | Session control |
| `GET /trips/{id}` | Status, device state, counts |
| `GET /trips/{id}/moments` | Saved moments with thumbnails and descriptions |
| `POST /trips/{id}/manual-capture` | App-side "save moment" (same path as the button) |
| `GET /trips/{id}/video` | Render status, URL when ready |
| `GET /media/{token}` | Signed, expiring URL for clips and final MP4 |
| `WS /trips/{id}/events` | Push: `device_status`, `moment_saved`, `stage_progress`, `video_ready`, `error` |

The app falls back to polling `GET /trips/{id}` every 3 s if the WebSocket drops.

## 6. Video editing pipeline

The renderer turns a validated JSON edit spec into one MP4 in three FFmpeg passes: normalize, trim, then assemble with transitions, titles and music; no model touches pixels.

### 6.1 Edit spec

```json
{
  "trip_id": "t_123",
  "style": "cinematic",
  "output": {"width": 1280, "height": 720, "fps": 30},
  "title": {"text": "San Francisco", "subtitle": "October 2026", "duration_s": 2.5},
  "clips": [
    {"moment_id": "m_07", "in_s": 11.0, "out_s": 17.5, "transition_in": "fade", "transition_s": 0.5, "caption": null}
  ],
  "music": {"track": "assets/music/cinematic_01.mp3", "gain_db": -14, "duck_original_audio": true},
  "end_card": {"text": "Captured with AI Travel Videographer", "duration_s": 2.0}
}
```

Captions and titles may only use the destination, itinerary names, and Stage 2 descriptions; the validator rejects any caption not traceable to one of those (PRD §7.4).

### 6.2 Render passes

1. **Normalize** each source clip to the output profile: `-vf scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,fps=30,format=yuv420p -c:v libx264 -preset veryfast -crf 21 -c:a aac -ar 48000 -ac 2` (silent audio track added with `anullsrc` when a clip has none). For the 9:16 Social Reel preset, center-crop with `crop=ih*9/16:ih` instead.
2. **Trim** each clip to `[in_s, out_s]` with accurate seek (`-ss` after `-i`).
3. **Assemble** with one `filter_complex`: chain `xfade` (video) and `acrossfade` (audio) between clips; `drawtext` for title and captions using a bundled font file; mix the music bed with `amix` and `sidechaincompress` for ducking; `afade` out over the last 2 s. Encode `-preset medium -crf 20 -movflags +faststart`.

If pass 3 fails, retry once with hard cuts (concat demuxer, no `xfade`), which removes the most fragile filter. Each pass is a subprocess with a timeout (normalize 30 s per clip, assemble 120 s) and its stderr is stored on the `videos` row.

### 6.3 Style presets

| Preset | Target length | Clip length | Transition | Aspect | Music | Text |
| --- | --- | --- | --- | --- | --- | --- |
| Cinematic (P0) | 45–60 s | 4–8 s | 0.5–0.8 s fade / dissolve | 16:9 | Ambient, −14 dB | Title + end card only |
| Social Reel (P1) | 30 s | 1.5–3 s | Hard cuts, 0.2 s whip on category change | 9:16 | Upbeat, −10 dB | Short captions |
| Travel Vlog (P1) | 60 s | 5–10 s | Hard cuts | 16:9 | Low bed, −20 dB, original audio kept | Location captions |

Presets are YAML files the planner receives verbatim, so adding a preset needs no code change.

### 6.4 Performance target

For 8 clips of \~30 s each on a recent laptop, normalize + assemble should finish in under 90 s with `veryfast` normalization; this must be measured in hours 18–28 and the preset downgraded if it misses the PRD's 2-minute target.

## 7. Companion web app

A Next.js (App Router) + Tailwind mobile web app served from the backend's LAN address; it holds no secrets and only talks to the backend.

| Screen | Route | Data source | Notes |
| --- | --- | --- | --- |
| Home | `/` | `GET /trips`, device status | Create trip, previous trips, device dot (green / amber / red) |
| Trip setup | `/trips/new` | `POST /trips` | Destination, name, itinerary chips, drag-to-rank preferences, style cards |
| Active trip | `/trips/[id]` | WS events | Recording badge, elapsed time, saved-moment feed with thumbnail + description, Save Moment, End Trip |
| Processing | `/trips/[id]/processing` | WS `stage_progress` | Steps: finalizing → transferring n/m → planning → rendering |
| Video | `/trips/[id]/video` | `GET /trips/{id}/video` | `<video>` player, download, moments list |

**Status mapping (PRD §8.3).** The UI derives one banner from trip state + device state + detection health:

| Condition | Banner |
| --- | --- |
| ACTIVE, device online, Stage 2 healthy | Recording |
| ACTIVE, no heartbeat 15 s | Device disconnected — still recording on device |
| ACTIVE, Stage 2 errors > 3 in 60 s | AI analysis limited — manual save still works |
| FINALIZING / PROCESSING | Processing video |
| COMPLETED | Video ready |
| ERROR | Specific message + Retry |

The app shows "Video ready" only after the backend's ffprobe check passes. Active-trip screens hold a Screen Wake Lock where supported, but recording never depends on the page being open.

## 8. Non-functional requirements

The hard requirement is zero lost pre-roll for any moment the backend decides to save; every other number here is a target to measure, not a guarantee.

### 8.1 Timing budget (sample → protected footage)

| Step | Target | Worst case allowed |
| --- | --- | --- |
| Sample interval | 2 s | 2 s |
| Upload JPEG | 150 ms | 1 s |
| Stage 1 score + gate | 80 ms | 300 ms |
| Provisional hold reaches device | 100 ms | 1 s |
| **Time to protection** | **\~2.3 s** | **\~4.3 s** |
| Stage 2a / 2b decision (off critical path) | 2–4 s | 8 s + 8 s |

Because the hold is sent before the VLM answers, required buffer = 15 s pre-roll + \~4.3 s ≈ 20 s; the 90 s buffer covers backend restarts and retries.

### 8.2 Reliability

- Device keeps recording through backend or Wi-Fi loss; queued manual moments replay on reconnect (idempotent by `moment_id`).
- Device crash: systemd restarts the agent within 3 s; `index.json` restores protection state; segments are never deleted during startup until the index is reconciled.
- Disk guard: if free space < 2 GB, the device stops auto moments, keeps manual ones, and reports `DISK_LOW`.
- Clock: device and backend share the trip clock from the start command; NTP on the LAN keeps wall clocks for logs only.
- Clip transfer verifies size and runs `ffprobe`; 3 retries with backoff.

### 8.3 Security

- `DEVICE_TOKEN` on all device routes; app routes use a per-trip session token issued at creation. Demo LAN only; no public exposure.
- Model API keys live in backend `.env`, never in the web bundle.
- Media served via signed URLs expiring after 1 h.

### 8.4 Privacy (PRD §10)

- Camera and recorder are off unless a trip is ACTIVE; LED is wired to recorder state, not to software UI.
- Only 512-px sample frames go to the cloud VLM; full-resolution video never leaves the LAN.
- Unsaved segments are deleted within 90 s; sample JPEGs are deleted at trip completion; cloud provider data retention follows the provider's API terms, which the team must check and state in the demo.
- Audio off by default; trip setup has an explicit "Allow cloud analysis" toggle. If off, the system runs Stage 1 auto-save and manual capture only.
- Stage 2 prompt forbids identifying people; descriptions refer to "a person", "friends", never names.

## 9. Testing, milestones, risks

The build follows PRD §14; each phase ends with a technical gate below, and the team does not start AI tuning until the retention gate passes on real hardware.

### 9.1 Milestone gates

1. **Hour 4 — Capture:** Pi records 10 min of 720p30 segments with no dropped segments and CPU temp < 80 °C.
2. **Hour 10 — Retention:** a curl `retain` sent at T yields a clip spanning ≥ T−15 s to T+15 s, verified by a burned-in clock on screen; unprotected segments older than 90 s are gone.
3. **Hour 16 — Control:** phone app starts and stops a trip; banner reflects a pulled Wi-Fi cable within 15 s.
4. **Hour 23 — Detection:** on a replayed 10-min walk, Stage 1 escalates ≤ 10 % of samples and the full cascade saves 3 of 3 scripted moments.
5. **Hour 28 — Video:** ending a trip yields a valid 30–60 s MP4 with no manual step.
6. **Hour 33 — Repeatability:** three consecutive end-to-end runs succeed.

### 9.2 Test plan

- **Unit:** ring-buffer cleanup and protection (incl. overlapping moments and pending protections), score formulas, gate logic, budget bucket, edit-spec validator.
- **Replay harness:** recorded sample folders + labeled "should save" timestamps; reports precision/recall and calls per minute for each threshold set.
- **Personalization check:** the same replay with "architecture" vs "food" preferences must produce different top-3 moments (PRD FR-010).
- **Fault injection:** kill backend mid-trip, block the VLM API, fill disk, reboot the Pi; each must keep or recover footage per §8.2.
- **Render matrix:** 1, 3 and 10 clips; clips with and without audio; both aspect ratios.

### 9.3 Risks

| Risk | Mitigation in this design |
| --- | --- |
| Pi Zero can't encode 720p30 and sample at once | Lores stream for samples; fallback 720p24 or 2 Mbps |
| CLIP zero-shot misranks scenes | Prompt tuning on replay set; Stage 2 corrects; manual button |
| VLM latency or outage | Speculative hold; Stage 1 auto-save fallback |
| Over-escalation drains budget | Peak detection, cooldowns, adaptive threshold |
| Redundant moments of one view | Novelty vs saved embeddings; merging; Stage 3 dedupe |
| `xfade` filter graph errors | Hard-cut fallback render |
| Venue Wi-Fi blocks device ↔ laptop | Bring a travel router; phone hotspot as backup |

### 9.4 Open questions

- Which VLM provider and model IDs, and what per-trip spend cap for the demo?
- Does the team have a GPU laptop, or must CLIP run on CPU (affects Stage 1 batch size)?
- Is a mic worth wiring for P1 audio cues, given A/V sync risk?
- Which royalty-free tracks are licensed for the demo?
