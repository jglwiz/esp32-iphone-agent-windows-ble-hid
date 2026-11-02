"""Calibration jobs share the serial worker; receiver never touches the device."""
from __future__ import annotations
import asyncio
import base64, hashlib, json, os, queue, threading, time, uuid
from pathlib import Path
import cv2
import numpy as np
from iphone_backend.calibration import EventGate, fit_samples, validate_holdout, atomic_save
from iphone_backend.calibration_receiver import Receiver
from iphone_backend.native_pointer import map_target, validate_profile

class CalibrationJob:
 def __init__(self, request_id, mode, owner, timeout=180):
  self.request_id=request_id;self.job_id=uuid.uuid4().hex;self.mode=mode;self.owner=owner
  self.gate=EventGate();self.cancel=threading.Event();self.deadline=time.perf_counter()+timeout
  self.folder=owner.runtime/'calibrations'/self.job_id;self.folder.mkdir(parents=True)
  self.result={'job_id':self.job_id,'request_id':request_id,'mode':mode,'state':'waiting_for_page','samples':[],'created_at':time.time(),'evidence_directory':str(self.folder)}
  self.receiver=Receiver(self.gate,port=int(os.environ.get('BLE_HID_CALIBRATION_PORT','8893')))
  self.receiver.loop=getattr(owner,"event_loop",None)
  self.receiver.start()
  host=os.environ.get('BLE_HID_CALIBRATION_HOST','')
  if not host:
   self.receiver.close();raise ValueError('BLE_HID_CALIBRATION_HOST is required')
  self.result['page_url']=f'http://{host}:{self.receiver.server.server_port}/?token={self.gate.token}'
  try:self.save()
  except Exception:
   self.receiver.close()
   raise
 def save(self):
  (self.folder/'result.json').write_text(json.dumps(self.result,ensure_ascii=False,indent=2),encoding='utf-8')
 def finish(self,state,error=None):
  self.result['state']=state
  if error:self.result['error']=str(error)
  self.result['finished_at']=time.time()
  try:self.save()
  except OSError as exc:
   self.owner.set_fault('Calibration evidence persistence failed: '+str(exc))
   self.result['state']='failed';self.result['error']=self.owner.fault
 def wait_page(self):
  try:
   while not self.gate.page_session:
    self.owner.check(self.cancel,self.deadline)
    if self.gate.invalid:raise ValueError(self.gate.invalid)
    time.sleep(.1)
   with self.owner.lock:
    self.owner.calibration_reserved=self.job_id
    self.owner.jobs.put_nowait((self,None,self.cancel,self.deadline))
  except Exception as exc:
   with self.owner.lock:
    if self.owner.calibration_reserved==self.job_id:self.owner.calibration_reserved=None
   self.finish('cancelled' if self.cancel.is_set() else 'failed',exc)
   self.receiver.close()
 async def wait_page_async(self):
  try:
   while not self.gate.page_session:
    self.owner.check(self.cancel,self.deadline)
    if self.gate.invalid:raise ValueError(self.gate.invalid)
    await asyncio.sleep(.1)
   with self.owner.lock:
    self.owner.calibration_reserved=self.job_id
    self.owner.jobs.put_nowait((self,None,self.cancel,self.deadline))
  except Exception as exc:
   with self.owner.lock:
    if self.owner.calibration_reserved==self.job_id:self.owner.calibration_reserved=None
   self.finish('cancelled' if self.cancel.is_set() else 'failed',exc)
   await asyncio.to_thread(self.receiver.close)
 def start(self):
  loop=getattr(self.owner,'event_loop',None)
  if loop:
   self.wait_future=asyncio.run_coroutine_threadsafe(self.wait_page_async(),loop)
  else:threading.Thread(target=self.wait_page,daemon=True,name='calibration-wait').start()
 def geometry(self,after=None):
  state,frame=self.owner.capture(after,self.cancel,self.deadline)
  if frame is None or frame.get('snapshot_version')!=1 or frame.get('atomic_snapshot') is not True:raise ValueError('Fresh v1 frame unavailable')
  try:
   source,seq_text=frame['frame_id'].rsplit(':',1)
   if not source or not seq_text.isdecimal():raise ValueError('Invalid frame ID')
   seq=int(seq_text);timestamp=frame['frame_time']
   if hasattr(self,'frame_source') and (source!=self.frame_source or seq<self.frame_sequence or timestamp<self.frame_timestamp):raise ValueError('Frame source changed or regressed')
   self.frame_source,self.frame_sequence,self.frame_timestamp=source,seq,timestamp
  except (KeyError,TypeError,ValueError) as exc:raise ValueError('Invalid calibration frame binding') from exc
  raw=base64.b64decode(frame.pop('jpeg_base64'));self.frame_sha256=hashlib.sha256(raw).hexdigest();img=cv2.imdecode(np.frombuffer(raw,np.uint8),cv2.IMREAD_COLOR)
  if img is None or img.shape[:2]!=(1080,496):raise ValueError('Geometry changed')
  b,g,r=cv2.split(img);count,_,stats,centers=cv2.connectedComponentsWithStats(((b>180)&(g>180)&(r<100)).astype('uint8'))
  marks=np.array([centers[i] for i in range(1,count) if 30<stats[i,4]<1000 and .5<stats[i,2]/stats[i,3]<2])
  if len(marks)!=4:raise ValueError('Calibration page markers unavailable')
  left,top=marks.min(axis=0);right,bottom=marks.max(axis=0)
  if right-left<250 or bottom-top<350 or any(min(abs(x-left),abs(x-right))>3 or min(abs(y-top),abs(y-bottom))>3 for x,y in marks):raise ValueError('Invalid page markers')
  image_path=self.folder/(uuid.uuid4().hex+'.jpg');image_path.write_bytes(raw)
  return [float(left),float(top),float(right),float(bottom)],frame,str(image_path)
 def run(self):
  w=self.owner
  released=False
  try:
   if w.fault:raise ValueError('Serial worker fault: '+str(w.fault))
   self.result['state']='sampling';self.save()
   if not w.device_id or not w.phone_id:raise ValueError('Explicit device and phone identities required')
   status=w.send(self.cancel,self.deadline,'status')
   w.record_status(status)
   if status.get('device_id')!=w.device_id or status.get('supports_absolute_pointer') is not True or status.get('absolute_descriptor_version')!=1 or status.get('absolute_ready') is not True:raise ValueError('Native device capability unavailable')
   connection=(status.get('boot_id'),status.get('connection_generation'))
   marks,frame,path=self.geometry();self.result.update(markers=marks,initial_frame=frame,initial_image=path)
   l,t,r,b=marks; vw,vh=self.gate.viewport
   if vw<=52 or vh<=82:raise ValueError('Invalid viewport')
   def target(x,y):return [round(l+(r-l)*x),round(t+(b-t)*y)]
   def sample(name,desired,command):
    w.check(self.cancel,self.deadline)
    if not (l+25<command[0]<r-25 and t+35<command[1]<b-25):raise ValueError('Command outside safe area')
    now=w.send(self.cancel,self.deadline,'status')
    w.record_status(now)
    if (now.get('boot_id'),now.get('connection_generation'))!=connection or not now.get('absolute_ready'):raise ValueError('Connection changed')
    before,_,_=self.geometry()
    if np.max(np.abs(np.array(before)-marks))>2 or self.gate.invalid:raise ValueError('Page changed before click')
    if time.monotonic()-self.gate.last_poll>2:raise ValueError('Page session is not active')
    probe=self.gate.arm()
    ack_until=time.monotonic()+5
    while not self.gate.acknowledged:
     if self.gate.invalid:raise ValueError(self.gate.invalid)
     if time.monotonic()>ack_until:raise ValueError('Page probe acknowledgement timeout')
     w.pause(.1,self.cancel,self.deadline)
    hid=map_target(*command);started=time.time();w.send(self.cancel,self.deadline,'abs.click',x=hid[0],y=hid[1])
    until=time.perf_counter()+3
    while len(self.gate.events)<3:
     if self.gate.invalid:raise ValueError(self.gate.invalid)
     if time.perf_counter()>until:raise ValueError('Incomplete click event chain; not replayed')
     w.pause(.1,self.cancel,self.deadline)
    # Quiescence catches repeated/extra events before a sample is consumed.
    w.pause(.25,self.cancel,self.deadline)
    events=self.gate.consume(probe);e=events[-1]
    if (e['width'],e['height'])!=(vw,vh):raise ValueError('Viewport changed')
    actual=[l+(e['x']-26)/(vw-52)*(r-l),t+(e['y']-46)/(vh-82)*(b-t)]
    observed,frame,path=self.geometry(events[-1]['received_at'])
    if self.gate.invalid:raise ValueError(self.gate.invalid)
    if np.max(np.abs(np.array(observed)-marks))>2:raise ValueError('Calibration geometry changed')
    row={'name':name,'target':desired,'command':command,'actual':actual,'events':events,'probe_id':probe,'frame':frame,'image':path}
    self.result['samples'].append(row);self.save();return row
   if self.mode=='verify':
    existing=w.profile
    if existing is None:raise ValueError('Existing profile required for verification')
    validate_profile(existing,device_id=w.device_id,phone_id=w.phone_id,descriptor_version=1,geometry=(496,1080,'portrait'),require_verified=False)
    axes=existing['axes'];mode=existing['mapping'];measured=None
   else:
    train=[]
    for i,xy in enumerate(((.2,.2),(.8,.2),(.5,.5),(.2,.8),(.8,.8),(.5,.5))):
     point=target(*xy);train.append(sample('train-%02d'%i,point,point))
    self.result['state']='fitting';self.save()
    mode,axes,measured=fit_samples(train)
   self.result.update(mapping=mode,axes=axes,fit=measured,state='validating');self.save()
   def correct(point):return [round((point[i]-axes[i]['offset'])/axes[i]['slope']) for i in (0,1)]
   validation=[]
   for i,xy in enumerate(((.35,.35),(.65,.65),(.65,.35),(.35,.65),(.5,.65))):
    point=target(*xy);validation.append(sample('holdout-%02d'%i,point,correct(point)))
   validate_holdout(validation)
   extra=[]
   for i,xy in enumerate(((.35,.35),(.65,.65),(.65,.35),(.35,.65),(.5,.5))):
    point=target(*xy);extra.append(sample('click-%02d'%i,point,correct(point)))
   validate_holdout(extra)
   x0,y0=target(.35,.35);x1,y1=target(.65,.65)
   if self.mode=='verify':
    old=w.profile['verified_region'];x0=max(x0,old[0]);y0=max(y0,old[1]);x1=min(x1,old[2]);y1=min(y1,old[3])
    if x0>=x1 or y0>=y1:raise ValueError('No verified region remains')
   profile={'schema_version':1,'profile_id':uuid.uuid4().hex,'device_id':w.device_id,'phone_id':w.phone_id,'descriptor_version':1,'geometry':[496,1080,'portrait'],'coordinate_space':'full_screenshot_pixels','frame_source_id':self.frame_source,'frame_sequence':self.frame_sequence,'frame_timestamp':self.frame_timestamp,'frame_sha256':self.frame_sha256,'verification':'verified','verified_region':[x0,y0,x1,y1],'axes':axes,'mapping':mode,'evidence_directory':str(self.folder),'verified_at':time.time(),'max_holdout_error':max(max(abs(x['target'][i]-x['actual'][i]) for i in (0,1)) for x in validation)}
   w.check(self.cancel,self.deadline)
   if self.gate.invalid:raise ValueError(self.gate.invalid)
   final=w.send(self.cancel,self.deadline,'status')
   w.record_status(final)
   if (final.get('boot_id'),final.get('connection_generation'))!=connection or not final.get('absolute_ready'):raise ValueError('Connection changed before publication')
   w.send(self.cancel,self.deadline,'release_all')
   released=True
   w.check(self.cancel,self.deadline)
   if self.gate.invalid:raise ValueError(self.gate.invalid)
   # Persist complete validation evidence before the sole atomic profile commit.
   # A crash before the profile replacement leaves only a 'validated' candidate.
   self.result.update(state='validation_passed',profile=profile,validated_at=time.time(),activation='not_committed')
   self.save()
   w.check(self.cancel,self.deadline)
   if self.gate.invalid:raise ValueError(self.gate.invalid)
   atomic_save(w.runtime/'calibration-profile.json',profile)
   with w.lock:
    w.profile=profile;w.profile_generation+=1;w.geometry_last=(self.frame_sequence,self.frame_timestamp,self.frame_sha256)
   self.result.update(state='passed',activation='committed',finished_at=time.time())
  except Exception as exc:self.finish('cancelled' if self.cancel.is_set() else 'failed',exc)
  finally:
   if not released:
    try:w.bridge.request('release_all',timeout=1)
    except Exception as exc:w.set_fault('Calibration release failed: '+str(exc))
   with w.lock:
    if w.calibration_reserved==self.job_id:w.calibration_reserved=None
   self.receiver.close()
