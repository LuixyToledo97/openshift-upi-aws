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

---

## 🪞 A registry mirror, to stop paying for the same gigabytes every deploy

**What**: a pull-through cache or mirror registry so the cluster's container
images don't cross the NAT gateway from quay.io on every single deploy.

**Why**: measured on the real bill, 2026-07-26 to 2026-08-03. This is the
largest line item in the whole project, larger than all EC2 compute:

```
EU-NatGateway-Bytes      $8.85     ← ~197 GB processed
Amazon EC2 - Compute     $6.17
EU-EBS:VolumeUsage.gp3   $1.67
EU-NatGateway-Hours      $1.44
```

Per deploy, from the NAT gateway's CloudWatch `BytesInFromDestination` rather
than divided out of the monthly total: **35.2 GB across ten deploys, ≈$1.58
each** — six nodes pulling their own copy of the release payload. Those metrics
outlive the gateway, so the baseline was reconstructed from ten
already-destroyed NAT gateways, one datapoint per deploy. The images arrive from the internet, where
inbound transfer is free; the charge is entirely the NAT gateway's per-GB
processing ("Data processing charges apply for each gigabyte processed through
the NAT gateway regardless of the traffic's source or destination").

For short lab cycles this dominates. A one-hour deploy-test-destroy run costs
roughly $1.06 of infrastructure and $1.50 of traffic — and it puts the Spot
work in perspective, which saves ~$0.11/h of compute.

**Updated 2026-08-03, after trimming capabilities.** Disabling optional
components took a deploy from 35.2 GB to 24.8 GB, so the ceiling for a mirror
is now the ~24.8 GB that remain — mostly the base payload, which each of the
six nodes pulls its own copy of. At $0.045/GB a mirror might save ~$0.90 a
deploy against ~$15/month for a small always-on instance: **break-even is
around seventeen deploys a month**. During active development on this repo
(eleven deploys in nine days) it pays; for occasional lab use it does not.
That makes this a question about how the lab is used, not a technical one.

**Open questions**:
- Where does the mirror live? It only pays off if it survives between deploys,
  which means persistent infrastructure that costs money of its own. An S3-backed
  registry, a small always-on instance, something on the operator's own machine
  reached over the tunnel?
- OpenShift already supports this properly — `ImageContentSourcePolicy` /
  `ImageDigestMirrorSet`, the mechanism behind disconnected installs, and
  `oc adm release mirror`. Reuse that rather than inventing anything.
- Does it belong in `deploy` at all, or is it an opt-in piece like
  `safety-net`? Someone deploying once a month should not pay to maintain a cache.
- Would deduplicating between the six nodes (rather than between deploys) be
  simpler and get most of the benefit?

**Explicitly rejected**: putting the nodes in the public subnet so image pulls
go through the internet gateway, where inbound is free. It would remove the
charge almost entirely and cost only ~$0.025/h in public IPs — but the private
node topology is the thing this repository exists to reproduce, and `ocplab ssh`
is built on it. Cheaper is not the only axis.

---

## 🎯 `oc` points at the wrong cluster, not just the wrong version

**What**: after `ocplab deploy`, typing `oc get nodes` by hand talks to
whatever `~/.kube/config` has as its current context — for Luis on 2026-08-03,
a leftover Docker Desktop cluster. The cluster's kubeconfig is at
`install-dir/auth/kubeconfig`, and only ocplab knows that: every role passes it
explicitly, so the tooling works while the human's shell doesn't.

**Why it matters**: we solved half of this already. `openshift.version` links
`oc` into `.venv/bin` so the *version* matches the cluster. The *cluster* it
points at was left unsolved, and that half is the more dangerous one — the
failure isn't "can't find the cluster", it's connecting to a **different**
cluster in silence. An `oc delete` in that state goes somewhere unintended.

**Options, none decided**:
- Document it and stop there — `export KUBECONFIG=$PWD/install-dir/auth/kubeconfig`.
- Have `ocplab console` print the export line beside the URL and password.
- Have `ocplab setup` append the export to `.venv/bin/activate`, with a matching
  restore in `deactivate`. Best result, most invasive: it changes KUBECONFIG for
  everything in that shell, not just ocplab.

The venv already has to be activated for ocplab to work at all, which is what
makes the third option tempting and also what makes it presumptuous.
