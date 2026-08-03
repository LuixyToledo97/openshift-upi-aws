# Backlog

Ideas, features, and fixes planned for future versions — not committed to any
specific release yet. When something here actually gets built, it moves into
`CHANGELOG.md` under `[Unreleased]` and gets removed from here.

Each entry: what it is, why it matters, and open questions to resolve before
implementing — not a design doc, just enough context to pick it up later
without having to reconstruct the reasoning from scratch.

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
