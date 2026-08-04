# Screenshots

Images of the web UI, referenced from [`README.md`](../../../README.md) and
[`docs/web-ui.md`](../../web-ui.md).

**Capture them against a sanitised `cluster.yaml`.** The UI puts real values on
screen — the Overview shows your cluster's fully qualified domain, and the
Configuration tab shows the whole file, hosted zone id included. Those are the
exact things a public repository should not carry:

```bash
cp cluster.yaml /tmp/cluster.yaml.real     # keep yours
cp examples/standard.yaml cluster.yaml     # placeholder values
ocplab web stop && ocplab web start
#  ... capture ...
cp /tmp/cluster.yaml.real cluster.yaml     # put yours back
ocplab web stop && ocplab web start
```

**Capture the page, not the browser.** The URL carries the session token. It
dies with the server, so a leaked one is worthless — but there is no reason to
publish it, and a screenshot with an address bar in it looks unconsidered.

Full-width on a wide window: Actions and Help lay out as four columns above
1250px and collapse to two below it, and the four-column arrangement is the
one worth showing.
