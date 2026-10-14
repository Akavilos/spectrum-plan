"""社区口癖样本：裁剪结果、动作与引用保留、实际承诺、酒馆往返。"""
import json
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from role_system.output_rules import OutputRules, normalize_rule, presets
from role_system.spectrum_module.spectrum.parse import Decision


def run():
    with tempfile.TemporaryDirectory() as directory:
        output = OutputRules(Path(directory))
        rows = presets()
        for row in rows:
            row['enabled'] = row.get('recommended', False) or row['id'] in ('preset-0', 'preset-1', 'preset-2')
        output.save({'enabled': True, 'rules': rows})
        cases = {
            '我接住了。先吃饭吧。': '先吃饭吧。',
            '我稳稳得接住你。慢慢说。': '慢慢说。',
            '我会稳稳地接住你，慢慢说。': '慢慢说。',
            '我稳稳的接住你。': '',
            '我就在这里，不躲、不藏、不逃、不绕，我会稳稳地接住你。先喝口水。': '先喝口水。',
            '你的委屈，我接住了。今天很累吧。': '今天很累吧。',
            '我会把你的情绪稳稳接住。要吃什么？': '要吃什么？',
            '我会稳稳接住你的所有情绪。先吃点东西。': '先吃点东西。',
            '总的来说，我接住了。明天九点给你打电话。': '明天九点给你打电话。',
            '吃过了，你呢？': '吃过了，你呢？',
            '你跳下来。我稳稳地接住你。': '你跳下来。我稳稳地接住你。',
            '你滑倒了。\n我接住了。': '你滑倒了。\n我接住了。',
            '我稳稳地接住你扔来的杯子。': '我稳稳地接住你扔来的杯子。',
            '我接住了球。': '我接住了球。',
            '我接住了。杯子没碎。': '我接住了。杯子没碎。',
            '他说：“我稳稳地接住你。”': '他说：“我稳稳地接住你。”',
            '“\n我接住了。\n”这句太模板了。': '“\n我接住了。\n”这句太模板了。',
            '别再说“我接住了”。': '别再说“我接住了”。',
            '不是明天，而是后天。': '不是明天，而是后天。',
            '我在家，今晚不出门。': '我在家，今晚不出门。',
            '这就够了。': '这就够了。',
        }
        for original, expected in cases.items():
            actual, hits = output.apply(original)
            assert actual == expected, (original, actual, expected)
            assert not any('error' in hit for hit in hits), hits
        exported = output.tavern_export()
        output.save({'enabled': True, 'rules': [normalize_rule(row) for row in exported]})
        for original, expected in cases.items():
            assert output.apply(original)[0] == expected, original
        # 把整个目录写出供同一轮验收检查 JavaScript 实际执行结果。
        if len(sys.argv) > 1:
            Path(sys.argv[1]).write_text(json.dumps({'rules': exported, 'cases': cases}, ensure_ascii=False), encoding='utf-8')
        for row in rows: row['enabled'] = True
        output.save({'enabled': True, 'rules': rows})
        optional = {
            '她语气很平，像在说今天的天气。': '她语气很平。',
            '今天的天气很晴朗。': '今天的天气很晴朗。',
            '我极其极其喜欢这个。': '我喜欢这个。',
            '先说结论，已经完成了。': '已经完成了。',
            '你问到了问题的核心。明天再试。': '明天再试。',
            '这次我懂了，我真的懂了。先吃饭。': '先吃饭。',
            '我就在这里，不躲、不藏、不逃。先吃饭。': '先吃饭。',
            '先吃饭。那就够了。': '先吃饭。',
            '先吃饭。随时告诉我。': '先吃饭。',
        }
        for original, expected in optional.items():
            assert output.apply(original)[0] == expected, (original, output.apply(original))
        decision = Decision(ok=True, mode='immediate', content='我稳稳地接住你。明天九点给你打电话。')
        decision.follow_up = {'summary': '明天九点给你打电话。'}
        output.prepare_decision(decision)
        assert decision.follow_up and decision.content == '明天九点给你打电话。'
        empty = Decision(ok=True, mode='immediate', content='我接住了。')
        empty.follow_up = {'summary': '我接住了。'}
        output.prepare_decision(empty)
        assert empty.mode == 'silent' and empty.follow_up is None
        print(f'PASS community regex: {len(rows)} presets, {len(cases) + len(optional)} samples, promises and Tavern round-trip')


if __name__ == '__main__': run()
