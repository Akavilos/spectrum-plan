"""扩展插件可调用的语音与重排接口；不会自动改变现有聊天的媒体处理。"""
import uuid
from pathlib import Path
from .provider import Provider
from .model_protocols import endpoint
from .model_http import ModelError


class UtilityProvider(Provider):
    async def text_to_speech(self, text):
        from .model_fallback import invoke
        return await invoke(self, '_text_to_speech', {'text': text})

    async def speech_to_text(self, audio, filename='audio.wav'):
        from .model_fallback import invoke
        return await invoke(self, '_speech_to_text', {'audio': audio, 'filename': filename})

    async def rerank(self, query, documents):
        from .model_fallback import invoke
        return await invoke(self, '_rerank', {'query': query, 'documents': documents})

    def url(self, suffix):
        c = self.config
        if c.get('protocol', 'openai') not in ('openai', 'responses'):
            raise ModelError('此用途需要 OpenAI 兼容语音接口或 /rerank 接口，请按服务商说明配置')
        return endpoint(c['base_url'], 'openai', listing=True).removesuffix('/models') + suffix

    async def _text_to_speech(self, text):
        c = self.config
        return await self.http.json(c, self.url('/audio/speech'), {
            **c.get('source_parameters', {}), **c.get('parameters', {}),
            'model': c['model'], 'input': text, 'voice': c.get('parameters', {}).get('voice', 'alloy')}, binary=True)

    async def _speech_to_text(self, audio, filename='audio.wav'):
        c = self.config
        boundary = 'spectrum-' + uuid.uuid4().hex
        fields = {**c.get('source_parameters', {}), **c.get('parameters', {}), 'model': c['model']}
        chunks = []
        for key, value in fields.items():
            chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
        suffix = Path(filename).suffix.lower()
        if suffix not in ('.wav', '.mp3', '.mp4', '.mpeg', '.mpga', '.m4a', '.webm', '.ogg', '.flac'):
            raise ModelError('请选择接口支持的音频文件格式')
        chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="audio{suffix}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode())
        chunks.extend([audio, f'\r\n--{boundary}--\r\n'.encode()])
        data = await self.http.json(c, self.url('/audio/transcriptions'), raw_data=b''.join(chunks), content_type='multipart/form-data; boundary=' + boundary)
        return data['text']

    async def _rerank(self, query, documents):
        c = self.config
        data = await self.http.json(c, self.url('/rerank'), {**c.get('source_parameters', {}), **c.get('parameters', {}),
            'model': c['model'], 'query': query, 'documents': documents})
        return data.get('results', data.get('data', []))
