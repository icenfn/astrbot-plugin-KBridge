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
  sched: "定时同步",
};

const ICONS = {
  sync: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v6h-6"/></svg>',
  plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>',
  trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12.5l5 5L20 6.5"/></svg>',
  link: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1.5 1.5"/><path d="M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1.5-1.5"/></svg>',
  book: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5v14z"/><path d="M20 17v4H6.5A2.5 2.5 0 0 1 4 18.5"/></svg>',
  bolt: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2L4 14h6l-1 8 9-12h-6l1-8z"/></svg>',
  clock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/></svg>',
};

function confirmDialog({ title, message, okText = "确认删除", danger = true }) {
  return new Promise((resolve) => {
    const ov = document.createElement("div");
    ov.className = "modal-overlay";
    ov.innerHTML = `<div class="modal confirm-modal">
      <div class="modal-head"><span class="modal-title"></span><button class="modal-x" aria-label="关闭">×</button></div>
      <div class="confirm-body">
        <div class="confirm-msg"></div>
        <div class="confirm-ops">
          <button class="btn" data-c="no">取消</button>
          <button class="btn ${danger ? "danger" : "primary"}" data-c="yes"></button>
        </div>
      </div>
    </div>`;
    document.body.appendChild(ov);
    const done = (v) => { ov.remove(); resolve(v); };
    ov.querySelector(".modal-title").textContent = title;
    ov.querySelector(".confirm-msg").textContent = message;
    ov.querySelector('[data-c="yes"]').textContent = okText;
    ov.querySelector('[data-c="no"]').onclick = () => done(false);
    ov.querySelector('[data-c="yes"]').onclick = () => done(true);
    ov.querySelector(".modal-x").onclick = () => done(false);
    ov.addEventListener("click", (e) => { if (e.target === ov) done(false); });
    requestAnimationFrame(() => ov.classList.add("open"));
  });
}

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
    if (!p.supported || p.enabled === false) return;
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
    {
      label: "定时同步",
      value: stats.sched?.enabled ? stats.sched.text : "未启用",
      cls: stats.sched?.enabled ? "good" : "",
      icon: ICONS.clock,
    },
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
      <button class="btn" data-action="goto-sched">${ICONS.clock}定时设置</button>
    </div>
    ${stats.configured && !Object.values(stats.configured).some(Boolean)
      ? `<div class="notice warn">尚无平台配置：请到「平台配置」填写凭据</div>` : ""}`;
  const all = document.querySelector("[data-action=sync-all]");
  if (all) all.onclick = () => triggerSync();
  const g1 = document.querySelector("[data-action=goto-platforms]");
  if (g1) g1.onclick = () => switchView("platforms");
  const g2 = document.querySelector("[data-action=goto-subs]");
  if (g2) g2.onclick = () => switchView("subs");
  const g3 = document.querySelector("[data-action=goto-sched]");
  if (g3) g3.onclick = () => switchView("sched");
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
        const noFields = !(p.fields || []).length;
        const set = p.supported
          ? (noFields || (p.fields || []).every((f) => platformConfig.values[f.key]))
          : false;
        const off = p.enabled === false;
        return `
        <div class="platform-card ${p.supported ? "" : "soon"} ${off ? "off" : ""}" data-platform="${id}">
          <div class="platform-head">
            <div class="platform-ic ${set ? "good" : ""}">${ICONS.link}</div>
            <label class="p-toggle" title="启用/禁用平台">
              <input type="checkbox" data-pen="${id}" ${off ? "" : "checked"} ${p.supported ? "" : "disabled"} />
              <span>${off ? "已禁用" : "已启用"}</span>
            </label>
          </div>
          <div class="platform-name">${p.name}</div>
          <div class="platform-desc">${p.supported ? (off ? "已禁用，点击可查看/修改配置" : (noFields ? "点击管理网页分组" : "点击进入配置")) : "开发中，敬请期待"}</div>
          <span class="badge ${p.supported ? (set ? "ok" : "warn") : "soon"}">
            ${p.supported ? (noFields ? "已就绪" : (set ? "已配置" : "未配置")) : "即将支持"}
          </span>
          ${p.supported ? '<div class="open-hint">进入配置 ›</div>' : ""}
        </div>`;
      }).join("")}
    </div>`;
  root.querySelectorAll(".platform-card").forEach((card) => {
    card.addEventListener("click", (e) => {
      if (e.target.closest(".p-toggle")) return; // 开关区域不触发配置弹窗
      openPlatformConfig(card.dataset.platform).catch((err) => {
        console.error("[KBridge] 打开平台配置失败:", err);
        toast(`打开配置失败：${err.message}`, "error");
      });
    });
  });
  root.querySelectorAll("[data-pen]").forEach((cb) => {
    // 开关点击不触发卡片进入配置
    cb.addEventListener("click", (e) => e.stopPropagation());
    cb.closest("label")?.addEventListener("click", (e) => e.stopPropagation());
    cb.onchange = async () => {
      const id = cb.dataset.pen;
      try {
        await bridge.apiPost("config", { platform_enabled: { [id]: cb.checked } });
        await loadConfig();
        await Promise.all([renderPlatforms(), refresh()]);
        toast(`平台「${platforms[id]?.name || id}」已${cb.checked ? "启用" : "禁用"}`, "success");
      } catch (e) {
        toast(e.message, "error");
        cb.checked = !cb.checked;
      }
    };
  });
}

async function openPlatformConfig(id) {
  const p = platformConfig?.platforms?.[id];
  if (!p) return;
  if (!p.supported) {
    toast(`${p.name} 即将支持`, "warn");
    return;
  }
  const ghBlock = id === "github"
    ? `<div class="gh-section">
        <div class="gh-head">已添加仓库<span class="dim"> · 0</span></div>
        <div class="gh-list"><div class="empty-state small">加载中…</div></div>
        <div class="add-row">
          <input class="input grow" id="gh-url" placeholder="https://github.com/owner/repo 或 /tree/branch/path" />
          <button class="btn primary" data-action="add-gh">${ICONS.plus}添加仓库</button>
        </div>
        <p class="add-hint dim">支持整个仓库或 tree 子目录；仅导入 AstrBot 可解析格式（md/txt/pdf/docx/xlsx/epub 等）</p>
      </div>` : "";
  const u2Block = id === "url2kb"
    ? `<div class="u2k-wrap">
        <div class="u2k-add">
          <div class="u2k-add-title">添加分组<span class="dim"> · 分组名即 AstrBot 知识库名</span></div>
          <div class="add-row">
            <input class="input grow" id="u2k-name" placeholder="分组名称" />
            <input class="input grow" id="u2k-note" placeholder="备注（可选）" />
            <button class="btn primary" data-action="add-u2k">${ICONS.plus}添加分组</button>
          </div>
        </div>
        <div class="u2k-groups"><div class="empty-state small">加载中…</div></div>
      </div>` : "";
  await showFullPage({
    title: `${p.name} · 平台配置`,
    contentHtml: `
      ${(p.fields || []).map((f) => {
        const isSecret = !!f.secret;
        const cur = platformConfig.values[f.key];
        const has = !!cur;
        return `
        <div class="pfield">
          <label>${f.label}</label>
          <input class="input" data-key="${f.key}" type="${isSecret ? "password" : "text"}"
            autocomplete="off"
            value="${isSecret ? "" : esc(typeof cur === "string" ? cur : "")}"
            placeholder="${isSecret ? (has ? "已配置（留空保持不变）" : `输入 ${f.label}`) : `默认 ${cur || ""}`}" />
        </div>`;
      }).join("")}
      ${ghBlock}
      ${u2Block}
      <button class="btn primary" id="pf-save">保存配置</button>`,
    onMount: (body, close) => {
      const ghList = body.querySelector(".gh-list");
      const ghHead = body.querySelector(".gh-head .dim");
      const renderGhList = async () => {
        try {
          const r = await bridge.apiGet("subs");
          const repos = (r || []).filter((x) => x.platform === "github" && !x.error);
          if (ghHead) ghHead.textContent = ` · ${repos.length}`;
          ghList.innerHTML = repos.length
            ? repos.map((s) => `
                <div class="gh-row">
                  <span class="gh-name" title="${esc(s.kb_id)}">${esc(s.display_name || s.kb_name)}</span>
                  <button class="btn small danger" data-rm="${esc(s.kb_id)}">${ICONS.trash}</button>
                </div>`).join("")
            : `<div class="empty-state small">暂无仓库</div>`;
          ghList.querySelectorAll("[data-rm]").forEach((b) => {
            b.addEventListener("click", () => rmRepo(b.dataset.rm));
          });
        } catch (e) {
          toast(e.message, "error");
        }
      };
      const rmRepo = async (kbId) => {
        const ok = await confirmDialog({
          title: "删除仓库订阅",
          message: `确认移除仓库订阅「${kbId}」？其已同步到 AstrBot 的本地知识库数据不受影响。`,
          okText: "删除",
        });
        if (!ok) return;
        try {
          const r = await bridge.apiPost("subs/remove", { kb_id: kbId, platform: "github" });
          toast(`已删除仓库：${r.name}`, "success");
          await renderGhList();
          await refresh();
        } catch (e) {
          toast(e.message, "error");
        }
      };
      const addGh = async () => {
        const url = body.querySelector("#gh-url")?.value?.trim();
        if (!url) {
          toast("请输入 GitHub 仓库地址", "warn");
          return;
        }
        try {
          const r = await bridge.apiPost("subs/add", { url, platform: "github" });
          toast(r.exists ? `已在同步列表：${r.sub.kb_name}` : `已添加仓库：${r.sub.kb_name}`, r.exists ? "warn" : "success");
          body.querySelector("#gh-url").value = "";
          await renderGhList();
          await refresh();
        } catch (e) {
          toast(e.message, "error");
        }
      };
      body.querySelector("[data-action=add-gh]")?.addEventListener("click", addGh);
      body.querySelector("#gh-url")?.addEventListener("keydown", (e) => { if (e.key === "Enter") addGh(); });
      if (ghList) renderGhList();

      // ---- url2kb：分组管理（分组名 = AstrBot 知识库名） ----
      if (id === "url2kb") {
        const u2Root = body.querySelector(".u2k-groups");
        const h = (v) => String(v ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
        const groupHtml = (g) => `
          <div class="u2k-group">
            <div class="u2k-head">
              <input class="input u2k-name-i" data-gname="${h(g.id)}" value="${h(g.name)}" placeholder="分组名称" />
              <input class="input u2k-note-i" data-gnote="${h(g.id)}" value="${h(g.note || "")}" placeholder="备注（可选）" />
              <button class="btn small" data-save-g="${h(g.id)}">保存</button>
              <button class="btn small danger" data-rmg="${h(g.id)}" title="删除分组">${ICONS.trash}</button>
            </div>
            <div class="u2k-urls">
              ${(g.urls || []).length ? (g.urls || []).map((u) => `
                <div class="u2k-url">
                  <span class="u2k-title" title="${h(u.title || u.url)}">${h(u.title || u.url)}</span>
                  <span class="dim u2k-url2" title="${h(u.url)}">${h(u.url)}</span>
                  <button class="btn small danger u2k-rm" data-rmu="${h(g.id)}" data-uid="${h(u.id)}" title="删除 URL">${ICONS.trash}</button>
                </div>`).join("") : `<div class="empty-state small">暂无 URL</div>`}
              <div class="add-row">
                <input class="input grow" id="u2k-url-${h(g.id)}" placeholder="https:// 输入网页地址" />
                <button class="btn" data-add-u="${h(g.id)}">${ICONS.plus}添加 URL</button>
              </div>
            </div>
          </div>`;
        const renderU2k = async () => {
          try {
            const r = await bridge.apiGet("url2kb");
            const groups = r.groups || [];
            u2Root.innerHTML = groups.length
              ? groups.map(groupHtml).join("")
              : `<div class="empty-state small">暂无分组 · 先添加一个分组</div>`;
            u2Root.querySelectorAll("[data-rmg]").forEach((b) =>
              b.addEventListener("click", () => removeGroup(b.dataset.rmg)));
            u2Root.querySelectorAll("[data-save-g]").forEach((b) =>
              b.addEventListener("click", () => saveGroup(b.dataset.saveG)));
            u2Root.querySelectorAll("[data-rmu]").forEach((b) =>
              b.addEventListener("click", () => removeUrl(b.dataset.rmu, b.dataset.uid)));
            u2Root.querySelectorAll("[data-add-u]").forEach((b) =>
              b.addEventListener("click", () => addUrl(b.dataset.addU)));
          } catch (e) {
            toast(e.message, "error");
          }
        };
        const removeGroup = async (gid) => {
          try {
            await bridge.apiPost("url2kb/groups", { action: "remove", id: gid });
            toast("已删除分组", "success");
            await renderU2k();
            await refresh();
          } catch (e) { toast(e.message, "error"); }
        };
        const saveGroup = async (gid) => {
          try {
            const name = u2Root.querySelector(`[data-gname="${gid}"]`)?.value?.trim();
            const note = u2Root.querySelector(`[data-gnote="${gid}"]`)?.value?.trim() || "";
            if (!name) { toast("分组名称必填", "warn"); return; }
            await bridge.apiPost("url2kb/groups", { action: "update", id: gid, name, note });
            toast("已保存分组", "success");
            await renderU2k();
            await refresh();
          } catch (e) { toast(e.message, "error"); }
        };
        const removeUrl = async (gid, uid) => {
          try {
            await bridge.apiPost("url2kb/urls", { action: "remove", group_id: gid, url_id: uid });
            toast("已删除 URL", "success");
            await renderU2k();
            await refresh();
          } catch (e) { toast(e.message, "error"); }
        };
        const addUrl = async (gid) => {
          const inp = u2Root.querySelector(`#u2k-url-${gid}`);
          const btn = u2Root.querySelector(`[data-add-u="${gid}"]`);
          const url = inp?.value?.trim();
          if (!url) { toast("请输入网页地址", "warn"); return; }
          const idleLabel = `${ICONS.plus}添加 URL`;
          if (btn) { btn.disabled = true; btn.textContent = "识别中…"; }
          try {
            const r = await bridge.apiPost("url2kb/urls", { action: "add", group_id: gid, url });
            toast(r.title ? `已添加，识别标题：${r.title}` : "已添加（未能自动识别标题）", r.title ? "success" : "warn");
            inp.value = "";
            await renderU2k();
            await refresh();
          } catch (e) { toast(e.message, "error"); }
          finally { if (btn) { btn.disabled = false; btn.textContent = idleLabel; } }
        };
        const addGroup = async () => {
          const name = body.querySelector("#u2k-name")?.value?.trim();
          if (!name) { toast("分组名称（知识库名）必填", "warn"); return; }
          const note = body.querySelector("#u2k-note")?.value?.trim() || "";
          try {
            await bridge.apiPost("url2kb/groups", { action: "add", name, note });
            toast(`已添加分组：${name}`, "success");
            body.querySelector("#u2k-name").value = "";
            body.querySelector("#u2k-note").value = "";
            await renderU2k();
            await refresh();
          } catch (e) { toast(e.message, "error"); }
        };
        body.querySelector("[data-action=add-u2k]")?.addEventListener("click", addGroup);
        renderU2k();
      }
      body.querySelector("#pf-save").onclick = async () => {
        const fields = {};
        body.querySelectorAll(".input[data-key]").forEach((inp) => {
          const v = inp.value.trim();
          if (v) fields[inp.dataset.key] = v;
        });
        if (id === "github" && !platformConfig.values.github_token && !fields.github_token) {
          toast("GitHub Token 为必填项，请先填写", "warn");
          return;
        }
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
  const off = platformConfig?.platforms?.[s.platform]?.enabled === false;
  const cur = stats?.current;
  const syncingThis = cur && cur.kb_id === s.kb_id;
  const otherSyncing = (stats?.is_syncing && !syncingThis) || false;
  const missing = !!s.kb_missing;
  const statusText = syncingThis
    ? `同步中${cur.total ? ` ${cur.synced}/${cur.total}` : ""}`
    : missing
      ? "未同步"
      : ({
          ok: "正常",
          error: "异常",
          partial: "部分失败",
          cancelled: "已取消",
          pending: "待同步",
        }[s.last_status] || s.last_status || "未同步");
  const statusCls = syncingThis ? "soon" : missing ? "err" : s.last_status === "ok" ? "ok" : s.last_status === "error" ? "err" : "warn";
  const statusTitle = missing ? "目标知识库已删除，同步时将自动重建" : (s.last_error || "");
  const name = s.display_name || s.kb_name || s.kb_id || "未命名";
  const meta = [
    s.url_count != null ? `${s.url_count} 个 URL` : `已同步 ${s.synced_count}`,
    s.last_sync_at ? `上次 ${s.last_sync_at.slice(5, 19)}` : "",
  ].filter(Boolean);
  const syncBtn = off
    ? `<button class="btn small" disabled>${ICONS.sync}平台已禁用</button>`
    : syncingThis
      ? `<button class="btn small danger" data-cancel="${esc(s.kb_id)}">${ICONS.bolt}取消 ${cur.total ? `${cur.synced}/${cur.total}` : ""}</button>`
      : otherSyncing
        ? `<button class="btn small" disabled>${ICONS.sync}同步中…</button>`
        : `<button class="btn small" data-kb="${esc(s.kb_id)}" data-platform="${esc(s.platform)}">${ICONS.sync}同步</button>`;
  return `
    <div class="sub-card ${off ? "off" : ""}">
      <div class="sub-main">
        <div class="sub-line1">
          <span class="sub-name" title="${esc(name)}">${esc(name)}</span>
        </div>
        <div class="sub-line2">
          <span class="badge ${statusCls}" title="${esc(statusTitle)}">${statusText}</span>
          <span class="meta">${meta.map((x) => `<span class="dim">${x}</span>`).join('<span class="sep">·</span>')}</span>
        </div>
      </div>
      <div class="sub-ops">
        <label class="switch sub-toggle" title="定时同步开关">
          <input type="checkbox" data-tgl="${esc(s.kb_id)}" data-platform="${esc(s.platform)}" ${s.enabled ? "checked" : ""} ${off ? "disabled" : ""} />
          <span class="track"></span>
          <span>定时同步</span>
        </label>
        ${syncBtn}
        <button class="btn small danger sub-dellocal" data-del="${esc(s.kb_id)}" data-dname="${esc(name)}" data-platform="${esc(s.platform)}" title="删除已同步到 AstrBot 的本地知识库（订阅保留）">${ICONS.trash}删除本地</button>
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
  // GitHub 分组始终渲染（提供仓库 URL 添加入口，即使暂无已添加仓库）
  if (!groups.has("github")) groups.set("github", []);
  groups.forEach((subs, g) => {
    const label = platformConfig?.platforms?.[g]?.name || g;
    const off = platformConfig?.platforms?.[g]?.enabled === false;
    const subListHtml = subs.length
      ? subs.map((s) => subCard(s)).join("")
      : `<div class="empty-state small">暂无已添加仓库</div>`;
    html += `
      <div class="sub-group">
        <div class="sub-group-title">${label}<span class="dim"> · ${subs.length}</span>${off ? '<span class="badge warn">已禁用</span>' : ""}</div>
        <div class="sub-list">${subListHtml}</div>
      </div>`;
  });
  if (!html) {
    html = `<div class="empty-state">暂无同步源 · 先到「平台配置」填写凭据</div>`;
  }
  root.innerHTML = html;
  root.querySelectorAll("[data-kb]").forEach((b) => {
    b.onclick = () => triggerSync(b.dataset.kb, b.dataset.platform || "ima");
  });
  root.querySelectorAll("[data-cancel]").forEach((b) => {
    b.onclick = async () => {
      try {
        const r = await bridge.apiPost("sync/cancel", { kb_id: b.dataset.cancel });
        toast(r.cancelled ? "正在取消同步…（已同步部分保留）" : r.message || "同步已结束", r.cancelled ? "warn" : "ok");
        await refresh();
      } catch (e) {
        toast(e.message, "error");
      }
    };
  });
  root.querySelectorAll("[data-del]").forEach((b) => {
    b.onclick = async () => {
      const ok = await confirmDialog({
        title: "删除本地知识库",
        message: `确认删除「${b.dataset.dname || b.dataset.del}」已同步到 AstrBot 的本地知识库数据？订阅将保留，可随时重新同步。`,
        okText: "删除",
      });
      if (!ok) return;
      try {
        const r = await bridge.apiPost("subs/delete-local", { kb_id: b.dataset.del, platform: b.dataset.platform || "ima" });
        toast(`已删除本地知识库：${r.name}`, "success");
        await refresh();
      } catch (e) {
        toast(e.message, "error");
      }
    };
  });
  root.querySelectorAll("[data-tgl]").forEach((t) => {
    t.onchange = async () => {
      try {
        const r = await bridge.apiPost("subs/toggle", {
          kb_id: t.dataset.tgl,
          platform: t.dataset.platform || "ima",
          enabled: t.checked,
        });
        toast(`${r.name} 定时同步已${r.enabled ? "开启" : "关闭"}`, "success");
        await refresh();
      } catch (e) {
        toast(e.message, "error");
        t.checked = !t.checked; // 回滚
      }
    };
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
      const view = $("view-subs");
      if (view._subs) renderSubs(); // 同步中实时刷新按钮/进度
      // 手动单源同步时 is_syncing 为 False，用 current 判断同步是否结束
      if (!s.is_syncing && !s.current) {
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

// ---------- 定时同步 ----------

async function loadSchedule() {
  return bridge.apiGet("schedule");
}

async function loadScheduleData(render = true) {
  const root = $("view-sched");
  root._loading = true;
  if (render) renderSchedule();
  try {
    root._data = await loadSchedule();
  } catch (e) {
    toast(e.message, "error");
  }
  root._loading = false;
  if (render) renderSchedule();
}

function schedLogHtml(l) {
  const level = l.level === "error" ? "err" : l.level === "warn" ? "warn" : "ok";
  const tag = { ok: "OK", warn: "WARN", err: "FAIL" }[level];
  const t = l.t && l.t.length >= 19 ? l.t.slice(5, 19) : l.t;
  return `<div class="console-line ${level}">
    <span class="ct">${esc(t)}</span>
    <span class="cb">[${tag}]</span>
    <span class="cm">${esc(l.msg)}</span>
  </div>`;
}

function renderSchedule() {
  const root = $("view-sched");
  if (root._loading) {
    root.innerHTML = `<div class="loading"><span class="spinner lg"></span>加载中…</div>`;
    return;
  }
  const d = root._data;
  if (!d) {
    root.innerHTML = `<div class="empty-state">加载定时同步配置失败 · 点击右上角刷新重试</div>`;
    return;
  }
  const logs = d.logs || [];
  const nOk = logs.filter((l) => l.level !== "error" && l.level !== "warn").length;
  const nWarn = logs.filter((l) => l.level === "warn").length;
  const nErr = logs.filter((l) => l.level === "error").length;
  root.innerHTML = `
    <div class="sched-bar card">
      <label class="switch-label" title="启用后按间隔自动同步已开启「定时」的同步源">
        <input type="checkbox" id="sched-enabled" ${d.enabled ? "checked" : ""} />
        <span>${d.enabled ? "定时同步已启用" : "定时同步已关闭"}</span>
      </label>
      <div class="sched-interval">
        <span class="dim">间隔</span>
        <div class="iv-group"><input class="input num" id="sched-d" type="number" min="0" value="${d.days}" /><span>天</span></div>
        <div class="iv-group"><input class="input num" id="sched-h" type="number" min="0" value="${d.hours}" /><span>时</span></div>
        <div class="iv-group"><input class="input num" id="sched-m" type="number" min="0" value="${d.minutes}" /><span>分</span></div>
        <div class="iv-group"><input class="input num" id="sched-s" type="number" min="0" value="${d.seconds}" /><span>秒</span></div>
      </div>
      <button class="btn primary" id="sched-save">保存</button>
    </div>
    <div class="sched-logs card">
      <div class="sched-log-head">
        <span class="sched-log-title">自动同步日志</span>
        <span class="sched-log-stats">
          <span class="dim">共 ${logs.length}</span>
          <span class="st ok">${nOk} 成功</span>
          <span class="st warn">${nWarn} 警告</span>
          <span class="st err">${nErr} 失败</span>
        </span>
        <button class="btn small" id="sched-clear">${ICONS.trash}清除日志</button>
      </div>
      <div class="sched-console">
        ${logs.length ? logs.map(schedLogHtml).join("") : `<div class="empty-state small">暂无日志</div>`}
      </div>
    </div>`;
  $("sched-clear").onclick = async () => {
    try {
      await bridge.apiPost("schedule/clear");
      toast("日志已清空", "success");
      await loadScheduleData();
    } catch (e) {
      toast(e.message, "error");
    }
  };
  $("sched-save").onclick = async () => {
    const enabled = $("sched-enabled").checked;
    const days = parseInt($("sched-d").value || "0", 10) || 0;
    const hours = parseInt($("sched-h").value || "0", 10) || 0;
    const minutes = parseInt($("sched-m").value || "0", 10) || 0;
    const seconds = parseInt($("sched-s").value || "0", 10) || 0;
    const total = days * 86400 + hours * 3600 + minutes * 60 + seconds;
    if (enabled && total < 1) {
      toast("启用定时同步时，间隔时间至少 1 秒", "warn");
      return;
    }
    try {
      const r = await bridge.apiPost("schedule", { enabled, days, hours, minutes, seconds });
      toast(r.enabled ? `定时同步已启用（每 ${fmtInterval(r.interval)}）` : "定时同步已关闭", "success");
      await loadScheduleData();
    } catch (e) {
      toast(e.message, "error");
    }
  };
}

function fmtInterval(sec) {
  const d = Math.floor(sec / 86400); sec %= 86400;
  const h = Math.floor(sec / 3600); sec %= 3600;
  const m = Math.floor(sec / 60); const s = sec % 60;
  const parts = [];
  if (d) parts.push(`${d}天`);
  if (h) parts.push(`${h}小时`);
  if (m) parts.push(`${m}分`);
  if (s) parts.push(`${s}秒`);
  return parts.join("") || "0";
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
  if (activeView === "sched") {
    if (!$("view-sched")._data) loadScheduleData();
    else renderSchedule();
  }
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
      if (activeView === "sched") await loadScheduleData();
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
