# 🎩 OpenShift 4.22 UPI on AWS with Terraform + Ansible

OpenShift Container Platform lab on AWS using **UPI** (*User-Provisioned
Infrastructure*): Terraform defines and manages all the infrastructure,
and the OpenShift installer only generates the configuration files
(Ignition) and monitors the boot process. Orchestrated end to end by
**`ocplab`**, a small Python CLI that drives a set of idempotent Ansible
roles, which in turn drive Terraform and the OpenShift installer.
`cluster.yaml` is the single declarative source of truth — everything
else (`terraform.tfvars`, Ansible variables, `install-config.yaml`) is
generated from it.

This is not ROSA (managed by Red Hat) nor IPI (the installer creates the
infrastructure). Here we control every AWS resource ourselves.

- **Cluster**: `ocp4lab`
- **Base domain**: `aws.example.com`
- **Region**: `eu-west-1` (Ireland), a single AZ (`eu-west-1a`)
- **Topology**: 1 bootstrap (temporary) + 3 masters + 2 workers
- **Cost**: ~**$1.06/hour** with everything up

> ⚠️ This lab **costs money while it's running**. The normal flow is
> create → test → destroy the same day. `ocplab deploy` and
> `ocplab destroy` always ask for confirmation before touching anything
> billable.

---

## Table of contents

1. [🏗️ Architecture](#1-architecture)
2. [✅ Prerequisites](#2-prerequisites)
3. [📁 Repository structure](#3-repository-structure)
4. [⚙️ The `ocplab` CLI and `cluster.yaml`](#4-the-ocplab-cli-and-clusteryaml)
5. [🧱 The Terraform files, explained](#5-the-terraform-files-explained)
6. [🚀 Deploying the cluster](#6-deploying-the-cluster)
7. [💣 Destroying the cluster](#7-destroying-the-cluster)
8. [💰 Costs](#8-costs)
9. [🛡️ Cost safety net](#9-cost-safety-net)
10. [🔧 Troubleshooting and lessons learned](#10-troubleshooting-and-lessons-learned)
11. [⚡ Quick reference](#11-quick-reference)
12. [📚 References](#12-references)

---

## 1. Architecture

### 🗺️ Topology

```
                          Internet
                              │
              ┌───────────────┼───────────────┐
              │               │               │
        ┌─────▼─────┐   ┌─────▼─────┐   ┌─────▼──────┐
        │ NLB api   │   │   IGW     │   │ ELB router │
        │ (external)│   │           │   │  (*.apps)  │
        │   :6443   │   │           │   │  :80 :443  │
        └─────┬─────┘   └─────┬─────┘   └─────┬──────┘
              │               │               │
    ══════════╪═══════════════╪═══════════════╪══════════  VPC 10.0.0.0/16
              │               │               │
        ┌─────┴───────────────┴───────────────┴────────┐
        │  Public subnet  10.0.1.0/24  (eu-west-1a)    │
        │   · NAT Gateway + EIP                        │
        │   · bootstrap (temporary, gets destroyed)     │
        └──────────────────────┬───────────────────────┘
                               │ outbound via NAT
        ┌──────────────────────┴───────────────────────┐
        │  Private subnet  10.0.2.0/24  (eu-west-1a)   │
        │   · master-0/1/2   m5.xlarge                 │
        │   · worker-0/1     m5.large                  │
        │   · NLB api-int   :6443  :22623               │
        └──────────────────────────────────────────────┘
```

### 🧩 Components

| Component | Qty | Type | Location | Created by |
|---|---:|---|---|---|
| Bootstrap (temporary) | 1 | `m5.xlarge` | public subnet | Terraform |
| Control plane (masters) | 3 | `m5.xlarge` | private subnet | Terraform |
| Compute (workers) | 2 | `m5.large` | private subnet | Terraform |
| NAT Gateway + EIP | 1 | — | public subnet | Terraform |
| External API NLB | 1 | Network LB | public subnet | Terraform |
| Internal API NLB | 1 | Network LB | private subnet | Terraform |
| **Router ELB (`*.apps`)** | 1 | Classic LB | public subnet | **the cluster** ⚠️ |
| **Security group `k8s-elb-*`** | 1 | — | VPC | **the cluster** ⚠️ |
| **`*.apps` records** | 2 | Route53 A | both zones | **the cluster** ⚠️ |

⚠️ The last three are created by the `ingress-operator` **outside of
Terraform**. They're why a raw `terraform destroy` fails if they aren't
cleaned up first — `ocplab destroy` handles this automatically (see
[section 7](#7-destroying-the-cluster)).

> **Design decision:** a single AZ and a single NAT Gateway to minimize
> cost. There's no real high availability here — this is a lab, not
> production.

### 🔄 Installation flow

```
1. openshift-install generates bootstrap.ign / master.ign / worker.ign
2. Terraform uploads bootstrap.ign to S3 and creates all the infrastructure
3. The bootstrap boots a TEMPORARY control plane (bootkube)
4. The masters boot and pull their config from the Machine Config Server (:22623)
5. aws-cloud-controller-manager identifies the nodes via the
   kubernetes.io/cluster/<infraID> tag and removes the "uninitialized" taint  ← CRITICAL
6. OVN-Kubernetes gets deployed → the nodes become Ready
7. etcd forms quorum (discovery via DNS SRV records)                          ← CRITICAL
8. The final control plane takes over → the bootstrap is no longer needed
9. The workers request to join → their CSRs must be approved (automated by `ocplab`)
10. The cluster-version-operator applies the remaining ~1015 manifests
```

`ocplab deploy` automates steps 1 through 10 end to end — see
[section 6](#6-deploying-the-cluster) for what it does at each stage and
how long each one takes.

---

## 2. Prerequisites

> Everything in this section is also available as a standalone terminal
> checklist, independent of this file and without needing `cluster.yaml`
> to exist yet: `./ocplab prereqs`.

### 2.1 💻 Local machine (Linux / macOS / WSL2)

If you're on Windows, first WSL2 (PowerShell as administrator) — everything
below then runs inside the Ubuntu WSL2 shell, same as native Linux:

```powershell
wsl --install -d Ubuntu-24.04
```

Base packages — pick the line for your distro (macOS needs
[Homebrew](https://brew.sh) installed first):

```bash
# Debian / Ubuntu / WSL2
sudo apt update && sudo apt upgrade -y
sudo apt install -y curl unzip jq git gnupg software-properties-common

# Fedora / RHEL / CentOS Stream (dnf; use 'yum' in place of 'dnf' on RHEL/CentOS 7)
sudo dnf install -y curl unzip jq git gnupg2

# macOS
brew install curl jq git gnupg
```

**Terraform** (via `tfenv`, lets you pin the version per project —
`ocplab` needs `>= 1.7.0`; this part is identical on Linux and macOS):

```bash
git clone --depth=1 https://github.com/tfutils/tfenv.git ~/.tfenv
echo 'export PATH="$HOME/.tfenv/bin:$PATH"' >> ~/.bashrc   # ~/.zshrc on macOS
source ~/.bashrc
tfenv install latest
tfenv use latest
terraform -version
```

**AWS CLI v2:**

```bash
# Linux (x86_64 — use .../awscli-exe-linux-aarch64.zip on ARM64)
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip awscliv2.zip && sudo ./aws/install

# macOS
curl "https://awscli.amazonaws.com/AWSCLIV2.pkg" -o "AWSCLIV2.pkg"
sudo installer -pkg AWSCLIV2.pkg -target /

aws --version
```

**OpenShift binaries** (swap `linux` for `mac` in both URLs on macOS):

```bash
cd /tmp
curl -O https://mirror.openshift.com/pub/openshift-v4/clients/ocp/latest/openshift-install-linux.tar.gz
curl -O https://mirror.openshift.com/pub/openshift-v4/clients/ocp/latest/openshift-client-linux.tar.gz
tar xvf openshift-install-linux.tar.gz
tar xvf openshift-client-linux.tar.gz
sudo mv openshift-install oc kubectl /usr/local/bin/
openshift-install version && oc version --client
```

`ocplab preflight` checks the binaries above are on the PATH before you ever
touch AWS — regardless of which OS/package manager put them there.

**Or let `ocplab` manage the OpenShift binaries for you.** That `latest` in
the URLs above is a moving target: clone this repo a month from now and you
get a different cluster from the same config. Pin the version instead and
`ocplab` downloads and caches it, so `openshift-install` and `oc` don't need
installing by hand at all:

```bash
ocplab versions list                 # what's published, and what you have
ocplab versions download 4.22.6      # checksum-verified, cached
```

```
    VERSION    CHANNEL        RELEASED       CACHED
    ---------- -------------- -------------- ------
  * 4.22.6     stable-4.22    28 Jul 2026    yes
    4.21.25    stable-4.21    28 Jul 2026    no
    4.20.30    stable-4.20    28 Jul 2026    no
    ...
```

Then set it in `cluster.yaml`:

```yaml
openshift:
  version: 4.22.6
```

From then on ocplab uses that exact version — including for the RHCOS AMI,
which it reads from the installer binary itself, so one field pins both the
cluster and its node image. `oc` and `kubectl` are symlinked into the venv, so
typing `oc get co` by hand gets the matching client.

To change version later, edit `openshift.version` and run
`ocplab versions download` — it fetches the version if you don't have it and
repoints the links either way. Any command that reads the config does the
same, so `ocplab status` is enough when it's already downloaded. Whenever the
links actually move, ocplab says so:

```
Linked openshift-install, oc, kubectl to OpenShift 4.22.6 in .venv/bin.
  If 'oc version' still reports another version, run 'hash -r' — your shell
  remembers where a command lived the first time you ran it.
```

> That `hash -r` only bites **once**, the first time you pin a version in a
> shell that had already used the `oc` you installed by hand. After that the
> cached path is the symlink itself, so later version changes are picked up
> with nothing to do. Confusingly, `which oc` never consults that cache, so it
> shows the new path while `oc` still runs the old binary.

Each version takes ~795 MB on disk (`ocplab status` shows the total,
`ocplab versions rm <version>` frees one). Leave `openshift.version` unset and
nothing changes: the binaries on your PATH are used, exactly as before.

Which versions actually work is a separate question from which ones you can
download — see [§2.1.1](#211--tested-versions) below.

### 2.1.1 ✅ Tested versions

`ocplab versions list` shows everything the mirror publishes. That is not the
same as everything that has been *tried*. This table is the honest answer:

| Version | Status | Last verified | Notes |
|---|---|---|---|
| **4.22.6** | ✅ Verified | 2026-08-03 | Several full cycles. One bootstrap failure that did not reproduce (see below) |
| **4.22.7** | ✅ Verified | 2026-08-03 | Deploy, verify and `ssh` identical to 4.22.6. `destroy` needed a second run, for a bug that was not version-specific (see below) |
| Anything else | ⚪ Untested | — | Nothing prevents it, nothing confirms it |

**"Verified" means a real end-to-end run against AWS**: `deploy` →
`verify` reporting every node Ready and every ClusterOperator healthy → `ssh`
onto a node → `destroy` leaving nothing behind. Not "the binary downloaded",
and not "terraform applied".

**Why so few.** The four traps documented in `CLAUDE.md` — the `owned` cluster
tag, etcd DNS discovery, the private zone shadowing the public one, and the
ingress teardown ordering — were each found the hard way against 4.22. Nothing
says they still apply unchanged on another minor, and nothing says a new one
hasn't appeared. Until somebody runs it, "should work" is a guess.

**The one failure worth knowing about**: on 2026-08-02 a 4.22.6 deploy died
with the bootstrap's `kube-apiserver` crashlooping on its `rbac/bootstrap-roles`
PostStartHook. A clean retry the same afternoon reached
`bootstrap-success` with no crashloop at all, so it was transient rather than a
property of that version. Recorded because "it worked for me" is more useful
with the exceptions attached.

**And the one on 4.22.7**: `destroy` failed with `DependencyViolation` on the
internet gateway, because the ingress-operator had recreated the router's ELB
after the teardown deleted it — a latent bug that earlier teardowns had simply
raced past, not anything to do with the version. A second `ocplab destroy`
cleaned it up (the role is idempotent, which is why re-running is the
documented response), and the operators are now stopped before the
IngressController is deleted so the race can't happen.

Timings were within seconds of 4.22.6 across every phase, which is the boring
result you want from a version bump.

**If you run another version successfully**, a PR adding a row here is a
genuinely useful contribution — more so than most code.

### 2.2 🛠️ Setting up `ocplab`

```bash
git clone <this-repo> ~/openshift-upi-aws
cd ~/openshift-upi-aws
./ocplab setup            # creates .venv/, installs Ansible + collections + Python deps
source .venv/bin/activate # needed before any command that calls Ansible
```

`ocplab validate`/`ocplab render` only need PyYAML (installed at the
system level via `python3-yaml`) and work without the venv activated.
Everything else (`preflight`, `bootstrap`, `deploy`, `destroy`,
`safety-net`) calls `ansible-playbook`, so it needs the venv active — if
you forget, `ocplab` detects it and tells you explicitly.

### 2.3 ☁️ AWS account

The **fully automated path** — recommended if you're starting from
scratch and already have *some* working AWS credentials (even your
personal/root ones, used just this once):

```bash
aws configure --profile default   # or whatever profile you already have
./ocplab bootstrap --admin-profile default
```

This creates the `openshift-lab-terraform` IAM user with
`AdministratorAccess`, generates an access key, and writes it into
`~/.aws/credentials` under the profile named in `cluster.yaml`
(`platform.aws.profile`, `openshift-lab` by default) — automatically, and
idempotently (running it again when the user already exists and works is
a no-op).

**What `ocplab bootstrap` does under the hood**, if you'd rather do it by
hand or just want to understand it:

1. AWS Console → **IAM** → **Users** → **Create user**, name
   `openshift-lab-terraform`, **do not** enable console access.
2. **Attach policies directly** → `AdministratorAccess`.
3. **Security credentials** tab → **Create access key** → CLI usage, save
   the Secret Access Key (shown only once).
4. `aws configure --profile openshift-lab` with those keys, region
   `eu-west-1`.

> **About `AdministratorAccess`:** this is what the official installer
> documentation recommends
> ([openshift/installer — docs/user/aws/iam.md](https://github.com/openshift/installer/blob/main/docs/user/aws/iam.md)):
> the installer touches so many services that Red Hat doesn't publish a
> minimal policy for UPI.

> 🔒 **Never paste a Secret Access Key into a chat, ticket, or commit.**
> If it gets exposed, delete it in IAM and generate a new one immediately.

**Checking or tearing down what `bootstrap` created:**

```bash
./ocplab bootstrap status              # read-only: what exists, what doesn't
./ocplab bootstrap destroy --yes       # deletes the hosted zone + local SSH key
```

`destroy` cleans up any leftover DNS records first (the ingress-operator
creates a `*.apps.<cluster>.<domain>` alias directly in this public zone,
outside Terraform — it survives `ocplab destroy` and would otherwise block
zone deletion) before deleting the zone itself. It deliberately **does
NOT** touch the IAM user by default — that's the only configured AWS
profile on most setups, so deleting it is a real self-lockout risk.
Doing so requires an explicit extra flag on top of `--yes`:

```bash
./ocplab bootstrap destroy --yes --delete-iam-user --admin-profile default
```

This needs a *different*, already-working admin profile (never the
profile being deleted deleting itself) and locks you out of further
`ocplab` automation until you reconfigure `--admin-profile` by hand if you
have no other working AWS credentials — think carefully before using it.

### 2.4 🎫 Red Hat portal

**Pull secret** — authorizes downloading Red Hat's images:

1. Account on [console.redhat.com](https://console.redhat.com) (the free
   tier works)
2. Go to **console.redhat.com/openshift/install/pull-secret**
3. Click **Copy** (it's a long single-line JSON blob)
4. Save it to `~/.ocplab/pull-secret.json`:
   ```bash
   mkdir -p ~/.ocplab && chmod 700 ~/.ocplab
   # paste the JSON into the file below
   nano ~/.ocplab/pull-secret.json
   chmod 600 ~/.ocplab/pull-secret.json
   ```
   `cluster.yaml`'s `credentials.pullSecretFile` points here by default.

The pull secret **doesn't expire**. What expires (in 24h) are the
Ignition certificates the installer generates — `ocplab ignition`
regenerates them automatically when they're stale (see
[section 6](#6-deploying-the-cluster)).

### 2.5 🌐 Domain and DNS

OpenShift **requires** a real domain managed in Route53: it creates public
records during installation. An `/etc/hosts` entry won't work. This is
**registrar-agnostic** — the domain used throughout this guide
(`example.com`) can be registered anywhere (Cloudflare, Namecheap,
GoDaddy, Google Domains, your employer's registrar...); only the exact
click-path in step 2 below changes depending on where.

**Known issue with some registrars:** a few (Cloudflare Registrar is a
notable example) **do not allow** pointing the root domain's nameservers
to a third party — they force you to use their own DNS for the root.

**Adopted solution — delegate a subdomain to Route53** (works everywhere,
and is recommended even if your registrar *would* allow repointing the
root — it's less disruptive):

1. `ocplab bootstrap` creates the **public hosted zone**
   (`aws.example.com`) automatically if it doesn't exist yet — in the
   same run that creates the IAM user, using the `--admin-profile`
   credentials — and prints its 4 nameservers.

   If you'd rather create it by hand instead of running `bootstrap` for
   this part, use that same **admin profile**, not `openshift-lab`: the
   `openshift-lab` profile has no working credentials until `bootstrap`
   (or a manual IAM setup) creates them.
   ```bash
   aws route53 create-hosted-zone \
     --name aws.example.com \
     --caller-reference "openshift-lab-$(date +%s)" \
     --profile default   # or whatever --admin-profile you bootstrapped from
   ```

2. At your registrar's DNS management page, add **4 separate NS
   records** for the `aws` subdomain, one per nameserver Route53 handed
   back — e.g. Type `NS` · Name `aws` · Content: each of the 4 AWS
   nameservers. The exact UI varies by registrar (Cloudflare calls this
   **DNS → Records**; others call it "subdomain delegation" or similar).

   This step is **manual and outside AWS** — `ocplab` can't automate it
   for any registrar.

3. Verify (takes anywhere from minutes to 1h):

   ```bash
   dig NS aws.example.com +short     # should return the ns-*.awsdns-* records
   ```

   `ocplab preflight` also checks this automatically (comparing what
   Route53 delegated against what public DNS actually resolves).

**Resulting zones:**

| Zone | ID | Managed by | Lifecycle |
|---|---|---|---|
| `aws.example.com` (public) | `Z0123456789ABCDEFGHIJ` | `ocplab bootstrap` (created once) | **permanent — never destroyed by `ocplab destroy`** |
| `ocp4lab.aws.example.com` (private) | variable | Terraform | created/destroyed with the cluster |

With `baseDomain: aws.example.com` and `metadata.name: ocp4lab` in
`cluster.yaml`, the FQDNs are:

| Name | Points to |
|---|---|
| `api.ocp4lab.aws.example.com` | External NLB (access with `oc` from outside) |
| `api-int.ocp4lab.aws.example.com` | Internal NLB (traffic inside the cluster) |
| `*.apps.ocp4lab.aws.example.com` | Router ELB (console and applications) |
| `etcd-0/1/2.ocp4lab.aws.example.com` | Private IPs of the masters |
| `_etcd-server-ssl._tcp.ocp4lab...` | SRV → the three `etcd-N` |

---

## 3. Repository structure

```
~/openshift-upi-aws/
├── ocplab                  # CLI in Python — thin UX layer, no business logic
├── cluster.yaml            # YOUR declarative lab config — generated by `ocplab init`, gitignored
├── requirements.txt        # Python deps for the venv (ansible, boto3, dnspython...)
├── .venv/                  # project venv, created by `ocplab setup` — gitignored
│
├── templates/
│   └── install-config.yaml # historical reference only, no longer used at runtime
│
├── terraform/
│   ├── versions.tf                # Terraform and provider versions
│   ├── providers.tf               # AWS provider + default_tags (incl. infraID)
│   ├── variables.tf               # region, AZ, cluster name, CIDRs
│   ├── variables-ec2.tf           # RHCOS AMI, instance types, worker count
│   ├── terraform.tfvars           # GENERATED by `ocplab render` — gitignored
│   ├── vpc.tf                     # VPC, subnets, IGW, NAT, route tables
│   ├── iam.tf                     # master/worker roles and instance profiles
│   ├── security-groups.tf         # master and worker SGs
│   ├── load-balancers.tf          # external/internal NLB + target groups
│   ├── route53.tf                 # DNS records (public and private zone)
│   ├── instance-connect.tf        # EC2 Instance Connect Endpoint (how `ocplab ssh` gets in)
│   ├── bootstrap.tf                # S3 bucket with bootstrap.ign
│   └── ec2.tf                     # instances + target group attachments
│
├── ansible/
│   ├── ansible.cfg
│   ├── callback_plugins/
│   │   └── ocplab_output.py       # readable CLI output + writes the execution log
│   ├── requirements.yml           # collections: amazon.aws, community.general, kubernetes.core
│   ├── inventory/
│   │   ├── localhost.yml          # everything runs locally, no remote nodes
│   │   └── group_vars/all/
│   │       ├── defaults.yml       # fixed project values (hand-maintained)
│   │       └── generated.yml      # GENERATED by `ocplab render` — gitignored
│   ├── playbooks/                 # one per ocplab command, plus deploy.yml/destroy.yml
│   └── roles/
│       ├── preflight/             # binaries, AWS credentials, pull secret, DNS, AMI
│       ├── bootstrap/             # IAM user, public hosted zone, SSH key
│       ├── ignition/               # install-config.yaml -> manifests -> Ignition configs
│       ├── infra/                 # terraform apply/destroy
│       ├── cluster_boot/          # wait-for bootstrap-complete + destroys the bootstrap
│       ├── finalize/              # CSR approval, CPMS, wait-for install-complete
│       ├── teardown/              # cleans up orphaned ELB/ENI/SG + terraform destroy
│       └── safety_net/            # budget, budget action, killswitch lambda + schedule
│
├── install-dir/                   # GENERATED — ephemeral, contains credentials
│   ├── bootstrap.ign              # ~320 KB, goes to S3
│   ├── master.ign / worker.ign    # ~1.7 KB, go as user_data
│   ├── metadata.json              # contains the infraID
│   └── auth/
│       ├── kubeconfig
│       └── kubeadmin-password
│
├── logs/                          # GENERATED — one plain-text log per run, gitignored
│
└── archive/                       # install-dir from previous runs
```

### 🚫 `.gitignore`

```gitignore
# Your personal lab config — generated locally by `ocplab init`
cluster.yaml

templates/install-config.yaml
install-dir/
archive/
*.tfstate
*.tfstate.backup
.terraform/
.terraform.lock.hcl

# Generated by `ocplab render` from cluster.yaml — do not edit by hand
terraform/terraform.tfvars
ansible/inventory/group_vars/all/generated.yml

# Per-run execution logs — live AWS identifiers, local history, not source
logs/

__pycache__/
*.pyc
.venv/
```

### 🔐 Getting a shell on a node — `ocplab ssh`

```bash
ocplab ssh
```

```
NODE      INSTANCE              PRIVATE DNS
master-0  i-0132c40888832801c   ip-10-0-2-75.eu-west-1.compute.internal
master-1  i-0ee55ff66aa77bb88   ip-10-0-2-38.eu-west-1.compute.internal
worker-0  i-0aa11bb22cc33dd44   ip-10-0-2-140.eu-west-1.compute.internal
```

```bash
ocplab ssh master-0
```

Anything after the node name goes straight to `ssh`, so one-shot commands
work too:

```bash
ocplab ssh master-0 sudo crictl ps
```

**How it reaches them.** Masters and workers have no public IP and live in
the private subnet — the `SSH from my IP` rules in `security-groups.tf` are
real, but on their own there is no route in from the internet. Instead of
giving the nodes public addresses (which would mean moving them to the public
subnet and giving up the topology this lab exists to reproduce) or running a
bastion (another instance to pay for and tear down in the right order), the
connection goes through an **EC2 Instance Connect Endpoint**: an
identity-aware TCP proxy that authenticates and authorizes with IAM before
traffic reaches the VPC. AWS charges nothing for it, and because this lab is
single-AZ, the cross-AZ data transfer charge doesn't apply either.

The node names are the ones Terraform already assigns (`master-0`,
`worker-1`), not a second naming scheme. The key is the one from
`credentials.sshPublicKeyFile`, minus the `.pub`. Host keys are kept in
`~/.ocplab/known_hosts` rather than your own, since this lab recreates its
instances constantly and every rebuild would otherwise leave a dead entry
behind.

Two limits worth knowing: an endpoint allows 20 concurrent connections, and a
single connection lasts at most one hour before you have to reconnect.

### 📜 Output and execution logs

Every command that runs a playbook prints a compact, readable stream rather
than raw Ansible: one line per task, skipped tasks hidden, and the roles'
own reports (the `verify` problem list, the `cost` breakdown, the hosted-zone
nameservers) shown as the payload they are.

Each run also writes a full plain-text log under `logs/`, named after the
command and when it started:

```
logs/2026-08-02_092654_verify.log
logs/2026-08-02_101530_bootstrap-apply.log
logs/2026-08-02_104512_deploy.log
logs/2026-08-02_110004_verify-dry-run.log
```

The path is printed **before** the run starts, so a long `deploy` can be
followed from a second terminal:

```bash
tail -f logs/2026-08-02_104512_deploy.log
```

The log holds more than the terminal does — every skipped task, and the
complete result of anything that failed. When a command fails, `ocplab`
prints what failed and points at that file.

Two escape hatches:

- `-v` / `-vv` switches back to raw Ansible output. The run is still logged,
  written by Ansible's own logger instead.
- `NO_COLOR=1` disables colour. Colour is also dropped automatically when
  the output isn't a terminal, so piping or redirecting gives clean text.

Logs are never pruned automatically — they're small, but they accumulate.
Delete `logs/` whenever you like; nothing depends on it.

---

## 4. The `ocplab` CLI and `cluster.yaml`

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
| `ocplab ssh [node]` | Lists the running nodes, or opens a shell on one | No |
| `ocplab versions list\|download\|rm` | Manages the cached OpenShift binaries | No |
| `ocplab destroy` | Ordered teardown of the whole cluster | No (stops billing) |
| `ocplab power on\|off\|status` | Gracefully power the cluster off/on, or check which it currently is | No (still bills EBS while off) |
| `ocplab safety-net apply\|status\|destroy` | Manage the budget/killswitch, outside Terraform | No |

Global flags, valid before or after the subcommand: `-f/--config` (path to
`cluster.yaml`, default `./cluster.yaml`), `--dry-run` (maps to Ansible's
`--check --diff` — preview without changing anything in AWS or in the
cluster), `-v`/`-vv` (verbosity), `--yes` (skip the interactive
confirmation), `--tags`. `ocplab --version` prints the current version (see
[CHANGELOG.md](CHANGELOG.md)).

One deliberate exception to "changes nothing": under `--dry-run`, commands
still refresh the two generated files from `cluster.yaml` before running.
They're gitignored, deterministic, and rebuilt in under a second — and
without that, a dry-run would inspect whatever the previous render left
behind and answer confidently about a config you no longer have. The
`render` command itself is the exception to the exception: there the
generated files *are* the subject of the command, so `ocplab render
--dry-run -v` shows the diff and writes nothing.

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
  # if omitted; set it explicitly only to pin a specific AMI (see §11)
  # rhcosAmi: ami-0123456789abcdef0

credentials:
  pullSecretFile: ~/.ocplab/pull-secret.json    # never committed
  sshPublicKeyFile: ~/.ssh/id_ed25519.pub

certificates:
  # optional — bring your own REAL, CA-signed certs for the API and
  # *.apps (see §6.1). Not for self-signed certs.
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

## 5. The Terraform files, explained

**Total: 60 resources.**

### 🧩 `versions.tf`

Pins Terraform ≥ 1.7 and the `aws ~> 5.0` and `http ~> 3.4` providers. The
`http` provider is used to detect your public IP so SSH can be opened only
for you.

### 🔑 `providers.tf` — the most important file

```hcl
locals {
  infra_id = jsondecode(file("${path.module}/../install-dir/metadata.json")).infraID
}

provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile

  default_tags {
    tags = {
      Project                                   = "openshift-lab"
      "kubernetes.io/cluster/${local.infra_id}" = "owned"
    }
  }
}
```

Three critical things:

- **`kubernetes.io/cluster/<infraID> = owned`** — without this tag
  `aws-cloud-controller-manager` won't start and **the cluster never
  completes** (see [10.1](#101--the-kubernetesiocluster-tag--main-root-cause)).
- **`infra_id` is read from `metadata.json`**, never typed by hand: the
  installer generates a different random suffix (`ocp4lab-rz2zm`,
  `ocp4lab-qbxhm`...) every time the Ignition configs are regenerated.
- **`Project = openshift-lab`** — this is the tag the killswitch Lambda
  looks for, and also the `teardown` role to locate the VPC.

### 🌐 `vpc.tf` — 10 resources

VPC `10.0.0.0/16` with DNS enabled, IGW, public subnet `10.0.1.0/24` (with
`map_public_ip_on_launch`), private subnet `10.0.2.0/24`, EIP + NAT
Gateway, two route tables and their associations.

The subnets carry the tags Kubernetes uses to place load balancers:

- public → `kubernetes.io/role/elb = 1`
- private → `kubernetes.io/role/internal-elb = 1`

### 🔐 `iam.tf` — 6 resources

Roles and instance profiles for masters and workers. The masters need
`ec2:*` and `elasticloadbalancing:*` because the cloud provider manages EBS
volumes and load balancers from inside the cluster. The workers only need
read access and access to the Ignition bucket.

### 🛡️ `security-groups.tf` — 2 resources

| SG | Ports |
|---|---|
| master | `6443` from the Internet · `22` from your IP · all TCP/UDP intra-VPC |
| worker | `80`/`443` from the Internet · `22` from your IP · all TCP/UDP intra-VPC |

The broad intra-VPC rule covers etcd (2379-2380), MCS (22623), kubelet
(10250-10259), NodePorts (30000-32767), and the pod network.

Your IP is auto-detected with `data "http" "my_ip"` against
`checkip.amazonaws.com`. **If your network changes, `ocplab deploy` picks
up the new IP automatically on the next `terraform apply`.**

### ⚖️ `load-balancers.tf` — 8 resources

| LB | Type | Ports | Targets |
|---|---|---|---|
| `ocp4lab-api-ext` | public NLB | 6443 | bootstrap + 3 masters |
| `ocp4lab-api-int` | internal NLB | 6443, 22623 | bootstrap + 3 masters |

HTTPS health checks: `/readyz` on 6443, `/healthz` on 22623.

> The NLBs take **2–3 minutes** to create and about the same to delete.
> That's normal.

### 🌍 `route53.tf` — 13 resources

**Public zone** (`aws.example.com`, pre-existing):

| Resource | Type | Target |
|---|---|---|
| `api.ocp4lab...` | alias A | external NLB |
| `api-int.ocp4lab...` | alias A | internal NLB |
| `etcd-0/1/2.ocp4lab...` | A | masters' private IPs |
| `_etcd-server-ssl._tcp...` | SRV | the three `etcd-N`, port 2380 |

**Private zone** (`ocp4lab.aws.example.com`, associated to the VPC):

- Created by Terraform with **`force_destroy = true`** (essential: the
  ingress-operator adds records there that Terraform doesn't know about).
- Tag `Name = <infraID>-int` — this is how the ingress-operator finds it.
- Contains **the same** `api`, `api-int`, `etcd-N`, and SRV records.

> **Why duplicate the records?** When you associate a private zone with a
> VPC, that zone *shadows* the public DNS for its entire subtree
> **inside** the VPC. If the private zone only had `*.apps`, the pods
> would stop resolving `api-int` and `cluster-version-operator` would die.
> This happened to us — see [10.3](#103-the-private-zone-shadows-the-public-one).

The `*.apps` record is **not** in Terraform: the ingress-operator creates
it automatically in both zones.

### 🥾 `bootstrap.tf` — 3 resources

`bootstrap.ign` weighs ~320 KB and EC2's `user_data` is limited to 16 KB.
Standard solution: upload it to S3 and pass the instance a minimal Ignition
config that downloads it from there.

```hcl
locals {
  bootstrap_user_data = base64encode(jsonencode({
    ignition = {
      version = "3.2.0"
      config  = { merge = [{ source = "s3://${aws_s3_bucket.ignition.id}/bootstrap.ign" }] }
    }
  }))
}
```

A bucket policy allows the master and worker roles to read the object.

### 🖥️ `ec2.tf` — 18 resources

6 instances + 12 target group attachments.

- **RHCOS AMI** — specific to region and version. `ocplab render`
  auto-discovers it for `platform.aws.region` unless `cluster.yaml`'s
  `openshift.rhcosAmi` pins one explicitly (see
  [section 11](#11-quick-reference)).
- **`user_data`** — masters and workers receive `master.ign` / `worker.ign`
  directly (they're small: they just point to the Machine Config Server).
- **Disks** — size/type configurable per node group in `cluster.yaml`
  (120 GB gp3 by default).

---

## 6. Deploying the cluster

### ⚡ The short version

```bash
cd ~/openshift-upi-aws
source .venv/bin/activate
./ocplab preflight    # optional but recommended — catches problems before they cost money
./ocplab deploy
```

That's it. `ocplab deploy` asks for confirmation once (infrastructure is
about to become billable), then runs everything below unattended.

### 🪜 What `ocplab deploy` does, stage by stage

1. **render** — `cluster.yaml` → fresh `terraform.tfvars` and Ansible
   variables (done in Python, before Ansible even starts).
2. **ignition** — generates `install-config.yaml` from those variables
   plus the pull secret and SSH key, then runs `openshift-install create
   manifests` and `create ignition-configs`. Skipped if `install-dir`
   already exists and is less than 24h old (`ocplab ignition --force` to
   override).
   > ⏰ Ignition certificates expire after 24h — if a deploy sits idle
   > longer than that, the next `ocplab deploy` regenerates them
   > automatically, in a clean directory (the old one gets archived to
   > `archive/`). Regenerating changes the `infraID`, which
   > `providers.tf` picks up automatically on the next `terraform apply`.
3. **infra apply** — `terraform apply`, creating all 60 resources.
   Typically ~4 minutes (the NLBs and the NAT Gateway are the slowest
   part).
4. **cluster_boot** — `openshift-install wait-for bootstrap-complete`.
   **13–20 minutes**, normally. Expected log tail:
   ```
   INFO API v1.35.5 up
   INFO Waiting up to 45m0s for bootstrapping to complete...
   INFO Waiting for the bootstrap etcd member to be removed...
   INFO Bootstrap etcd member has been removed
   INFO It is now safe to remove the bootstrap resources
   INFO Time elapsed: 16m13s
   ```
   > 🚫 **Don't touch anything during this stage.** No restarting
   > instances, no changing the network, no deleting pods. Every manual
   > intervention mid-bootstrap in earlier testing cost hours of
   > debugging. If the wait times out, nothing bad happens — it's just a
   > monitor, not an action; `ocplab deploy` re-running reattaches to the
   > current state.

   Once bootstrap completes, the bootstrap EC2 instance gets destroyed
   automatically (it's no longer needed, and it was costing ~$0.21/h).
5. **finalize**:
   - Approves the workers' CSRs, in **two rounds** with a 180s wait
     between them (client cert first, registers the node; only then does
     the kubelet request the server cert).
   - Deletes the `ControlPlaneMachineSet` — in UPI there are no `Machine`
     objects for the masters (Terraform created them directly), so the
     CPMS can never satisfy its replica count and would otherwise stay
     `Degraded` forever, blocking the next step. The operator recreates
     it as `Inactive` and it turns healthy on its own within a couple of
     minutes.
   - `openshift-install wait-for install-complete`. Ends with:
     ```
     INFO All cluster operators have completed progressing
     INFO Install complete!
     INFO Access the OpenShift web-console here: https://console-openshift-console.apps.ocp4lab.aws.example.com
     INFO Login to the console with user: "kubeadmin", and password: "..."
     ```
   - Custom TLS certs for the API/`*.apps`, **if configured** — see
     [§6.1](#61-custom-tls-certificates) below. Fully optional: skipped
     with no error if `cluster.yaml`'s `certificates` paths don't have
     anything at them.

### 6.1 Custom TLS certificates

OpenShift auto-generates its own self-signed certs for the external API
(`api.<cluster>.<domain>`) and the default router (`*.apps.<cluster>.<domain>`).
`ocplab` can swap those for your own — but **only with a REAL certificate,
signed by a trusted CA** (Let's Encrypt, your org's PKI, a purchased
cert...). `ocplab` does not generate certificates itself.

> ⚠️ **Don't use a self-signed cert here.** It was tried and removed. A
> self-signed cert doesn't solve anything a lab needs — OpenShift's own
> default cert already has the correct hostnames and is already trusted
> everywhere inside the cluster, out of the box. Swapping in a different
> self-signed cert only adds manual cluster-trust-bundle and kubeconfig
> wiring for zero real benefit, and breaks badly if that wiring is
> incomplete: on a live run this crash-looped `openshift-authentication`
> (`oauth-openshift` pods stuck in `CrashLoopBackOff`) and broke `oc`
> access to the cluster until fully untangled. This only pays off with a
> certificate that's genuinely, publicly trusted.

If you do have a real certificate, place the cert/key files anywhere and
point `cluster.yaml`'s `certificates.*` fields at them:

```bash
./ocplab deploy   # finalize applies whatever it finds at those paths
```

This requires **both** an explicit `certificates.*` field in `cluster.yaml`
**and** a real file at that path — not just a file sitting on disk. Leave
the fields unset (the default) → the piece is skipped and the cluster
keeps OpenShift's own default cert, same as if this feature didn't exist.
(An earlier version only checked file presence, defaulting unset fields
to a fixed path — that meant leftover files from a previous attempt could
get silently re-applied on a later deploy even after removing them from
`cluster.yaml`. Fixed: undeclared fields are never defaulted.) The API
cert and the apps cert are independent — you can configure either one
without the other.

To swap in different certs later (including onto an already-running
cluster), overwrite the files at the same paths and re-run `ocplab
deploy` — `finalize`'s cert step is idempotent and safe to re-apply.

Since this lab's domain is a real, publicly delegated one (`ocplab
preflight` verifies the delegation), a real cert here is realistic to get
— e.g. via Let's Encrypt using a DNS-01 challenge against Route53 — not
just a theoretical option.

### 🔎 Access and verify

```bash
./ocplab console          # prints the console URL and the kubeadmin password
./ocplab verify            # wraps the three checks below, exits non-zero if unhealthy
export KUBECONFIG=~/openshift-upi-aws/install-dir/auth/kubeconfig
oc get clusterversion     # Cluster version is 4.22.x
oc get nodes              # 5 Ready
oc get co                 # all True / False / False
```

`ocplab verify` fails fast (in seconds, not indefinitely) if the API
isn't reachable at all — e.g. the cluster is powered off
(`ocplab power off`) or was destroyed — with a message pointing at
`ocplab power status`/`ocplab status` instead of hanging on the
Kubernetes client's own much longer default.

### 🪶 Optional — take load off the masters

```bash
oc patch schedulers.config.openshift.io cluster --type merge \
  -p '{"spec":{"mastersSchedulable":false}}'
```

---

## 7. Destroying the cluster

> ⚠️ **Order matters.** The cluster creates AWS resources that Terraform
> doesn't know about (the router ELB, its ENIs, its security group, and
> the `*.apps` records). A raw `terraform destroy` **fails** with
> `DependencyViolation` on the Internet Gateway and the public subnet if
> they aren't cleaned up first — `ocplab destroy` handles this for you.

### ⚡ The short version

```bash
cd ~/openshift-upi-aws
./ocplab destroy
```

Asks for confirmation once, then runs the full sequence unattended.

### 🪜 What `ocplab destroy` does, stage by stage

1. Deletes the `ingresscontroller` (the operator takes down its ELB,
   ENIs, security group, and `*.apps` records with it).
2. **Polls for real** (not a blind `sleep`) until the router ELB actually
   disappears, up to 300s; falls back to deleting it manually if the
   timeout is reached.
3. Polls for the ENIs to free up, up to 180s; attempts a manual delete of
   any stragglers (tolerating failure — they're usually still detaching).
4. Cleans up orphaned `k8s-elb-*` security groups (never touching the
   `ocp4lab-*` ones, which Terraform manages).
5. Runs `terraform destroy`. Duration: ~5–10 minutes. Approximate order:
   attachments and DNS records (fast) → instances (1–4 min) → NLBs and NAT
   Gateway (~1 min) → public subnet and IGW → VPC (last, 1–3 min).
6. Prints a final count of EC2 instances, NAT Gateways, NLBs, classic
   ELBs, and EIPs still tagged `Project=openshift-lab` — should all be
   zero.

### 🧹 If it fails anyway — manual cleanup

`ocplab destroy`'s polling handles the common cases, but if AWS is
unusually slow or something is left over, these are the exact symptoms
and fixes:

| Error | Orphaned resource | Fix |
|---|---|---|
| `Network vpc-xxx has some mapped public address(es)` | Router ELB | delete the Classic LB |
| `The subnet has dependencies and cannot be deleted` | ELB ENIs | wait 1–2 min after deleting the LB |
| `DependencyViolation` on the VPC | SG `k8s-elb-*` | delete it manually |
| `HostedZoneNotEmpty` | `*.apps` record, private zone | delete it manually |

```bash
VPC_ID=vpc-xxxxxxxx     # the one shown in the error

# 1. Router ELB — filter by VPC, NOT by name (it has a hash-based name;
#    k8s-elb-* is its SECURITY GROUP's name, not the ELB's own)
aws elb describe-load-balancers \
  --query "LoadBalancerDescriptions[?VPCId=='$VPC_ID'].[LoadBalancerName]" \
  --output text --profile openshift-lab
aws elb delete-load-balancer --load-balancer-name <NAME> --profile openshift-lab

# 2. Wait for the ENIs to free up (1-2 min) — the table should end up empty
aws ec2 describe-network-interfaces --filters "Name=vpc-id,Values=$VPC_ID" \
  --query 'NetworkInterfaces[*].[NetworkInterfaceId,Description,Status]' \
  --output table --profile openshift-lab

# 3. Orphaned security group
aws ec2 describe-security-groups --filters "Name=vpc-id,Values=$VPC_ID" \
  --query 'SecurityGroups[?GroupName!=`default`].[GroupId,GroupName]' \
  --output table --profile openshift-lab
aws ec2 delete-security-group --group-id <SG_ID> --profile openshift-lab

# 4. Retry
cd terraform && terraform destroy -auto-approve
```

**Deleting an orphaned `*.apps` record:**

```bash
aws route53 list-resource-record-sets --hosted-zone-id <ZONE> --profile openshift-lab

cat > /tmp/del.json << 'EOF'
{
  "Changes": [{
    "Action": "DELETE",
    "ResourceRecordSet": {
      "Name": "*.apps.ocp4lab.aws.example.com.",
      "Type": "A",
      "AliasTarget": {
        "HostedZoneId": "<ELB_HOSTED_ZONE_ID>",
        "DNSName": "<ELB_DNS_NAME>.",
        "EvaluateTargetHealth": false
      }
    }
  }]
}
EOF

aws route53 change-resource-record-sets \
  --hosted-zone-id <ZONE> --change-batch file:///tmp/del.json --profile openshift-lab
```

> In a Route53 `DELETE`, the `ResourceRecordSet` must match **exactly**
> the existing one (name, type, and full `AliasTarget`). NS and SOA
> records don't get deleted this way: they disappear on their own when the
> zone is removed.

### 🔒 What does NOT get destroyed (and shouldn't)

- Public hosted zone `aws.example.com` (`Z0123456789ABCDEFGHIJ`) — if you
  delete it you'll have to redo the NS delegation at your registrar.
- IAM user `openshift-lab-terraform`.
- The cost safety net (`ocplab safety-net` resources) — see
  [section 9](#9-cost-safety-net).
- The domain itself, at whichever registrar it's on.

### ♻️ Recreating after a destroy

```bash
cd ~/openshift-upi-aws
./ocplab deploy
```

### 🔌 Stopping and starting the cluster, instead of destroying it

```bash
./ocplab power off      # gracefully shut everything down
./ocplab power on       # bring it back up
./ocplab power status   # is it currently on, off, or somewhere in between?
```

An alternative to `destroy` + redeploy, offered as a convenience — **not**
as a way to save money. Read that twice: a stopped cluster's EC2
instances still bill for their attached EBS volumes, so **leaving the
cluster stopped costs more over time than destroying it and redeploying
later** (redeploying is ~25-40 minutes of mostly-unattended wait; keeping
it stopped is pure ongoing cost for nothing running). Use this when you
want the exact same cluster back in ~10-15 minutes without a full
reinstall — not as a nightly cost-saving habit.

Follows Red Hat's documented graceful shutdown/restart procedure exactly
(`openshift/openshift-docs`, not a shortcut): `off` cordons every node,
drains the **workers only** (control plane nodes don't need draining for
standard cluster pods), then shuts down via an in-guest `shutdown`
command — **workers first, masters last**, since the control plane holds
the API VIP — rather than a raw cloud-provider stop call, so etcd/kubelet
close cleanly. `on` reverses it: **masters first**, waits for them
`Ready` (approving any CSRs along the way, same logic `ocplab deploy`
already uses), **then** workers, same wait-and-approve, then uncordons
everything and runs the same health check as `ocplab verify`. Every wait
is a real poll (AWS instance state + Kubernetes node readiness) — never a
blind sleep.

`off` also prints a reminder of the API-server-to-kubelet certificate's
expiry date — restart on or before it to avoid extra manual CSR approvals
on top of the normal post-restart ones.

`status` is read-only — it just reports each master/worker EC2 instance's
current AWS state (`Power: ON`/`OFF`/`MIXED (...)`) without touching
`oc`/kubectl at all. It's what `ocplab status` points to for "is the
cluster on or off"; for cluster **health** (nodes, operators) that's still
`ocplab verify`.

---

## 8. Costs

On-Demand prices for **eu-west-1** (Ireland). Approximate — check
[calculator.aws](https://calculator.aws) for exact figures.

### 💵 `ocplab cost` — the live version of this table

```bash
./ocplab cost
```

Everything below this point is a static, hand-written snapshot for
**eu-west-1** — useful for a quick mental model, but it goes stale the
moment AWS reprices something or you deploy to a different region.
`ocplab cost` is the real thing: it looks at what's **actually**
deployed right now (instance types/counts, EBS volumes, NAT Gateway,
load balancers, allocated Elastic IPs), whether each EC2 instance is
currently **running or stopped** (`ocplab power off` correctly zeroes
out the compute line while EBS/NAT/LB keep billing), and fetches
real on-demand prices for `platform.aws.region` from the AWS Pricing
API — cached locally under `~/.ocplab/pricing-cache.json` for a week
(on-demand prices rarely change) so every later run is instant. Example
output, on this lab powered off:

```
=== Approximate hourly cost — ocp4lab (eu-west-1) ===
Compute (EC2, 0 running / 5 stopped): $0/h
Storage (EBS, 5 volume(s), 600 GB total): $0.0723/h
NAT Gateway (1): $0.048/h
Load Balancers (2): $0.0504/h
Public IPv4 addresses (1): $0.005/h
-----------------------------------------------------------
TOTAL: $0.1757/h (~$4.22/day if left running)
```

Still an approximation by nature (no tool can be exact without your real
usage): it excludes data transfer, load balancer LCU usage-based charges,
and public IPs owned directly by a load balancer (only allocated Elastic
IPs are counted) — always flagged in the output, never silently assumed.

### ⏱️ Hourly breakdown (static, eu-west-1 only)

| Resource | Qty | $/h unit | $/h total |
|---|---:|---:|---:|
| EC2 `m5.xlarge` (masters) | 3 | 0.214 | **0.642** |
| EC2 `m5.large` (workers) | 2 | 0.107 | **0.214** |
| NAT Gateway | 1 | 0.052 | **0.052** |
| NLB (external + internal api) | 2 | 0.025 | **0.050** |
| Router ELB (`*.apps`) | 1 | 0.028 | **0.028** |
| EBS gp3 (~600 GB) | — | — | **0.070** |
| Route53 hosted zones | 2 | — | **0.0014** |
| **TOTAL steady-state cluster** | | | **≈ $1.06/h** |

During installation the bootstrap (`m5.xlarge`) adds about 20 minutes:
**≈ $0.07 extra** per deployment.

### 📈 Cost by uptime

| Usage | Approx. cost |
|---|---|
| 1 hour | $1.06 |
| 2h session | $2.12 |
| 8h workday | $8.50 |
| 24h (left on by mistake) | $25.40 |
| A month running continuously | ~$770 |

**The variable that controls spend is uptime, not the architecture.**
Running `ocplab destroy` at the end of every session is what makes the
difference — and the [safety net](#9-cost-safety-net) is what protects
you if you forget.

### 📌 Fixed permanent costs

| Resource | Cost |
|---|---|
| Public hosted zone `aws.example.com` | $0.50/month |
| Domain `example.com` (varies by registrar) | ~$1–15/year |
| S3 bucket (empty after destroy) | cents |
| Killswitch Lambda (~30 invocations/month) | $0 — permanent free tier |

### ⏸️ About "stopping" the instances

Stopping the EC2 instances only saves the compute portion (~$0.86/h).
**Still billing:** the NAT Gateway, the three load balancers, the EBS
volumes, and the hosted zones (~$0.20/h). Also, a stopped-and-restarted
OpenShift cluster usually runs into etcd and certificate-rotation
problems.

**`ocplab destroy` is better than stopping.**

---

## 9. Cost safety net

Three independent mechanisms, managed by **`ocplab safety-net`** but
created **outside of Terraform on purpose**: that way they survive
`ocplab destroy`/`deploy` cycles and keep protecting you even if you
forget everything else.

```bash
./ocplab safety-net apply     # create/ensure all three mechanisms (idempotent)
./ocplab safety-net status    # report what exists, without touching anything
./ocplab safety-net destroy   # tear all three down (rarely needed — see below)
```

### 9.1 🔔 AWS Budget with alerts

Budget `openshift-lab-budget` for **50 USD/month** (configurable via
`cluster.yaml`'s `safetyNet.budgetUsd`) with email alerts at **50%, 80%,
and 100%** to the addresses in `safetyNet.alertEmails`.

> Budgets emails **don't require prior confirmation** (unlike SNS): they
> sit waiting and fire on their own once the threshold is crossed. Not
> receiving anything means you haven't spent that much, not that it's
> misconfigured.

### 9.2 🚨 Budget Action — automatic lockdown at 80%

Once you go over 80%, AWS automatically attaches (`--approval-model
AUTOMATIC`) the `openshift-lab-budget-lockdown` policy to the IAM user,
which **denies creating** expensive resources:

```json
"Action": [
  "ec2:RunInstances", "ec2:CreateNatGateway", "ec2:AllocateAddress",
  "elasticloadbalancing:CreateLoadBalancer", "rds:CreateDBInstance"
]
```

> It blocks **creation** only, never deletion: you'll always be able to
> run `ocplab destroy` even while the lockdown is active.

**Limitation:** AWS billing isn't real-time (it can take hours to reflect
spend). That's why the third mechanism exists.

### 9.3 🔌 Killswitch — daily Lambda at 22:00 (Europe/Madrid)

Lambda `openshift-lab-killswitch`, invoked by EventBridge Scheduler
(`safetyNet.killswitch.schedule`/`timezone` in `cluster.yaml`), which
**terminates** everything tagged `Project = openshift-lab`: EC2 instances,
NAT Gateways, Load Balancers, and loose EIPs.

Uses a scheduler timezone, so it adjusts on its own to the
daylight-saving-time change.

> ⚠️ **Acts directly on AWS, not through Terraform.** If it fires, the
> `terraform.tfstate` will end up out of sync: run `ocplab destroy`
> afterward to clean up the state. It's an emergency mechanism, not the
> normal flow.

### 📋 Safety net inventory

| Type | Name |
|---|---|
| Budget | `openshift-lab-budget` (+ its Budget Action) |
| IAM Policy | `openshift-lab-budget-lockdown` |
| IAM Role | `openshift-lab-budget-action-role` |
| IAM Role | `openshift-lab-killswitch-role` |
| IAM Role | `openshift-lab-scheduler-role` |
| Lambda | `openshift-lab-killswitch` |
| EventBridge Schedule | `openshift-lab-killswitch-schedule` |

`ocplab safety-net destroy` tears all of it down, in the reverse order of
creation. You'd only do this if you were permanently retiring the lab —
day to day, leave it running.

---

## 10. Troubleshooting and lessons learned

The core issues that cost real debugging time. **All of them are already
fixed in the code**, but it's worth understanding them.

### 10.1 ⭐ The `kubernetes.io/cluster/<infraID>` tag — main root cause

**Symptom:** the cluster gets stuck at
`Working towards 4.22.6: 80 of 1015 done (7% complete)` for hours. Nodes
stay `NotReady`. `network-operator` and `etcd-operator` stay `Pending`
forever:

```
0/3 nodes are available: 3 node(s) had untolerated taint(s)
```

**Diagnosis:**

```bash
oc get nodes -o json | jq -r '.items[] | .metadata.name, (.spec.taints[]? | "  \(.key)")'
# → node.cloudprovider.kubernetes.io/uninitialized
```

And in the CCM logs (via SSH into the node, since `oc logs` doesn't work
yet):

```
E tags.go:99] Tag "KubernetesCluster" nor "kubernetes.io/cluster/..." not found
F main.go:104] Cloud provider could not be initialized: AWS cloud failed to find ClusterID
```

**Explanation:** AWS boots the nodes with the
`node.cloudprovider.kubernetes.io/uninitialized:NoSchedule` taint. Only
`aws-cloud-controller-manager` can remove it, and to do so it checks its
own EC2 instance's tags looking for `kubernetes.io/cluster/*`. Without
that tag it goes into `CrashLoopBackOff` → the taint never gets removed →
nothing gets scheduled → **total circular deadlock**.

**Fix:** `default_tags` in `providers.tf` with the infraID read from
`metadata.json`.

### 10.2 🔍 etcd discovery via DNS

**Symptom:** the masters never form quorum. No
`/etc/kubernetes/manifests/etcd-pod.yaml`. `oc get machineconfig | grep etcd`
is empty.

**Explanation:** in UPI, etcd members discover each other via an SRV
record `_etcd-server-ssl._tcp.<cluster>.<domain>` that points to three A
records `etcd-0/1/2`.

```bash
dig SRV _etcd-server-ssl._tcp.ocp4lab.aws.example.com +short
dig A etcd-0.ocp4lab.aws.example.com +short
```

**Fix:** the `etcd_a` and `etcd_srv` resources in `route53.tf`.

### 10.3 The private zone shadows the public one

**Symptom:** after creating the private hosted zone to fix the ingress,
`cluster-version-operator` goes into `CrashLoopBackOff`:

```
lookup api-int.ocp4lab.aws.example.com on 10.0.0.2:53: no such host
F start.go:27] error: error processing feature gates: failed to sync informer cache
```

**Explanation:** a private hosted zone associated with a VPC takes
priority over the public one **inside** that VPC, for its entire subtree.
If it only contains `*.apps`, the rest of the names (`api`, `api-int`,
`etcd-*`) stop resolving from the pods.

**Fix:** replicate `api`, `api-int`, `etcd-N`, and the SRV record in the
private zone too.

**Check from inside the cluster:**

```bash
oc debug node/<node> -- chroot /host dig +short api-int.ocp4lab.aws.example.com
```

### 10.4 ⏳ Ignition certificates — expire after 24h

```
ERROR Bootstrap Ignition-Config Certificate aggregator-ca.crt expired at ...
FATAL failed to fetch Bootstrap Ignition Config: 13 certificates expired
WARNING Please regenerate ignition configuration files in a new directory.
```

**Fix:** the `ignition` role always regenerates in a clean directory (the
old `install-dir`'s internal state is what makes the installer reuse
expired certificates) — this is automatic in `ocplab deploy`.

### 10.5 🛑 `control-plane-machine-set` stuck `Degraded`

Normal in UPI: there are no `Machine` objects for the masters (Terraform
created them). Blocks `wait-for install-complete` — the `finalize` role
deletes it automatically; see [section 6](#6-deploying-the-cluster).

---

### ❓ Other common issues

#### 🐍 `ocplab preflight`/`deploy`/etc. fail with "couldn't run 'ansible-playbook'"

The venv isn't activated: `source .venv/bin/activate`. `ocplab` detects
this specific case and says so explicitly, but if the message instead
points at some *other* `ansible-playbook` (e.g. from a different project
or an editor extension), check `which -a ansible-playbook` — it means
something earlier in your `PATH` is shadowing the venv's.

#### 🔍 `oc` responds with `nodes "..." not found` or `No resources found`

That terminal doesn't have `KUBECONFIG` exported and points to a
different context:

```bash
export KUBECONFIG=~/openshift-upi-aws/install-dir/auth/kubeconfig
```

#### 🔑 `Permission denied (publickey)` when hopping from the bootstrap to a master

`ssh -A` forwards the **agent**, and the agent only forwards keys loaded
with `ssh-add` (having `-i` isn't enough):

```bash
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/id_ed25519
ssh-add -l
ssh -A -i ~/.ssh/id_ed25519 core@<BOOTSTRAP_PUBLIC_IP>
ssh core@<MASTER_PRIVATE_IP>
```

The RHCOS user is always **`core`**, with passwordless `sudo`.

#### 🌐 `authentication` or `console` reports `False` with `no such host`

CoreDNS cached a negative response while DNS was still incomplete. Usually
recovers on its own within a few minutes. To force it:

```bash
oc delete pods -n openshift-dns -l dns.operator.openshift.io/daemonset-dns=default
```

#### 🔀 The `*.apps` record doesn't get created

Check that the cluster sees both zones:

```bash
oc get dns.config/cluster -o yaml | grep -A6 -E "publicZone|privateZone"
```

Should show `privateZone.tags.Name = <infraID>-int` and `publicZone.id`.

#### 📡 `RetrievedUpdates: False — version not found in the "stable-4.22" channel`

Cosmetic: that version isn't published on that update channel yet. Doesn't
affect the cluster.

---

### 🩺 Diagnostic commands

```bash
# Overall status
oc get clusterversion
oc get co | grep -v "True.*False.*False"     # only the unhealthy ones

oc get nodes -o wide

# Real detail when the CVO reports MultipleErrors
oc get clusterversion version -o jsonpath='{range .status.conditions[*]}{.type}{"\t"}{.status}{"\t"}{.message}{"\n"}{end}'
oc logs -n openshift-cluster-version -l k8s-app=cluster-version-operator --tail=100

# Node taints
oc get nodes -o json | jq -r '.items[] | .metadata.name, (.spec.taints[]? | "  \(.key)=\(.value):\(.effect)")'

# Target group health
aws elbv2 describe-target-health --target-group-arn <ARN> --profile openshift-lab

# Inside a node (via SSH)
sudo crictl ps -a
sudo crictl logs --tail=60 <CONTAINER_ID>
sudo journalctl -u kubelet --no-pager | tail -50
sudo systemctl status crio

# Continuous monitoring
watch -n 30 'oc get clusterversion; echo; oc get co | grep -v "True.*False.*False"'
```

---

## 11. Quick reference

### 🆕 Deploy from scratch

```bash
cd ~/openshift-upi-aws
source .venv/bin/activate
./ocplab preflight
./ocplab deploy
```

### 💥 Destroy

```bash
cd ~/openshift-upi-aws
./ocplab destroy
```

### ℹ️ Cluster info

```bash
./ocplab console          # console URL + kubeadmin password
./ocplab status           # install-dir age, Terraform resource count
jq -r .infraID install-dir/metadata.json
```

### 🔄 Updating the RHCOS AMI

`ocplab render` auto-discovers the current AMI for `platform.aws.region`
whenever `cluster.yaml`'s `openshift.rhcosAmi` is unset — just run it again
to pick up a newer one:

```bash
./ocplab render
```

To pin a specific AMI instead (e.g. to freeze a known-good version), set it
explicitly:

```bash
openshift-install coreos print-stream-json | \
  jq -r '.architectures.x86_64.images.aws.regions["eu-west-1"].image'
# copy the value into cluster.yaml -> openshift.rhcosAmi, then:
./ocplab render
```

### 👷 Changing the number of workers

```bash
# cluster.yaml -> compute.replicas
./ocplab render
cd terraform && terraform apply -auto-approve
# afterward, approve the CSRs of the new nodes — ./ocplab deploy re-running
# also handles this via the finalize role
```

---

## 12. References

- [OpenShift Container Platform documentation](https://docs.redhat.com/en/documentation/openshift_container_platform)
- [openshift/installer — AWS IAM permissions](https://github.com/openshift/installer/blob/main/docs/user/aws/iam.md)
- [Red Hat pull secret](https://console.redhat.com/openshift/install/pull-secret)
- [Terraform AWS Provider](https://registry.terraform.io/providers/hashicorp/aws/latest/docs)
- [AWS pricing calculator](https://calculator.aws)

---

## 🤝 Contributing

Contributions are welcome! This started as a personal lab, but anyone is
free to:

- 🐛 Open an issue to report a bug, ask a question, or suggest an improvement
- 🔀 Fork the repository and adapt it to your own needs
- 📬 Submit a pull request with a fix, a new feature, or a better approach

Whether it's a small typo fix or a bigger idea, all contributions are
genuinely appreciated. Check [BACKLOG.md](BACKLOG.md) for what's already
planned — a good starting point if you're looking for something to pick up.

---

## 👤 Author

🧑‍💻 **Name:** Luis Garcia

☁️ **Role:** Cloud Native Engineer

📍 **Location:** Consuegra (Spain)

🐙 **GitHub:** [@LuixyToledo97](https://github.com/LuixyToledo97)

📧 **Email:** [luisgarciavalle97@outlook.es](mailto:luisgarciavalle97@outlook.es)

💼 **LinkedIn:** [lgv-rhca](https://www.linkedin.com/in/lgv-rhca/)

---

## 📄 License

MIT — see [LICENSE](LICENSE). Use it, fork it, modify it, ship it in
whatever you're building; just keep the copyright notice.
