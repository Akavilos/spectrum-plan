"""将宿主已有原文、事实与棱镜瞬间转换成同一只读视图。"""
import json
from dataclasses import replace
from .core import Memory


class HostAdapter:
    def __init__(self, system): self.system=system

    def aliases(self, account):
        from ..spectrum_module.spectrum.person import unambiguous_aliases
        return unambiguous_aliases(self.system.spectrum.store.all_person_aliases(),account)

    def records(self,ctx,settings):
        source=self.system.spectrum.store
        snapshot=source.active_memory_snapshot(ctx.account)
        aliases=self.aliases(ctx.account)
        role_store=self.system.personality.store
        roles=sorted((role_store.role(row['id']) for row in role_store.roles()),key=lambda item:item['created'])
        def legacy_owner(ts):
            selected=roles[0] if roles else None
            for item in roles:
                if item['created']<=ts:selected=item
            return selected is not None and selected['id']==ctx.role
        def owner(row):
            meta=json.loads(row.get('meta') or '{}')
            explicit=meta.get('memory_role_id')
            return explicit==ctx.role if explicit else legacy_owner(row['ts'])
        entries={row['id']:row for row in snapshot['entries'] if owner(row)}
        def person(value):
            return value if value.startswith(ctx.account+':') else ctx.account+':'+value
        def participants(row):
            meta=json.loads(row.get('meta') or '{}')
            uid=str(meta.get('sender_id') or '')
            ids=set()
            if uid: ids.add(person(uid))
            if ':FriendMessage:' in row['umo']: ids.add(person(row['umo'].rsplit(':',1)[-1]))
            ids.update(pid for alias,pid in aliases if len(alias)>=2 and alias in row['text'])
            return tuple(sorted(ids))
        out=[]
        for row in entries.values():
            if row['kind'] not in ('received','delivered','delivered_media','observed'): continue
            world=row['kind']=='observed'
            label={'received':'对方当时发来的原话','delivered':'我实际发出的原话','delivered_media':'我的媒体送达记录','observed':'已登记的环境观察'}[row['kind']]
            if not json.loads(row.get('meta') or '{}').get('memory_role_id'):
                label+='；旧记录按角色档案时段关联'
            out.append(Memory(f"entry:{row['id']}",ctx.account,ctx.role,'光谱原文',row['umo'],row['text'],row['ts'],
                              row['ts'],people=participants(row),references=(f"entry:{row['id']}",),nature=label,
                              public=world or ':GroupMessage:' in row['umo']))
        for table in ('memories','person_memories'):
            for row in snapshot[table]:
                refs=[int(item) for item in str(row.get('source_entry_ids') or '').replace('|',',').split(',') if item.strip().isdigit()]
                originals=[entries[i] for i in refs if i in entries]
                if refs and len(originals)!=len(refs): continue
                if not owner(row): continue
                session=row.get('umo') or row.get('origin_umo') or ''
                people=set(pid for item in originals for pid in participants(item))
                if row.get('person_id'):people.add(row['person_id'])
                if ':FriendMessage:' in session:people.add(person(session.rsplit(':',1)[-1]))
                out.append(Memory(f'{table}:{row["id"]}',ctx.account,ctx.role,'人物记忆' if table=='person_memories' else '光谱事实摘要',
                                  session,row['text'],row['ts'],references=tuple(f'entry:{i}' for i in refs),
                                  people=tuple(sorted(people)),nature='摘要；发生时间需参照原文',
                                  public=':GroupMessage:' in session,importance=row.get('importance',.5)))
        for row in snapshot['shared_life_events']:
            entry=entries.get(row['source_entry_id'])
            if entry is None: continue
            text=f"{row['text']}（依据：{row['basis']}）"
            state=row['state']+('；已被后续记录替代' if row['superseded_by'] else '')
            if row['valid_until'] and row['valid_until']<ctx.now:state+='；有效期已过，仅作历史记录'
            nature={'physical':'依据自身生活记录登记的事件','commitment':'我在已发送回复中作出的承诺',
                    'knowledge':'我根据收到的原话记下的认知；陈述不等于亲眼确认'}[row['kind']]
            people=set(participants(entry))
            people.update(pid for alias,pid in aliases if alias==row['subject'])
            if row['subject'].startswith(ctx.account+':'):people.add(row['subject'])
            out.append(Memory(f"world:{row['id']}",ctx.account,ctx.role,'共同生活事件',row['source_umo'],text,row['ts'],
                              row['ts'],people=tuple(sorted(people)),references=(f"entry:{entry['id']}",),
                              nature=nature,state=state,public=':GroupMessage:' in row['source_umo'],importance=.8))
        if settings['prism']:
            matching={(row['umo'],row['text']):row['id'] for row in entries.values()}
            for key,scene in self.system.prism.memory.scenes.items():
                for index,m in enumerate(scene.moments):
                    if not m.umo.startswith(ctx.account+':'):continue
                    if m.memory_role_id:
                        if m.memory_role_id!=ctx.role:continue
                    elif not legacy_owner(m.ts):continue
                    if not m.text:continue
                    people=(person(m.speaker),) if m.speaker else ()
                    ref=matching.get((m.umo,m.text))
                    u=m.understanding
                    text=m.text
                    if u:
                        text+='\n我当时的主观理解：'+u.read_as
                        if u.was_wrong:text+='\n我后来收到的纠正：'+u.corrected_by
                    out.append(Memory(f'prism:{key}:{index}',ctx.account,ctx.role,'棱镜经历',m.umo,text,m.ts,m.ts,
                                      people=people,references=(f'entry:{ref}',) if ref else (f'prism:{key}:{index}',),
                                      nature='我当时的感受与主观理解',emotions=m.emotions,mood=(m.mood.p,m.mood.a,m.mood.d),
                                      topic=m.topic or scene.topic,public=':GroupMessage:' in m.umo,importance=min(1,m.salience)))
        if settings['personality'] and self.system.personality.compatible():
            from ..personality.learning import valid_sources
            for item in role_store.list_insights(ctx.role,limit=1000):
                if item['account']!=ctx.account or item['status'] not in ('tentative','formed'):continue
                if not valid_sources(source,item) or any(i not in entries for i in item['supports']):continue
                out.append(Memory(f'personality:{item["id"]}',ctx.account,ctx.role,'人格经历认识',item['origin'],item['content'],
                                  item['ts'],people=(item['person_id'],) if item['person_id'] else (),
                                  references=tuple(f'entry:{i}' for i in item['supports']),nature='我从经历形成的主观认识',state=item['status']))
        names={pid:source.get_person_name(pid) or pid for pid in {p for m in out for p in m.people}}
        return [replace(m,names=tuple((pid,names[pid]) for pid in m.people)) for m in out]
