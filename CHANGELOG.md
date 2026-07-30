# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.0.0] - 2026-07-30

Initial public release.

### Added

- `ocplab` CLI covering the full lifecycle: `init`, `setup`, `validate`,
  `render`, `bootstrap`, `preflight`, `ignition`, `deploy`, `verify`,
  `cost`, `power`, `destroy`, `safety-net`, `status`, `console`, `prereqs`.
- Terraform for UPI infrastructure: VPC, subnets, NAT, IAM, security
  groups, load balancers, Route53 (public + private zones), EC2
  (bootstrap/masters/workers).
- Ansible roles for the full deploy/destroy lifecycle, RHCOS AMI
  auto-discovery, and post-install custom-certificate support
  (bring-your-own CA-signed cert).
- `ocplab verify`: live cluster health check (API reachability,
  `ClusterVersion`, node readiness, `ClusterOperators`), with a fast,
  bounded reachability check before touching the Kubernetes client.
- `ocplab power on|off|status`: graceful cluster shutdown/restart
  following Red Hat's documented procedure, and a read-only power-state
  check — an alternative to `destroy`, not a cost-saving one.
- `ocplab cost`: approximate current USD/hour for whatever's actually
  deployed, power-state aware, with a lazily-populated per-region AWS
  Pricing API cache.
- `ocplab safety-net`: AWS Budget with alerts, an automatic Budget Action
  lockdown at 80%, and a scheduled killswitch Lambda.
- `README.md` and `CLAUDE.md` documentation.
- MIT license.
