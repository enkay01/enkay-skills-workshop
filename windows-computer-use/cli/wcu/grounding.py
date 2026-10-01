"""Runtime-parameterised visual grounding primitives for the session server.

General primitives only: every reference input (template image, colour,
region, threshold) is supplied at runtime by the caller. No application
names, no curated assets, no stored template library.

These run inside the session process against PNGs it has already written,
so a grounding call re-reads the same bytes the caller was shown.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


def parse_hex_color(s: str) -> Tuple[int, int, int]:
    """Parse "#RRGGBB", "RRGGBB" (also 3-digit "#RGB"/"RGB") into (b, g, r)."""
    if not isinstance(s, str):
        raise ValueError(f"Colour must be a string, got {type(s).__name__}")
    text = s.strip()
    if text.startswith("#"):
        text = text[1:]
    if len(text) == 3:
        if not text or any(c not in _HEX_DIGITS for c in text):
            raise ValueError(f"Invalid hex colour: {s!r}")
        r = int(text[0] * 2, 16)
        g = int(text[1] * 2, 16)
        b = int(text[2] * 2, 16)
    elif len(text) == 6:
        if any(c not in _HEX_DIGITS for c in text):
            raise ValueError(f"Invalid hex colour: {s!r}")
        r = int(text[0:2], 16)
        g = int(text[2:4], 16)
        b = int(text[4:6], 16)
    else:
        raise ValueError(f"Invalid hex colour: {s!r}")
    return (b, g, r)


def clamp_region(frame_w: int, frame_h: int, region: Any) -> Tuple[int, int, int, int]:
    """Clamp [x, y, w, h] (frame px) to frame bounds.

    Returns (cx, cy, cw, ch) where (cx, cy) is the clamped origin and
    (cw, ch) the clamped size.

    Raises ValueError when w/h <= 0, the region has the wrong shape, or the
    origin lies outside the frame. Sizes extending past the frame edge are
    clamped; origins are not.
    """
    try:
        x, y, w, h = (int(v) for v in region)
    except Exception:
        raise ValueError(f"Invalid region (expected [x, y, w, h]): {region!r}")
    frame_w = int(frame_w)
    frame_h = int(frame_h)
    if w <= 0 or h <= 0:
        raise ValueError(f"Invalid region dimensions w={w} h={h}")
    if x < 0 or y < 0 or x >= frame_w or y >= frame_h:
        raise ValueError(
            f"Region origin outside frame {frame_w}x{frame_h}: {[x, y, w, h]}"
        )
    cx = x
    cy = y
    cw = min(w, frame_w - cx)
    ch = min(h, frame_h - cy)
    return cx, cy, cw, ch


def _clamp_region(
    image: np.ndarray, region: Sequence[int]
) -> Tuple[int, int, np.ndarray]:
    """Clamp (x, y, w, h) to image bounds; return (origin_x, origin_y, crop)."""
    img_h, img_w = image.shape[:2]
    x, y, w, h = (int(v) for v in region)
    if w <= 0 or h <= 0:
        raise ValueError(f"Empty region: {tuple(region)}")
    x1 = max(0, x)
    y1 = max(0, y)
    x2 = min(img_w, x + w)
    y2 = min(img_h, y + h)
    if x2 <= x1 or y2 <= y1:
        raise ValueError(f"Region outside image bounds: {tuple(region)}")
    return x1, y1, image[y1:y2, x1:x2]


def _iou(a: Sequence[int], b: Sequence[int]) -> float:
    ax, ay, aw, ah = (float(v) for v in a)
    bx, by, bw, bh = (float(v) for v in b)
    ix1, iy1 = max(ax, bx), max(ay, by)
    ix2, iy2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    iw, ih = ix2 - ix1, iy2 - iy1
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def match_template(
    image_bgr: np.ndarray,
    template_bgr: np.ndarray,
    region: Optional[Sequence[int]] = None,
    threshold: float = 0.8,
    scales: Sequence[float] = (0.8, 1.0, 1.2),
) -> List[Dict[str, Any]]:
    """Grayscale TM_CCOEFF_NORMED template match at each template scale.

    Returns [{"bbox": [x, y, w, h], "confidence": float}] in full-image
    pixels, sorted by confidence descending with IoU>0.5 NMS. Empty list
    when the template exceeds the search area at every scale.
    """
    if (
        image_bgr is None
        or template_bgr is None
        or image_bgr.size == 0
        or template_bgr.size == 0
    ):
        raise ValueError("image_bgr and template_bgr must be non-empty arrays")
    if region is None:
        origin_x, origin_y = 0, 0
        search = image_bgr
    else:
        origin_x, origin_y, search = _clamp_region(image_bgr, region)

    search_gray = cv2.cvtColor(search, cv2.COLOR_BGR2GRAY)
    tmpl_gray_full = cv2.cvtColor(template_bgr, cv2.COLOR_BGR2GRAY)
    base_h, base_w = tmpl_gray_full.shape[:2]
    search_h, search_w = search_gray.shape[:2]

    hits: List[Dict[str, Any]] = []
    for scale in scales:
        sw = max(1, int(round(base_w * float(scale))))
        sh = max(1, int(round(base_h * float(scale))))
        if sw > search_w or sh > search_h:
            continue
        if sw == base_w and sh == base_h:
            tmpl = tmpl_gray_full
        else:
            interp = cv2.INTER_AREA if float(scale) < 1.0 else cv2.INTER_LINEAR
            tmpl = cv2.resize(tmpl_gray_full, (sw, sh), interpolation=interp)
        result = cv2.matchTemplate(search_gray, tmpl, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(result >= threshold)
        for x, y in zip(xs.tolist(), ys.tolist()):
            hits.append(
                {
                    "bbox": [int(x + origin_x), int(y + origin_y), int(sw), int(sh)],
                    "confidence": float(result[int(y), int(x)]),
                }
            )

    hits.sort(key=lambda d: d["confidence"], reverse=True)
    kept: List[Dict[str, Any]] = []
    for cand in hits[:1000]:
        if all(_iou(cand["bbox"], k["bbox"]) <= 0.5 for k in kept):
            kept.append(cand)
    return kept


def find_color(
    image_bgr: np.ndarray,
    hex_color: str,
    tolerance: int = 30,
    region: Optional[Sequence[int]] = None,
    min_area: int = 25,
) -> List[Dict[str, Any]]:
    """Segment pixels within per-channel tolerance of a BGR colour.

    Returns [{"bbox": [x, y, w, h], "centroid": [cx, cy],
    "confidence": coverage fraction 0..1}] sorted by area descending.
    """
    if image_bgr is None or image_bgr.size == 0:
        raise ValueError("image_bgr must be a non-empty array")
    b, g, r = parse_hex_color(hex_color)
    if region is None:
        origin_x, origin_y = 0, 0
        search = image_bgr
    else:
        origin_x, origin_y, search = _clamp_region(image_bgr, region)

    target = np.array([b, g, r], dtype=np.int16)
    diff = np.abs(search.astype(np.int16) - target)
    mask = (np.all(diff <= int(tolerance), axis=2)).astype(np.uint8) * 255
    closed = cv2.morphologyEx(
        mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8)
    )
    num, _labels, stats, centroids = cv2.connectedComponentsWithStats(
        closed, connectivity=8
    )

    found: List[Dict[str, Any]] = []
    for i in range(1, num):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area < min_area:
            continue
        x = int(stats[i, cv2.CC_STAT_LEFT])
        y = int(stats[i, cv2.CC_STAT_TOP])
        w = int(stats[i, cv2.CC_STAT_WIDTH])
        h = int(stats[i, cv2.CC_STAT_HEIGHT])
        cx, cy = centroids[i]
        coverage = float(area) / float(w * h) if w * h > 0 else 0.0
        found.append(
            {
                "bbox": [x + origin_x, y + origin_y, w, h],
                "centroid": [
                    int(round(float(cx) + origin_x)),
                    int(round(float(cy) + origin_y)),
                ],
                "confidence": max(0.0, min(1.0, coverage)),
                "_area": area,
            }
        )
    found.sort(key=lambda d: d["_area"], reverse=True)
    for d in found:
        del d["_area"]
    return found