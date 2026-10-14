"""用独立数据和替身发送器验证私聊合并的计时、调用和原文边界。"""
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from role_system.private_messages import PrivateMessages
from role_system.host import Host
from role_system.service import RoleSystem
from role_system.ports import Event, NativeSegment
from role_system.provider import ModelResponse
from role_system.spectrum_module.spectrum import settings
from role_system.spectrum_module.spectrum.prompt_flow import exchange_context, missing_exchange


def configure(system, key, value):
    system.configs['spectrum'][settings.FIELD_GROUP[key]][key] = value


async def run():
    with tempfile.TemporaryDirectory() as directory, patch('role_system.image_input.read_image',return_value=('image/png','iVBORw0KGgo=')):
        host = Host(Path(directory)); system = RoleSystem(host)
        host.config['enabled'] = True
        host.config['model'].update(base_url='https://fixture.invalid/v1', model='fixture', api_key='fixture')
        for key, value in [('enabled',True), ('possession_enabled',False), ('private_message_merge_wait',.08),
                           ('private_message_merge_max_wait',.25), ('message_segment_interval',0)]:
            configure(system, key, value)
        primary = 'fixture:FriendMessage:100'
        other = 'fixture:FriendMessage:200'
        def event(text, umo=primary, id='1'):
            return Event(host, umo, umo.rsplit(':',1)[-1], '人物'+umo.rsplit(':',1)[-1], text, message_id=id)
        calls, prism_calls, sent = [], [], []
        async def generate(e, req, **kwargs):
            calls.append((e, req))
            return ModelResponse('<life>我正在和他聊晚饭。</life><reply>' + json.dumps(
                {'seen':True, 'reply':{'mode':'immediate','content':'我刚吃过了。'}}, ensure_ascii=False)+'</reply>')
        async def read(e, req): prism_calls.append(e.message_str)
        async def transport(umo, chain): sent.append((umo,chain)); return {'message_id':len(sent)}
        system._generate = generate
        system.prism.on_llm_request = read
        host.transport = transport
        first = asyncio.create_task(system.receive(event('晚饭吃了吗', id='1')))
        await asyncio.sleep(.04)
        second_event = event('我刚吃完', id='2')
        second_event.parts.append(NativeSegment('image', {'url':'https://fixture.invalid/photo.jpg'}))
        second = asyncio.create_task(system.receive(second_event))
        await asyncio.sleep(.05)
        assert not calls, '第二条来信没有重置等待时间'
        assert await asyncio.gather(first,second) == ['sent','sent']
        assert len(calls)==1 and prism_calls==['晚饭吃了吗\n我刚吃完']
        combined, req = calls[0]
        assert combined.user_id=='100' and req.media[0]['image_url']['url']=='data:image/png;base64,iVBORw0KGgo='
        assert req.prompt=='晚饭吃了吗\n我刚吃完'
        assert not any('晚饭吃了吗' in str(row) or '我刚吃完' in str(row) for row in req.contexts)
        rows=host.history_db.execute("SELECT content,sender_id FROM messages WHERE role='user' ORDER BY id").fetchall()
        assert rows==[('晚饭吃了吗','100'),('我刚吃完','100')]
        entries=system.spectrum.store.recent_exchange(primary)
        assert [r['text'] for r in reversed(entries) if r['kind']=='received']==['晚饭吃了吗','我刚吃完']
        current_ids=combined.get_extra('_spectrum_current_received_ids')
        assert len(current_ids)==2
        assert not system.spectrum._format_exchange(primary,req=req,current_text=combined.message_str,excluded_ids=current_ids).startswith('人物100：晚饭')

        # 相同文字的旧来信仍属于历史，不按内容误删。
        old=system.spectrum.store.add_entry(primary,'received','又来了')
        current=system.spectrum.store.add_entry(primary,'received','又来了')
        history=system.spectrum.store.recent_exchange(primary)
        isolated=exchange_context(history,'又来了',system.spectrum.store.parse_meta,is_group=False,current_ids=[current])
        assert sum(row['content']=='又来了' for row in isolated)==1
        assert '又来了' in missing_exchange(history,[],'又来了',system.spectrum.store.parse_meta,is_group=False,current_ids=[current])

        # 不同私聊各自合并；群聊和指令在等待期间正常进入处理。
        before=len(calls)
        a=asyncio.create_task(system.receive(event('A',id='3')))
        b=asyncio.create_task(system.receive(event('B',other,'4')))
        await asyncio.sleep(.01)
        group=event('群聊原文','fixture:GroupMessage:300','5'); group.is_at_or_wake_command=False
        assert await system.receive(group)=='observed'
        async def command(e): return None
        system.commands['测试']=command
        assert await system.receive(event('/测试',id='6'))=='command'
        assert len(calls)==before
        await asyncio.gather(a,b)
        assert {e.unified_msg_origin for e,r in calls[before:]}=={primary,other}
        assert {r.prompt for e,r in calls[before:]}=={'A','B'}

        # 关闭时已有的待合并消息不会被后一条越过或丢弃。
        before=len(calls)
        waiting=asyncio.create_task(system.receive(event('关前',id='6a')))
        await asyncio.sleep(.01)
        configure(system,'private_message_merge_enabled',False)
        assert await system.receive(event('关后',id='6b'))=='sent'
        assert await waiting=='sent'
        assert len(calls)==before+1 and calls[-1][1].prompt=='关前\n关后'

        # 关闭合并时立即处理；冻结中仍逐条保存而不调用模型。
        configure(system,'private_message_merge_enabled',False)
        assert await system.receive(event('立即处理',id='7'))=='sent'
        configure(system,'private_message_merge_enabled',True)
        before=len(calls)
        with patch('role_system.service.freeze.is_frozen',return_value=True):
            results=await asyncio.gather(system.receive(event('冻结一',id='8')),system.receive(event('冻结二',id='9')))
        assert results==['frozen','frozen'] and len(calls)==before
        entries=system.spectrum.store.recent_exchange(primary)
        assert sum(r['text']=='冻结一' for r in entries)==1 and sum(r['text']=='冻结二' for r in entries)==1

        configure(system,'l2_sleep_enabled',True)
        system.spectrum.store.set_sleep_asleep(time.time()+3600)
        before=len(calls),len(prism_calls)
        assert await asyncio.gather(system.receive(event('睡中一',id='9a')),system.receive(event('睡中二',id='9b')))==['sleeping','sleeping']
        assert (len(calls),len(prism_calls))==before
        assert [row['text'] for row in system.spectrum.store.get_sleep_inbox()]==['睡中一','睡中二']
        system.spectrum.store.set_sleep_awake()

        pending=asyncio.create_task(system.receive(event('尚未说完',id='10')))
        await asyncio.sleep(.01)
        await system.close()
        assert await pending=='shutdown'
        import sqlite3
        with sqlite3.connect(Path(directory)/'conversation.db') as db:
            assert db.execute("SELECT COUNT(*) FROM messages WHERE content='尚未说完'").fetchone()[0]==1

    # 持续输入达到上限后成轮，生成中的后续来信进入下一轮。
    lock=asyncio.Lock(); requests=[]; saved=[]; active=asyncio.Event(); release=asyncio.Event()
    config={'private_message_merge_enabled':True,'private_message_merge_wait':.08,'private_message_merge_max_wait':.12}
    async def consume(e):
        requests.append(e.message_str)
        if len(requests)==1:
            active.set(); await release.wait()
        return 'sent'
    queue=PrivateMessages(lock,consume,saved.append,lambda key,default:config.get(key,default))
    e=lambda text:Event(None,'fixture:FriendMessage:1','1','名字',text)
    tasks=[]
    for text in ('一','二','三','四'):
        tasks.append(asyncio.create_task(queue.receive(e(text))))
        await asyncio.sleep(.04)
    assert active.is_set() and len(requests)==1
    later=asyncio.create_task(queue.receive(e('五')))
    await asyncio.sleep(.01);release.set()
    await asyncio.gather(*tasks,later)
    assert requests==['一\n二\n三','四\n五'],requests
    await queue.close()
    async def failure(e): raise ValueError('fixture model failure')
    queue=PrivateMessages(asyncio.Lock(),failure,saved.append,lambda key,default:config.get(key,default))
    try: await queue.receive(e('失败'))
    except ValueError: pass
    else: raise AssertionError('模型失败未返回接收者')
    assert not queue.pending
    await queue.close()
    print('PASS private messages: debounce, maximum, private isolation, group/commands, disabled and pending switch, media, raw history, no duplication, frozen, sleeping, shutdown, in-flight follow-up, errors')


asyncio.run(run())
