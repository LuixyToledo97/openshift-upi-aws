# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
uses [Semantic Versioning](https://semver.org/).

## [1.1.0] - 2026-08-04

### Added


- `ocplab web start|stop|status`: a browser UI for the lab — dashboard, a
  `cluster.yaml` editor with live validation, and every operation with its
  output streaming as it runs.

  It runs as a background service and gives the terminal back: the server is
  spawned in its own session, so closing the shell does not stop it. `status`
  reprints the URL with its token, `stop` shuts it down. A recorded PID is
  never trusted on its own — the process has to be alive *and* recognisably
  the server, or `stop` could signal whatever inherited a recycled id.

  `start` checks its prerequisites first and starts **nothing** if any fail,
  reporting all of them at once. It requires `cluster.yaml` to exist but
  deliberately not to be valid: the Configuration editor is how a broken one
  gets fixed, and refusing to start over a config error would lock you out of
  the tool that repairs it.

  It is a second thin layer, not a second program: the server never calls AWS
  and never reads Terraform state, it runs `ocplab <command>` as a subprocess
  and shows that command's own output. The browser also cannot supply an argv —
  it posts a command *id* and the server runs the fixed argv that id maps to,
  so there is no path from user input to a command line.

  It binds `127.0.0.1` with no flag to change it, rejects requests whose `Host`
  header is not loopback (a page you visit could otherwise point a DNS name at
  `127.0.0.1` and drive it through your browser), and requires a token minted
  at startup. Operations are serialised one at a time, because there is one
  Terraform state and two concurrent applies corrupt it. Output is kept
  server-side, so closing the tab does not stop the run, and reopening it
  replays everything before following live.

  `ocplab ssh` is deliberately excluded — it needs a real terminal, and a web
  terminal would hand whoever reached the server a root shell on the nodes.

  The overview is built on data, not on parsed prose: `ocplab status` grew a
  `--json` flag and the dashboard reads that. A dashboard scraped from human
  output breaks the first time someone improves a sentence. Everything it
  shows is local and instant, which is what makes it safe to poll — live state
  (nodes, operators, power, cost) stays behind explicit actions, because those
  cost time and money and should be asked for.

  The output panel appears when something runs, docks bottom or right, and is
  drag-resizable. It reserves space rather than overlaying, so the page below
  stays scrollable — an overlay made three views unusable in an earlier
  revision. Its source selector switches between the run's own output and any
  log file tailed live, Terraform's resource-by-resource detail and the
  OpenShift installer's log included; the CLI can only point you at those in
  another terminal.

  Actions are vertical lists with their descriptions folded behind a chevron,
  each row tinted by how much damage it can do — red destroys, amber changes
  something you may not get back, green restores. Runs can be filtered by
  read-only versus changed-something, and **survive a restart of the server**:
  `logs/runs.json` keeps the metadata, and a run from an earlier server replays
  from its own log file rather than from lines nobody kept. A Help tab carries
  the workflows — first-time setup, deploy, day-to-day, teardown — and About
  the version, licence and author's links.

  No new dependency and no build step: a stdlib HTTP server, plus plain HTML,
  CSS and JavaScript. The two fonts (Inter and JetBrains Mono, 176 KB, SIL
  Open Font License) are bundled rather than fetched, so it renders the same
  everywhere and needs no internet.

- `ocplab status --json`, the same information as the text output in a
  machine-readable shape. Written for the web dashboard, useful for anything
  else that wants the local state without parsing a report.

- `oc` and `kubectl` now point at **this** cluster, not just at the matching
  client version. Activating the venv exports `KUBECONFIG`, and `deactivate`
  puts it back.

  Pinning `openshift.version` had solved which *binary* you get; which
  *cluster* it talked to was still whatever `~/.kube/config` had as its current
  context — for one of us, a leftover Docker Desktop. That is the worse half:
  the failure is not "cluster not found", it is reaching a different cluster in
  silence, and a hand-typed `oc delete` in that state lands somewhere it was
  never meant to.

  `ocplab setup` adds a clearly marked block to `.venv/bin/activate`. It never
  overrides a `KUBECONFIG` you set yourself, and it exports the path even
  before `install-dir` exists — pointing at a file that isn't there makes `oc`
  fail loudly, which beats quietly using the context this exists to avoid.
  Deleting the block opts out. **An existing venv needs `ocplab setup` run once
  to pick this up**; nothing is rebuilt.

- `ocplab env`, which prints `export KUBECONFIG=...` for `eval "$(ocplab
  env)"` — for the shells the activate hook can't reach: one where the venv was
  never activated, a script, or a venv created before the hook existed. Only
  the export goes to stdout, so it is always safe to eval.

- `ocplab status` now reports which cluster your shell points at, beside the
  version it runs, and `ocplab console` says so when you're not on this one.

- `ocplab repair`: recreates workers that Terraform manages but that no longer
  exist, approves the new node's CSRs, and removes `Node` objects left behind
  by instances that are gone. Written alongside Spot support, since a reclaimed
  worker is the common cause, but it is not Spot-specific — a node terminated
  by hand or lost to an AWS failure recovers the same way.

  It is the only command that runs `terraform apply` against a **running**
  cluster, so it plans first and inspects the result: a repair is add-only, and
  anything that would be changed, replaced or destroyed is refused with the
  plan shown. That guard is the point of the command rather than a precaution —
  `render` auto-discovers the RHCOS AMI, so a changed `openshift.version` makes
  Terraform want to replace all three masters, and an unguarded apply would
  destroy the cluster it was asked to fix.

  It also declines to create masters: a replacement master arrives with a new
  private IP and needs its etcd membership repaired by hand, so an instance
  that exists but isn't an etcd member is worse than an obviously missing one.
  And it never runs the `ignition` role, which would mint a new `infraID` and
  re-tag a live cluster.

  `Node` objects are pruned only when no running EC2 instance carries that
  private DNS name — checked live, so a merely unhealthy node keeps its object.
- `openshift.capabilities` in `cluster.yaml`, passed through to the installer's
  own `capabilities` block. Optional components that aren't enabled are never
  deployed, so their images are never pulled — and image pulls across the NAT
  gateway are the largest single charge this project incurs.

  `CloudCredential`, `CloudControllerManager` and `Ingress` cannot be disabled
  on AWS; that is the installer's rule, discovered by asking it rather than by
  reading around it. `validate` enforces it so the failure arrives at
  `ocplab validate` instead of several commands later.

  Measured rather than estimated: against a ten-deploy baseline of 35.2 GB, the
  set now shipped in `examples/minimal.yaml` brought a deploy down to 24.8 GB —
  **29% less, about $0.46 a time** — with the cluster healthy at 27
  ClusterOperators instead of 34. It costs real functionality: no internal
  image registry, no S2I builds, no OperatorHub catalogue. `init --minimal`
  enables it; the standard template documents it commented-out.

  Both figures come from the NAT gateway's `BytesInFromDestination` metric in
  CloudWatch, which outlives the gateway — so the baseline was reconstructed
  from ten already-destroyed NAT gateways, one datapoint per deploy. Cost
  Explorer could not have answered this: it lags a day and nets credits against
  usage, which is why the first look at the bill showed zeros.
- `ocplab init --minimal` writes the cheaper profile instead of the standard
  one. The flag makes it discoverable: an example only helps people who already
  know it exists.

  Both templates now live in `examples/` — `standard.yaml` and `minimal.yaml` —
  and `init` writes them verbatim. The standard one used to be a string
  constant inside `ocplab`, which left the two halves of the same idea in
  different places. A config template is data, the whole repository is needed
  to run anything regardless, and in a public repo the config format is among
  the first things people read.

  `.gitignore` now anchors `/cluster.yaml` to the repository root. The
  unanchored pattern matched at any depth, so a shipped `examples/cluster.yaml`
  would have been excluded silently — present for the author, missing for
  everyone who cloned.
- Spot instances for compute and bootstrap nodes: `compute.spot` and
  `bootstrap.spot` in `cluster.yaml`, both defaulting to false. Measured in
  `eu-west-1a`, Spot ran 50-55% below on-demand — worth correcting against the
  70-90% figure that gets quoted.

  `controlPlane.spot` is **rejected**. In UPI there is no
  ControlPlaneMachineSet and no Machine API for manually provisioned machines,
  so a reclaimed master is never replaced automatically and recovering one
  means repairing etcd membership by hand. Workers and the bootstrap are
  recoverable — `terraform apply` recreates them and ocplab already approves
  the CSRs — which is why the choice is per node group rather than one
  "minimal mode" switch that would force the same answer on all three.

  Requests are one-time with the default `terminate` behaviour. AWS supports
  Spot requests that stop rather than terminate, preserving the disk, but only
  for `persistent` requests — and a persistent request outlives its instance
  and can relaunch it, possibly into a VPC midway through being destroyed.

  `ocplab power off` now refuses when workers are on Spot, before cordoning or
  draining anything: a one-time Spot instance can only be terminated, never
  stopped.
- README §2.1.1: a tested-versions table. `ocplab versions list` shows what the
  mirror publishes, which says nothing about what has actually been run — the
  table records only versions that completed a real deploy → verify → ssh →
  destroy cycle against AWS, with the failures that happened along the way.
  Today that is exactly one version, which is the point of writing it down.


- Readable command output: a custom Ansible stdout callback
  (`ansible/callback_plugins/ocplab_output.py`) replaces the raw
  `TASK [role : ...] ****` stream with one line per task. Skipped tasks are
  hidden, `debug` reports and `assert` messages — which is how the roles
  actually report to the user — are promoted, loops collapse to a summary
  with per-item detail, and tasks using `until`/`retries` show live attempt
  progress. `include_tasks`/`include_role` results are suppressed as
  scaffolding, and modules that write their own terminal output (`pause`) no
  longer collide with the in-place "currently running" line.
- Per-run execution logs under `logs/`, one plain-text file per run, named
  after the command, its action, and when it started (e.g.
  `logs/2026-08-02_104512_bootstrap-apply.log`). Written by the callback,
  ANSI-free and timestamped, and more complete than the terminal: it keeps
  skipped tasks and the full result of anything that failed. The path is
  printed before the run starts, so long operations can be followed with
  `tail -f` from another terminal.
- A descriptive failure report: on a failed run `ocplab` now names the task
  that failed and shows its actual error, then points at the log file for
  the rest. Previously a failure produced no ocplab-level message at all —
  whatever Ansible printed last was all there was. For `command`/`shell`
  tasks it deliberately prefers the failed program's own stderr over
  Ansible's generic "The command exited with a non-zero return code",
  which is a label rather than a diagnosis.

- **The OpenShift version is now a config value.** `openshift.version` in
  `cluster.yaml` pins an exact `x.y.z`, and `ocplab` downloads and caches the
  matching `openshift-install` and `oc` under `~/.ocplab/bin/<version>/`,
  invoking them by absolute path. Previously the version was whatever binaries
  happened to be installed by hand, from a mirror URL (`.../clients/ocp/latest/`)
  that moves — so the same `cluster.yaml` produced different clusters months
  apart.

  New `ocplab versions` command:

  ```
  ocplab versions list                # what the mirror publishes, and what you have
  ocplab versions download 4.22.6     # checksum-verified, ~476 MB, cached
  ocplab versions download            # the version cluster.yaml declares
  ocplab versions rm 4.19.39          # refuses to remove the one in use
  ```

  `list` discovers the `stable-X.Y` channels from the mirror rather than
  hardcoding them, resolves each to its concrete version via `release.txt`,
  and shows the publication date — a channel that hasn't moved in months is
  effectively out of support. The listing is cached in
  `~/.ocplab/versions-cache.json` for 12h (`--refresh` to force), the same
  pattern `cost` uses for AWS pricing.

  **Opt-in and backwards compatible**: with no `openshift.version`, ocplab
  keeps using whatever is on the PATH, exactly as before. `preflight` only
  requires `openshift-install`/`oc` on the PATH in that case, and warns when a
  PATH binary disagrees with the pinned version. `.venv/bin/{oc,kubectl,openshift-install}`
  are symlinked to the pinned version so typing `oc` by hand matches the
  cluster, kept in step by `render`, `status` and `versions` alike, and
  removed again if the version is unpinned. When those links actually move,
  ocplab says so and points at `hash -r`, since a shell that already ran `oc`
  keeps calling the path it cached — a failure that otherwise looks exactly
  like a download that didn't happen. `ocplab status` reports the pinned
  version and the cache size.

  Downloads are verified against the mirror's `sha256sum.txt`, and the
  unpacked binary is asked its own version before being accepted.

  The RHCOS AMI follows from the same binary — it's read out of the
  installer's embedded image catalogue, no network or AWS call — so one field
  pins both the cluster and its node image. Pinning `openshift.rhcosAmi` as
  well is still allowed but now warns when the two disagree: a node image that
  doesn't match the release surfaces as odd boot failures much later, never as
  a configuration error.
- `ocplab ssh`: lists the cluster's running nodes, or opens an interactive
  shell on one — `ocplab ssh master-0`. Anything after the node name is passed
  through to `ssh`, so `ocplab ssh master-0 sudo crictl ps` works. Node names
  are the ones Terraform already assigns, and the key is derived from
  `credentials.sshPublicKeyFile`.

  Reaching them needed a new piece of infrastructure
  (`terraform/instance-connect.tf`): masters and workers have no public IP and
  sit in the private subnet, so the existing `SSH from my IP` security-group
  rules had no route to work over. An **EC2 Instance Connect Endpoint** —
  an IAM-authorized TCP proxy, free of charge, and free of cross-AZ transfer
  cost in a single-AZ lab — avoids both giving the nodes public addresses and
  running a bastion. No security-group change was needed: the nodes already
  accept traffic from within the VPC. It is created with the rest of the
  infrastructure rather than on demand, because a debugging tool you have to
  provision first is one you don't have when you need it.
- Live, readable Terraform progress. The `infra` role now runs `terraform`
  directly with `-json` and pipes it through
  `ansible/roles/infra/files/tf_render.py`, which writes a resource-by-resource
  stream to a log file next to the run's own — followable with `tail -f`
  while a 4-minute apply or a 15-minute destroy is in flight:

  ```
  Plan: 62 to add, 0 to change, 0 to destroy.
    » creating   NAT gateway main
      … still creating NAT gateway main (1m 30s)
    ✓ created    NAT gateway main         nat-0f9e7d6c   1m 52s
  ```

  The plan summary is shown before anything is applied, the ocplab output
  ends with `applied: 62 added · 0 changed · 0 destroyed`, and a failure now
  reports Terraform's own diagnostics instead of a module dump. Under
  `--dry-run` the plan itself is listed resource by resource
  (`+ would create   NAT gateway main`), which during a real apply is
  suppressed as redundant with the apply events. Resource
  types are named in plain language (`aws_nat_gateway` → "NAT gateway"), with
  a fallback so unmapped types still read well.

### Changed


- `destroy` no longer spends five minutes waiting for the router ELB to delete
  itself. Measured on a real teardown: the poll ran all thirty attempts (364s,
  half of a 12m10s destroy) and the manual-delete fallback then removed it in
  26s. That is structural rather than unlucky — the ingress-operator is scaled
  to zero so it cannot recreate the ELB, and it is also what deletes the router
  Service the cloud-controller-manager watches, so nothing is left to tear the
  ELB down on its own. The timeout drops from 300s to 60s, which keeps the poll
  as a check without paying for an outcome that cannot arrive. Deleting the
  router Service explicitly is the proper fix and is in `BACKLOG.md`.


- The `infra` role no longer uses `community.general.terraform`. That module
  returns everything only when it finishes, which is why a long apply showed
  one frozen line and no way to tell progress from a hang. Its built-in
  `check_mode` (running `terraform plan` under `--check`) is reimplemented in
  the role rather than lost.
- `-v`/`-vv` is now an explicit escape hatch back to raw Ansible output.
  Those runs are still logged, via Ansible's own logger.
- Colour output is disabled automatically when stdout isn't a terminal, and
  honours `NO_COLOR`.

### Fixed


- Long `until`/`retries` waits produced no output at all when nothing was
  attached to a terminal. Each attempt went to the log file only, and the
  in-place "⋯ attempt n/m" line is a no-op without a TTY — so a `destroy`
  watched from the web UI, or any redirected run, sat silent for the six
  minutes it polls the router ELB and potentially twenty more on its ENIs,
  looking hung while working normally. Piped runs now get one line per
  attempt; terminals and the log file are unchanged.

- `cost` priced Spot instances at their on-demand rate. Measured on 2026-08-03
  against a live minimal-profile cluster, it reported **$0.9213/h** against a
  real **$0.8269/h** — inflated by **11.4%**, and inflated specifically in the
  direction that makes running on Spot look less worthwhile than it is.

  It now reads each instance's lifecycle — which `ec2_instance_info` already
  returns — and looks up the current Spot price for the types actually running
  as Spot. Those prices are deliberately **not** cached, unlike the on-demand
  ones: on-demand pricing changes a few times a year, Spot pricing moves by the
  hour and by availability zone, so a cached Spot price would be exactly as
  wrong as the on-demand figure it replaces, just less obviously. When a Spot
  price can't be fetched the instance falls back to on-demand and the report
  says which types that happened to, so the number is over-stated rather than
  quietly optimistic.

  The compute line splits into on-demand and Spot only when something is
  actually on Spot, and the report's closing note now says which basis was
  used — a Spot figure is a snapshot of a price that moves, and presenting it
  as if it were a rate would be its own kind of wrong.

- The orphaned `k8s-elb-*` security group could not be deleted, which stalled
  `destroy` on the VPC for twenty minutes and then failed it — with nothing in
  the output connecting the two, because the original single attempt swallowed
  its own error under `ignore_errors`.

  It was first treated as a timing problem and given retries. That made the
  failure visible, which was worth doing, but it could never have fixed it:
  measured on 2026-08-03, **zero** network interfaces were using the group and
  eleven minutes of retrying changed nothing. AWS reports
  `DependencyViolation: has a dependent object` for a security group that
  another group's *rules* reference, and the ingress-operator adds exactly such
  a rule to the node security groups so the router ELB can reach the nodes.
  That reference only clears when Terraform deletes the node groups — which
  happens *after* the cleanup task, so no retry budget could have succeeded.

  `teardown` now revokes the referencing rules before deleting the group, which
  is safe because those node groups are seconds from being destroyed anyway.
  The remaining short retry is a safety net for a genuine ENI attachment, and
  its warning says so, to send the next person looking at interfaces rather
  than back at the rules.

- Closing the terminal killed a running `deploy`. The stdout callback wrote to
  `sys.stdout` unguarded, so once the terminal window was gone the first task
  to complete raised and took the whole playbook down — silently, mid-install,
  leaving the bootstrap running and billing and its CSRs unapproved. Every
  terminal write is now guarded, and the log is written *before* stdout rather
  than after: the on-disk record is the durable half, the live view is the
  disposable one. Losing a window now costs the view and nothing else.
- `destroy` could fail with `DependencyViolation` on the internet gateway
  because the ingress router's ELB came back after being deleted. Deleting the
  default `IngressController` doesn't remove it — the cluster-ingress-operator
  reconciles it straight back, along with a new Classic ELB, and that ELB's
  ENIs hold the public subnet. Seen on 2026-08-03: deleted at 10:48:43, the
  ELB poll correctly saw zero at 10:48:48, and a replacement appeared at
  10:50:08, blocking the teardown until terraform gave up 20 minutes later.

  Earlier teardowns had won that race against the masters being terminated
  rather than avoided it. `teardown` now scales `cluster-version-operator` and
  then `ingress-operator` to zero before deleting anything — the CVO first, or
  it scales the other back up — and re-checks afterwards instead of trusting
  that "zero now" means "zero from here on". Not version-specific.


- `deploy` reported a healthy cluster as a failed deploy when the bootstrap
  took longer than `openshift-install`'s internal ~20-minute wait for the
  Kubernetes API — a limit no flag can extend. Seen for real: bootstrap spent
  21 minutes pulling operator images over the NAT gateway before it could
  render the control-plane manifests, and the installer gave up one minute
  before they landed. `wait-for bootstrap-complete` is now retried
  (`ocplab_bootstrap_wait_attempts`, default 3) instead of failing on the
  first timeout — safe because it's a monitor, not an action, and reattaches
  to the cluster's real state.
- Removed `ocplab_bootstrap_wait_timeout`, which was declared in
  `defaults.yml`, documented as the bootstrap wait timeout, and never read by
  anything.

- `ocplab bootstrap` could not run until `platform.aws.publicHostedZoneId`
  was filled in — but `bootstrap` is the command that *creates* the public
  hosted zone, so its ID couldn't be known yet. A chicken-and-egg that
  blocked every first-time run. The field may now be left on its `CHANGEME`
  placeholder for `bootstrap` only; every other command still validates it
  strictly, and `validate` now says what to do instead of only reporting the
  value as invalid.
- Commands ran against stale generated files. The Ansible roles read
  `generated.yml`, never `cluster.yaml` directly, but only `deploy` and
  `bootstrap` re-rendered it first — so `preflight`, `verify`, `cost`,
  `power`, `destroy`, `safety-net` and `ignition` all silently used whatever
  the previous render happened to write. Editing `cluster.yaml` and
  re-running had no effect; `-f <other-config>` was ignored outright. The
  render now happens in `run_ansible_playbook`, the single funnel every
  playbook-backed command goes through, so it can't be forgotten again.
- `--dry-run` previewed against stale data. Because the generated files
  weren't written under `--dry-run`, a dry-run inspected whatever the
  previous render left behind — reporting confidently on a config the user
  no longer had. Commands now refresh those two files from `cluster.yaml`
  even under `--dry-run`; they are gitignored, deterministic and rebuilt in
  under a second, so this isn't the kind of change the flag exists to
  prevent. `ocplab render --dry-run` is unaffected and still writes nothing,
  since there the generated files are the subject of the command.
- `ocplab cost` under-reported by missing the ingress router's load balancer.
  It only queried `elbv2`, and the router's ELB is **Classic**, which that API
  doesn't return at all — so a live cluster was reported at $1.0317/h when the
  real figure was $1.0597/h. The README's hand-written breakdown had listed
  that ELB correctly all along; only the code didn't count it. Found by
  checking the report against AWS on a running cluster.

  The Classic ELB is now discovered **by VPC**, not by name: the
  ingress-operator gives it a hash-based name (`afcbf899a…`), so the
  `contains(<cluster_name>)` filter the NLBs use could never have matched it —
  the same trap already documented for `teardown` in `CLAUDE.md`. It's also
  reported on its own line rather than merged into a single "Load Balancers"
  figure, since merging them is how it went unnoticed.
- `ocplab bootstrap destroy --delete-iam-user` deleted the IAM user but left
  its profile behind in `~/.aws/credentials`, so a dead access key stayed on
  disk indefinitely and `aws --profile <name>` failed with
  `InvalidClientTokenId` — reading like a broken key rather than a deliberately
  deleted one. Found by auditing every resource `bootstrap apply` creates
  against what `bootstrap destroy` removes; it was the only one without a
  counterpart. Only that profile section is removed, never the file, and only
  when the IAM user is actually deleted — a plain `bootstrap destroy` leaves
  the user alone, so those credentials are still valid and still needed.
- `ocplab --dry-run deploy` could never succeed: the `finalize` role's
  `kubernetes.core` tasks are not skipped under `--check` the way
  `command`/`shell` are, so a dry run always died resolving the API of a
  cluster it had deliberately not created. The whole role is now skipped
  under `--check`, with a line explaining why — there is nothing to preview
  in CSR approval or `wait-for install-complete` when nothing was deployed.
- A single `deploy` runs the `infra` role twice (the cluster's apply, then
  the bootstrap's targeted destroy) and both wrote to the same terraform log
  under `--dry-run`, where each is a `plan`. The log is now named after the
  intent rather than the terraform subcommand, so the two never collide.
- `ocplab bootstrap status` gathered the hosted zone ID and its four
  nameservers and then never printed them — the summary task was gated on
  the `apply` action. `status` now reports them too, worded for a zone that
  already exists rather than one just created.

## [1.0.0] - 2026-07-30

Initial public release.

### Added

- `ocplab` CLI covering the full lifecycle: `init`, `setup`, `validate`,
  `render`, `bootstrap`, `preflight`, `ignition`, `deploy`, `verify`,
  `cost`, `power`, `destroy`, `safety-net`, `status`, `console`, `prereqs`.
- Terraform for UPI infrastructure: VPC, subnets, NAT, IAM, security
  groups, load balancers, Route53 (public + private zones), EC2
  (bootstrap/masters/workers).
- Ansible roles for the full deploy/destroy lifecycle, RHCOS AMI
  auto-discovery, and post-install custom-certificate support
  (bring-your-own CA-signed cert).
- `ocplab verify`: live cluster health check (API reachability,
  `ClusterVersion`, node readiness, `ClusterOperators`), with a fast,
  bounded reachability check before touching the Kubernetes client.
- `ocplab power on|off|status`: graceful cluster shutdown/restart
  following Red Hat's documented procedure, and a read-only power-state
  check — an alternative to `destroy`, not a cost-saving one.
- `ocplab cost`: approximate current USD/hour for whatever's actually
  deployed, power-state aware, with a lazily-populated per-region AWS
  Pricing API cache.
- `ocplab safety-net`: AWS Budget with alerts, an automatic Budget Action
  lockdown at 80%, and a scheduled killswitch Lambda.
- `README.md` and `CLAUDE.md` documentation.
- MIT license.
