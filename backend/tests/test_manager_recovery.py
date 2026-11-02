import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch
from iphone_backend.manager import Manager, save
import httpx

class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_authenticated_identity_must_match_registered_process(self):
        for mismatch in ({'pid':10}, {'created_at':2}):
            with self.subTest(mismatch=mismatch), tempfile.TemporaryDirectory() as folder, patch('iphone_backend.manager.RUNTIME_DIR',Path(folder)), patch('iphone_backend.manager.subprocess.Popen') as spawn:
                save(Path(folder)/'api-instance.json',{'pid':9,'created_at':1,'instance_id':'known'})
                process=Mock()
                process.create_time.return_value=1
                response=Mock()
                response.json.return_value=dict({'pid':9,'created_at':1,'runtime_dir':folder,'protocol':1,'instance_id':'known'},**mismatch)
                client=AsyncMock()
                client.__aenter__.return_value=client
                client.get.return_value=response
                manager=Manager('t'*32)
                manager.api_health=AsyncMock(return_value={'pid':9,'instance_id':'known'})
                with patch('iphone_backend.manager.psutil.Process',return_value=process),patch('iphone_backend.manager.httpx.AsyncClient',return_value=client):
                    await manager.supervise()
                spawn.assert_not_called()
                self.assertIn('identity incompatible',manager.api_error)

    async def test_windows_absent_listener_timeout_checks_bind_before_spawn(self):
        with tempfile.TemporaryDirectory() as folder, patch('iphone_backend.manager.RUNTIME_DIR',Path(folder)), patch('iphone_backend.manager.port_available') as available, patch('iphone_backend.manager.subprocess.Popen') as spawn, patch('iphone_backend.manager.psutil.Process') as process:
            process.return_value.create_time.return_value=1.0
            spawn.return_value.pid=123
            spawn.return_value.poll.return_value=1
            manager=Manager('t'*32)
            manager.api_health=AsyncMock(side_effect=httpx.ConnectTimeout(''))
            await manager.supervise()
            available.assert_called_once()
            spawn.assert_called_once()
            self.assertIn('exited during startup',manager.api_error)

    async def test_venv_descendant_identity_and_clean_launcher_exit(self):
        with tempfile.TemporaryDirectory() as folder, patch('iphone_backend.manager.RUNTIME_DIR',Path(folder)), patch('iphone_backend.manager.port_available'), patch('iphone_backend.manager.subprocess.Popen') as spawn:
            launcher=Mock(pid=10)
            actual=Mock(pid=11)
            launcher.create_time.return_value=1.0
            launcher.children.return_value=[actual]
            actual.create_time.return_value=2.0
            actual.cmdline.return_value=['python','-m','iphone_backend','--role','api']
            actual.is_running.return_value=False
            child=Mock(pid=10,returncode=75)
            child.poll.side_effect=[None,75]
            spawn.side_effect=[child,RuntimeError('second spawn observed')]
            save(Path(folder)/'api-exit-real.json',{'clean_restart':True})
            manager=Manager('t'*32)
            manager.api_health=AsyncMock(side_effect=[httpx.ConnectTimeout(''),{'pid':11,'instance_id':'real'}])
            with patch('iphone_backend.manager.psutil.Process',side_effect=lambda pid:launcher if pid==10 else actual):
                await manager.supervise()
            self.assertEqual(json.loads((Path(folder)/'api-instance.json').read_text())['pid'],11)
            self.assertEqual(spawn.call_count,2)
            self.assertIn('second spawn observed',manager.api_error)

    async def test_timeout_with_occupied_port_never_spawns(self):
        with tempfile.TemporaryDirectory() as folder, patch('iphone_backend.manager.RUNTIME_DIR',Path(folder)), patch('iphone_backend.manager.port_available',side_effect=OSError('occupied')), patch('iphone_backend.manager.subprocess.Popen') as spawn:
            manager=Manager('t'*32)
            manager.api_health=AsyncMock(side_effect=httpx.ReadTimeout(''))
            await manager.supervise()
            spawn.assert_not_called()
            self.assertIn('occupied',manager.api_error)

    async def test_unknown_existing_api_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as folder, patch('iphone_backend.manager.RUNTIME_DIR',Path(folder)), patch('iphone_backend.manager.subprocess.Popen') as spawn:
            manager=Manager('t'*32)
            manager.api_health=AsyncMock(return_value={'pid':999,'instance_id':'foreign'})
            await manager.supervise()
            spawn.assert_not_called()
            self.assertEqual(manager.api_state,'attention_required')
            self.assertIn('not registered',manager.api_error)

    async def test_pid_reuse_rejected(self):
        with tempfile.TemporaryDirectory() as folder, patch('iphone_backend.manager.RUNTIME_DIR',Path(folder)), patch('iphone_backend.manager.subprocess.Popen') as spawn:
            save(Path(folder)/'api-instance.json',{'pid':9,'created_at':1,'instance_id':'known'})
            process=Mock()
            process.create_time.return_value=2
            manager=Manager('t'*32)
            manager.api_health=AsyncMock(return_value={'pid':9,'instance_id':'known'})
            with patch('iphone_backend.manager.psutil.Process',return_value=process):
                await manager.supervise()
            spawn.assert_not_called()
            self.assertIn('identity mismatch',manager.api_error)

    async def test_unproven_adopted_exit_not_restarted(self):
        with tempfile.TemporaryDirectory() as folder, patch('iphone_backend.manager.RUNTIME_DIR',Path(folder)), patch('iphone_backend.manager.subprocess.Popen') as spawn:
            save(Path(folder)/'api-instance.json',{'pid':9,'created_at':1,'instance_id':'known'})
            process=Mock()
            process.create_time.return_value=1
            process.is_running.return_value=False
            response=Mock()
            response.json.return_value={'pid':9,'created_at':1,'runtime_dir':folder,'protocol':1,'instance_id':'known'}
            client=AsyncMock()
            client.__aenter__.return_value=client
            client.get.return_value=response
            manager=Manager('t'*32)
            manager.api_health=AsyncMock(return_value={'pid':9,'instance_id':'known'})
            with patch('iphone_backend.manager.psutil.Process',return_value=process),patch('iphone_backend.manager.httpx.AsyncClient',return_value=client):
                await manager.supervise()
            spawn.assert_not_called()
            self.assertIn('without clean restart proof',manager.api_error)
