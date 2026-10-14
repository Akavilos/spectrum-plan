"""候选触发、概率、互斥和预算逐步筛选；只有入选正文参与后续递归。"""
from . import worldbook as wb


def group_names(entry):
    return set(wb.split_terms(entry['group_name']))


def resolve_groups(rows, text, rng, sticky_ids, occupied):
    remaining=list(rows);selected=[]
    for name in sorted(set().union(*(group_names(row) for row in remaining)) if remaining else set()):
        pool=[r for r in remaining if name in group_names(r) and not group_names(r)&occupied]
        if not pool: continue
        sticky=[r for r in pool if r['id'] in sticky_ids]
        winner=wb.pick_from_group(sticky or pool,text,rng)
        selected.append(winner)
        occupied.update(group_names(winner))
        remaining=[r for r in remaining if not group_names(r)&occupied]
    return [r for r in remaining if not group_names(r)]+selected


def evaluate(store, head, *, umo=None, qvec=None, rng=None, max_entries=0, token_budget=0,
             commit=True, current_ids=None, generation='normal', persona='', config):
    rng=rng or wb.DEFAULT_RNG
    books=store.worldbooks.selected_ids()
    if not books: return [],{'selected_books':[],'entries':[],'used_tokens':0,'budget':token_budget}
    selection=store.worldbooks.selection()
    default_depth=int(config('worldbook_scan_depth',2))
    depth_cap=store._worldbook_scan_cap(default_depth)
    minimum=int(config('worldbook_min_activations',0))
    expand_limit=max(default_depth,int(config('worldbook_max_scan_depth',20))) if minimum else default_depth
    if minimum: depth_cap=max(depth_cap,expand_limit)
    lines=store.worldbook_scan_lines(umo,depth_cap,head=head,current_ids=current_ids) if umo else []
    seq=store.worldbook_seq(umo) if umo else 0
    states=store.worldbook_states(umo) if umo else {}
    sticky_ids={eid for eid,state in states.items() if int(state['sticky_until'])>seq}
    timed=(seq,states) if umo else None
    steps=wb.clamp_steps(config('worldbook_max_steps',3)) if config('worldbook_recursion',False) and not minimum else 1
    feeds=[];selected=[];processed=set();occupied=set();trace=[]
    def sort_key(row):
        strategy=selection['strategy']
        first=selection['global_book'] if strategy=='global_first' else selection['current_book']
        source=0 if strategy=='order' or row['book_id']==first else 1
        return (0 if wb.is_constant(row) else 1,source,-int(row['order_value']),int(row['id']))
    for step in range(steps):
        depth=default_depth
        while True:
            candidates=store.worldbook_activate(head,lines=lines,default_depth=depth,extra='\n'.join(feeds),
                qvec=qvec if step==0 else None,recursive=step>0,timed=timed,book_ids=books,
                generation=generation,persona=persona,recursion_level=step)
            if step or not minimum or len(candidates)>=minimum or depth>=expand_limit: break
            depth+=1
        candidates=[row for row in candidates if row['id'] not in processed]
        eligible=[]
        for row in candidates:
            processed.add(row['id'])
            if row['id'] in sticky_ids or wb.probability_pass(row,rng): eligible.append(row)
            else: trace.append({'id':row['id'],'title':row['title'],'reason':'概率未通过','step':step})
        grouped=resolve_groups(eligible,wb.scan_text(head,lines,depth)+'\n'+'\n'.join(feeds),rng,sticky_ids,occupied)
        winner_ids={row['id'] for row in grouped}
        trace.extend({'id':row['id'],'title':row['title'],'reason':'互斥组未入选','step':step} for row in eligible if row['id'] not in winner_ids)
        ordered=selected+sorted(grouped,key=sort_key)
        kept,_=wb.trim_to_budget(ordered,max_entries,token_budget)
        kept_ids={row['id'] for row in kept}
        new=[row for row in kept if row['id'] not in {r['id'] for r in selected}]
        for row in grouped:
            trace.append({'id':row['id'],'title':row['title'],'reason':'入选' if row['id'] in kept_ids else '超过预算',
                          'step':step,'position':wb.POSITION_LABELS.get(row['position'],'生活与场景')})
        selected=kept
        occupied=set().union(*(group_names(row) for row in selected)) if selected else set()
        feeds.extend(row['text'] for row in new if not row['prevent_recursion'])
        if not new and not feeds: break
    selected.sort(key=sort_key)
    if commit and umo:
        for row in selected:
            state=wb.next_timed_state(row,seq,row['id'] in sticky_ids)
            if state: store.touch_worldbook_state(umo,row['id'],fired_seq=seq,**state)
    return selected,{'selected_books':books,'entries':trace,'used_tokens':sum(wb.estimate_tokens(r['text']) for r in selected),
                     'budget':token_budget,'scan_depth':depth,'message_count':seq,'generation':generation}
