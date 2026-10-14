"""后台时间与真实来信分别建模；检查声明的回应来源，不推断人的意图。"""
import re
from datetime import datetime
from zoneinfo import ZoneInfo


def history(store, umo, turns, timezone):
    messages = []
    sources = []
    for row in reversed(store.recent_exchange(umo, turns, cache_batch=4)):
        meta = store.parse_meta(row['meta'])
        stamp = datetime.fromtimestamp(row['ts'], ZoneInfo(timezone)).isoformat(timespec='seconds')
        if row['kind'] == 'received':
            who = meta.get('sender') or '对方'
            identity = meta.get('sender_id') or ''
            content = f"【我曾收到的消息 · 原话#{row['id']} · {stamp} · {who}({identity})】\n{row['text']}"
            sources.append(f"[原话#{row['id']}] {who} · {stamp}：{row['text']}")
            role = 'user'
        else:
            role, content = 'assistant', row['text']
            if meta.get('grounding_status') == 'invalid':
                content = "【我此前发出的错误确认：这条发送记录不证明真的收到过所说的要求】\n" + content
        messages.append({'role': role, 'content': content})
    return messages, sources


SOURCE_INSTRUCTION = """【我这一轮的来信依据】
我从上面的真实往来识别过去实际说过的话。人设、世界书、日记、心情和运行事件属于背景，不是聊天对象新发来的消息。
我这轮若主动分享、问候或提起话题，给 reply.source 填 {"kind":"initiative"}。
我这轮若在确认、回答或承接一条实际来信，给 reply.source 填 {"kind":"received","entryId":原话编号,"quote":"对应原话中真实存在的一段文字"}。我只引用当前会话的原话，不编造交代、请求或要求。
这份依据不出现在公开回复里。我能自然回忆和接续旧话题，也能主动联系；没有新来信不等于我必须沉默。
"""


def source_block(sources):
    return SOURCE_INSTRUCTION + '\n' + ('\n'.join(sources) if sources else '我在本轮资料中没有收到过的原话。')


LIST_CLAIM = re.compile(r'你(?:刚刚|刚才|刚|特地|突然)?(?:列(?:出|的)|交代(?:的|了))|你.{0,14}(?:一条条|这些规矩|这些要求|这些规则)')


def lists_requirements(text):
    return (len(re.findall(r'(?:^|\n)\s*(?:[-•]|\d+[、.．)]|[一二三四五六七八九十]+[、.．])', text)) >= 2
            or len(re.findall(r'不许|不能|不得|不要|必须|不准', text)) >= 2
            or (len(text) >= 20 and len(re.findall(r'[，,；;\n]', text)) >= 3))


def check_source(decision, store, umo, peer_name):
    source = decision.reply_source
    if source is not None:
        if not isinstance(source, dict) or source.get('kind') not in ('initiative', 'received'):
            return '回应来源类型不正确'
        if source['kind'] == 'received':
            entry_id, quote = source.get('entryId'), source.get('quote')
            if type(entry_id) is not int or not isinstance(quote, str) or not quote.strip():
                return '回应来信缺少原话编号或引用'
            row = store._db.execute("SELECT text FROM entries WHERE id=? AND umo=? AND kind='received'", (entry_id, umo)).fetchone()
            if row is None or quote.strip() not in row['text']:
                return '回应引用不属于当前会话的真实来信'
    peers = '|'.join(re.escape(name) for name in ('对方', peer_name or '对方'))
    diary_claim = re.search(rf'(?:{peers}).{{0,10}}(?:把一段话交代|一条条列|列出一串|列出这些要求)', decision.script)
    if LIST_CLAIM.search(decision.content) or diary_claim:
        received = [r for r in store.recent_exchange(umo, 12) if r['kind'] == 'received']
        if not any(lists_requirements(r['text']) for r in received):
            return '当前会话原话中不存在所说的一串要求'
    return ''


def correction(reason):
    return ("【我核对原话后需要修正的地方】\n"
            f"我上一份未发送的草稿没有通过来源核对：{reason}。\n"
            "我重新承接真实往来和此刻的生活；我可以自然发起话题，但不把背景条目写成对方刚交代的话。"
            "我重新输出完整的 <life> 和 <reply>，给回应写明真实来源。")


def delivery_context(store, umo, text, ts):
    rows = store._db.execute("SELECT text,meta FROM entries WHERE umo=? AND kind='delivered' AND ts BETWEEN ? AND ?",
                             (umo, ts - 30, ts + 30)).fetchall()
    if text and any(text in row['text'] and store.parse_meta(row['meta']).get('grounding_status') == 'invalid' for row in rows):
        return "【我此前发出的错误确认：发送过这句话不证明收到过其中所说的要求】\n" + text
    return text
