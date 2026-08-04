# Prerequisites

> Part of the [ocplab](../README.md) documentation.

## Prerequisites

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
| **4.22.7** | ✅ Verified | 2026-08-04 | Re-verified end to end, this time driven entirely from the web UI: deploy, `verify` at 5/5 Ready and 27 healthy ClusterOperators, `cost`, and a `destroy` that completed in 12m10s leaving nothing behind. The earlier run needed a second `destroy`, for a bug that was not version-specific and is now fixed |
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
[Deploying](lifecycle.md#deploying-the-cluster)).

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
