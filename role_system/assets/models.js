/* 保存与读取不调用模型；列表获取及付费测试由按钮触发。 */
let modelManager,
  activeSource = "",
  activeCategory = "chat",
  sourceModified = false,
  draftSource = null,
  editingModelId = "",
  populatedSource = "";
const md = (id) => el("md_" + id);
const mn = (tag, text, cls) => {
  const n = document.createElement(tag);
  if (text !== undefined) n.textContent = text;
  if (cls) n.className = cls;
  return n;
};
const mb = (label, fn, cls = "secondary") => {
  const b = mn("button", label, cls);
  b.type = "button";
  b.onclick = managed(fn);
  return b;
};
function fillProviderSelect(select, value = "", follow = true) {
  const opts = [new Option(follow ? "跟随对话模型" : "原有默认模型", "")];
  if (follow) opts.push(new Option("原有默认模型（固定使用）", "role"));
  for (const [id, m] of Object.entries(config.system.providers))
    if ((m.category || "chat") === "chat")
      opts.push(
        new Option(
          (m.name || id) +
            " · " +
            m.model +
            (m.enabled === false ? "（已关闭）" : ""),
          id,
        ),
      );
  if (value === "default") value = follow ? "role" : "";
  if (value === "role" && !follow) value = "";
  select.replaceChildren(...opts);
  select.value = value;
}
async function refreshModels() {
  modelManager = await api("/api/model-management");
  config = await api("/api/config");
  renderModels();
  fillProviderSelect(
    el("chat_provider_id"),
    config.system.chat_provider_id || "",
    false,
  );
}
function loadModels() {
  fillProviderSelect(
    el("chat_provider_id"),
    config.system.chat_provider_id || "",
    false,
  );
  refreshModels().catch((e) => toast(e.message));
}
const modelKeys = [
  "source_id",
  "model",
  "name",
  "enabled",
  "category",
  "temperature",
  "max_tokens",
  "context_window",
  "modalities",
  "parameters",
  "family",
  "stream",
  "prompt_cache",
];
const cleanModel = (m) =>
  Object.fromEntries(
    modelKeys.filter((k) => m[k] !== undefined).map((k) => [k, m[k]]),
  );
function resultView(result) {
  const box = mn(
    "div",
    undefined,
    "model-result " + (result.ok ? "success" : "failure"),
  );
  box.append(
    mn(
      "strong",
      (result.ok ? "测试通过" : "测试失败") +
        " · " +
        (result.latency_ms ?? "—") +
        " ms",
    ),
  );
  box.append(mn("p", result.message));
  if (result.dimension) box.append(mn("p", "向量维度：" + result.dimension));
  if (result.audio) {
    const audio = mn("audio");
    audio.controls = true;
    audio.src =
      "data:audio/" +
      (result.audio_format === "mp3" ? "mpeg" : result.audio_format || "mpeg") +
      ";base64," +
      result.audio;
    box.append(audio);
  }
  if (result.preview || result.results || result.detail || result.endpoint) {
    const details = mn("details");
    details.append(mn("summary", result.ok ? "查看测试结果" : "查看错误详情"));
    const lines = [
      result.preview,
      result.results ? JSON.stringify(result.results, null, 2) : "",
      result.status ? "HTTP " + result.status : "",
      result.endpoint,
      result.detail,
    ];
    details.append(mn("pre", lines.filter(Boolean).join("\n")));
    box.append(details);
  }
  return box;
}

function sourceLabel(id, source) {
  if (source.name && !source.name.startsWith("原有接口 · ")) return source.name;
  const template = modelManager.templates.find(
    (t) => t.base_url && source.base_url?.startsWith(t.base_url),
  );
  if (template) return templateLabel(template);
  const models = modelManager.models.filter((m) => m.source_id === id);
  if (models.some((m) => /^gemini/i.test(m.model))) return "Gemini";
  return source.name || id;
}

const protocolNames = {
  openai: "OpenAI",
  responses: "OpenAI Responses",
  anthropic: "Anthropic",
  gemini: "Gemini",
  ollama: "Ollama",
  azure: "Azure OpenAI",
};
const typeNames = {
  chat: "对话模型",
  stt: "语音识别",
  tts: "语音合成",
  embedding: "嵌入模型",
  rerank: "重排序",
};
const drawings = {
  test: "M5 8h4v8H5z M15 8h4v8h-4z M9 12h6 M7 4v4 M17 16v4",
  settings:
    "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M12 3v2 M12 19v2 M3 12h2 M19 12h2 M5.6 5.6l1.4 1.4 M17 17l1.4 1.4 M5.6 18.4L7 17 M17 7l1.4-1.4",
  trash: "M4 7h16 M9 7V4h6v3 M7 7l1 13h8l1-13 M10 10v7 M14 10v7",
  plus: "M12 5v14 M5 12h14",
  chat: "M4 5h16v11H9l-5 4z",
  stt: "M9 4a3 3 0 0 1 6 0v7a3 3 0 0 1-6 0z M6 10v2a6 6 0 0 0 12 0v-2 M12 18v4 M9 22h6",
  tts: "M4 9h4l5-4v14l-5-4H4z M17 8a6 6 0 0 1 0 8",
  embedding: "M12 3l9 5-9 5-9-5z M3 12l9 5 9-5 M3 16l9 5 9-5",
  rerank: "M5 6h14 M5 12h10 M5 18h6",
};
function drawing(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.6");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(svg.namespaceURI, "path");
  path.setAttribute("d", drawings[name] || drawings.settings);
  svg.append(path);
  return svg;
}
function iconButton(label, name, fn) {
  const b = mb("", fn, "ms-icon");
  b.title = label;
  b.setAttribute("aria-label", label);
  b.append(drawing(name));
  return b;
}
function markSourceModified(event) {
  if (event?.target?.id === "md_key_show") return;
  sourceModified = true;
  md("save_source").disabled = false;
  md("fetch").textContent = "保存并获取模型";
  md("enable_source").textContent = "保存并启用连接";
}
function chooseSource(id) {
  if (id === activeSource && !draftSource) return;
  if (sourceModified && !confirm("连接设置尚未保存，放弃修改并切换？")) return;
  activeSource = id;
  draftSource = null;
  sourceModified = false;
  renderModels();
}
async function deleteSource(id) {
  if (!confirm("删除此提供商及其未使用的模型？")) return;
  await api("/api/model-sources/delete", { id });
  if (id === activeSource) {
    activeSource = "";
    sourceModified = false;
    draftSource = null;
  }
  await refreshModels();
  renderFields();
}
function renderModels() {
  const entries = Object.entries(modelManager.sources);
  if (!draftSource && !modelManager.sources[activeSource])
    activeSource = entries[0]?.[0] || "";
  md("sources").replaceChildren();
  for (const [id, source] of entries) {
    const item = mn(
      "div",
      undefined,
      "ms-source-item" + (id === activeSource ? " selected" : ""),
    );
    const pick = mb("", () => chooseSource(id), "ms-source-item__content");
    pick.setAttribute("aria-pressed", String(id === activeSource));
    const avatar = mn(
      "span",
      sourceLabel(id, source).slice(0, 1),
      "ms-source-avatar",
    );
    const text = mn("div");
    text.append(
      mn(
        "div",
        sourceLabel(id, source) + (source.enabled === false ? " · 已关闭" : ""),
        "ms-source-item__title",
      ),
      mn(
        "div",
        source.base_url || protocolNames[source.protocol || "openai"],
        "ms-source-item__subtitle",
      ),
    );
    pick.append(avatar, text);
    item.append(
      pick,
      iconButton("删除提供商 " + sourceLabel(id, source), "trash", () =>
        deleteSource(id),
      ),
    );
    md("sources").append(item);
  }
  md("mobile_source").replaceChildren(
    ...entries.map(([id, source]) => new Option(sourceLabel(id, source), id)),
  );
  md("mobile_source").value = activeSource;
  md("categories").replaceChildren();
  for (const [id, label] of Object.entries(typeNames)) {
    const b = mb(
      "",
      () => {
        activeCategory = id;
        renderModels();
      },
      "ms-tab" + (id === activeCategory ? " active" : ""),
    );
    b.append(drawing(id), document.createTextNode(label));
    b.setAttribute("role", "tab");
    b.setAttribute("aria-selected", String(id === activeCategory));
    md("categories").append(b);
  }
  const source = draftSource || modelManager.sources[activeSource];
  md("provider_shell").classList.toggle("hidden", !source);
  md("provider_empty").classList.toggle("hidden", !!source);
  if (source) {
    md("source_title").textContent = sourceLabel(activeSource, source);
    md("source_description").textContent = source.base_url || "";
    if (!sourceModified) populateSource(activeSource, source);
  }
  md("source_state").classList.toggle("hidden", source?.enabled !== false);
  md("enable_source").classList.toggle("hidden", source?.enabled !== false);
  md("enable_source").textContent = sourceModified
    ? "保存并启用连接"
    : "启用连接";
  md("save_source").disabled = !sourceModified;
  md("fetch").disabled = !source;
  md("custom").disabled = !source || !!draftSource;
  md("fetch").textContent = sourceModified ? "保存并获取模型" : "获取模型列表";
  const search = md("search").value.trim().toLowerCase(),
    matches = (m) =>
      [m.id, m.model, m.name].some((v) =>
        (v || "").toLowerCase().includes(search),
      );
  const configured = modelManager.models
    .filter(
      (m) =>
        m.source_id === activeSource &&
        (m.category || "chat") === activeCategory,
    )
    .filter(matches);
  const saved = new Set(
    modelManager.models
      .filter(
        (m) =>
          m.source_id === activeSource &&
          (m.category || "chat") === activeCategory,
      )
      .map((m) => m.model),
  );
  const available = (modelManager.lists[activeSource] || [])
    .filter((m) => !saved.has(m.model))
    .filter(matches);
  md("model_list").replaceChildren();
  md("available_list").replaceChildren();
  md("configured_count").textContent = configured.length;
  md("available_count").textContent = available.length;
  md("list_count").textContent =
    "可用模型 " + (modelManager.lists[activeSource] || []).length;
  for (const model of configured) renderModelRow(model, false);
  for (const model of available) renderModelRow(model, true);
  if (!configured.length)
    md("model_list").append(
      mn("div", search ? "没有匹配的模型" : "尚未配置模型", "ms-models-empty"),
    );
  if (!available.length)
    md("available_list").append(
      mn(
        "div",
        search ? "没有匹配的模型" : "暂无可添加的模型",
        "ms-models-empty",
      ),
    );
  md("embedding_default").replaceChildren(
    new Option(
      config.system.embedding.enabled ? "原有嵌入配置" : "不使用嵌入模型",
      "",
    ),
    ...modelManager.models
      .filter((m) => m.category === "embedding")
      .map((m) => new Option((m.name || m.id) + " · " + m.model, m.id)),
  );
  md("embedding_default").value = modelManager.embedding_provider_id || "";
}
function renderModelRow(model, available) {
  const row = mn("article", undefined, "ms-model-row"),
    info = mb(
      "",
      () =>
        available
          ? editModel("", {
              source_id: activeSource,
              model: model.model,
              name: model.name,
              category: activeCategory,
              context_window: model.context_window,
              max_tokens: model.max_tokens,
            })
          : editModel(model.id),
      "ms-model-row__main",
    );
  info.append(
    mn(
      "div",
      available ? model.model : model.name || model.model,
      "ms-model-row__title",
    ),
  );
  info.title = available ? model.model : model.id;
  if (!available) info.append(mn("div", model.model, "ms-model-row__subtitle"));
  const badges = mn("div", undefined, "ms-model-row__meta");
  for (const cap of model.modalities || [])
    badges.append(
      mn(
        "span",
        { image: "识图", audio: "音频", tool_use: "工具", reasoning: "思考" }[
          cap
        ],
        "ms-model-row__badge",
      ),
    );
  if (model.context_window)
    badges.append(
      mn(
        "span",
        new Intl.NumberFormat("zh-CN", {
          notation: "compact",
          maximumFractionDigits: 1,
        }).format(model.context_window) + " 上下文",
        "ms-model-row__badge",
      ),
    );
  if (model.id === modelManager.chat_provider_id)
    badges.append(mn("span", "对话默认", "ms-model-row__badge"));
  if (model.id === modelManager.embedding_provider_id)
    badges.append(mn("span", "嵌入默认", "ms-model-row__badge"));
  for (const [category, id] of Object.entries(modelManager.fallback_models)) {
    if (id === model.id)
      badges.append(mn("span", typeNames[category] + "备用", "ms-model-use"));
  }
  info.append(badges);
  if (!available && model.ready === false)
    info.append(mn("p", model.unavailable_reason, "ms-connection-warning"));
  if (!available && (model.uses || []).length) {
    const uses = mn("div", undefined, "ms-model-uses");
    uses.append(mn("span", "已分配给"));
    const names = { spectrum: "光谱核心", prism: "棱镜", guard: "投递哨兵" };
    for (const use of model.uses)
      uses.append(mn("span", names[use] || use, "ms-model-use"));
    info.append(uses);
  }
  row.append(info);
  const actions = mn("div", undefined, "ms-model-row__actions");
  if (available)
    actions.append(
      iconButton("添加模型 " + model.model, "plus", () =>
        editModel("", {
          source_id: activeSource,
          model: model.model,
          name: model.name,
          category: activeCategory,
          context_window: model.context_window,
          max_tokens: model.max_tokens,
        }),
      ),
    );
  else {
    const toggle = mn("label", undefined, "ms-switch"),
      check = mn("input");
    check.type = "checkbox";
    check.setAttribute("role", "switch");
    check.setAttribute("aria-label", "启用模型 " + model.model);
    check.checked = model.enabled !== false;
    check.onchange = managed(async () => {
      await api("/api/models/save", {
        id: model.id,
        model: { ...cleanModel(model), enabled: check.checked },
      });
      await refreshModels();
      renderFields();
    });
    toggle.append(check);
    actions.append(toggle);
    const test = iconButton("测试模型 " + model.model, "test", () =>
      testModel(model, row),
    );
    test.disabled = model.ready === false || model.enabled === false;
    actions.append(
      test,
      iconButton("配置模型 " + model.model, "settings", () =>
        editModel(model.id),
      ),
      iconButton("删除模型 " + model.model, "trash", async () => {
        if (!confirm("删除模型 " + model.id + "？")) return;
        await api("/api/models/delete", { id: model.id });
        await refreshModels();
        renderFields();
      }),
    );
  }
  row.append(actions);
  if (!available && model.test) row.append(resultView(model.test));
  md(available ? "available_list" : "model_list").append(row);
}
async function testModel(m, card) {
  let audio;
  if (m.category === "stt") {
    const f = md("audio").files[0];
    if (!f) throw Error("请先选择测试音频");
    if (f.size > 20 * 1024 * 1024) throw Error("测试音频不超过 20 MB");
    audio = await new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onload = () => resolve(r.result.split(",")[1]);
      r.onerror = reject;
      r.readAsDataURL(f);
    });
  }
  const buttons = [...card.querySelectorAll("button")],
    disabledBefore = buttons.map((b) => b.disabled);
  buttons.forEach((b) => (b.disabled = true));
  card.querySelector(".model-result")?.remove();
  const busy = mn("p", "正在测试当前模型……", "hint");
  card.append(busy);
  try {
    const r = await api("/api/models/test", {
      id: m.id,
      audio,
      filename: md("audio").files[0]?.name,
    });
    m.test = r;
    card.append(resultView(r));
  } finally {
    busy.remove();
    buttons.forEach((b, i) => (b.disabled = disabledBefore[i]));
  }
}
function templateLabel(template) {
  return (
    { openai: "OpenAI", custom: "自定义", local: "本地服务" }[template.id] ||
    template.name ||
    template.id
  );
}
function pickTemplate() {
  md("templates").replaceChildren();
  const q = md("template_search").value.toLowerCase();
  for (const t of modelManager.templates.filter((t) =>
    (t.name + " " + t.id).toLowerCase().includes(q),
  ))
    md("templates").append(
      mb(templateLabel(t), () => {
        md("template_dialog").close();
        editSource("", t);
      }),
    );
}
function populateSource(id, source) {
  if (populatedSource !== id || draftSource)
    md("basic_settings").open = !!draftSource;
  populatedSource = id;
  md("connection_summary").textContent =
    (protocolNames[source.protocol || "openai"] || source.protocol) +
    " · " +
    (source.has_key
      ? source.key_count + " 枚密钥"
      : source.allow_no_key
        ? "无需密钥"
        : "未配置密钥");
  md("source_id").value = id;
  md("source_id").disabled = !draftSource;
  md("source_name").value = sourceLabel(id, source);
  md("protocol").replaceChildren(
    ...Object.entries(protocolNames).map(
      ([key, label]) => new Option(label, key),
    ),
  );
  md("protocol").value = source.protocol || "openai";
  for (const key of ["base_url", "proxy", "api_version"])
    md(key).value = source[key] || "";
  md("timeout").value = source.timeout ?? 120;
  md("retries").value = source.retry_count ?? 0;
  md("keys").value = "";
  md("keys").classList.remove("keys-visible");
  md("keys").placeholder = source.has_key
    ? "已保存 " + source.key_count + " 枚密钥"
    : "";
  md("key_show").checked = false;
  md("clear_keys").checked = false;
  md("source_enabled").checked = source.enabled !== false;
  md("no_key").checked = !!source.allow_no_key;
  md("headers").value = JSON.stringify(source.custom_headers || {}, null, 2);
  md("source_parameters").value = JSON.stringify(
    source.source_parameters || {},
    null,
    2,
  );
  md("saved_keys").disabled = !!draftSource || !source.has_key;
  sourceHelp();
}
function editSource(id = "", preset = {}) {
  if (sourceModified && !confirm("放弃未保存的连接设置？")) return;
  if (id) {
    chooseSource(id);
    return;
  }
  let next = preset.id || "provider";
  for (let n = 2; modelManager.sources[next]; n++)
    next = (preset.id || "provider") + "-" + n;
  draftSource = { enabled: true, ...preset, name: templateLabel(preset) };
  activeSource = next;
  sourceModified = false;
  renderModels();
  populateSource(next, draftSource);
  markSourceModified();
}
async function saveSource() {
  if (!md("source_form").reportValidity()) return false;
  const id = md("source_id").value.trim(),
    source = {
      name: md("source_name").value.trim() || id,
      protocol: md("protocol").value,
      base_url: md("base_url").value.trim(),
      enabled: md("source_enabled").checked,
      api_keys: md("keys")
        .value.split(/\r?\n/)
        .map((v) => v.trim())
        .filter(Boolean),
      allow_no_key: md("no_key").checked,
      timeout: Number(md("timeout").value),
      retry_count: Number(md("retries").value),
      proxy: md("proxy").value.trim(),
      custom_headers: JSON.parse(md("headers").value || "{}"),
      source_parameters: JSON.parse(md("source_parameters").value || "{}"),
      api_version: md("api_version").value || "2024-10-21",
    };
  await api("/api/model-sources/save", {
    id,
    source,
    clear_keys: md("clear_keys").checked,
  });
  activeSource = id;
  draftSource = null;
  sourceModified = false;
  md("keys").value = "";
  await refreshModels();
  renderFields();
  toast("已保存");
  return true;
}
async function fetchModels() {
  if (sourceModified && !(await saveSource())) return;
  const button = md("fetch");
  button.disabled = true;
  button.textContent = "正在获取…";
  try {
    const result = await api("/api/model-sources/list", { id: activeSource });
    if (!result.ok) {
      md("fetch_result").replaceChildren(resultView(result));
      return;
    }
    md("fetch_result").replaceChildren();
    await refreshModels();
  } finally {
    button.textContent = sourceModified ? "保存并获取模型" : "获取模型列表";
    button.disabled = !activeSource;
  }
}
function editModel(id = "", preset = {}) {
  editingModelId = id;
  const model = id
    ? modelManager.models.find((m) => m.id === id)
    : { enabled: true, temperature: 0.8, ...preset };
  md("model_id").value = id;
  md("model_id").disabled = !!id;
  md("model_source").replaceChildren(
    ...Object.entries(modelManager.sources).map(
      ([key, source]) => new Option(sourceLabel(key, source), key),
    ),
  );
  md("model_source").value = model.source_id || activeSource;
  md("model_category").replaceChildren(
    ...Object.entries(typeNames).map(([key, label]) => new Option(label, key)),
  );
  md("model_category").value = model.category || activeCategory;
  for (const key of [
    "model",
    "name",
    "temperature",
    "max_tokens",
    "context_window",
  ])
    md("m_" + key).value = model[key] ?? "";
  md("m_enabled").checked = model.enabled !== false;
  md("m_stream").checked = !!model.stream;
  md("m_family").value = model.family || "auto";
  md("m_parameters").value = JSON.stringify(model.parameters || {}, null, 2);
  for (const input of document.querySelectorAll("[data-model-cap]"))
    input.checked = (model.modalities || []).includes(input.dataset.modelCap);
  md("make_default").classList.toggle(
    "hidden",
    !["chat", "embedding"].includes(model.category || activeCategory),
  );
  md("make_default").disabled = !id;
  modelHelp();
  md("model_dialog").showModal();
}
function sourceHelp() {
  md("azure_version").classList.toggle(
    "hidden",
    md("protocol").value !== "azure",
  );
}
function modelHelp() {
  const p =
    modelManager.sources[md("model_source").value]?.protocol || "openai";
  md("model_suggestions").replaceChildren(
    ...(modelManager.lists[md("model_source").value] || []).map(
      (m) => new Option(m.name || m.model, m.model),
    ),
  );
  md("parameter_help").textContent =
    {
      anthropic:
        "Claude：thinking / output_config 等原生参数填在这里；部分思考模式要求温度留空或设为 1。",
      gemini:
        "Gemini 原生：generationConfig.thinkingConfig、safetySettings、tools 等；",
      responses: "Responses：reasoning、text 等原生参数。",
      ollama: "Ollama：options / think 等原生参数。",
    }[p] ||
    "填写服务商支持的 JSON 参数，例如 thinking、reasoning_effort；字段按接口原样传递。";
}
md("enable_source").onclick = managed(async () => {
  md("source_enabled").checked = true;
  markSourceModified();
  await saveSource();
  await status();
});
md("source_form").onsubmit = managed(async (event) => {
  event.preventDefault();
  await saveSource();
});
md("source_form").addEventListener("input", markSourceModified);
md("source_form").addEventListener("change", markSourceModified);
md("mobile_source").onchange = () => chooseSource(md("mobile_source").value);
md("make_default").onclick = managed(async () => {
  const model = modelManager.models.find((m) => m.id === editingModelId);
  await api("/api/models/default", {
    id: editingModelId,
    category: model.category || "chat",
  });
  await refreshModels();
  await status();
  toast("默认模型已更新");
});
md("saved_keys").onclick = managed(async () => {
  const r = await api("/api/model-sources/keys", { id: md("source_id").value });
  md("keys").value = r.api_keys.join("\n");
  md("key_show").checked = true;
  md("keys").classList.add("keys-visible");
});
md("key_show").onchange = () =>
  md("keys").classList.toggle("keys-visible", md("key_show").checked);
md("protocol").onchange = sourceHelp;
md("model_source").onchange = modelHelp;
md("model_form").onsubmit = managed(async (e) => {
  e.preventDefault();
  const source_id = md("model_source").value,
    model = md("m_model").value.trim(),
    id = md("model_id").value.trim() || source_id + "/" + model;
  const value = {
    source_id,
    model,
    name: md("m_name").value.trim(),
    category: md("model_category").value,
    enabled: md("m_enabled").checked,
    temperature:
      md("m_temperature").value === ""
        ? null
        : Number(md("m_temperature").value),
    max_tokens: md("m_max_tokens").value
      ? Number(md("m_max_tokens").value)
      : null,
    context_window: md("m_context_window").value
      ? Number(md("m_context_window").value)
      : null,
    stream: md("m_stream").checked,
    prompt_cache: editingModelId
      ? modelManager.models.find((m) => m.id === editingModelId).prompt_cache
      : undefined,
    family: md("m_family").value,
    modalities: [...document.querySelectorAll("[data-model-cap]:checked")].map(
      (n) => n.dataset.modelCap,
    ),
    parameters: JSON.parse(md("m_parameters").value || "{}"),
  };
  await api("/api/models/save", { id, model: value });
  activeSource = source_id;
  activeCategory = value.category;
  md("model_dialog").close();
  await refreshModels();
  renderFields();
  toast("模型已保存");
});
md("add_source").onclick = () => {
  pickTemplate();
  md("template_dialog").showModal();
};
md("template_search").oninput = pickTemplate;
md("fetch").onclick = managed(fetchModels);
md("custom").onclick = () => {
  md("manual_model").value = "";
  md("manual_id").value = activeSource + "/";
  md("manual_dialog").showModal();
};
md("search").oninput = renderModels;
md("tools").onclick = () => md("tools_dialog").showModal();
md("embedding_save").onclick = managed(async () => {
  await api("/api/models/default", {
    id: md("embedding_default").value,
    category: "embedding",
  });
  await refreshModels();
  toast("嵌入选择已保存");
});
md("export").onclick = managed(async () => {
  const data = await api("/api/models/export"),
    link = mn("a"),
    url = URL.createObjectURL(
      new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }),
    );
  link.href = url;
  link.download = "光谱计划-模型配置.json";
  link.click();
  URL.revokeObjectURL(url);
});
md("import").onclick = managed(async () => {
  const f = md("import_file").files[0];
  if (!f) throw Error("请选择模型配置 JSON 文件");
  await api("/api/models/import", JSON.parse(await f.text()));
  await refreshModels();
  renderFields();
  toast("模型设置已导入，新接口请补填密钥");
});
for (const b of document.querySelectorAll("[data-close-dialog]"))
  b.onclick = () => md(b.dataset.closeDialog).close();
el("setupForm").onsubmit = managed(async (e) => {
  e.preventDefault();
  await api("/api/config/system", {
    enabled: el("enabled").checked,
    persona: el("persona").value,
    operators: el("operators")
      .value.split(/[,，\s]+/)
      .filter(Boolean),
    chat_provider_id: el("chat_provider_id").value,
  });
  await load();
  toast("人设与运行设置已保存");
});

md("manual_model").oninput = () => {
  md("manual_id").value = activeSource + "/" + md("manual_model").value.trim();
};
md("manual_form").onsubmit = managed(async (event) => {
  event.preventDefault();
  const name = md("manual_model").value.trim();
  await api("/api/models/save", {
    id: activeSource + "/" + name,
    model: {
      source_id: activeSource,
      model: name,
      category: activeCategory,
      enabled: true,
      temperature: 0.8,
    },
  });
  md("manual_dialog").close();
  await refreshModels();
  renderFields();
  toast("模型已添加");
});
for (const button of document.querySelectorAll("#nav [data-tab]")) {
  const name = {
    models: "embedding",
    connections: "test",
    setup: "settings",
    settings: "settings",
    personality: "chat",
    spectrum: "embedding",
    prism: "tts",
    guard: "test",
    logs: "rerank",
    plugins: "plus",
    regex: "rerank",
    data: "embedding",
    access: "settings",
    overview: "chat",
  }[button.dataset.tab];
  button.prepend(drawing(name));
}

md("fallback_open").onclick = () => {
  for (const category of Object.keys(typeNames)) {
    const select = md("fallback_" + category);
    const candidates = modelManager.models.filter(
      (m) =>
        (m.category || "chat") === category &&
        (category !== "chat" || m.id !== modelManager.chat_provider_id),
    );
    select.replaceChildren(
      new Option("不使用备用模型", ""),
      ...candidates.map(
        (m) =>
          new Option(
            (m.name || m.id) + " · " + m.model + (m.ready ? "" : "（不可用）"),
            m.id,
          ),
      ),
    );
    select.value = modelManager.fallback_models[category] || "";
  }
  md("fallback_freeze").checked = Object.keys(modelManager.fallback_models)
    .length
    ? modelManager.freeze_enabled
    : true;
  md("fallback_freeze_note").textContent =
    "保留当前累计失败阈值：" +
    modelManager.freeze_threshold +
    " 次。当前冻结开关：" +
    (modelManager.freeze_enabled ? "已启用" : "未启用") +
    "；保存后按上方开关执行。";
  md("fallback_dialog").showModal();
};
md("fallback_form").onsubmit = managed(async (event) => {
  event.preventDefault();
  const fallback_models = Object.fromEntries(
    Object.keys(typeNames).map((category) => [
      category,
      md("fallback_" + category).value,
    ]),
  );
  await api("/api/models/fallback", {
    fallback_models,
    freeze_enabled: md("fallback_freeze").checked,
  });
  md("fallback_dialog").close();
  await refreshModels();
  renderFields();
  await status();
  toast("备用模型设置已保存");
});
