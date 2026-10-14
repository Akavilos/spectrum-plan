"""Optional voice tool; synthesis and delivery use the existing model and guard ports."""
import base64
import time
from .ports import MessageChain,NativeSegment,Plain,ToolUsageError,logger
from .model_http import ModelError
from .spectrum_module.spectrum import freeze

DEFAULTS={'enabled':False,'provider_id':'','private':True,'group':True,'max_chars':80,'send_text':False}


def validate(values):
    if not isinstance(values,dict) or set(values)-set(DEFAULTS):raise ValueError('语音设置包含未知选项')
    result={**DEFAULTS,**values}
    for key in ('enabled','private','group','send_text'):
        if type(result[key]) is not bool:raise ValueError('语音开关格式不正确')
    if not isinstance(result['provider_id'],str):raise ValueError('请选择已配置的语音模型')
    if type(result['max_chars']) is not int or not 10<=result['max_chars']<=120:raise ValueError('语音台词上限为 10 至 120 字')
    return result


class Speech:
    name='send_voice'
    description='我想在当前会话用语音说话时，可以使用这个工具。我填写实际要说的台词；发送成功后，我不再重复同一段台词。'
    parameters={'type':'object','properties':{'text':{'type':'string','description':'我实际要说的台词'}},'required':['text']}

    def __init__(self,system):
        self.system=system;self.host=system.host
        self.host.config.setdefault('speech',dict(DEFAULTS));self.host.config.save_config()

    @property
    def settings(self):return validate(self.host.config.get('speech',{}))

    @property
    def active(self):return self.settings['enabled']

    @active.setter
    def active(self,value):
        self.host.config['speech']={**self.settings,'enabled':bool(value)};self.host.config.save_config()

    def provider(self):
        provider=self.host.providers.get(self.settings['provider_id'])
        return provider if provider and provider.config.get('category')=='tts' and provider.ready else None

    async def call(self,wrapper,text):
        event=wrapper.context.event;settings=self.settings
        if not self.active:raise ToolUsageError('语音工具未开启')
        group=':GroupMessage:' in event.unified_msg_origin
        if not settings['group' if group else 'private']:raise ToolUsageError('当前会话类型未开启语音')
        if not isinstance(text,str) or not text.strip():raise ToolUsageError('语音台词不能为空')
        text=self.host.output_rules.apply(text.strip())[0].strip()
        if not text:raise ToolUsageError('输出整理后台词为空')
        if not any(char.isalnum() for char in text):raise ToolUsageError('语音需要可朗读的文字')
        if len(text)>settings['max_chars']:raise ToolUsageError(f"我每次语音最多说 {settings['max_chars']} 字，需要缩短台词")
        sent=set(event.get_extra('_speech_sent',[]))
        if text in sent:return '我已经成功发送这段语音，没有再次发送。'
        epoch=event.get_extra('_spectrum_freeze_epoch',freeze.checkpoint())
        if not freeze.may_commit(epoch):raise freeze.FrozenSkipped('暂停期间不合成或发送语音')
        provider=self.provider()
        if provider is None:raise ToolUsageError('语音模型尚未配置或不可用')
        started=time.monotonic()
        logger.info('[语音合成开始] %s | %s 字',event.unified_msg_origin,len(text))
        try:
            data=await provider.text_to_speech(text)
        except (freeze.FrozenSkipped,):raise
        except ModelError as error:
            logger.warning('[语音合成失败] %s',error)
            if not freeze.may_commit(epoch):raise freeze.FrozenSkipped('语音失败后系统暂停')
            if await self.host.send_message(event.unified_msg_origin,MessageChain([Plain(text)])) is False:
                raise ToolUsageError('语音失败且文字未发送')
            sent.add(text);event.set_extra('_speech_sent',list(sent))
            return '这次语音没有发出，我已用文字发送这段话。'
        if not freeze.may_commit(epoch):raise freeze.FrozenSkipped('合成期间系统暂停，未发送语音')
        logger.info('[语音合成完成] %s | %s 字节 | %.1f 秒',event.unified_msg_origin,len(data),time.monotonic()-started)
        segment=NativeSegment('record',{'file':'base64://'+base64.b64encode(data).decode('ascii')},metadata={'speech_text':text})
        chain=MessageChain(([Plain(text)] if settings['send_text'] else [])+[segment])
        chain.output_prepared=True
        if await self.host.send_message(event.unified_msg_origin,chain) is False:raise ToolUsageError('语音未发送')
        sent.add(text);event.set_extra('_speech_sent',list(sent))
        return '我已成功在当前会话发送语音，台词：'+text


def install(app,host,system,authorize):
    from fastapi import Depends,HTTPException,Request
    from fastapi.responses import FileResponse
    from pathlib import Path
    dependencies=[Depends(authorize)]
    @app.get('/assets/speech.js')
    async def script():return FileResponse(Path(__file__).parent/'assets/speech.js',media_type='application/javascript')
    @app.get('/api/speech',dependencies=dependencies)
    async def view():return {'settings':system.speech.settings,'ready':system.speech.provider() is not None}
    @app.post('/api/speech/settings',dependencies=dependencies)
    async def save(request:Request):
        try:
            values=validate(await request.json());id=values['provider_id']
            if id and (id not in host.providers or host.providers[id].config.get('category')!='tts'):
                raise ValueError('请选择模型管理中已保存的语音合成模型')
            async with system.lock:host.config['speech']=values;host.config.save_config()
        except ValueError as error:raise HTTPException(422,str(error)) from error
        return {'ok':True}
