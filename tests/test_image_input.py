"""Offline image reception and public-protocol checks; no supplier or QQ test messages."""
import asyncio,base64,io,sys,urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from role_system.ports import Event,NativeSegment,ModelRequest
from role_system.image_input import receive_images,read_image
from role_system.model_protocols import encode
from role_system.provider import Provider
from role_system.private_messages import merged_event

PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j2ioAAAAASUVORK5CYII=')
URI='data:image/png;base64,'+base64.b64encode(PNG).decode()


async def main():
    lookups=[]
    class Platform:
        async def image_info(self,file):
            lookups.append(file);return {'url':'https://fixture.invalid/image'}
    host=SimpleNamespace(platform_for=lambda umo:Platform())
    first=Event(host,'qq-test:FriendMessage:1','1','人物1','看看这张图',message_id='1')
    second=Event(host,first.unified_msg_origin,'1','人物1','[图片]',message_id='2',
        parts=[NativeSegment('image',{'file':'image-id.jpg'})])
    event=merged_event([first,second]);req=ModelRequest(event.message_str,"",[],None)
    with patch('urllib.request.urlopen',side_effect=lambda *a,**k:io.BytesIO(PNG)):
        await receive_images(event,req)
    assert lookups==['image-id.jpg'] and req.media[0]['image_url']['url']==URI
    assert req.prompt=='看看这张图\n[图片]' and not req.extra_user_content_parts
    for source in (URI,'base64://'+base64.b64encode(PNG).decode()):
        assert read_image(source)==('image/png',base64.b64encode(PNG).decode())
    failed=Event(host,event.unified_msg_origin,'1','人物1','[图片]',
        parts=[NativeSegment('image',{'url':'https://fixture.invalid/forbidden?secret=private'})])
    req_fail=ModelRequest(failed.message_str,"",[],None)
    with patch('urllib.request.urlopen',side_effect=urllib.error.HTTPError('fixture',403,'Forbidden',{},None)):
        await receive_images(failed,req_fail)
    assert not req_fail.media and '没有看到' in req_fail.extra_user_content_parts[0].text
    with patch('urllib.request.urlopen',return_value=io.BytesIO(b'<html>Access denied</html>')):
        try:read_image('https://fixture.invalid/not-image')
        except ValueError:pass
        else:raise AssertionError('HTML error page accepted as an image')
    plain=Event(host,event.unified_msg_origin,'1','人物1','普通消息');plain_req=ModelRequest(plain.message_str,"",[],None)
    with patch('urllib.request.urlopen',side_effect=AssertionError('plain text must not download')):
        await receive_images(plain,plain_req)
    assert not plain_req.media and not plain_req.extra_user_content_parts
    wire=[]
    provider=Provider({'base_url':'https://fixture.invalid/v1','model':'gemini-fixture','api_key':'fixture','protocol':'openai'})
    async def http(config,url,body,**kwargs):
        wire.append(body);return {'choices':[{'message':{'role':'assistant','content':'图片收到'},'finish_reason':'stop'}]}
    provider.http.json=http
    reply=await provider._text_chat(prompt=req.prompt,media=req.media,system_prompt='我的固定人设',role_mode='gemini')
    canonical=wire[0]
    assert canonical['messages'][0]=={'role':'system','content':'我的固定人设'}
    assert canonical['messages'][-1]['content'][-1]['image_url']['url']==URI
    assert reply.role_mode=='contract'
    for protocol in ('responses','anthropic','gemini','ollama','azure'):
        payload=encode(protocol,canonical,{},provider.config)
        if protocol=='responses':assert payload['input'][-1]['content'][-1]['image_url']==URI
        elif protocol=='anthropic':assert payload['messages'][-1]['content'][-1]['source']['media_type']=='image/png'
        elif protocol=='gemini':assert payload['contents'][-1]['parts'][-1]['inlineData']['mimeType']=='image/png'
        elif protocol=='ollama':assert payload['messages'][-1]['images'][0]==base64.b64encode(PNG).decode()
        else:assert payload['messages'][-1]['content'][-1]['image_url']['url']==URI
    print('PASS image input: QQ file ID, private batch, URL-to-inline data, explicit unread state, no text downloads, stable system prefix, six public protocols')


asyncio.run(main())

