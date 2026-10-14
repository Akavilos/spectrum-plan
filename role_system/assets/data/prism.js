/* 数据展示与明确的单项编辑；原始记录、记忆算法与角色生成由后端管理。 */
(() => {
  const tabs = [
    {id:'overview', title:'心情与概览', hint:'棱镜保留主观感受，共享心情、对人感受和相关经历；回忆正文与原始往来分别展示。'},
    {id:'scenes', title:'场景回忆', hint:'查看场景中的原始往来，编辑角色对这段经历的回忆。'},
    {id:'traces', title:'记忆强度', hint:'查看记忆当前的可回忆程度，或手动加强一条记忆。'},
    {id:'graph', title:'联想关系', hint:'查看场景与人物之间的联系，以及每个节点的关联记录。'},
    {id:'generalizations', title:'熟悉经历', hint:'重复的平淡话题及其来源。归纳随来源变化，不作为人物品行结论；使用开关在棱镜核心功能设置中。'},
    {id:'feelings', title:'人物感受', hint:'查看与清除棱镜核心对某个人的既有感受，不改变光谱核心的好感与信任。'},
    {id:'persons', title:'相关人物', hint:'查看哪些人物出现在场景中，以及共同经历形成的联系。'},
  ];
  const el = id => document.getElementById(id);
  const escape = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const stamp = value => value ? new Date(value * 1000).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : '尚未更新';
  const kind = value => ({scene:'场景',person:'人物',topic:'话题'}[value] || value);
  let current = 'overview', rows = [], graph = null, query = '', renderEpoch = 0, pendingDelete = null;
  const selection=new RecordSelection();
  const selectionResource=()=>({scenes:'prism.scenes',traces:'prism.traces',graph:'prism.scenes',feelings:'prism.persons',generalizations:'prism.generalizations'})[current]||'';
  const recordKey=row=>current==='graph'?row.id.slice(6):String(row.scene_id||row.key||row.person_id||row.id);
  const selectableRows=()=>filtered().filter(row=>current!=='graph'||row.kind==='scene');
  const selectionControls=()=>{
    const resource=selectionResource(),s=selection;
    el('selection-toolbar').innerHTML=!resource?'':`<div class="selection-bar">${s.mode?`<button type="button" class="btn btn-ghost btn-sm" data-action="select-page" ${s.busy?'disabled':''}>全选当前列表</button><button type="button" class="btn btn-ghost btn-sm" data-action="select-all" ${s.busy?'disabled':''}>全选筛选结果</button><button type="button" class="btn btn-ghost btn-sm" data-action="clear-selection">清空选择</button><button type="button" class="btn btn-danger btn-sm" data-action="delete-selected" ${!s.count||s.busy?'disabled':''}>${current==='graph'?'删除所选场景来源':'删除所选'}</button><button type="button" class="btn btn-ghost btn-sm" data-action="exit-selection">取消</button><span class="selection-count">已选 ${s.count} 条</span>`:'<button type="button" class="btn btn-ghost btn-sm" data-action="enter-selection">选择</button>'}</div>`;
  };
  const decorateSelection=items=>{
    selectionControls();if(!selection.mode||!selectionResource())return;
    const body=el('main').querySelector('table tbody');if(!body)return;
    [...body.rows].forEach((tr,index)=>{
      const row=items[index];if(!row||(current==='graph'&&row.kind!=='scene'))return;
      const input=document.createElement('input');input.type='checkbox';input.className='record-select';input.dataset.key=recordKey(row);input.checked=selection.has(recordKey(row));input.setAttribute('aria-label','选择记录 '+recordKey(row));tr.cells[0].prepend(input);
    });
  };
  let toastTimer;
  const toast = message => {
    el('toast').textContent = message; el('toast').hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(()=>el('toast').hidden=true,3000);
  };
  const api = async (path, body) => {
    const bridge = window.RoleModulePage;
    const value = body === undefined ? await bridge.apiGet('page/' + path) : await bridge.apiPost('page/' + path,body);
    if (value?.ok === false || value?.status === 'error') throw Error(value.error || value.message || '请求失败');
    return value && 'data' in value ? value.data : value;
  };
  const detailApi = async (path, params) => {
    const value = await window.RoleModulePage.apiGet('page/' + path,params);
    if (value?.ok === false) throw Error(value.error || '读取失败');
    return value.data;
  };
  const button = (action, key, text, style='btn-ghost') => `<button class="btn btn-sm ${style}" data-action="${action}" data-key="${escape(key)}">${text}</button>`;
  const empty = (title, hint='角色正常运行后，新记录会出现在这里。') => `<div class="empty-state"><strong>${title}</strong>${hint}</div>`;
  const table = (headers, body) => `<div class="table-scroll"><table><thead><tr>${headers.map(h=>`<th>${h}</th>`).join('')}</tr></thead><tbody>${body}</tbody></table></div>`;
  const strength = (value, scale=1) => `<span class="bar"><i style="width:${Math.max(0,Math.min(100,value*scale*100))}%"></i></span><span class="mono dim">${value.toFixed(3)}</span>`;
  const countCards = values => `<div class="cards">${values.map(([label,value])=>`<div class="card"><span>${label}</span><b>${value}</b></div>`).join('')}</div>`;
  const setNav = () => {
    el('nav').innerHTML = tabs.map(t=>`<button data-tab="${t.id}" class="${current===t.id?'on':''}" ${current===t.id?'aria-current="page"':''}>${t.title}</button>`).join('');
    const tab=tabs.find(t=>t.id===current);
    el('view-title').textContent=tab.title; el('view-hint').textContent=tab.hint;
    el('toolbar').hidden=current==='overview';
  };
  const filtered = () => rows.filter(row=>JSON.stringify(row).toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  const showRows = () => {
    const items = filtered();
    el('record-count').textContent = query ? `匹配 ${items.length} / 已加载 ${rows.length} 条` : `已加载 ${rows.length} 条`;
    selectionControls();
    if (!items.length) {
      el('main').innerHTML = empty(query ? '没有匹配的记录' : '暂无记录',query ? '试试其他关键词，或清除搜索。' : undefined);
      return;
    }
    if (current==='scenes') {
      el('main').innerHTML=table(['时间','回忆摘要','参与情况','显著程度','场合','操作'],items.map(r=>`<tr>
        <td class="mono dim">${escape(r.time)}</td><td class="record-text">${r.has_narrative?escape(r.narrative):'<span class="dim">尚未生成回忆</span>'}</td>
        <td class="dim">${r.people} 人 · ${r.moments} 条往来</td><td>${strength(r.peak_salience,2.5)}</td>
        <td><span class="tag ${r.public?'pub':'pri'}">${r.public?'群聊':'私聊'}</span></td><td>${button('open-scene',r.scene_id,'查看详情')}</td></tr>`).join(''))+'<div id="scene-detail"></div>';
    } else if (current==='traces') {
      el('main').innerHTML=table(['回忆摘要','当前强度','回忆次数','形成时间','状态','操作'],items.map(r=>`<tr>
        <td class="record-text">${escape(r.text)||'<span class="dim">暂无回忆正文</span>'}</td><td>${strength(r.strength)}</td><td>${r.rehearsals} 次</td>
        <td class="mono dim">${escape(r.time)}</td><td><span class="tag ${r.forgotten?'pri':'pub'}">${r.forgotten?'暂难回忆':'可回忆'}</span></td>
        <td>${button('boost',r.key,'加强记忆')}</td></tr>`).join(''));
    } else if (current==='graph') {
      const degrees = new Map();
      for(const edge of graph.edges){degrees.set(edge.a,(degrees.get(edge.a)||0)+1);degrees.set(edge.b,(degrees.get(edge.b)||0)+1);}
      el('main').innerHTML=countCards([['关联节点',graph.nodes.length],['联想联系',graph.edges.length],['场景节点',graph.nodes.filter(n=>n.kind==='scene').length],['人物节点',graph.nodes.filter(n=>n.kind==='person').length]])+
        '<p class="hint">共同出现的人物、相邻或相关的场景会形成联系。这里只展示已有联想，不将联想当作客观事实。</p>'+
        table(['节点','类型','关联数量','操作'],items.map(r=>`<tr><td>${escape(r.label)}</td><td><span class="tag">${escape(kind(r.kind))}</span></td><td>${degrees.get(r.id)||0}</td><td>${button('neighbors',r.id,'查看关联')}</td></tr>`).join(''))+'<div id="neighbor-detail"></div>';
    } else if (current==='generalizations') {
      el('main').innerHTML=table(['熟悉话题','参与人物','经历数量','最近往来','来源'],items.map(r=>`<tr>
        <td>${escape(r.topic)}</td><td class="mono">${r.people.map(escape).join('、')}</td><td>${r.count} 段</td>
        <td>${stamp(r.last_at)}</td><td>${r.sources.map(key=>button('open-scene',key.slice(6),'查看 '+key.slice(6))).join(' ')}</td></tr>`).join(''))+'<div id="scene-detail"></div>';
    } else if (current==='feelings') {
      el('main').innerHTML=table(['人物身份','感受','有效互动','更新时间'],items.map(r=>`<tr><td>${escape(r.person_id)}</td><td>${escape(r.describe)||'暂无明显的固定感受'}</td><td>${r.moments} 次</td><td>${stamp(r.updated)}</td></tr>`).join(''));
    } else if (current==='persons') {
      el('main').innerHTML=table(['人物账号','参与场景','共同经历关联的人物'],items.map(r=>`<tr><td class="mono">${escape(r.person_id)}</td><td>${r.scenes} 个</td><td>${r.acquainted_with.map(escape).join('、')||'<span class="dim">暂无</span>'}</td></tr>`).join(''));
    }
    decorateSelection(items);
  };
  const overview = (data, mood) => {
    const axes=[['愉悦程度',mood.mood[0],'当前感受偏愉快还是不快'],['活跃程度',mood.mood[1],'当前状态偏活跃还是平静'],['掌控感',mood.mood[2],'当前感觉更主动还是更被动']];
    return countCards([['经历场景',data.scenes],['已生成回忆',data.scenes_with_narrative],['可回忆的记忆',data.traces_alive],['暂难回忆的记忆',data.traces_forgotten]])+
      `<section class="detail" style="margin-top:0"><div class="detail-heading"><h3>当前心情</h3><span class="hint">${stamp(mood.updated_at)}${mood.stale?' · 待更新':''}</span></div>
       <p>${escape(mood.describe)}</p><div class="mood-grid">${axes.map(([title,value,help])=>`<div class="mood-item"><span class="hint">${title}</span><b>${value.toFixed(2)}</b><span class="hint">${help}</span></div>`).join('')}</div>
       <p class="hint" style="margin:14px 0 0">数值范围 −1 至 1。这份心情由棱镜共享给角色系统，作为主观背景。</p></section>
       <section class="detail"><div class="detail-heading"><h3>当前功能</h3><span class="hint">在功能设置 → 棱镜核心中修改</span></div>
       <div class="actions">${Object.entries(data.features||{}).map(([key,on])=>`<span class="tag ${on?'pub':'pri'}">${escape(({share_interperson:'对人感受',recall_by_topic:'相关联想',correction_enabled:'理解纠正',reconsolidation_enabled:'回忆更新',generalization_enabled:'日常归纳',batch_reading_enabled:'批量整理',rehearsal_enabled:'重访加固',personality_effects_enabled:'精细人格',share_scene_details:'经历细节',share_expression:'表达倾向'})[key])} · ${on?'开启':'关闭'}</span>`).join('')}</div>
       <p class="hint">已封存经历中有 ${data.corrections||0} 条理解收到明确纠正。批量整理开启会增加模型调用。</p></section>
       <div class="actions"><span class="hint">联想关系：${data.graph_nodes} 个节点 · ${data.graph_edges} 条联系</span></div>`;
  };
  const render = async () => {
    const epoch=++renderEpoch, target=current;
    setNav(); selectionControls(); el('refresh').disabled=true; el('main').innerHTML='<div class="list-loading" role="status">正在读取数据…</div>';
    try {
      if(target==='overview'){
        const [data,mood]=await Promise.all([api('overview'),api('mood')]);
        if(epoch===renderEpoch)el('main').innerHTML=overview(data,mood);
      } else {
        const data=await api(target);
        if(epoch!==renderEpoch)return;
        graph=target==='graph'?data:null;
        rows=graph?[...data.nodes].sort((a,b)=>b.salience-a.salience):data;
        showRows();
      }
    } catch(error) {
      if(epoch===renderEpoch)el('main').innerHTML=`<div class="notice error" role="alert">读取失败：${escape(error.message)} ${button('retry','','重新加载')}</div>`;
    } finally {if(epoch===renderEpoch)el('refresh').disabled=false;}
  };
  const sceneDetail = data => `<section class="detail scene-detail" aria-labelledby="scene-title">
    <div class="detail-heading"><h3 id="scene-title">场景详情</h3>${button('close-detail','','收起详情')}</div>
    <div class="kv"><b>时间与场合</b><span>${escape(data.time)} · ${data.moments.length} 条往来 · ${data.public?'群聊':'私聊'}</span>
      <b>参与人物</b><span class="mono">${data.present.map(escape).join('、')||'暂无'}</span><b>显著程度</b><span>${data.peak_salience.toFixed(3)} ${data.peak_emotions.map(([name,value])=>`<span class="tag">${escape(name)} ${value.toFixed(2)}</span>`).join(' ')}</span></div>
    <div class="detail-fields"><div class="wide"><label for="scene-narrative">回忆正文</label><textarea id="scene-narrative" rows="5">${escape(data.narrative)}</textarea></div>
      <div><label for="scene-doing">当时的活动</label><textarea id="scene-doing">${escape(data.doing)}</textarea></div>
      <div><label for="scene-meaning">这段经历的主观意义</label><textarea id="scene-meaning">${escape(data.meaning)}</textarea></div></div>
    <div class="actions">${button('save-scene',data.scene_id,'保存回忆','btn-primary')}</div>
    <p class="hint" style="margin-top:10px">保存只修改这段经历的回忆、活动与主观意义，不改写原始往来。删除会移除该场景及关联记忆。</p>
    ${data.revisions.length?`<details><summary>修改记录（${data.revisions.length} 次）</summary>${data.revisions.map(r=>`<div class="rev">${escape(r.before)} → ${escape(r.after)}<br>${escape(r.reason)} · ${escape(r.at)}</div>`).join('')}</details>`:''}
    ${data.segments?.length?`<details><summary>批量整理的话题（${data.segments.length} 段）</summary>${data.segments.map(p=>`<div class="rev">${escape(p.topic)||'未命名话题'} · 原话 ${p.start+1}–${p.end+1}</div>`).join('')}</details>`:''}
    ${data.moments.some(m=>m.understanding)?`<details><summary>理解与纠正记录</summary>${data.moments.filter(m=>m.understanding).map(m=>`<div class="rev">原话：${escape(m.text)}<br>当时理解：${escape(m.understanding.read_as)}${m.understanding.was_wrong?`<br>后来收到的纠正：${escape(m.understanding.corrected_by)}`:''}</div>`).join('')}</details>`:''}
    <h4 style="margin-top:20px">原始往来 <span class="hint">只读</span></h4>
    ${table(['时间','说话人','原文','显著程度'],data.moments.map(m=>`<tr><td class="mono dim">${escape(m.time)}</td><td>${m.is_hers?'角色':escape(m.speaker)}</td><td class="record-text">${escape(m.text)}</td><td class="mono dim">${m.salience.toFixed(2)}</td></tr>`).join(''))}
  </section>`;
  const openScene = async key => {
    const epoch=renderEpoch;
    const data=await detailApi('scenes/detail',{scene_id:key});
    if(epoch!==renderEpoch)return;
    const box=el('scene-detail'); box.innerHTML=sceneDetail(data);
    box.querySelector('section').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion:reduce)').matches?'auto':'smooth',block:'start'});
  };
  const action = async target => {
    const {action: name,key}=target.dataset;
    if(name==='enter-selection'){selection.enter();showRows();return;}
    if(name==='exit-selection'){selection.exit();showRows();return;}
    if(name==='clear-selection'){selection.clear();showRows();return;}
    if(name==='select-page'){selection.add(selectableRows().map(recordKey));showRows();return;}
    if(name==='select-all'){
      const resource=selectionResource(),scope=current,search=query,revision=selection.revision;
      selection.busy=true;selectionControls();
      try{const result=await recordRequest('query',{resource,filters:{search,history:true},select_all:true});
        if(revision===selection.revision&&scope===current&&search===query)selection.replace(result.ids);
      }catch(error){toast('选择失败：'+error.message);}finally{selection.busy=false;showRows();}return;
    }
    if(name==='delete-selected'){
      if(!selection.count)return;
      const resource=selectionResource(),scope=current,search=query,ids=[...selection.ids];
      try{const result=await recordRequest('query',{resource,limit:1});
        if(scope!==current||search!==query)return;
        pendingDelete={resource,ids};el('confirm-title').textContent='删除所选'+result.title;el('confirm-yes').textContent='确认删除';
        el('confirm-message').textContent=`确认删除选中的 ${ids.length} 条？${result.impact}`;el('confirm').showModal();
      }catch(error){toast(error.message);}return;
    }
    target.disabled=true;
    try {
      if(name==='open-scene')await openScene(key);
      else if(name==='save-scene'){
        await api('scenes/update',{scene_id:key,narrative:el('scene-narrative').value,doing:el('scene-doing').value,meaning:el('scene-meaning').value});
        toast('回忆已保存'); await openScene(key);
      } else if(name==='boost'){
        await api('traces/boost',{key,times:3}); toast('记忆已加强'); await render();
      } else if(name==='neighbors') {
        const epoch=renderEpoch, linked=await detailApi('graph/neighbors',{key});
        if(epoch!==renderEpoch)return;
        const names=new Map(graph.nodes.map(n=>[n.id,n.label]));
        el('neighbor-detail').innerHTML=`<section class="detail"><div class="detail-heading"><h3>${escape(names.get(key)||key)} 的关联</h3>${button('close-neighbors','','收起详情')}</div>`+
          (linked.length?table(['关联节点','类型','联系强度'],linked.map(n=>`<tr><td>${escape(names.get(n.key)||n.key)}</td><td>${escape(kind(n.kind))}</td><td>${n.w.toFixed(3)}</td></tr>`).join('')):empty('暂无关联'))+'</section>';
      } else if(name==='close-detail')el('scene-detail').innerHTML='';
      else if(name==='close-neighbors')el('neighbor-detail').innerHTML='';
      else if(name==='retry')await render();
    } catch(error){toast('操作失败：'+error.message);}
    finally{target.disabled=false;}
  };
  el('nav').addEventListener('click',event=>{
    const target=event.target.closest('[data-tab]'); if(!target)return;
    selection.exit();current=target.dataset.tab; query=''; el('search').value=''; el('clear-search').hidden=true; rows=[]; render();
  });
  el('main').addEventListener('click',event=>{
    const target=event.target.closest('[data-action]'); if(target)action(target);
  });
  el('search').addEventListener('input',()=>{selection.exit();query=el('search').value; el('clear-search').hidden=!query; showRows();});
  el('clear-search').onclick=()=>{selection.exit();query='';el('search').value='';el('clear-search').hidden=true;showRows();el('search').focus();};
  el('main').addEventListener('change',event=>{if(event.target.matches('.record-select')){selection.toggle(event.target.dataset.key,event.target.checked);selectionControls();}});
  el('selection-toolbar').addEventListener('click',event=>{const target=event.target.closest('[data-action]');if(target)action(target);});
  el('refresh').onclick=render;
  el('confirm-cancel').onclick=()=>el('confirm').close();
  el('confirm').addEventListener('close',()=>pendingDelete=null);
  el('confirm-yes').onclick=async()=>{
    const key=pendingDelete; el('confirm-yes').disabled=true;
    try{if(!key)return;const result=await recordRequest('delete',{...key,confirmed:true});el('confirm').close();selection.exit();toast(`已删除 ${result.deleted} 条`);await render();}
    catch(error){toast('删除失败：'+error.message);}
    finally{el('confirm-yes').disabled=false;}
  };
  setNav(); render();
})();
