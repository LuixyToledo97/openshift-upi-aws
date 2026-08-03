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
