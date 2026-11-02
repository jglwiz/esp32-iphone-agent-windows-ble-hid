import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, AsyncMock
import httpx
from iphone_backend.manager import Manager

class ManagerIPC(unittest.IsolatedAsyncioTestCase):
    async def test_start_deduplicated_and_survives_api_clients(self):
        with tempfile.TemporaryDirectory() as folder, patch('iphone_backend.manager.RUNTIME_DIR',Path(folder)):
            manager=Manager('t'*32)
            manager.capture.ensure_started=AsyncMock(return_value={'state':'ready','health':{'pid':123}})
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=manager),base_url='http://test') as client:
                self.assertEqual((await client.get('/health')).status_code,401)
                headers={'Authorization':'Bearer '+'t'*32}
                responses=await asyncio.gather(*(client.post('/screen/start',json={'request_id':'same'},headers=headers) for _ in range(10)))
                self.assertTrue(all(r.status_code==202 for r in responses))
                await asyncio.gather(*manager.tasks.values())
                result=await client.get('/screen/start/same',headers=headers)
                self.assertEqual(result.json()['state'],'ready')
                manager.capture.ensure_started.assert_awaited_once()
            restored=Manager('t'*32)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=restored),base_url='http://test') as client:
                result=await client.get('/screen/start/same',headers=headers)
                self.assertEqual(result.json()['state'],'ready')

    async def test_start_failure_is_queryable(self):
        with tempfile.TemporaryDirectory() as folder, patch('iphone_backend.manager.RUNTIME_DIR',Path(folder)):
            manager=Manager('t'*32)
            manager.capture.ensure_started=AsyncMock(side_effect=RuntimeError('foreign owner'))
            await manager.start_capture('failed')
            self.assertIn('foreign owner',manager.path('failed').read_text())
