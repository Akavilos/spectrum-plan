/* 管理操作共用登录状态，编辑先留在页面，保存后才影响业务。 */
const managed = (fn) => async (...args) => {try {await fn(...args)} catch (error) {toast(error.message)}};
const node = (tag, text, className) => {const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(className)n.className=className;return n};
function action(text, fn){const b=node('button',text,'secondary');b.type='button';b.onclick=managed(fn);return b}
function field(parent,label,value,type='text'){parent.append(node('label',label));const input=document.createElement(type==='textarea'?'textarea':'input');if(type!=='textarea')input.type=type;if(type==='checkbox')input.checked=!!value;else input.value=value??'';parent.append(input);return input}
async function downloadFile(path,name){const response=await fetch(path);if(!response.ok){const value=await response.json();throw Error(value.detail||'下载失败')}const url=URL.createObjectURL(await response.blob());const a=node('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
async function readJSON(input){if(!input.files[0])throw Error('先选择 JSON 文件');return JSON.parse(await input.files[0].text())}
async function restartPlatform(){await api('/api/restart',{});toast('正在重启，稍后刷新页面');setTimeout(()=>location.reload(),5000)}

async function loadPlugins(){
 const data=await api('/api/extensions'),root=el('pluginList');
 const expanded=new Set([...root.querySelectorAll('details.plugin-card')].filter(card=>card.open).map(card=>card.dataset.pluginId));root.replaceChildren();
 if(!data.plugins.length)root.append(node('p','尚未安装额外插件。光谱核心、棱镜核心与投递哨兵在功能设置中管理。','hint'));
 for(const item of data.plugins){
  const disclosure=node('details',undefined,'plugin-card');disclosure.dataset.pluginId=item.id;disclosure.open=expanded.has(item.id);disclosure.append(node('summary',item.name));
  const card=node('div',undefined,'metric');card.append(node('p',`${item.kind==='nonebot'?'NoneBot 插件':'角色扩展模块'} · ${item.id} · ${item.loaded?'已加载':item.error?'加载失败':'未加载'}${item.pending_restart?' · 待重启':''}`,'hint'));
  if(item.description)card.append(node('p',item.description,'hint'));
  if(item.error)card.append(node('p',item.error,'notice'));
  const enabled=field(card,'启用插件（保存后重启生效）',item.enabled,'checkbox');
  const inputs={};let raw;
  if(Object.keys(item.schema||{}).length){
   let group;
   for(const[key,definition]of Object.entries(item.schema)){
    if(definition.group&&definition.group!==group){group=definition.group;card.append(node('h3',group));}
    const value=item.settings[key]??definition.default;
    if(definition.enum){card.append(node('label',definition.title||key));const select=node('select');for(const [index,option] of definition.enum.entries()){const opt=node('option',definition.labels?.[index]||String(option));opt.value=String(option);select.append(opt)}select.value=String(value);card.append(select);inputs[key]={input:select,definition}}
    else{const type=definition.type==='boolean'?'checkbox':['integer','number'].includes(definition.type)?'number':definition.type==='text'||['array','object'].includes(definition.type)?'textarea':'string';
     const input=field(card,definition.title||key,['array','object'].includes(definition.type)?JSON.stringify(value??(definition.type==='array'?[]:{}),null,2):value,definition.secret?'password':type==='string'?'text':type);
     if(definition.minimum!==undefined)input.min=definition.minimum;if(definition.maximum!==undefined)input.max=definition.maximum;
     inputs[key]={input,definition};}
    if(definition.description)card.append(node('p',definition.description,'hint'));
   }
  }else{raw=field(card,'插件设置（JSON 对象）',JSON.stringify(item.settings,null,2),'textarea');card.append(node('p','填写此插件文档声明的配置项。NoneBot 原生插件的设置在重启时写入启动配置。','hint'))}
  const buttons=node('div',undefined,'actions');
  buttons.append(action('保存插件设置',async()=>{let settings=raw?JSON.parse(raw.value):{...item.settings};for(const[key,{input,definition}]of Object.entries(inputs)){settings[key]=definition.enum?definition.enum.find(x=>String(x)===input.value):definition.type==='boolean'?input.checked:['integer','number'].includes(definition.type)?Number(input.value):['array','object'].includes(definition.type)?JSON.parse(input.value):input.value}await api('/api/extensions/'+item.id,{enabled:enabled.checked,settings});toast('已保存，重启后生效');await loadPlugins()}),action('卸载插件',async()=>{await api('/api/extensions/'+item.id,{action:'remove'});await loadPlugins();toast('代码已卸载；独立设置和数据仍然保留')}));
  card.append(buttons);disclosure.append(card);root.append(disclosure);
 }
}
el('packageForm').onsubmit=managed(async e=>{e.preventDefault();const b=el('packageInstall');b.disabled=true;try{await api('/api/extensions/install',{id:el('packageId').value,name:el('packageName').value,package:el('packageSpec').value,entry:el('packageEntry').value});toast('安装完成，配置后启用并重启');await loadPlugins()}finally{b.disabled=false}});
el('zipInstall').onclick=managed(async()=>{const file=el('pluginZip').files[0];if(!file)throw Error('选择带 manifest.json 的 ZIP 插件包');const b=el('zipInstall');b.disabled=true;try{const response=await fetch('/api/extensions/install',{method:'POST',headers:{'Content-Type':'application/zip'},body:await file.arrayBuffer()});const body=await response.json();if(!response.ok)throw Error(body.detail);toast(body.message);await loadPlugins()}finally{b.disabled=false}});
el('pluginRestart').onclick=managed(restartPlatform);

let regexRules=[],regexPresets=[];
function renderRules(){
 const root=el('regexList');root.replaceChildren();
 regexRules.forEach((rule,index)=>{
  const card=node('details');const title=node('summary',`${index+1}. ${rule.name||'新规则'}${rule.enabled?' · 已启用':' · 已关闭'}`);card.append(title);
  const enabled=field(card,'启用这条规则',rule.enabled,'checkbox');enabled.onchange=()=>{rule.enabled=enabled.checked;title.textContent=`${index+1}. ${rule.name||'新规则'}${rule.enabled?' · 已启用':' · 已关闭'}`};
  const modeLabel=node('label','处理方式');const mode=node('select');for(const[value,label]of [['replace','替换：保留句子，改写匹配措辞'],['trim','裁剪：删除匹配部分']]){const option=node('option',label);option.value=value;mode.append(option)}mode.value=rule.operation|| (rule.replacement?'replace':'trim');card.append(modeLabel,mode);mode.onchange=()=>{rule.operation=mode.value;const replacement=card.querySelector('[data-replacement]');replacement.disabled=mode.value==='trim';replacement.previousElementSibling.textContent=mode.value==='trim'?'替换内容（裁剪模式不使用）':'替换内容（支持 $1、$&、命名捕获组）'};
  for(const[key,label,type]of [['name','规则名称','text'],['pattern','查找表达式','textarea'],['flags','标志（g 全部替换，i 忽略大小写，m 多行，s 跨行，u Unicode）','text'],['replacement','替换内容（留空表示删除，支持 $1、$& 和 {{match}}）','textarea']]){const input=field(card,label,rule[key],type);if(key==='replacement'){input.dataset.replacement='true';input.disabled=mode.value==='trim';input.previousElementSibling.textContent=mode.value==='trim'?'替换内容（裁剪模式不使用）':'替换内容（支持 $1、$&、命名捕获组）'}input.oninput=()=>{rule[key]=input.value;title.textContent=`${index+1}. ${rule.name||'新规则'}${rule.enabled?' · 已启用':' · 已关闭'}`}}
  const safe=field(card,'仅调整措辞与格式，保留原回复的承诺登记（删除实质内容时请关闭）',rule.preserve_communication,'checkbox');safe.onchange=()=>rule.preserve_communication=safe.checked;
  if(rule.description)card.append(node('p',rule.description,'hint'));
  if(rule.source){const a=node('a','参考来源','hint');if(/^https?:\/\//.test(rule.source)){a.href=rule.source;a.target='_blank';a.rel='noopener';card.append(a)}}
  const buttons=node('div',undefined,'actions');buttons.append(action('上移',()=>{if(index){[regexRules[index-1],regexRules[index]]=[regexRules[index],regexRules[index-1]];renderRules()}}),action('下移',()=>{if(index<regexRules.length-1){[regexRules[index+1],regexRules[index]]=[regexRules[index],regexRules[index+1]];renderRules()}}),action('删除',()=>{regexRules.splice(index,1);renderRules()}));card.append(buttons);root.append(card);
 });
}
async function loadRegex(){const data=await api('/api/regex');regexRules=data.rules;regexPresets=data.presets;el('regexEnabled').checked=data.enabled;renderRules()}
el('regexAdd').onclick=()=>{regexRules.push({name:'新规则',pattern:'',flags:'g',operation:'replace',replacement:'',enabled:false,preserve_communication:false});renderRules();el('regexList').lastChild.open=true};
function addPresets(operation){let count=0;for(const preset of regexPresets.filter(row=>!operation||row.operation===operation)){if(!regexRules.some(r=>r.id===preset.id)){regexRules.push({...preset});count++}}return count}
el('regexPresets').onclick=()=>{const count=addPresets();renderRules();toast(count?'预设已加入，默认关闭；保存后生效':'预设已在列表中，现有编辑不会被覆盖')};
el('regexRecommended').onclick=()=>{addPresets('replace');const selected=new Set(regexPresets.filter(r=>r.recommended&&r.operation==='replace').map(r=>r.id));for(const rule of regexRules){if(selected.has(rule.id))rule.enabled=true}el('regexEnabled').checked=true;renderRules();toast('已勾选基础替换；保留其他开关，先预览再保存')};
el('regexTrimPresets').onclick=()=>{const count=addPresets('trim');renderRules();toast(count?'裁剪预设已补入，默认关闭':'裁剪预设已在列表中，开关保持原状')};
el('regexSave').onclick=managed(async()=>{await api('/api/regex',{enabled:el('regexEnabled').checked,rules:regexRules});await loadRegex();toast('正则设置已生效')});
el('regexPreview').onclick=managed(async()=>{const data=await api('/api/regex/preview',{text:el('regexInput').value,rules:regexRules});el('regexOutput').value=data.text;el('regexHits').textContent=data.hits.length?data.hits.map(h=>`${h.name}（${h.operation==='trim'?'裁剪':'替换'}）：${h.error||h.matches+' 次匹配'}${h.before!==undefined?'\n原文：'+h.before+'\n结果：'+(h.after||'【空文本】'):''}`).join('\n\n'):'没有规则命中；预览只应用已勾选的规则，不受总开关影响'});
el('regexExport').onclick=managed(()=>downloadFile('/api/regex/export','output-regex.json'));
el('regexTavernExport').onclick=managed(()=>downloadFile('/api/regex/export?tavern=true','tavern-regex.json'));
el('regexImport').onclick=managed(async()=>{const result=await api('/api/regex/import',{data:await readJSON(el('regexFile')),replace:el('regexReplace').checked});await loadRegex();toast(`已导入 ${result.count} 条规则；总开关保留原状态`)});
el('dataExport').onclick=managed(()=>downloadFile('/api/data/export?section='+el('dataSection').value,'role-system-data.json'));
el('dataImport').onclick=managed(async()=>{const result=await api('/api/data/import',{data:await readJSON(el('dataFile'))});await load();toast(result.restart_required?'已导入，请重启平台使全部设置生效':'已导入并生效')});
el('dataBackup').onclick=managed(()=>downloadFile('/api/data/backup?include_secrets='+el('backupSecrets').checked,'role-system-backup.zip'));
el('dataHistory').onclick=managed(()=>downloadFile('/api/data/history','conversation.jsonl'));
el('dataRestart').onclick=managed(restartPlatform);
for(const b of document.querySelectorAll('[data-tab]'))b.addEventListener('click',managed(async()=>{if(b.dataset.tab==='plugins')await loadPlugins();if(b.dataset.tab==='regex')await loadRegex()}));

el('showPanelKey').onchange=()=>{for(const id of ['newPanelKey','confirmPanelKey'])el(id).type=el('showPanelKey').checked?'text':'password'};
el('accessForm').onsubmit=managed(async e=>{e.preventDefault();const result=await api('/api/access/key',{new_key:el('newPanelKey').value,confirm_key:el('confirmPanelKey').value});el('newPanelKey').value='';el('confirmPanelKey').value='';el('showPanelKey').checked=false;el('showPanelKey').onchange();el('accessResult').textContent=result.message;toast('管理密钥已保存')});
