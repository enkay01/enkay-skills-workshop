"""Encode raw BGRA frames into host-viewable PNG screenshots.

The engine returns tightly packed (or strided) BGRA8 frames. The CLI never
puts raw frame bytes on the wire: it encodes a PNG in memory and writes it
to the session's screenshot directory, returning an absolute path.
"""

from __future__ import annotations

import io
import time
from pathlib import Path
from typing import Any, Dict

from PIL import Image

# Bound how many screenshots one session keeps on disk. Older files are
# deleted as new ones are written; the whole directory is removed on stop.
MAX_SHOTS = 100


def bgra_to_png_bytes(width: int, height: int, stride: int, data: bytes) -> bytes:
    """Convert a BGRA8 frame to PNG bytes.

    ``stride`` is the row pitch in bytes and may exceed ``width * 4``; the
    tail of each row is padding and is dropped.
    """
    width = int(width)
    height = int(height)
    stride = int(stride)
    if width <= 0 or height <= 0:
        raise ValueError(f"Invalid frame dimensions {width}x{height}")
    row_bytes = width * 4
    if stride == row_bytes:
        raw = data[: row_bytes * height]
    else:
        raw = b"".join(
            data[y * stride : y * stride + row_bytes] for y in range(height)
        )
    if len(raw) < row_bytes * height:
        raise ValueError(
            f"Frame buffer too short: got {len(raw)} bytes, "
            f"need {row_bytes * height} for {width}x{height}"
        )
    # PIL decodes "RGBA" as R,G,B,A; our bytes are B,G,R,A.
    img = Image.frombytes("RGBA", (width, height), raw)
    b, g, r, _a = img.split()
    rgb = Image.merge("RGB", (r, g, b))
    buf = io.BytesIO()
    rgb.save(buf, format="PNG", optimize=False)
    return buf.getvalue()


def save_observation_png(
    shots_dir: Path, meta: Dict[str, Any], payload: bytes
) -> Path:
    """Write one observation as a PNG and enforce the retention bound."""
    shots_dir.mkdir(parents=True, exist_ok=True)
    observation_id = meta.get("observation_id", 0)
    stamp = int(time.time() * 1000) % 10_000_000
    path = shots_dir / f"obs_{observation_id:06d}_{stamp}.png"
    png = bgra_to_png_bytes(
        meta["width"], meta["height"], meta["stride_bytes"], payload
    )
    path.write_bytes(png)
    _enforce_retention(shots_dir)
    return path


def _enforce_retention(shots_dir: Path) -> None:
    """Keep only the most recent ``MAX_SHOTS`` screenshots."""
    shots = sorted(shots_dir.glob("obs_*.png"))
    excess = len(shots) - MAX_SHOTS
    for old in shots[: max(0, excess)]:
        try:
            old.unlink()
        except OSError:
            pass
