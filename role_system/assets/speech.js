let speechSettings={};
async function loadSpeech(){
 const view=await api('/api/speech');speechSettings=view.settings;
 for(const k of ['enabled','private','group','send_text'])el('speech_'+k).checked=view.settings[k];
 el('speech_max_chars').value=view.settings.max_chars;
 const catalog=await api('/api/model-management'),select=el('speech_provider_id');select.replaceChildren();
 const empty=node('option','请选择语音合成模型');empty.value='';select.append(empty);
 for(const model of catalog.models.filter(m=>m.category==='tts')){const item=node('option',model.name||model.model||model.id);item.value=model.id;select.append(item)}
 select.value=view.settings.provider_id;el('speech_state').textContent=view.ready?'语音模型已配置，角色可自行选择发送语音':'请先在模型管理配置语音合成模型，再在此选择';
}
el('speechForm').onsubmit=managed(async event=>{event.preventDefault();const values={...speechSettings};
 for(const k of ['enabled','private','group','send_text'])values[k]=el('speech_'+k).checked;
 values.max_chars=Number(el('speech_max_chars').value);values.provider_id=el('speech_provider_id').value;
 await api('/api/speech/settings',values);await loadSpeech();toast('语音设置已生效');
});
for(const button of document.querySelectorAll('[data-tab]'))button.addEventListener('click',managed(async()=>{if(button.dataset.tab==='speech')await loadSpeech()}));
