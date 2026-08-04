# CLAUDE.md

Project context and conventions for AI coding assistants (Claude Code and
similar). `README.md` is the documentation for humans; this file is the
equivalent for an assistant working in this codebase — architecture,
standing rules, and non-obvious gotchas worth knowing before changing
anything.

## What this is

**OpenShift on AWS with UPI** (User-Provisioned Infrastructure): Terraform
defines the infrastructure and the OpenShift installer only generates the
Ignition configs and monitors the boot process. This is not ROSA nor IPI.

The version is a `cluster.yaml` field, not a property of the project —
`openshift.version` pins the installer, the client and the RHCOS AMI together.
Development and every measurement in this file were done against 4.22.x; see
README's tested-versions table for what has actually been run end to end.

- Two front ends over one engine: the **`ocplab` CLI**, and a **local browser
  UI** (`ocplab web`) that runs the very same commands as subprocesses. Neither
  is the "real" one; see `The web UI` below for the rules that keep the second
  from becoming a second product.
- Cluster: `ocp4lab` · Domain: `aws.example.com` (placeholder — set your
  own in `cluster.yaml`) · Region: `eu-west-1` by default (1 AZ)
- 3 masters `m5.xlarge` + 2 workers `m5.large` + 1 temporary bootstrap
- ~$1.06/hour on-demand while it's up in `eu-west-1` — see `ocplab cost`
  for a live, region-aware figure
- **Plus a per-deploy data charge, which is the biggest line in the project.**
  The six nodes each pull their own images from quay.io
  through the NAT gateway. Measured per deploy from the gateway's CloudWatch
  `BytesInFromDestination`: **35.2 GB** across ten deploys (≈$1.58), or
  **24.8 GB** (≈$1.12) with the capability set in `examples/minimal.yaml`.
  Larger than all EC2 compute over the same period, and `ocplab cost` cannot
  see it — it prices resources, and this is usage. Never quote the hourly
  figure as the cost of a short cycle.

## Architecture

- **`ocplab` (Python) is a thin CLI with no business logic.** It
  validates `cluster.yaml`, translates it into `terraform.tfvars` +
  `ansible/inventory/group_vars/all/generated.yml`, and invokes
  `ansible-playbook`. All actual logic — AWS calls, idempotency, health
  checks — lives in Ansible roles, not in the Python script.
  - **`ocplab ssh` is the one deliberate exception**, and should stay the
    only one. An Ansible module never hands over the terminal, so a task
    physically cannot give you an interactive shell. The exception is kept
    narrow: the command resolves a node name to an instance ID and then
    `execvp`s into `ssh`, replacing the process — so there is no AWS logic
    to speak of, and nothing of ocplab is left running during the session.
    If a future command needs a TTY, follow this shape; if it doesn't, it
    belongs in a role.
- **Terraform is fixed `.tf` files + a generated `terraform.tfvars`**,
  not Jinja2-templated `.tf` files. Easier to read and debug than
  templated infrastructure code.
- **Ignition freshness policy**: `install-dir/` is reused if it's less
  than 24h old (`ocplab_ignition_max_age_hours`); older gets archived to
  `archive/` and regenerated. `ocplab ignition --force` skips the check.
- Every role that touches AWS is designed to read **real, live state**
  (via `amazon.aws.*` modules or the `aws` CLI) rather than trusting
  local files like `terraform.tfstate` — `power`, `verify`, and `cost`
  in particular never assume the cluster is in whatever state it was
  last left in.

## Commands

All genuinely implemented (nothing is a stub):

| Command | What it does |
|---|---|
| `prereqs` | Static checklist (no `cluster.yaml`/AWS needed): binaries, credentials, pull secret, DNS delegation |
| `init [--minimal]` | Writes a starter `cluster.yaml` — `--minimal` writes `examples/minimal.yaml` instead (smaller instances, 100 GB disks, Spot workers) |
| `setup` | Creates the project venv, installs Ansible + collections (`--recreate` to rebuild) |
| `validate` | Checks `cluster.yaml` for errors, accumulating all of them, not just the first |
| `render` | `cluster.yaml` → `terraform.tfvars` + `generated.yml` (auto-discovers the RHCOS AMI if unset) |
| `bootstrap [apply\|status\|destroy]` | IAM user, public hosted zone, SSH keypair — the one-time AWS prerequisites `preflight` can't create itself |
| `preflight` | Read-only: binaries, AWS credentials, pull secret, DNS delegation, AMI availability |
| `ignition [--force]` | Generates/regenerates `install-dir` and the Ignition configs |
| `deploy` | render → ignition → `terraform apply` → wait for boot → finalize, in one operation |
| `verify` | Live health check: API reachability (fast, bounded), `ClusterVersion`, node readiness, `ClusterOperators` |
| `cost` | Approximate current USD/hour for whatever's actually deployed, power-state aware |
| `console` | Prints the console URL and the `kubeadmin` password |
| `env` | Prints `export KUBECONFIG=...` for this cluster, for `eval "$(ocplab env)"` |
| `web start\|stop\|status` | Runs the local browser UI on `127.0.0.1` as a background service (dashboard, `cluster.yaml` editor, live operations) |
| `destroy` | Ordered teardown of the whole cluster |
| `power on\|off\|status` | Graceful shutdown/restart, or a read-only power-state check — an alternative to `destroy`, **not** a cost-saving one (EBS/NAT/LBs keep billing while off) |
| `ssh [node]` | Lists the running nodes, or opens a shell on one, through the EC2 Instance Connect Endpoint (nodes have no public IP) |
| `repair` | Recreates missing workers on a running cluster, approves their CSRs, prunes orphaned `Node` objects — refuses any plan that isn't add-only |
| `versions list\|download\|rm` | Manages the cached `openshift-install`/`oc` under `~/.ocplab/bin/<version>/`, pinned by `openshift.version` |
| `safety-net apply\|status\|destroy` | Budget + Budget Action + killswitch Lambda, outside Terraform |
| `status` | Local-only summary (`install-dir` age, Terraform resource count) — points to `verify`/`power status` for live state |

Global flags (before or after the subcommand): `-f/--config`, `--dry-run`
(maps to Ansible's `--check --diff`), `-v`/`-vv`, `--yes` (skip
confirmation), `--tags`.

`cluster.yaml`'s schema is documented in `docs/cli.md` — don't edit
`terraform.tfvars`/`generated.yml` by hand, they're generated from it.

## Structure

```
├── README.md               # landing page — links into docs/, deliberately short
├── docs/                   # the long-form documentation, one file per topic
│   ├── profiles.md          #   standard vs minimal, side by side
│   └── assets/              #   the mark, and the 1280x640 repo preview tile
├── ocplab                  # CLI in Python (thin UX layer)
├── web/                    # browser UI — a second thin layer over the same CLI
│   ├── server.py            #   stdlib HTTP server; runs `ocplab` as a subprocess
│   └── static/              #   index.html + app.js + style.css, no build step
├── cluster.yaml             # YOUR config — generated by `ocplab init`, gitignored
├── requirements.txt        # venv Python deps (ansible, boto3, dnspython...)
├── .venv/                  # project venv — gitignored
├── templates/              # install-config.yaml — historical reference only,
│                            #   no longer used at runtime (ignition generates its own)
├── terraform/               # ~60 resources
│   └── instance-connect.tf  #   EC2 Instance Connect Endpoint — how `ocplab ssh` gets in
├── ansible/
│   ├── ansible.cfg
│   ├── callback_plugins/
│   │   └── ocplab_output.py  # stdout callback: readable output + writes logs/
│   ├── requirements.yml    # collections (amazon.aws, community.general, kubernetes.core)
│   ├── inventory/
│   │   ├── localhost.yml
│   │   └── group_vars/all/
│   │       ├── defaults.yml    # fixed project values (hand-maintained)
│   │       └── generated.yml   # ← written by `ocplab render`, gitignored
│   ├── playbooks/           # one per ocplab command, plus deploy.yml/destroy.yml
│   └── roles/
│       ├── preflight/      # binaries, AWS credentials, pull secret, DNS, AMI
│       ├── bootstrap/      # IAM user, public hosted zone, SSH key — creates, doesn't verify
│       ├── ignition/       # install-config.yaml -> manifests -> Ignition configs
│       ├── infra/          # terraform apply/destroy, streamed via -json + files/tf_render.py
│       │                     #   (reused by cluster_boot and teardown)
│       ├── cluster_boot/   # wait-for bootstrap-complete + destroys the bootstrap
│       ├── finalize/       # CSR approval, deletes CPMS, wait-for install-complete, custom certs
│       ├── teardown/       # cleans up orphaned ELB/ENI/SG + terraform destroy
│       ├── safety_net/     # budget, budget action, killswitch lambda + schedule
│       ├── verify/         # read-only cluster health: API reachability, ClusterVersion, nodes, ClusterOperators
│       ├── power/          # graceful on/off/status — alternative to destroy, still bills EBS while off
│       └── cost/           # read-only: live AWS state + cached Pricing API data -> $/hour
├── install-dir/            # generated, ephemeral (24h-expiring credentials — never commit)
├── logs/                    # generated, one plain-text log per run — gitignored
└── archive/                 # install-dir from previous runs
```

**The venv:** `ansible-playbook`/`ansible-galaxy` aren't available system-wide
on Debian-based systems (`externally-managed-environment`) — they live in
`.venv/`, gitignored. `ocplab setup` creates it; `source .venv/bin/activate`
before running `preflight`/`bootstrap`/`deploy`/etc. `ocplab
validate`/`render` don't need the venv (they only depend on PyYAML). The
CLI refuses to run as root, since that breaks the `~/.aws`, `~/.ocplab`,
`~/.ssh` paths it reads from.

## Important rules

1. **Never run `terraform apply` or `destroy` without asking for explicit
   confirmation.** Every `apply` starts billing; every `destroy` tears
   down the environment.
2. **Never write secrets into the repo.** The pull secret lives at
   `~/.ocplab/pull-secret.json`, outside the git tree.
3. **Don't edit generated files**: `terraform/terraform.tfvars` and
   `ansible/inventory/group_vars/all/generated.yml` are written by
   `ocplab render` from `cluster.yaml`. If something needs to change,
   change it in `cluster.yaml`.
4. **`install-dir` is ephemeral**: it contains credentials and
   certificates that expire after 24h. Never commit it.
5. **`cluster.yaml` is personal and gitignored.** It's generated locally
   by `ocplab init` and filled in with real values (domain, AWS profile,
   email) — never meant to be committed.

## The four traps that cost the most debugging time

Already fixed in the code — if something breaks, check here first:

1. **`kubernetes.io/cluster/<infraID> = owned` tag** on every AWS resource
   (`providers.tf` → `default_tags`). Without it `aws-cloud-controller-manager`
   won't start (`AWS cloud failed to find ClusterID`), won't remove the
   `node.cloudprovider.kubernetes.io/uninitialized` taint, and **nothing
   gets scheduled**: the cluster gets stuck at 7%. The `infraID` is read
   from `install-dir/metadata.json` and **changes every time** the
   Ignition configs are regenerated.

2. **etcd discovery via DNS**: the `etcd-0/1/2` A records and the
   `_etcd-server-ssl._tcp` SRV record are required. Without them the
   masters never form quorum.

3. **The private zone shadows the public one inside the VPC.** The
   private hosted zone must also contain `api`, `api-int`, `etcd-N`, and
   the SRV record — not just `*.apps`. Otherwise the pods stop resolving
   `api-int` and `cluster-version-operator` dies.

4. **Before `terraform destroy`**: the `ingresscontroller` has to be
   deleted and torn down first, with **real verification** (not a blind
   `sleep`) that the router ELB, its ENIs, and the `k8s-elb-*` security
   group actually disappear. Otherwise the destroy fails with
   `DependencyViolation` on the IGW and the public subnet.
   - **Deleting the IngressController does not remove it — the
     cluster-ingress-operator reconciles it straight back**, and creates a new
     Classic ELB with it. Found on 2026-08-03: deleted at 10:48:43, the ELB
     poll correctly saw zero at 10:48:48, and a replacement appeared at
     10:50:08, holding the IGW until terraform gave up 20 minutes later.
     Earlier teardowns had simply won the race against the masters being
     terminated. `delete_ingresscontroller.yml` therefore scales
     `cluster-version-operator` **and then** `ingress-operator` to zero before
     deleting anything — the CVO first, or it scales the ingress-operator back
     up. `wait_for_elb.yml` re-checks afterwards rather than trusting that
     "zero now" means "zero from here on".
   - The router ELB **is not** called `k8s-elb-*` (that's its security
     group): it has a hash-based name, so filter by `VPCId`, never by name.
   - **The ELB disappearing from the AWS API is not the same as its ENIs
     being released.** `DescribeLoadBalancers` stops listing a deleted ELB
     almost immediately, but AWS releases its `requester-id=amazon-elb`
     ENIs (and their public IPs) on a much looser background timeline —
     up to 35+ minutes. Waiting for **all** of the VPC's ENIs to reach
     zero doesn't work either, since the cluster's own instances are
     still up at that point in teardown. `teardown/tasks/wait_for_enis.yml`
     filters specifically on `requester-id: amazon-elb` with a 20-minute
     timeout. If `destroy` still fails on the orphaned `k8s-elb-*`
     security group after that, re-running `ocplab destroy` is safe — the
     role is idempotent and by then the ENIs are almost certainly free.
   - **Scaling the ingress-operator to zero also stops the ELB from deleting
     itself, so the poll that waits for it can only ever time out.** Measured
     on 2026-08-04: the poll ran all thirty attempts — **364s, half of a
     12m10s destroy** — and the manual-delete fallback then removed the ELB in
     26s. The operator is what deletes the router Service, and the Service is
     what the cloud-controller-manager watches; with it stopped there is
     nothing left to tear the ELB down on its own. The timeout is now 60s
     rather than 300s, which keeps the poll as a check without paying six
     minutes for an outcome that cannot happen. Deleting the router Service
     explicitly is the real fix and is in `BACKLOG.md`.
   - **Deleting the ELB explicitly releases its ENIs almost immediately.** The
     20-minute `ocplab_eni_teardown_timeout` exists for ENIs left to expire on
     their own (35+ minutes, measured); in the 2026-08-04 run, where the ELB
     was deleted by hand, the wait was satisfied in **4 seconds**.
   - **The `k8s-elb-*` security group is not held by an ENI, and waiting will
     never free it.** Measured on 2026-08-03: zero network interfaces were
     using it, and it still refused to delete after eleven minutes of retries.
     AWS answers `DependencyViolation: has a dependent object` for a security
     group that another group's **rules** reference — and the ingress-operator
     adds exactly such a rule to the node security groups so the router ELB can
     reach the nodes. That reference only clears when Terraform deletes the node
     groups, which happens *after* the cleanup task, so retrying there could
     never succeed. `cleanup_orphan_sgs.yml` revokes the referencing rules
     first; the retry that remains is a safety net, not the mechanism. If this
     is ever hit again, check for ENIs — the rule case is handled.

Extra: the `ControlPlaneMachineSet` always stays `Degraded` in UPI (no
`Machine` objects) and blocks `wait-for install-complete`. Handled with
`oc delete controlplanemachineset/cluster -n openshift-machine-api`.

## Other gotchas worth knowing

- **`ansible.builtin.lookup('file', ...)` strips the file's trailing
  newline.** Building PEM content (certs, keys) from a file lookup needs
  an explicit `+ "\n"`, or concatenating two PEM blocks (e.g. cert + key
  in a combined Secret) glues `-----END CERTIFICATE----------BEGIN...`
  into one unparseable line.
- **jmespath silently breaks when negation meets a trailing projection.**
  `[?!status.conditions].metadata.name` (meant to find CSRs with no
  `status.conditions`, i.e. unapproved) always returns `[]`, even though
  `!status.conditions` alone works fine — the projection right after the
  negated filter is the part that misbehaves. Use `[?status.conditions ==
  \`null\`]` instead (see `finalize/tasks/approve_csr_round.yml` and
  `power/tasks/csr_approval_round.yml`).
- **A YAML folded scalar (`>-`) only joins SAME-indentation lines with
  spaces.** A continuation line indented *deeper* than the block's base
  indentation (e.g. for visual alignment) is kept as a separate literal
  chunk with its own newline instead of being folded in — silently
  splitting one intended shell command into several. Keep every
  continuation line of a multi-line `shell:`/`command:` at the exact same
  indentation (see `cost/tasks/refresh_pricing.yml`).
- **`amazon.aws.iam_policy` doesn't degrade gracefully under forced
  `check_mode`** when the role/function it's attaching a policy to
  doesn't exist yet — it calls the underlying list API to decide what it
  would change, which raises a hard `NoSuchEntity`/`ResourceNotFound`
  instead of reporting "would create". Gate this kind of task on the
  parent resource's own `check_mode` probe result (`not
  <parent>.changed`) instead of letting it run unconditionally.
- **`aws ... --query ... --output text` prints the literal string `None`** when
  the query matches nothing — not an empty line. Anything that later does
  `float()` or a numeric comparison on that value needs to reject `"None"`
  explicitly, not just `""` (see the Spot price table in
  `cost/tasks/refresh_pricing.yml`).
- **`InstanceLifecycle` is absent on on-demand instances**, and only present
  (as `"spot"`) on Spot ones — so `ec2_instance_info` results need
  `selectattr('instance_lifecycle', 'defined')` before any comparison, and a
  `json_query` projection of it yields `null` rather than omitting the key.
- **The AWS Pricing API (Price List Query API) only has an endpoint in
  `us-east-1`/`ap-south-1`**, regardless of which region is being priced
  — the target region is a `location` FILTER value (a human-readable
  name like `"EU (Ireland)"`, not the region code), not the `--region`
  flag. It also auto-paginates through an entire result set by default;
  use `--no-paginate --max-results N` when only the first match matters.
- **External CLI calls need their own bounded timeout.** Neither
  `kubernetes.core.k8s_info` nor the `aws` CLI reliably fail fast when
  the network hangs (an unreachable API server, a stalled Pricing API
  call) — wrap with `ansible.builtin.wait_for` (TCP-level) or a plain
  `timeout N` shell prefix rather than relying on the client's own
  default, which can be far longer than reasonable or effectively unbounded.
- **`kube-apiserver` rolls a real revision across all masters** in
  response to certain `APIServer`/config changes, and the API can be
  transiently flaky (connection refused) for a few seconds right after
  masters restart — wrap `oc`/API calls made in that window with
  `until`/`retries`, not a single unretried attempt.
- **`group_vars` must live inside `inventory/`, not as a loose sibling.**
  Ansible only auto-loads `group_vars/`/`host_vars/` next to the
  inventory file or next to the playbook being run — anywhere else fails
  *silently* (vars come back `undefined`, no error).
- **The `SSH from my IP` rules in `security-groups.tf` do nothing on their
  own.** Masters and workers sit in the private subnet with no public IP, so
  there is no route in from the internet however open the security group is.
  Access goes through the EC2 Instance Connect Endpoint
  (`terraform/instance-connect.tf`), whose traffic the nodes already accept
  under their existing "all internal VPC traffic" rule — which is why adding
  `ocplab ssh` needed no security-group change at all. Don't "fix" the
  my-IP rules by widening them; they are not the mechanism.
- Master nodes in this topology are schedulable and carry
  `node-role.kubernetes.io/worker` in addition to `master` — a
  worker-only query by label will also match masters. Target nodes by
  exact name (from the EC2 instance's own `private_dns_name`, which is
  also what OpenShift names the node) instead.

## The OpenShift binaries

`openshift.version` in `cluster.yaml` is **optional**, and that is deliberate:
declaring it opts into managed binaries, omitting it keeps the PATH behaviour
the project had before. Making it required would force every existing
`cluster.yaml` to be migrated — a major version bump for no benefit.

- **Never invoke `openshift-install` or `oc` by bare name in a role.** Use
  `{{ ocplab_openshift_install_bin }}` and `{{ ocplab_oc_bin }}`, which
  `render` sets to either an absolute path under `~/.ocplab/bin/<version>/`
  or the bare name. That one indirection is what makes pinned and unpinned
  the same code path everywhere.
- **ocplab resolves binaries by absolute versioned path, never through the
  symlinks.** The `.venv/bin/{oc,kubectl,openshift-install}` links exist only
  so a human typing `oc get co` gets the matching client; if ocplab used them,
  changing the version mid-operation would swap the binary under a running
  deploy.
- **`sync_managed_binaries()` is the single owner of those links**, and it is
  called from `render`, `status` and `versions` — not from `render` alone.
  That was the first attempt, on the reasoning that render precedes every
  command; it doesn't. `status` is local and never renders, and `versions`
  skips rendering on purpose (rendering needs the very binary it may be
  downloading). Those two are exactly what you run right after pinning a
  version, so the links stayed stale while `status` cheerfully reported the
  new version as active. Any new command that reads the config and may touch
  disk should call it too.
- **A shell that already ran `oc` keeps calling the old path** even after the
  symlink is created — bash caches command locations per session, and `which`
  doesn't consult that cache, so `which oc` shows the new path while `oc`
  runs the old binary. It looks exactly like a failed download and isn't one.
  `hash -r` clears it. Worth suggesting before debugging anything else when a
  reported client version doesn't match the pinned one.
- **Only exact `x.y.z` is accepted, never a channel.** Since every command
  re-renders, a channel like `stable-4.22` would be re-resolved on each
  invocation and could move under a live cluster. `ocplab versions list` is
  how you find the concrete version to write.
- The RHCOS AMI is discovered from the installer binary itself, so pinning the
  version pins the node image too — one field controls both.

## Which cluster `oc` points at

Pinning the version solved half of "typing `oc` by hand does the right thing".
The other half — *which cluster* — is the dangerous one: the failure isn't
"not found", it's reaching a **different** cluster silently.

`ocplab setup` appends a marked block to `.venv/bin/activate` that exports
`KUBECONFIG`, and `ocplab env` prints the same line for `eval` in shells the
hook can't reach. Four things about it are load-bearing:

- **The hook and a wrapper script have identical coverage**, which is what
  decided the design. `.venv/bin/oc` is only on `PATH` when the venv is
  active — the very condition that fires the hook. With equal reach, the hook
  wins because `echo $KUBECONFIG` then tells the truth; a wrapper would leave
  the standard diagnostic answering "unset" while `oc` talked to the lab,
  reintroducing the invisibility this exists to remove.
- **`_OCPLAB_KUBECONFIG` is what makes re-sourcing `activate` safe**, and it
  is not decoration. The second pass redefines `deactivate` from scratch,
  destroying the wrapper, and would then skip the block because `KUBECONFIG`
  is already set — leaving it exported with nothing left to undo it. "We set
  it" has to count as "not set".
- **The restore wraps the venv's own `deactivate`** by renaming it with
  `typeset -f | sed` on the *first line only*, which preserves each shell's
  brace layout (bash puts `{` on line 2, zsh on line 1). That part is bash/zsh
  only and deliberately optional — the guard degrades to "KUBECONFIG outlives
  the deactivate", never to a broken activation. The `export` itself cannot
  fail, which is the point.
- **It exports the path even when `install-dir` doesn't exist.** Gating on
  existence would silently fall back to `~/.kube/config` for any shell
  activated before the first deploy — exactly the bug. `oc` failing with
  `localhost:8080 refused` is the safe direction, and `docs/troubleshooting.md`
  names it.
- **The tested-versions table in `README.md` is a factual record, and it is a factual record,
  rather than a compatibility claim.** A row only goes in after a real end-to-end run
  against AWS: deploy → verify with every node Ready and every ClusterOperator
  healthy → ssh → destroy leaving nothing behind. Not "the binary downloaded",
  not "terraform applied". After any deploy on a version that isn't listed,
  update it — including the failures, which are the part people actually need.

## Cluster capabilities

`openshift.capabilities` in `cluster.yaml` maps straight onto the installer's
own `capabilities` block. Anything not enabled is never deployed, so its images
are never pulled — which is the point, given that image pulls across the NAT
gateway are the biggest line on the bill.

**Three are mandatory on AWS, and this is the installer's rule, not a
preference.** Asked for `baselineCapabilitySet: None` with nothing else, 4.22.7
answers:

```
disabling CloudCredential capability available only for baremetal platforms
disabling CloudControllerManager is only supported on the Baremetal, None, or External platform
the Ingress capability is required
```

So `CloudCredential`, `CloudControllerManager` and `Ingress` always stay.
`validate` checks for them, purely so the failure lands at `ocplab validate`
rather than several commands later when `ignition` finally runs the installer.

`openshift-install explain installconfig.capabilities` lists the accepted
baseline values, and the capability names come from `ClusterVersionCapability`
in `openshift/api`. Omit the section entirely and the installer's default
(`vCurrent`) applies, which is what every deploy did before this existed.

**Measured on 2026-08-03**: the set in `examples/minimal.yaml` took a deploy
from a ten-deploy baseline of 35.2 GB down to **24.8 GB — 29% less**, with the
cluster healthy at 27 ClusterOperators instead of 34.

The measurement did not come from the bill. NAT gateways publish
`BytesInFromDestination` to CloudWatch, those metrics outlive the gateway, and
one datapoint per deploy gives an exact per-deploy figure instead of a monthly
total — which is also how the 35.2 GB baseline was reconstructed from ten
already-destroyed NAT gateways. Cost Explorer has a 24-hour lag and nets
credits against usage; CloudWatch does neither.

## Spot instances

`compute.spot` and `bootstrap.spot` in `cluster.yaml` put those nodes on Spot.
Three decisions here are load-bearing and were made against the documentation,
not by preference:

- **`controlPlane.spot` is rejected by `validate`, on purpose.** Red Hat's
  `cpmso-limitations.adoc` says a cluster without preexisting control-plane
  `Machine` objects "cannot use a control plane machine set or enable the use
  of a control plane machine set after installation", and that the operator is
  "not supported on clusters with manually provisioned machines". UPI has
  neither, so a reclaimed master is never replaced automatically and recovering
  one means repairing etcd membership by hand. Workers are different: losing
  one reschedules pods, and `terraform apply` recreates it.
- **One-time requests, `terminate` behaviour.** AWS supports Spot requests that
  *stop* instead of terminating (preserving the disk), but only for
  `persistent` requests — and a persistent request outlives the instance it
  launched and can relaunch it, potentially into a VPC being destroyed. Given
  trap #4's history, a predictable teardown wins.
- **Spot and `power off` are mutually exclusive**, and `power off` refuses up
  front rather than discovering it after cordoning and draining: a one-time
  Spot instance can only be terminated, never stopped.

Real prices, measured in `eu-west-1a` on 2026-08-03: Spot ran ~50-55% below
on-demand, **not** the 70-90% often quoted. With masters staying on-demand, a
minimal profile lands near $0.83/h against $1.06/h.

**`cost` prices Spot from `describe-spot-price-history`, and deliberately does
not cache it.** Everything else in that role is cached for a week under
`~/.ocplab/pricing-cache.json`, which is right for on-demand rates that change
a few times a year and wrong for Spot rates that move hourly and per-AZ — a
cached Spot price would be as misleading as the on-demand one it replaced, just
less obviously. When the lookup fails the instance falls back to on-demand and
the report names the type, so the total over-states rather than quietly
promising a discount nobody checked.

**There are two `cluster.yaml` templates and they must be kept in step**:
`examples/standard.yaml` and `examples/minimal.yaml`, which `ocplab init` and
`ocplab init --minimal` write verbatim. `validate` catches a *missing required*
field in either, but nothing catches a new *optional* field that only got added
to one — so when the schema grows, update both by hand.

Both are files rather than strings in `ocplab`: a config template is data, the
whole repository is needed to run anything anyway, and in a public repo the
config format is one of the first things people read. Note that `.gitignore`
anchors `/cluster.yaml` to the root for this reason — an unanchored pattern
matches at any depth and would silently exclude a shipped
`examples/cluster.yaml`, which is why the files are named `standard`/`minimal`.

## `ocplab repair`

The only command that runs `terraform apply` against a **running** cluster, so
its safety model is the feature, not a detail around it:

- **It plans first and inspects the plan.** A repair is add-only: if anything
  would be changed, replaced or destroyed, it refuses and shows what Terraform
  wanted to do. That isn't hypothetical — `render` auto-discovers the RHCOS AMI,
  so a changed `openshift.version` makes Terraform want to replace all three
  masters. An unguarded `apply` there destroys the cluster it was asked to fix.
- **It refuses to create masters**, even though creating is not destructive. An
  instance is not an etcd member: a replacement master arrives with a new
  private IP, needs the `etcd-N` records repointed and the dead member removed
  by hand. Pretending to handle that is worse than declining.
- **It never runs the `ignition` role.** Regenerating `install-dir` mints a new
  `infraID`, which is in every resource's `default_tags` — re-tagging a live
  cluster is trap #1 arriving through the back door.
- **`Node` objects are pruned only when the instance is really gone**, checked
  against live EC2 rather than assumed from the node's status. A node that is
  merely unhealthy keeps its instance, and deleting its `Node` object would be
  destructive rather than tidy.

It is not Spot-specific, though Spot is the usual reason a worker vanishes.

## The web UI (`ocplab web`)

A second thin UX layer, held to the same rule as the first: **no business
logic**. `web/server.py` never calls AWS and never reads Terraform state — it
runs `ocplab <command>` as a subprocess and streams the output. Anything about
*what* a command does still belongs in the Ansible role.

- **The browser cannot supply an argv, and that is structural rather than
  validated.** It posts a command *id*; the server looks that id up in
  `COMMANDS` and runs the fixed argv stored there. There is no path from user
  input to a command line, so there is nothing to sanitise and nothing to
  inject into. New operations are new entries in that list, never a new way to
  pass arguments.
- **Three security controls, none of them optional flags.** It binds
  `127.0.0.1` with no setting to change it; it rejects any request whose `Host`
  header isn't loopback; and every API call needs a token minted at startup.
  The Host check is the non-obvious one: without it, a page you visit can point
  a DNS name at `127.0.0.1` and drive this server from your browser. A local
  server that can run `terraform destroy` is worth rebinding for. The token
  travels in a header, except on the event stream where it has to be in the
  query string — `EventSource` cannot set headers.
- **Every command runs with `--yes`.** The browser collects the confirmation
  first; an `input()` prompt in a subprocess nobody can type into would hang
  for ever.
- **Jobs are serialised, one at a time.** Not a simplification — there is one
  cluster and one Terraform state, and two concurrent `apply` runs corrupt it.
- **Output lines are kept server-side, not just forwarded.** Reloading the tab
  mid-deploy, or opening it forty minutes in, replays the whole run and then
  follows live. A stream that attaches to an *already finished* job must send
  its own `done` event: `finish()` delivered the sentinel to whoever was
  subscribed at the time, so a late subscriber would otherwise replay the
  history and block for ever, holding a thread. That is exactly what the
  history tab's "View output" does.
- **`ocplab ssh` is deliberately absent and stays absent.** It needs a TTY, and
  a web terminal would also hand whoever reached this server a root shell on
  the nodes — the thing the loopback binding exists to prevent. `env`, `init`
  and `setup` are excluded for duller reasons, all listed in `EXCLUDED` so the
  UI can explain itself rather than just omitting them.
- **`/api/validate` is the one place a path reaches an ocplab argv**, and the
  server writes that path itself into a temp directory. It exists so pasted
  YAML can be checked *before* saving — otherwise "see what's wrong with this
  config" would mean overwriting the working one to find out.
- **The dashboard reads `ocplab status --json`, not parsed text.** `status` was
  refactored into `collect_status()` plus two renderers for this. A dashboard
  built on scraping human prose breaks the first time someone improves a
  sentence, and this project rewrites its own sentences often. Everything in
  that payload is local and instant, which is what makes polling it safe —
  live state (`verify`, `cost`, `power status`) stays behind explicit buttons,
  because those cost an AWS round trip and two of them cost money.
- **`[hidden] { display: none !important; }` in `style.css` is load-bearing.**
  A class that sets `display` beats the browser's own `[hidden]` rule on
  specificity, so `.panel-out`/`.runchip` marked `hidden` stayed on screen.
  That single line was the cause of four separate reported bugs: the output
  panel covering the page on every tab, Actions/Configuration/Runs being
  impossible to scroll, a "Running" chip that never cleared, and its Open
  button doing nothing (the job id it reads is only set while something runs).
- **The output panel reserves space, it does not overlay.** Opening it sets
  `--pad-bottom`/`--pad-right` on `<body>` to its own size, so the page gives
  up real estate instead of being covered. Overlaying is what made three views
  unscrollable. It is dockable bottom/right and drag-resizable, clamped so it
  can never take the whole viewport, and the preference lives in
  `localStorage`.
- **Sizes in the UI use binary units, like `human_size()` in the CLI.**
  Decimal units put "1.7 GB" on the dashboard beside "1.6 GB" from `ocplab
  status` for the same bytes, which reads as a bug in one of them.
- **Fonts are bundled, not fetched** (`web/static/fonts/`, ~176 KB, SIL OFL —
  attribution in that directory's README, and it is a licence condition, not
  politeness). The UI must look the same everywhere and work with no internet,
  which is plausibly the situation when the cluster you are fixing *is* the
  problem. `_serve_static` therefore checks path *containment* rather than
  parent equality — the earlier check silently 404'd anything in a
  subdirectory.
- **`web start` daemonises; it does not hold the terminal.** The child is
  spawned as `python web/server.py` in its own session (`start_new_session`),
  so closing the shell or Ctrl-C never reaches it. Two details are
  load-bearing: the token goes through the **environment, not argv**, because
  `/proc/<pid>/cmdline` is world-readable while `/proc/<pid>/environ` is
  owner-only; and `start` polls the port until the server actually accepts a
  connection before reporting success, so a server that dies on startup can't
  leave a cheerful URL pointing at nothing.
- **A recorded PID is never trusted on its own.** `.ocplab-web.json` outlives
  reboots and `kill -9`, and PIDs get recycled, so `web_process_is_ours()`
  requires the process to be alive *and* to have `web/server.py` in its
  command line. Without the second half, `stop` could signal whatever
  unrelated process inherited the id.
- **`web start` runs a preflight and starts nothing if it fails**, accumulating
  every problem like `validate` and `preflight` do. It requires `cluster.yaml`
  to *exist* but deliberately **not** to be valid — the Configuration editor is
  how a broken one gets fixed, so refusing to start over a config error would
  lock the user out of the tool that repairs it.
- **Nothing opens a browser.** `webbrowser.open` shells out to `gio` under WSL
  and fails noisily (`Operation not supported`) while the server is fine. For a
  background service, printing the URL — and reprinting it from `status` — is
  both simpler and more honest.
- **The output panel can tail log files, not just job streams.** During a
  deploy the playbook line reads "terraform apply, 5m47s" while everything
  interesting is in `logs/*_terraform-apply.log` and
  `install-dir/.openshift_install.log` — which the roles answer by telling you
  to open another terminal, exactly the errand a UI should remove. `/api/logs`
  enumerates them and `/api/logs/stream` tails one. The browser sends a *name*
  matched against a freshly built list, never a path: same rule as `COMMANDS`,
  so there is nothing to sanitise. Only the last 256 KB is sent on attach — a
  terraform log runs to megabytes.
- **The dashboard poll is unconditional, and that matters.** It used to skip
  whenever a stream was open, which meant watching a log froze the status
  cards and the running chip for the whole deploy. The auto-attach to a new
  running job is what gets suppressed instead, and only while a log is on
  screen: yanking the panel away from a log someone chose to watch is worse
  than not reattaching.
- **Run history survives a restart; run *output* is not duplicated to achieve
  it.** `logs/runs.json` holds metadata only, because the output already lives
  in one file per run under `logs/` and a second copy would be a second thing
  to keep in step. A job from an earlier server is flagged `historical` and the
  UI tails its `log_file` instead of a stream that no longer exists. That
  filename comes from parsing the CLI's own `Log: <path>` line — the one place
  anything here reads structure out of command output, and it earns the
  exception: guessing the newest file races, and recomputing the name would
  mean reimplementing `log_path_for()`, which belongs to the CLI.
- **Job ids are millisecond-stamped, not a counter.** A counter restarts with
  the process and collides with the history the previous one wrote.
- **Every list that can grow lives in a `.scrollbox`.** Letting Recent runs
  grow pushed the page down and made the Overview itself scroll, which an
  overview must never do.
- **Action rows carry a `tone`, and it is a warning system rather than
  decoration**: red destroys, amber changes something you may not get back,
  green restores. It lives in `COMMANDS` beside `group` so there is one place
  to look. Descriptions are collapsed behind a chevron, and the chevron is a
  *separate* control from the row that runs the command — one button that both
  explains and fires is a button people hesitate over.
- **Use `Array.from()` on `select.options`, not spread.** Both work in a
  browser, but `Array.from` also accepts plain array-likes, which is what the
  headless render tests hand it.
- **A 401 makes the page reload itself, once.** Every `start` mints a fresh
  token, which silently invalidates open tabs — the calls 401 and the page
  looks dead for no visible reason. `/` is served *without* authentication and
  the token is stamped into the HTML on the way out, so a reload is a complete
  fix. A `sessionStorage` flag stops a genuinely bad token from looping, and
  any successful call clears it so the next restart can heal too. Do not
  "solve" this by making the token survive restarts: it dying with the server
  is the property that makes a leaked URL worthless.
- **A client hanging up mid-response is not an error.** The reload above tears
  down its own request, so `BrokenPipeError` is routine here. It is swallowed
  in `_send` and in `Server.handle_error`; everything else still gets a full
  trace. This matters because "does the log contain a traceback" is the
  cheapest health check this server has, and routine noise there blinds it.
- **`--tint` lives on `:root`, and drives more than the logo.** Both marks, the
  header tabs, the Configuration controls and the About links read it, so the
  chrome lights up as one. `TINTS` holds variable *names* rather than
  `var(...)` wrappers because the favicon needs a literal colour — it is a file
  on disk and cannot follow a CSS variable, so it is redrawn as a data URI from
  the resolved value.
- **Layout rules that were each a reported bug**: Actions and Help are fixed
  four-column grids (auto-fit sized off a minimum and gave three, dropping
  Teardown to a second row); group headers stack title over blurb with a
  min-height, or the lists start at four different heights; `Recent runs` caps
  at 11.6rem, about three rows, and must **not** carry `flex: 1` — that let it
  grow to fill the block and quietly defeated the cap, stretching its
  neighbour to match.
- **Stdlib only, no build step.** No new entry in `requirements.txt`, no Node,
  no `node_modules`. The frontend has to stay as readable on GitHub as the
  rest of the repository.

## Output and logging

`ansible/callback_plugins/ocplab_output.py` is the `stdout_callback` for
every playbook run. Two things about it are load-bearing:

- **The roles' `debug` and `assert` messages are the user-facing product**,
  not scaffolding — `verify`'s problem list, `cost`'s report, `bootstrap`'s
  nameserver table. The callback promotes them and collapses everything
  else. When adding a task whose output the user is meant to read, use
  `debug`/`assert` rather than inventing another channel.
- **The callback owns both streams**: the terminal rendering *and* the
  plain-text log at `OCPLAB_LOG_FILE` (set by `run_ansible_playbook`, one
  file per run under `logs/`). It deliberately does **not** use Ansible's
  `ANSIBLE_LOG_PATH`, which logs whatever passes through `Display` and would
  capture the terminal formatting, including the in-place line rewrites.
  The one exception is `-v`/`-vv`: that falls back to the stock `default`
  callback, so `ANSIBLE_LOG_PATH` is used for those runs instead — either
  way exactly one writer, never two.

Log writes are best-effort and swallow their own errors: losing the log must
never take down a `deploy` that is otherwise fine.

**The terminal is best-effort too, and that is the more important half.** The
log is written *before* stdout in `_emit`, and every write to stdout is
wrapped. Found the hard way on 2026-08-03: a deploy was left running and its
terminal window closed; the first task to finish afterwards raised writing to a
stdout with no terminal behind it and killed the whole playbook — silently,
mid-install, with the bootstrap still up and billing and four CSRs unapproved.
Closing a window must cost the live view and nothing else.

## Conventions

- Documentation and comments **in English**.
- Comments explain the *why*, not the *what* — identifiers already say
  what; a comment earns its place by capturing a non-obvious constraint.
- Prefer Ansible modules over `command:`/`shell:`; fall back to the `aws`
  CLI only for services with no module (e.g. Budgets, EventBridge
  Scheduler, Pricing) — idempotency is the point of using Ansible here.
- Accumulate problems and report them together at the end
  (`preflight`/`validate`/`verify`) rather than stopping at the first one.
- When adding a resource to Terraform, make sure it inherits the
  `default_tags`.
- **Versioning**: [Semantic Versioning](https://semver.org/). The single
  source of truth is the `VERSION` constant near the top of `ocplab`
  (exposed via `ocplab --version`) — bump it, add an entry to
  `CHANGELOG.md` (format: [Keep a Changelog](https://keepachangelog.com/)),
  and tag the commit `vX.Y.Z` together, as one unit. Patch = fixes with
  no behavior change; minor = new features/commands, backward compatible;
  major = breaking changes (e.g. a `cluster.yaml` schema change requiring
  manual migration, or removing/renaming a CLI command).
- **Planned work lives in `BACKLOG.md`**, not in this file — ideas,
  features, and fixes not yet built, each with the why and open questions
  captured so context isn't lost before someone picks it up. When
  something from it actually gets implemented, it moves into
  `CHANGELOG.md` under `[Unreleased]` and gets removed from the backlog.
