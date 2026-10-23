# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow>=11"]
# ///
"""Show how each clip will be framed when the output aspect differs from the source.

Usage:
    uv run editing/cropcheck.py editing/timelines/renfest_reel.json [clip_id,clip_id,...]

For every clip it shows the first, middle and last frame of the source with the output
window (from crop_x / crop_y) outlined in green, the rest dimmed, and a 0-10 ruler across
the top. To center a subject at ruler position r (0-10) horizontally:
    crop_x = (r / 10 - window / 2) / (1 - window)
where window is the printed window fraction. Sheets go to editing/work/cropcheck/<timeline>/.
"""

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

EDITING = Path(__file__).resolve().parent
W, H, LABEL_H = 384, 216, 20
PER_SHEET = 8


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    timeline_path = Path(sys.argv[1])
    only = set(sys.argv[2].split(",")) if len(sys.argv) > 2 else None
    t = json.loads(timeline_path.read_text())
    out_dir = EDITING / "work" / "cropcheck" / timeline_path.stem
    frames_dir = out_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("crop_*.jpg"):
        old.unlink()

    ow, oh = t["output"]["width"], t["output"]["height"]
    win_w = min(1.0, (H * ow / oh) / W)  # window size as a fraction of source width/height
    win_h = min(1.0, (W * oh / ow) / H)
    clips = [c for c in t["clips"] if not only or c["id"] in only]

    proxies = {}
    jobs = []
    for c in clips:
        if c["source"] not in proxies:
            scenes = json.loads((EDITING / "work" / c["source"] / "scenes.json").read_text())
            proxies[c["source"]] = EDITING / "work" / c["source"] / scenes["proxy"]
        for at in (c["in_s"] + 0.04, (c["in_s"] + c["out_s"]) / 2, c["out_s"] - 0.06):
            at = round(at, 2)
            jobs.append((c, at, frames_dir / f"{c['source']}_{at}.jpg"))

    def grab(job) -> None:
        c, at, path = job
        if not path.exists():
            subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-ss", str(at), "-i", str(proxies[c["source"]]),
                 "-frames:v", "1", "-vf", f"scale={W}:{H}", str(path)],
                check=True,
            )

    with ThreadPoolExecutor(8) as pool:
        list(pool.map(grab, jobs))

    font = ImageFont.load_default(size=14)
    small = ImageFont.load_default(size=11)
    for n in range(0, len(clips), PER_SHEET):
        batch = clips[n: n + PER_SHEET]
        sheet = Image.new("RGB", (3 * W, len(batch) * (H + LABEL_H)), "black")
        draw = ImageDraw.Draw(sheet)
        for row, c in enumerate(batch):
            y = row * (H + LABEL_H)
            cx, cy = c.get("crop_x", 0.5), c.get("crop_y", 0.5)
            x0, y0 = (1 - win_w) * cx * W, (1 - win_h) * cy * H
            box = [x0, y0, x0 + win_w * W, y0 + win_h * H]
            for col, (_, _, path) in enumerate(j for j in jobs if j[0] is c):
                x = col * W
                with Image.open(path) as im:
                    im = im.convert("RGB")
                    mask = Image.new("L", im.size, 110)
                    ImageDraw.Draw(mask).rectangle(box, fill=0)
                    sheet.paste(Image.composite(Image.new("RGB", im.size), im, mask), (x, y))
                draw.rectangle([x + box[0], y + box[1], x + box[2], y + box[3] - 1], outline=(0, 255, 0), width=2)
                for i in range(11):
                    gx = x + i * W / 10
                    draw.line([(gx, y), (gx, y + 8)], fill="yellow")
                    draw.text((gx + 2, y + 8), str(i), fill="yellow", font=small)
            label = f"{c['id']}  in {c['in_s']}  dur {c['out_s'] - c['in_s']:.2f}  crop_x {cx}  crop_y {cy}"
            draw.text((4, y + H + 2), label, fill="white", font=font)
        sheet.save(out_dir / f"crop_{n // PER_SHEET + 1:02d}.jpg", quality=82)

    print(f"window fraction w={win_w:.4f} h={win_h:.4f}; sheets in {out_dir}")


if __name__ == "__main__":
    main()
