const bridge = window.AstrBotPluginPage;
const $ = (id) => document.getElementById(id);

let pollTimer = null;

function setMsg(el, text, ok = true) {
  el.textContent = text || "";
  el.className = "msg" + (text ? (ok ? " ok" : " err") : "");
}

async function loadStats() {
  const s = await bridge.apiGet("stats");
  $("stat-ima").textContent = s.ima_configured ? "已配置" : "未配置";
  $("stat-ima").className = "card-value" + (s.ima_configured ? " good" : " warn");
  $("stat-subs").textContent = s.sub_count;
  $("stat-synced").textContent = s.total_synced;
  $("stat-cron").textContent = s.cron_enabled ? `每 ${s.cron_interval} 分钟` : "关闭";
  $("stat-cron").className = "card-value" + (s.cron_enabled ? " good" : "");
  $("cron-on").checked = s.cron_enabled;
  $("cron-minutes").value = s.cron_interval || "";
  $("cron-label").textContent = s.cron_enabled ? `每 ${s.cron_interval} 分钟自动同步` : "未启用";
  $("btn-sync-all").disabled = s.is_syncing;
  $("btn-sync-all").textContent = s.is_syncing ? "同步中…" : "同步全部";
  return s;
}

async function loadSubs() {
  const subs = await bridge.apiGet("subs");
  const body = $("subs-body");
  if (!subs.length) {
    body.innerHTML = '<tr><td colspan="6" class="empty">暂无订阅，从上方选择 IMA 知识库添加</td></tr>';
    return;
  }
  body.textContent = "";
  subs.forEach((s, i) => {
    const tr = document.createElement("tr");

    const tdName = document.createElement("td");
    const name = document.createElement("div");
    name.className = "kb-name";
    name.textContent = s.kb_name || s.kb_id;
    const id = document.createElement("div");
    id.className = "kb-id";
    id.textContent = s.kb_id;
    tdName.append(name, id);

    const tdTarget = document.createElement("td");
    tdTarget.textContent = s.target_kb || "(自动创建)";

    const tdStatus = document.createElement("td");
    const badge = document.createElement("span");
    badge.className = "badge " + (s.last_status === "ok" ? "ok" : s.last_status === "error" ? "err" : "warn");
    badge.textContent =
      s.last_status === "ok" ? "正常" : s.last_status === "error" ? "异常" : s.last_status === "partial" ? "部分失败" : "待同步";
    if (s.last_error) badge.title = s.last_error;
    tdStatus.append(badge);

    const tdCount = document.createElement("td");
    tdCount.textContent = s.synced_count;

    const tdTime = document.createElement("td");
    tdTime.className = "dim";
    tdTime.textContent = s.last_sync_at || "—";

    const tdOps = document.createElement("td");
    tdOps.className = "ops";
    const btnSync = document.createElement("button");
    btnSync.className = "btn small";
    btnSync.textContent = "同步";
    btnSync.onclick = () => triggerSync(i);
    const btnDel = document.createElement("button");
    btnDel.className = "btn small danger";
    btnDel.textContent = "删除";
    btnDel.onclick = () => removeSub(i);
    tdOps.append(btnSync, btnDel);

    tr.append(tdName, tdTarget, tdStatus, tdCount, tdTime, tdOps);
    body.append(tr);
  });
}

async function loadKbs() {
  try {
    const kbs = await bridge.apiGet("kbs");
    const sel = $("kb-select");
    sel.textContent = "";
    if (!kbs.length) {
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = "IMA 账号下暂无知识库";
      sel.append(opt);
      return;
    }
    kbs.forEach((k) => {
      const opt = document.createElement("option");
      opt.value = k.id || "";
      opt.textContent = k.name || k.id;
      sel.append(opt);
    });
  } catch (e) {
    setMsg($("add-msg"), "加载 IMA 知识库失败：" + e.message, false);
  }
}

async function triggerSync(index = null) {
  try {
    setMsg($("sync-msg"), "正在启动同步…");
    await bridge.apiPost("sync", index === null ? {} : { index });
    setMsg($("sync-msg"), "同步已启动，正在后台执行…");
    startPoll();
  } catch (e) {
    setMsg($("sync-msg"), e.message, false);
  }
}

async function removeSub(index) {
  if (!confirm("确认删除该订阅？已同步的文档不会被删除。")) return;
  try {
    const r = await bridge.apiPost(`subs/${index}/remove`, {});
    setMsg($("sync-msg"), `已删除订阅：${r.name}`);
    await refresh();
  } catch (e) {
    setMsg($("sync-msg"), e.message, false);
  }
}

function startPoll() {
  if (pollTimer) return;
  pollTimer = setInterval(async () => {
    try {
      const s = await loadStats();
      await loadSubs();
      if (!s.is_syncing) {
        clearInterval(pollTimer);
        pollTimer = null;
        setMsg($("sync-msg"), "同步完成");
      }
    } catch {
      /* 轮询失败静默 */
    }
  }, 2000);
}

async function refresh() {
  await Promise.all([loadStats(), loadSubs()]);
}

async function main() {
  const context = await bridge.ready();
  const applyTheme = (isDark) => {
    document.documentElement.dataset.dark = isDark ? "true" : "false";
  };
  applyTheme(context.isDark);
  bridge.onContext((ctx) => applyTheme(ctx.isDark));
  $("btn-refresh").onclick = refresh;
  $("btn-add").onclick = async () => {
    const kbId = $("kb-select").value;
    if (!kbId) {
      setMsg($("add-msg"), "请先选择 IMA 知识库", false);
      return;
    }
    try {
      const r = await bridge.apiPost("subs/add", { kb_id: kbId, target_kb: $("kb-target").value });
      setMsg($("add-msg"), `已添加订阅：${r.sub.kb_name}`);
      $("kb-target").value = "";
      await refresh();
    } catch (e) {
      setMsg($("add-msg"), e.message, false);
    }
  };
  $("btn-sync-all").onclick = () => triggerSync(null);
  $("btn-cron").onclick = async () => {
    const on = $("cron-on").checked;
    const minutes = parseInt($("cron-minutes").value, 10);
    try {
      const r = await bridge.apiPost("cron", { on, minutes: on ? minutes || undefined : undefined });
      setMsg($("cron-msg"), r.message);
      await loadStats();
    } catch (e) {
      setMsg($("cron-msg"), e.message, false);
    }
  };
  await loadKbs();
  await refresh();
}

main();
