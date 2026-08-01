# Backlog

Ideas, features, and fixes planned for future versions — not committed to any
specific release yet. When something here actually gets built, it moves into
`CHANGELOG.md` under `[Unreleased]` and gets removed from here.

Each entry: what it is, why it matters, and open questions to resolve before
implementing — not a design doc, just enough context to pick it up later
without having to reconstruct the reasoning from scratch.

---

## 🪙 "Minimal mode" — a cheaper deployment profile using Spot Instances

**What**: a second deployment path, alongside the current one — not a
replacement — aimed at the lowest realistic cost for a lab that doesn't need
to survive an unexpected interruption. Candidate name: `minimal` mode (open
to a better one).

**What it would change**:
- AWS Spot Instances instead of On-Demand for masters/workers (and/or
  bootstrap, which is short-lived anyway and a natural first candidate).
- EBS volumes reduced to the smallest viable size/type (currently gp3 120GB
  across the board — revisit what OpenShift actually needs at minimum).
- Possibly trim other resources/capabilities not strictly required for a
  bare-functional cluster.

**Why**: the whole project already leans cost-conscious (`ocplab cost`,
`safety-net`, `power off`) — Spot is the next lever, and a meaningfully
bigger one (up to ~70-90% off On-Demand for compute).

**Open questions**:
- Spot instances can be reclaimed by AWS with a 2-minute warning. For a
  3-master control plane that needs etcd quorum, losing a master
  unexpectedly is a real risk — needs a clear answer on how disruptive this
  actually is in practice before calling it a supported mode, not just "use
  Spot and hope."
- How is `minimal` mode selected — a field in `cluster.yaml`
  (`platform.aws.capacityType` or similar), a separate example config, a
  CLI flag? Needs a decision before touching Terraform.
- Does this interact with `ocplab power on/off`? Restarting a Spot instance
  isn't the same guarantee as an On-Demand one (capacity might not be
  available at restart time).

---

## 📊 Optional monitoring stack (infrastructure + platform)

**What**: an ad-hoc, opt-in monitoring stack giving a centralized view
across both the AWS infrastructure side and the OpenShift platform side —
not replacing OpenShift's own built-in cluster-monitoring-operator, but
sitting alongside/on top of it.

**Why**: right now, cluster health is a point-in-time check
(`ocplab verify`) — there's no ongoing/historical visibility. For a lab used
over multiple sessions, being able to see trends (not just current state)
has real value.

**Why "ad-hoc" / optional**: same reasoning as `safety-net` — this is extra
weight not everyone using the lab wants running by default. Should be its
own opt-in piece (own role, own `ocplab` subcommand or flag), never part of
the default `deploy` path.

**Open questions** (genuinely open — no solution decided yet):
- Scope: infra-side (AWS: costs, instance health) only, platform-side
  (OpenShift: operators, resource usage) only, or both unified?
- Build vs. reuse: lean on OpenShift's own monitoring stack and just expose
  it better, or bring something external (Grafana, CloudWatch dashboards)?
- Where does it run — inside the cluster (adds load to the very thing being
  monitored) or outside it?

---

## 🔢 Parameterize the OpenShift version in `cluster.yaml` (and manage the binaries)

**What**: let `cluster.yaml` declare which OpenShift version to install, and
have `ocplab` download and cache the matching `openshift-install` **and**
`oc` itself — instead of the version being implicitly whatever binaries
happen to be on the machine, installed by hand.

**Why**: right now the version isn't a config value anywhere, and README
§2.1 tells people to install from the mirror's `.../clients/ocp/latest/`
path. That's not reproducible: two people cloning this repo a month apart
get different clusters from the same config. Making the version explicit
means the declared config actually matches what gets deployed, and it turns
"install two binaries by hand" into one less manual prerequisite.

### Verified against the mirror (2026-08-01) — the URL scheme works

The `latest` segment in the README's download URL is just a directory name,
and it can be swapped for anything below. All confirmed live:

| Form | Example | Result |
|---|---|---|
| Exact version | `/clients/ocp/4.22.6/` | 200 |
| Old version | `/clients/ocp/4.19.9/` | 200 |
| Channel alias | `/clients/ocp/stable-4.21/` | 200 |
| Arch-aware path | `/pub/openshift-v4/arm64/clients/ocp/4.22.6/` | 200 |
| Nonexistent version | `/clients/ocp/4.99.9/` | **404** |

The directory listing goes back to 4.1.0, so the whole history is reachable.

Each version/channel directory also carries:
- **`release.txt`** — contains `Name: <x.y.z>`, so a channel alias can be
  resolved to the concrete version it currently points at, and that resolved
  value recorded for reproducibility.
- **`sha256sum.txt`** — checksums for every tarball, so downloads can be
  verified rather than trusted.
- The clean **404** on a nonexistent version is nearly-free validation for
  whatever `cluster.yaml` declares.

**`stable` and `latest` are not the same thing** — worth knowing, since the
README currently points at `latest`, which runs *ahead* of stable:
`latest` → 4.22.7 while `stable`/`stable-4.22` → 4.22.6, at the time of
checking.

### Decided

- **Scope: `stable-X.Y` channels plus `latest`, nothing else.** `candidate-*`
  are unsupported pre-release builds and `fast-*` runs ahead of stable —
  neither belongs in a lab meant to be reproducible. `latest` stays in scope
  because it's what README §2.1 uses today, so keeping it preserves current
  behaviour for anyone already following the docs. Note it is *not* a synonym
  for stable: at time of checking `latest` → 4.22.7 while `stable-4.22` →
  4.22.6.
- **Binaries move out of `/usr/local/bin` and into a per-version cache**,
  one directory per version, e.g. `~/.ocplab/bin/<version>/{openshift-install,oc}`.
  `ocplab` invokes them by **absolute path**. Two things fall out of this for
  free: no `sudo` (which matters — the CLI refuses to run as root), and no
  repeat of the PATH-ambiguity problem already hit with `ansible-playbook`
  resolving to the wrong copy.
- **Download is cache-aware**: if the requested version is already present in
  the cache, do nothing. Only fetch what's missing.
- **`oc` becomes managed too**, not just `openshift-install` — which removes
  it (and `openshift-install`) from the manual-prerequisites list in
  `prereqs` and README §2.1.

### How the user runs `oc` — resolved, with the version-switching trap

The obvious approach (symlink `.venv/bin/oc` → `~/.ocplab/bin/<version>/oc`,
since the venv is already on the PATH) is **wrong as stated**, because that
symlink hardcodes one version. Destroy the lab, change the version in
`cluster.yaml`, redeploy — and the user's shell still resolves `oc` to the
*previous* version's binary. That failure is silent, not loud: client/server
skew of ±1 minor mostly works, so `oc get co` looks fine while some
`oc adm` behaviour quietly doesn't match the cluster. Prune that old cached
version and the symlink dangles instead, breaking `oc` with a confusing
error.

Fix: **one extra level of indirection**, separating "where a binary lives"
from "which version is active".

```
~/.ocplab/bin/4.22.6/{openshift-install,oc}    immutable per-version store
~/.ocplab/bin/4.21.25/{openshift-install,oc}
~/.ocplab/bin/current -> 4.22.6/               the single definition of "active"
.venv/bin/oc          -> ~/.ocplab/bin/current/oc
```

Switching versions repoints exactly one symlink (`current`); everything
downstream follows automatically and the venv symlink never needs touching
or updating. `ocplab setup --recreate` only has to recreate that one link,
idempotently.

**Hard rule that comes with it: `ocplab` itself must never resolve anything
through `current`.** Internally it always uses the absolute versioned path.
`current` exists solely for the human's interactive shell — otherwise
repointing the version mid-`deploy` would swap the binary out from under a
running operation.

Remaining sub-questions:
- `current` is per-user (`~/.ocplab/`) while `cluster.yaml` is per-directory.
  Fine for one lab; ambiguous if someone ever runs two clusters from two
  checkouts. Does `current` belong in the repo instead?
- Pruning must refuse to delete whatever version `current` points at, and
  `ocplab` should detect and repair a dangling `current` rather than
  surfacing a raw "No such file or directory".
- When is `current` repointed — at `render`, at `deploy`, or by an explicit
  command? Repointing on `render` is the most predictable, since that's
  already the step that turns `cluster.yaml` into everything else.

### Stable versions currently published (2026-08-01)

Candidates for a compatibility test matrix. The tarball date is a useful
signal: the ones that haven't moved in many months are no longer receiving
z-stream updates, i.e. effectively past their support window.

| Channel | Resolves to | Install tarball last updated |
|---|---|---|
| `stable-4.22` | 4.22.6 | 2026-07-28 |
| `stable-4.21` | 4.21.25 | 2026-07-28 |
| `stable-4.20` | 4.20.30 | 2026-07-28 |
| `stable-4.19` | 4.19.39 | 2026-07-29 |
| `stable-4.18` | 4.18.49 | 2026-07-29 |
| `stable-4.17` | 4.17.55 | 2026-07-16 |
| `stable-4.16` | 4.16.55 | 2026-01-22 |
| `stable-4.15` | 4.15.59 | 2025-11-19 |
| `stable-4.14` | 4.14.58 | 2025-12-04 |

`stable-4.1` through `stable-4.13` also exist but are far past end of life.
Realistic first target: **4.20 / 4.21 / 4.22** (the three actively-updated
ones), widening only if there's a reason to. Confirm the actual Red Hat
lifecycle status per minor before claiming support — the tarball dates above
are a proxy, not the official answer.

### Open questions

- **Size**: `openshift-install-linux.tar.gz` is **435 MB** (`oc` is 41 MB).
  Caching per version is mandatory, not a nicety — but it also means the
  cache grows by ~475 MB per version tried. Needs a pruning story (`ocplab`
  subcommand to list/remove cached versions?) before it silently eats disk.
- **What does `preflight` check now?** Today it's a bare `which` for four
  binaries. It would shift to: is the declared version cached (and if not,
  is that a failure or a "will download"?), and warn when a binary already
  on the PATH disagrees with what `cluster.yaml` declares — that mismatch
  will otherwise be a genuinely confusing failure mode.
- **What exactly goes in `cluster.yaml`?** A pinned `4.22.6`, a channel
  `stable-4.22`, or either? A channel is convenient but reintroduces the
  reproducibility problem it's meant to solve — mitigated by resolving via
  `release.txt` and recording the concrete version that was used.
- **Backwards compatibility**: people already have these binaries installed
  from the current README. Does the managed cache take over unconditionally,
  or is it opt-in with the PATH binary as fallback?
- **Architecture**: the mirror has an `arm64` path, but `terraform/`
  hardcodes x86_64 instance types (`m5.*`). Multi-arch is a separate piece of
  work — don't let the download layer imply it's supported.

### Still true regardless

Being able to *download* any version isn't the same as *supporting* it. The
four documented traps in `CLAUDE.md` — and everything else in it — were found
and fixed against 4.22 specifically. Each version in the matrix needs at
least one real end-to-end smoke test before it gets claimed as supported.

**Bonus, already true**: the RHCOS AMI is auto-discovered from the installer
binary itself (`openshift-install coreos print-stream-json`, no AWS or
network call). So pinning the version pins the node image too, automatically
and consistently — one config field ends up controlling both.

---

## 🔐 `ocplab ssh` — SSH into cluster nodes

**What**: a new subcommand to SSH into a master or worker node directly
(e.g. `ocplab ssh master-0`, `ocplab ssh worker-1`), instead of hand-typing
the key path and public/private DNS name every time.

**Why**: this already comes up during any real troubleshooting session —
right now it means manually finding the node's address and constructing
the `ssh -i ...` command by hand.

**Already true today, don't rebuild it**: `terraform/security-groups.tf`
already restricts port 22 on both the master and worker SGs to a single
`/32` — the public IP of whichever machine ran `terraform apply`, captured
live via `data "http" "my_ip"` (`https://checkip.amazonaws.com`) at plan
time. `ocplab ssh` doesn't need to add this restriction; it needs to
**respect** it — and clearly surface the failure mode where it's the most
confusing: if `ocplab ssh` is run from a *different* machine/IP than the
one that last ran `apply` (different network, VPN toggled, home vs.
coffee-shop), the connection will just time out with no useful error
pointing at the actual cause. Worth an explicit check/message for that
case rather than a bare SSH timeout.

**Open questions**:
- Node addressing: nodes sit in the private subnet with no public IP —
  `ocplab ssh` needs a route in (bastion via one of the LBs? SSM Session
  Manager instead of raw SSH? Or is the master's port 6443 exposure a hint
  there's already a path worth reusing?). Needs research before assuming
  plain `ssh -i key ec2-user@<node>` even works from outside the VPC.
- Target selection UX: by role+index (`master-0`), by raw instance ID, by
  private DNS name — pick one consistent with how `power`/`cost` already
  identify nodes (they use the EC2 instance's own `private_dns_name`).

---

## 🧹 Audit: everything the bootstrap prerequisites create must be destroyable

**What**: verify (and fix if not) that `ocplab bootstrap destroy` can tear
down everything `ocplab bootstrap apply` creates — the general principle
being that nothing from the AWS-prerequisites step should be able to
outlive its own teardown path.

**Already true today, checked before backlogging this**:
- The **public DNS hosted zone** *is* already destroyable —
  `ansible/roles/bootstrap/tasks/route53.yml`'s `destroy` block deletes it,
  including cleaning up stray records (e.g. the `*.apps` alias the
  ingress-operator leaves behind) first, since Route53 refuses to delete a
  non-empty zone. This part is not missing.
- The **S3 bucket for `bootstrap.ign`** (`terraform/bootstrap.tf`,
  `aws_s3_bucket.ignition`) is **not** created by the `bootstrap` Ansible
  role at all — it's Terraform-managed cluster infrastructure, torn down
  by `ocplab destroy` (`terraform destroy`) along with everything else, not
  by `ocplab bootstrap destroy`. The bucket has no `force_destroy`, but
  since its one object (`aws_s3_object.bootstrap_ignition`) is a separate
  Terraform-managed resource, normal dependency ordering should delete the
  object before the bucket — this *should* already work, but hasn't been
  independently confirmed live (verify on the next real `ocplab destroy`
  rather than assuming).

**Real open item**: this project already has one documented case of
"bootstrap" meaning two different things (the Ansible role for AWS
prerequisites vs. `bootstrap.tf`'s S3 bucket/instance for the cluster's own
bootstrap process — see `CLAUDE.md`, "easy to confuse"). Worth a pass to
make sure that confusion hasn't hidden an actual gap anywhere else, not
just in the two things checked here — e.g. the IAM user and local SSH
keypair `bootstrap apply` creates are already covered by `bootstrap
destroy` per existing design, but re-confirm nothing new has been added
since without updating the destroy path to match.

---

## ✨ Presentable CLI output, execution logs, and useful failure messages

**What**: three related changes that all land in the same place —
`run_ansible_playbook()` in `ocplab` (around line 605), the single funnel
every playbook-backed command goes through:

1. **Stop showing raw Ansible as the primary output.** Replace the wall of
   `TASK [role : ...] ****` banners with something readable — progress the
   user actually wants to follow, not the installer's internals.
2. **Persist every run that changes something to a log file**, in a `logs/`
   directory, named after the command/action and the time it ran (e.g.
   `logs/2026-08-01T18-42-11_deploy.log`).
3. **On failure, say why in plain language** — the failing task and its
   actual error, formatted readably — followed by a pointer to the full log
   file for the run.

**Why**: today the CLI is a thin, well-designed UX layer over Ansible, and
then hands the user unfiltered Ansible. The output ergonomics of individual
tasks have already been tuned by hand more than once (end-of-run nameserver
table, the `less +F` hint before the long `wait-for`s) — this is the same
concern applied at the level of the whole run instead of task by task. And
once a `deploy` scrolls hundreds of lines past, there is currently **no
record of it at all** — nothing is captured anywhere.

**Already true today, don't rebuild it**:
- `run_ansible_playbook()` calls `subprocess.run(cmd, cwd=ANSIBLE_DIR)`
  with **no** `capture_output` / no redirection — Ansible writes straight to
  the terminal and nothing is retained. It then does
  `sys.exit(result.returncode)`, so a failed run produces **no ocplab-level
  message whatsoever**: whatever Ansible printed last is all the user gets.
- `ansible/ansible.cfg` already sets `stdout_callback = default` and
  `callback_result_format = yaml` — the switch point for presentation is
  one line in a file that already exists, plus a `callback_plugins` path if
  a custom one gets written.
- `-v/-vv` and `--dry-run` (`--check --diff`) are already plumbed through
  and must keep working — whatever the pretty layer does, `-v` should stay
  the escape hatch back to raw Ansible.

**Worth knowing before designing this** (found while writing this entry, not
yet acted on):
- Ansible has a **native** `ANSIBLE_LOG_PATH` (also `log_path` in
  `ansible.cfg`) that writes a complete run log to a file *without* touching
  what goes to stdout. That decouples requirement 2 from requirement 1
  entirely — file logging does not have to mean capturing/`tee`-ing the
  subprocess pipe, which is the fiddly part. Log path can be set per-run via
  the environment passed to `subprocess.run`.
- "Show the stderr of Ansible prettily" is not quite the right target: an
  Ansible task failure does **not** generally arrive on the process's
  stderr. It arrives as the failed task's result fields (`msg`, and
  sometimes `stderr`/`stdout` for `command`/`shell` tasks) rendered to
  stdout by the callback. Getting a reliable structured error means either
  a custom callback plugin or parsing the log — not reading `stderr`.
  Worth resolving early, since the whole failure-message feature depends on
  it.

**Open questions**:
- **How pretty, and at what cost?** A custom stdout callback plugin (pure
  Ansible, no new dependency) vs. capturing the stream and re-rendering it
  in Python with something like `rich` (much nicer, but a new runtime
  dependency in `requirements.txt` and a second thing that can break).
  There are also stock alternatives worth evaluating first — `dense`,
  `unixy`, `community.general.yaml` — which are a config change and nothing
  more.
- **Live feedback must survive it.** `deploy` has ~20-minute silent stretches
  (`wait-for bootstrap-complete`, `wait-for install-complete`). A prettifier
  that buffers, or that collapses everything into a spinner, makes a long
  run *less* legible, not more. Whatever is chosen has to stream. Note the
  workflow this has to coexist with: watching the run in one terminal while
  tailing `install-dir/.openshift_install.log` in another.
- **Which commands log?** The stated rule is "everything that does, modifies
  or destroys": `bootstrap apply|destroy`, `ignition`, `deploy`, `destroy`,
  `power on|off`, `safety-net apply|destroy`. Read-only ones (`preflight`,
  `verify`, `cost`, `status`, `power status`) arguably shouldn't clutter
  `logs/` — but `verify` output is exactly what someone would want to keep
  when a deploy goes wrong. Decide deliberately rather than by accident.
- **Log file naming and retention.** Timestamp format (ISO-8601 is sortable
  but has colons — awkward in filenames), whether the cluster name belongs
  in the name, and whether old logs are ever pruned or grow forever.
  `logs/` must be added to `.gitignore` — it will contain live AWS
  identifiers and cluster internals, and it is **not** currently ignored.
- **Does `--dry-run` write a log?** It changes nothing, but it's the run
  people most want to re-read afterwards.
