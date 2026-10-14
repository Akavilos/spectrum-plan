"""主动回忆设置、索引维护与仅含来源标识的诊断。"""
from fastapi import Depends, HTTPException, Request
from .service import validate


def install(app,host,system,authorize):
    memory=system.active_memory
    dependencies=[Depends(authorize)]

    @app.get('/api/active-memory',dependencies=dependencies)
    async def view():
        return {'settings':memory.settings,'recent':list(memory.recent),'index':dict(memory.index_status),
                'cached_vectors':len(memory.vectors)}

    @app.post('/api/active-memory/settings',dependencies=dependencies)
    async def save(request:Request):
        values=await request.json()
        try:
            values=validate(values)
            selected=values['embedding_provider_id']
            if selected and (selected not in host.providers or host.providers[selected].config.get('category')!='embedding'):
                raise ValueError('请从已保存的嵌入模型中选择')
            async with system.lock:
                host.config['active_memory']=values;host.config.save_config()
                if not values['vector_enabled'] and memory.task and not memory.task.done():memory.task.cancel()
        except ValueError as error:raise HTTPException(422,str(error)) from error
        return {'ok':True}

    @app.post('/api/active-memory/index',dependencies=dependencies)
    async def prepare():
        try:memory.start_index()
        except ValueError as error:raise HTTPException(422,str(error)) from error
        return {'ok':True}

    @app.post('/api/active-memory/index-clear',dependencies=dependencies)
    async def clear(request:Request):
        if (await request.json()).get('confirm') is not True:raise HTTPException(422,'请确认清空语义索引')
        await memory.close()
        memory.vectors.clear();memory.query_cache.clear();memory.path.unlink(missing_ok=True)
        memory.index_status={'state':'idle','encoded':0,'total':0}
        return {'ok':True}
