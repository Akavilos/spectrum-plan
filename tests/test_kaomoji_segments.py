"""颜文字经过正文规则和实际发送分段后保持完整。"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from role_system.message_segments import split_message, message_batches
from role_system.output_rules import OutputRules, presets
from role_system.ports import MessageChain, Plain, NativeSegment

greeting = 'Ciallo～(∠・ω< )⌒☆'
with tempfile.TemporaryDirectory() as directory:
    rules = OutputRules(Path(directory))
    enabled = {'preset-2', 'preset-5', 'preset-comfort-catch', 'preset-comfort-emotion'}
    rules.save({'enabled': True, 'rules': [{**row, 'enabled': row['id'] in enabled} for row in presets()]})
    for greeting in (greeting, greeting.replace('～', '~')):
        assert rules.apply(greeting)[0] == greeting
        for maximum in (16, 32, 55):
            assert split_message(greeting, maximum) == [greeting]
            original = '原来嘴上说着不理我，心里还眼巴巴数着这个呀！好啦，' + greeting + '，这下心满意足了吧，快乖乖闭上眼睛睡觉！'
            batches = message_batches(MessageChain([Plain(rules.apply(original)[0])]), True, maximum)
            texts = [batch.get_plain_text() for batch in batches]
            assert ''.join(texts).replace('，','').replace(',','') == original.replace('，','').replace(',','')
            assert all(not text.endswith(('，',',')) for text in texts)
            assert sum(greeting in text for text in texts) == 1
    for face in ('(≧▽≦)', '(｡･ω･｡)', '（＾ω＾）'):
        assert any(face in part for part in split_message('先聊聊。' + '长文字'*12 + face + '，晚安。', 16))
    assert split_message('好啦～别急~我马上来。下一句！') == ['好啦～别急~我马上来。', '下一句！']
    assert split_message('第一句。第二句！第三句？') == ['第一句。', '第二句！', '第三句？']
    long = '没有标点的长文字'*25
    pieces = split_message(long, 32)
    assert len(pieces) > 1 and ''.join(pieces) == long and all(len(part) <= 32 for part in pieces)
    for atom in ('https://example.com/'+('path/'*15), '`hello~world!`', '```python\nprint("~!")\n```'):
        assert any(atom in part for part in split_message('前文。'+atom+'。后文！',16))
    chain = MessageChain([NativeSegment('image', {'file':'fixture'}), Plain('Ciallo～(∠・ω< )⌒☆')])
    assert isinstance(message_batches(chain, True, 16)[0].chain[0], NativeSegment)
    assert len(message_batches(chain, False, 16)) == 1
    def delivered(text, enabled=True, maximum=55):
        return [batch.get_plain_text() for batch in message_batches(MessageChain([Plain(text)]), enabled, maximum)]
    assert delivered('先坐一会，喝点水，') == ['先坐一会，喝点水']
    assert delivered('Sit down, have some water,') == ['Sit down, have some water']
    assert delivered('第一段，\n第二段,\n好吗？') == ['第一段','第二段','好吗？']
    assert delivered('你好，', False) == ['你好，']
    assert delivered('，\n,') == []
    url='https://example.com/a,b,'
    assert delivered(url, maximum=16) == [url]
    assert delivered('`a,b,`，') == ['`a,b,`']
    assert delivered('第一句。第二句！第三句？') == ['第一句。','第二句！','第三句？']
print('PASS segments: trailing commas, internal commas, kaomoji, code, URL, media, switch')
