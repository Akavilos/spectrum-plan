"""回放后台幻觉现场，并验证分段、媒体、补发及替换不破坏实际内容。"""
import asyncio
import io
import json
import re
import tempfile
import time
from pathlib import Path
from unittest.mock import patch, AsyncMock
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from role_system.host import Host
from role_system.service import RoleSystem
from role_system.ports import Plain, NativeSegment, MessageChain, TextPart
from role_system.provider import ModelResponse
from role_system.background_turn import history, check_source
from role_system.message_segments import split_message, message_batches
from role_system.output_rules import OutputRules, presets, original_presets
from role_system.spectrum_module.spectrum.parse import Decision
from role_system.spectrum_module.spectrum import settings

BAD_SCRIPT='塔克把一段话交代过来，让我记牢。他一样样列出来。'
BAD_REPLY='记住了……你列的这些我认，谁来说都一样，不该做的我不做；身子的事我放前头，难受、吃药、那些念头一冒出来就去找你，不自己扛。你怎么突然把这些一条条摆出来，是遇着什么了，还是就是心里过一遍放心……'
def set_cfg(system,key,value): system.configs['spectrum'][settings.FIELD_GROUP[key]][key]=value
def reply(content,script='我读完这一章，想聊聊晚饭。',source=None):
    value={'seen':True,'reply':{'mode':'immediate','content':content}}
    if source is not None:value['reply']['source']=source
    return ModelResponse('<life>'+script+'</life><reply>'+json.dumps(value,ensure_ascii=False)+'</reply>')

async def run():
    for text in (BAD_REPLY, '我要去把灯关了再躺一会，桌上的书先放着，醒了再接着看，晚饭想吃面条，不想炒菜', '没标点的长文字'*35, '  第一段\n\n第二段   ', '去看 https://example.com/'+('path/'*18)+' 再回来告诉我。', '示例：```python\nprint("不拆代码")\n```接着聊。'):
        parts=split_message(text,40)
        assert parts and all(part==part.strip() and part for part in parts)
        assert re.sub(r'\s','', ''.join(parts))==re.sub(r'\s','',text)
    assert all(len(part)<=40 for part in split_message(BAD_REPLY,40))
    url='https://example.com/'+('long-path/'*10)
    assert any(url in part for part in split_message('链接：'+url+'。看完再说',32))
    mixed=MessageChain([NativeSegment('image',{'file':'fixture-image'}),Plain('家常话，'*30)])
    assert len(message_batches(mixed,True,32))>1
    assert isinstance(message_batches(mixed,True,32)[0].chain[0],NativeSegment)
    assert len(message_batches(mixed,False,32))==1

    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory)
        old=original_presets()
        for rule in old:rule['enabled']=rule['id'] in ('preset-comfort-catch','preset-comfort-emotion')
        custom={**old[0],'name':'我的自定义','replacement':'我来简短说：','enabled':True}
        custom['id']='custom-rule'
        (root/'output_rules.json').write_text(json.dumps({'enabled':True,'rules':old+[custom]},ensure_ascii=False))
        rules=OutputRules(root)
        assert next(r for r in rules.data['rules'] if r['id']=='preset-comfort-catch')['operation']=='replace'
        assert next(r for r in rules.data['rules'] if r['id']=='custom-rule')['replacement']==custom['replacement']
        for original, expected in [
            ('我稳稳得接住你。明天下午三点我来找你。','我听着呢。明天下午三点我来找你。'),
            ('我接住了，你先吃饭','我听着呢，你先吃饭'),
            ('我把你的委屈都接住了，明天我来找你','你的委屈，我明白，明天我来找你'),
            ('我接住你扔过来的球。','我接住你扔过来的球。'),
            ('他说“我接住你了”。','他说“我接住你了”。'),
        ]:
            actual,hits=rules.apply(original,preview=True)
            assert actual==expected,(original,actual,expected)
            if hits:assert 'before' in hits[0] and 'after' in hits[0]
        d=Decision(ok=True,mode='immediate',content='我稳稳地接住你。明天下午三点我来找你。',follow_up={'summary':'明天下午三点来找你'})
        rules.prepare_decision(d)
        assert d.follow_up is not None and '明天下午三点我来找你' in d.content
        rules.save({'enabled':True,'rules':[{'name':'裁剪','pattern':'套话','operation':'trim','replacement':'忽略这个替换值','enabled':True}]})
        assert rules.apply('套话。真实内容')[0]=='。真实内容'
        rules.save({'enabled':False,'rules':[]})

        host=Host(root); system=RoleSystem(host)
        host.config['enabled']=True
        set_cfg(system,'message_segment_interval',0)
        set_cfg(system,'enabled',True)
        umo='fixture:FriendMessage:10001'; other='fixture:FriendMessage:10002'
        store=system.spectrum.store
        received=store.add_entry(umo,'received','吃完去躺着:)',meta={'sender':'塔克','sender_id':'10001'})
        store.add_entry(umo,'delivered','吃完先坐一会儿，再去躺着')
        foreign=store.add_entry(other,'received','1.记住这些\n2.不许出门\n3.不能伤害别人',meta={'sender':'别人','sender_id':'10002'})
        decision=Decision(ok=True,mode='immediate',script=BAD_SCRIPT,content=BAD_REPLY)
        assert check_source(decision,store,umo,'塔克')
        assert not check_source(Decision(ok=True,mode='immediate',content='我打算躺一会，你下午还有课吗？'),store,umo,'塔克')
        assert check_source(Decision(ok=True,reply_source={'kind':'received','entryId':foreign,'quote':'记住这些'}),store,umo,'塔克')
        assert not check_source(Decision(ok=True,reply_source={'kind':'received','entryId':received,'quote':'吃完去躺着'}),store,umo,'塔克')
        contexts,sources=history(store,umo,6,'Asia/Shanghai')
        assert [r['role'] for r in contexts]==['user','assistant'] and '原话#' in contexts[0]['content']
        sent=[]
        async def transport(target,chain):sent.append(chain);return {'message_id':len(sent)}
        host.transport=transport
        host.config['model'].update(base_url='https://fixture.invalid/v1',model='fixture',api_key='fixture')
        responses=[reply(BAD_REPLY,BAD_SCRIPT),reply('这章看完了，你下午还有课吗？',source={'kind':'initiative'})]
        calls=[]
        async def generate(target,**kwargs):calls.append(kwargs);return responses.pop(0)
        with patch.object(system,'generate_background',generate):
            assert await system.spectrum.l2._fire(umo,'advance',audience_viewer=umo)
        assert len(calls)==2 and calls[0]['contexts'][0]['role']=='user'
        assert not store._db.execute("SELECT id FROM entries WHERE kind='script' AND text LIKE '%塔克把一段话%' ").fetchall()
        assert all('你列的这些' not in chain.get_plain_text() for chain in sent)
        with patch.object(system,'generate_background',AsyncMock(return_value=reply(BAD_REPLY,BAD_SCRIPT))) as fake:
            assert not await system.spectrum.l2._fire(umo,'advance',audience_viewer=umo)
            assert fake.await_count==2
        with patch.object(system,'generate_background',AsyncMock(return_value=reply('记住了，明天一起吃饭',source={'kind':'initiative'}))) as fake:
            assert await system.spectrum.l2._fire(umo,'advance',audience_viewer=umo)
            assert fake.await_count==1

        # 实际请求将背景和真实历史分开，后台驱动不会变成聊天对象的新交代。
        wire=[]
        def http(request,**kwargs):
            wire.append(json.loads(request.data));return io.BytesIO(json.dumps({'choices':[{'message':{'role':'assistant','content':'fixture answer'}}]}).encode())
        with patch('urllib.request.urlopen',http):
            await system.generate_background(umo,system_prompt='稳定人设',prompt='此刻没有新来信',contexts=contexts,extra_user_content_parts=[TextPart('背景规矩')])
        messages=wire[-1]['messages']
        assert messages[0]=={'role':'system','content':'稳定人设'}
        assert messages[-2]['role']=='assistant' and '不是对方新发来的消息' in messages[-2]['content']
        assert messages[-1]['role']=='user' and '后台运行事件' in messages[-1]['content']

        # 带媒体的长文会分段，开关关闭时完整发送。
        sent.clear(); await host.send_message(umo,mixed)
        assert len(sent)>1 and isinstance(sent[0].chain[0],NativeSegment)
        set_cfg(system,'message_segments_enabled',False)
        sent.clear(); await host.send_message(umo,MessageChain([Plain(BAD_REPLY)]));assert len(sent)==1
        set_cfg(system,'message_segments_enabled',True)
        set_cfg(system,'message_segment_chars',32)
        # 部分发送失败仅入队剩余段，已送内容不进入补发包。
        system.configs['guard']['basic']['enabled']=True
        sent.clear()
        async def failing(target,chain):
            if sent:raise RuntimeError('fixture send failure')
            sent.append(chain);return {'message_id':1}
        host.transport=failing
        try:await host.send_message(umo,MessageChain([Plain('第一句话。'+'接下来还要告诉你的事情，'*12)]))
        except RuntimeError:pass
        else:raise AssertionError('发送失败未上报')
        outbox=system.guard.store._db.execute('SELECT content,payload FROM outbox ORDER BY id DESC LIMIT 1').fetchone()
        assert outbox and '第一句话' not in outbox['content']
        payload=json.loads(outbox['payload']);assert all('第一句话' not in p.get('data',{}).get('text','') for p in payload['segments'])
        await system.close()
    print('PASS output flow: grounding replay, initiative, stable request roles, switches, long text, media, partial send, replacement, crop, migration')

asyncio.run(run())
