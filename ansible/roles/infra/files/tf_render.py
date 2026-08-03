#!/usr/bin/env python3
"""Turn `terraform -json` events into a human-readable stream.

Reads terraform's machine-readable output on stdin, writes readable lines to
the file given by --log (which is meant to be followed with `tail -f` while a
long apply runs), optionally keeps the raw JSON with --raw, and prints a JSON
summary on stdout for Ansible to register.

Why this exists: `community.general.terraform` returns everything at once when
it finishes, so a 4-minute apply — or a 15-minute destroy — shows nothing at
all until it's over. The event schema used here was verified against a real
terraform stream, not assumed.

Stdlib only, and it never fails the pipeline: an unparseable line is passed
through verbatim rather than raising, because losing the rendering must not
take down a terraform run that is otherwise fine.
"""

import argparse
import json
import sys

# Read as: aws_nat_gateway -> "NAT gateway". Only types this project actually
# creates need an entry; anything else falls back to a readable form of the
# type name itself, so an unmapped resource still reads fine and this map
# never becomes a maintenance obligation.
FRIENDLY = {
    "aws_vpc": "VPC",
    "aws_subnet": "subnet",
    "aws_internet_gateway": "internet gateway",
    "aws_nat_gateway": "NAT gateway",
    "aws_eip": "elastic IP",
    "aws_route_table": "route table",
    "aws_route_table_association": "route table association",
    "aws_route": "route",
    "aws_security_group": "security group",
    "aws_security_group_rule": "security group rule",
    "aws_iam_role": "IAM role",
    "aws_iam_role_policy": "IAM role policy",
    "aws_iam_instance_profile": "IAM instance profile",
    "aws_lb": "load balancer",
    "aws_lb_target_group": "target group",
    "aws_lb_listener": "listener",
    "aws_lb_target_group_attachment": "target group attachment",
    "aws_route53_zone": "Route53 zone",
    "aws_route53_record": "DNS record",
    "aws_instance": "EC2 instance",
    "aws_s3_bucket": "S3 bucket",
    "aws_s3_object": "S3 object",
}

# Plan-time rendering: terraform's action name is already the infinitive, so
# it needs no conjugating — only a marker and terraform's own word for delete.
PLANNED = {
    "create": ("+", "create"),
    "delete": ("-", "destroy"),
    "update": ("~", "update"),
    "replace": ("±", "replace"),
    "read": ("→", "read"),
    "import": ("←", "import"),
}

# terraform's action verbs -> (present participle, past participle)
ACTIONS = {
    "create": ("creating", "created"),
    "delete": ("destroying", "destroyed"),
    "update": ("updating", "updated"),
    "replace": ("replacing", "replaced"),
    "read": ("reading", "read"),
    "import": ("importing", "imported"),
}


# Acronyms that look wrong in lower case when a type falls through to the
# generic fallback — e.g. aws_s3_bucket_policy rendering as "s3 bucket policy"
# right next to a mapped "S3 object".
ACRONYMS = {"s3", "iam", "vpc", "dns", "elb", "ebs", "ami", "nat", "eip", "acl", "kms", "api"}


def friendly(resource):
    rtype = resource.get("resource_type", "") or ""
    fallback = " ".join(
        word.upper() if word in ACRONYMS else word
        for word in rtype.removeprefix("aws_").split("_")
    )
    label = FRIENDLY.get(rtype) or fallback or "resource"
    name = resource.get("resource_name", "") or ""
    key = resource.get("resource_key")
    if key is not None:
        name = f"{name}[{key}]"
    return f"{label} {name}".strip()


def human_seconds(seconds):
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return ""
    if seconds < 60:
        return f"{seconds}s"
    return f"{seconds // 60}m {seconds % 60:02d}s"


class Renderer:
    def __init__(self, log, raw, plan_only=False):
        self.log = log
        self.raw = raw
        # `planned_change` events are emitted during an apply too, right before
        # the applies themselves. Rendering them there would say "will create
        # X" immediately followed by "creating X" and "created X" — three lines
        # per resource, two of them noise. So they're only rendered when the
        # run is a plan and there is nothing else coming.
        self.plan_only = plan_only
        self.plan = None
        self.result = None
        self.diagnostics = []
        self.changed = 0

    def emit(self, text=""):
        if self.log:
            self.log.write(text + "\n")
            self.log.flush()  # the whole point is that this can be tailed live

    def handle(self, event):
        etype = event.get("type")
        hook = event.get("hook") or {}
        resource = hook.get("resource") or {}
        action = hook.get("action", "")
        present, past = ACTIONS.get(action, (action, action))

        if etype == "planned_change":
            if not self.plan_only:
                return
            change = event.get("change") or {}
            resource = change.get("resource") or {}
            action = change.get("action", "")
            marker, verb = PLANNED.get(action, ("•", action))
            self.emit(f"  {marker} would {verb:<8} {friendly(resource)}")

        elif etype == "change_summary":
            changes = event.get("changes") or {}
            if changes.get("operation") == "plan":
                self.plan = changes
                self.emit()
                self.emit(
                    f"Plan: {changes.get('add', 0)} to add, "
                    f"{changes.get('change', 0)} to change, "
                    f"{changes.get('remove', 0)} to destroy."
                )
                # The separator introduces the per-resource apply stream. On a
                # plan-only run nothing follows it, so it would just dangle.
                if not self.plan_only:
                    self.emit("-" * 64)
            else:
                self.result = changes
                self.emit("-" * 64)
                self.emit(event.get("@message", ""))

        elif etype == "apply_start":
            self.emit(f"  » {present:<10} {friendly(resource)}")

        elif etype == "apply_progress":
            elapsed = human_seconds(hook.get("elapsed_seconds"))
            self.emit(f"    … still {present} {friendly(resource)} ({elapsed})")

        elif etype == "apply_complete":
            self.changed += 1
            elapsed = human_seconds(hook.get("elapsed_seconds"))
            ident = hook.get("id_value") or ""
            # Truncated: some AWS ids (ARNs, LB names) are long enough to wrap
            # the line and ruin the alignment that makes this readable at all.
            if len(ident) > 24:
                ident = ident[:23] + "…"
            self.emit(f"  ✓ {past:<10} {friendly(resource):<44} {ident:<25} {elapsed}")

        elif etype == "apply_errored":
            self.emit(f"  ✗ FAILED {present} {friendly(resource)}")

        elif etype == "diagnostic":
            diag = event.get("diagnostic") or {}
            if diag.get("severity") != "error":
                return
            where = diag.get("address") or ""
            summary = diag.get("summary", "")
            detail = (diag.get("detail") or "").strip()
            self.diagnostics.append(
                {"address": where, "summary": summary, "detail": detail}
            )
            self.emit()
            self.emit(f"  ✗ ERROR {(where + ': ') if where else ''}{summary}")
            for line in detail.splitlines():
                self.emit(f"      {line}")

    def summary(self):
        return {
            "plan": self.plan,
            "result": self.result,
            "diagnostics": self.diagnostics,
            "changed_resources": self.changed,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", help="file to write the readable stream to")
    parser.add_argument("--raw", help="file to keep terraform's raw JSON in")
    parser.add_argument(
        "--plan-only", action="store_true",
        help="the run is a plan, so render the planned changes themselves "
             "(during an apply they are redundant with the apply events)",
    )
    args = parser.parse_args()

    log = open(args.log, "a", encoding="utf-8") if args.log else None
    raw = open(args.raw, "a", encoding="utf-8") if args.raw else None
    renderer = Renderer(log, raw, plan_only=args.plan_only)

    for line in sys.stdin:
        if raw:
            raw.write(line)
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            # Not JSON: terraform occasionally emits plain text (a panic, a
            # provider's own noise). Pass it through rather than swallowing it.
            renderer.emit(line)
            continue
        try:
            renderer.handle(event)
        except Exception as exc:  # noqa: BLE001 - never break the pipeline
            renderer.emit(f"  [tf_render: could not render an event: {exc}]")

    json.dump(renderer.summary(), sys.stdout)
    for handle in (log, raw):
        if handle:
            handle.close()


if __name__ == "__main__":
    main()
