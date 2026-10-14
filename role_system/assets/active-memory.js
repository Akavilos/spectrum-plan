/* 主动回忆只在角色调用工具时运行；此页面管理开关、索引与诊断。 */
let activeMemorySettings;
async function loadActiveMemory(){
 const view=await api('/api/active-memory');activeMemorySettings=view.settings;
 for(const key of ['enabled','prism','personality','vector_enabled'])el('am_'+key).checked=view.settings[key];
 for(const key of ['max_results','char_budget','vector_threshold','candidate_limit'])el('am_'+key).value=view.settings[key];
 const catalog=await api('/api/model-management');const select=el('am_embedding_provider_id');select.replaceChildren();
 const follow=node('option','跟随默认嵌入模型');follow.value='';select.append(follow);
 for(const model of catalog.models.filter(m=>m.category==='embedding')){const option=node('option',model.name||model.model||model.id);option.value=model.id;select.append(option)}
 select.value=view.settings.embedding_provider_id;
 const states={idle:'尚未准备',running:'正在准备',complete:'已完成',paused:'已暂停',unavailable:'本次未完成，基础搜索仍可使用'};
 el('am_index_state').textContent=`语义索引：${states[view.index.state]||view.index.state} · 本次 ${view.index.encoded}/${view.index.total} · 已缓存 ${view.cached_vectors} 条`;
 const root=el('am_diagnostics');root.replaceChildren();
 if(!view.recent.length)root.append(node('p','暂无主动回忆调用。角色可以选择直接回答。'));
 for(const item of view.recent){const card=node('details');card.append(node('summary',`${new Date(item.at*1000).toLocaleString()} · 找到 ${item.results.length} 件`));
  card.append(node('p',`会话 ${item.session} · 候选 ${item.candidates} · 可搜索 ${item.searchable} · ${item.elapsed_ms}毫秒`),node('p',item.semantic),node('p',`来源标识：${item.results.join('、')||'无'}`));root.append(card)}
}
el('activeMemoryForm').onsubmit=managed(async event=>{
 event.preventDefault();const values={...activeMemorySettings};
 for(const key of ['enabled','prism','personality','vector_enabled'])values[key]=el('am_'+key).checked;
 for(const key of ['max_results','char_budget','vector_threshold','candidate_limit'])values[key]=Number(el('am_'+key).value);
 values.embedding_provider_id=el('am_embedding_provider_id').value;
 await api('/api/active-memory/settings',values);await loadActiveMemory();toast('主动回忆设置已生效');
});
el('am_prepare').onclick=managed(async()=>{if(!confirm('准备语义索引会调用所选嵌入模型并计入请求限额，继续？'))return;await api('/api/active-memory/index',{});await loadActiveMemory();toast('索引准备已开始，可点击刷新查看进度')});
el('am_clear').onclick=managed(async()=>{if(!confirm('清空主动回忆的语义索引？原始记忆、聊天和设置保留。'))return;await api('/api/active-memory/index-clear',{confirm:true});await loadActiveMemory();toast('语义索引已清空')});
el('am_refresh').onclick=managed(loadActiveMemory);
for(const button of document.querySelectorAll('[data-tab]'))button.addEventListener('click',managed(async()=>{if(button.dataset.tab==='activeMemory')await loadActiveMemory()}));
