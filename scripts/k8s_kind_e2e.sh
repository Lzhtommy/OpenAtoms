#!/usr/bin/env bash
# Verifies the K8sProvider end to end on a throwaway kind cluster (PLAN P8):
#   kind cluster (+ host data dir mounted) → load sandbox image → namespaces/RBAC/PV/PVC from deploy/ →
#   api (lite infra) on the host with OA_SANDBOX_PROVIDER=k8s via a ServiceAccount token + port-forward →
#   scripts/e2e.py → warm-pool wake measurement → teardown.
# Requires: docker, kubectl, kind (KIND=… or ./.tools/kind). Ports: OA_API_PORT/OA_GATEWAY_PORT (default 8400/8480).
# KEEP=1 keeps the cluster afterwards. OA_KIND_GVISOR=1 installs gVisor (scripts/kind_gvisor.sh) in the node and
#  1. checks the production path: a sandboxd pod with RuntimeClass `gvisor` (netstack) answers /health and /exec on its
#     podIP from another pod (in-cluster api/gateway reach sandboxes exactly like this);
#  2. runs every e2e sandbox pod under gVisor. The api here runs on the host and reaches pods via kubectl
#     port-forward, which cannot reach netstack listeners, so these pods use RuntimeClass `gvisor-hostinet`
#     (same Sentry/gofer isolation, host-netns sockets); the script asserts they really run on runsc.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
KIND="${KIND:-$ROOT/.tools/kind}"
CLUSTER="${CLUSTER:-oa-prod}"
WORK="${WORK:-$(mktemp -d)}"
export OA_DATA_DIR="${OA_DATA_DIR:-$WORK/data}"
API_PORT="${OA_API_PORT:-8400}" GW_PORT="${OA_GATEWAY_PORT:-8480}"
mkdir -p "$OA_DATA_DIR"
KCFG="$WORK/kubeconfig"
STACK_PID=""
cleanup() {
  [ -n "$STACK_PID" ] && kill "$STACK_PID" 2>/dev/null || true
  for port in "$API_PORT" "$GW_PORT"; do lsof -nP -iTCP:$port -sTCP:LISTEN -t 2>/dev/null | xargs -r kill 2>/dev/null || true; done
  pkill -f "kubeconfig $KCFG" 2>/dev/null || true
  if [ "${KEEP:-0}" != "1" ]; then "$KIND" delete cluster --name "$CLUSTER" >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT

echo "→ kind cluster $CLUSTER (data: $OA_DATA_DIR)"
OA_DATA_HOST_DIR="$OA_DATA_DIR" envsubst < deploy/kind/kind-config.yaml.tmpl > "$WORK/kind.yaml"
"$KIND" create cluster --name "$CLUSTER" --config "$WORK/kind.yaml" --kubeconfig "$KCFG" --wait 120s
K="kubectl --kubeconfig $KCFG"
GVISOR="${OA_KIND_GVISOR:-0}"
if [ "$GVISOR" = "1" ]; then
  echo "→ gVisor runtime in the kind node"
  ./scripts/kind_gvisor.sh $("$KIND" get nodes --name "$CLUSTER")
fi
echo "→ validate production manifests against the live API server"
$K apply -k deploy/k8s --dry-run=client >/dev/null
echo "  client dry-run ok"
echo "→ load image (this copies ~1 GB into the node)"
t0=$(date +%s); "$KIND" load docker-image openatoms/sandbox:latest --name "$CLUSTER"; echo "  loaded in $(( $(date +%s) - t0 ))s"
$K apply -f deploy/k8s/namespace.yaml -f deploy/k8s/sandbox-policy.yaml -f deploy/k8s/rbac.yaml
$K apply -k deploy/k8s --dry-run=server >/dev/null   # full production set validated by the API server
echo "  server dry-run ok"
$K apply -f deploy/kind/workspaces-pv.yaml

echo "→ service account token + api server for the host-run api"
export OA_K8S_TOKEN="$($K -n openatoms create token oa-sandbox-manager --duration=2h)"
export OA_K8S_API="$($K config view --minify -o jsonpath='{.clusters[0].cluster.server}')"
$K config view --minify --raw -o jsonpath='{.clusters[0].cluster.certificate-authority-data}' | base64 -d > "$WORK/ca.crt"
RUNTIME_CLASS=""
if [ "$GVISOR" = "1" ]; then
  RUNTIME_CLASS=gvisor-hostinet
  # gVisor enforces file permissions in its Sentry. Files written by the host-run api (macOS uid 501) show up as
  # root:0755 inside the kind node (Docker Desktop file sharing); runc gets away with uid 1000 only because that share
  # ignores permissions. In production api/worker run as uid 1000 like the sandboxes (deploy/k8s), so only this
  # harness runs sandbox pods as root.
  export OA_K8S_RUN_AS_USER=0
  printf 'apiVersion: node.k8s.io/v1\nkind: RuntimeClass\nmetadata: { name: gvisor-hostinet }\nhandler: runsc-hostinet\n' | $K apply -f -
  echo "→ production path: sandboxd under RuntimeClass gvisor (netstack), reached by podIP from another pod"
  $K apply -f deploy/kind/gvisor-check.yaml >/dev/null
  $K wait --for=condition=Ready pod/gvisor-check pod/gvisor-check-client --timeout=180s >/dev/null
  ip=$($K get pod gvisor-check -o jsonpath='{.status.podIP}')
  out=$($K exec gvisor-check-client -- curl -sf -m 20 -H 'X-Sandbox-Token: check' -H 'Content-Type: application/json' \
        -d '{"cmd":"uname -r && node -v && pnpm -v"}' "http://$ip:49999/exec")
  echo "$out" | grep -q '4.19.0-gvisor' || { echo "netstack gVisor sandbox not reachable/not gVisor: $out"; exit 1; }
  echo "  podIP $ip: $(echo "$out" | grep -o '"data":"[^"]*"' | tr '\n' ' ')"
  $K delete -f deploy/kind/gvisor-check.yaml --wait=false >/dev/null
fi
export OA_K8S_CA="$WORK/ca.crt" OA_K8S_ACCESS=portforward OA_K8S_RUNTIME_CLASS="$RUNTIME_CLASS" OA_SANDBOX_PROVIDER=k8s
export OA_KUBECTL="$WORK/kubectl-kind"
printf '#!/bin/sh\nexec kubectl --kubeconfig %s "$@"\n' "$KCFG" > "$OA_KUBECTL"; chmod +x "$OA_KUBECTL"
# The SA token must allow what the provider does:
for verb in "create pods" "delete pods" "create pods/portforward"; do
  $K auth can-i $verb -n openatoms-sandboxes --as system:serviceaccount:openatoms:oa-sandbox-manager | grep -q yes \
    || { echo "RBAC missing: $verb"; exit 1; }
done

echo "→ api + gateway (provider=k8s, warm pool ${OA_SANDBOX_WARM_POOL:=2})"
# the scripted model only: never spend a real provider's credits from a .env (dev.sh lets these win over .env)
export OA_DEFAULT_MODEL=oa-mock OA_BASIC_MODEL=oa-mock OA_LLM_API_KEY=
export OA_SANDBOX_WARM_POOL OA_API_PORT="$API_PORT" OA_GATEWAY_PORT="$GW_PORT" OA_SKIP_WEB=1 OA_RATE_LIMIT=0 \
       OA_MOCK_DELAY="${OA_MOCK_DELAY:-0.003}"
./scripts/dev.sh > "$WORK/stack.log" 2>&1 &
STACK_PID=$!
for _ in $(seq 1 60); do curl -sf --noproxy '*' "http://127.0.0.1:$API_PORT/api/v1/health" >/dev/null && break; sleep 1; done
echo "→ waiting for warm pool pods"
for _ in $(seq 1 90); do
  n=$($K -n openatoms-sandboxes get pods -l openatoms.pool=1 --field-selector=status.phase=Running -o name | wc -l | tr -d ' ')
  [ "$n" -ge "$OA_SANDBOX_WARM_POOL" ] && break; sleep 2
done
$K -n openatoms-sandboxes get pods -o wide
if [ "$GVISOR" = "1" ]; then
  echo "→ sandbox pods run under gVisor"
  pod=$($K -n openatoms-sandboxes get pods -l openatoms.pool=1 -o jsonpath='{.items[0].metadata.name}')
  rc=$($K -n openatoms-sandboxes get pod "$pod" -o jsonpath='{.spec.runtimeClassName}')
  kver=$($K -n openatoms-sandboxes exec "$pod" -- cat /proc/version)
  echo "  $pod runtimeClassName=$rc kernel: $kver"
  [ "$rc" = "$RUNTIME_CLASS" ] && echo "$kver" | grep -q gvisor || { echo "sandbox pod is not running under gVisor"; exit 1; }
  $K -n openatoms-sandboxes exec "$pod" -- dmesg 2>/dev/null | head -2 | sed 's/^/  /' || true
fi

echo "→ e2e (lite infra, k8s sandboxes)"
uv run scripts/e2e.py --mode lite --api "http://127.0.0.1:$API_PORT" --gateway "http://127.0.0.1:$GW_PORT" \
  || { tail -60 "$WORK/stack.log"; exit 1; }
$K -n openatoms-sandboxes get pods
if [ "$GVISOR" = "1" ]; then
  n=$($K -n openatoms-sandboxes get pods -o jsonpath='{range .items[*]}{.spec.runtimeClassName}{"\n"}{end}' | grep -vc "^$RUNTIME_CLASS\$" || true)
  [ "$n" = 0 ] || { echo "$n sandbox pod(s) without RuntimeClass gvisor"; exit 1; }
  echo "  all sandbox pods (pool slots and project sandboxes) use RuntimeClass $RUNTIME_CLASS"
fi
echo "→ wake latency (pool size $OA_SANDBOX_WARM_POOL)"
uv run scripts/measure_sandbox_start.py --api "http://127.0.0.1:$API_PORT" --projects 2 --settle 40 \
  | tee "$WORK/k8s-start-warm.json"
echo "✓ kind e2e passed (logs: $WORK)"
