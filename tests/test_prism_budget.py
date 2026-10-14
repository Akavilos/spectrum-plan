"""复现思考占满旧预算、核对配置预算与一次失败计数；所有 HTTP 均为离线替身。"""
import asyncio,io,json,sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from role_system.provider import Provider
from role_system.model_http import ModelError
from role_system.prism_module.prism.reading_model import read_async
from role_system.spectrum_module.spectrum import freeze,limiter

class Wire:
    def __init__(self):self.calls=[]
    def __call__(self,request,**kwargs):
        body=json.loads(request.data);self.calls.append(body)
        cap=body['max_tokens']
        exhausted=cap==2048
        content='' if exhausted else json.dumps({'belonging':.2,'confidence':.7,'topic':'晚饭','tone':'轻松'})
        return io.BytesIO(json.dumps({'choices':[{'message':{'role':'assistant','content':content,'reasoning_content':'private thinking'},
            'finish_reason':'length' if exhausted else 'stop'}],
            'usage':{'completion_tokens':2048 if exhausted else 3000,'completion_tokens_details':{'reasoning_tokens':2048 if exhausted else 2500}}}).encode())

async def run():
    freeze.reset_for_test();limiter.set_cfg_getter(lambda key,default:False if key=='low_freq_mode' else default)
    model={'base_url':'https://fixture.invalid/v1','model':'fixture-thinking','api_key':'fixture-key','max_tokens':None}
    provider=Provider(model);wire=Wire()
    with patch('urllib.request.urlopen',wire):
        # 旧预算失败的诊断不再丢失截断与思考信息。
        try:await provider.text_chat(prompt='fixture',max_tokens=2048)
        except ModelError as error:
            assert '长度上限' in str(error)
            metadata=json.loads(error.detail)
            assert metadata['finish_reason']=='length' and metadata['reasoning_tokens']==2048
            assert 'private thinking' not in error.detail
        else:raise AssertionError('reasoning-only output was accepted as an answer')
        wire.calls.clear()
        result=await read_async(provider,'晚饭吃了吗？')
        assert len(wire.calls)==1 and wire.calls[0]['max_tokens']==8192
        assert result.event.touches
        # 模型管理的显式值优先，包括用户选择的小预算；不偷偷扩大或追加重试。
        model['max_tokens']=12000;wire.calls.clear()
        await read_async(provider,'晚饭吃了吗？')
        assert len(wire.calls)==1 and wire.calls[0]['max_tokens']==12000
        model['max_tokens']=2048;wire.calls.clear()
        controller=freeze.install(lambda key,default:{'freeze_enabled':True,'freeze_fail_streak':3}.get(key,default))
        freeze.begin_failure_scope()
        await read_async(provider,'晚饭吃了吗？')
        assert len(wire.calls)==1 and controller._fail_streak==1 and not freeze.is_frozen()
        freeze.reset_for_test()
    print('PASS: reasoning-only truncation, metadata without thinking text, finite default/configured budget, no extra retries, one freeze failure')

if __name__=='__main__':asyncio.run(run())
