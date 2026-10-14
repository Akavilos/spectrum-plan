"""只选当前相关认识；人物、机器人账号及私密来源先于相关性判断。"""
from ..spectrum_module.spectrum.person import unambiguous_aliases
from ..spectrum_module.spectrum.recall import recall_keys, score_item
from .learning import source_record, source_rows, valid_sources

COMMON_KEYS = {'我们', '你们', '他们', '她们', '自己', '对方', '这个', '那个', '什么', '怎么',
               '觉得', '时候', '今天', '昨天', '明天', '事情', '的话', '可以', '可能', '现在',
               '一直', '一起', '没有', '知道', '这样', '那样', '还是', '就是', '真的', '一下'}


def topic_score(query, topics):
    """具体短词可匹配复合主题词；普通代词和时间词不单独触发认识。"""
    keys = [key for key in recall_keys(query, cap=128) if key not in COMMON_KEYS]
    return sum(topic.casefold() in query.casefold() or score_item(keys, topic) > 0
               for topic in topics if topic not in COMMON_KEYS)


def select(store, source_store, role_id, umo, person_id, query, limit):
    account = umo.split(':', 1)[0]
    with source_store._lock:
        aliases = [(row['alias'], row['person_id']) for row in source_store._db.execute('SELECT alias,person_id FROM person_aliases')]
    relevant_people = {person_id} if person_id else set()
    for alias, pid in unambiguous_aliases(aliases, account):
        if alias and alias in query: relevant_people.add(pid)
    ranked = []
    for item in store.list_insights(role_id, limit=1000):
        if item['account'] != account or not (umo in item['visibility'] or 'public' in item['visibility']) or item['status'] not in ('tentative', 'formed'):
            continue
        if item['person_id'] and item['person_id'] not in relevant_people: continue
        related = topic_score(query, item['topics'])
        if not related: continue
        rows = source_rows(source_store, item['supports'])
        if not valid_sources(source_store, item) or any(row['kind'] != 'received' or row['umo'] != item['origin'] for row in rows):
            continue
        if item['person_id'] and any(source_record(row)['person_id'] != item['person_id'] for row in rows): continue
        ranked.append((related, item['status'] == 'formed', item['ts'], item))
    ranked.sort(key=lambda value: value[:3], reverse=True)
    return [item[-1] for item in ranked[:limit]]


def render(role, insights, source_store, query, character_budget):
    blocks = []
    if role['base']:
        keys = recall_keys(query)
        lines = sorted(enumerate(role['base']), key=lambda pair: (-score_item(keys, pair[1]), pair[0]))
        selected = [line for _, line in lines[:4]]
        blocks.append('【我相对稳定的倾向】\n' + '\n'.join(selected))
    for item in insights:
        who = source_store.get_person_name(item['person_id']) if item['person_id'] else ''
        heading = f'我与{who or "这位朋友"}（人物身份 {item["person_id"]}）相处后形成的认识' if item['person_id'] else '我从具体经历形成的自身认识'
        state = '我仍暂时这样看待这段经历' if item['status'] == 'tentative' else '我在不同日期的经历支持了这一认识'
        refs = '、'.join(str(eid) for eid in item['supports'][-6:])
        blocks.append(f'【{heading}】\n{item["content"]}\n〔认识#{item["id"]}；{state}；原话#{refs}〕')
    selected = []
    size = 0
    for block in blocks:
        if size + len(block) > character_budget: continue
        selected.append(block); size += len(block) + 2
    return '\n\n'.join(selected)
