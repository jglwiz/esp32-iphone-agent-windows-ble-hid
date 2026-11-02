"""Human-reviewed, movement-only calibration using server-owned action evidence.

A verified visual coordinate profile does not assert successful button delivery.
Review executes on the sole serial worker; no client paths or mapping coefficients
are trusted. Training and independent holdouts come from completed diagnostic moves.
"""
import base64
import copy
import hashlib
import json
import math
import threading
import time
import uuid
from pathlib import Path
import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from iphone_backend.calibration import fit_samples, atomic_save
from iphone_backend.native_pointer import validate_profile, map_target


class Observation(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    action_id: str = Field(min_length=1, max_length=80)
    center: list[float] = Field(min_length=2, max_length=2)
    uncertainty_px: float = Field(gt=0, le=8)
    image_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    target: list[int] | None = Field(default=None, min_length=2, max_length=2)


class VisualReviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(min_length=1, max_length=80)
    reviewer: str = Field(min_length=1, max_length=100)
    scene: str = Field(pattern=r'^(home|notifications|control_center)$')
    training: list[Observation] = Field(min_length=6, max_length=9)
    holdout: list[Observation] = Field(min_length=5, max_length=5)


def evaluate(training, holdout):
    """Fit positive axes, then constrain scope to a tested rectangle and its center."""
    if len(training) not in (6,9) or len(holdout)!=5: raise ValueError('Six legacy or nine grid training and five holdout points required')
    if len(training)==9:
        points={tuple(s['command']) for s in training}
        xs=sorted({p[0] for p in points});ys=sorted({p[1] for p in points})
        if len(xs)!=3 or len(ys)!=3 or points!={(x,y) for x in xs for y in ys} or xs[0]>30 or xs[-1]<465 or ys[0]>70 or ys[-1]<1000:
            raise ValueError('Nine-point grid must cover the full usable screen')
    commands=np.asarray([s['command'] for s in training],dtype=float)
    if np.ptp(commands[:,0])<100 or np.ptp(commands[:,1])<200: raise ValueError('Training coverage too small')
    for s in training+holdout:
        c=s['actual'];u=s['uncertainty_px']
        if len(c)!=2 or any(not math.isfinite(v) for v in c) or not (0<=c[0]<496 and 0<=c[1]<1080) or not math.isfinite(u) or not 0<u<=8:
            raise ValueError('Invalid visual observation')
    mapping, axes, diagnostics=fit_samples(training)
    for s in training:
        error=max(abs(s['actual'][i]-(s['command'][i]*axes[i]['slope']+axes[i]['offset'])) for i in (0,1))
        if error+s['uncertainty_px']>8: raise ValueError('Training uncertainty exceeds tolerance')
    if len(training)==6 and max(abs(training[2]['actual'][i]-training[5]['actual'][i]) for i in (0,1))+max(training[2]['uncertainty_px'],training[5]['uncertainty_px'])>8:
        raise ValueError('Repeated point unstable')
    targets=[tuple(s['target']) for s in holdout]
    if len(set(targets))!=5: raise ValueError('Holdouts must be unique')
    xs=[p[0] for p in targets];ys=[p[1] for p in targets]
    x0,x1=min(xs),max(xs);y0,y1=min(ys),max(ys)
    if x1-x0<80 or y1-y0<150 or set(targets)!={(x0,y0),(x0,y1),(x1,y0),(x1,y1),((x0+x1)/2,(y0+y1)/2)}:
        raise ValueError('Holdouts must cover four rectangle corners and center')
    actual=np.asarray([s['actual'] for s in training],dtype=float)
    if x0<actual[:,0].min() or x1>actual[:,0].max() or y0<actual[:,1].min() or y1>actual[:,1].max():
        raise ValueError('Holdout region exceeds training coverage')
    checks=[]
    for s in holdout:
        if any(max(abs(s['command'][i]-t['command'][i]) for i in (0,1))<2 for t in training):
            raise ValueError('Holdout reuses training command')
        expected=[(s['target'][i]-axes[i]['offset'])/axes[i]['slope'] for i in (0,1)]
        if max(abs(expected[i]-s['command'][i]) for i in (0,1))>1: raise ValueError('Holdout did not use inverse mapping')
        error=math.hypot(*(s['actual'][i]-s['target'][i] for i in (0,1)))
        if error+s['uncertainty_px']>8: raise ValueError('Holdout tolerance including uncertainty exceeded')
        checks.append({'target':s['target'],'error_px':error,'uncertainty_px':s['uncertainty_px']})
    return mapping,axes,[x0,y0,x1,y1],checks


class VisualReviewJob:
    def __init__(self,owner,body,encoded):
        self.owner=owner;self.body=body;self.encoded=encoded
        self.job_id=uuid.uuid4().hex;self.request_id=body.request_id
        self.completed=False
        self.generation=owner.profile_generation
        self.cancel=threading.Event();self.deadline=time.perf_counter()+20
        self.folder=owner.runtime/'visual-calibrations'/self.job_id
        self.result={'job_id':self.job_id,'request_id':self.request_id,'state':'queued','evidence_type':'visual_pointer','click_delivery_verified':False}

    def fail(self,error):
        self.result.update(state='failed',error=str(error))

    def status(self):
        w=self.owner
        if w.fault or not w.bridge: raise ValueError('Controller unavailable')
        state=w.send(self.cancel,self.deadline,'status');w.record_status(state)
        if w.profile_generation!=self.generation: raise ValueError('Connection or profile changed')
        if not all(state.get(k) is True for k in ('connected','encrypted','absolute_ready')) or state.get('device_id')!=w.device_id:
            raise ValueError('Native pointer not ready')
        return {k:state[k] for k in ('device_id','boot_id','connection_generation','absolute_descriptor_version')}

    def evidence(self,obs,binding,seen):
        w=self.owner
        if obs.action_id in seen: raise ValueError('Action reused')
        seen.add(obs.action_id)
        entry=w.entries.get(obs.action_id)
        if entry is None or not entry[1].done(): raise ValueError('Unknown or incomplete action')
        action=json.loads(entry[0]);result=copy.deepcopy(entry[1].result())
        if action.get('coordinate_backend')!='native_hid' or not action.get('diagnostic_uncalibrated') or len(action['steps'])!=1 or action['steps'][0]['op']!='move_to':
            raise ValueError('Only single diagnostic native movement is evidence')
        if (result.get('instance_id')!=w.instance_id or result.get('profile_generation')!=self.generation or result.get('connection_binding')!=binding or result.get('input_state')!='reports_sent' or result.get('released') is not True or result.get('error') or result.get('capture_error')):
            raise ValueError('Action evidence invalidated')
        checks=result.get('absolute') or []
        if len(checks)!=1 or checks[0].get('pointer_check',{}).get('state')!='passed':
            raise ValueError('Continuous pointer response evidence required')
        frame=result.get('frame') or {}
        if (not frame.get('pointer_response_verified') and (frame.get('after_action') is not True or not frame.get('frame_time',0)>result.get('settled_at',float('inf')))) or not 0<=time.time()-frame['frame_time']<=900:
            raise ValueError('Frame stale or not after settlement')
        raw=Path(frame['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=obs.image_sha256: raise ValueError('Image hash mismatch')
        image=cv2.imdecode(np.frombuffer(raw,np.uint8),cv2.IMREAD_COLOR)
        if image is None or image.shape[:2]!=(1080,496): raise ValueError('Invalid image geometry')
        return {'command':[action['steps'][0]['x'],action['steps'][0]['y']], 'actual':obs.center,'uncertainty_px':obs.uncertainty_px,'target':obs.target,'frame':frame,'action_id':obs.action_id,'sha256':obs.image_sha256}

    def run(self):
        w=self.owner
        try:
            binding=self.status();seen=set()
            training=[self.evidence(o,binding,seen) for o in self.body.training]
            holdout=[self.evidence(o,binding,seen) for o in self.body.holdout]
            if any(s['target'] is not None for s in training) or any(s['target'] is None for s in holdout): raise ValueError('Invalid training/holdout roles')
            all_samples=training+holdout
            ids=[s['frame']['frame_id'] for s in all_samples]
            sources={fid.rsplit(':',1)[0] for fid in ids}
            if len(set(ids))!=len(all_samples) or len(sources)!=1: raise ValueError('Duplicate frames or capture source changed')
            mapping,axes,region,checks=evaluate(training,holdout)
            self.status()
            capture,frame=w.capture(None,self.cancel,self.deadline,geometry_only=True)
            if frame is None or not 0<=frame['age_seconds']<=5 or frame['frame_id'].rsplit(':',1)[0] not in sources: raise ValueError('Fresh current source required')
            last=max(all_samples,key=lambda s:s['frame']['frame_time'])['frame']
            baseline={'frame_source_id':next(iter(sources)),'frame_sequence':int(last['frame_id'].rsplit(':',1)[1]),'frame_timestamp':last['frame_time'],'frame_sha256':next(s['sha256'] for s in all_samples if s['frame']['frame_id']==last['frame_id'])}
            w.accept_geometry(frame,baseline)
            profile={'schema_version':1,'profile_id':uuid.uuid4().hex,'device_id':w.device_id,'phone_id':w.phone_id,'descriptor_version':binding['absolute_descriptor_version'],'geometry':[496,1080,'portrait'],'coordinate_space':'full_screenshot_pixels','verification':'verified','verified_region':region,'axes':axes,'mapping':mapping,'evidence_type':'visual_pointer','click_delivery_verified':False,'scene':self.body.scene,'verified_at':time.time(),'evidence_directory':str(self.folder),**baseline}
            validate_profile(profile,device_id=w.device_id,phone_id=w.phone_id,descriptor_version=binding['absolute_descriptor_version'],geometry=(496,1080,'portrait'))
            for x,y in [region[:2],region[2:]]: map_target(x,y,profile,click=True)
            self.folder.mkdir(parents=True)
            atomic_save(self.folder/'review.json',{'request':self.body.model_dump(),'training':training,'holdout':holdout,'checks':checks,'candidate':profile})
            if self.status()!=binding: raise ValueError('Connection changed before publication')
            w.check(self.cancel,self.deadline)
            atomic_save(w.runtime/'calibration-profile.json',profile)
            with w.lock: w.profile=profile;w.profile_generation+=1
            self.result.update(state='passed',profile=profile,checks=checks)
        except Exception as exc: self.fail(exc)
        finally:
            with w.lock:
                if w.calibration_reserved==self.job_id:w.calibration_reserved=None
                if w.active_id==self.request_id:w.active_id=None
            try:
                self.folder.mkdir(parents=True,exist_ok=True)
                atomic_save(self.folder/'result.json',self.result)
            except OSError as exc: w.set_fault('Visual review evidence persistence failed: '+str(exc));self.fail(exc)
            self.completed=True
