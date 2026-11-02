"""Unified package entry point; importing the package never starts hardware."""
import argparse
import os

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--role', choices=('manager', 'api', 'capture'), default='manager')
    args = parser.parse_args()
    if args.role == 'manager':
        from .manager import main as manage
        return manage()
    if args.role == 'capture':
        import runpy
        runpy.run_module('iphone_backend.airplay.capture_worker', run_name='__main__')
        return 0
    import uvicorn
    from .app import create_app
    from .service import SerialWorker
    from .service_manager import RESTART_EXIT
    worker = SerialWorker()
    managed = os.environ.get('BLE_HID_MANAGED') == '1'
    def shutdown():
        server.should_exit = True
    from .manager_client import call
    async def probe():
        return await call('GET','/health')
    app = create_app(worker, restart_callback=shutdown if managed else None,
                     restart_token=os.environ.get('BLE_HID_RESTART_TOKEN', ''),
                     manager_probe=probe if managed else None)
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=8892,
        workers=1, proxy_headers=False, timeout_graceful_shutdown=60))
    server.run()
    from .manager import save
    from .config import RUNTIME_DIR
    save(RUNTIME_DIR/('api-exit-'+worker.instance_id+'.json'),
         {'clean_restart': bool(managed and worker.restart_request and worker.shutdown_clean and not worker.thread.is_alive()),
          'instance_id':worker.instance_id})
    return RESTART_EXIT if managed and worker.restart_request and worker.shutdown_clean and not worker.thread.is_alive() else (1 if worker.restart_request else 0)

if __name__ == '__main__':
    raise SystemExit(main())
