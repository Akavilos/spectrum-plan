/* 数据列表共用的选择状态：选择快照由明确编号组成，不随新记录自动扩大。 */
class RecordSelection {
  constructor(){this.mode=false;this.ids=new Set();this.busy=false;this.revision=0;}
  get count(){return this.ids.size;}
  has(key){return this.ids.has(String(key));}
  enter(){this.mode=true;}
  toggle(key,on){this.revision++;key=String(key);if(on===undefined)on=!this.ids.has(key);if(on)this.ids.add(key);else this.ids.delete(key);}
  add(keys){this.revision++;this.mode=true;for(const key of keys)this.ids.add(String(key));}
  replace(keys){this.revision++;this.mode=true;this.ids=new Set(keys.map(String));}
  clear(){this.revision++;this.ids.clear();}
  exit(){this.clear();this.mode=false;}
}
window.RecordSelection=RecordSelection;
window.recordRequest=async(path,body)=>{
  if(window.RoleModulePage){const r=await window.RoleModulePage.apiPost('/api/records/'+path,body);if(r.ok===false)throw Error(r.message||r.detail||r.error);return r;}
  const r=await fetch('/api/records/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const data=await r.json();if(!r.ok)throw Error(data.detail||'数据操作失败');return data;
};
