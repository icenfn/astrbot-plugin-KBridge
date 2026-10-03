const bridge = window.AstrBotPluginPage;
const $ = (id) => document.getElementById(id);

let pollTimer = null;
let stats = null;
let platformConfig = null;
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
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12.5l5 5L20 6.5"/></svg>',
  link: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1.5 1.5"/><path d="M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1.5-1.5"/></svg>',
  book: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5v14z"/><path d="M20 17v4H6.5A2.5 2.5 0 0 1 4 18.5"/></svg>',
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

function showFullPage({ title, contentHtml, onMount }) {
  return new Promise((resolve) => {
    let overlay = document.getElementById("full-modal");
    const close = () => {
      overlay.classList.remove("open");
      resolve(false);
    };
    if (!overlay) {
      overlay = document.createElement("div");
      overlay.id = "full-modal";
      overlay.className = "modal-overlay full";
      overlay.innerHTML = `<div class="modal full">
        <div class="modal-head"><span class="modal-title"></span><button class="modal-x" aria-label="关闭">×</button></div>
        <div class="modal-body full-body"></div>
      </div>`;
      document.body.appendChild(overlay);
      overlay.querySelector(".modal-x").onclick = close;
      overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
      document.addEventListener("keydown", (ev) => {
        if (ev.key === "Escape" && overlay.classList.contains("open")) close();
      });
    }
    overlay.querySelector(".modal-title").textContent = title;
    const body = overlay.querySelector(".modal-body");
    body.innerHTML = contentHtml;
    if (onMount) onMount(body, close);
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
  return await bridge.apiGet("subs");
}

async function loadConfig() {
  platformConfig = await bridge.apiGet("config");
  return platformConfig;
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
    const isSet = (p.fields || []).every((f) => platformConfig.values[f.key]);
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
    ${stats.configured && !Object.values(stats.configured).some(Boolean)
      ? `<div class="notice warn">尚无平台配置：请到「平台配置」填写凭据</div>` : ""}`;
  const all = document.querySelector("[data-action=sync-all]");
  if (all) all.onclick = () => triggerSync();
  const g1 = document.querySelector("[data-action=goto-platforms]");
  if (g1) g1.onclick = () => switchView("platforms");
  const g2 = document.querySelector("[data-action=goto-subs]");
  if (g2) g2.onclick = () => switchView("subs");
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
          ? (p.fields || []).every((f) => platformConfig.values[f.key])
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
          <div class="platform-desc">${p.supported ? "点击进入配置" : "开发中，敬请期待"}</div>
          ${p.supported ? '<div class="open-hint">进入配置 ›</div>' : ""}
        </div>`;
      }).join("")}
    </div>`;
  root.querySelectorAll(".platform-card").forEach((card) => {
    card.onclick = () => openPlatformConfig(card.dataset.platform);
  });
}

async function openPlatformConfig(id) {
  const p = platformConfig?.platforms?.[id];
  if (!p) return;
  if (!p.supported) {
    toast(`${p.name} 即将支持`, "warn");
    return;
  }
  await showFullPage({
    title: `${p.name} · 平台配置`,
    contentHtml: `
      ${(p.fields || []).map((f) => {
        const isSecret = !!f.secret;
        const cur = platformConfig.values[f.key];
        const has = typeof cur === "string" ? !!cur : !!cur;
        return `
        <div class="pfield">
          <label>${f.label}</label>
          <input class="input" data-key="${f.key}" type="${isSecret ? "password" : "text"}"
            autocomplete="off"
            value="${isSecret ? "" : esc(typeof cur === "string" ? cur : "")}"
            placeholder="${isSecret ? (has ? "已配置（留空保持不变）" : `输入 ${f.label}`) : `默认 ${cur || ""}`}" />
        </div>`;
      }).join("")}
      <p class="add-hint dim">ima 目标知识库自动创建（名称与源一致）；有道云同步至上方指定的 AstrBot 知识库</p>
      <button class="btn primary" id="pf-save">保存配置</button>`,
    onMount: (body, close) => {
      body.querySelector("#pf-save").onclick = async () => {
        const fields = {};
        body.querySelectorAll(".input[data-key]").forEach((inp) => {
          const v = inp.value.trim();
          if (v) fields[inp.dataset.key] = v;
        });
        if (!Object.keys(fields).length) {
          toast("没有需要保存的内容", "warn");
          return;
        }
        try {
          const r = await bridge.apiPost("config", { fields });
          toast(`已保存：${r.saved.join(", ")}`, "success");
          await loadConfig();
          await loadStats();
          renderPlatforms();
          renderOverview();
          close();
        } catch (e) {
          toast(e.message, "error");
        }
      };
    },
  });
}

function subCard(s) {
  const statusText = {
    ok: "正常",
    error: "异常",
    partial: "部分失败",
    pending: "待同步",
  }[s.last_status] || s.last_status;
  const statusCls = s.last_status === "ok" ? "ok" : s.last_status === "error" ? "err" : "warn";
  const name = s.display_name || s.kb_name || s.kb_id || "未命名";
  const target = s.platform === "youdao" ? s.target_kb || "YoudaoNote" : "";
  return `
    <div class="sub-card">
      <div class="sub-main">
        <div class="sub-line1">
          <span class="sub-name">${esc(name)}</span>
          <span class="badge ${statusCls}" title="${esc(s.last_error || "")}">${statusText}</span>
        </div>
        <div class="sub-line2">
          ${target ? `<span class="dim">目标库 ${esc(target)}</span>` : ""}
          <span class="dim">已同步 ${s.synced_count}</span>
          <span class="dim">${s.last_sync_at ? `上次 ${s.last_sync_at}` : ""}</span>
        </div>
      </div>
      <div class="sub-ops">
        <button class="btn small" data-platform="${s.platform}" data-kb="${s.kb_id}">${ICONS.sync}同步</button>
      </div>
    </div>`;
}

function renderSubs() {
  const root = $("view-subs");
  const list = root._subs || [];
  const loading = root._loading;
  if (loading) {
    root.innerHTML = `<div class="loading"><span class="spinner lg"></span>加载中…</div>`;
    return;
  }
  const errs = list.filter((s) => s.error);
  const items = list.filter((s) => !s.error);
  const groups = new Map();
  items.forEach((s) => {
    const g = s.platform || "ima";
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(s);
  });
  let html = "";
  errs.forEach((e) => {
    const label = platformConfig?.platforms?.[e.platform]?.name || e.platform || "";
    html += `<div class="notice warn">${esc(label)}：${esc(e.message)}</div>`;
  });
  groups.forEach((subs, g) => {
    const label = platformConfig?.platforms?.[g]?.name || g;
    html += `
      <div class="sub-group">
        <div class="sub-group-title">${label}<span class="dim"> · ${subs.length}</span></div>
        <div class="sub-list">${subs.map((s) => subCard(s)).join("")}</div>
      </div>`;
  });
  if (!items.length && !errs.length) {
    html = `<div class="empty-state">暂无同步源 · 先到「平台配置」填写凭据</div>`;
  }
  root.innerHTML = html;
  root.querySelectorAll("[data-kb]").forEach((b) => {
    b.onclick = () => triggerSync(b.dataset.kb, b.dataset.platform || "ima");
  });
}

// ---------- 操作 ----------

async function triggerSync(kbId = null, platform = "ima") {
  try {
    toast(kbId ? "正在启动同步…" : "正在启动全量同步…");
    await bridge.apiPost("sync", kbId ? { kb_id: kbId, platform } : {});
    toast("同步已启动，后台执行中");
    startPoll();
  } catch (e) {
    toast(e.message, "error");
  }
}

function startPoll() {
  if (pollTimer) return;
  pollTimer = setInterval(async () => {
    try {
      const s = await loadStats();
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
}

main();
