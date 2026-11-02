"""Estimate a partially visible disk from its arc, never from the requested target."""
import cv2
import numpy as np


def disk_reference(mask):
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if len(contours) != 1: return None
    (x, y), radius = cv2.minEnclosingCircle(contours[0])
    if not 10 <= radius <= 20: return None
    if np.count_nonzero(mask)/(np.pi*radius*radius) < .82: return None
    return float(radius)


def arc_center(mask, radius):
    """Fixed-radius envelope fit; ambiguous or insufficient arcs are rejected.

    Radius is measured from the other, complete footprint in this transition.
    Target coordinates are deliberately not an input. Near-optimal centers must
    agree within two pixels; this is separate from the existing 8px target gate.
    """
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if len(contours) != 1: return None
    points = contours[0][:, 0, :].astype(float)
    if len(points) < 24: return None
    lo, hi = points.min(axis=0), points.max(axis=0)
    xs=np.arange(max(0,hi[0]-radius-1), min(495,lo[0]+radius+1)+.1,.5)
    ys=np.arange(max(0,hi[1]-radius-1), min(1079,lo[1]+radius+1)+.1,.5)
    if not len(xs) or not len(ys): return None
    centers=np.stack(np.meshgrid(xs,ys),axis=-1).reshape(-1,2)
    distances=np.linalg.norm(points[None,:,:]-centers[:,None,:],axis=2)
    # Fit the exterior envelope, not the interior boundary created by occlusion.
    feasible=(distances.max(axis=1)<=radius+1.25)
    support=np.abs(distances-radius)<=1.0
    scores=support.sum(axis=1)
    scores[~feasible]=0
    best=int(scores.max())
    if best < 24: return None
    winners=centers[scores>=best-1]
    center=centers[int(scores.argmax())]
    if np.max(np.linalg.norm(winners-center,axis=1)) > 2: return None
    edge=points[support[int(scores.argmax())]]-center
    angles=np.sort(np.mod(np.arctan2(edge[:,1],edge[:,0]),2*np.pi))
    span=2*np.pi-np.diff(np.r_[angles,angles[0]+2*np.pi]).max()
    if span < np.deg2rad(120): return None
    return {'center':center.tolist(),'radius':radius,'arc_degrees':float(np.rad2deg(span)),
            'support_points':best,'center_spread_px':float(np.max(np.linalg.norm(winners-center,axis=1)))}
