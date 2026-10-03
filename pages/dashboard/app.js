const bridge = window.AstrBotPluginPage;
const $ = (id) => document.getElementById(id);

let pollTimer = null;
let logTimer = null;
let stats = null;
let platformConfig = null;
let kbsCache = [];
let subsCache = [];
let activeView = "overview";

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

const VIEW_TITLES = {
  overview: "总览",
  platforms: "平台配置",
  subs: "同步管理",
};

const ICONS = {
  sync: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v6h-6"/></svg>',
  trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13"/></svg>',
  plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12.5l5 5L20 6.5"/></svg>',
  link: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1.5 1.5"/><path d="M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1.5-1.5"/></svg>',
  book: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5v14z"/><path d="M20 17v4H6.5A2.5 2.5 0 0 1 4 18.5"/></svg>',
  clock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/></svg>',
  bolt: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2L4 14h6l-1 8 9-12h-6l1-8z"/></svg>',
};

function toast(message, type = "ok") {
  // type: ok(info 蓝) / success(绿) / error(红) / warn(橙)
  const wrap = $("toast-wrap");
  while (wrap.children.length >= 4) wrap.firstChild.remove(); // 最多同时 4 条
  const el = document.createElement("div");
  el.className = "toast " + type;
  const ic = { ok: ICONS.check, success: ICONS.check, error: ICONS.bolt, warn: ICONS.bolt }[type] || ICONS.check;
  el.innerHTML = `<span class="toast-ic">${ic}</span><span class="toast-msg">${message}</span><button class="toast-close" aria-label="关闭">×</button>`;
  wrap.append(el);
  el.querySelector(".toast-close").onclick = () => dismiss(el);
  requestAnimationFrame(() => el.classList.add("show"));
  const dur = type === "error" ? 5000 : type === "warn" ? 4000 : 2500;
  setTimeout(() => dismiss(el), dur);
}
function dismiss(el) {
  if (!el || el.classList.contains("out")) return;
  el.classList.remove("show");
  el.classList.add("out");
  setTimeout(() => el.remove(), 220);
}

function showConfirm({ title, message, danger = false, confirmText = "确认", extraHtml = "" }) {
  return new Promise((resolve) => {
    let overlay = document.getElementById("modal-overlay");
    if (!overlay) {
      overlay = document.createElement("div");
      overlay.id = "modal-overlay";
      overlay.className = "modal-overlay";
      overlay.innerHTML = `<div class="modal">
        <div class="modal-head"><span class="modal-title"></span><button class="modal-x" aria-label="关闭">×</button></div>
        <div class="modal-body"></div>
        <div class="modal-foot"><button class="btn" data-act="cancel">取消</button><button class="btn primary ${danger ? "danger" : ""}" data-act="ok">确认</button></div>
      </div>`;
      document.body.appendChild(overlay);
      overlay.querySelector(".modal-x").onclick = () => { overlay.classList.remove("open"); resolve(false); };
      overlay.querySelector('[data-act="cancel"]').onclick = () => { overlay.classList.remove("open"); resolve(false); };
      overlay.addEventListener("click", (e) => { if (e.target === overlay) { overlay.classList.remove("open"); resolve(false); } });
    }
    overlay.querySelector(".modal-title").textContent = title;
    const body = overlay.querySelector(".modal-body");
    body.innerHTML = `<div class="modal-msg">${message}</div>${extraHtml}`;
    const okBtn = overlay.querySelector('[data-act="ok"]');
    okBtn.textContent = confirmText;
    okBtn.onclick = () => { overlay.classList.remove("open"); resolve(true); };
    requestAnimationFrame(() => overlay.classList.add("open"));
  });
}

function setSyncUI(syncing) {
  $("sync-pill").classList.toggle("hidden", !syncing);
  $("side-sync").classList.toggle("hidden", !syncing);
  const allBtn = document.querySelector("[data-action=sync-all]");
  if (allBtn) {
    allBtn.disabled = syncing;
    allBtn.innerHTML = syncing ? '<span class="spinner"></span>同步中…' : ICONS.sync + "同步全部";
  }
}

// ---------- 数据 ----------

async function loadStats() {
  stats = await bridge.apiGet("stats");
  setSyncUI(stats.is_syncing);
  return stats;
}

async function loadSubs() {
  const r = await bridge.apiGet("subs");
  subsCache = r;
  return r;
}

async function loadConfig() {
  platformConfig = await bridge.apiGet("config");
  return platformConfig;
}

async function loadKbs(force = false) {
  if (!kbsCache.length || force) {
    kbsCache = await bridge.apiGet("kbs");
  }
  return kbsCache;
}

// ---------- 视图渲染 ----------

function renderOverview() {
  const root = $("view-overview");
  if (!stats) {
    root.textContent = "";
    return;
  }
  // 平台配置统计：已配置 x / 支持总数
  let supported = 0;
  let configured = 0;
  Object.entries(platformConfig?.platforms || {}).forEach(([id, p]) => {
    if (!p.supported) return;
    supported += 1;
    const isSet =
      id === "ima" &&
      platformConfig.values["ima_client_id"] &&
      platformConfig.values["ima_api_key"];
    if (isSet) configured += 1;
  });
  const platOk = supported > 0 && configured === supported;
  const cards = [
    {
      label: "平台配置",
      value: `已配置 ${configured}/${supported}`,
      cls: platOk ? "good" : "warn",
      icon: ICONS.link,
    },
    { label: "同步源", value: stats.sub_count, cls: "", icon: ICONS.book },
    { label: "已同步文档", value: stats.total_synced, cls: "", icon: ICONS.check },
  ];
  root.innerHTML = `
    <div class="stat-grid">
      ${cards.map((c) => `
        <div class="stat-card">
          <div class="stat-ic ${c.cls}">${c.icon}</div>
          <div>
            <div class="stat-label">${c.label}</div>
            <div class="stat-value ${c.cls}">${c.value}</div>
          </div>
        </div>`).join("")}
    </div>
    <div class="quick-actions">
      <button class="btn primary" data-action="sync-all">${ICONS.sync}同步全部</button>
      <button class="btn" data-action="goto-platforms">${ICONS.link}去配置平台</button>
      <button class="btn" data-action="goto-subs">${ICONS.book}管理同步</button>
    </div>
    ${!stats.ima_configured ? `<div class="notice warn">IMA 未配置：请到「平台配置」填写 Client ID / API Key</div>` : ""}`;
  bindQuickActions();
}

function bindQuickActions() {
  const goto = (view) => () => switchView(view);
  const all = document.querySelector("[data-action=sync-all]");
  if (all) all.onclick = () => triggerSync(null);
  const g1 = document.querySelector("[data-action=goto-platforms]");
  if (g1) g1.onclick = goto("platforms");
  const g2 = document.querySelector("[data-action=goto-subs]");
  if (g2) g2.onclick = goto("subs");
}

function renderPlatforms() {
  const root = $("view-platforms");
  if (!platformConfig) {
    root.textContent = "";
    return;
  }
  const platforms = platformConfig.platforms;
  root.innerHTML = `<p class="lead">配置各知识源平台凭据，同步即基于此连接</p>
    <div class="platform-grid">
      ${Object.entries(platforms).map(([id, p]) => {
        const set = p.supported
          ? platformConfig.values["ima_client_id"] && platformConfig.values["ima_api_key"]
          : false;
        return `
        <div class="platform-card ${p.supported ? "" : "soon"}" data-platform="${id}">
          <div class="platform-head">
            <div class="platform-ic ${set ? "good" : ""}">${ICONS.link}</div>
            <span class="badge ${p.supported ? (set ? "ok" : "warn") : "soon"}">
              ${p.supported ? (set ? "已配置" : "未配置") : "即将支持"}
            </span>
          </div>
          <div class="platform-name">${p.name}</div>
          <div class="platform-desc">${p.supported ? "点击配置凭据并管理同步" : "开发中，敬请期待"}</div>
          ${p.supported ? '<div class="platform-form hidden"></div>' : ""}
        </div>`;
      }).join("")}
    </div>`;

  // ima 平台展开表单
  const card = document.querySelector('[data-platform="ima"]');
  if (card) {
    const form = card.querySelector(".platform-form");
    form.innerHTML = `
      <div class="pfield">
        <label>Client ID</label>
        <input class="input" id="pf-cid" type="text" autocomplete="off"
          placeholder="${platformConfig.values["ima_client_id"] ? "已配置（留空保持不变）" : "输入 Client ID"}" />
      </div>
      <div class="pfield">
        <label>API Key</label>
        <input class="input" id="pf-key" type="text" autocomplete="off"
          placeholder="${platformConfig.values["ima_api_key"] ? "已配置（留空保持不变）" : "输入 API Key"}" />
      </div>
      <button class="btn primary" data-action="save-ima">保存配置</button>`;
    card.addEventListener("click", (e) => {
      if (e.target.closest("[data-action=save-ima]")) return;
      if (e.target.closest(".platform-form")) return;
      form.classList.toggle("hidden");
    });
    const save = form.querySelector("[data-action=save-ima]");
    save.onclick = async () => {
      const fields = {};
      const cid = $("pf-cid").value.trim();
      const key = $("pf-key").value.trim();
      if (cid) fields.ima_client_id = cid;
      if (key) fields.ima_api_key = key;
      if (!Object.keys(fields).length) {
        toast("没有需要保存的内容", "warn");
        return;
      }
      try {
        const r = await bridge.apiPost("config", { fields });
        toast(`已保存：${r.saved.join(", ")}`);
        $("pf-cid").value = "";
        $("pf-key").value = "";
        await loadConfig();
        await loadStats();
        renderPlatforms();
        renderOverview();
      } catch (e) {
        toast(e.message, "error");
      }
    };
  }
}

function renderSubs() {
  const root = $("view-subs");
  const subs = root._subs || [];
  const loading = root._loading;
  if (loading) {
    root.innerHTML = `<div class="loading"><span class="spinner lg"></span>加载中…</div>`;
    return;
  }
  const kbName = (k) => k.name || k.kb_name || k.title || k.id || "未命名";
  // 按平台分组渲染 select（optgroup）
  const groups = new Map();
  kbsCache.forEach((k) => {
    const g = k.platform || "ima";
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(k);
  });
  let sel = "";
  groups.forEach((items, g) => {
    const label = g === "ima" ? "腾讯 ima" : g;
    sel += `<optgroup label="${label}">${items
      .map((k) => `<option value="${k.id}" data-name="${kbName(k)}" data-platform="${g}">${kbName(k)}</option>`)
      .join("")}</optgroup>`;
  });
  if (!sel) sel = '<option value="">加载 IMA 知识库…</option>';
  // 同步列表按平台分组
  const subGroups = new Map();
  subs.forEach((s) => {
    const g = s.platform || "ima";
    if (!subGroups.has(g)) subGroups.set(g, []);
    subGroups.get(g).push(s);
  });
  let subListHtml = "";
  if (subs.length) {
    subGroups.forEach((items, g) => {
      const label = g === "ima" ? "腾讯 ima" : g;
      subListHtml += `
        <div class="sub-group">
          <div class="sub-group-title">${label}<span class="dim"> · ${items.length}</span></div>
          <div class="sub-list">${items.map((s, i) => subCard(s, subs.indexOf(s))).join("")}</div>
        </div>`;
    });
  } else {
    subListHtml = `<div class="empty-state">暂无同步 · 从上方选择 IMA 知识库拉取</div>`;
  }
  root.innerHTML = `
    <div class="add-panel">
      <div class="add-row">
        <select class="input grow" id="sub-kb">
          ${sel || '<option value="">加载 IMA 知识库…</option>'}
        </select>
        <button class="btn primary" data-action="add-sub">${ICONS.plus}拉取</button>
      </div>
      <p class="add-hint dim">拉取后自动创建 AstrBot 知识库（名称与 IMA 知识库一致），无需手动指定目标库名</p>
    </div>
    ${subListHtml}
    <div class="log-card">
      <div class="log-head">
        <span>同步日志</span>
        <button class="btn small" data-action="reload-logs">${ICONS.sync}刷新</button>
      </div>
      <div class="log-list" id="log-list">
        <div class="log-empty dim">加载中…</div>
      </div>
    </div>`;

  root.querySelector("[data-action=reload-logs]").onclick = loadLogs;
  loadLogs();
  const addBtn = root.querySelector("[data-action=add-sub]");
  addBtn.onclick = async () => {
    const kbSel = $("sub-kb");
    const kbId = kbSel.value;
    if (!kbId) {
      toast("请先选择 IMA 知识库", "warn");
      return;
    }
    const opt = kbSel.selectedOptions[0];
    const kbName = opt?.dataset?.name || "";
    const platform = opt?.dataset?.platform || "ima";
    try {
      const r = await bridge.apiPost("subs/add", {
        kb_id: kbId,
        kb_name: kbName,
        platform,
      });
      toast(`已拉取：${r.sub.kb_name}`);
      await refresh();
    } catch (e) {
      toast(e.message, "error");
    }
  };
  root.querySelectorAll("[data-action=sub-sync]").forEach((b) => {
    b.onclick = () => triggerSync(Number(b.dataset.index));
  });
  root.querySelectorAll("[data-action=sub-del]").forEach((b) => {
    b.onclick = () => removeSub(Number(b.dataset.index));
  });
}

function subCard(s, i) {
  const statusText = {
    ok: "正常",
    error: "异常",
    partial: "部分失败",
    pending: "待同步",
  }[s.last_status] || s.last_status;
  const statusCls = s.last_status === "ok" ? "ok" : s.last_status === "error" ? "err" : "warn";
  return `
    <div class="sub-card">
      <div class="sub-main">
        <div class="sub-line1">
          <span class="sub-name">${s.kb_name || s.kb_id}</span>
          <span class="badge ${statusCls}" title="${s.last_error || ""}">${statusText}</span>
        </div>
        <div class="sub-line2">
          <span class="dim">已同步 ${s.synced_count}</span>
          <span class="dim">${s.last_sync_at ? `上次 ${s.last_sync_at}` : ""}</span>
        </div>
      </div>
      <div class="sub-ops">
        <button class="btn small" data-action="sub-sync" data-index="${i}">${ICONS.sync}同步</button>
        <button class="btn small danger" data-action="sub-del" data-index="${i}">${ICONS.trash}</button>
      </div>
    </div>`;
}


async function loadLogs() {
  const list = document.getElementById("log-list");
  if (!list) return;
  try {
    const r = await bridge.apiGet("logs");
    if (!r.items || !r.items.length) {
      list.innerHTML = `<div class="log-empty dim">暂无 KBridge 日志 · 触发一次同步后这里会显示同步过程与结果</div>`;
      return;
    }
    list.innerHTML = r.items
      .map((it) => {
        const lv = it.level || "INFO";
        const cls = lv === "ERRO" || lv === "ERROR" ? "err" : lv === "WARN" || lv === "WARNING" ? "warn" : lv === "DBUG" || lv === "DEBUG" ? "dbg" : "";
        return `<div class="log-line ${cls}"><span class="log-time">${it.time || ""}</span><span class="log-lv">${esc(lv)}</span><span class="log-msg">${esc(it.message || "")}</span></div>`;
      })
      .join("");
  } catch {
    /* 静默 */
  }
}

// ---------- 操作 ----------

async function triggerSync(index = null) {
  try {
    toast(index === null ? "正在启动全量同步…" : "正在启动同步…");
    await bridge.apiPost("sync", index === null ? {} : { index });
    toast("同步已启动，后台执行中");
    startPoll();
  } catch (e) {
    toast(e.message, "error");
  }
}

async function removeSub(index) {
  const s = subsCache[index];
  if (!s) return;
  const kbName = s.target_kb || s.kb_name || "（未知）";
  const checked = await showConfirm({
    title: "删除同步源",
    message: `确定删除同步源「${esc(s.kb_name || s.kb_id)}」？`,
    danger: true,
    confirmText: "删除",
    extraHtml: `<label class="modal-check"><input type="checkbox" id="del-kb-chk" /> 同时删除 AstrBot 知识库「${esc(kbName)}」</label>
      <p class="modal-warn dim">删除知识库不可恢复，其下所有文档与索引将一并清除</p>`,
  });
  if (!checked) return;
  const delKb = document.getElementById("del-kb-chk")?.checked || false;
  try {
    const r = await bridge.apiPost(`subs/${index}/remove`, { delete_kb: delKb });
    toast(delKb && r.kb_deleted ? `已删除同步源与知识库：${r.name}` : `已删除同步源：${r.name}`, "success");
    await refresh();
  } catch (e) {
    toast(e.message, "error");
  }
}

function startPoll() {
  if (pollTimer) return;
  pollTimer = setInterval(async () => {
    try {
      const s = await loadStats();
      renderSchedule();
      renderOverview();
      if (!s.is_syncing) {
        clearInterval(pollTimer);
        pollTimer = null;
        toast("同步完成");
        await refresh();
      }
    } catch {
      /* 静默 */
    }
  }, 2000);
}

async function refresh() {
  const [s, subs] = await Promise.all([loadStats(), loadSubs()]);
  const view = $("view-subs");
  view._subs = subs;
  renderSubs();
  renderOverview();
}

// ---------- 导航 ----------

function switchView(view) {
  activeView = view;
  $("view-title").textContent = VIEW_TITLES[view];
  document.querySelectorAll(".view").forEach((el) => {
    el.classList.toggle("active", el.id === `view-${view}`);
  });
  document.querySelectorAll(".nav-item, .tab-item").forEach((el) => {
    el.classList.toggle("active", el.dataset.view === view);
  });
  // 同步管理视图：日志 5s 轮询，离开停止
  if (view === "subs") {
    if (!logTimer) {
      logTimer = setInterval(loadLogs, 5000);
    }
  } else if (logTimer) {
    clearInterval(logTimer);
    logTimer = null;
  }
  renderCurrentView();
}

function renderCurrentView() {
  if (activeView === "overview") renderOverview();
  if (activeView === "platforms") renderPlatforms();
  if (activeView === "subs") renderSubs();
}

// ---------- 初始化 ----------

async function main() {
  const context = await bridge.ready();
  const applyTheme = (isDark) => {
    document.documentElement.dataset.dark = isDark ? "true" : "false";
  };
  applyTheme(context.isDark);
  bridge.onContext((ctx) => applyTheme(ctx.isDark));

  document.querySelectorAll(".nav-item, .tab-item").forEach((el) => {
    el.onclick = () => switchView(el.dataset.view);
  });
  $("btn-refresh").onclick = async () => {
    try {
      await loadKbs(true);
      await refresh();
      toast("已刷新");
    } catch (e) {
      toast(e.message, "error");
    }
  };

  // 首屏加载
  try {
    await Promise.all([loadStats(), loadConfig()]);
  } catch (e) {
    toast(e.message, "error");
  }
  const view = $("view-subs");
  try {
    view._loading = true;
    renderSubs();
    await loadKbs();
    const subs = await loadSubs();
    view._subs = subs;
    view._loading = false;
    renderSubs();
  } catch (e) {
    view._loading = false;
    renderSubs();
  }
  renderOverview();
  renderPlatforms();
  renderSchedule();
}

main();
