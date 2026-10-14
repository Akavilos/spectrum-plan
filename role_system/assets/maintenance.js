/* 维护范围先由服务器确认；取消或关闭窗口不会提交任何清理请求。 */
(() => {
  let prepared = null;
  let submitting = false;
  const dialog = el("maintenanceDialog");

  function selected() {
    const choices = {};
    for (const input of document.querySelectorAll("[data-maintenance-db]:checked")) {
      (choices[input.dataset.maintenanceDb] ??= []).push(input.value);
    }
    return choices;
  }

  function updateSelection() {
    const count = document.querySelectorAll("[data-maintenance-db]:checked").length;
    el("maintenanceClearSelected").disabled = count === 0;
    el("maintenanceSelection").textContent = count ? `已选 ${count} 类数据` : "尚未选择数据";
    for (const button of document.querySelectorAll("[data-maintenance-all]")) {
      const inputs = [...document.querySelectorAll(`[data-maintenance-db="${button.dataset.maintenanceAll}"]`)];
      button.textContent = inputs.every(input => input.checked) ? "取消全选" : "全选";
    }
  }

  async function loadMaintenance() {
    const data = await api("/api/maintenance");
    const previous = selected();
    const root = el("maintenanceDatabases");
    root.replaceChildren();
    for (const database of data.databases) {
      const card = document.createElement("article");
      card.className = "maintenance-db";
      const header = document.createElement("div");
      header.className = "maintenance-db-header";
      const title = document.createElement("h3");
      title.textContent = database.title;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "secondary maintenance-mini";
      button.dataset.maintenanceAll = database.id;
      button.setAttribute("aria-label", `全选${database.title}`);
      button.onclick = () => {
        const inputs = [...card.querySelectorAll("input")];
        const checked = !inputs.every(input => input.checked);
        inputs.forEach(input => { input.checked = checked; });
        updateSelection();
      };
      header.append(title, button);
      card.append(header);
      for (const group of database.groups) {
        const label = document.createElement("label");
        label.className = "maintenance-choice";
        const input = document.createElement("input");
        input.type = "checkbox";
        input.dataset.maintenanceDb = database.id;
        input.value = group.id;
        input.checked = previous[database.id]?.includes(group.id) || false;
        input.onchange = updateSelection;
        const text = document.createElement("span");
        text.textContent = group.label;
        const count = document.createElement("small");
        count.textContent = `${group.count} 条`;
        label.append(input, text, count);
        card.append(label);
      }
      root.append(card);
    }
    updateSelection();
    const result = data.last_result;
    el("maintenanceResult").textContent = data.pending ? "维护已确认，正在重启…" :
      result ? `${result.title}已完成 · 操作前备份：${result.backup}` : "";
  }

  async function prepare(action, choices) {
    if (submitting) return;
    try {
      prepared = await api("/api/maintenance/prepare", { action, selected: choices || {} });
      el("maintenanceTitle").textContent = prepared.title;
      const scope = el("maintenanceScope");
      scope.replaceChildren();
      for (const text of [...prepared.scope, ...prepared.notes]) {
        const item = document.createElement("li");
        item.textContent = text;
        scope.append(item);
      }
      el("maintenanceConfirmation").value = "";
      el("maintenanceConfirmation").placeholder = prepared.title;
      el("maintenanceExpected").textContent = `请输入“${prepared.title}”，再点击确认。`;
      el("maintenanceError").textContent = "";
      el("maintenanceCommit").disabled = true;
      dialog.showModal();
    } catch (error) { toast(error.message); }
  }

  el("maintenanceConfirmation").oninput = () => {
    el("maintenanceCommit").disabled = submitting || el("maintenanceConfirmation").value !== prepared?.title;
  };
  el("maintenanceCancel").onclick = () => dialog.close();
  dialog.addEventListener("close", () => { if (!submitting) prepared = null; });
  dialog.addEventListener("cancel", event => { if (submitting) event.preventDefault(); });
  el("maintenanceForm").onsubmit = async event => {
    event.preventDefault();
    if (submitting || !prepared || el("maintenanceConfirmation").value !== prepared.title) return;
    submitting = true;
    el("maintenanceCommit").disabled = true;
    el("maintenanceCancel").disabled = true;
    try {
      const result = await api("/api/maintenance/commit", {
        token: prepared.token, confirmation: el("maintenanceConfirmation").value,
      });
      el("maintenanceError").textContent = result.message;
      const started = Date.now();
      const poll = async () => {
        try {
          const data = await api("/api/maintenance");
          if (data.last_result?.id === result.id && data.last_result.ok) {
            location.reload();
            return;
          }
        } catch (_) { /* 重启时连接会短暂断开。 */ }
        if (Date.now() - started < 120000) setTimeout(poll, 1500);
        else {
          el("maintenanceError").textContent = "等待重启超时，请刷新页面查看运行日志。";
          el("maintenanceCancel").disabled = false;
          submitting = false;
        }
      };
      setTimeout(poll, 1500);
    } catch (error) {
      submitting = false;
      el("maintenanceError").textContent = error.message;
      el("maintenanceCancel").disabled = false;
      el("maintenanceConfirmation").oninput();
    }
  };
  el("maintenanceClearSelected").onclick = () => prepare("databases", selected());
  el("maintenanceClearAll").onclick = async () => {
    try {
      const data = await api("/api/maintenance");
      const choices = Object.fromEntries(data.databases.map(db => [db.id, db.groups.map(group => group.id)]));
      await prepare("databases", choices);
    } catch (error) { toast(error.message); }
  };
  for (const button of document.querySelectorAll("[data-maintenance-action]")) {
    button.onclick = () => prepare(button.dataset.maintenanceAction);
  }
  el("maintenanceRefresh").onclick = () => loadMaintenance().catch(error => toast(error.message));
  for (const button of document.querySelectorAll("[data-tab='data']")) {
    button.addEventListener("click", () => loadMaintenance().catch(error => toast(error.message)));
  }
})();
