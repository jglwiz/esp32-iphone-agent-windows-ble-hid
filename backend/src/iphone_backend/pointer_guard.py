"""Fail-closed visual response checks for a fixed portrait native pointer.

Receive timestamps alone are insufficient. A no-button probe creates a visible
challenge: two compact changed footprints must match probe and target, and the
response must persist across distinct frames. Never fit or change direction here.
"""
import base64
import hashlib
import json
import time
import uuid
import cv2
import numpy as np
from iphone_backend.native_pointer import map_target
from iphone_backend.pointer_geometry import disk_reference, arc_center, clipped_disk_center


def decode(raw):
    im = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if im is None or im.shape != (1080, 496, 3):
        raise ValueError('pointer_guard: invalid portrait geometry')
    return im


def response(baseline, image, probe, target):
    mask = (cv2.absdiff(image, baseline).max(axis=2) > 22).astype(np.uint8)
    # Ignore status-bar updates except where a challenged pointer may appear.
    top = mask[:70].copy()
    mask[:70] = 0
    for px, py in (probe, target):
        x0,x1=max(0,int(px)-30),min(496,int(px)+31)
        y0,y1=max(0,int(py)-30),min(70,int(py)+31)
        if y0 < y1: mask[y0:y1,x0:x1] = top[y0:y1,x0:x1]
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    # Segment compact footprints without thin connected tails. Keep the original
    # mask for the outside-change budget: removed pixels are not silently ignored.
    compact = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, labels, stats, centers = cv2.connectedComponentsWithStats(compact)
    candidates = []
    for index, (stat, center) in enumerate(zip(stats[1:], centers[1:]), 1):
        x, y, w, h, area = map(int, stat)
        if 10 <= w <= 48 and 10 <= h <= 48 and 80 <= area <= 1600:
            candidates.append({'center':center.tolist(), 'box':[x,y,w,h], 'area':area, 'label':index})
    radii=[disk_reference((labels==c['label']).astype(np.uint8)) for c in candidates]
    for c,r in zip(candidates,radii):
        c['position_verified']=r is not None
    # A boundary-clipped circular arc can measure its own radius even when the
    # other footprint overlaps an icon and is incomplete too.
    for i,(c,r) in enumerate(zip(candidates,radii)):
        if r is None:
            estimate=clipped_disk_center((labels==c['label']).astype(np.uint8))
            if estimate is not None:
                c.update(centroid=c['center'],arc_fit=estimate,center=estimate['center'],position_verified=True)
                radii[i]=estimate['radius']
    complete=[r for r in radii if r is not None]
    if complete and max(complete)-min(complete)<=2:
        radius=float(np.median(complete))
        for c,r in zip(candidates,radii):
            if r is None:
                estimate=arc_center((labels==c['label']).astype(np.uint8),radius)
                c['centroid']=c['center']
                c['arc_fit']=estimate
                c['position_verified']=estimate is not None
                if estimate is not None: c['center']=estimate['center']
    matches = []
    for point in (probe, target):
        near = [c for c in candidates if c.get('position_verified',True) and np.linalg.norm(np.array(c['center'])-point) <= 8]
        if len(near) != 1:
            return {'valid':False, 'reason':'response_missing_or_position_mismatch', 'candidates':candidates}
        matches.append(near[0])
    if matches[0]['label'] == matches[1]['label']:
        return {'valid':False, 'reason':'ambiguous_response', 'candidates':candidates}
    explained = np.isin(labels, [c['label'] for c in matches])
    # Opening removes thin edge pixels of the same disk. Attribute only original
    # changed pixels inside the measured disk envelope, never a larger target ROI.
    yy,xx=np.indices(mask.shape)
    for c in matches:
        fitted=c.get('arc_fit')
        r=fitted['radius'] if fitted else disk_reference((labels==c['label']).astype(np.uint8))
        if r is not None:
            cx,cy=c['center']
            explained |= ((xx-cx)**2+(yy-cy)**2 <= (r+1.25)**2)
    residual = (mask & ~explained).astype(np.uint8)
    outside = int(np.count_nonzero(residual))
    localized=[]
    if outside > 80:
        _,_,extra_stats,extra_centers=cv2.connectedComponentsWithStats(residual)
        accounted=0
        for stat,c in zip(extra_stats[1:],extra_centers[1:]):
            x,y,w,h,area=map(int,stat)
            if area>=40:
                if w>48 or h>48 or min(np.linalg.norm(c-point) for point in (probe,target))<50:
                    localized=[];break
                localized.append({'box':[x,y,w,h],'center':c.tolist(),'area':area});accounted+=area
        if not 1<=len(localized)<=2 or accounted>1600 or outside-accounted>80:localized=[]
    return {'valid':outside <= 80, 'reason':'matched' if outside <= 80 else 'scene_changed',
            'center':matches[1]['center'], 'outside_pixels':outside, 'candidates':candidates,
            'localized_scene_changes':localized}



class Stability:
    def __init__(self):
        self.rows=[]
        self.seen=set()
    def add(self, frame_id, now, observation):
        if frame_id in self.seen: return False
        self.seen.add(frame_id)
        if not observation['valid']:
            self.rows=[]
            return False
        center=observation['center']
        if self.rows and np.linalg.norm(np.array(center)-self.rows[0][1]) > 2:
            self.rows=[]
        self.rows.append((now, center))
        return len(self.rows)>=3 and now-self.rows[0][0]>=.12


def choose_anchor(image, target, probe, exclude=None):
    """Prefer a low-texture interior patch; still verify its actual response."""
    candidates = []
    for y in (180,360,540,720,900):
        for x in (80,160,248,336,416):
            if exclude is not None and np.linalg.norm(np.array((x,y))-exclude)<80:continue
            if min(np.linalg.norm(np.array((x,y))-p) for p in (target,probe)) < 80:
                continue
            patch = image[y-24:y+25,x-24:x+25].astype(float)
            texture = float(np.mean(np.std(patch,axis=(0,1))))
            candidates.append((texture, (x-248)**2+(y-540)**2, x, y))
    _,_,x,y = min(candidates)
    return [x,y]


def anchor_response(before, image, anchor):
    """Require an observed changed disk at the anchor, not elapsed time alone."""
    mask = (cv2.absdiff(image, before).max(axis=2) > 22).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    count, labels, stats, centers = cv2.connectedComponentsWithStats(mask)
    matches = []
    for i in range(1, count):
        if np.linalg.norm(centers[i] - anchor) <= 8 and disk_reference((labels == i).astype(np.uint8)) is not None:
            matches.append(centers[i].tolist())
    return {'valid': len(matches) == 1, 'center': matches[0] if len(matches) == 1 else None}


def guard(worker, target, profile, cancel, deadline, *, review_local_scene=False):
    folder=worker.runtime/'pointer-checks'/uuid.uuid4().hex
    folder.mkdir(parents=True)
    probe=[target[0]+80 if target[0]<248 else target[0]-80,
           target[1]+100 if target[1]<540 else target[1]-100]
    evidence={'version':9,'target':list(target),'probe':probe,'frames':[], 'scene_changes':[], 'state':'checking',
              'profile_id':(profile or {}).get('profile_id'),
              'connection':{k:(getattr(worker,'status',None) or {}).get(k) for k in ('device_id','boot_id','connection_generation')}}
    source=getattr(worker,'geometry_source',None)
    last_sequence=-1
    last_time=-1
    seen=set()
    def snapshot(phase):
        nonlocal source, last_sequence, last_time
        worker.check(cancel, deadline)
        status, frame=worker.capture(None,cancel,deadline,geometry_only=True)
        if frame is None: raise RuntimeError('pointer_guard: frame unavailable')
        sid=frame['frame_id'].rsplit(':',1)[0]
        if source is not None and sid!=source: raise RuntimeError('pointer_guard: capture source changed')
        source=sid
        if frame['age_seconds']>2: return None
        if frame['frame_id'] in seen: return None
        sequence=int(frame['frame_id'].rsplit(':',1)[1])
        if sequence<=last_sequence or frame['frame_time']<last_time:
            raise RuntimeError('pointer_guard: frame order regressed')
        last_sequence=sequence;last_time=frame['frame_time']
        seen.add(frame['frame_id'])
        raw=base64.b64decode(frame['jpeg_base64']);im=decode(raw)
        path=folder/f'{len(evidence["frames"]):03d}.jpg';path.write_bytes(raw)
        row={k:v for k,v in frame.items() if k!='jpeg_base64'}
        row.update(phase=phase,path=str(path),sha256=hashlib.sha256(raw).hexdigest())
        evidence['frames'].append(row)
        return im,row
    preserve_mapping = False
    try:
        _, planning_frame = worker.capture(None,cancel,deadline,geometry_only=True)
        if planning_frame is None: raise RuntimeError('pointer_guard: anchor planning frame unavailable')
        planning_image=decode(base64.b64decode(planning_frame['jpeg_base64']))
        # Pick two quiet, separated absolute checkpoints. A fixed offset probe
        # can overlap a dock icon and erase the very footprint we need to verify.
        probe=choose_anchor(planning_image,target,target)
        evidence['probe']=probe
        previous_anchor=None
        baseline=None
        evidence['anchor_attempts']=[]
        for attempt in range(2):
            anchor=choose_anchor(planning_image,target,probe,exclude=previous_anchor)
            evidence['anchor']=anchor
            evidence['anchor_attempts'].append({'target':anchor,'state':'checking'})
            hid=map_target(*anchor,profile)
            worker.send(cancel,deadline,'abs.move',x=hid[0],y=hid[1])
            end=min(deadline,time.perf_counter()+2.5)
            anchor_stable=Stability();last_image=None
            while time.perf_counter()<end:
                value=snapshot('anchor')
                if value is not None:
                    im,row=value;last_image=im
                    observation=anchor_response(planning_image,im,anchor)
                    row['observation']=observation
                    if anchor_stable.add(row['frame_id'],time.perf_counter(),observation):
                        baseline=im
                        break
                worker.pause(.04,cancel,deadline)
            if baseline is not None:
                evidence['anchor_attempts'][-1]['state']='passed'
                break
            evidence['anchor_attempts'][-1]['state']='unobserved'
            # A newly subscribed iOS connection can ignore its first report.
            # One different absolute checkpoint is safe; never replay buttons.
            if last_image is None:break
            planning_image=last_image
            previous_anchor=anchor
        if baseline is None: raise RuntimeError('pointer_guard: stable anchor response unavailable; click blocked')
        # Three distinct locations disambiguate removal and appearance footprints.
        # A delayed target->probe frame cannot satisfy both ordered transitions.
        for phase, old, new in [('probe',anchor,probe),('target',probe,target)]:
            preserve_mapping = False
            hid=map_target(*new,profile)
            worker.send(cancel,deadline,'abs.move',x=hid[0],y=hid[1])
            stable=Stability();position_stable=Stability();end=min(deadline,time.perf_counter()+2.5)
            while time.perf_counter()<end:
                value=snapshot(phase)
                if value is not None:
                    im,row=value;observation=response(baseline,im,old,new)
                    if review_local_scene and observation.get('localized_scene_changes'):
                        # Only movement-only scope sampling can defer this to a
                        # mandatory human scene review; click guard stays strict.
                        observation['valid']=True
                        observation['reason']='localized_scene_change_requires_review'
                        evidence['scene_changes'].append({'frame_id':row['frame_id'],'path':row['path'],'regions':observation['localized_scene_changes']})
                    row['observation']=observation
                    localized = observation.get('reason') == 'scene_changed' and bool(observation.get('localized_scene_changes'))
                    position_ok = observation['valid'] or localized
                    position_confirmed = position_stable.add(row['frame_id'],time.perf_counter(),{**observation,'valid':position_ok})
                    preserve_mapping = localized and position_confirmed
                    if stable.add(row['frame_id'],time.perf_counter(),observation):
                        baseline=im
                        break
                worker.pause(.04,cancel,deadline)
            else:
                raise RuntimeError('pointer_guard: direction/position response or stability unverified; click blocked')
        evidence.update(state='passed',center=observation['center'],stable_frames=len(stable.rows))
        return {'state':'passed','evidence_directory':str(folder),'center':observation['center'], 'version':9, 'scene_changes':evidence['scene_changes'],
                'reports_attempted':len(evidence['anchor_attempts'])+2,
                'anchor_recovered':len(evidence['anchor_attempts'])>1,
                'frame':{k:v for k,v in row.items() if k!='observation'}}
    except Exception as exc:
        preserve_mapping = preserve_mapping and str(exc) == 'pointer_guard: direction/position response or stability unverified; click blocked'
        evidence.update(state='failed',error=str(exc))
        evidence['mapping_preserved'] = preserve_mapping
        if not preserve_mapping:
            worker.invalidate_geometry()
        raise RuntimeError(f'{exc}; evidence={folder}') from exc
    finally:
        (folder/'result.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
