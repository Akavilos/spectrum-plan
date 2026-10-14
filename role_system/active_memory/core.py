"""只读记忆视图、线索筛选与结果组装；身份边界先于相关性。"""
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Context:
    account: str
    role: str
    session: str
    now: float
    timezone: str = 'Asia/Shanghai'


@dataclass(frozen=True)
class Memory:
    key: str
    account: str
    role: str
    source: str
    session: str
    text: str
    recorded_at: float
    occurred_at: float | None = None
    ended_at: float | None = None
    people: tuple = ()
    references: tuple = ()
    nature: str = '原话'
    state: str = ''
    emotions: tuple = ()
    mood: tuple = ()
    topic: str = ''
    public: bool = False
    importance: float = 0.5
    names: tuple = ()


@dataclass
class Cues:
    query: str = ''
    people: tuple = ()
    start: float | None = None
    end: float | None = None
    latest: bool = False
    emotion: str = ''
    intensity: float | None = None
    notes: list = field(default_factory=list)


EMOTIONS = {'开心':'喜悦','高兴':'喜悦','快乐':'喜悦','难过':'悲伤','伤心':'悲伤',
            '害怕':'恐惧','生气':'愤怒','谢谢':'感激','放心':'安心','委屈':'委屈','紧张':'紧张'}
PAD = {'喜悦':(.4,.2,.1),'悲伤':(-.4,-.2,-.5),'恐惧':(-.64,.6,-.43),
       '愤怒':(-.51,.59,.25),'感激':(.4,.2,-.3),'安心':(.3,-.3,.1),
       '委屈':(-.4,.1,-.5),'紧张':(-.2,.6,-.2)}
COMMON = {'时候','事情','那个','这个','我们','自己','之前','记得','什么','怎么','昨天','今天','上次','当时','回忆','说过','对方'}
SYNONYMS = ({'奶茶','饮品','饮料'}, {'米线','米粉'}, {'答应','承诺','约定'}, {'送来','送到','送的','收到'})


def keywords(text):
    words = re.findall(r'[a-zA-Z0-9_-]{2,}|[\u4e00-\u9fff]{2,}', text.casefold())
    keys = set()
    for word in words:
        if len(word)<=16: keys.add(word)
        if re.search('[\u4e00-\u9fff]', word):
            keys.update(word[i:i+2] for i in range(len(word)-1))
    keys -= COMMON
    for group in SYNONYMS:
        if keys & group: keys.update(group)
    return keys


def normalize_time(value, ctx):
    if value is None: return None,None,False,[]
    if not isinstance(value,dict) or set(value)-{'start','end','hint'}:
        raise ValueError('time 使用 start、end、hint 组成的对象')
    zone=ZoneInfo(ctx.timezone)
    def parse(text,end=False):
        if not isinstance(text,str): raise ValueError('日期使用 ISO 日期或时间文字')
        try: point=datetime.fromisoformat(text.replace('Z','+00:00'))
        except ValueError as error: raise ValueError('日期格式应为 YYYY-MM-DD 或 ISO 时间') from error
        if point.tzinfo is None: point=point.replace(tzinfo=zone)
        if end and len(text)==10: point+=timedelta(days=1)
        return point.timestamp()
    start=parse(value['start']) if value.get('start') else None
    end=parse(value['end'],True) if value.get('end') else None
    hint=value.get('hint','').strip()
    latest=hint in ('上次','最近一次','最后一次')
    notes=[]
    if start is None and end is None and hint and not latest:
        today=datetime.fromtimestamp(ctx.now,zone).replace(hour=0,minute=0,second=0,microsecond=0)
        day={'今天':0,'昨天':1,'前天':2}
        matched=next((label for label in day if hint.startswith(label)),None)
        if matched:
            left=today-timedelta(days=day[matched]);right=left+timedelta(days=1)
            period=hint[len(matched):]
            hours={'早上':(5,12),'上午':(6,12),'中午':(11,14),'下午':(12,18),'晚上':(18,24),'夜里':(18,24)}
            if period in hours:
                begin,finish=hours[period];left+=timedelta(hours=begin);right=today-timedelta(days=day[matched])+timedelta(hours=finish)
            start,end=left.timestamp(),right.timestamp()
        elif hint in ('上周','本周'):
            left=today-timedelta(days=today.weekday()+ (7 if hint=='上周' else 0))
            start,end=left.timestamp(),(left+timedelta(days=7)).timestamp()
        elif re.fullmatch(r'最近\d{1,3}天',hint):
            start,end=(today-timedelta(days=int(hint[2:-1]))).timestamp(),ctx.now+1
        else: notes.append('我没有确定这条模糊时间线索对应的日期范围。')
    if start is not None and end is not None and start>=end: raise ValueError('时间范围的结束须晚于开始')
    return start,end,latest,notes


def emotion_score(memory,cues):
    if not cues.emotion: return 0.0
    matches=[float(amp) for label,amp in memory.emotions if EMOTIONS.get(label,label)==cues.emotion]
    if matches:
        value=max(matches)
        return value if cues.intensity is None else max(0,1-abs(value-cues.intensity))
    target=PAD.get(cues.emotion)
    if target and memory.mood:
        return max(0,1-math.sqrt(sum((a-b)**2 for a,b in zip(target,memory.mood)))/math.sqrt(12))*.5
    return 0.0


def candidates(records,ctx,cues,limit=80):
    keys=keywords(cues.query)
    scoped=[]
    for memory in records:
        if memory.account!=ctx.account or memory.role!=ctx.role: continue
        if cues.people and not set(cues.people)<=set(memory.people): continue
        when=memory.occurred_at
        # 明确查事件时间时，未知发生时间不能用整理时间冒充。
        if cues.start is not None or cues.end is not None:
            if when is None: continue
            if cues.start is not None and (memory.ended_at or when)<cues.start: continue
            if cues.end is not None and when>=cues.end: continue
        feeling=emotion_score(memory,cues)
        if cues.emotion and not keys and feeling<=0: continue
        hits=sum(len(key) for key in keys if key in (memory.topic+' '+memory.text).casefold())
        scoped.append((memory,float(hits),feeling))
    anchors=[item for item in scoped if item[1]>0] if keys else scoped
    anchors.sort(key=lambda item:(item[1],item[2],item[0].occurred_at or item[0].recorded_at),reverse=True)
    picked={item[0].key:item for item in anchors[:limit]}
    # 只沿来源一致、同一主题且有人物交集的证据传播，最多两跳。
    for _ in range(2):
        topics={(m.session,m.topic,p) for m,_,_ in picked.values() if m.topic for p in m.people}
        added=False
        for item in scoped:
            m,hits,feeling=item
            if m.key in picked or not m.topic: continue
            if any((m.session,m.topic,p) in topics for p in m.people):
                picked[m.key]=(m,.5,feeling);added=True
                if len(picked)>=limit: break
        if not added or len(picked)>=limit: break
    return list(picked.values())[:limit]


def select(pool,ctx,cues,limit=3):
    def score(item):
        m,hits,feeling=item
        age=max(0,ctx.now-(m.occurred_at or m.recorded_at))/86400
        relevance=hits/(hits+5) if hits else 0
        return relevance+.15*feeling+.05*m.importance+.05/(1+age)
    ranked=sorted(pool,key=lambda item:((item[0].occurred_at if item[0].occurred_at is not None else float('-inf')) if cues.latest else score(item),score(item)),reverse=True)
    out=[];covered=set();texts=set()
    for memory,_,_ in ranked:
        refs=set(memory.references)
        identity=(memory.session,re.sub(r'\s','',memory.text))
        if identity in texts or (refs and refs & covered): continue
        texts.add(identity);covered.update(refs);out.append(memory)
        if len(out)>=limit: break
    return out


def render(memories,ctx,notes,budget=1800):
    result=[];used=0
    for memory in memories:
        body=memory.text[:max(0,min(550,budget-used))]
        if not body: break
        used+=len(body)
        show=lambda ts:datetime.fromtimestamp(ts,ZoneInfo(ctx.timezone)).isoformat() if ts is not None else None
        result.append({'id':memory.key,'归属角色':ctx.role,'涉及人物':list(memory.people),
                       '人物称呼':dict(memory.names),
                       '来源':memory.source,'来源会话':memory.session,'原文标识':list(memory.references),
                       '发生时间':show(memory.occurred_at),'记录时间':show(memory.recorded_at),
                       '性质':memory.nature,'状态':memory.state,'情绪主体':'我' if memory.emotions or memory.mood else None,
                       '情绪':list(memory.emotions),'心情':list(memory.mood),
                       '当前转述范围':'可谈起' if memory.public or memory.session==ctx.session else '其他往来的私下记录，保留原人物归属与转述分寸',
                       '我找到的记录':body,'正文截短':len(body)<len(memory.text)})
    return {'说明':'我查到了这些带来源的过去记录，是否提起由我决定。' if result else '我没有找到符合这些线索的记忆。',
            '时间与检索说明':notes,'记忆':result}
