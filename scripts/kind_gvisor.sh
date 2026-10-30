#!/usr/bin/env bash
# Installs gVisor (runsc + containerd shim) into kind node containers and registers two runtime handlers:
#   runsc          — gVisor with its own network stack (netstack), what deploy/k8s RuntimeClass `gvisor` uses;
#   runsc-hostinet — gVisor with network=host (syscalls and files still go through the Sentry/gofer, sockets are the
#                    pod netns'). Needed only when the api runs outside the cluster and reaches pods through
#                    `kubectl port-forward`: CRI port-forward dials 127.0.0.1 inside the pod netns, where netstack's
#                    listeners do not exist. In-cluster api/gateway talk to podIP and use plain `runsc`.
# Used by scripts/k8s_kind_e2e.sh when OA_KIND_GVISOR=1.
#   scripts/kind_gvisor.sh <node-container>...
# Binaries come from the official release bucket, pinned by GVISOR_RELEASE and verified against its .sha512;
# they are cached in .tools/gvisor/<release>/<arch>.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RELEASE="${GVISOR_RELEASE:-20261026}"
[ $# -ge 1 ] || { echo "usage: $0 <kind-node-container>..." >&2; exit 2; }

for node in "$@"; do
  arch=$(docker exec "$node" uname -m)   # aarch64 | x86_64 (the node's arch, not the host's)
  cache="$ROOT/.tools/gvisor/$RELEASE/$arch"
  if [ ! -x "$cache/runsc" ]; then
    url="https://storage.googleapis.com/gvisor/releases/release/$RELEASE/$arch"
    mkdir -p "$cache"
    echo "  downloading gVisor $RELEASE/$arch"
    curl -sSfL -o "$cache/gvisor.tar.bz2" "$url/gvisor.tar.bz2"
    curl -sSfL -o "$cache/gvisor.tar.bz2.sha512" "$url/gvisor.tar.bz2.sha512"
    (cd "$cache" && shasum -a 512 -c gvisor.tar.bz2.sha512 >/dev/null) || { echo "gVisor checksum mismatch" >&2; exit 1; }
    tar -xjf "$cache/gvisor.tar.bz2" -C "$cache"
  fi
  docker cp "$cache/runsc" "$node:/usr/local/bin/runsc"
  docker cp "$cache/containerd-shim-runsc-v1" "$node:/usr/local/bin/containerd-shim-runsc-v1"
  docker cp "$cache/gvisor-bin" "$node:/usr/local/bin/gvisor-bin"   # helper binaries runsc execs since 2026 releases
  if ! docker exec "$node" grep -q 'runtimes.runsc]' /etc/containerd/config.toml; then
    printf '[runsc_config]\n  network = "host"\n' | docker exec -i "$node" tee /etc/containerd/runsc-hostinet.toml >/dev/null
    cri='plugins."io.containerd.grpc.v1.cri".containerd.runtimes'
    printf '\n[%s.runsc]\n  runtime_type = "io.containerd.runsc.v1"\n[%s.runsc-hostinet]\n  runtime_type = "io.containerd.runsc.v1"\n[%s.runsc-hostinet.options]\n  TypeUrl = "io.containerd.runsc.v1.options"\n  ConfigPath = "/etc/containerd/runsc-hostinet.toml"\n' \
      "$cri" "$cri" "$cri" | docker exec -i "$node" tee -a /etc/containerd/config.toml >/dev/null
  fi
  docker exec "$node" systemctl restart containerd
  for _ in $(seq 1 30); do docker exec "$node" systemctl is-active --quiet containerd && break; sleep 1; done
  echo "  $node: $(docker exec "$node" runsc --version | head -1)"
done
