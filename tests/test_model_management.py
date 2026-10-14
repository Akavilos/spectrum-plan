"""公开协议和管理流程的离线验收；所有 HTTP 请求用替身，不访问真实模型。"""
import asyncio
import copy
import io
import json
import os
import tempfile
import sys
import urllib.error
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI, Request, HTTPException
from role_system.host import Host, SendTool
from role_system.service import RoleSystem
from role_system.web import install_panel
from role_system.provider import Provider
from role_system.model_protocols import encode, decode, endpoint, read_stream
from role_system.model_http import ModelError
from role_system.data_exchange import export_data, validate_import, import_data
from test_http import ASGIClient


class Wire:
    def __init__(self): self.calls = []
    def __call__(self, request, **kwargs):
        body = json.loads(request.data) if request.data and request.get_header('Content-type', '').startswith('application/json') else None
        def check_enum_types(schema):
            if isinstance(schema, dict):
                if 'enum' in schema: assert schema.get('type'), 'enum parameter missing schema type'
                for value in schema.values(): check_enum_types(value)
            elif isinstance(schema, list):
                for value in schema: check_enum_types(value)
        if body: check_enum_types(body.get('tools', []))
        self.calls.append((request.full_url, dict(request.header_items()), body))
        url = request.full_url
        if '/models' in url and request.data is None:
            if 'claude' in url:
                data = {'data': [{'id': 'claude-two' if 'after_id' in url else 'claude-one'}], 'has_more': 'after_id' not in url, 'last_id': 'claude-one'}
            else: data = {'data': [{'id': 'fixture-chat'}, {'id': 'fixture-embedding'}]}
        elif '/embeddings' in url: data = {'data': [{'index': i, 'embedding': [1., 2., 3.]} for i in range(len(body['input']))]}
        elif '/messages' in url: data = {'content': [{'type': 'text', 'text': 'OK'}], 'usage': {'input_tokens': 4, 'output_tokens': 1}, 'stop_reason': 'end_turn'}
        elif ':generateContent' in url: data = {'candidates': [{'content': {'parts': [{'text': 'OK'}]}, 'finishReason': 'STOP'}]}
        elif '/responses' in url: data = {'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'OK'}]}], 'status': 'completed'}
        elif '/audio/speech' in url: return io.BytesIO(b'fixture-audio')
        elif '/audio/transcriptions' in url: data = {'text': '你好'}
        elif '/rerank' in url: data = {'results': [{'index': 0, 'relevance_score': .9}]}
        elif 'bad' in url:
            raise urllib.error.HTTPError(url, 400, 'Bad request', {}, io.BytesIO(b'{"error":{"message":"unsupported temperature; key fixture-secret"}}'))
        else: data = {'choices': [{'message': {'role': 'assistant', 'content': 'OK'}, 'finish_reason': 'stop'}]}
        return io.BytesIO(json.dumps(data).encode())


def protocol_roundtrips():
    base = {'model': 'fixture', 'messages': [{'role': 'system', 'content': '我按当前环境回应。'}, {'role': 'user', 'content': '你好'}]}
    fixtures = {
        'anthropic': {'content': [{'type': 'thinking', 'thinking': 'hidden', 'signature': 'signature-opaque'}, {'type': 'tool_use', 'id': 'c1', 'name': 'lookup', 'input': {'x': 1}}]},
        'gemini': {'candidates': [{'content': {'parts': [{'functionCall': {'name': 'lookup', 'args': {'x': 1}}, 'thoughtSignature': 'signature-opaque'}]}}]},
        'responses': {'output': [{'type': 'reasoning', 'id': 'reasoning-opaque', 'encrypted_content': 'opaque'}, {'type': 'function_call', 'call_id': 'c1', 'name': 'lookup', 'arguments': '{"x":1}'}]},
    }
    for protocol, fixture in fixtures.items():
        message, finish, _ = decode(protocol, fixture)
        assert finish == 'tool_calls'
        original = copy.deepcopy(message)
        call = message['tool_calls'][0]
        body = encode(protocol, {**base, 'messages': base['messages'] + [message, {'role': 'tool', 'tool_call_id': call['id'], 'content': 'result'}]}, {}, {})
        assert message == original, 'native content mutated during encoding'
        assert 'opaque' in json.dumps(body), 'native signatures/reasoning lost'
        assert 'result' in json.dumps(body)
    assert endpoint('https://relay.invalid', 'openai') == 'https://relay.invalid/chat/completions'
    assert endpoint('https://relay.invalid/custom/v1/chat/completions', 'openai') == 'https://relay.invalid/custom/v1/chat/completions'
    assert endpoint('https://azure.invalid/openai/deployments/old', 'azure', 'new').split('?')[0] == 'https://azure.invalid/openai/deployments/new/chat/completions'
    assert encode('anthropic', base, {}, {'max_tokens': None})['max_tokens'] == 4096
    assert 'stream' not in encode('gemini', base, {'stream': True}, {})
    stream = io.BytesIO(b'data: {"choices":[{"delta":{"content":"O"}}]}\n\ndata: {"choices":[{"delta":{"content":"K"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n')
    assert decode('openai', read_stream(stream, 'openai'))[0]['content'] == 'OK'
    anthropic = io.BytesIO(b'data: {"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":""}}\ndata: {"type":"content_block_delta","index":0,"delta":{"thinking":"hidden"}}\ndata: {"type":"content_block_delta","index":0,"delta":{"signature":"opaque"}}\ndata: {"type":"content_block_start","index":1,"content_block":{"type":"text","text":""}}\ndata: {"type":"content_block_delta","index":1,"delta":{"text":"OK"}}\n')
    message = decode('anthropic', read_stream(anthropic, 'anthropic'))[0]
    assert message['content'] == 'OK' and message['_native_content'][0]['signature'] == 'opaque'
    from role_system.model_catalog import migrate
    cfg = {'model': {}, 'providers': {}, 'embedding': {'enabled': True, 'base_url': 'http://fixture.invalid/v1', 'model': 'embed', 'api_key': 'embed-secret'}}
    migrate(cfg); assert cfg['embedding_provider_id'] == 'legacy-embedding' and not cfg['embedding']['enabled']
    del cfg['providers']['legacy-embedding']; assert not migrate(cfg), 'deleted embedding restored during migration'
    cfg['providers']['flat'] = {'base_url': 'http://fixture.invalid', 'model': 'embed', 'category': 'embedding', 'enabled': False, 'name': 'paused'}
    migrate(cfg); assert cfg['providers']['flat']['category'] == 'embedding' and not cfg['providers']['flat']['enabled'] and cfg['providers']['flat']['name'] == 'paused'
    from role_system.runtime_logs import config_credentials, redact_text
    assert 'fixture-secret' not in redact_text('fixture-secret header-secret', config_credentials({'provider_sources': {'x': {'api_keys': ['fixture-secret'], 'custom_headers': {'x-api-key': 'header-secret'}}}}))


async def run():
    protocol_roundtrips()
    wire = Wire()
    with tempfile.TemporaryDirectory() as directory, patch('urllib.request.urlopen', wire):
        # Upgrade a populated instance; preserve credentials, provider IDs and selected chat.
        Path(directory, 'system.json').write_text(json.dumps({'model': {'base_url': 'https://ark.invalid/v3', 'api_key': 'fixture-secret', 'model': 'old-role'}, 'providers': {'gemini': {'base_url': 'https://relay.invalid', 'api_key': 'relay-secret', 'model': 'gemini-proxy'}}, 'chat_provider_id': 'gemini'}))
        app = FastAPI()
        def authorize(request: Request):
            if request.cookies.get('role_panel') != 'fixture-panel-key': raise HTTPException(401)
        host = Host(directory, app, authorize); system = RoleSystem(host)
        install_panel(app, host, system, authorize, 'fixture-panel-key')
        assert host.provider_for().config['protocol'] == 'openai'
        assert host.provider_for().config['api_key'] == 'relay-secret'
        async with ASGIClient(app) as client:
            assert (await client.get('/api/model-management')).status_code == 401
            await client.post('/api/login', {'key': 'fixture-panel-key'})
            view = (await client.get('/api/model-management')).json()
            assert 'secret' not in json.dumps(view) and len(wire.calls) == 0
            assert (await client.post('/api/providers/gemini', {'api_key': 'changed-relay-secret'})).status_code == 200
            assert host.provider_for('gemini').config['api_key'] == 'changed-relay-secret'
            for id, protocol in [('ark', 'openai'), ('claude', 'anthropic'), ('google', 'gemini'), ('responses', 'responses'), ('bad', 'openai')]:
                s = {'base_url': f'https://{id}.invalid/v1', 'protocol': protocol, 'api_keys': ['fixture-secret'], 'custom_headers': {'x-auth-token': 'header-secret'}, 'enabled': True}
                assert (await client.post('/api/model-sources/save', {'id': id, 'source': s})).status_code == 200
                result = await client.post('/api/models/save', {'id': id + '/test', 'model': {'source_id': id, 'model': 'fixture', 'temperature': None, 'enabled': True}})
                assert result.status_code == 200, result.text
            result = (await client.post('/api/model-sources/list', {'id': 'claude'})).json()
            assert result['ok'] and len(result['models']) == 2
            for id in ['ark', 'claude', 'google', 'responses']:
                result = (await client.post('/api/models/test', {'id': id + '/test'})).json()
                assert result['ok'] and result['preview'] == 'OK' and 'latency_ms' in result, result
                assert 'temperature' not in wire.calls[-1][2]
            error = (await client.post('/api/models/test', {'id': 'bad/test'})).json()
            assert not error['ok'] and error['status'] == 400 and 'unsupported temperature' in error['message'] and 'fixture-secret' not in json.dumps(error)
            assert (await client.post('/api/models/default', {'id': 'ark/test'})).status_code == 200
            assert (await client.post('/api/models/delete', {'id': 'ark/test'})).status_code == 409
            assert (await client.post('/api/model-sources/delete', {'id': 'ark'})).status_code == 409
            saved_keys = host.config['provider_sources']['ark']['api_keys'][:]
            await client.post('/api/model-sources/save', {'id': 'ark', 'source': {'api_keys': [], 'enabled': False}})
            assert host.config['provider_sources']['ark']['api_keys'] == saved_keys and host.provider_for() is None
            assert host.providers['ark/test'].unavailable_reason == '提供商连接已关闭'
            rows = (await client.get('/api/model-management')).json()['models']
            closed = next(m for m in rows if m['id'] == 'ark/test')
            assert not closed['ready'] and closed['unavailable_reason'] == '提供商连接已关闭'
            calls = len(wire.calls)
            await client.post('/api/models/default', {'id': 'gemini'})
            blocked = await client.post('/api/config/system', {'chat_provider_id': 'ark/test'})
            assert blocked.status_code == 422 and '提供商连接已关闭' in blocked.text
            assert host.config['chat_provider_id'] == 'gemini' and len(wire.calls) == calls
            blocked = await client.post('/api/models/default', {'id': 'ark/test'})
            assert blocked.status_code == 422 and '提供商连接已关闭' in blocked.text
            await client.post('/api/model-sources/save', {'id': 'ark', 'source': {'enabled': True}})
            await client.post('/api/models/default', {'id': 'ark/test'})
            assert host.provider_for().config['model'] == 'fixture'
            assert host.provider_for().unavailable_reason == ''
            from types import SimpleNamespace
            tool = SendTool(host)
            assert tool.parameters['properties']['messages']['items']['properties']['type']['type'] == 'string'
            assert len(tool.parameters['properties']['messages']['items']['properties']['type']['enum']) == 9
            for id in ('ark', 'google', 'claude', 'responses'):
                await host.providers[id + '/test']._text_chat(prompt='fixture', func_tool=SimpleNamespace(tools=[tool]))
                assert wire.calls[-1][2]['tools'], 'send tool removed instead of repaired'
            for category in ['embedding', 'stt', 'tts', 'rerank']:
                assert (await client.post('/api/models/save', {'id': 'ark/' + category, 'model': {'source_id': 'ark', 'model': 'fixture', 'category': category}})).status_code == 200
                result = (await client.post('/api/models/test', {'id': 'ark/' + category, **({'audio': 'aGVsbG8='} if category == 'stt' else {})})).json()
                assert result['ok'], result
            assert (await client.post('/api/models/default', {'id': 'ark/embedding', 'category': 'embedding'})).status_code == 200
            assert host.get_all_embedding_providers()[0].config['model'] == 'fixture'
            assert (await client.post('/api/config/prism', {'basic': {'provider_id': 'ark/embedding'}})).status_code == 422
            assert (await client.post('/api/models/default', {'id': '', 'category': 'embedding'})).status_code == 200
            assert not host.get_all_embedding_providers()
            exported = (await client.get('/api/models/export')).json()
            assert 'secret' not in json.dumps(exported)
            assert (await client.post('/api/models/import', exported)).status_code == 200
            assert host.provider_for().config['api_keys'] == saved_keys
            config_view = (await client.get('/api/config')).json()
            assert 'secret' not in json.dumps(config_view)
            package = export_data(host, system, 'settings')
            import_data(host, system, validate_import(host, system, package))
            assert host.provider_for().config['api_keys'] == saved_keys
            assert (await client.post('/api/model-sources/save', {'id': 'ark', 'source': {'custom_headers': {'test': 'bad\nheader'}}})).status_code == 422
            # Reading manager repeatedly does not reset round-robin provider instances or trigger requests.
            selected = host.provider_for(); count = len(wire.calls)
            await client.get('/api/model-management'); assert host.provider_for() is selected and len(wire.calls) == count
            with patch.dict(os.environ, {'FIXTURE_KEY': 'env-secret'}):
                p = Provider({'base_url': 'https://env.invalid', 'api_keys': ['$FIXTURE_KEY', 'second-secret'], 'model': 'test'})
                await p._text_chat(prompt='test'); await p._text_chat(prompt='test')
                assert wire.calls[-2][1]['Authorization'] == 'Bearer env-secret' and wire.calls[-1][1]['Authorization'] == 'Bearer second-secret'
        await system.close()
    print('PASS: source/model workflows, 4 chat protocols, list pagination, tool signatures, 400 details and redaction, migration/selectors, embedding/voice/rerank, secret-preserving import, key rotation, no automatic model calls')


if __name__ == '__main__': asyncio.run(run())
