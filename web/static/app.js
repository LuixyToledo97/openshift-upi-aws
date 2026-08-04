/* ocplab web UI.
 *
 * Same rule as the server: nothing here knows anything about OpenShift or AWS.
 * It picks a command id, renders what came back, and gets out of the way.
 */

const TOKEN = window.OCPLAB_TOKEN;
const $ = (id) => document.getElementById(id);
const STORE = "ocplab.panel";

/* Groups in the order they escalate. The blurb is the promise each makes about
   how much damage it can do, which is what you want to know first. */
const GROUPS = [
  ["inspect", "Inspect", "Read-only. Safe at any time."],
  ["prepare", "Prepare", "Writes files, or creates the one-time AWS prerequisites."],
  ["operate", "Operate", "Changes the running cluster."],
  ["danger", "Teardown", "Destructive, and not reversible."],
];

/* Fetched on demand, never polled: each costs a round trip to AWS. */
const LIVE = ["verify", "cost", "power-status", "console"];

let state = { commands: [], excluded: {}, recent: [], about: {} };
let stream = null;
let attached = null;
let runFilter = "all";

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
  const box = $("toast");
  box.textContent = message;
  box.className = "toast" + (kind ? ` is-${kind}` : "");
  box.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { box.hidden = true; }, kind === "bad" ? 9000 : 3800);
}

const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

const isSafe = (job) => job.group === "inspect";
const resultText = (job) => job.running ? "running" : job.exit_code === 0 ? "ok" : `exit ${job.exit_code}`;

/* ── output panel: dockable, resizable, and never in the way ── */

const panel = {
  get dock() { return $("outPanel").dataset.dock; },

  load() {
    let saved = {};
    try { saved = JSON.parse(localStorage.getItem(STORE) || "{}"); } catch { /* first run */ }
    $("outPanel").dataset.dock = saved.dock === "right" ? "right" : "bottom";
    this.size(saved.size || 320);
  },

  save() {
    try {
      localStorage.setItem(STORE, JSON.stringify({
        dock: this.dock,
        size: parseInt(getComputedStyle(document.documentElement).getPropertyValue("--panel-size"), 10),
      }));
    } catch { /* private browsing; the panel just won't remember */ }
  },

  size(px) {
    const axis = this.dock === "right" ? window.innerWidth : window.innerHeight;
    const clamped = Math.max(160, Math.min(px, axis - 140));
    document.documentElement.style.setProperty("--panel-size", `${clamped}px`);
    this.reserve();
  },

  /* The page gives up real estate rather than being covered. Overlaying is
     what made Actions, Configuration and Runs impossible to scroll. */
  reserve() {
    const open = !$("outPanel").hidden;
    const size = open ? getComputedStyle(document.documentElement).getPropertyValue("--panel-size") : "0px";
    const side = this.dock === "right" && window.innerWidth > 900;
    document.body.style.setProperty("--pad-bottom", side ? "0px" : size);
    document.body.style.setProperty("--pad-right", side ? size : "0px");
  },

  toggleDock() {
    const next = this.dock === "bottom" ? "right" : "bottom";
    $("outPanel").dataset.dock = next;
    $("dockBtn").title = next === "bottom" ? "Dock to the right" : "Dock to the bottom";
    $("dockBtn").textContent = next === "bottom" ? "⇥" : "⤓";
    this.size(next === "right" ? 460 : 320);
    this.save();
  },

  open() { $("outPanel").hidden = false; this.reserve(); },

  close() {
    $("outPanel").hidden = true;
    this.reserve();
    if (stream) { stream.close(); stream = null; }
    attached = null;
  },

  bindResize() {
    const bar = $("resizer");
    const start = (ev) => {
      ev.preventDefault();
      document.body.classList.add("is-resizing");
      const move = (e) => {
        const point = e.touches ? e.touches[0] : e;
        this.size(this.dock === "right"
          ? window.innerWidth - point.clientX
          : window.innerHeight - point.clientY);
      };
      const stop = () => {
        document.body.classList.remove("is-resizing");
        document.removeEventListener("mousemove", move);
        document.removeEventListener("touchmove", move);
        document.removeEventListener("mouseup", stop);
        document.removeEventListener("touchend", stop);
        this.save();
      };
      document.addEventListener("mousemove", move);
      document.addEventListener("touchmove", move, { passive: false });
      document.addEventListener("mouseup", stop);
      document.addEventListener("touchend", stop);
    };
    bar.addEventListener("mousedown", start);
    bar.addEventListener("touchstart", start, { passive: false });
    window.addEventListener("resize", () => this.reserve());
  },
};

/* ── console ───────────────────────────────────────────────── */

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
  // textContent, never innerHTML: output with angle brackets in it is routine.
  node.textContent = text + "\n";
  box.append(node);
  if ($("follow").checked) box.scrollTop = box.scrollHeight;
}

function openOutput(job, { fresh } = {}) {
  panel.open();
  if (stream) { stream.close(); stream = null; }
  if (fresh || attached !== job.id) $("console").textContent = "";
  attached = job.id;

  $("panelTitle").textContent = job.label + (job.dry_run ? " (dry run)" : "");
  $("panelSub").textContent = job.running ? "running…" : resultText(job);
  $("cancelBtn").hidden = !job.running;

  stream = new EventSource(`/api/jobs/${encodeURIComponent(job.id)}/stream?token=${encodeURIComponent(TOKEN)}`);
  stream.onmessage = (ev) => {
    const data = JSON.parse(ev.data);
    if (data.line !== undefined) return appendLine(data.line);
    if (data.done) {
      appendLine(data.exit_code === 0
        ? "--- finished successfully ---"
        : `--- finished with exit code ${data.exit_code} ---`);
      $("panelSub").textContent = data.exit_code === 0 ? "finished" : `exit ${data.exit_code}`;
      $("cancelBtn").hidden = true;
      stream.close(); stream = null;
      refresh();
    }
  };
  stream.onerror = () => { if (stream) { stream.close(); stream = null; } };
}

/* ── overview ──────────────────────────────────────────────── */

function card(label, value, { note, pill, mono, extra } = {}) {
  const box = el("div", "card");
  box.append(el("div", "card-label", label));
  box.append(el("div", "card-value" + (mono ? " mono" : ""), value));
  if (pill) box.append(el("span", `pill ${pill[0]}`, pill[1]));
  if (extra) box.append(extra);
  if (note) box.append(el("div", "card-note", note));
  return box;
}

function versionsCard(o) {
  const list = el("div", "verlist");
  for (const v of o.cache) {
    const pinned = v.version === o.pinned;
    const row = el("div", "verrow" + (pinned ? " is-pinned" : ""));
    row.append(el("span", null, v.version), el("span", null, pinned ? "pinned" : bytes(v.size)));
    list.append(row);
  }
  const pinnedMissing = o.pinned && !o.downloaded;
  return card(
    "OpenShift",
    o.pinned || "not pinned",
    {
      pill: o.pinned
        ? (o.downloaded ? ["ok", "downloaded"] : ["warn", "not downloaded"])
        : ["info", "using your PATH"],
      extra: o.cache.length ? list : undefined,
      note: pinnedMissing
        ? "Run Versions to fetch it."
        : (o.cache.length ? `${o.cache_total_human} cached in ${o.cache_dir}` : null),
    },
  );
}

// Binary units, matching human_size() in the CLI. Decimal units here would put
// "1.7 GB" on the dashboard next to "1.6 GB" from `ocplab status` for the same
// bytes, and someone would reasonably conclude one of them is broken.
const bytes = (n) => n >= 1024 ** 3
  ? `${(n / 1024 ** 3).toFixed(1)} GB`
  : `${Math.round(n / 1024 ** 2)} MB`;

function renderOverview(payload) {
  const banner = $("configBanner");
  const host = $("statusCards");
  host.textContent = "";

  if (!payload.ok) {
    banner.hidden = false;
    banner.textContent = payload.error || "cluster.yaml could not be read.";
    $("clusterName").textContent = "No valid cluster.yaml";
    $("clusterSub").textContent = "Fix it under Configuration.";
    return;
  }
  banner.hidden = true;

  const s = payload.status;
  $("clusterName").textContent = `${s.cluster.name}.${s.cluster.base_domain}`;
  $("clusterSub").textContent =
    `${s.cluster.region} · ${s.cluster.control_plane} control plane + ${s.cluster.compute} compute`;

  host.append(versionsCard(s.openshift));

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

  // Only surfaced when it is wrong. Reporting "correct" every time is noise;
  // reporting it when your oc would hit another cluster is the whole point.
  if (!s.kubeconfig.points_here) {
    host.append(card(
      "oc is pointing elsewhere",
      s.kubeconfig.current ? "another kubeconfig" : "~/.kube/config",
      { pill: ["warn", "not this cluster"],
        note: 'Activate the venv, or run: eval "$(ocplab env)"' },
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
    // Same stream as everything else, rendered inline: these are short, and the
    // answer is the point rather than the progress.
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
  if (!runs.length) { host.append(el("span", "empty", "Nothing has run yet.")); return; }
  for (const job of runs) {
    const line = el("div", "runline");
    const left = el("div");
    left.append(el("b", null, job.label + (job.dry_run ? " (dry run)" : "")));
    left.append(el("div", "when", `${new Date(job.started * 1000).toLocaleTimeString()} · ${resultText(job)}`));
    const btn = el("button", "btn btn-quiet btn-sm", "Output");
    btn.addEventListener("click", () => openOutput(job));
    line.append(left, btn);
    host.append(line);
  }
}

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
  const notes = Object.entries(state.excluded).map(([n, w]) => `ocplab ${n} — ${w}`).join("\n");
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
    openOutput(job, { fresh: true });
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

/* ── runs ──────────────────────────────────────────────────── */

function renderRuns() {
  const body = $("runsTable").querySelector("tbody");
  body.textContent = "";
  const runs = (state.recent || []).filter((j) =>
    runFilter === "all" || (runFilter === "safe" ? isSafe(j) : !isSafe(j)));

  $("runsEmpty").hidden = runs.length > 0;
  $("runsTable").hidden = runs.length === 0;

  for (const job of runs) {
    const tr = el("tr");
    const duration = job.finished
      ? `${Math.round(job.finished - job.started)}s`
      : `${Math.round(Date.now() / 1000 - job.started)}s…`;
    const kind = el("td");
    kind.append(el("span", `tag ${isSafe(job) ? "safe" : "changed"}`,
      isSafe(job) ? "read-only" : (job.dry_run ? "preview" : "changed")));
    tr.append(
      el("td", null, job.label + (job.dry_run ? " (dry run)" : "")),
      kind,
      el("td", null, new Date(job.started * 1000).toLocaleTimeString()),
      el("td", null, duration),
      el("td", null, resultText(job)),
    );
    const last = el("td");
    const btn = el("button", "btn btn-quiet btn-sm", "Output");
    btn.addEventListener("click", () => openOutput(job));
    last.append(btn);
    tr.append(last);
    body.append(tr);
  }
}

/* ── about ─────────────────────────────────────────────────── */

function renderAbout() {
  const a = state.about || {};
  $("brandVersion").textContent = a.version ? `v${a.version}` : "";
  $("aboutVersion").textContent = a.version ? `v${a.version}` : "";
  $("aboutTagline").textContent = a.tagline || "";
  $("aboutFonts").textContent = a.fonts || "";

  const grid = $("aboutGrid");
  grid.textContent = "";
  for (const [term, value] of [
    ["Version", a.version || "unknown"],
    ["Licence", `${a.license} — free to use, modify and redistribute`],
    ["Author", a.author],
    ["GitHub", `@${a.github_user}`],
  ]) {
    if (!value) continue;
    grid.append(el("dt", null, term), el("dd", null, value));
  }

  const links = $("aboutLinks");
  links.textContent = "";
  for (const [label, href, primary] of [
    ["View the repository", a.repo_url, true],
    [`@${a.github_user} on GitHub`, a.github_url, false],
  ]) {
    if (!href) continue;
    const link = el("a", "btn" + (primary ? " btn-primary" : ""), label);
    link.href = href;
    link.target = "_blank";
    // noopener: the page being opened must not get a handle back to this one,
    // which is a local server holding AWS credentials.
    link.rel = "noopener noreferrer";
    links.append(link);
  }
}

/* ── shell ─────────────────────────────────────────────────── */

function switchView(name) {
  document.querySelectorAll(".viewbtn").forEach((b) => b.classList.toggle("is-active", b.dataset.view === name));
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("is-active", v.id === `view-${name}`));
  if (name === "runs") renderRuns();
}

function setLive(job) {
  const chip = $("runChip");
  const running = job && job.running;
  chip.hidden = !running;
  if (running) {
    $("runChipLabel").textContent = job.label + (job.dry_run ? " (dry run)" : "");
    chip.dataset.job = job.id;
  }
  document.title = running ? `▶ ${job.label} — ocplab` : "ocplab";
  document.querySelectorAll(".action").forEach((b) => { b.disabled = !!running; });
}

async function refresh() {
  try {
    const next = await api("/api/state");
    state = { ...state, ...next };
    renderActions();
    renderLiveActions();
    renderRecent();
    renderAbout();
    setLive(next.current);
    if (document.querySelector('.viewbtn[data-view="runs"]').classList.contains("is-active")) renderRuns();
    // Reattaching after a reload is why the lines live server-side: a deploy
    // started twenty minutes ago picks up exactly where it was.
    if (next.current && next.current.running && attached !== next.current.id) {
      openOutput(next.current, { fresh: true });
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

/* ── init ──────────────────────────────────────────────────── */

function init() {
  panel.load();
  panel.bindResize();

  document.querySelectorAll(".viewbtn").forEach((b) =>
    b.addEventListener("click", () => switchView(b.dataset.view)));
  document.querySelectorAll("#runFilter .seg").forEach((b) =>
    b.addEventListener("click", () => {
      runFilter = b.dataset.filter;
      document.querySelectorAll("#runFilter .seg").forEach((x) => x.classList.toggle("is-active", x === b));
      renderRuns();
    }));

  $("panelClose").addEventListener("click", () => panel.close());
  $("dockBtn").addEventListener("click", () => panel.toggleDock());
  $("runChip").addEventListener("click", () => {
    const job = (state.recent || []).find((j) => j.id === $("runChip").dataset.job);
    if (job) openOutput(job);
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
