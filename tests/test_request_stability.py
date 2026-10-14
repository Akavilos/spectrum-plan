"""验证缓存前缀、工具续请求和回执接线；不连接模型或 QQ。"""
import asyncio
import ast
import io
import json
import logging
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from role_system.host import Host
from role_system.ports import Event, ModelRequest, Plain, MessageChain, TextPart
from role_system.service import RoleSystem
from role_system.spectrum_module.spectrum import settings, freeze
from role_system.spectrum_module.spectrum.delivery import track_reply
from role_system.spectrum_module.spectrum.parse import Decision
from role_system.spectrum_module.spectrum.prompt_flow import exchange_context, system_context
from role_system.runtime_logs import RuntimeLogs
from run import run as capture_console


class Wire:
    def __init__(self): self.calls = []
    def __call__(self, request, **kwargs):
        body = json.loads(request.data)
        self.calls.append(body)
        if len(self.calls) == 1:
            message = {'role': 'assistant', 'content': None, 'reasoning_content': 'native fixture',
                'tool_calls': [{'id': 'call-1', 'type': 'function', 'function': {'name': 'fixture_tool', 'arguments': '{}'}}]}
        else:
            message = {'role': 'assistant', 'content': 'fixture reply'}
        return io.BytesIO(json.dumps({'choices': [{'message': message}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 20, 'prompt_cache_hit_tokens': 80, 'prompt_cache_miss_tokens': 20}}).encode())


class Tool:
    name, description, parameters, active = 'fixture_tool', 'fixture', {'type': 'object'}, True
    async def call(self, context, **kwargs): return 'fixture result'


async def run():
    logging.basicConfig(level=logging.WARNING)
    with tempfile.TemporaryDirectory() as directory:
        host = Host(directory)
        host.config['persona'] = 'fixture persona'
        host.config['model'].update(base_url='https://fixture.invalid/v1', model='gemini-fixture', family='gemini', api_key='fixture')
        system = RoleSystem(host)
        cfg = system.configs['spectrum']
        cfg[settings.FIELD_GROUP['possession_enabled']].update(possession_enabled=True, prefill_gemini_closed=True)
        await system.start()
        umo = 'qq-fixture:FriendMessage:1'
        for index in range(30): host.append_history(umo, 'user', str(index))
        old = host.history(umo)
        for index in range(30, 39):
            host.append_history(umo, 'user', str(index))
            assert host.history(umo)[:len(old)] == old
        assert len(host.history(umo)) == 39
        host.append_history(umo, 'user', '39')
        assert len(host.history(umo)) == 30 and host.history(umo)[0]['content'] == '10'
        for index in range(40, 44):
            before = host.history(umo)
            host.append_history(umo, 'user', str(index))
            assert host.history(umo)[:len(before)] == before
        assert host.history('qq-fixture:FriendMessage:2') == []

        rows = [{'id': 3, 'kind': 'received', 'text': '重复来信', 'meta': {'sender': '甲', 'sender_id': '1'}},
                {'id': 2, 'kind': 'delivered', 'text': '之前回复', 'meta': {}},
                {'id': 1, 'kind': 'received', 'text': '重复来信', 'meta': {'sender': '乙', 'sender_id': '2'}}]
        messages = exchange_context(rows, '重复来信', lambda value: value, is_group=True)
        assert len(messages) == 2 and messages[0]['content'] == '[来信者：乙 · 2]\n重复来信'
        assert messages[1] == {'role': 'assistant', 'content': '之前回复'}

        from role_system.spectrum_module.spectrum.contract import build_contract
        contract = build_contract(True, prefill=False, life_style=str(system.spectrum._cfg('life_style', '') or ''))
        system.spectrum.l2.set_sys_core(umo, 'legacy core without separator')
        assert await system.spectrum.l2.current_sys_core(umo) == system_context(host.persona_prompt(), contract)
        host.tools.register(Tool())
        event = Event(host, umo, '1', '甲', 'fixture question')
        event.set_extra('_spectrum_freeze_epoch', freeze.checkpoint())
        req = ModelRequest('fixture question', system_context(host.persona_prompt(), contract),
                           [{'role': 'user', 'content': 'past'}], host.tools,
                           [TextPart('temporary scene').mark_as_temp()], 'gemini')
        wire = Wire()
        with patch('urllib.request.urlopen', wire): response = await system._generate(event, req)
        assert len(wire.calls) == 2
        first, continuation = (call['messages'] for call in wire.calls)
        assert continuation[:len(first)] == first, 'tool continuation changed the original request'
        assert wire.calls[1]['tools'] == wire.calls[0]['tools']
        assert wire.calls[1]['tool_choice'] == 'none'
        assert continuation[-2]['reasoning_content'] == 'native fixture'
        assert continuation[-1] == {'role': 'tool', 'tool_call_id': 'call-1', 'content': 'fixture result'}
        assert sum('temporary scene' in str(message['content']) for message in continuation) == 1
        assert response.usage['prompt_cache_hit_tokens'] == 80
        assert all('temporary scene' not in message['content'] for message in host.history(umo))

        sent = []
        async def transport(session, chain): sent.append(chain.get_plain_text()); return {'message_id': len(sent)}
        host.transport = transport
        decision = Decision(ok=True, content='收到', mode='immediate')
        receipt = track_reply(event, decision, {}, system.spectrum._plain_text)
        assert 'send' not in event.__dict__
        await event.send(MessageChain([Plain('收到')]))
        assert receipt.complete and receipt.text == '收到'
        freeze.manual('freeze')
        try:
            await event.send(MessageChain([Plain('不应发送')]))
            raise AssertionError('freeze did not stop sending')
        except freeze.FrozenSkipped: pass
        assert len(sent) == 1 and receipt.failed
        freeze.manual('thaw')
        host.config['model']['api_key'] = 'console-test-secret'
        host.config.save_config()
        await system.close()

        logs = RuntimeLogs(Path(directory) / 'logs', lambda: [])
        stdout, stderr = sys.stdout, sys.stderr
        from loguru import logger as platform
        logs.bind_platform(platform)
        sink = logs.platform_sink
        logs.bind_platform(platform)
        assert logs.platform_sink == sink
        logs.start(); logs.start()
        assert sys.stdout is stdout and sys.stderr is stderr
        platform.info('unique-platform-fixture')
        assert len(logs.read(query='unique-platform-fixture')) == 1
        with patch.dict('os.environ', {'ROLE_DATA_DIR': directory}):
            result = capture_console([sys.executable, '-c', "import sys; print('console-start'); print('console-test-secret'); print('console-error', file=sys.stderr); sys.exit(7)"])
        assert result == 7 and sys.stdout is stdout and sys.stderr is stderr
        console = logs.read(source='console')
        assert any(row['message'] == 'console-start' for row in console)
        assert any(row['message'] == 'console-error' and row['level'] == 'ERROR' for row in console)
        assert 'console-test-secret' not in json.dumps(console)
        assert len(logs.read(source='all')) == len(logs.read()) + len(console)
        logs.close()

    root = Path(__file__).resolve().parents[1] / 'role_system'
    for source in root.rglob('*.py'):
        tree = ast.parse(source.read_text(encoding='utf-8-sig'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    assert not (isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name)
                                and (target.value.id, target.attr) in {('event', 'send'), ('sys', 'stdout'), ('sys', 'stderr')}), str(source)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in ('eval', 'exec'), str(source)
    print('PASS: stable history, identities, system prefix, tool continuation, usage, receipts, freeze, logger lifecycle, process output capture/redaction/exit status and no runtime method/stream replacements')


if __name__ == '__main__': asyncio.run(run())
