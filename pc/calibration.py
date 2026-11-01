"""Calibration primitives: correlated one-shot event chains and bounded affine fitting."""
from __future__ import annotations
import copy
import json
import math
import os
from pathlib import Path
import secrets
import threading
import time
import uuid
import numpy as np


class EventGate:
    """Only an acknowledged probe in the current page session can yield one chain."""
    def __init__(self):
        self.token = secrets.token_urlsafe(32)
        self.page_session = None
        self.armed = None
        self.acknowledged = False
        self.events = []
        self.invalid = None
        self.last_poll = 0.0
        self.lock = threading.Lock()

    def ready(self, token, session, width, height):
        with self.lock:
            if token != self.token or not isinstance(session, str) or not session or not isinstance(width, int) or not isinstance(height, int) or width < 100 or height < 100:
                raise ValueError('Invalid page ready')
            if self.page_session is not None and self.page_session != session:
                self.invalid = 'page_session_changed'
                raise ValueError(self.invalid)
            if self.page_session is not None and self.viewport != (width, height):
                self.invalid = 'viewport_changed'
                raise ValueError(self.invalid)
            self.page_session, self.viewport = session, (width, height)

    def arm(self):
        with self.lock:
            if self.invalid or not self.page_session or self.armed:
                raise ValueError('Cannot arm probe')
            self.armed = uuid.uuid4().hex
            self.acknowledged = False
            self.events = []
            return self.armed

    def ack(self, token, session, probe):
        with self.lock:
            if token != self.token or session != self.page_session or probe != self.armed or self.acknowledged or self.invalid:
                raise ValueError('Invalid arm acknowledgement')
            self.acknowledged = True

    def event(self, token, session, probe, kind, x, y, width, height, client_time=None):
        with self.lock:
            if token != self.token or session != self.page_session or probe != self.armed or not self.acknowledged or self.invalid:
                if token == self.token and session == self.page_session:
                    self.invalid = 'unexpected_or_uncorrelated_touch'
                raise ValueError('Uncorrelated event')
            if (width, height) != self.viewport or kind != ('pointerdown','pointerup','click')[min(len(self.events),2)] or len(self.events) >= 3:
                self.invalid = 'event_order_or_geometry_changed'
                raise ValueError(self.invalid)
            if any(type(v) not in (int,float) or not math.isfinite(v) for v in (x,y)) or not (0 <= x < width and 0 <= y < height):
                self.invalid = 'invalid_event_coordinates'
                raise ValueError(self.invalid)
            row = {'kind':kind,'x':x,'y':y,'width':width,'height':height,'client_time':client_time,'received_at':time.time()}
            if self.events and (abs(x-self.events[0]['x']) > 2 or abs(y-self.events[0]['y']) > 2):
                self.invalid = 'event_coordinates_changed'
                raise ValueError(self.invalid)
            self.events.append(row)

    def consume(self, probe):
        with self.lock:
            if self.invalid or probe != self.armed or len(self.events) != 3:
                raise ValueError('Incomplete or invalid event chain')
            events = copy.deepcopy(self.events)
            self.armed = None
            self.acknowledged = False
            self.events = []
            return events


def fit_samples(samples):
    if len(samples) != 6: raise ValueError('Six training samples required')
    for sample in samples:
        if not isinstance(sample, dict): raise ValueError('Invalid sample')
        for key in ('command','actual'):
            values=sample.get(key)
            if not isinstance(values,(list,tuple)) or len(values)!=2 or any(type(v) not in (int,float) or not math.isfinite(v) for v in values):
                raise ValueError('Invalid sample coordinates')
    commands = np.array([s['command'] for s in samples],dtype=float)
    actual = np.array([s['actual'] for s in samples],dtype=float)
    if commands.shape != (6,2) or actual.shape != (6,2) or not np.isfinite(commands).all() or not np.isfinite(actual).all():
        raise ValueError('Invalid samples')
    if np.max(np.abs(commands[2]-commands[5])) != 0 or np.max(np.abs(actual[2]-actual[5])) > 8:
        raise ValueError('Repeatability check failed')
    axes=[]
    for i in (0,1):
        if len(set(commands[:,i])) < 2: raise ValueError('Insufficient calibration range')
        slope,offset=np.linalg.lstsq(np.c_[commands[:,i],np.ones(6)],actual[:,i],rcond=None)[0]
        residual=float(np.max(np.abs(actual[:,i]-(commands[:,i]*slope+offset))))
        if not .8 < slope < 1.2 or abs(offset)>50 or residual>8: raise ValueError('Fit unstable or implausible')
        axes.append({'slope':float(slope),'offset':float(offset),'max_residual_px':residual})
    if np.max(np.abs(actual-commands)) <= 8:
        return 'identity',[{'slope':1.0,'offset':0.0},{'slope':1.0,'offset':0.0}],axes
    return 'inverse_affine',axes,axes


def validate_holdout(samples):
    if not isinstance(samples, list) or len(samples) != 5:
        raise ValueError('Independent holdout validation failed')
    for sample in samples:
        if not isinstance(sample, dict): raise ValueError('Invalid holdout sample')
        target, actual = sample.get('target'), sample.get('actual')
        if not isinstance(target, (list, tuple)) or not isinstance(actual, (list, tuple)) or len(target) != 2 or len(actual) != 2:
            raise ValueError('Invalid holdout coordinates')
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in (*target, *actual)):
            raise ValueError('Invalid holdout coordinates')
        if max(abs(target[i]-actual[i]) for i in (0,1)) > 8:
            raise ValueError('Independent holdout validation failed')


def atomic_save(path, profile):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temp.open('x',encoding='utf-8') as f:
            json.dump(profile,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
        os.replace(temp,path)
    finally:
        if temp.exists():temp.unlink()


def load_unverified(path):
    p=Path(path)
    if not p.exists():return None
    profile=json.loads(p.read_text(encoding='utf-8'))
    profile['verification']='needs_verification'
    return profile
