const byId = (id) => document.getElementById(id);
const make = (tag, text, cls) => {
  const item = document.createElement(tag);
  if (text !== undefined) item.textContent = text;
  if (cls) item.className = cls;
  return item;
};
const state = { data: null, results: [], selected: null, parent: null, events: [] };
const fmt = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 });
const timeText = (value) => value ? value.replace("T", " ") : "—";
const localMinute = (milliseconds) => new Date(milliseconds - new Date(milliseconds).getTimezoneOffset() * 60000)
  .toISOString().slice(0, 16);

function message(text, error = false) {
  const notice = byId("notice");
  notice.textContent = text;
  notice.classList.toggle("error", error);
  notice.hidden = !text;
  if (error) notice.scrollIntoView({ block: "nearest" });
}

async function api(url, options) {
  const response = await fetch(url, options);
  const payload = await response.json();
  if (!response.ok) {
    const detail = payload.detail;
    throw new Error(typeof detail === "string" ? detail : "请求字段不符合要求，请检查日期和数量。");
  }
  return payload;
}

function post(url, data) {
  return api(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
}

function row(values) {
  const tr = make("tr");
  values.forEach((value) => {
    const td = make("td");
    if (value instanceof Node) td.append(value);
    else td.textContent = value ?? "—";
    tr.append(td);
  });
  return tr;
}

async function action(button, waiting, run) {
  button.disabled = true;
  message(waiting);
  try { await run(); }
  catch (error) { message(error.message, true); }
  finally { button.disabled = false; }
}

byId("importForm").addEventListener("submit", (event) => {
  event.preventDefault();
  action(event.submitter, "正在导入并核对工作簿…", async () => {
    const file = byId("inputFile").files[0];
    const data = new FormData();
    data.append("file", file);
    state.data = await api("/api/datasets", { method: "POST", body: data });
    state.results = []; state.selected = null; state.parent = null; state.events = [];
    byId("dataName").textContent = state.data.name;
    const info = state.data.summary;
    const stats = [["订单", info.orders], ["产品编码", info.codes], ["有效产线", info.lines],
      ["订单需求 PCS", info.order_units], ["生产任务 PCS", info.production_units]];
    byId("summary").replaceChildren(...stats.map(([label, value]) => {
      const box = make("div", undefined, "stat");
      box.append(make("span", label), make("strong", fmt.format(value)));
      return box;
    }));
    byId("summary").hidden = false;
    byId("issues").hidden = false;
    byId("issues").querySelector("summary").textContent = "数据异常（" + state.data.issues.length + "）";
    byId("issueRows").replaceChildren(...state.data.issues.map((i) => row([i.category, i.sheet, i.row || "—", i.reference || "—", i.detail])));
    byId("audit").href = "/api/datasets/" + state.data.id + "/audit.xlsx";
    byId("audit").hidden = false;
    byId("planAt").value = info.last_order;
    byId("planEnd").value = localMinute(Date.parse(info.last_due) + 7 * 86400000);
    byId("runPlan").disabled = false;
    byId("results").hidden = true;
    byId("replanControls").disabled = true;
    byId("baseLabel").textContent = "生成并选择一个方案后即可开始重排。";
    byId("changeList").replaceChildren();
    renderEvents();
    message("导入完成。");
  });
});

byId("planForm").addEventListener("submit", (event) => {
  event.preventDefault();
  action(event.submitter, "正在构造并校验候选方案…", async () => {
    const results = await post("/api/plans", { dataset_id: state.data.id, at: byId("planAt").value, end: byId("planEnd").value });
    accept(results, null);
  });
});

function accept(results, parent) {
  state.results = [...results.plans, results.baseline];
  state.parent = parent;
  state.events = [];
  renderEvents();
  byId("results").hidden = false;
  byId("resultMeta").textContent = results.plans.length + " 个候选 · " + results.solver_seconds.toFixed(2)
    + " 秒求解 · " + results.request_seconds.toFixed(2) + " 秒含读入与保存";
  selectPlan(state.results[0]);
  message("排程完成。未排任务见订单交付下方。");
  byId("results").scrollIntoView({ block: "start" });
}

function metricRow(plan, previous = false) {
  const m = plan.metrics;
  const name = previous ? "调整前 · " + plan.strategy : plan.strategy;
  const pick = previous ? make("span", name) : make("button", name, "secondary");
  if (!previous) {
    pick.type = "button";
    pick.setAttribute("aria-pressed", String(plan.id === state.selected.id));
    pick.addEventListener("click", () => selectPlan(plan, true));
  }
  const tr = row([pick, fmt.format(m.otd * 100) + "%", fmt.format(m.weighted_tardy_hours), fmt.format(m.setup_hours),
    fmt.format(m.load_cv), m.pending_orders, fmt.format(m.shortfall_units),
    m.comparable ? m.moved + " / " + m.comparable : "—"]);
  if (previous) tr.className = "previous";
  else if (plan.id === state.selected.id) tr.className = "selected";
  return tr;
}

function selectPlan(plan, restoreFocus = false) {
  state.selected = plan;
  byId("planRows").replaceChildren(...(state.parent ? [metricRow(state.parent, true)] : []), ...state.results.map((p) => metricRow(p)));
  if (restoreFocus) byId("planRows").querySelector('[aria-pressed="true"]').focus();
  byId("export").href = "/api/plans/" + plan.id + "/export.xlsx";
  byId("chosenMeta").textContent = plan.strategy + " · " + timeText(plan.at) + " 排产 · " + plan.batches.length + " 道计划作业";
  const currentLine = byId("lineFilter").value;
  byId("lineFilter").replaceChildren(new Option("全部", ""), ...plan.lines.map((line) => new Option(line, line)));
  if (plan.lines.includes(currentLine)) byId("lineFilter").value = currentLine;
  byId("barDetail").textContent = "选择甘特条查看作业明细。";
  byId("unassigned").querySelector("summary").textContent = "未排任务（" + plan.unassigned.length + "）";
  byId("unassignedRows").replaceChildren(...(plan.unassigned.length ? plan.unassigned.map((r) => row([r.job, r.code, r.model, r.qty, r.reason])) : [row(["本方案无未排任务", "", "", "", ""])]));
  byId("replanControls").disabled = false;
  byId("baseLabel").textContent = "当前基准 · " + plan.strategy + " · " + timeText(plan.at);
  byId("eventAt").min = plan.at;
  if (!byId("eventAt").value || byId("eventAt").value < plan.at || byId("eventAt").value >= plan.end) {
    byId("eventAt").value = localMinute(Date.parse(plan.at) + 14400000);
    if (byId("eventAt").value >= plan.end) byId("eventAt").value = plan.at;
  }
  byId("changeList").replaceChildren(...plan.changes.map((item) => make("li", item)));
  drawGantt();
  renderOrders();
  eventFields();
}

function drawGantt() {
  const plan = state.selected;
  if (!plan) return;
  const filter = byId("lineFilter").value;
  const span = byId("timeFilter").value;
  const from = Date.parse(span === "all" || span === "window" ? plan.start : plan.at);
  const to = span === "all" ? Math.max(from + 3600000, ...plan.batches.map((item) => Date.parse(item.end)))
    : span === "window" ? Date.parse(plan.end) : from + Number(span) * 86400000;
  const lane = (name, css) => {
    const container = make("div", undefined, css);
    const track = make("div", undefined, "track");
    container.append(make("span", name, "line"), track);
    return { container, track };
  };
  const axis = lane("产线", "lane axis");
  for (let offset = 0; offset < 6; offset++) {
    const value = localMinute(from + (to - from) * offset / 6);
    const tick = make("span", value.slice(5, 10) + (span === "1" ? " " + value.slice(11, 16) : ""), "tick");
    tick.style.left = (100 * offset / 6) + "%";
    axis.track.append(tick);
  }
  const fragment = document.createDocumentFragment();
  fragment.append(axis.container);
  function attach(track, node, start, end) {
    if (end <= from || start >= to || start === end) return;
    node.style.left = (100 * (Math.max(start, from) - from) / (to - from)) + "%";
    node.style.width = (100 * (Math.min(end, to) - Math.max(start, from)) / (to - from)) + "%";
    track.append(node);
  }
  for (const line of plan.lines.filter((item) => !filter || item === filter)) {
    const track = lane(line, "lane");
    for (const job of plan.batches.filter((item) => item.line === line)) {
      const label = [job.line, job.job, job.model, fmt.format(job.qty) + " 件", timeText(job.start) + " 至 " + timeText(job.end), job.status].join(" · ");
      attach(track.track, make("span", undefined, "setup-bar"), Date.parse(job.setup_start), Date.parse(job.start));
      const bar = make("button", undefined, "jobbar " + job.state);
      bar.type = "button";
      bar.title = label;
      bar.setAttribute("aria-label", label);
      bar.addEventListener("click", () => { byId("barDetail").textContent = label; });
      attach(track.track, bar, Date.parse(job.start), Date.parse(job.end));
    }
    fragment.append(track.container);
  }
  byId("gantt").replaceChildren(fragment);
}

function renderOrders() {
  if (!state.selected) return;
  const key = byId("orderSearch").value.trim().toLowerCase();
  const filter = byId("orderFilter").value;
  const orders = state.selected.orders.filter((o) => (!filter || o.status === filter) &&
    [o.id, o.customer, o.code].some((value) => value.toLowerCase().includes(key)));
  byId("orderCount").textContent = "显示 " + orders.length + " / " + state.selected.orders.length + " 条订单";
  byId("orderRows").replaceChildren(...orders.map((o) => row([o.id, o.customer, o.code, fmt.format(o.qty),
    fmt.format(o.stock), fmt.format(o.planned), timeText(o.due), timeText(o.completion),
    make("span", o.status, o.status === "未排完" ? "status unfilled" : o.status === "延期" ? "status warn" : "status")])));
}

function eventFields() {
  const kind = byId("eventType").value;
  const options = state.selected?.lines || [];
  const fill = (name, type, id, value, required = true) => {
    const label = make("label", name);
    const input = make("input");
    input.type = type; input.id = id; input.value = value || ""; input.required = required;
    if (type === "number") input.min = 0;
    label.append(input);
    return label;
  };
  const code = () => fill("产品编码", "text", "eventCode", "");
  const next = byId("eventAt").value ? localMinute(Date.parse(byId("eventAt").value) + 2 * 86400000) : "";
  let fields;
  if (kind === "insert") {
    fields = [code(), fill("型号", "text", "eventModel", ""), fill("件数", "number", "eventQty", "3000"),
      fill("交期", "datetime-local", "eventDue", next)];
  } else if (kind === "delay") {
    fields = [code(), fill("预计可用时间", "datetime-local", "eventAvailable", next)];
  } else {
    const label = make("label", "停机产线");
    const select = make("select");
    select.id = "eventLine"; select.required = true;
    select.append(new Option("选择产线", ""), ...options.map((line) => new Option(line, line)));
    label.append(select);
    fields = [label, fill("停机分钟", "number", "eventDuration", "240"),
      fill("停机前完成件数（在制时）", "number", "eventCompleted", "", false)];
  }
  byId("eventFields").replaceChildren(...fields);
}

function renderEvents() {
  byId("eventList").replaceChildren(...state.events.map((event, index) => {
    const summary = event.type === "insert" ? "插单 · " + event.code + " · " + event.qty + " 件"
      : event.type === "fault" ? "停机 · " + event.line + " · " + event.duration_minutes + " 分钟"
        : "物料延期 · " + event.code + " · " + timeText(event.available);
    const li = make("li", summary);
    const remove = make("button", "移除", "secondary");
    remove.type = "button";
    remove.setAttribute("aria-label", "移除" + summary);
    remove.addEventListener("click", () => { state.events.splice(index, 1); renderEvents(); });
    li.append(remove);
    return li;
  }));
  byId("runReplan").disabled = !state.events.length;
}

byId("eventForm").addEventListener("submit", (e) => {
  e.preventDefault();
  const type = byId("eventType").value;
  const value = (id) => byId(id).value;
  let event;
  if (type === "insert") {
    event = { type, code: value("eventCode"), model: value("eventModel"), qty: Number(value("eventQty")), due: value("eventDue") };
    if (event.qty <= 0) return message("插单件数必须大于 0。", true);
  } else if (type === "fault") {
    event = { type, line: value("eventLine"), duration_minutes: Number(value("eventDuration")),
      completed_qty: value("eventCompleted") === "" ? null : Number(value("eventCompleted")) };
    if (event.duration_minutes <= 0) return message("停机分钟必须大于 0。", true);
  } else {
    event = { type, code: value("eventCode"), available: value("eventAvailable") };
  }
  state.events.push(event);
  renderEvents();
  message("事件已加入待处理列表。");
});

byId("runReplan").addEventListener("click", (e) => {
  const parent = state.selected;
  action(e.currentTarget, "正在保护已执行任务并计算余量…", async () => {
    const results = await post("/api/plans/" + parent.id + "/replan", {
      at: byId("eventAt").value, freeze_hours: Number(byId("freeze").value), events: state.events
    });
    accept(results, parent);
  }).finally(renderEvents);
});

byId("lineFilter").addEventListener("change", drawGantt);
byId("timeFilter").addEventListener("change", drawGantt);
byId("orderSearch").addEventListener("input", renderOrders);
byId("orderFilter").addEventListener("change", renderOrders);
byId("eventType").addEventListener("change", eventFields);
eventFields();
