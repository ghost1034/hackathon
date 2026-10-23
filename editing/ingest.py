# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow>=11"]
# ///
"""Ingest a long-form source video into material an AI editor can work with.

Usage:
    uv run editing/ingest.py renfest-b-roll.mp4 --id renfest

Writes editing/work/<id>/:
    source.json   ffprobe summary of the original file
    proxy.mp4     720p proxy for previews and analysis (one 4K decode, shared with scene detection)
    scenes.json   shots split into <= --max-segment second segments, with frame, audio and sharpness
    frames/       one 384 px frame per segment
    sheets/       labeled contact sheets (16 segments each) for visual review

Each step is skipped when its output already exists; pass --force to redo everything.
"""

import argparse
import array
import json
import math
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageStat

WORK_ROOT = Path(__file__).resolve().parent / "work"
FRAME_W, FRAME_H = 384, 216
SHEET_COLS, SHEET_ROWS = 4, 4
LABEL_H = 22
AUDIO_RATE = 8000


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def probe(src: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(src)],
        check=True, capture_output=True, text=True,
    ).stdout
    data = json.loads(out)
    video = next(s for s in data["streams"] if s["codec_type"] == "video")
    audio = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    num, den = (int(x) for x in video["r_frame_rate"].split("/"))
    return {
        "path": str(src.resolve()),
        "duration_s": float(data["format"]["duration"]),
        "width": video["width"],
        "height": video["height"],
        "fps": num / den,
        "fps_fraction": video["r_frame_rate"],
        "video_codec": video["codec_name"],
        "has_audio": audio is not None,
        "size_bytes": int(data["format"]["size"]),
    }


def make_proxy_and_detect_cuts(src: Path, work: Path, threshold: float) -> list[float]:
    """Decode the source once: write the proxy and log scene-change timestamps together."""
    proxy, log_path = work / "proxy.mp4", work / "proxy.log"
    filtergraph = (
        "[0:v]split=2[p][d];"
        "[p]scale=1280:720:flags=bicubic,format=yuv420p[pv];"
        f"[d]scale=320:180,select='gt(scene,{threshold})',showinfo[dv]"
    )
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-stats_period", "15",
        "-hwaccel", "videotoolbox", "-i", str(src),
        "-filter_complex", filtergraph,
        "-map", "[pv]", "-map", "0:a?",
        "-c:v", "h264_videotoolbox", "-b:v", "5M", "-g", "24",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(proxy),
        "-map", "[dv]", "-f", "null", "-",
    ]
    log(f"encoding proxy + detecting cuts (log: {log_path})")
    with open(log_path, "w") as f:
        subprocess.run(cmd, check=True, stderr=f)
    return parse_cuts(log_path)


def parse_cuts(log_path: Path) -> list[float]:
    pattern = re.compile(r"Parsed_showinfo.*?pts_time:\s*([\d.]+)")
    return sorted(float(m.group(1)) for m in pattern.finditer(log_path.read_text()))


def build_segments(cuts: list[float], duration: float, min_shot: float, max_segment: float) -> list[dict]:
    bounds = [0.0]
    for t in cuts:
        if t - bounds[-1] >= min_shot:
            bounds.append(t)
    if duration - bounds[-1] < min_shot and len(bounds) > 1:
        bounds.pop()
    bounds.append(duration)

    segments = []
    for shot, (start, end) in enumerate(zip(bounds, bounds[1:])):
        parts = max(1, math.ceil((end - start) / max_segment))
        step = (end - start) / parts
        for i in range(parts):
            s, e = start + i * step, start + (i + 1) * step
            segments.append({
                "id": f"s{len(segments) + 1:04d}",
                "shot": shot + 1,
                "start_s": round(s, 3),
                "end_s": round(e, 3),
                "duration_s": round(e - s, 3),
            })
    return segments


def extract_frames(proxy: Path, segments: list[dict], frames_dir: Path) -> None:
    frames_dir.mkdir(exist_ok=True)

    def grab(seg: dict) -> None:
        out = frames_dir / f"{seg['id']}.jpg"
        seg["frame"] = str(out.relative_to(frames_dir.parent))
        if out.exists():
            return
        mid = (seg["start_s"] + seg["end_s"]) / 2
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", f"{mid:.3f}", "-i", str(proxy),
             "-frames:v", "1", "-vf", f"scale={FRAME_W}:{FRAME_H}", "-q:v", "3", str(out)],
            check=True,
        )

    log(f"extracting {len(segments)} frames")
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(grab, segments))


def score_sharpness(segments: list[dict], work: Path) -> None:
    """Edge-energy variance of the grayscale frame. Relative only: compare within one source."""
    for seg in segments:
        with Image.open(work / seg["frame"]) as im:
            edges = im.convert("L").filter(ImageFilter.FIND_EDGES)
            seg["sharpness"] = round(ImageStat.Stat(edges).var[0], 1)


def measure_audio(proxy: Path, segments: list[dict]) -> None:
    """Per-segment RMS and peak loudness in dBFS from a mono 8 kHz decode of the proxy."""
    log("measuring audio loudness")
    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", str(proxy), "-vn", "-ac", "1", "-ar", str(AUDIO_RATE), "-f", "s16le", "-"],
        stdout=subprocess.PIPE,
    )
    per_second: list[tuple[float, int]] = []  # (mean square, peak) for each second
    while chunk := proc.stdout.read(AUDIO_RATE * 2):
        samples = array.array("h", chunk[: len(chunk) - len(chunk) % 2])
        if samples:
            per_second.append((sum(x * x for x in samples) / len(samples), max(map(abs, samples))))
    proc.wait()

    def dbfs(v: float) -> float:
        return round(20 * math.log10(v / 32768), 1) if v > 0 else -120.0

    for seg in segments:
        window = per_second[int(seg["start_s"]): max(int(seg["start_s"]) + 1, int(seg["end_s"]))]
        if not window:
            seg["audio"] = None
            continue
        seg["audio"] = {
            "rms_db": dbfs(math.sqrt(sum(ms for ms, _ in window) / len(window))),
            "peak_db": dbfs(max(p for _, p in window)),
        }


def fmt_tc(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def make_sheets(segments: list[dict], work: Path) -> None:
    sheets_dir = work / "sheets"
    sheets_dir.mkdir(exist_ok=True)
    per_sheet = SHEET_COLS * SHEET_ROWS
    font = ImageFont.load_default(size=16)
    cell_h = FRAME_H + LABEL_H

    for n in range(0, len(segments), per_sheet):
        batch = segments[n: n + per_sheet]
        sheet = Image.new("RGB", (SHEET_COLS * FRAME_W, SHEET_ROWS * cell_h), "black")
        draw = ImageDraw.Draw(sheet)
        for i, seg in enumerate(batch):
            x, y = (i % SHEET_COLS) * FRAME_W, (i // SHEET_COLS) * cell_h
            with Image.open(work / seg["frame"]) as im:
                sheet.paste(im, (x, y))
            label = f"{seg['id']}  {fmt_tc(seg['start_s'])}  {seg['duration_s']:.1f}s  shot {seg['shot']}"
            draw.text((x + 6, y + FRAME_H + 2), label, fill="white", font=font)
        name = f"sheet_{n // per_sheet + 1:03d}_{batch[0]['id']}-{batch[-1]['id']}.jpg"
        sheet.save(sheets_dir / name, quality=85)
    log(f"wrote {math.ceil(len(segments) / per_sheet)} contact sheets to {sheets_dir}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", type=Path)
    ap.add_argument("--id", required=True, help="short source id, e.g. renfest")
    ap.add_argument("--threshold", type=float, default=0.3, help="scene-change sensitivity (0-1, lower = more cuts)")
    ap.add_argument("--min-shot", type=float, default=1.0, help="merge cuts closer than this many seconds")
    ap.add_argument("--max-segment", type=float, default=10.0, help="split long shots into segments of at most this length")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    if not args.source.exists():
        sys.exit(f"source not found: {args.source}")
    work = WORK_ROOT / args.id
    if args.force:
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)

    info = probe(args.source)
    (work / "source.json").write_text(json.dumps(info, indent=2))
    log(f"source: {info['width']}x{info['height']} @ {info['fps']:.3f} fps, {fmt_tc(info['duration_s'])}")

    proxy = work / "proxy.mp4"
    if not proxy.exists() or not (work / "proxy.log").exists():
        cuts = make_proxy_and_detect_cuts(args.source, work, args.threshold)
    else:
        log("proxy exists, reusing detected cuts")
        cuts = parse_cuts(work / "proxy.log")
    log(f"{len(cuts)} raw scene changes")

    segments = build_segments(cuts, info["duration_s"], args.min_shot, args.max_segment)
    extract_frames(proxy, segments, work / "frames")
    score_sharpness(segments, work)
    if info["has_audio"]:
        measure_audio(proxy, segments)
    make_sheets(segments, work)

    (work / "scenes.json").write_text(json.dumps({
        "source_id": args.id,
        "source": info,
        "proxy": "proxy.mp4",
        "params": {"threshold": args.threshold, "min_shot": args.min_shot, "max_segment": args.max_segment},
        "shot_count": segments[-1]["shot"] if segments else 0,
        "segments": segments,
    }, indent=2))
    log(f"done: {len(segments)} segments in {segments[-1]['shot'] if segments else 0} shots -> {work / 'scenes.json'}")


if __name__ == "__main__":
    main()
