# Troubleshooting and lessons learned

> Part of the [ocplab](../README.md) documentation.

## Troubleshooting and lessons learned

The core issues that cost real debugging time. **All of them are already
fixed in the code**, but it's worth understanding them.

### 11.1 ⭐ The `kubernetes.io/cluster/<infraID>` tag — main root cause

**Symptom:** the cluster gets stuck at
`Working towards 4.22.6: 80 of 1015 done (7% complete)` for hours. Nodes
stay `NotReady`. `network-operator` and `etcd-operator` stay `Pending`
forever:

```
0/3 nodes are available: 3 node(s) had untolerated taint(s)
```

**Diagnosis:**

```bash
oc get nodes -o json | jq -r '.items[] | .metadata.name, (.spec.taints[]? | "  \(.key)")'
# → node.cloudprovider.kubernetes.io/uninitialized
```

And in the CCM logs (via SSH into the node, since `oc logs` doesn't work
yet):

```
E tags.go:99] Tag "KubernetesCluster" nor "kubernetes.io/cluster/..." not found
F main.go:104] Cloud provider could not be initialized: AWS cloud failed to find ClusterID
```

**Explanation:** AWS boots the nodes with the
`node.cloudprovider.kubernetes.io/uninitialized:NoSchedule` taint. Only
`aws-cloud-controller-manager` can remove it, and to do so it checks its
own EC2 instance's tags looking for `kubernetes.io/cluster/*`. Without
that tag it goes into `CrashLoopBackOff` → the taint never gets removed →
nothing gets scheduled → **total circular deadlock**.

**Fix:** `default_tags` in `providers.tf` with the infraID read from
`metadata.json`.

### 11.2 🔍 etcd discovery via DNS

**Symptom:** the masters never form quorum. No
`/etc/kubernetes/manifests/etcd-pod.yaml`. `oc get machineconfig | grep etcd`
is empty.

**Explanation:** in UPI, etcd members discover each other via an SRV
record `_etcd-server-ssl._tcp.<cluster>.<domain>` that points to three A
records `etcd-0/1/2`.

```bash
dig SRV _etcd-server-ssl._tcp.ocp4lab.aws.example.com +short
dig A etcd-0.ocp4lab.aws.example.com +short
```

**Fix:** the `etcd_a` and `etcd_srv` resources in `route53.tf`.

### 11.3 The private zone shadows the public one

**Symptom:** after creating the private hosted zone to fix the ingress,
`cluster-version-operator` goes into `CrashLoopBackOff`:

```
lookup api-int.ocp4lab.aws.example.com on 10.0.0.2:53: no such host
F start.go:27] error: error processing feature gates: failed to sync informer cache
```

**Explanation:** a private hosted zone associated with a VPC takes
priority over the public one **inside** that VPC, for its entire subtree.
If it only contains `*.apps`, the rest of the names (`api`, `api-int`,
`etcd-*`) stop resolving from the pods.

**Fix:** replicate `api`, `api-int`, `etcd-N`, and the SRV record in the
private zone too.

**Check from inside the cluster:**

```bash
oc debug node/<node> -- chroot /host dig +short api-int.ocp4lab.aws.example.com
```

### 11.4 ⏳ Ignition certificates — expire after 24h

```
ERROR Bootstrap Ignition-Config Certificate aggregator-ca.crt expired at ...
FATAL failed to fetch Bootstrap Ignition Config: 13 certificates expired
WARNING Please regenerate ignition configuration files in a new directory.
```

**Fix:** the `ignition` role always regenerates in a clean directory (the
old `install-dir`'s internal state is what makes the installer reuse
expired certificates) — this is automatic in `ocplab deploy`.

### 11.5 🛑 `control-plane-machine-set` stuck `Degraded`

Normal in UPI: there are no `Machine` objects for the masters (Terraform
created them). Blocks `wait-for install-complete` — the `finalize` role
deletes it automatically; see [Deploying](lifecycle.md#deploying-the-cluster).

---

### ❓ Other common issues

#### 🐍 `ocplab preflight`/`deploy`/etc. fail with "couldn't run 'ansible-playbook'"

The venv isn't activated: `source .venv/bin/activate`. `ocplab` detects
this specific case and says so explicitly, but if the message instead
points at some *other* `ansible-playbook` (e.g. from a different project
or an editor extension), check `which -a ansible-playbook` — it means
something earlier in your `PATH` is shadowing the venv's.

#### 🔍 `oc` responds with `nodes "..." not found` or `No resources found`

That terminal is talking to a **different cluster** — usually a leftover
Docker Desktop or minikube context in `~/.kube/config`. Check with
`ocplab status`, which reports where your shell points, or:

```bash
eval "$(ocplab env)"
```

Activating the venv normally does this for you (see [Which cluster `oc` talks
to](cli.md#-which-cluster-oc-talks-to)). If it didn't, either you have `KUBECONFIG`
set to something else — which ocplab deliberately never overrides — or the
venv predates that behaviour, in which case run `ocplab setup` once.

#### 🔍 `oc` responds with `connection to the server localhost:8080 was refused`

The opposite problem, and a much safer one: `KUBECONFIG` points at this
cluster's kubeconfig, but `install-dir/auth/kubeconfig` doesn't exist yet. The
cluster hasn't been deployed (or `install-dir` was archived). Run
`ocplab deploy`, or `ocplab status` to see what state `install-dir` is in.

#### 🔑 `Permission denied (publickey)` when hopping from the bootstrap to a master

`ssh -A` forwards the **agent**, and the agent only forwards keys loaded
with `ssh-add` (having `-i` isn't enough):

```bash
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/id_ed25519
ssh-add -l
ssh -A -i ~/.ssh/id_ed25519 core@<BOOTSTRAP_PUBLIC_IP>
ssh core@<MASTER_PRIVATE_IP>
```

The RHCOS user is always **`core`**, with passwordless `sudo`.

#### 🌐 `authentication` or `console` reports `False` with `no such host`

CoreDNS cached a negative response while DNS was still incomplete. Usually
recovers on its own within a few minutes. To force it:

```bash
oc delete pods -n openshift-dns -l dns.operator.openshift.io/daemonset-dns=default
```

#### 🔀 The `*.apps` record doesn't get created

Check that the cluster sees both zones:

```bash
oc get dns.config/cluster -o yaml | grep -A6 -E "publicZone|privateZone"
```

Should show `privateZone.tags.Name = <infraID>-int` and `publicZone.id`.

#### 📡 `RetrievedUpdates: False — version not found in the "stable-4.22" channel`

Cosmetic: that version isn't published on that update channel yet. Doesn't
affect the cluster.

---

### 🩺 Diagnostic commands

```bash
# Overall status
oc get clusterversion
oc get co | grep -v "True.*False.*False"     # only the unhealthy ones

oc get nodes -o wide

# Real detail when the CVO reports MultipleErrors
oc get clusterversion version -o jsonpath='{range .status.conditions[*]}{.type}{"\t"}{.status}{"\t"}{.message}{"\n"}{end}'
oc logs -n openshift-cluster-version -l k8s-app=cluster-version-operator --tail=100

# Node taints
oc get nodes -o json | jq -r '.items[] | .metadata.name, (.spec.taints[]? | "  \(.key)=\(.value):\(.effect)")'

# Target group health
aws elbv2 describe-target-health --target-group-arn <ARN> --profile openshift-lab

# Inside a node (via SSH)
sudo crictl ps -a
sudo crictl logs --tail=60 <CONTAINER_ID>
sudo journalctl -u kubelet --no-pager | tail -50
sudo systemctl status crio

# Continuous monitoring
watch -n 30 'oc get clusterversion; echo; oc get co | grep -v "True.*False.*False"'
```

---
