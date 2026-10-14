"""人格管理接口：底稿可移植，经历认识只能通过私有实例备份迁移。"""
import json
from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from .learning import source_rows


def install(app, system, authorize):
    p = system.personality
    dependencies = [Depends(authorize)]

    @app.get('/api/personality', dependencies=dependencies)
    async def view():
        async with system.lock: return p.view()

    @app.post('/api/personality/settings', dependencies=dependencies)
    async def save(request: Request):
        body = await request.json()
        try:
            async with system.lock:
                p.save(body['data'], confirm=body.get('confirm_persona') is True)
        except (ValueError, KeyError) as error: raise HTTPException(422, str(error))
        return {'ok': True}

    @app.post('/api/personality/draft', dependencies=dependencies)
    async def draft():
        try: return {'ok': True, 'draft': await p.draft()}
        except (ValueError, KeyError, json.JSONDecodeError) as error: raise HTTPException(422, str(error))

    @app.post('/api/personality/apply-draft', dependencies=dependencies)
    async def apply_draft():
        try:
            async with system.lock: p.apply_draft()
        except ValueError as error: raise HTTPException(422, str(error))
        return {'ok': True}

    @app.post('/api/personality/learn', dependencies=dependencies)
    async def learn():
        try: return await p.learn(force=True)
        except (ValueError, KeyError, json.JSONDecodeError) as error: raise HTTPException(422, str(error))

    @app.get('/api/personality/export', dependencies=dependencies)
    async def export():
        return JSONResponse({'format': 'role-system-data-v1', 'sections': {'personality': p.export()}},
                            headers={'Content-Disposition': 'attachment; filename="personality-base.json"'})

    @app.post('/api/personality/new-role', dependencies=dependencies)
    async def new_role():
        async with system.lock: role_id = p.new_role()
        return {'ok': True, 'role_id': role_id}

    @app.post('/api/personality/switch-role', dependencies=dependencies)
    async def switch_role(request: Request):
        body = await request.json()
        try:
            async with system.lock: p.switch_role(body.get('role_id'))
        except ValueError as error: raise HTTPException(422, str(error))
        return {'ok': True}

    @app.get('/api/personality/core-versions', dependencies=dependencies)
    async def core_history(): return {'versions': p.store.core_history(p.config.role_id)}

    @app.post('/api/personality/clear-learning', dependencies=dependencies)
    async def clear():
        async with system.lock:
            p.clear_learning()
        return {'ok': True}

    @app.get('/api/personality/insights/{insight_id}', dependencies=dependencies)
    async def detail(insight_id: int):
        item = p.store.insight(insight_id)
        if item is None or item['role_id'] != p.config.role_id: raise HTTPException(404, '当前角色没有这条认识')
        evidence = [dict(row) for row in source_rows(p.sources, sorted(set(item['supports']) | set(item['doubts'])))]
        return {'insight': item, 'evidence': evidence, 'versions': p.store.history(insight_id)}

    @app.post('/api/personality/insights/{insight_id}', dependencies=dependencies)
    async def edit(insight_id: int, request: Request):
        body = await request.json()
        try:
            async with system.lock: item = p.edit(insight_id, body)
        except ValueError as error: raise HTTPException(422, str(error))
        return {'ok': True, 'insight': item}
