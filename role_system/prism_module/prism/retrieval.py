"""按人物、话题和来源选择主观经历；检索只读取已保存内容。"""
from datetime import datetime
from functools import lru_cache
import re
import hashlib
import json

from .decay import Generalization, is_generalizable
from .visibility import visibility_of_scene, can_speak

STOP_WORDS = set('今天 昨天 明天 现在 刚刚 这个 那个 什么 怎么 可以 不是 就是 一下 一个 时候 觉得 知道 记得 之前 以后 你说 我说 我们 你们 他们 还有 然后 已经 这样 那样 有没有 是不是 为什么 还记 刚才'.split())
STOP_WORDS.update(('家常', '闲聊', '聊天', '日常'))


@lru_cache(maxsize=1024)
def terms(text):
    result = set()
    for run in re.findall(r'[\u4e00-\u9fff]+|[A-Za-z0-9_]+', (text or '').casefold()):
        if run.isascii():
            if len(run) >= 2:
                result.add(run)
        else:
            result.update(run[i:i+size] for size in (2, 3, 4)
                          for i in range(len(run)-size+1)
                          if not any(stop in run[i:i+size] for stop in STOP_WORDS))
    return frozenset(result)


def relevance(query, text, topic=''):
    query_terms = terms(query)
    if not query_terms:
        return 0.0
    matched = query_terms & terms(text)
    topic_match = query_terms & terms(topic)
    return min(1.0, len(matched) / max(3, min(12, len(query_terms)))) + (0.6 if topic_match else 0.0)


def scene_text(scene):
    return '\n'.join([scene.narrative, scene.meaning, scene.topic] +
                     [m.text for m in scene.moments] +
                     [part.get('topic', '') for part in scene.segments])


def query_seeds(scenes, query):
    ranked = sorted(((relevance(query, scene_text(sc), sc.topic), key)
                     for key, sc in scenes.items()), reverse=True)
    return {key: min(1.0, score) for score, key in ranked[:6] if score > 0}


def scene_identity(scene):
    names = {}
    for m in scene.moments:
        if not m.is_hers and m.speaker:
            names[m.speaker] = m.name or m.speaker
    return '、'.join(f'{name}（{pid}）' if name != pid else pid for pid, name in names.items())


def stamp(ts):
    # 时区由进程 TZ 设置；与记录面板采用相同显示方式。
    return datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M')


def familiar_experiences(memory, person='', query='', viewpoint=None):
    """从仍可召回的平淡经历派生熟悉话题，来源删除后归纳自然消失。"""
    groups = {}
    for key, sc in memory.scenes.items():
        tr = memory.traces.get(key)
        if not sc.topic or (tr and tr.forgotten()):
            continue
        if viewpoint and not can_speak(visibility_of_scene(sc), viewpoint)[0]:
            continue
        if person and person not in sc.present:
            continue
        if query and relevance(query, scene_text(sc), sc.topic) <= 0:
            continue
        # 人物组合保持一致，群聊多人话题不归给其中某一个人。
        groups.setdefault((sc.topic, tuple(sorted(sc.present))), []).append(sc)
    result = []
    for (topic, people), scenes in groups.items():
        if not is_generalizable(scenes):
            continue
        g = Generalization(theme=topic, scene_keys=[f'scene:{s.scene_id}' for s in scenes],
                           count=len(scenes), first_at=min(s.start for s in scenes),
                           last_at=max(s.end for s in scenes))
        identity=hashlib.sha256(json.dumps([topic,people,sorted(g.scene_keys)],ensure_ascii=False).encode()).hexdigest()
        if identity in memory.suppressed_generalizations:
            continue
        result.append({'id':identity,'topic': g.theme, 'people': list(people), 'sources': g.scene_keys,
                       'count': g.count, 'first_at': g.first_at, 'last_at': g.last_at})
    return sorted(result, key=lambda x: -x['last_at'])


def recent_messages(memory, *, umo, query, people=(), exclude_scenes=(), exclude_texts=(), limit=2):
    """其他会话仅提供相关且有来源的原话，避免重复当前历史和已选回忆。"""
    candidates = []
    for key, sc in memory.scenes.items():
        if key in exclude_scenes:
            continue
        tr = memory.traces.get(key)
        if tr and tr.forgotten():
            continue
        for m in sc.moments:
            if m.is_hers or not m.speaker or not m.text.strip() or m.umo == umo:
                continue
            if any(m.text.strip() in t for t in exclude_texts if t):
                continue
            score = relevance(query, m.text, m.topic)
            if m.speaker in people:
                score += 0.5
            if score > 0:
                candidates.append((score, m.ts, m))
    out, seen = [], set()
    for score, ts, m in sorted(candidates, key=lambda x: (-x[0], -x[1])):
        identity = (m.umo, m.speaker, m.text.strip())
        if identity in seen:
            continue
        seen.add(identity)
        private = '私下来信' if 'FriendMessage' in m.umo else '群聊发言'
        out.append(f'我在 {stamp(ts)} 收到{m.name or m.speaker}（{m.speaker}）的{private}，'
                   f'来源 {m.umo}，原文摘录：{m.text[:180]}')
        if len(out) == limit:
            break
    return '\n'.join(out)
