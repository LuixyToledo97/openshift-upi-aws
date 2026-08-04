# Costs

> Part of the [ocplab](../README.md) documentation.

## Costs

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
(on-demand prices rarely change) so every later run is instant.

**Instances running as Spot are priced as Spot**, from
`describe-spot-price-history` for your availability zone. Those prices are
*not* cached: on-demand pricing changes a few times a year, Spot pricing moves
by the hour, and a week-old Spot price would be no better than the on-demand
one. When a Spot price can't be fetched, that instance falls back to its
on-demand rate and the output names the type — the total is then an
over-estimate, which is the safe direction to be wrong in.

Example output, on a running minimal-profile cluster with Spot workers:

```
=== Approximate hourly cost — ocp4lab (eu-west-1) ===
Compute (EC2, 5 running / 0 stopped): $0.617/h (3 on-demand $0.5568/h + 2 Spot $0.0602/h)
Storage (EBS, 5 volume(s), 500 GB total): $0.0634/h
NAT Gateway (1): $0.048/h
Network Load Balancers (2, API): $0.0576/h
Classic Load Balancer (1, ingress router): $0.03/h
Public IPv4 addresses (1): $0.005/h
-----------------------------------------------------------
TOTAL: $0.821/h (~$19.7/day if left running)
Approximate pricing — excludes data transfer, load balancer LCU
usage-based charges, public IPs owned directly by load balancers (only
allocated Elastic IPs are counted), and Route 53 (the cluster's private
hosted zone is $0.50/month, about $0.0007/h, plus per-query charges).
Spot instances are priced at the current eu-west-1a Spot price, which moves hourly; the rest is on-demand.
```

Still an approximation by nature (no tool can be exact without your real
usage): it excludes data transfer, load balancer LCU usage-based charges,
public IPs owned directly by a load balancer (only allocated Elastic IPs
are counted), and Route 53 — always flagged in the output, never silently
assumed.

The exclusion that matters most is the first one. Per-deploy data transfer is
the largest single charge in this project — see [The hourly rate is not the
cost of a cycle](#-the-hourly-rate-is-not-the-cost-of-a-cycle) — and `cost`
cannot see it: it prices resources, and that is usage. Never read the hourly
figure as the cost of a short deploy-test-destroy cycle.

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
difference — and the [safety net](#cost-safety-net) is what protects
you if you forget.

### 📌 Fixed permanent costs

| Resource | Cost |
|---|---|
| Public hosted zone `aws.example.com` | $0.50/month |
| Domain `example.com` (varies by registrar) | ~$1–15/year |
| S3 bucket (empty after destroy) | cents |
| Killswitch Lambda (~30 invocations/month) | $0 — permanent free tier |

### 🚚 The hourly rate is not the cost of a cycle

Everything above is the **steady-state** cost of a running cluster. A
deploy-test-destroy cycle carries a one-off charge on top, and it is not small.

Measured on a real bill, 2026-07-26 to 2026-08-03 — the largest line item in
the whole project turned out to be neither compute nor storage:

| Line | |
|---|---:|
| NAT gateway **data processing** | **$8.85** |
| EC2 compute | $6.17 |
| EBS gp3 | $1.67 |
| NAT gateway hours | $1.44 |

Per deploy, measured from the gateway's own CloudWatch metric rather than
divided out of a monthly bill: **35.2 GB across ten deploys, ≈$1.58 each** —
what six nodes each pulling their own images of the OpenShift release payload
from quay.io costs. The images themselves are free to receive — AWS
does not charge for inbound data transfer — but a NAT gateway charges per GB
"regardless of the traffic's source or destination", and the nodes are in a
private subnet by design.

So a one-hour cycle is closer to **$1.06 of infrastructure plus $1.50 of
traffic** than to $1.06 total. For short, frequent labs, more than half the
bill is images crossing the NAT.

`ocplab cost` cannot see this — it prices the resources that exist right now,
and this is a usage charge that only appears on the bill afterwards. The
disclaimer at the bottom of its report ("excludes data transfer") is doing more
work than it looks.

**Disabling optional cluster capabilities cuts about a third of it.** Measured
on 2026-08-03 against a ten-deploy baseline of 35.2 GB: the set in
`examples/minimal.yaml` brought one deploy down to **24.8 GB, 29% less**, worth
~$0.46 a time — and left the cluster healthy, with 27 ClusterOperators instead
of 34. It costs real functionality, though: no internal image registry, no S2I
builds, no OperatorHub catalogue. `ocplab init --minimal` includes it; the
standard profile documents it commented-out, so you can enable it there too.

The ~24.8 GB that remain are mostly the base payload, which every node pulls
its own copy of. A mirror or pull-through cache would deduplicate that; it's in
[BACKLOG.md](../BACKLOG.md), with the arithmetic for when it pays for itself
(roughly seventeen deploys a month).

### 🪙 A cheaper profile — smaller nodes and Spot

```bash
ocplab init --minimal
```

writes a `cluster.yaml` that gets the same cluster for **≈ $0.83/h instead of
$1.06/h**. It's a plain config file, not a hidden mode: everything it changes
is visible in it, with the reasoning inline — and it's the same file as
[`examples/minimal.yaml`](../examples/minimal.yaml), readable here without
running anything.

Three levers, in order of how much they're worth:

| Lever | Saving | Notes |
|---|---|---|
| **Spot for workers + bootstrap** | ~$0.11/h | Measured at 50-55% off on-demand in `eu-west-1a` — not the 70-90% often quoted |
| **Smaller instances** (`t3.xlarge` / `t3.large`) | ~$0.13/h | Exactly Red Hat's documented minimums: 4 vCPU / 16 GB control plane, 2 vCPU / 8 GB compute |
| **100 GB disks instead of 120 GB** | ~$0.01/h | 100 GB is the documented minimum. `gp3` is already the cheapest usable type — `st1`/`sc1` cost less per GB but **AWS does not allow them as boot volumes** |

**The control plane stays on-demand, and `controlPlane.spot` is rejected.** In
UPI there is no ControlPlaneMachineSet and no Machine API for manually
provisioned machines, so a reclaimed master is never replaced automatically
and recovering one means repairing etcd membership by hand. Workers are a
different proposition: losing one reschedules pods, and `ocplab deploy`
recreates it.

**When a worker does get reclaimed**, the cluster keeps working — you lose
capacity, not availability, and pods reschedule onto what's left. Nothing in
the cluster reacts to AWS's 2-minute notice (UPI has no node-termination
handler), though the AWS cloud-controller-manager does clean up the orphaned
`Node` object on its own once the instance is gone. To put the capacity back:

```bash
ocplab repair
```

It recreates the missing instance, approves the new node's CSRs, and removes
the orphaned `Node` object. It is **not** Spot-specific — a node terminated by
hand or lost to an AWS failure is recovered exactly the same way.

`repair` is the only command that runs `terraform apply` against a running
cluster, so it plans first and **refuses anything that isn't add-only**: if
Terraform wants to change, replace or destroy something, it stops and tells you
what. It also declines to recreate a *master* — a replacement master needs its
etcd membership repaired by hand, and an instance that exists but isn't an etcd
member is worse than an obviously missing one.

Two things to know before using it:

- **`ocplab power off` stops working** with Spot workers — a Spot instance can
  only be terminated, never stopped. `ocplab power off` refuses up front rather
  than discovering it after draining. Use `destroy` instead, which is cheaper
  than a stopped cluster anyway.
- **`t3` is burstable.** Sustained CPU consumes credits and then throttles, and
  etcd wants a 10 ms p99 fsync. Fine for a lab; if it feels slow under load,
  `m6a.xlarge` is non-burstable and still cheaper than `m5.xlarge`.

The saving stops around 22% because NAT, the load balancers and the public IP
come to ~$0.13/h regardless of node size — a quarter of the bill in this
profile. Going below that means changing the architecture, not the instances.

### ⏸️ About "stopping" the instances

Stopping the EC2 instances only saves the compute portion (~$0.86/h).
**Still billing:** the NAT Gateway, the three load balancers, the EBS
volumes, and the hosted zones (~$0.20/h). Also, a stopped-and-restarted
OpenShift cluster usually runs into etcd and certificate-rotation
problems.

**`ocplab destroy` is better than stopping.**

---

## Cost safety net

Three independent mechanisms, managed by **`ocplab safety-net`** but
created **outside of Terraform on purpose**: that way they survive
`ocplab destroy`/`deploy` cycles and keep protecting you even if you
forget everything else.

```bash
./ocplab safety-net apply     # create/ensure all three mechanisms (idempotent)
./ocplab safety-net status    # report what exists, without touching anything
./ocplab safety-net destroy   # tear all three down (rarely needed — see below)
```

### 10.1 🔔 AWS Budget with alerts

Budget `openshift-lab-budget` for **50 USD/month** (configurable via
`cluster.yaml`'s `safetyNet.budgetUsd`) with email alerts at **50%, 80%,
and 100%** to the addresses in `safetyNet.alertEmails`.

> Budgets emails **don't require prior confirmation** (unlike SNS): they
> sit waiting and fire on their own once the threshold is crossed. Not
> receiving anything means you haven't spent that much, not that it's
> misconfigured.

### 10.2 🚨 Budget Action — automatic lockdown at 80%

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

### 10.3 🔌 Killswitch — daily Lambda at 22:00 (Europe/Madrid)

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
