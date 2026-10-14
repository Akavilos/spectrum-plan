/* 底稿、经历认识与版本分开编辑；保存与学习操作分别说明是否调用模型。 */
let personalityView;
const personalityLabels={tentative:'暂时认识',formed:'已形成的认识',revising:'待修订',revoked:'已撤销'};
async function loadPersonality(){
 personalityView=await api('/api/personality');const {settings,role}=personalityView;fillProviderSelect(el('personality_provider_id'),settings.provider_id||'');
 for(const key of ['enabled','learning_enabled'])el('personality_'+key).checked=settings[key];
 for(const key of ['interval_hours','min_messages','daily_budget','batch_size','max_context','context_chars'])el('personality_'+key).value=settings[key];
 el('personalityBase').value=role.base.join('\n');el('personalityLocked').checked=!!role.locked;
 el('personalityConfirm').checked=false;el('personalityReview').classList.toggle('hidden',!personalityView.persona_changed);
 el('personalityState').textContent=personalityView.persona_changed?'人设已变化：旧人格背景和学习暂停，请审阅后确认沿用档案，或建立新档案。':`${settings.enabled?'人格背景已启用':'人格背景已关闭'} · ${settings.learning_enabled?'经历学习已允许':'经历学习已关闭'} · 今日整理 ${personalityView.used_today}/${settings.daily_budget} 次 · 待处理原话 ${personalityView.pending_evidence} 条`;
 el('personalitySource').textContent=`底稿版本 ${role.revision} · ${role.source} · 档案 ${role.id.slice(0,8)}`;
 el('personalityDraft').value=role.draft.join('\n');el('personalityApplyDraft').disabled=!role.draft.length||!!role.locked||role.draft_hash!==personalityView.roles.find(r=>r.id===role.id)?.persona_hash||personalityView.persona_changed;
 el('personalityResult').textContent=personalityView.last_result.message||JSON.stringify(personalityView.last_result,null,2);
 const roles=el('personalityRoles');roles.replaceChildren();for(const r of personalityView.roles){const option=node('option',`${new Date(r.created*1000).toLocaleDateString()} · ${r.id.slice(0,8)}`);option.value=r.id;roles.append(option)}roles.value=role.id;
 renderPersonalityNotes();
}
function renderPersonalityNotes(){
 const root=el('personalityNotes');root.replaceChildren();
 const filter=el('personalityFilter').value;
 const rows=personalityView.insights.filter(item=>!filter||item.status===filter);
 if(!rows.length)root.append(node('p','这个分组还没有认识。启用学习后，实际来信逐步形成可追溯的认识。','hint'));
 for(const item of rows){
  const card=node('details');card.append(node('summary',`${personalityLabels[item.status]} · ${item.topic} · ${item.name||item.person_id||'自身感受'}`));
  card.append(node('p',`来源会话 ${item.origin} · 机器人账号 ${item.account} · 修订 ${item.revision} · ${item.supports.length} 条支持原话 · ${item.doubts.length} 条相反原话`,'hint'));
  if(item.source_valid===false)card.append(node('p','来源原话已有修改或缺失，这条认识暂不注入；请查看证据后修订或撤销。','notice'));
  const content=field(card,'当前认识（第一人称）',item.content,'textarea');const topics=field(card,'相关关键词（逗号分隔，每个2–20字）',item.topics.join('，'));
  const locked=field(card,'锁定这条认识，阻止后续自动修订',!!item.locked,'checkbox');
  const detail=node('div');const buttons=node('div',undefined,'actions');
  buttons.append(action('保存修改',async()=>{await api('/api/personality/insights/'+item.id,{action:'edit',content:content.value,topics:topics.value.split(/[，,]/).map(s=>s.trim()).filter(Boolean),locked:locked.checked});await loadPersonality();toast('认识已保存，来源范围保留')}),
   action('查看证据与修订',async()=>{
    const data=await api('/api/personality/insights/'+item.id);detail.replaceChildren();
    detail.append(node('h3','来源原话'));for(const row of data.evidence){let meta=JSON.parse(row.meta);detail.append(node('p',`#${row.id} · ${meta.sender||meta.sender_id||'说话人'} · ${new Date(row.ts*1000).toLocaleString()} · ${row.umo}`,'hint'),node('pre',row.text))}
    if(data.evidence.length<new Set([...item.supports,...item.doubts]).size)detail.append(node('p','有来源原话已删除或不在本次查看范围；缺失来源的认识不会注入。','notice'));
    detail.append(node('h3','修订记录'));for(const version of data.versions){const snapshot=JSON.parse(version.snapshot);const v=node('div',undefined,'metric');v.append(node('p',`版本 ${version.revision} · ${version.cause} · ${personalityLabels[snapshot.status]}`,'hint'),node('p',snapshot.content));v.append(action('恢复此版本并锁定',async()=>{await api('/api/personality/insights/'+item.id,{action:'rollback',revision:version.revision});await loadPersonality();toast('已恢复并锁定，可查看后手动解锁')}));detail.append(v)}
   }),action('撤销这条认识',async()=>{await api('/api/personality/insights/'+item.id,{action:'revoke'});await loadPersonality();toast('已撤销；保留修订记录，不改过去发送的消息')}));
  card.append(buttons,detail);root.append(card);
 }
}
el('personalityForm').onsubmit=managed(async event=>{
 event.preventDefault();const settings={provider_id:el('personality_provider_id').value};for(const key of ['enabled','learning_enabled'])settings[key]=el('personality_'+key).checked;
 for(const key of ['interval_hours','min_messages','daily_budget','batch_size','max_context','context_chars'])settings[key]=Number(el('personality_'+key).value);
 await api('/api/personality/settings',{data:{settings,base:el('personalityBase').value.split('\n').map(s=>s.trim()).filter(Boolean),locked:el('personalityLocked').checked},confirm_persona:el('personalityConfirm').checked});
 await loadPersonality();toast('人格设置已生效，不需要重启');
});
el('personalityGenerateDraft').onclick=managed(async()=>{el('personalityGenerateDraft').disabled=true;try{await api('/api/personality/draft',{});await loadPersonality();toast('整理完成，仅保存草稿，尚未替换底稿')}finally{el('personalityGenerateDraft').disabled=false}});
el('personalityCopyDraft').onclick=()=>{el('personalityBase').value=el('personalityDraft').value;toast('已放入编辑框，审阅后保存')};
el('personalityApplyDraft').onclick=managed(async()=>{await api('/api/personality/apply-draft',{});await loadPersonality();toast('已应用当前人设草稿并锁定底稿')});
el('personalityLearn').onclick=managed(async()=>{el('personalityLearn').disabled=true;try{const result=await api('/api/personality/learn',{});await loadPersonality();const labels={disabled_or_paused:'先启用人格背景和经历学习，并确认人设版本',waiting_for_evidence:'尚无足够的新原话；不会把旧聊天当成新经历',sleeping:'角色睡眠期间不整理未读来信',budget:'今日整理预算已用完',busy:'已有整理正在进行',context_changed:'整理期间上下文变化，本次未写入',model_unconfigured:'请先配置对话模型'};toast(labels[result.status]||`本次整理 ${result.evidence||0} 条原话，写入 ${result.accepted||0} 条认识`)}finally{el('personalityLearn').disabled=false}});
el('personalityFilter').onchange=()=>{if(personalityView)renderPersonalityNotes()};
el('personalityRefresh').onclick=managed(loadPersonality);
el('personalityExport').onclick=managed(()=>downloadFile('/api/personality/export','personality-base.json'));
el('personalityNewRole').onclick=managed(async()=>{if(!confirm('建立空白角色档案？现有人设不变，旧人格档案保留，学习会关闭。'))return;await api('/api/personality/new-role',{});await loadPersonality();toast('新档案已建立，可在上方切回旧档案')});
el('personalitySwitchRole').onclick=managed(async()=>{await api('/api/personality/switch-role',{role_id:el('personalityRoles').value});await loadPersonality();toast('已切换档案，经历学习暂时关闭，请核对人设后再启用')});
el('personalityClear').onclick=managed(async()=>{if(!confirm('仅删除当前人格档案的学习认识、修订和待处理编号？聊天、人物关系、生活事件、世界书和底稿保留。'))return;await api('/api/personality/clear-learning',{});await loadPersonality();toast('学习结果已清空，重新从现在积累')});
el('personalityCoreHistory').onclick=managed(async()=>{const data=await api('/api/personality/core-versions');const root=el('personalityCoreVersions');root.replaceChildren();for(const row of data.versions){const snapshot=JSON.parse(row.snapshot);const card=node('details');card.append(node('summary',`底稿版本 ${row.revision} · ${row.cause}`),node('pre',snapshot.base.join('\n')));card.append(action('放入底稿编辑框',()=>{el('personalityBase').value=snapshot.base.join('\n');toast('旧版本已放入编辑框，审阅后保存')}));root.append(card)}});
for(const button of document.querySelectorAll('[data-tab]'))button.addEventListener('click',managed(async()=>{if(button.dataset.tab==='personality')await loadPersonality()}));
