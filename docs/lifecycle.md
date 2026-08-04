# Deploying and destroying the cluster

> Part of the [ocplab](../README.md) documentation.

## Deploying the cluster

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
     [§7.1](#71-custom-tls-certificates) below. Fully optional: skipped
     with no error if `cluster.yaml`'s `certificates` paths don't have
     anything at them.

### 7.1 Custom TLS certificates

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
oc get clusterversion     # Cluster version is 4.22.x
oc get nodes              # 5 Ready
oc get co                 # all True / False / False
```

Those `oc` commands need no `export`: activating the venv already points
`KUBECONFIG` at this cluster — see [Which cluster `oc` talks
to](cli.md#-which-cluster-oc-talks-to). In a shell where you didn't activate it,
`eval "$(ocplab env)"`.

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

## Destroying the cluster

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
  [the cost safety net](costs.md#cost-safety-net).
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
