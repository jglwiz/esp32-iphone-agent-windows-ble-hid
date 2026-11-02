"""Composition root for the migrated API."""
import asyncio
from contextlib import asynccontextmanager
from .service import create_app as control_app
from .airplay.routes import install
from .system_routes import install as install_system
from .service import SerialWorker
from .device_reset import install as install_reset
from .management_routes import install as install_management

def create_app(worker=None, **kwargs):
    worker = worker or SerialWorker()
    app = control_app(worker, **kwargs)
    screen_status = install(app, worker.frames)
    install_system(app, worker, kwargs.get('restart_token', ''))
    install_reset(app, worker, kwargs.get('restart_token', ''))
    install_management(app, kwargs.get('restart_token', ''))
    app.openapi_tags = [
        {'name':'esp32','description':'Device input, calibration and reset'},
        {'name':'airplay','description':'Frame snapshots and capture lifecycle'},
        {'name':'system','description':'Backend health, logs and API lifecycle'},
    ]
    for route in app.routes:
        if hasattr(route, 'tags'):
            path = route.path
            route.tags = ['airplay' if path == '/frame' or path.startswith('/screen')
                          else 'system' if path == '/health' or path.startswith(('/admin','/system'))
                          else 'esp32']
    original_lifespan = app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(app):
        async def monitor():
            while True:
                await screen_status()
                probe=kwargs.get('manager_probe')
                if probe:
                    try:
                        worker.manager_status=dict(await probe(), reachable=True)
                    except Exception:
                        worker.manager_status={'reachable':False}
                await asyncio.sleep(2)
        async with original_lifespan(app):
            task=asyncio.create_task(monitor())
            try: yield
            finally:
                task.cancel()
                await asyncio.gather(task,return_exceptions=True)
    app.router.lifespan_context=lifespan
    return app
