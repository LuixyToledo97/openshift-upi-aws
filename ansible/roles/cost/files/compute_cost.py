#!/usr/bin/env python3
# Takes the discovered-resources + prices JSON (written to a temp file by
# refresh_pricing.yml/compute_report.yml) and computes the hourly cost
# breakdown. Plain Python instead of Jinja: this involves dynamic
# dict-key lookups (price per instance/volume type) and running sums that
# Jinja2 handles awkwardly at best — same reasoning as extract_price.py.
import json
import sys

data = json.load(open(sys.argv[1]))
prices = data["prices"]
missing = set()


def price_for(key):
    if key not in prices or prices[key] == "":
        missing.add(key)
        return 0.0
    return float(prices[key])


running = [i for i in data["instances"] if i["state"] == "running"]
stopped = [i for i in data["instances"] if i["state"] != "running"]

# AWS sets InstanceLifecycle to "spot" only on Spot instances; on-demand ones
# have no such field, so json_query hands us null rather than omitting the key.
spot_prices = data.get("spot_prices") or {}


def is_spot(instance):
    return (instance.get("lifecycle") or "") == "spot"


def instance_price(instance):
    """Spot instances cost the Spot price, not the on-demand one.

    Pricing them at on-demand inflated a measured $0.8269/h cluster to
    $0.9213/h — 11.4% — and did it in the direction that makes running on Spot
    look less worthwhile than it is. Falls back to on-demand when the Spot
    price couldn't be fetched, which overstates rather than understates: better
    to quote too much than to quietly promise a discount that wasn't checked.
    """
    if is_spot(instance) and instance["instance_type"] in spot_prices:
        return float(spot_prices[instance["instance_type"]])
    return price_for("ec2:" + instance["instance_type"])


on_demand_running = [i for i in running if not is_spot(i)]
spot_running = [i for i in running if is_spot(i)]
ec2_ondemand_hourly = sum(instance_price(i) for i in on_demand_running)
ec2_spot_hourly = sum(instance_price(i) for i in spot_running)
ec2_hourly = ec2_ondemand_hourly + ec2_spot_hourly
# EBS bills whether the instance is on or off — every volume found counts,
# not just ones attached to a running instance.
ebs_hourly = sum(v["size"] * price_for("ebs:" + v["type"]) / 730.0 for v in data["volumes"])
nat_hourly = data["nat_count"] * price_for("nat_gateway_hour")
nlb_hourly = data["lb_count"] * price_for("nlb_hour")
# Kept separate from the NLBs rather than lumped into one "load balancers"
# figure: they're a different resource at a different rate, created by a
# different thing (the ingress-operator, not Terraform), and merging them is
# how the Classic one went unnoticed in the first place.
clb_hourly = data["classic_lb_count"] * price_for("clb_hour")
lb_hourly = nlb_hourly + clb_hourly
eip_hourly = data["eip_count"] * price_for("public_ipv4_hour")
total_hourly = ec2_hourly + ebs_hourly + nat_hourly + lb_hourly + eip_hourly

print(json.dumps({
    "running_instances": len(running),
    "stopped_instances": len(stopped),
    "ondemand_instances": len(on_demand_running),
    "spot_instances": len(spot_running),
    "ec2_ondemand_hourly": round(ec2_ondemand_hourly, 4),
    "ec2_spot_hourly": round(ec2_spot_hourly, 4),
    # Spot instances whose current price could not be fetched, and are
    # therefore counted at the on-demand rate.
    "spot_unpriced": sorted({
        i["instance_type"] for i in spot_running if i["instance_type"] not in spot_prices
    }),
    "volume_count": len(data["volumes"]),
    "volume_total_gb": sum(v["size"] for v in data["volumes"]),
    "nat_count": data["nat_count"],
    "lb_count": data["lb_count"],
    "classic_lb_count": data["classic_lb_count"],
    "eip_count": data["eip_count"],
    "ec2_hourly": round(ec2_hourly, 4),
    "ebs_hourly": round(ebs_hourly, 4),
    "nat_hourly": round(nat_hourly, 4),
    "lb_hourly": round(nlb_hourly, 4),
    "classic_lb_hourly": round(clb_hourly, 4),
    "eip_hourly": round(eip_hourly, 4),
    "total_hourly": round(total_hourly, 4),
    "missing_prices": sorted(missing),
}))
