import secrets
import httpx
from fastapi import Request, HTTPException
from pydantic import BaseModel, Field
from .manager_client import call

class StartRequest(BaseModel):
    request_id: str = Field(min_length=1,max_length=80)

def install(app, token):
    def auth(request):
        if not request.client or request.client.host not in ('127.0.0.1','::1'):
            raise HTTPException(403,'Loopback client required')
        if not token or not secrets.compare_digest(request.headers.get('authorization',''),'Bearer '+token):
            raise HTTPException(401,'Invalid management token')
    async def invoke(method,path,body=None):
        try: return await call(method,path,body)
        except httpx.HTTPStatusError as exc:
            raise HTTPException(exc.response.status_code, 'Manager rejected request') from exc
        except httpx.HTTPError as exc:
            raise HTTPException(503,'Backend manager unavailable') from exc
    @app.post('/screen/start',status_code=202)
    async def start(body: StartRequest, request: Request):
        auth(request)
        return await invoke('POST','/screen/start',body.model_dump())
    @app.get('/screen/start/{request_id}')
    async def status(request_id: str, request: Request):
        auth(request)
        from urllib.parse import quote
        return await invoke('GET','/screen/start/'+quote(request_id,safe=''))
