"""Auditable fixed-template scope expansion, executed solely by the serial worker."""
import base64, copy, hashlib, json, math, random, time, uuid
import cv2
import numpy as np
from calibration import atomic_save
from native_pointer import map_target, validate_profile

REGION=[24,60,471,1010]
def template():
    randomizer=random.Random(20261031)
    grid=[(x,y) for y in (60,540,1010) for x in (24,248,471)]
    targets=[]
    for _ in range(3):
        row=grid.copy();randomizer.shuffle(row)
        while targets and row[0]==targets[-1]:randomizer.shuffle(row)
        targets.extend(row)
    targets.extend(((24,540),(471,540),(248,60),(248,1010)))
    return targets

class ScopeJob:
    def __init__(self,owner,request_id):
        self.owner=owner;self.request_id=request_id;self.job_id=uuid.uuid4().hex
        self.profile=copy.deepcopy(owner.profile);self.generation=owner.profile_generation
        self.cancel=__import__('threading').Event();self.deadline=time.perf_counter()+180
        self.folder=owner.runtime/'scope'/self.job_id;self.folder.mkdir(parents=True)
        self.result={'job_id':self.job_id,'request_id':request_id,'instance_id':owner.instance_id,'state':'queued','samples':[],'created_at':time.time(),'evidence_directory':str(self.folder)}
        self.save()
    def save(self):
        (self.folder/'result.json').write_text(json.dumps(self.result,indent=2),encoding='utf-8')
    def fail(self,exc):
        self.result.update(state='cancelled' if self.cancel.is_set() else 'failed',error=str(exc),finished_at=time.time())
        try:self.save()
        except OSError as err:self.owner.set_fault('Scope evidence persistence failed: '+str(err))
    def capture(self,after=None):
        state,frame=self.owner.capture(after,self.cancel,self.deadline)
        if frame is None or frame.get('snapshot_version')!=1 or frame.get('atomic_snapshot') is not True:raise ValueError('Fresh v1 frame unavailable')
        self.owner.accept_geometry(frame,self.profile)
        raw=base64.b64decode(frame.pop('jpeg_base64'));image=cv2.imdecode(np.frombuffer(raw,np.uint8),cv2.IMREAD_COLOR)
        if image is None or image.shape[:2]!=(1080,496):raise ValueError('Screenshot geometry changed')
        return frame,raw
    def status(self):
        s=self.owner.send(self.cancel,self.deadline,'status');self.owner.record_status(s)
        if self.owner.profile_generation!=self.generation or not self.owner.profile or self.owner.profile.get('profile_id')!=self.profile['profile_id']:
            raise ValueError('Profile or connection generation changed')
        validate_profile(self.profile,device_id=self.owner.device_id,phone_id=self.owner.phone_id,descriptor_version=s.get('absolute_descriptor_version'),geometry=(496,1080,'portrait'))
        if s.get('device_id')!=self.owner.device_id or s.get('supports_absolute_pointer') is not True or s.get('absolute_ready') is not True:raise ValueError('Native HID unavailable')
        binding=(s.get('boot_id'),s.get('connection_generation'))
        if not binding[0] or binding[1] is None:raise ValueError('Connection binding missing')
        if hasattr(self,'binding') and binding!=self.binding:raise ValueError('Connection changed')
        self.binding=binding;return s
    def run(self):
        w=self.owner
        try:
            if w.fault or not w.bridge:raise ValueError('Serial worker unavailable')
            self.result['state']='sampling';self.save();self.status();self.capture()
            for x,y in template():map_target(x,y,self.profile)
            previous_frame=None
            for i,(x,y) in enumerate(template(),1):
                w.check(self.cancel,self.deadline);state=self.status()
                hid=map_target(x,y,self.profile);started=time.time()
                w.send(self.cancel,self.deadline,'abs.move',x=hid[0],y=hid[1])
                w.pause(.7,self.cancel,self.deadline);settled=time.time();frame,raw=self.capture(settled)
                if frame.get('after_action') is not True or frame.get('frame_id')==previous_frame:raise ValueError('Frame is not a new post-move snapshot')
                previous_frame=frame.get('frame_id')
                name='sample-%02d'%i;path=self.folder/(name+'.jpg');path.write_bytes(raw)
                self.result['samples'].append({'sample_id':name,'target':[x,y],'hid':hid,'started_at':started,'frame':frame,'sha256':hashlib.sha256(raw).hexdigest(),'image':str(path),'profile_id':self.profile['profile_id'],'device_id':state['device_id'],'boot_id':state['boot_id'],'connection_generation':state['connection_generation'],'geometry':[496,1080,'portrait']})
                self.save()
            self.status();w.send(self.cancel,self.deadline,'release_all')
            self.review_deadline=time.monotonic()+900
            self.result.update(review_expires_at=time.time()+900,state='awaiting_review',binding={'device_id':w.device_id,'phone_id':w.phone_id,'boot_id':self.binding[0],'connection_generation':self.binding[1],'profile_id':self.profile['profile_id'],'profile_generation':self.generation,'geometry':[496,1080,'portrait']},sampled_at=time.time());self.save()
        except Exception as exc:
            if isinstance(exc,OSError):w.set_fault('Scope evidence persistence failed: '+str(exc))
            self.fail(exc)
            try:w.bridge.request('release_all',timeout=1)
            except Exception as err:w.set_fault('Scope release failed: '+str(err))
        finally:
            with w.lock:
                if w.calibration_reserved==self.job_id:w.calibration_reserved=None

    def approve(self,review):
        w=self.owner
        try:
            if self.result['state']!='awaiting_review':raise ValueError('Scope job is not awaiting review')
            if not hasattr(self,'review_deadline') or time.monotonic()>self.review_deadline:raise ValueError('Scope review window expired')
            self.status();self.capture()
            samples=self.result['samples'];observations=review['observations']
            if len(samples)!=len(template()) or set(observations)!={s['sample_id'] for s in samples}:raise ValueError('Every fixed sample needs one review')
            checks=[]
            for sample in samples:
                path=self.folder/(sample['sample_id']+'.jpg');raw=path.read_bytes()
                if hashlib.sha256(raw).hexdigest()!=sample['sha256']:raise ValueError('Evidence image hash changed')
                obs=observations[sample['sample_id']]
                if isinstance(obs,dict) and obs=={'uncertain':True}:raise ValueError('Uncertain sample cannot expand scope')
                if not isinstance(obs,dict) or set(obs)!={'center','uncertainty_px'}:raise ValueError('Invalid review observation')
                center=obs['center'];uncertainty=obs['uncertainty_px']
                if not isinstance(center,list) or len(center)!=2 or any(type(v) not in (int,float) or not math.isfinite(v) for v in center) or type(uncertainty) not in (int,float) or not math.isfinite(uncertainty) or not 0<uncertainty<=8:raise ValueError('Uncertain or invalid observation')
                if not (0<=center[0]<496 and 0<=center[1]<1080):raise ValueError('Observation outside screenshot')
                error=math.hypot(center[0]-sample['target'][0],center[1]-sample['target'][1])
                if error+uncertainty>8:raise ValueError('Review tolerance exceeded')
                checks.append({'sample_id':sample['sample_id'],'center':center,'uncertainty_px':uncertainty,'error_px':error})
            self.status();self.capture();w.check(self.cancel,self.deadline)
            profile=copy.deepcopy(self.profile);profile['profile_id']=uuid.uuid4().hex;profile['verified_region']=REGION.copy();profile['scope_evidence']=str(self.folder);profile['scope_verified_at']=time.time()
            self.result.update(state='review_passed',reviewer=review['reviewer'],review=checks,candidate_profile=profile);self.save()
            self.status();w.check(self.cancel,self.deadline)
            w.send(self.cancel,self.deadline,'release_all');w.check(self.cancel,self.deadline)
            atomic_save(w.runtime/'calibration-profile.json',profile)
            with w.lock:w.profile=profile;w.profile_generation+=1
            self.result.update(state='passed',profile=profile,finished_at=time.time())
        except Exception as exc:self.fail(exc)
        finally:
            with w.lock:
                if w.calibration_reserved==self.job_id:w.calibration_reserved=None


class ScopeApproval:
    def __init__(self,job,review):
        self.job=job;self.review=review;self.request_id='scope-approve-'+job.job_id
    def run(self):self.job.approve(self.review)
