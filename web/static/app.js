/* ocplab web UI.
 *
 * Same rule as the server: no logic about OpenShift or AWS lives here. This
 * file picks a command id, shows what came back, and gets out of the way.
 */

const TOKEN = window.OCPLAB_TOKEN;
const $ = (id) => document.getElementById(id);

const GROUPS = [
  ["inspect", "Inspect", "Read-only. Safe to run at any time."],
  ["prepare", "Prepare", "Generates files or one-time AWS prerequisites."],
  ["operate", "Operate", "Changes the running cluster."],
  ["danger", "Teardown", "Destructive and not reversible."],
];

let state = { commands: [], excluded: {}, current: null };
let stream = null;
let attachedJob = null;

/* ---------- transport ---------- */

async function api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: { "X-Ocplab-Token": TOKEN, "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || `${res.status} ${res.statusText}`);
  return body;
}

function toast(message, kind) {
  const el = $("toast");
  el.textContent = message;
  el.className = "toast" + (kind ? ` is-${kind}` : "");
  el.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.hidden = true; }, kind === "bad" ? 8000 : 3500);
}

/* ---------- console ---------- */

function classify(line) {
  if (line.startsWith("$ ")) return "cmdline";
  if (/^---/.test(line)) return "end";
  if (/\b(FAILED|ERROR|error:|failed|fatal)\b/.test(line)) return "bad";
  if (/\b(warning|WARNING|NOT|not available)\b/.test(line)) return "warn";
  if (/\b(ok|OK|healthy|Ready|complete|done|Done)\b/.test(line)) return "good";
  return null;
}

function appendLine(text) {
  const el = $("console");
  const hint = el.querySelector(".hint");
  if (hint) hint.remove();
  const node = document.createElement("span");
  const kind = classify(text);
  if (kind) node.className = kind;
  // textContent, never innerHTML: these lines are command output, and output
  // containing angle brackets is normal rather than exceptional.
  node.textContent = text + "\n";
  el.appendChild(node);
  if ($("follow").checked) el.scrollTop = el.scrollHeight;
}

function resetConsole(message) {
  const el = $("console");
  el.textContent = "";
  if (message) {
    const hint = document.createElement("span");
    hint.className = "hint";
    hint.textContent = message;
    el.appendChild(hint);
  }
}

/* ---------- jobs ---------- */

function setRunning(job) {
  state.current = job;
  const dot = $("statusDot");
  const running = job && job.running;

  $("jobLabel").textContent = job
    ? `${job.label}${job.dry_run ? " (dry run)" : ""} — ${running ? "running…" : exitText(job)}`
    : "No operation running";
  $("cancelBtn").hidden = !running;

  dot.className = "dot" + (running ? " is-running" : job ? (job.exit_code === 0 ? " is-ok" : " is-fail") : "");
  dot.title = running ? "running" : "idle";
  document.title = running ? `▶ ${job.label} — ocplab` : "ocplab";

  document.querySelectorAll(".cmd").forEach((b) => { b.disabled = !!running; });
}

function exitText(job) {
  if (job.exit_code === 0) return "finished";
  return `exit code ${job.exit_code}`;
}

function attach(job, { fresh } = {}) {
  if (stream) { stream.close(); stream = null; }
  if (fresh) resetConsole(null);
  attachedJob = job.id;
  setRunning(job);

  stream = new EventSource(`/api/jobs/${encodeURIComponent(job.id)}/stream?token=${encodeURIComponent(TOKEN)}`);
  stream.onmessage = (ev) => {
    const data = JSON.parse(ev.data);
    if (data.line !== undefined) { appendLine(data.line); return; }
    if (data.done) {
      appendLine(data.exit_code === 0
        ? "--- finished successfully ---"
        : `--- finished with exit code ${data.exit_code} ---`);
      stream.close();
      stream = null;
      refresh();
    }
  };
  stream.onerror = () => { if (stream) { stream.close(); stream = null; } };
}

async function run(command) {
  const dryRun = $("dryRun").checked;
  if (command.confirm && !dryRun) {
    const ok = await confirmDialog(command);
    if (!ok) return;
  }
  try {
    const job = await api("/api/run", {
      method: "POST",
      body: JSON.stringify({ command: command.id, dry_run: dryRun }),
    });
    switchTab("operations");
    attach(job, { fresh: true });
  } catch (err) {
    toast(err.message, "bad");
  }
}

function confirmDialog(command) {
  const dlg = $("confirmDialog");
  $("confirmTitle").textContent = command.label;
  $("confirmText").textContent = command.confirm;
  $("confirmDry").hidden = true;
  $("confirmOk").className = command.group === "danger" ? "btn btn-danger" : "btn btn-primary";
  dlg.showModal();
  return new Promise((resolve) => {
    dlg.addEventListener("close", () => resolve(dlg.returnValue === "ok"), { once: true });
  });
}

/* ---------- rendering ---------- */

function renderCommands() {
  const host = $("commandGroups");
  host.textContent = "";
  for (const [id, title, blurb] of GROUPS) {
    const commands = state.commands.filter((c) => c.group === id);
    if (!commands.length) continue;
    const section = document.createElement("div");
    section.className = "group";
    const h = document.createElement("h3");
    h.textContent = title;
    h.title = blurb;
    section.appendChild(h);
    for (const command of commands) {
      const btn = document.createElement("button");
      btn.className = "cmd" + (command.group === "danger" ? " is-danger" : "");
      const strong = document.createElement("strong");
      strong.textContent = command.label;
      const span = document.createElement("span");
      span.textContent = command.desc;
      btn.append(strong, span);
      btn.addEventListener("click", () => run(command));
      section.appendChild(btn);
    }
    host.appendChild(section);
  }

  const notes = Object.entries(state.excluded)
    .map(([name, why]) => `<b>${name}</b> — ${why}`)
    .join("<br>");
  $("excluded").innerHTML = notes ? `Not available here:<br>${notes}` : "";
}

function renderHistory() {
  const body = $("historyTable").querySelector("tbody");
  body.textContent = "";
  for (const job of state.recent || []) {
    const tr = document.createElement("tr");
    const duration = job.finished
      ? `${Math.round(job.finished - job.started)}s`
      : `${Math.round(Date.now() / 1000 - job.started)}s…`;
    const result = job.running
      ? '<span class="run">running</span>'
      : job.exit_code === 0
        ? '<span class="ok">ok</span>'
        : `<span class="fail">exit ${job.exit_code}</span>`;
    tr.innerHTML = `
      <td>${job.label}${job.dry_run ? " <em>(dry run)</em>" : ""}</td>
      <td>${new Date(job.started * 1000).toLocaleTimeString()}</td>
      <td>${duration}</td>
      <td>${result}</td>
      <td></td>`;
    const view = document.createElement("button");
    view.className = "btn btn-quiet";
    view.textContent = "View output";
    view.addEventListener("click", () => { switchTab("operations"); attach(job, { fresh: true }); });
    tr.lastElementChild.appendChild(view);
    body.appendChild(tr);
  }
}

/* ---------- configuration ---------- */

async function loadConfig() {
  const cfg = await api("/api/config");
  $("configEditor").value = cfg.content;
  $("configState").textContent = cfg.exists
    ? "Loaded from the repository root."
    : "No cluster.yaml yet — paste one or start from a template.";
  const select = $("templateSelect");
  while (select.options.length > 1) select.remove(1);
  for (const name of cfg.templates) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = `examples/${name}`;
    select.appendChild(opt);
  }
}

async function validateConfig() {
  try {
    const res = await api("/api/validate", {
      method: "POST",
      body: JSON.stringify({ content: $("configEditor").value }),
    });
    switchTab("operations");
    resetConsole(null);
    appendLine("$ ocplab validate  (on the editor's contents, not the saved file)");
    res.output.split("\n").forEach(appendLine);
    toast(res.ok ? "cluster.yaml is valid." : "cluster.yaml has errors — see the output.",
          res.ok ? "good" : "bad");
  } catch (err) {
    toast(err.message, "bad");
  }
}

async function saveConfig() {
  try {
    await api("/api/config", {
      method: "POST",
      body: JSON.stringify({ content: $("configEditor").value }),
    });
    toast("Saved. The previous contents are in cluster.yaml.bak.", "good");
    refresh();
  } catch (err) {
    toast(err.message, "bad");
  }
}

/* ---------- shell ---------- */

function switchTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("is-active", t.dataset.tab === name));
  document.querySelectorAll(".panel").forEach((p) => p.classList.toggle("is-active", p.id === `tab-${name}`));
  if (name === "history") renderHistory();
}

async function refresh() {
  try {
    const next = await api("/api/state");
    state = { ...state, ...next };
    $("repoPath").textContent = next.repo;
    renderCommands();
    setRunning(next.current);
    if (document.querySelector('.tab[data-tab="history"]').classList.contains("is-active")) renderHistory();
    // Reattaching after a reload is the whole point of keeping the lines
    // server-side: a deploy started twenty minutes ago picks up where it was.
    if (next.current && next.current.running && attachedJob !== next.current.id) {
      attach(next.current, { fresh: true });
    }
  } catch (err) {
    toast(`Lost contact with the server: ${err.message}`, "bad");
  }
}

function init() {
  document.querySelectorAll(".tab").forEach((t) =>
    t.addEventListener("click", () => switchTab(t.dataset.tab)));

  $("clearBtn").addEventListener("click", () => resetConsole("Cleared. The run's own log under logs/ is untouched."));
  $("cancelBtn").addEventListener("click", async () => {
    if (!state.current) return;
    try { await api("/api/cancel", { method: "POST", body: JSON.stringify({ id: state.current.id }) }); }
    catch (err) { toast(err.message, "bad"); }
  });

  $("validateBtn").addEventListener("click", validateConfig);
  $("saveBtn").addEventListener("click", saveConfig);
  $("templateSelect").addEventListener("change", async (ev) => {
    if (!ev.target.value) return;
    const name = ev.target.value;
    ev.target.value = "";
    if ($("configEditor").value.trim() &&
        !confirm(`Replace the editor's contents with examples/${name}?`)) return;
    const res = await api(`/api/template?name=${encodeURIComponent(name)}`);
    $("configEditor").value = res.content;
    $("configState").textContent = `Loaded from examples/${name} — not saved yet.`;
  });

  refresh();
  loadConfig().catch((err) => toast(err.message, "bad"));
  setInterval(() => { if (!stream) refresh(); }, 5000);
}

document.addEventListener("DOMContentLoaded", init);
