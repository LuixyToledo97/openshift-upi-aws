# The `ocplab` CLI and `cluster.yaml`

> Part of the [ocplab](../README.md) documentation.

## The `ocplab` CLI and `cluster.yaml`

`ocplab` has no business logic of its own: it validates `cluster.yaml`,
translates it into `terraform.tfvars` and Ansible variables, and invokes
`ansible-playbook`. All the actual logic — idempotent creation, polling,
verification — lives in the Ansible roles listed above.

### ⌨️ Commands

| Command | What it does | Costs money? |
|---|---|---|
| `ocplab prereqs` | Prints the manual AWS/DNS/Red Hat prerequisites checklist (no `cluster.yaml` needed) | No |
| `ocplab init` | Writes a `cluster.yaml` template | No |
| `ocplab setup` | Creates the venv, installs Ansible + deps | No |
| `ocplab validate` | Checks `cluster.yaml` for errors, no side effects | No |
| `ocplab render` | `cluster.yaml` → `terraform.tfvars` + Ansible vars | No |
| `ocplab bootstrap [apply\|status\|destroy]` | Manages the IAM user, public hosted zone, SSH key (default action: `apply`) | No |
| `ocplab preflight` | Read-only checks: binaries, credentials, DNS, AMI | No |
| `ocplab ignition` | Generates/regenerates `install-dir` and the Ignition configs | No |
| `ocplab deploy` | render + ignition + `terraform apply` + wait for boot + finalize | **Yes** |
| `ocplab status` | Local summary (`install-dir` age, Terraform resource count) | No |
| `ocplab verify` | Live cluster health: `ClusterVersion`, node readiness, `ClusterOperators` | No |
| `ocplab cost` | Approximate current USD/hour cost of what's actually deployed | No |
| `ocplab console` | Prints the console URL and the `kubeadmin` password | No |
| `ocplab env` | Prints `export KUBECONFIG=...` for this cluster, to `eval` | No |
| `ocplab web start\|stop\|status` | Runs the browser UI on `127.0.0.1`, in the background | No (but it can run everything that does) |
| `ocplab ssh [node]` | Lists the running nodes, or opens a shell on one | No |
| `ocplab repair` | Recreates a worker that disappeared, on a running cluster | No |
| `ocplab versions list\|download\|rm` | Manages the cached OpenShift binaries | No |
| `ocplab destroy` | Ordered teardown of the whole cluster | No (stops billing) |
| `ocplab power on\|off\|status` | Gracefully power the cluster off/on, or check which it currently is | No (still bills EBS while off) |
| `ocplab safety-net apply\|status\|destroy` | Manage the budget/killswitch, outside Terraform | No |

Global flags, valid before or after the subcommand: `-f/--config` (path to
`cluster.yaml`, default `./cluster.yaml`), `--dry-run` (maps to Ansible's
`--check --diff` — preview without changing anything in AWS or in the
cluster), `-v`/`-vv` (verbosity), `--yes` (skip the interactive
confirmation), `--tags`. `ocplab --version` prints the current version (see
[CHANGELOG.md](../CHANGELOG.md)).

One deliberate exception to "changes nothing": under `--dry-run`, commands
still refresh the two generated files from `cluster.yaml` before running.
They're gitignored, deterministic, and rebuilt in under a second — and
without that, a dry-run would inspect whatever the previous render left
behind and answer confidently about a config you no longer have. The
`render` command itself is the exception to the exception: there the
generated files *are* the subject of the command, so `ocplab render
--dry-run -v` shows the diff and writes nothing.

### 🎯 Which cluster `oc` talks to

Pinning `openshift.version` makes `oc` the *right version*. It says nothing
about *which cluster* it points at — that's whatever `~/.kube/config` has as
its current context, quite possibly a leftover Docker Desktop or minikube. And
that is the more dangerous half: the failure isn't "cluster not found", it's
reaching a **different** cluster in silence. An `oc delete` in that state goes
somewhere you didn't intend.

So activating the venv also points `KUBECONFIG` at this cluster:

```bash
source .venv/bin/activate
oc config current-context     # admin  — this cluster
deactivate
oc config current-context     # docker-desktop  — back to whatever you had
```

`ocplab setup` adds a clearly marked block to `.venv/bin/activate` to do this,
and `deactivate` undoes it. Two rules it follows:

- **It never overrides a `KUBECONFIG` you set yourself.** An explicit value is
  yours; the hook only fills in a blank.
- **It exports the path even before the cluster exists.** `install-dir` isn't
  there until the first deploy, and pointing at a file that isn't there makes
  `oc` fail loudly — much better than quietly falling back to the very context
  this exists to avoid.

Deleting that block opts out permanently. Restoring on `deactivate` needs the
venv's own shell function rewritten, so that part is bash/zsh only; in another
shell `KUBECONFIG` simply outlives the `deactivate`, and nothing else changes.

For a shell where you never activated the venv — a script, or a terminal you
opened for one command — there's `ocplab env`:

```bash
eval "$(ocplab env)"
```

`ocplab status` reports where your current shell points, next to which version
it's running, and `ocplab console` says so too if you're not on this cluster.

> If you created the venv before this existed, run `ocplab setup` once to add
> the block. It won't rebuild anything.

### 📝 `cluster.yaml`

```yaml
apiVersion: ocplab/v1
kind: ClusterConfig

metadata:
  name: ocp4lab
  baseDomain: aws.example.com

platform:
  aws:
    region: eu-west-1
    availabilityZone: eu-west-1a
    profile: openshift-lab
    publicHostedZoneId: Z0123456789ABCDEFGHIJ

networking:
  vpcCidr: 10.0.0.0/16
  publicSubnetCidr: 10.0.1.0/24
  privateSubnetCidr: 10.0.2.0/24
  clusterNetwork: 10.128.0.0/14
  serviceNetwork: 172.30.0.0/16

controlPlane:
  replicas: 3          # must be odd — etcd quorum
  instanceType: m5.xlarge
  volumeSize: 120
  volumeType: gp3

compute:
  replicas: 2
  instanceType: m5.large
  volumeSize: 120
  volumeType: gp3

bootstrap:
  instanceType: m5.xlarge
  volumeSize: 120
  volumeType: gp3

openshift:
  # optional — 'ocplab render' auto-discovers it for platform.aws.region
  # if omitted; set it explicitly only to pin a specific AMI (see §12)
  # rhcosAmi: ami-0123456789abcdef0

credentials:
  pullSecretFile: ~/.ocplab/pull-secret.json    # never committed
  sshPublicKeyFile: ~/.ssh/id_ed25519.pub

certificates:
  # optional — bring your own REAL, CA-signed certs for the API and
  # *.apps (see §7.1). Not for self-signed certs.
  # apiCertFile: ~/.ocplab/certs/ocp4lab-api.crt
  # apiKeyFile: ~/.ocplab/certs/ocp4lab-api.key
  # appsCertFile: ~/.ocplab/certs/ocp4lab-apps.crt
  # appsKeyFile: ~/.ocplab/certs/ocp4lab-apps.key

safetyNet:
  enabled: true
  budgetUsd: 50
  alertEmails:
    - you@example.com
  killswitch:
    enabled: true
    schedule: "0 22 * * ? *"       # 22:00 local time, every day
    timezone: Europe/Madrid
```

`ocplab validate` checks all of this: valid CIDRs that don't overlap and
sit inside the VPC, an odd `controlPlane.replicas`, that the credential
files actually exist and look right, valid emails, and more — with a
concrete error message per problem, not just the first one it finds.

---
