"""使用断网替身验证回退、工具连续性、各用途与冻结计数。"""
import asyncio
import io
import json
import tempfile
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI, Request, HTTPException
from role_system.host import Host
from role_system.service import RoleSystem
from role_system.web import install_panel
from role_system.provider import Provider, ModelResponse
from role_system.model_http import ModelError
from role_system.model_fallback import ModelFallbackError, canonical_history
from role_system.ports import Event, ModelRequest
from role_system.spectrum_module.spectrum import freeze, limiter
from test_http import ASGIClient


class Wire:
    def __init__(self): self.calls = []; self.fail_backup = False
    def __call__(self, request, **kwargs):
        body = json.loads(request.data) if request.data and request.get_header('Content-type', '').startswith('application/json') else None
        self.calls.append((request.full_url, body))
        if 'primary.invalid' in request.full_url or self.fail_backup:
            raise urllib.error.HTTPError(request.full_url, 503, 'unavailable', {}, io.BytesIO(b'{"error":{"message":"fixture outage"}}'))
        if 'embeddings' in request.full_url: result = {'data': [{'index': i, 'embedding': [1., 2.]} for i in range(len(body['input']))]}
        elif 'audio/speech' in request.full_url: return io.BytesIO(b'fixture-audio')
        elif 'audio/transcriptions' in request.full_url: result = {'text': 'fixture transcript'}
        elif '/rerank' in request.full_url: result = {'results': [{'index': 0, 'relevance_score': .9}]}
        elif '/messages' in request.full_url: result = {'content': [{'type': 'text', 'text': 'backup answer'}], 'stop_reason': 'end_turn'}
        else: result = {'choices': [{'message': {'role': 'assistant', 'content': 'backup answer'}, 'finish_reason': 'stop'}]}
        return io.BytesIO(json.dumps(result).encode())


def controller(host, threshold=2):
    freeze.begin_failure_scope()
    return freeze.install(lambda key, default: {'freeze_enabled': True, 'freeze_fail_streak': threshold}.get(key, default), host)


async def run():
    wire = Wire()
    with tempfile.TemporaryDirectory() as directory, patch('urllib.request.urlopen', wire):
        app = FastAPI()
        def authorize(request: Request):
            if request.cookies.get('role_panel') != 'fixture-panel-key': raise HTTPException(401)
        host = Host(directory, app, authorize); system = RoleSystem(host)
        install_panel(app, host, system, authorize, 'fixture-panel-key')
        host.config['provider_sources'] = {
            'p': {'base_url': 'https://primary.invalid/v1', 'api_key': 'fixture-secret', 'enabled': True},
            'b': {'base_url': 'https://backup.invalid/v1', 'api_key': 'fixture-secret', 'protocol': 'anthropic', 'enabled': True}}
        host.config['model'] = {'source_id': 'p', 'model': 'primary-model', 'enabled': True}
        host.config['providers'] = {'backup': {'source_id': 'b', 'model': 'backup-model', 'enabled': True}}
        host.refresh_providers()
        async with ASGIClient(app) as client:
            assert (await client.post('/api/models/fallback', {'fallback_models': {'chat': 'backup'}})).status_code == 401
            await client.post('/api/login', {'key': 'fixture-panel-key'})
            assert (await client.post('/api/models/fallback', {'fallback_models': {'chat': 'role'}})).status_code == 422
            response = await client.post('/api/models/fallback', {'fallback_models': {'chat': 'backup'}, 'freeze_enabled': True})
            assert response.status_code == 200, response.text
            assert system.spectrum._cfg('freeze_enabled', False)
            assert (await client.post('/api/models/delete', {'id': 'backup'})).status_code == 409
            assert (await client.post('/api/config/system', {'chat_provider_id': 'backup'})).status_code == 422
            exported = (await client.get('/api/models/export')).json()
            assert exported['fallback_models'] == {'chat': 'backup'} and 'fixture-secret' not in json.dumps(exported)
            assert (await client.post('/api/models/import', exported)).status_code == 200
            # A connection test reports the selected model's failure without using the backup.
            wire.calls.clear(); result = (await client.post('/api/models/test', {'id': 'role'})).json()
            assert not result['ok'] and len(wire.calls) == 1
        state = controller(host)
        wire.calls.clear()
        with patch.object(limiter, 'acquire', new_callable=AsyncMock) as rate:
            response = await host.provider.text_chat(prompt='original input', model='temporary-primary-model')
            assert rate.await_count == 2
        assert response.completion_text == 'backup answer' and response.used_provider is host.providers['backup']
        assert len(wire.calls) == 2 and wire.calls[-1][1]['model'] == 'backup-model'
        assert wire.calls[-1][1]['messages'][-1]['content'][0]['text'] == 'original input'
        assert state._fail_streak == 0 and not freeze.is_frozen()
        history = [{'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'done', 'function': {'name': 'send_message_to_user', 'arguments': '{}'}}], '_native_protocol': 'gemini', '_native_content': [{'thoughtSignature': 'private-signature'}]}, {'role': 'tool', 'tool_call_id': 'done', 'content': 'delivered receipt'}]
        response = await host.provider.text_chat(prompt='', contexts=history, continue_tools=True, func_tool=host.tools)
        body = wire.calls[-1][1]
        assert 'tools' not in body and 'private-signature' not in json.dumps(body) and 'delivered receipt' in json.dumps(body)
        assert canonical_history(history) != history and history[0]['_native_content'][0]['thoughtSignature'] == 'private-signature'
        # The service pins a successful backup across a tool continuation and executes the tool once.
        calls = {'primary': 0, 'backup': 0, 'tool': 0}
        async def execute(wrapper): calls['tool'] += 1; return 'already delivered'
        host.tools.register(SimpleNamespace(name='fixture_tool', description='fixture', parameters={'type':'object','properties':{}}, active=True, call=execute))
        tool_call = {'id':'once', 'type':'function', 'function':{'name':'fixture_tool','arguments':'{}'}}
        backup = host.providers['backup']
        assert host.backup_for(backup) is None, '备用指向当前模型时等同于未设备用，不递归调用自身'
        async def primary_down(**kwargs): calls['primary'] += 1; raise ModelError('fixture outage')
        async def backup_tools(**kwargs):
            calls['backup'] += 1
            if calls['backup'] == 1:
                return ModelResponse('', tool_calls=[tool_call], native_message={'role':'assistant','content':None,'tool_calls':[tool_call]}, input_messages=[{'role':'user','content':'input'}], used_provider=backup, role_mode='contract')
            assert kwargs['continue_tools'] and 'already delivered' in json.dumps(kwargs['contexts'])
            return ModelResponse('finished', used_provider=backup, role_mode='contract')
        event = Event(host, 'qq-fixture:FriendMessage:1', '1', 'fixture', 'input')
        req = ModelRequest('input', 'system', [], host.tools, role_mode='deepseek')
        with patch.object(host.provider, '_text_chat', primary_down), patch.object(backup, '_text_chat', backup_tools):
            response = await system._generate(event, req)
        assert response.completion_text == 'finished' and calls == {'primary':1,'backup':2,'tool':1}
        assert not event.get_extra('_spectrum_request')['prefill'] and req.role_mode == 'contract'
        # If only the continuation fails, the backup sees receipts without native tool declarations.
        calls = {'primary': 0, 'backup': 0, 'tool': 0}
        async def primary_tools(**kwargs):
            calls['primary'] += 1
            if calls['primary'] == 1:
                return ModelResponse('', tool_calls=[tool_call], native_message={'role':'assistant','content':None,'tool_calls':[tool_call]}, input_messages=[{'role':'user','content':'input'}], used_provider=host.provider, role_mode='deepseek')
            raise ModelError('continuation outage')
        async def backup_finish(**kwargs):
            calls['backup'] += 1
            assert kwargs['func_tool'] is None and 'already delivered' in json.dumps(kwargs['contexts'])
            return ModelResponse('finished', used_provider=backup, role_mode='contract')
        event = Event(host, 'qq-fixture:FriendMessage:1', '1', 'fixture', 'input')
        req = ModelRequest('input', 'system', [], host.tools, role_mode='deepseek')
        with patch.object(host.provider, '_text_chat', primary_tools), patch.object(backup, '_text_chat', backup_finish):
            response = await system._generate(event, req)
        assert calls == {'primary':2,'backup':1,'tool':1} and not event.get_extra('_spectrum_request')['prefill']
        wire.fail_backup = True
        try: await host.provider.text_chat(prompt='failed turn')
        except ModelFallbackError as error:
            freeze.record_failure('prism:model', error)
        else: raise AssertionError('double failure hidden')
        assert state._fail_streak == 1 and not freeze.is_frozen()
        freeze.begin_failure_scope()
        try: await host.provider.text_chat(prompt='second failed turn')
        except ModelFallbackError: pass
        assert state._fail_streak == 2 and freeze.is_frozen()
        count = len(wire.calls)
        try: await host.provider.text_chat(prompt='paused')
        except freeze.FrozenSkipped: pass
        assert len(wire.calls) == count
        wire.fail_backup = False
        state = controller(host)
        primary = host.provider
        async def paused(**kwargs):
            state.manual('freeze'); state.manual('thaw')
            raise ModelError('finished after pause')
        with patch.object(primary, '_text_chat', paused):
            count = len(wire.calls)
            try: await primary.text_chat(prompt='pause boundary')
            except freeze.FrozenSkipped: pass
            assert len(wire.calls) == count and state._fail_streak == 0
        async def cancelled(**kwargs): raise asyncio.CancelledError()
        with patch.object(primary, '_text_chat', cancelled):
            try: await primary.text_chat(prompt='cancelled')
            except asyncio.CancelledError: pass
            assert state._fail_streak == 0
        # Typed operations preserve inputs and only use a backup of their own category.
        host.config['provider_sources']['b']['protocol'] = 'openai'
        for category in ('embedding', 'stt', 'tts', 'rerank'):
            host.config['providers']['p-' + category] = {'source_id': 'p', 'model': 'same-model', 'category': category}
            host.config['providers']['b-' + category] = {'source_id': 'b', 'model': 'same-model', 'category': category}
            host.config['fallback_models'][category] = 'b-' + category
        host.config['embedding_provider_id'] = 'p-embedding'; host.refresh_providers()
        state = controller(host)
        assert await host.providers['p-embedding'].get_embeddings(['a','b']) == [[1.,2.],[1.,2.]]
        assert await host.providers['p-tts'].text_to_speech('hello') == b'fixture-audio'
        assert await host.providers['p-stt'].speech_to_text(b'audio') == 'fixture transcript'
        assert (await host.providers['p-rerank'].rerank('query',['document']))[0]['index'] == 0
        assert state._fail_streak == 0
        host.config['providers']['b-embedding']['model'] = 'different-space'
        from role_system.model_fallback import validate_choices
        try: validate_choices(host.config, host.config['fallback_models'])
        except ValueError as error: assert '向量空间' in str(error)
        else: raise AssertionError('embedding spaces mixed')
        await system.close()
        freeze.reset_for_test()
    print('PASS: global/typed fallback, own model/protocol, tool history, one failure count, threshold freeze, cancellation/pause, config/export/delete protection, isolated tests')


if __name__ == '__main__': asyncio.run(run())
