/* 统一的聊天、投递和人格数据列表；删除须经范围确认。 */
(() => {
  const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  class RecordWorkbench {
    constructor(root,resources){
      this.root=root;this.resources=resources;this.resource=resources[0][0];this.page=0;this.search='';this.rows=[];this.total=0;this.epoch=0;this.selection=new RecordSelection();
      root.innerHTML=`<div class="data-toolbar"><select class="records-type" aria-label="数据类型">${resources.map(([key,label])=>`<option value="${key}">${escape(label)}</option>`).join('')}</select><input class="records-search" aria-label="搜索全部记录" placeholder="搜索记录"><button type="button" data-action="refresh">刷新</button></div><div class="records-selection"></div><div class="records-list"></div><div class="record-pager"></div><dialog class="confirm-dialog"><h3>删除所选记录</h3><p class="record-impact"></p><div class="actions"><button type="button" data-action="cancel-delete">取消</button><button type="button" data-action="confirm-delete" class="btn btn-danger">确认删除</button></div></dialog><p class="records-message hint" role="status"></p>`;
      root.classList.add('record-workbench');
      root.querySelector('dialog').addEventListener('close',()=>{this.pending=null;});
      root.addEventListener('click',e=>{const target=e.target.closest('[data-action]');if(target)this.action(target.dataset.action).catch(error=>this.message(error.message));});
      root.addEventListener('change',e=>{if(e.target.matches('.record-select')){this.selection.toggle(e.target.dataset.key,e.target.checked);this.toolbar();}});
      root.querySelector('select').onchange=e=>{this.resource=e.target.value;this.page=0;this.selection.exit();this.rows=[];this.total=0;this.paint();this.load().catch(error=>this.message(error.message));};
      let timer;root.querySelector('.records-search').oninput=e=>{this.search=e.target.value;this.page=0;this.selection.exit();this.epoch++;this.rows=[];this.paint();clearTimeout(timer);timer=setTimeout(()=>this.load().catch(error=>this.message(error.message)),250);};
    }
    message(text){this.root.querySelector('.records-message').textContent=text;}
    toolbar(){const s=this.selection;this.root.querySelector('.records-selection').innerHTML=`<div class="selection-bar">${s.mode?`<button type="button" data-action="page">全选当前页</button><button type="button" data-action="all">全选筛选结果</button><button type="button" data-action="clear">清空选择</button><button type="button" data-action="delete" class="btn btn-danger" ${s.count?'':'disabled'}>删除所选</button><button type="button" data-action="exit">取消</button><span class="selection-count">已选 ${s.count} 条</span>`:'<button type="button" data-action="select">选择</button><span class="selection-count">'+this.total+' 条记录</span>'}</div>`;}
    paint(){
      this.toolbar();const s=this.selection;
      this.root.querySelector('.records-list').innerHTML=this.rows.length?`<div class="table-scroll"><table><thead><tr><th>记录</th><th>内容</th></tr></thead><tbody>${this.rows.map(row=>{
        const summary=row.content||row.text||row.impression||row.topic||row.name||row.value||row.person_id||row.id||row._key;
        return `<tr><td>${s.mode?`<input type="checkbox" class="record-select" data-key="${escape(row._key)}" aria-label="选择记录 ${escape(row._key)}" ${s.has(row._key)?'checked':''}>`:''}${escape(row.id||row.person_id||row.umo||row._key)}</td><td class="record-cells"><details><summary>${escape(String(summary).slice(0,150))}</summary><pre>${escape(JSON.stringify(Object.fromEntries(Object.entries(row).filter(([k])=>k!=='_key')),null,2))}</pre></details></td></tr>`;
      }).join('')}</tbody></table></div>`:'<p class="hint">没有匹配的记录。</p>';
      this.root.querySelector('.record-pager').innerHTML=`<button type="button" data-action="previous" ${this.page?'':'disabled'}>上一页</button><span>${this.page+1} / ${Math.max(1,Math.ceil(this.total/50))}</span><button type="button" data-action="next" ${(this.page+1)*50<this.total?'':'disabled'}>下一页</button>`;
    }
    async load(){const epoch=++this.epoch;const data=await recordRequest('query',{resource:this.resource,filters:{search:this.search,history:true},offset:this.page*50,limit:50});if(epoch!==this.epoch)return;this.rows=data.items;this.total=data.total;this.description=data;this.paint();}
    async action(action){
      const s=this.selection;
      if(s.busy)return;
      if(action==='select')s.enter();
      else if(action==='exit')s.exit();
      else if(action==='clear')s.clear();
      else if(action==='page')s.add(this.rows.map(r=>r._key));
      else if(action==='all'){
        const scope=this.resource,search=this.search,revision=s.revision;
        s.busy=true;try{const data=await recordRequest('query',{resource:scope,filters:{search,history:true},select_all:true});if(revision===s.revision&&scope===this.resource&&search===this.search)s.replace(data.ids);}finally{s.busy=false;}
      }else if(action==='delete'){
        if(!s.count)return;
        this.pending={resource:this.resource,ids:[...s.ids]};
        this.root.querySelector('.record-impact').textContent=`删除 ${this.description.title} 中选中的 ${s.count} 条？${this.description.impact}`;
        this.root.querySelector('dialog').showModal();return;
      }else if(action==='cancel-delete'){this.pending=null;this.root.querySelector('dialog').close();return;}
      else if(action==='confirm-delete'){
        if(!this.pending)return;s.busy=true;
        try{const data=await recordRequest('delete',{...this.pending,confirmed:true});this.pending=null;this.root.querySelector('dialog').close();s.exit();this.message(`已删除 ${data.deleted} 条${data.missing?'，'+data.missing+' 条已不存在或不属于当前范围':''}`);await this.load();window.dispatchEvent(new CustomEvent('records-deleted',{detail:data}));}finally{s.busy=false;}return;
      }else if(action==='next'){this.page++;await this.load();return;}
      else if(action==='previous'){this.page=Math.max(0,this.page-1);await this.load();return;}
      else if(action==='refresh'){await this.load();return;}
      this.paint();
    }
  }
  window.RecordWorkbench=RecordWorkbench;
  const widgets=new Map();
  const types={
    conversation:[['conversation.messages','平台聊天记录']],
    guard:[['guard.outbox','待补发消息'],['guard.inbox','缺席来信'],['guard.state','送达状态'],['guard.usage','发送用量']],
    personality:[['personality.insights','当前角色的经历认识'],['personality.roles','其他角色档案']],
  };
  window.loadRecordWorkbench=async type=>{
    const root=document.getElementById(type+'Records');if(!root)return;
    if(!widgets.has(type))widgets.set(type,new RecordWorkbench(root,types[type]));
    await widgets.get(type).load();
  };
  for(const button of document.querySelectorAll('[data-tab]'))button.addEventListener('click',()=>{
    if(button.dataset.tab==='data')loadRecordWorkbench('conversation').catch(error=>console.error(error));
    if(button.dataset.tab==='guard')loadRecordWorkbench('guard').catch(error=>console.error(error));
    if(button.dataset.tab==='personality')loadRecordWorkbench('personality').catch(error=>console.error(error));
  });
  window.addEventListener('records-deleted',event=>{
    if(event.detail.resource.startsWith('personality.')&&typeof loadPersonality==='function')loadPersonality().catch(console.error);
    if(event.detail.resource.startsWith('guard.')&&typeof showGuard==='function')showGuard().catch(console.error);
    if(typeof status==='function')status().catch(console.error);
  });
})();
