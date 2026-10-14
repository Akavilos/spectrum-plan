"""验证共享限额默认值、峰值预留与限频跳过的冻结隔离；不请求真实模型。"""
import asyncio,json,sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from role_system.spectrum_module.spectrum import freeze,limiter
from role_system.prism_module.prism import runtime

class Wire:
    def __init__(self):self.calls=0
    async def text_chat(self,**kwargs):
        self.calls+=1
        return SimpleNamespace(completion_text='fixture',role='assistant')

async def run():
    root=Path(__file__).resolve().parents[1]
    schema=json.loads((root/'role_system/spectrum_module/_conf_schema.json').read_text(encoding='utf-8'))
    assert schema['stability']['items']['low_freq_rpm']['default']==schema['low_freq_rpm']['default']==60
    assert schema['stability']['items']['low_freq_mode']['default'] is False
    cfg={'freeze_enabled':True,'freeze_fail_streak':3,'low_freq_mode':True,'low_freq_max_wait_seconds':0}
    getter=lambda key,default:cfg.get(key,default)
    old_clock=limiter._clock
    clock=[0.0]
    try:
        limiter._clock=lambda:clock[0];limiter.set_cfg_getter(getter);limiter.reset_state()
        control=freeze.install(getter);wire=Wire()
        # 5轮各按9次保守预算，再为后台工作保留15次共享额度。
        for _ in range(45):await runtime.call_model(wire,prompt='foreground fixture')
        assert limiter.status()['tokens']==15 and wire.calls==45
        for _ in range(15):await freeze.call_model(wire,prompt='background fixture')
        assert wire.calls==60
        for _ in range(4):
            try:await runtime.call_model(wire,prompt='no remaining allowance')
            except limiter.LowFreqSkipped as error:
                runtime.record_failure('reading',error)
            else:raise AssertionError('额度耗尽后仍发送请求')
        assert wire.calls==60 and control._fail_streak==0 and not freeze.is_frozen()
        clock[0]+=1
        await runtime.call_model(wire,prompt='one replenished allowance')
        assert wire.calls==61 and not freeze.is_frozen()
        # 保留用户显式设置，真实接口错误仍执行已有的累计冻结规则。
        cfg['low_freq_rpm']=10;assert limiter.rpm()==10
        limiter.reset_state();assert limiter._take() and limiter.status()['tokens']==9
        for _ in range(3):runtime.record_failure('reading',RuntimeError('actual model failure'))
        assert freeze.is_frozen() and control._fail_streak==3
    finally:
        freeze.reset_for_test();limiter._clock=old_clock;limiter.reset_state();limiter.set_cfg_getter(None)
    print('PASS shared 60/min defaults, 45 foreground+15 background allowance, replenish, skipped requests do not freeze, explicit limit and actual failure threshold preserved')

if __name__=='__main__':asyncio.run(run())
