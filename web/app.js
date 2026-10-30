/* 原生 JavaScript：DOM、点击事件、fetch、async/await。没有前端构建工具。 */
const $ = (id) => document.getElementById(id);
let dataset = null;
let currentRun = null;
let mode = "local";
let currentTab = "groups";
let busy = false;
let modelConfigured = false;
let pollVersion = 0;
const statusText = { completed: "已完成", running: "执行中", needs_input: "等待补充", needs_review: "等待审阅", failed: "执行失败" };

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}

function showError(error) {
  $("message").textContent = error.message || String(error);
  $("message").hidden = false;
  $("message").focus();
}

function clearError() { $("message").hidden = true; }

async function api(path, options = {}) {
  const headers = { "X-Requested-With": "table-report-agent", ...(options.headers || {}) };
  let response;
  try { response = await fetch(path, { ...options, headers }); }
  catch { throw new Error("无法连接本机服务。请检查启动终端，再刷新页面或查看最近任务。"); }
  let data;
  try { data = await response.json(); }
  catch { throw new Error("服务返回了无法识别的响应，请检查启动终端。"); }
  if (!response.ok) {
    const detail = typeof data.detail === "string" ? data.detail : "提交的字段不完整或格式不正确，请检查后重试。";
    throw new Error(detail);
  }
  return data;
}

function post(path, data) {
  return api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
}

function setBusy(value) {
  busy = value;
  for (const id of ["demo-button", "upload-button", "mode-local", "mode-agent", "continue-button", "approve-plan", "revise-plan"]) $(id).disabled = value;
  $("run-button").disabled = value || !dataset || (mode === "agent" && !modelConfigured);
  $("run-button").textContent = value ? "正在执行…" : mode === "local" ? "开始本地核对 →" : "执行 Agent 任务 →";
}

function setMode(value) {
  mode = value;
  $("mode-local").setAttribute("aria-pressed", String(mode === "local"));
  $("mode-agent").setAttribute("aria-pressed", String(mode === "agent"));
  $("local-settings").hidden = mode !== "local";
  $("agent-settings").hidden = mode !== "agent";
  // 隐藏的必填控件必须同时禁用，否则浏览器会拦截 Agent 提交。
  for (const input of $("local-settings").querySelectorAll("input, select")) input.disabled = mode !== "local";
  $("mode-hint").textContent = mode === "local" ? "你选择字段，程序按固定规则计算。无需模型 API。" : "模型根据任务检查字段、选择工具；有歧义时会暂停并向你提问。";
  setBusy(busy);
}

function selectOptions(id, headers, placeholder, preferred = "") {
  const select = $(id);
  select.replaceChildren();
  const empty = node("option", placeholder);
  empty.value = "";
  select.append(empty);
  for (const header of headers) {
    const option = node("option", header);
    option.value = header;
    select.append(option);
  }
  select.value = preferred;
}

function makeTable(headers, rows) {
  const table = node("table");
  const head = node("thead");
  const tr = node("tr");
  for (const header of headers) { const th = node("th", header); th.scope = "col"; tr.append(th); }
  head.append(tr);
  const body = node("tbody");
  if (!rows.length) {
    const row = node("tr"); const cell = node("td", "当前没有符合条件的记录。"); cell.colSpan = headers.length; row.append(cell); body.append(row);
  }
  for (const values of rows) {
    const row = node("tr");
    for (const value of values) { const cell = node("td", String(value ?? "—")); cell.title = String(value ?? ""); row.append(cell); }
    body.append(row);
  }
  table.append(head, body);
  return table;
}

function loadDataset(info) {
  dataset = info;
  $("dataset-banner").hidden = false;
  $("dataset-banner").textContent = `${info.is_demo ? "虚构示例 · " : "已导入 · "}报名 ${info.registration.row_count} 条 / 签到 ${info.checkin.row_count} 条`;
  const idAliases = ["学号", "学生编号", "student_id", "id", "编号", "工号"];
  for (const kind of ["registration", "checkin"]) {
    const headers = info[kind].headers;
    const guess = headers.find((h) => idAliases.includes(h.toLowerCase())) || "";
    selectOptions(`${kind}-key`, headers, "请选择匹配字段", guess);
    selectOptions(`${kind}-key-2`, headers, "不使用第二字段");
    selectOptions(`${kind}-filter-column`, headers, "不筛选");
    $(`${kind}-filter-value`).value = "";
  }
  selectOptions("group-by", info.registration.headers, "不分组", info.registration.headers.find((h) => ["班级", "部门", "class"].includes(h.toLowerCase())) || "");
  $("name-only").checked = false;
  const previews = $("previews"); previews.replaceChildren();
  for (const kind of ["registration", "checkin"]) {
    const table = info[kind];
    const wrapper = node("div", undefined, "preview-table");
    wrapper.append(node("h4", table.name), makeTable(["源记录号", ...table.headers], table.preview.map((r) => [r.record, ...table.headers.map((h) => r.values[h])])));
    previews.append(wrapper);
  }
  $("preview-area").hidden = false;
  setBusy(busy);
}

function localConfig() {
  const registrationKeys = [$("registration-key").value];
  const checkinKeys = [$("checkin-key").value];
  const r2 = $("registration-key-2").value, c2 = $("checkin-key-2").value;
  if (!registrationKeys[0] || !checkinKeys[0]) throw new Error("请为两张表选择主要匹配字段。");
  if (Boolean(r2) !== Boolean(c2)) throw new Error("组合匹配需要同时选择两张表的第二字段。");
  if (r2) { registrationKeys.push(r2); checkinKeys.push(c2); }
  function filters(kind) {
    const column = $(`${kind}-filter-column`).value;
    return column ? [{ column, value: $(`${kind}-filter-value`).value.trim() }] : [];
  }
  return { registration_keys: registrationKeys, checkin_keys: checkinKeys, group_by: $("group-by").value || null,
    registration_filters: filters("registration"), checkin_filters: filters("checkin"), allow_name_only: $("name-only").checked };
}

function restoreConfig(config) {
  for (const kind of ["registration", "checkin"]) {
    $(kind + "-key").value = config[kind + "_keys"][0];
    $(kind + "-key-2").value = config[kind + "_keys"][1] || "";
    const filter = config[kind + "_filters"][0];
    $(kind + "-filter-column").value = filter?.column || "";
    $(kind + "-filter-value").value = filter?.value || "";
  }
  $("group-by").value = config.group_by || "";
  $("name-only").checked = config.allow_name_only;
}

function renderTable() {
  if (!currentRun?.result) return;
  const result = currentRun.result;
  let headers, rows;
  if (currentTab === "groups") {
    headers = ["分组", "有效报名", "已到场", "未到场", "到场率"];
    rows = result.groups.map((g) => [g.group, g.registered, g.arrived, g.absent, `${g.attendance_rate}%`]);
  } else if (currentTab === "details") {
    const fields = result.sources.registration.headers;
    headers = [...fields, "核对状态", "报名源记录", "签到源记录"];
    rows = result.details.map((d) => [...fields.map((h) => d.values[h]), d.status, d.registration_records.join("、"), d.checkin_records.join("、") || "—"]);
  } else {
    headers = ["异常类型", "来源文件", "源记录号", "说明", "原始字段"];
    rows = result.issues.map((i) => [i.type, i.source, i.record, i.detail, JSON.stringify(i.values)]);
  }
  const search = $("table-search").value.trim().toLowerCase();
  rows = rows.filter((r) => r.some((v) => String(v).toLowerCase().includes(search)));
  const table = makeTable(headers, rows);
  $("result-table").replaceChildren(...table.childNodes);
  const total = result.preview_counts[currentTab];
  $("table-caption").textContent = `共 ${total} 条 · 当前显示 ${rows.length} 条${total > 200 ? "（最多预览 200 条，下载含全部）" : ""}`;
}

function renderRun(run) {
  currentRun = run;
  $("empty-state").hidden = Boolean(run);
  $("result-content").hidden = !run?.result || run.status !== "completed";
  $("run-progress").hidden = run?.status !== "running";
  $("plan-review").hidden = run?.status !== "needs_review";
  $("clarification").hidden = run?.status !== "needs_input" && !(run?.status === "failed" && run.mode === "agent");
  $("run-status").textContent = run ? statusText[run.status] : "等待核对";
  $("run-status").className = `badge ${run?.status || ""}`;
  if (!run) {
    $("trace-list").replaceChildren(node("li", "运行后可查看读取、核对、工具调用和失败原因。", "muted"));
    $("usage").textContent = "尚未执行";
    return;
  }
  if (run.error) showError(new Error(run.error));
  if (run.status === "needs_review" && run.pending_config) {
    const cfg = run.pending_config;
    $("plan-fields").replaceChildren();
    const filters = (items) => items.length ? items.map((f) => `${f.column} = ${JSON.stringify(f.value)}`).join(" 且 ") : "无筛选，使用全表";
    for (const [label, value] of [["匹配字段", `${cfg.registration_keys.join(" + ")} ↔ ${cfg.checkin_keys.join(" + ")}`], ["报名筛选", filters(cfg.registration_filters)], ["签到筛选", filters(cfg.checkin_filters)], ["分组统计", cfg.group_by || "不分组"]]) {
      $("plan-fields").append(node("dt", label), node("dd", value));
    }
  }
  if (run.status === "needs_input" || run.status === "failed") {
    $("question-text").textContent = run.question || "可以填写补充说明后重试；遇到额度或接口错误时，先在本地修正配置。";
    $("answer-form").hidden = run.mode !== "agent";
    $("local-question-hint").hidden = run.mode === "agent";
    $("continue-button").textContent = run.status === "failed" ? "重试 Agent 任务" : "提交并继续";
  }
  $("trace-list").replaceChildren();
  for (const event of run.trace) {
    const li = node("li");
    const stepNames = { inspect_tables: "检查表格字段", reconcile_attendance: "提交或执行核对", summarize_by_group: "分组统计", ask_user: "请求补充说明" };
    li.append(node("strong", stepNames[event.step] || event.step), node("span", { ok: "完成", waiting: "等待确认", error: "失败" }[event.status] || event.status), node("span", event.duration_ms ? `${event.duration_ms} ms` : "", "trace-time"), node("p", event.detail));
    $("trace-list").append(li);
  }
  $("usage").textContent = run.mode === "local" ? "本地计算 · 无模型调用" : `${run.requests} 次请求 · 输入 ${run.usage.prompt_tokens} / 输出 ${run.usage.completion_tokens} token`;
  if (!run.result || run.status !== "completed") return;
  const t = run.result.totals;
  $("metrics").replaceChildren();
  for (const [label, value] of [["有效报名", t.valid_registered], ["已到场", t.arrived], ["未到场", t.absent], ["到场率", t.attendance_rate === null ? "—" : `${t.attendance_rate}%`]]) {
    const metric = node("div", undefined, "metric"); metric.append(node("span", label, "metric-label"), node("strong", value, "metric-value")); $("metrics").append(metric);
  }
  $("quality-note").textContent = `待确认并排除的报名记录 ${t.excluded_registration_rows} 条 · 重复签到 ${t.duplicate_checkin_rows} 条 · 未报名签到 ${t.unregistered_people} 个标识。异常项可能重叠，请查看源记录。`;
  $("downloads").replaceChildren();
  for (const [kind, title] of [["report", "下载核对报告"], ["details", "人员明细 CSV"], ["issues", "异常名单 CSV"], ["groups", "分组统计 CSV"]]) {
    const a = node("a", title); a.href = `/api/runs/${run.id}/export/${kind}`; $("downloads").append(a);
  }
  const rules = node("ul");
  const cfg = run.result.config;
  for (const rule of [`匹配：${cfg.registration_keys.join(" + ")} ↔ ${cfg.checkin_keys.join(" + ")}`, `报名筛选：${JSON.stringify(cfg.registration_filters)}；签到筛选：${JSON.stringify(cfg.checkin_filters)}`, ...run.result.rules]) rules.append(node("li", rule));
  $("rules-content").replaceChildren(rules);
  renderTable();
}

async function refreshHistory() {
  const runs = await api("/api/runs");
  $("history").replaceChildren();
  if (!runs.length) $("history").append(node("p", "尚无任务记录。", "muted small"));
  for (const run of runs) {
    const button = node("button", undefined, "history-item"); button.type = "button";
    button.append(node("strong", run.mode === "local" ? "报名与签到核对" : run.task), node("span", `${statusText[run.status]} · ${new Date(run.created_at).toLocaleString("zh-CN")}`));
    button.addEventListener("click", async () => {
      if (busy) return;
      clearError();
      try {
        const full = await api(`/api/runs/${run.id}`);
        loadDataset(await api(`/api/datasets/${full.dataset_id}`));
        setMode(full.mode);
        if (full.result) restoreConfig(full.result.config);
        if (full.task) $("agent-task").value = full.task;
        renderRun(full);
        if (full.status === "running") { setBusy(true); await poll(full.id); }
      } catch (error) { showError(error); }
    });
    $("history").append(button);
  }
}

async function poll(id) {
  const version = ++pollVersion;
  try {
    while (version === pollVersion) {
      const run = await api(`/api/runs/${id}`);
      renderRun(run);
      if (run.status !== "running") break;
      await new Promise((resolve) => setTimeout(resolve, 1200));
    }
    await refreshHistory();
  } finally { setBusy(false); }
}

$("mode-local").addEventListener("click", () => setMode("local"));
$("mode-agent").addEventListener("click", () => setMode("agent"));
$("upload-form").addEventListener("submit", async (event) => {
  event.preventDefault(); clearError(); setBusy(true);
  try {
    const data = new FormData(event.currentTarget);
    loadDataset(await api("/api/datasets", { method: "POST", body: data }));
    renderRun(null);
  } catch (error) { showError(error); }
  finally { setBusy(false); }
});
$("demo-button").addEventListener("click", async () => {
  clearError(); setBusy(true);
  try {
    $("upload-form").reset();
    loadDataset(await post("/api/demo", {})); setMode("local");
    renderRun(await post("/api/runs/local", { dataset_id: dataset.id, config: localConfig() }));
    await refreshHistory();
  } catch (error) { showError(error); }
  finally { setBusy(false); }
});
$("run-form").addEventListener("submit", async (event) => {
  event.preventDefault(); if (!dataset || busy) return;
  clearError(); setBusy(true);
  try {
    if (mode === "local") {
      renderRun(await post("/api/runs/local", { dataset_id: dataset.id, config: localConfig() }));
      await refreshHistory();
    } else {
      const run = await post("/api/runs/agent", { dataset_id: dataset.id, task: $("agent-task").value.trim(), consent_to_send: $("consent").checked, allow_name_only: $("name-only").checked });
      renderRun(run); await poll(run.id);
    }
  } catch (error) { showError(error); }
  finally { setBusy(false); }
});
$("answer-form").addEventListener("submit", async (event) => {
  event.preventDefault(); if (!currentRun || busy) return;
  clearError(); setBusy(true);
  try {
    const run = await post(`/api/runs/${currentRun.id}/continue`, { answer: $("answer").value.trim(), consent_to_send: $("consent").checked, allow_name_only: $("name-only").checked });
    $("answer").value = ""; renderRun(run); await poll(run.id);
  } catch (error) { showError(error); }
  finally { setBusy(false); }
});
for (const button of document.querySelectorAll("[data-tab]")) button.addEventListener("click", () => {
  currentTab = button.dataset.tab; $("table-search").value = "";
  for (const tab of document.querySelectorAll("[data-tab]")) tab.setAttribute("aria-pressed", String(tab === button));
  renderTable();
});
$("table-search").addEventListener("input", renderTable);
$("refresh-history").addEventListener("click", () => refreshHistory().catch(showError));
for (const button of document.querySelectorAll("[data-task]")) button.addEventListener("click", () => { $("agent-task").value = button.dataset.task; $("agent-task").focus(); });

async function submitReview(approved) {
  if (!currentRun || busy) return;
  if (!approved && !$("plan-note").value.trim()) { $("plan-note").focus(); showError(new Error("请先写下希望修改的内容。")); return; }
  clearError(); setBusy(true);
  try {
    const run = await post(`/api/runs/${currentRun.id}/review`, { approved, note: $("plan-note").value.trim(), consent_to_send: $("consent").checked });
    $("plan-note").value = ""; renderRun(run); await poll(run.id);
  } catch (error) { showError(error); }
  finally { setBusy(false); }
}
$("approve-plan").addEventListener("click", () => submitReview(true));
$("revise-plan").addEventListener("click", () => submitReview(false));

function applySettings(data) {
  modelConfigured = data.configured;
  $("settings-state").textContent = data.configured ? "已配置" : "未配置";
  $("settings-url").value = data.base_url;
  $("settings-model").value = data.model;
  $("settings-extra").value = data.extra_json;
  $("settings-key").value = "";
  $("key-hint").textContent = data.has_api_key ? "已有密钥，留空可保留；更换 API 地址时需重新输入对应密钥。密钥不会回填到网页。" : "密钥不会回填到网页，也不会保存在浏览器存储中。";
  $("settings-overrides").hidden = !data.environment_overrides.length;
  $("settings-overrides").textContent = `当前进程环境变量优先于页面保存：${data.environment_overrides.join("、")}。若页面设置未生效，请检查启动环境。`;
  $("model-status").textContent = data.configured ? "已读取本地模型配置；可在模型设置中测试接口。" : "模型 API 尚未配置，点击右上角“模型设置”添加自己的服务。";
  setBusy(busy);
}

function settingsBusy(value) {
  for (const id of ["save-settings", "test-settings", "clear-settings"]) $(id).disabled = value;
}
function settingsRequest() {
  return { base_url: $("settings-url").value.trim(), model: $("settings-model").value.trim(), api_key: $("settings-key").value.trim(), extra_json: $("settings-extra").value.trim() || "{}" };
}
function settingsFeedback(message, error = false) {
  $("settings-feedback").textContent = message;
  $("settings-feedback").classList.toggle("error", error);
}
$("open-settings").addEventListener("click", async () => {
  try {
    applySettings(await api("/api/settings/model")); settingsFeedback(""); $("test-consent").checked = false;
    $("settings-dialog").showModal();
  } catch (error) { showError(error); }
});
$("close-settings").addEventListener("click", () => $("settings-dialog").close());
$("settings-dialog").addEventListener("close", () => { $("settings-key").value = ""; $("test-consent").checked = false; });
$("settings-form").addEventListener("submit", async (event) => {
  event.preventDefault(); settingsBusy(true); settingsFeedback("正在保存到本机…");
  try {
    applySettings(await post("/api/settings/model", settingsRequest()));
    settingsFeedback("配置已保存在本机；保存不会发起模型请求。可继续测试连接。");
  } catch (error) { settingsFeedback(error.message, true); }
  finally { settingsBusy(false); }
});
$("test-settings").addEventListener("click", async () => {
  if (!$("settings-form").reportValidity()) return;
  if (!$("test-consent").checked) { settingsFeedback("请先勾选同意测试；测试会发起一次可能计费的请求。", true); $("test-consent").focus(); return; }
  settingsBusy(true); settingsFeedback("正在测试连接与工具调用，最长可能需要 45 秒…");
  try {
    const result = await post("/api/settings/model/test", { ...settingsRequest(), consent_to_test: true });
    settingsFeedback(`${result.message} 测试不会自动保存未保存的配置。`, !result.tool_call_verified);
  } catch (error) { settingsFeedback(error.message, true); }
  finally { settingsBusy(false); }
});
$("clear-settings").addEventListener("click", async () => {
  settingsBusy(true);
  try {
    const data = await api("/api/settings/model", { method: "DELETE" }); applySettings(data);
    settingsFeedback(data.configured ? "本机保存已清除；环境变量或 .env 中的配置仍有效。" : "本机保存的配置和密钥已清除。");
  } catch (error) { settingsFeedback(error.message, true); }
  finally { settingsBusy(false); }
});
(async () => {
  try {
    await api("/api/health");
    $("service-status").textContent = "本机服务已连接";
    applySettings(await api("/api/settings/model"));
    setBusy(false); await refreshHistory();
  } catch (error) { $("service-status").textContent = "本机服务未连接"; showError(error); }
})();
