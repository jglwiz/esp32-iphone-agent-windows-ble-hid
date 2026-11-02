"""Authenticated loopback management IPC, separate from device operations."""
import os
import httpx

async def call(method, path, body=None):
    token = os.environ.get('BLE_HID_RESTART_TOKEN','')
    url = 'http://127.0.0.1:'+os.environ.get('IPHONE_MANAGER_PORT','8894')
    async with httpx.AsyncClient(trust_env=False, timeout=2) as client:
        response = await client.request(method, url+path, json=body,
            headers={'Authorization':'Bearer '+token})
        response.raise_for_status()
        return response.json()
