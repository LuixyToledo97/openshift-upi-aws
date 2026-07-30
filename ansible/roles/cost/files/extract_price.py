#!/usr/bin/env python3
# Reads the JSON output of 'aws pricing get-products' from stdin and prints
# a single matching on-demand USD price. Needed because the Pricing API's
# PriceList entries are JSON-encoded STRINGS (not nested objects), and the
# actual price sits several levels deep under randomly-keyed 'terms.OnDemand'
# and 'priceDimensions' dicts (SKU/rateCode hashes, not fixed names) — not
# reasonably queryable with Ansible's Jinja/jmespath filters, so this is a
# plain script instead.
import argparse
import json
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--unit", required=True, help="priceDimensions unit to match, e.g. 'Hrs' or 'GB-Mo'")
parser.add_argument("--description-contains", default=None, help="optional substring the price dimension's description must contain")
args = parser.parse_args()

data = json.load(sys.stdin)
for entry in data.get("PriceList", []):
    product = json.loads(entry)
    on_demand = product.get("terms", {}).get("OnDemand", {})
    for term in on_demand.values():
        for dimension in term.get("priceDimensions", {}).values():
            if dimension.get("unit") != args.unit:
                continue
            description = dimension.get("description", "")
            if args.description_contains and args.description_contains not in description:
                continue
            price = dimension.get("pricePerUnit", {}).get("USD")
            if price is not None:
                print(price)
                sys.exit(0)

sys.exit(1)
