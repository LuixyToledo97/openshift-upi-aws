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
each** — six nodes each pulling images for themselves. Those metrics
outlive the gateway, so the baseline was reconstructed from ten
already-destroyed NAT gateways, one datapoint per deploy. The images arrive from the internet, where
inbound transfer is free; the charge is entirely the NAT gateway's per-GB
processing ("Data processing charges apply for each gigabyte processed through
the NAT gateway regardless of the traffic's source or destination").

For short lab cycles this dominates. A one-hour deploy-test-destroy run costs
roughly $1.06 of infrastructure and $1.50 of traffic — and it puts the Spot
work in perspective, which saves ~$0.11/h of compute.

**"Six identical copies" is an assumption, not a measurement.** The total is
measured; how it splits across the nodes is not. The sets almost certainly
overlap rather than match — three masters pull the whole control plane, two
workers pull far less, the bootstrap pulls the payload — which is consistent
with the 24.8 GB but implies a different shape. It does not change the case
for a cache (all of it crosses the NAT either way, and an in-VPC source
removes all of it), but it does change any design that tries to deduplicate
*between the nodes* instead. One deploy with per-instance CloudWatch metrics
would settle it.

**Updated 2026-08-03, after trimming capabilities.** Disabling optional
components took a deploy from 35.2 GB to 24.8 GB, so the ceiling for a mirror
is now the ~24.8 GB that remain.

### The design: ECR pull-through cache, not a mirror instance

The first pass at this assumed a mirror meant persistent infrastructure of its
own — a small always-on instance at ~$15/month, which put **break-even at
around seventeen deploys a month** and made this a question about how the lab
is used rather than a technical one. Amazon ECR removes that premise, and the
conclusion flips with it.

- **ECR stores image layers in S3, and the S3 *gateway* endpoint is free** —
  no hourly rate, no per-GB processing. That is where the bytes go, so the
  expensive part of the transfer stops costing anything. This is the whole
  finding; everything else follows from it.
- The ECR *interface* endpoints (`ecr.api`, `ecr.dkr`) do cost ~$0.011/h each,
  but Terraform owns them, so they exist only while the cluster does: **~$0.02
  over a one-hour cycle**. Worth confirming whether they are needed at all —
  without them the control-plane calls (auth, manifests) fall back to the NAT
  gateway, which is a few MB, not gigabytes.
- **ECR supports `quay.io` as a pull-through cache upstream**, with the
  upstream credentials held in Secrets Manager. So nothing has to be uploaded
  by hand: the first deploy pulls through ECR, which fetches from quay on
  AWS's side rather than across the NAT gateway, and later deploys hit the
  cache. That also removes the "mirror 15 GB from a domestic upload link"
  problem, which would have taken hours.
- Storage is ~$0.10/GB-month, so one cached release payload is **~$1.50/month**
  with a lifecycle policy to keep it from accumulating versions.

**Recalculated break-even: about 1.5 deploys a month** against ~$1.12 saved
per deploy (24.8 GB × $0.045). At that point it pays for essentially any use,
which is the opposite of the earlier conclusion.

These are design figures from the AWS documentation, **not measured** — unlike
every other number in this entry, which came from CloudWatch. Nothing here has
been built or tested.

### Why it wasn't built yet: the images are needed before the cluster exists

`ecr-credential-provider`, the kubelet plugin that is the correct way to
authenticate against ECR, is configured through a `MachineConfig` — which
needs a running cluster. The bootstrap and the masters pull their images from
Ignition's baked-in pull secret, long before any of that exists.

Which leaves a static ECR token in the pull secret at `ignition` time, and
**ECR tokens expire after 12 hours**. For a deploy-test-destroy cycle that is
irrelevant. For a cluster left up overnight it is a silent time bomb: any pod
needing an image not already on its node fails to pull, with nothing
connecting the failure to a token. Applying the credential provider by
`MachineConfig` during `finalize` fixes it, but that is another moving part on
the deploy's critical path.

**Full scope**: pull-through rule + Secrets Manager secret, the VPC endpoints
in Terraform, `imageDigestSources` in the install-config, an augmented pull
secret in `ignition`, the credential-provider `MachineConfig` in `finalize`,
an ECR lifecycle policy, and the documentation. That is a major-version
feature, and it touches the one path with no margin for a new failure mode.

**Open questions**:
- Does it belong in `deploy` at all, or is it opt-in like `safety-net`?
- Is the release payload reachable purely through the `quay.io` pull-through
  rule, or do some images come from `registry.redhat.io` and need a second one?
- Do the ECR interface endpoints earn their $0.02, or is the NAT fine for the
  control-plane calls?

**Explicitly rejected**: putting the nodes in the public subnet so image pulls
go through the internet gateway, where inbound is free. It would remove the
charge almost entirely and cost only ~$0.025/h in public IPs — but the private
node topology is the thing this repository exists to reproduce, and `ocplab ssh`
is built on it. Cheaper is not the only axis.
