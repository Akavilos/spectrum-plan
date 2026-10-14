"""验证日志捕获、错误详情、认证、筛选及重启保留，不访问模型或 QQ。"""
import asyncio
import logging
import tempfile
from pathlib import Path
from test_http import ASGIClient
from fastapi import FastAPI, Request, HTTPException
from role_system.host import Host
from role_system.service import RoleSystem
from role_system.web import install_panel
from role_system.runtime_logs import RuntimeLogs


async def run():
    with tempfile.TemporaryDirectory() as directory:
        app = FastAPI()
        def authorize(request: Request):
            if request.cookies.get('role_panel') != 'fixture-panel-key':
                raise HTTPException(401, '未登录')
        host = Host(directory, app, authorize)
        system = RoleSystem(host)
        install_panel(app, host, system, authorize, 'fixture-panel-key')
        host.config['model']['api_key'] = 'fixture-model-secret'
        logger = logging.getLogger('role_system')
        logger.info('光谱测试 INFO')
        try:
            raise ValueError('fixture-model-secret')
        except ValueError:
            logger.exception('棱镜测试错误')
        # Verify that the platform logger forwards records to the log panel.
        from loguru import logger as platform
        host.runtime_logs.bind_platform(platform)
        try:
            raise RuntimeError('platform trace')
        except RuntimeError:
            platform.exception('平台连接错误')
        rows = host.runtime_logs.read(level='ERROR', query='棱镜')
        assert len(rows) == 1 and 'ValueError' in rows[0]['message']
        assert 'fixture-model-secret' not in rows[0]['message']
        assert 'RuntimeError' in host.runtime_logs.read(query='平台连接')[0]['message']
        async with ASGIClient(app) as client:
            assert (await client.get('/api/logs')).status_code == 401
            assert (await client.get('/api/logs/download')).status_code == 401
            await client.post('/api/login', json={'key': 'fixture-panel-key'})
            response = await client.get('/api/logs')
            assert response.status_code == 200 and len(response.json()['entries']) >= 3
            download = await client.get('/api/logs/download')
            assert download.status_code == 200 and '光谱测试' in download.text
            assert 'fixture-model-secret' not in download.text
        host.runtime_logs.close()
        reopened = RuntimeLogs(Path(directory)/'logs', lambda: ['fixture-model-secret'])
        assert len(reopened.read()) >= 3
        reopened.close()
        await system.close()
    print('PASS: standard/platform logs, traceback, redaction, authentication, filters, download and restart history')


if __name__ == '__main__':
    asyncio.run(run())
