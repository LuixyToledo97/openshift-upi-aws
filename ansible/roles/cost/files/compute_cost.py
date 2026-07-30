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

ec2_hourly = sum(price_for("ec2:" + i["instance_type"]) for i in running)
# EBS bills whether the instance is on or off — every volume found counts,
# not just ones attached to a running instance.
ebs_hourly = sum(v["size"] * price_for("ebs:" + v["type"]) / 730.0 for v in data["volumes"])
nat_hourly = data["nat_count"] * price_for("nat_gateway_hour")
lb_hourly = data["lb_count"] * price_for("nlb_hour")
eip_hourly = data["eip_count"] * price_for("public_ipv4_hour")
total_hourly = ec2_hourly + ebs_hourly + nat_hourly + lb_hourly + eip_hourly

print(json.dumps({
    "running_instances": len(running),
    "stopped_instances": len(stopped),
    "volume_count": len(data["volumes"]),
    "volume_total_gb": sum(v["size"] for v in data["volumes"]),
    "nat_count": data["nat_count"],
    "lb_count": data["lb_count"],
    "eip_count": data["eip_count"],
    "ec2_hourly": round(ec2_hourly, 4),
    "ebs_hourly": round(ebs_hourly, 4),
    "nat_hourly": round(nat_hourly, 4),
    "lb_hourly": round(lb_hourly, 4),
    "eip_hourly": round(eip_hourly, 4),
    "total_hourly": round(total_hourly, 4),
    "missing_prices": sorted(missing),
}))
