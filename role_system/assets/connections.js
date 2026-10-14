/* 接入参数只在重启时交给公开适配器接口；在线账号来自实际连接。 */
let connectionView;
function connectionAddress() {
 const place = el('connectionLocation').value;
 return place === 'container' ? connectionView.container_url : `${location.protocol === 'https:' ? 'wss' : 'ws'}://${el('connectionHost').value.trim()}${connectionView.reverse_path}`;
}
function renderConnectionHelp() {
 const forward = el('connectionMode').value === 'forward_ws';
 el('forwardConnection').classList.toggle('hidden', !forward);
 el('reverseConnection').classList.toggle('hidden', forward);
 el('connectionHostRow').classList.toggle('hidden', el('connectionLocation').value === 'container');
 el('connectionAddress').value = connectionAddress();
 el('connectionGuide').textContent = forward ? '在客户端开启 OneBot v11 的 WebSocket 服务端，将地址和相同令牌填在本页。平台重启后主动连接。Docker 中的 localhost 指当前容器。' : '在客户端的网络配置中新建 WebSocket 客户端，填写下面的地址和相同令牌，保存并启用。客户端还需要先登录机器人账号。';
 el('connectionExport').disabled = forward || el('connectionClient').value !== 'napcat';
}
async function loadConnections() {
 connectionView = await api('/api/connections'); const values = connectionView.settings;
 for (const [id, label] of Object.entries(connectionView.clients)) {
  if (!el('connectionClient').querySelector(`option[value="${id}"]`)) el('connectionClient').append(new Option(label, id));
 }
 el('connectionClient').value = values.client; el('connectionMode').value = values.mode;
 el('connectionWsUrl').value = values.ws_url; el('connectionToken').value = '';
 el('connectionToken').placeholder = connectionView.has_token ? '令牌已保存；留空保留' : '双方未设认证时可留空';
 if (!el('connectionHost').value) el('connectionHost').value = location.host;
 el('connectionState').textContent = connectionView.restart_required ? '新连接设置已保存，等待重启生效；当前连接仍使用原设置。' : connectionView.bots.length ? `已连接账号：${connectionView.bots.join('、')}` : '尚未连接。检查客户端登录状态、地址、令牌，以及网络端口是否可达。';
 renderConnectionHelp();
}
for (const id of ['connectionMode', 'connectionClient', 'connectionLocation', 'connectionHost']) el(id).onchange = renderConnectionHelp;
el('connectionForm').onsubmit = managed(async event => {
 event.preventDefault(); await api('/api/connections', {client: el('connectionClient').value, mode: el('connectionMode').value, ws_url: el('connectionWsUrl').value, access_token: el('connectionToken').value});
 el('connectionToken').value = ''; await loadConnections(); toast('连接设置已保存，重启后生效');
});
el('connectionRefresh').onclick = managed(loadConnections);
el('connectionRestart').onclick = managed(restartPlatform);
el('connectionShowToken').onclick = managed(async () => {
 el('connectionToken').value = (await api('/api/connections/token')).access_token;
 el('connectionToken').type = 'text'; toast('令牌已显示，复制到客户端后可点击隐藏');
});
el('connectionHideToken').onclick = () => { el('connectionToken').type = 'password'; el('connectionToken').value = ''; };
el('connectionCopy').onclick = managed(async () => {
 const input = el('connectionAddress');
 if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(input.value); toast('接入地址已复制'); }
 else { input.focus(); input.select(); toast('地址已选中，按 Ctrl+C 复制'); }
});
el('connectionExport').onclick = managed(async () => {
 if (connectionView.restart_required) throw Error('请先重启使保存的连接设置生效，再导出');
 const token = (await api('/api/connections/token')).access_token;
 const network = {httpServers: [], httpSseServers: [], httpClients: [], websocketServers: [],
  websocketClients: [{enable: true, name: 'spectrum-project', url: connectionAddress(), token, reportSelfMessage: false, messagePostFormat: 'array', debug: false, heartInterval: 30000, reconnectInterval: 5000}], plugins: []};
 const url = URL.createObjectURL(new Blob([JSON.stringify({network, musicSignUrl: '', enableLocalFile2Url: false, parseMultMsg: false}, null, 2)], {type: 'application/json'}));
 const link = document.createElement('a'); link.href = url; link.download = 'onebot11.json'; link.click(); URL.revokeObjectURL(url);
});
for (const button of document.querySelectorAll('[data-tab]')) button.addEventListener('click', managed(async () => { if (button.dataset.tab === 'connections') await loadConnections(); }));
