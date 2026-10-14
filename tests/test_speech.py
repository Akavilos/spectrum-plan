"""Voice tool delivery, receipt persistence, replay metadata and disabled gates."""
import asyncio,sys,tempfile
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from role_system.host import Host
from role_system.service import RoleSystem
from role_system.ports import Event,ToolContext,ToolUsageError,serialize_segment,deserialize_segment

async def run():
    with tempfile.TemporaryDirectory() as temp:
        host=Host(temp);system=RoleSystem(host);captured=[]
        async def transport(session,chain):captured.append(chain);return {'message_id':101}
        host.transport=transport
        class Provider:
            config={'category':'tts'};ready=True
            async def text_to_speech(self,text):return b'RIFF-audio-fixture'
        host.providers['speech-fixture']=Provider()
        host.config['speech'].update(enabled=True,provider_id='speech-fixture')
        event=Event(host,'qq-9:FriendMessage:1','1','甲','语音测试')
        wrapper=ToolContext(host,event)
        assert host.tools.get_tool('send_voice') is system.speech
        outcome=await system.speech.call(wrapper,text='我刚泡了杯茶。')
        assert '成功' in outcome and len(captured)==1
        assert captured[0].chain[0].type=='record'
        assert '我刚泡了杯茶' in host.history(event.unified_msg_origin)[-1]['content']
        assert '再次' in await system.speech.call(wrapper,text='我刚泡了杯茶。')
        assert len(captured)==1
        encoded=serialize_segment(captured[0].chain[0]);decoded=deserialize_segment(encoded)
        assert decoded.metadata['speech_text']=='我刚泡了杯茶。'
        # Only transport data goes to OneBot; internal transcript metadata stays local.
        assert 'speech_text' not in decoded.data
        host.config['speech']['private']=False
        try:await system.speech.call(wrapper,text='第二段')
        except ToolUsageError:pass
        else:raise AssertionError('私聊关闭未阻止语音')
        host.config['speech']['enabled']=False
        assert host.tools.get_tool('send_voice') is None
        await system.close()
        system.guard.store.close();system.spectrum.store.close()
        system.prism.store.close()
    print('speech_tool_delivery_and_receipts_ok')

if __name__=='__main__':asyncio.run(run())
