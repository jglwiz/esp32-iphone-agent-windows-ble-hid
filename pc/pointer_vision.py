"""Estimate pointer position from two frames and a known move.

No device I/O. Coordinates are screenshot pixels, not native screen points.
Conservative heuristic for the sampled 496x1080 AirPlay stream only.
"""

import cv2
import numpy as np


def locate(before, after, dx, dy):
    def unknown(reason):
        return {"status": "unknown", "reason": reason}

    if before is None or after is None or before.shape != after.shape:
        return unknown("missing_or_mismatched_frames")
    if before.shape[:2] != (1080, 496):
        return unknown("uncalibrated_resolution")
    direction = np.array([dx, dy], dtype=float)
    norm = np.linalg.norm(direction)
    if norm == 0:
        return unknown("no_known_motion")
    direction /= norm
    mask = (cv2.absdiff(before, after).max(axis=2) > 25).astype(np.uint8)
    if mask.mean() > 0.04:
        return unknown("scene_changed")
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    _, _, stats, centers = cv2.connectedComponentsWithStats(mask)
    candidates = []
    for stat, center in zip(stats[1:], centers[1:]):
        x, y, w, h, area = stat
        if (x > 0 and y > 0 and x+w < 496 and y+h < 1080 and
                20 <= w <= 42 and 20 <= h <= 42 and
                0.65 <= w / h <= 1.5 and 300 <= area <= 1300 and
                area / (w * h) >= 0.45):
            candidates.append(center)
        elif (x == 0 or y == 0 or x+w == 496 or y+h == 1080):
            # A clipped disk's visible centroid is biased inward. Reconstruct its
            # center from the opposite rim using the measured ~14 px radius.
            horizontal_clip = x == 0 or x+w == 496
            vertical_clip = y == 0 or y+h == 1080
            if (12 <= w <= 32 and 12 <= h <= 32 and 110 <= area <= 850
                    and area / (w*h) >= 0.45
                    and (horizontal_clip or 24 <= w <= 32)
                    and (vertical_clip or 24 <= h <= 32)):
                radius = 14.0
                cx, cy = center
                if x == 0:
                    cx = x+w-1-radius
                elif x+w == 496:
                    cx = x+radius
                if y == 0:
                    cy = y+h-1-radius
                elif y+h == 1080:
                    cy = y+radius
                candidates.append(np.array([np.clip(cx, 0, 495), np.clip(cy, 0, 1079)]))
    # Require two distinct, compact footprints: disappearance alone is not motion.
    if len(candidates) != 2:
        return unknown(f"expected_two_footprints_got_{len(candidates)}")
    old, new = sorted(candidates, key=lambda p: float(p @ direction))
    delta = new - old
    distance = np.linalg.norm(delta)
    alignment = float(delta @ direction / max(distance, 1e-9))
    if distance < 35 or alignment < 0.95:
        return unknown("motion_direction_or_separation_mismatch")
    return {"status": "estimated", "x": round(float(new[0]), 2),
            "y": round(float(new[1]), 2), "old_center": old.tolist(),
            "direction_alignment": alignment,
            "method": "two_frame_motion_footprints"}

