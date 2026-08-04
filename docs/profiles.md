# Profiles: standard vs minimal

> Part of the [ocplab](../README.md) documentation.

Two starting configurations ship with the project, and `ocplab init` writes one
of them verbatim:

```bash
ocplab init            # examples/standard.yaml
ocplab init --minimal  # examples/minimal.yaml
```

They deploy the **same cluster topology** — 3 control plane, 2 compute, 1
temporary bootstrap, one AZ. What differs is what that topology is made of and
what it costs. Everything here is a `cluster.yaml` field, so a profile is a
starting point rather than a mode: mix them freely.

---

## At a glance

| | standard | minimal |
|---|---|---|
| Control plane | 3 × `m5.xlarge` | 3 × `t3.xlarge` |
| Compute | 2 × `m5.large` | 2 × `t3.large`, **Spot** |
| Bootstrap | `m5.xlarge` | `t3.xlarge`, **Spot** |
| Disks | 120 GB gp3 | 100 GB gp3 |
| Capabilities | installer default (`vCurrent`) | trimmed (`None` + 8) |
| ClusterOperators | 34 | 27 |
| **Cost, running** | **~$1.06/h** | **~$0.83/h** |
| **Data per deploy** | **~35.2 GB (≈$1.58)** | **~24.8 GB (≈$1.12)** |

Both figures on the last two rows are measured, not estimated — see
[Costs](costs.md).

---

## Topology

Identical in both. The difference is the instance types inside the boxes.

### standard

```
                          Internet
                              │
              ┌───────────────┼───────────────┐
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
        │   · bootstrap   m5.xlarge   (temporary)      │
        └──────────────────────┬───────────────────────┘
                               │ outbound via NAT
        ┌──────────────────────┴───────────────────────┐
        │  Private subnet  10.0.2.0/24  (eu-west-1a)   │
        │   · master-0/1/2   m5.xlarge      on-demand  │
        │   · worker-0/1     m5.large       on-demand  │
        │   · NLB api-int   :6443  :22623              │
        └──────────────────────────────────────────────┘
```

### minimal

```
                          Internet
                              │
              ┌───────────────┼───────────────┐
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
        │   · bootstrap   t3.xlarge   SPOT ⚡          │
        └──────────────────────┬───────────────────────┘
                               │ outbound via NAT
        ┌──────────────────────┴───────────────────────┐
        │  Private subnet  10.0.2.0/24  (eu-west-1a)   │
        │   · master-0/1/2   t3.xlarge      on-demand  │
        │   · worker-0/1     t3.large       SPOT ⚡     │
        │   · NLB api-int   :6443  :22623              │
        └──────────────────────────────────────────────┘

        ⚡ Spot: reclaimable by AWS at any time.
           A lost worker is recovered with `ocplab repair`.
```

**The control plane is never Spot, and `validate` refuses it.** UPI has no
`Machine` objects and therefore no ControlPlaneMachineSet, so a reclaimed
master is never replaced automatically — recovering one means repairing etcd
membership by hand. Losing a worker only reschedules pods.

---

## Components

| Component | standard | minimal | Created by |
|---|---|---|---|
| Bootstrap (temporary) | 1 × `m5.xlarge` | 1 × `t3.xlarge` **Spot** | Terraform |
| Control plane | 3 × `m5.xlarge` | 3 × `t3.xlarge` | Terraform |
| Compute | 2 × `m5.large` | 2 × `t3.large` **Spot** | Terraform |
| Root disks | 120 GB gp3 | 100 GB gp3 | Terraform |
| NAT Gateway + EIP | 1 | 1 | Terraform |
| External API NLB | 1 | 1 | Terraform |
| Internal API NLB | 1 | 1 | Terraform |
| Router ELB (`*.apps`) | 1 | 1 | **the cluster** ⚠️ |
| Security group `k8s-elb-*` | 1 | 1 | **the cluster** ⚠️ |
| `*.apps` records | 2 | 2 | **the cluster** ⚠️ |
| Internal image registry | ✅ | ❌ | — |
| OperatorHub catalogues | ✅ | ❌ | — |
| Insights / node-tuning | ✅ | ❌ | — |

⚠️ Created by the `ingress-operator` outside Terraform in both profiles. See
[Troubleshooting](troubleshooting.md).

---

## `cluster.yaml`

Only the parts that differ. Everything else — `metadata`, `platform`,
`networking`, `pullSecret`, `sshKey` — is identical.

### standard

```yaml
controlPlane:
  replicas: 3
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

# openshift: omitted entirely — the binaries on your PATH are used,
# and the installer's default capability set (vCurrent) applies.
```

### minimal

```yaml
controlPlane:
  replicas: 3
  instanceType: t3.xlarge      # never Spot: validate refuses it
  volumeSize: 100
  volumeType: gp3

compute:
  replicas: 2
  instanceType: t3.large
  volumeSize: 100
  volumeType: gp3
  spot: true                   # reclaimable; recover with `ocplab repair`

bootstrap:
  instanceType: t3.xlarge
  volumeSize: 100
  volumeType: gp3
  spot: true                   # destroyed at the end of the install anyway

openshift:
  version: 4.22.6              # pins the client, installer AND the RHCOS AMI
  capabilities:
    baselineCapabilitySet: None
    additionalEnabledCapabilities:
      - CloudCredential          # mandatory on AWS
      - CloudControllerManager   # mandatory on AWS
      - Ingress                  # mandatory on AWS
      - MachineAPI
      - Console
      - Storage
      - CSISnapshot
      - OperatorLifecycleManager
```

The three marked mandatory are the installer's own rule, not a preference.
Asked for `None` with nothing else, 4.22.7 refuses outright. `ocplab validate`
enforces it so the failure lands there rather than several commands later.

---

## Cost

### Hourly, while running (`eu-west-1`)

| Resource | standard | minimal |
|---|---:|---:|
| Control plane | 3 × 0.214 = **0.642** | 3 × 0.1856 = **0.557** |
| Compute | 2 × 0.107 = **0.214** | 2 × 0.044 = **0.088** ⚡ |
| NAT Gateway | **0.052** | **0.048** |
| API NLBs (2) | **0.050** | **0.050** |
| Router ELB | **0.028** | **0.028** |
| EBS gp3 | **0.070** | **0.060** |
| Public IPv4 | **0.005** | **0.005** |
| **Total** | **~$1.06/h** | **~$0.83/h** |

⚡ Spot at the rate measured in `eu-west-1a` on 2026-08-04: **52.6% below
on-demand**, inside the 50-55% band this project has consistently seen — and
well short of the 70-90% often quoted. `ocplab cost` prices Spot live and
labels it, because a Spot figure is a snapshot of something that moves hourly.

### By uptime

| Uptime | standard | minimal |
|---|---:|---:|
| 1 hour | $1.06 | $0.83 |
| 4 hours | $4.24 | $3.32 |
| 8 hours (a working day) | $8.48 | $6.64 |
| 24 hours | $25.44 | $19.92 |
| A week, left up | $178 | $139 |

### The charge neither table shows

Every deploy pulls container images from quay.io through the NAT gateway, and
NAT charges per gigabyte processed regardless of direction:

| | standard | minimal |
|---|---:|---:|
| Data per deploy | **35.2 GB** | **24.8 GB** |
| Cost per deploy | **≈$1.58** | **≈$1.12** |

**For a short cycle this dominates.** A one-hour deploy-test-destroy run on the
standard profile is roughly $1.06 of infrastructure and $1.58 of traffic — the
traffic costs more than everything else combined, and `ocplab cost` cannot see
it, because it prices resources and this is usage.

Both numbers come from the NAT gateway's CloudWatch `BytesInFromDestination`
metric, one datapoint per deploy, not from dividing a monthly bill. Trimming
capabilities is what closes the gap: **29% less data, about $0.46 a deploy**.

---

## Which to pick

**minimal**, unless you need what it removes. It is the profile this project is
developed against, and the one whose numbers above were measured.

**standard** when you want the cluster a real installation would give you: an
internal image registry, S2I builds, the OperatorHub catalogues, Insights and
node tuning. Those are genuine functionality, not padding — `minimal` finishes
with 27 healthy ClusterOperators against `standard`'s 34, and the seven missing
are seven things you cannot then use.
