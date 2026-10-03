"""Turn the recorded demo video into a README GIF (two-pass palette, small file).

    uv run --with imageio-ffmpeg python scripts/make_demo_gif.py \
        [--src docs/demo/demo.webm] [--out docs/demo/demo.gif] [--width 800] [--fps 5] [--speed 2.3]

imageio-ffmpeg ships a static ffmpeg binary, so nothing needs installing system-wide.
"""

from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path

import imageio_ffmpeg


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="docs/demo/demo.webm")
    ap.add_argument("--out", default="docs/demo/demo.gif")
    ap.add_argument("--width", type=int, default=800)
    ap.add_argument("--fps", type=int, default=5)
    ap.add_argument("--speed", type=float, default=2.3, help=">1 plays faster than real time")
    a = ap.parse_args()

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    base = f"setpts=PTS/{a.speed},fps={a.fps},scale={a.width}:-1:flags=lanczos"
    with tempfile.TemporaryDirectory() as tmp:
        palette = Path(tmp) / "palette.png"
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-loglevel",
                "error",
                "-i",
                a.src,
                "-vf",
                f"{base},palettegen=max_colors=64:stats_mode=diff",
                str(palette),
            ],
            check=True,
        )
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-loglevel",
                "error",
                "-i",
                a.src,
                "-i",
                str(palette),
                "-lavfi",
                f"{base}[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle",
                "-loop",
                "0",
                a.out,
            ],
            check=True,
        )
    size = Path(a.out).stat().st_size / 1e6
    print(f"wrote {a.out} ({size:.1f} MB)")


if __name__ == "__main__":
    main()
