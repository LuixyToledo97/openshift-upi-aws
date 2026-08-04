/* ocplab web UI.
 *
 * Same rule as the server: nothing here knows anything about OpenShift or AWS.
 * It picks a command id, renders what came back, and gets out of the way.
 */

const TOKEN = window.OCPLAB_TOKEN;
const $ = (id) => document.getElementById(id);

/* Groups, in the order they escalate. The blurb is the promise each one makes
   about how much damage it can do, which is the thing worth knowing first. */
const GROUPS = [
  ["inspect", "Inspect", "Read-only. Safe at any time."],
  ["prepare", "Prepare", "Writes files, or creates the one-time AWS prerequisites."],
  ["operate", "Operate", "Changes the running cluster."],
  ["danger", "Teardown", "Destructive, and not reversible."],
];

/* Live state is fetched on demand rather than polled: each of these costs an
   AWS round trip, and two of them cost money to answer. */
const LIVE = ["verify", "cost", "power-status", "console"];

let state = { commands: [], excluded: {}, recent: [] };
let stream = null;
let attached = null;

/* ── transport ─────────────────────────────────────────────── */

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
  toast._t = setTimeout(() => { el.hidden = true; }, kind === "bad" ? 9000 : 3800);
}

const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

/* ── overview ──────────────────────────────────────────────── */

function card(label, value, { note, pill, mono } = {}) {
  const box = el("div", "card");
  box.append(el("div", "card-label", label));
  box.append(el("div", "card-value" + (mono ? " mono" : ""), value));
  if (pill) box.append(el("span", `pill ${pill[0]}`, pill[1]));
  if (note) box.append(el("div", "card-note", note));
  return box;
}

function renderOverview(payload) {
  const banner = $("configBanner");
  const host = $("statusCards");
  host.textContent = "";

  if (!payload.ok) {
    banner.hidden = false;
    banner.textContent = payload.error || "cluster.yaml could not be read.";
    $("clusterName").textContent = "No valid cluster.yaml";
    $("clusterSub").textContent = "Fix it under Configuration";
    return;
  }
  banner.hidden = true;

  const s = payload.status;
  $("clusterName").textContent = `${s.cluster.name}.${s.cluster.base_domain}`;
  $("clusterSub").textContent = `${s.cluster.region} · ${s.cluster.control_plane} control plane + ${s.cluster.compute} compute`;

  host.append(card(
    "OpenShift",
    s.openshift.pinned || "not pinned",
    s.openshift.pinned
      ? { pill: s.openshift.downloaded ? ["ok", "downloaded"] : ["warn", "not downloaded"],
          note: s.openshift.downloaded ? null : "Run Versions to fetch it." }
      : { pill: ["info", "PATH"], note: "Using whatever openshift-install/oc your PATH finds." },
  ));

  host.append(card(
    "Terraform state",
    s.terraform.exists ? `${s.terraform.resources} resources` : "no state file",
    s.terraform.exists && s.terraform.resources > 0
      ? { pill: ["ok", "deployed"] }
      : { pill: ["info", "nothing deployed"], note: "Nothing is billing from Terraform." },
  ));

  host.append(card(
    "install-dir",
    s.install_dir.exists ? s.install_dir.infra_id : "not generated",
    s.install_dir.exists
      ? { mono: true,
          pill: s.install_dir.stale ? ["bad", "expired"] : ["ok", `${s.install_dir.age_hours}h old`],
          note: s.install_dir.stale
            ? "Older than 24h — its certificates have expired. Regenerate before applying."
            : "The infraID every AWS resource is tagged with." }
      : { note: "Run Ignition to generate it." },
  ));

  host.append(card(
    "oc points at",
    s.kubeconfig.points_here ? "this cluster" : (s.kubeconfig.current ? "another cluster" : "~/.kube/config"),
    s.kubeconfig.points_here
      ? { pill: ["ok", "correct"] }
      : { pill: ["warn", "not this cluster"],
          note: 'Activate the venv, or run: eval "$(ocplab env)"' },
  ));

  if (s.openshift.cache.length) {
    host.append(card(
      "Binary cache",
      s.openshift.cache_total_human,
      { note: `${s.openshift.cache.map((v) => v.version).join(", ")} in ${s.openshift.cache_dir}` },
    ));
  }
}

function renderLiveActions() {
  const host = $("liveActions");
  host.textContent = "";
  for (const id of LIVE) {
    const command = state.commands.find((c) => c.id === id);
    if (!command) continue;
    const btn = el("button", "btn btn-sm", command.label);
    btn.title = command.desc;
    btn.addEventListener("click", () => runLive(command));
    host.append(btn);
  }
}

async function runLive(command) {
  const out = $("liveOut");
  out.textContent = `Running ${command.label}…`;
  try {
    const job = await api("/api/run", { method: "POST", body: JSON.stringify({ command: command.id }) });
    out.textContent = "";
    // Same stream as everything else, rendered inline instead of in the drawer:
    // these are short and the answer is the point, not the progress.
    const src = new EventSource(`/api/jobs/${encodeURIComponent(job.id)}/stream?token=${encodeURIComponent(TOKEN)}`);
    src.onmessage = (ev) => {
      const data = JSON.parse(ev.data);
      if (data.line !== undefined && !data.line.startsWith("$ ")) {
        out.append(document.createTextNode(data.line + "\n"));
        out.scrollTop = out.scrollHeight;
      }
      if (data.done) { src.close(); refresh(); }
    };
    src.onerror = () => src.close();
  } catch (err) {
    out.textContent = err.message;
  }
}

function renderRecent() {
  const host = $("recentRuns");
  host.textContent = "";
  const runs = (state.recent || []).slice(0, 6);
  if (!runs.length) { host.append(el("span", "empty", "No runs yet.")); return; }
  for (const job of runs) {
    const line = el("div", "runline");
    const left = el("div");
    left.append(el("b", null, job.label + (job.dry_run ? " (dry run)" : "")));
    left.append(el("div", "when", `${new Date(job.started * 1000).toLocaleTimeString()} · ${resultText(job)}`));
    const btn = el("button", "btn btn-quiet btn-sm", "Output");
    btn.addEventListener("click", () => openDrawer(job));
    line.append(left, btn);
    host.append(line);
  }
}

const resultText = (job) =>
  job.running ? "running" : job.exit_code === 0 ? "ok" : `exit ${job.exit_code}`;

/* ── actions ───────────────────────────────────────────────── */

function renderActions() {
  const host = $("actionGroups");
  host.textContent = "";
  for (const [id, title, blurb] of GROUPS) {
    const commands = state.commands.filter((c) => c.group === id);
    if (!commands.length) continue;
    const section = el("div", "agroup");
    const head = el("div", "agroup-head");
    head.append(el("h3", null, title), el("span", null, blurb));
    const grid = el("div", "agrid");
    for (const command of commands) {
      const btn = el("button", "action" + (id === "danger" ? " danger" : ""));
      btn.append(el("b", null, command.label), el("span", null, command.desc));
      btn.addEventListener("click", () => run(command));
      grid.append(btn);
    }
    section.append(head, grid);
    host.append(section);
  }

  const notes = Object.entries(state.excluded)
    .map(([name, why]) => `ocplab ${name} — ${why}`).join("\n");
  $("excluded").textContent = notes ? `Not available here:\n${notes}` : "";
}

async function run(command) {
  const dryRun = $("dryRun").checked;
  if (command.confirm && !dryRun && !(await confirmDialog(command))) return;
  try {
    const job = await api("/api/run", {
      method: "POST",
      body: JSON.stringify({ command: command.id, dry_run: dryRun }),
    });
    openDrawer(job, { fresh: true });
  } catch (err) {
    toast(err.message, "bad");
  }
}

function confirmDialog(command) {
  const dlg = $("confirmDialog");
  $("confirmTitle").textContent = command.label;
  $("confirmText").textContent = command.confirm;
  $("confirmOk").className = command.group === "danger" ? "btn btn-danger" : "btn btn-primary";
  dlg.showModal();
  return new Promise((resolve) =>
    dlg.addEventListener("close", () => resolve(dlg.returnValue === "ok"), { once: true }));
}

/* ── output drawer ─────────────────────────────────────────── */

function classify(line) {
  if (line.startsWith("$ ")) return "cmdline";
  if (line.startsWith("---")) return "end";
  if (/\b(FAILED|ERROR|error:|failed|fatal|Traceback)\b/.test(line)) return "bad";
  if (/\b(warning|WARNING|NOT\b|Cancelled)\b/.test(line)) return "warn";
  if (/\b(ok:|healthy|Ready|complete|succeeded|valid)\b/.test(line)) return "good";
  return null;
}

function appendLine(text) {
  const box = $("console");
  const node = el("span", classify(text) || null);
  // textContent, never innerHTML: command output containing angle brackets is
  // routine rather than exceptional.
  node.textContent = text + "\n";
  box.append(node);
  if ($("follow").checked) box.scrollTop = box.scrollHeight;
}

function openDrawer(job, { fresh } = {}) {
  $("drawer").hidden = false;
  if (stream) { stream.close(); stream = null; }
  if (fresh || attached !== job.id) $("console").textContent = "";
  attached = job.id;

  $("drawerTitle").textContent = job.label + (job.dry_run ? " (dry run)" : "");
  $("drawerSub").textContent = job.running ? "running…" : resultText(job);
  $("cancelBtn").hidden = !job.running;

  stream = new EventSource(`/api/jobs/${encodeURIComponent(job.id)}/stream?token=${encodeURIComponent(TOKEN)}`);
  stream.onmessage = (ev) => {
    const data = JSON.parse(ev.data);
    if (data.line !== undefined) return appendLine(data.line);
    if (data.done) {
      appendLine(data.exit_code === 0
        ? "--- finished successfully ---"
        : `--- finished with exit code ${data.exit_code} ---`);
      $("drawerSub").textContent = data.exit_code === 0 ? "finished" : `exit ${data.exit_code}`;
      $("cancelBtn").hidden = true;
      stream.close(); stream = null;
      refresh();
    }
  };
  stream.onerror = () => { if (stream) { stream.close(); stream = null; } };
}

/* ── configuration ─────────────────────────────────────────── */

async function loadConfig() {
  const cfg = await api("/api/config");
  $("configEditor").value = cfg.content;
  $("configState").textContent = cfg.exists
    ? "Loaded from the repository root."
    : "No cluster.yaml yet — paste one, or start from a template.";
  const select = $("templateSelect");
  while (select.options.length > 1) select.remove(1);
  for (const name of cfg.templates) {
    const opt = el("option", null, `examples/${name}`);
    opt.value = name;
    select.append(opt);
  }
}

async function validateConfig() {
  const box = $("validationBox");
  box.className = "validation";
  box.textContent = "Validating…";
  try {
    const res = await api("/api/validate", {
      method: "POST", body: JSON.stringify({ content: $("configEditor").value }),
    });
    box.textContent = "";
    box.className = "validation " + (res.ok ? "ok" : "bad");
    const lines = res.output.split("\n").filter((l) => l.trim());
    box.append(el("h4", null, res.ok ? "Valid" : lines[0] || "Invalid"));
    const list = el("ul");
    for (const line of lines.slice(1)) list.append(el("li", null, line.replace(/^\s*-\s*/, "")));
    if (list.childElementCount) box.append(list);
  } catch (err) {
    box.className = "validation bad";
    box.textContent = err.message;
  }
}

async function saveConfig() {
  try {
    await api("/api/config", { method: "POST", body: JSON.stringify({ content: $("configEditor").value }) });
    toast("Saved. The previous contents are in cluster.yaml.bak.", "good");
    refresh();
  } catch (err) {
    toast(err.message, "bad");
  }
}

/* ── shell ─────────────────────────────────────────────────── */

function switchView(name) {
  document.querySelectorAll(".viewbtn").forEach((b) => b.classList.toggle("is-active", b.dataset.view === name));
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("is-active", v.id === `view-${name}`));
  if (name === "runs") renderRuns();
}

function renderRuns() {
  const body = $("runsTable").querySelector("tbody");
  body.textContent = "";
  const runs = state.recent || [];
  $("runsEmpty").hidden = runs.length > 0;
  $("runsTable").hidden = runs.length === 0;
  for (const job of runs) {
    const tr = el("tr");
    const duration = job.finished
      ? `${Math.round(job.finished - job.started)}s`
      : `${Math.round(Date.now() / 1000 - job.started)}s…`;
    tr.append(
      el("td", null, job.label + (job.dry_run ? " (dry run)" : "")),
      el("td", null, new Date(job.started * 1000).toLocaleTimeString()),
      el("td", null, duration),
      el("td", null, resultText(job)),
    );
    const td = el("td");
    const btn = el("button", "btn btn-quiet btn-sm", "Output");
    btn.addEventListener("click", () => openDrawer(job));
    td.append(btn);
    tr.append(td);
    body.append(tr);
  }
}

function setLive(job) {
  const chip = $("liveChip");
  const running = job && job.running;
  chip.hidden = !running;
  if (running) $("liveLabel").textContent = job.label + (job.dry_run ? " (dry run)" : "");
  document.title = running ? `▶ ${job.label} — ocplab` : "ocplab";
  document.querySelectorAll(".action").forEach((b) => { b.disabled = !!running; });
  chip.dataset.job = running ? job.id : "";
}

async function refresh() {
  try {
    const next = await api("/api/state");
    state = { ...state, ...next };
    renderActions();
    renderLiveActions();
    renderRecent();
    setLive(next.current);
    if (document.querySelector('.viewbtn[data-view="runs"]').classList.contains("is-active")) renderRuns();
    // Reattaching after a reload is why the lines live server-side: a deploy
    // started twenty minutes ago picks up exactly where it was.
    if (next.current && next.current.running && attached !== next.current.id) {
      openDrawer(next.current, { fresh: true });
    }
  } catch (err) {
    toast(`Lost contact with the server: ${err.message}`, "bad");
  }
  try {
    renderOverview(await api("/api/overview"));
  } catch (err) {
    renderOverview({ ok: false, error: err.message });
  }
}

function init() {
  document.querySelectorAll(".viewbtn").forEach((b) =>
    b.addEventListener("click", () => switchView(b.dataset.view)));

  $("drawerClose").addEventListener("click", () => {
    $("drawer").hidden = true;
    if (stream) { stream.close(); stream = null; }
    attached = null;
  });
  $("liveOpen").addEventListener("click", () => {
    const job = state.recent.find((j) => j.id === $("liveChip").dataset.job);
    if (job) openDrawer(job);
  });
  $("cancelBtn").addEventListener("click", async () => {
    if (!attached) return;
    try { await api("/api/cancel", { method: "POST", body: JSON.stringify({ id: attached }) }); }
    catch (err) { toast(err.message, "bad"); }
  });

  $("validateBtn").addEventListener("click", validateConfig);
  $("saveBtn").addEventListener("click", saveConfig);
  $("templateSelect").addEventListener("change", async (ev) => {
    const name = ev.target.value;
    ev.target.value = "";
    if (!name) return;
    if ($("configEditor").value.trim() &&
        !confirm(`Replace the editor's contents with examples/${name}?`)) return;
    const res = await api(`/api/template?name=${encodeURIComponent(name)}`);
    $("configEditor").value = res.content;
    $("configState").textContent = `Loaded from examples/${name} — not saved yet.`;
  });

  refresh();
  loadConfig().catch((err) => toast(err.message, "bad"));
  setInterval(() => { if (!stream) refresh(); }, 6000);
}

document.addEventListener("DOMContentLoaded", init);
