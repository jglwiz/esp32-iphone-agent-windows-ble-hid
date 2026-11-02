"""Read-only frame routes never acquire the serial queue."""
import asyncio
import time
import httpx
from fastapi import Response, HTTPException
from .frames import FrameCapture, InvalidFrame

def install(app, source=None):
    source = source or FrameCapture()

    async def read():
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=.7) as client:
                frame = await asyncio.wait_for(source.reader(client), .8)
            if frame is not None and frame.timestamp > time.time() + 1:
                raise InvalidFrame('Frame timestamp is in the future')
            return frame
        except InvalidFrame as exc:
            raise HTTPException(502, str(exc)) from exc
        except (httpx.HTTPError, TimeoutError) as exc:
            raise HTTPException(503, 'Frame source unavailable') from exc

    @app.get('/frame')
    async def frame():
        value = await read()
        headers = {'Cache-Control': 'no-store', 'X-Frame-Snapshot-Version': '1'}
        if value is None:
            return Response(status_code=204, headers=headers)
        headers.update({'X-Frame-Time': str(value.timestamp), 'X-Frame-Id': value.frame_id})
        return Response(value.jpeg, media_type='image/jpeg', headers=headers)

    @app.get('/screen/status')
    async def status():
        result = dict(reachable=True, frame_available=False, frame_time=None,
                      age_seconds=None, freshness='unavailable', observed_at=time.time())
        try:
            value = await read()
            if value is not None:
                age = time.time() - value.timestamp
                result.update(frame_available=True, frame_time=value.timestamp,
                              age_seconds=age, freshness='fresh' if age <= 5 else 'stale_or_unknown')
        except HTTPException as exc:
            result.update(reachable=exc.status_code != 503,
                          freshness='invalid' if exc.status_code == 502 else 'unavailable',
                          error=exc.detail)
        source.last_observation = dict(result)
        return result
    return status
