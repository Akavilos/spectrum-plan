"""以明确原话更新主观理解；原始消息保留，版本和来源随经历保存。"""
import time

from .understanding import Understanding, correct, is_correction
from .decay import Revision, reconsolidate
from .retrieval import relevance, scene_text


def correction_targets(plugin, umo, person):
    targets = []
    scenes = list(plugin.memory.scenes.values())
    opened = plugin.scene_book.open.get(umo)
    if opened:
        scenes.append(opened)
    scenes.sort(key=lambda s: s.moments[-1].ts if s.moments else 0, reverse=True)
    for sc in scenes:
        for m in reversed(sc.moments):
            if m.umo != umo or m.speaker != person or not m.text:
                continue
            if m.understanding and not m.understanding.is_open():
                continue
            targets.append((sc, m))
            if len(targets) == 8:
                return targets
    return targets


def target_prompt(targets):
    return [{'id': str(i), 'quote': m.text[:1200],
             'reading': m.understanding.read_as if m.understanding else m.text[:200],
             'speaker': '我' if m.is_hers else '当前发言人'}
            for i, (_, m) in enumerate(targets)]


def apply_correction(plugin, read, text, targets, *, now=None):
    """目标编号与两端原话同时匹配才记纠正，不从质疑或情绪推断事实。"""
    now = now or time.time()
    changes = []
    if not is_correction(text) or read.event_confidence < 0.6:
        return changes
    for item in read.corrections:
        if not isinstance(item, dict):
            continue
        target = str(item.get('target', ''))
        quote = item.get('previous_quote', '')
        evidence = item.get('evidence', '')
        if not target.isdigit() or int(target) >= len(targets):
            continue
        if not isinstance(quote, str) or not isinstance(evidence, str):
            continue
        sc, m = targets[int(target)]
        if len(quote.strip()) < 4 or len(evidence.strip()) < 4 or quote not in m.text or evidence not in text:
            continue
        if evidence.strip() == quote.strip():
            continue
        u = m.understanding or Understanding(utter=m.text, read_as=m.text[:200])
        if not u.is_open():
            continue
        correct(u, evidence)
        m.understanding = u
        reason = f'收到明确纠正；原话：{quote}；新来信：{evidence}'
        key = f'scene:{sc.scene_id}'
        revision = Revision(key=key, before=u.read_as, after=evidence, reason=reason, at=now)
        sc.revisions.append(revision)
        trace = plugin.memory.traces.get(key)
        if trace and plugin._cfg('reconsolidation_enabled', True):
            # 明确重访产生再巩固窗口；不把背景读取计为排练。
            trace.last_recalled = now
            base = sc.narrative or u.read_as
            new_reading = f'{base}\n我后来收到的纠正原话是：{evidence}'
            reconsolidate(trace, sc, new_reading, reason, now)
            plugin.store.save_trace(trace)
        plugin.store.save_scene(sc.seal() if hasattr(sc, 'seal') else sc)
        changes.append(key)
    if changes:
        plugin.graph.rebuild(plugin.memory.scenes.values())
        plugin.store.save_graph(plugin.graph)
    return changes


def rehearse(plugin, *, umo, person, query, now=None):
    """明确重访同一人的相关经历才加固，单条每天至多一次。"""
    if not plugin._cfg('rehearsal_enabled', False):
        return []
    if not any(cue in query for cue in ('还记得', '上次', '那天', '以前', '又聊', '记不记得')):
        return []
    now = now or time.time()
    ranked = []
    for key, sc in plugin.memory.scenes.items():
        tr = plugin.memory.traces.get(key)
        if not tr or tr.forgotten(now) or person not in sc.present or now-tr.last_recalled < 86400:
            continue
        score = relevance(query, scene_text(sc), sc.topic)
        if score >= 0.5:
            ranked.append((score, key))
    keys = [key for _, key in sorted(ranked, reverse=True)[:2]]
    for key in keys:
        tr = plugin.memory.traces[key]
        tr.recall(now)
        plugin.store.save_trace(tr)
    return keys
