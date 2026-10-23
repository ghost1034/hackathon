"""Validate a timeline, stage the clips it uses, and render it with Remotion.

Usage:
    python3 editing/render.py editing/timelines/sample.json                 # fast preview from the proxy
    python3 editing/render.py editing/timelines/sample.json --quality final # full quality from the original
    python3 editing/render.py editing/timelines/sample.json --stage-only    # stage for `npm run studio`

Only the [in_s, out_s] ranges a timeline uses are cut (accurate seek, re-encoded to the output
size and fps) into editing/remotion/public/clips/<timeline>/, so Remotion never touches the
multi-gigabyte source. Cuts are cached by content hash, so re-rendering after a text or
transition change only re-runs Remotion.
"""

import argparse
import copy
import hashlib
import json
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

EDITING = Path(__file__).resolve().parent
WORK_ROOT = EDITING / "work"
REMOTION = EDITING / "remotion"
PUBLIC = REMOTION / "public"
RENDERS = EDITING / "renders"

STYLES = {"cinematic", "reel", "vlog"}
TRANSITIONS = {"cut", "fade", "slide", "wipe"}
MOTIONS = {"push_in", "pull_out", "punch", "none"}
OVERLAY_STYLES = {"hook", "caption", "punch"}
OVERLAY_POSITIONS = {"top", "center", "bottom"}
MIN_CLIP_S = 0.5
PREVIEW_LONG_SIDE = 1280


class TimelineError(Exception):
    pass


def load_source(source_id: str) -> dict:
    scenes = WORK_ROOT / source_id / "scenes.json"
    if not scenes.exists():
        raise TimelineError(f"source '{source_id}' has not been ingested ({scenes} missing)")
    data = json.loads(scenes.read_text())
    return {
        "original": Path(data["source"]["path"]),
        "proxy": WORK_ROOT / source_id / data["proxy"],
        "duration_s": data["source"]["duration_s"],
    }


def resolve_path(p: str, timeline_path: Path) -> Path:
    for base in (Path.cwd(), timeline_path.parent, EDITING):
        candidate = (base / p).resolve()
        if candidate.exists():
            return candidate
    raise TimelineError(f"file not found: {p}")


def validate(t: dict, timeline_path: Path) -> dict[str, dict]:
    errors = []
    if t.get("version") != 1:
        errors.append("version must be 1")
    if t.get("style") not in STYLES:
        errors.append(f"style must be one of {sorted(STYLES)}")
    out = t.get("output", {})
    if not all(isinstance(out.get(k), (int, float)) and out[k] > 0 for k in ("width", "height", "fps")):
        errors.append("output needs positive width, height and fps")
    if not t.get("clips"):
        errors.append("timeline has no clips")

    sources: dict[str, dict] = {}
    seen = set()
    for i, c in enumerate(t.get("clips", [])):
        where = f"clips[{i}] ({c.get('id', '?')})"
        if c.get("id") in seen:
            errors.append(f"{where}: duplicate id")
        seen.add(c.get("id"))
        try:
            src = sources.setdefault(c["source"], load_source(c["source"]))
        except (KeyError, TimelineError) as e:
            errors.append(f"{where}: {e}")
            continue
        in_s, out_s = c.get("in_s"), c.get("out_s")
        if not (isinstance(in_s, (int, float)) and isinstance(out_s, (int, float))):
            errors.append(f"{where}: in_s and out_s are required numbers")
            continue
        if in_s < 0 or out_s > src["duration_s"]:
            errors.append(f"{where}: [{in_s}, {out_s}] is outside the source (0-{src['duration_s']:.2f}s)")
        if out_s - in_s < MIN_CLIP_S:
            errors.append(f"{where}: shorter than {MIN_CLIP_S}s")
        if c.get("transition_in", "cut") not in TRANSITIONS:
            errors.append(f"{where}: transition_in must be one of {sorted(TRANSITIONS)}")
        if c.get("motion", "none") not in MOTIONS:
            errors.append(f"{where}: motion must be one of {sorted(MOTIONS)}")
        if c.get("transition_in", "cut") != "cut" and c.get("transition_s", 0.5) * 2 > out_s - in_s:
            errors.append(f"{where}: transition_s must be at most half the clip length")
        for k in ("crop_x", "crop_y"):
            if not 0 <= c.get(k, 0.5) <= 1:
                errors.append(f"{where}: {k} must be between 0 and 1")

    for i, o in enumerate(t.get("overlays", [])):
        where = f"overlays[{i}]"
        if not o.get("text"):
            errors.append(f"{where}: text is required")
        if not (0 <= o.get("start_s", -1) < o.get("end_s", -1)):
            errors.append(f"{where}: needs 0 <= start_s < end_s")
        if o.get("style", "caption") not in OVERLAY_STYLES:
            errors.append(f"{where}: style must be one of {sorted(OVERLAY_STYLES)}")
        if o.get("position", "center") not in OVERLAY_POSITIONS:
            errors.append(f"{where}: position must be one of {sorted(OVERLAY_POSITIONS)}")

    for i, e in enumerate(t.get("effects", [])):
        if e.get("type") == "flash" and isinstance(e.get("at_s"), (int, float)):
            continue
        if e.get("type") == "fade_out" and isinstance(e.get("duration_s"), (int, float)):
            continue
        errors.append(f"effects[{i}]: expected {{type: flash, at_s}} or {{type: fade_out, duration_s}}")

    music = t.get("music")
    if music:
        try:
            music["_path"] = resolve_path(music["track"], timeline_path)
        except TimelineError as e:
            errors.append(f"music: {e}")

    if errors:
        raise TimelineError("invalid timeline:\n  " + "\n  ".join(errors))
    return sources


def cut_clip(clip: dict, src: dict, quality: str, size: tuple[int, int], fps: float, stage: Path) -> str:
    original = quality == "final"
    input_path = src["original"] if original else src["proxy"]
    cx, cy = clip.get("crop_x", 0.5), clip.get("crop_y", 0.5)
    key = json.dumps([str(input_path), clip["in_s"], clip["out_s"], size, fps, quality, cx, cy])
    name = f"{clip['id']}-{quality}-{hashlib.sha1(key.encode()).hexdigest()[:8]}.mp4"
    out = stage / name
    if not out.exists():
        w, h = size
        tmp = out.with_suffix(".part.mp4")
        cmd = ["ffmpeg", "-y", "-v", "error"]
        if original:
            cmd += ["-hwaccel", "videotoolbox"]
        cmd += [
            "-ss", f"{clip['in_s']:.3f}", "-i", str(input_path), "-t", f"{clip['out_s'] - clip['in_s']:.3f}",
            "-map", "0:v:0", "-map", "0:a:0?",
            "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}:(iw-ow)*{cx}:(ih-oh)*{cy},"
                   f"fps={fps},format=yuv420p",
            "-c:v", "h264_videotoolbox", "-b:v", "40M" if original else "8M", "-g", str(round(fps)),
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart", str(tmp),
        ]
        subprocess.run(cmd, check=True)
        tmp.rename(out)
    return f"clips/{stage.name}/{name}"


def stage(t: dict, sources: dict[str, dict], quality: str, name: str) -> Path:
    stage_dir = PUBLIC / "clips" / name
    stage_dir.mkdir(parents=True, exist_ok=True)
    out = t["output"]
    scale = preview_scale(out["width"], out["height"]) if quality == "preview" else 1.0
    size = (round(out["width"] * scale / 2) * 2, round(out["height"] * scale / 2) * 2)

    staged = copy.deepcopy(t)
    with ThreadPoolExecutor(max_workers=3 if quality == "final" else 6) as pool:
        srcs = pool.map(
            lambda c: cut_clip(c, sources[c["source"]], quality, size, out["fps"], stage_dir), staged["clips"]
        )
        for clip, src in zip(staged["clips"], srcs):
            clip["src"] = src
            print(f"  staged {clip['id']}: {clip['in_s']:.2f}-{clip['out_s']:.2f}s", flush=True)

    if staged.get("music"):
        music_path = staged["music"].pop("_path")
        dest = stage_dir / f"music{music_path.suffix}"
        shutil.copyfile(music_path, dest)
        staged["music"]["src"] = f"clips/{name}/{dest.name}"

    props = {"timeline": staged}
    props_path = stage_dir / "props.json"
    props_path.write_text(json.dumps(props, indent=2))
    (PUBLIC / "clips" / "current.json").write_text(json.dumps(staged, indent=2))
    return props_path


def preview_scale(width: int, height: int) -> float:
    return min(1.0, PREVIEW_LONG_SIDE / max(width, height))


def render(props_path: Path, quality: str, output: dict, name: str) -> Path:
    RENDERS.mkdir(exist_ok=True)
    out = RENDERS / f"{name}-{quality}.mp4"
    scale = preview_scale(output["width"], output["height"]) if quality == "preview" else 1.0
    cmd = [
        "npx", "remotion", "render", "src/index.ts", "Highlight", str(out),
        f"--props={props_path}", f"--scale={scale}", "--log=warn",
    ]
    subprocess.run(cmd, check=True, cwd=REMOTION)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=width,height",
         "-of", "json", str(out)],
        check=True, capture_output=True, text=True,
    ).stdout
    info = json.loads(probe)
    v = info["streams"][0]
    print(f"rendered {out} ({v['width']}x{v['height']}, {float(info['format']['duration']):.1f}s)")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("timeline", type=Path)
    ap.add_argument("--quality", choices=["preview", "final"], default="preview")
    ap.add_argument("--stage-only", action="store_true", help="stage clips and props for Remotion Studio, no render")
    args = ap.parse_args()

    t = json.loads(args.timeline.read_text())
    name = args.timeline.stem
    try:
        sources = validate(t, args.timeline)
    except TimelineError as e:
        sys.exit(str(e))

    print(f"staging {len(t['clips'])} clips ({args.quality})")
    props_path = stage(t, sources, args.quality, name)
    if args.stage_only:
        print(f"staged; run `npm run studio` in {REMOTION}")
        return
    render(props_path, args.quality, t["output"], name)


if __name__ == "__main__":
    main()
