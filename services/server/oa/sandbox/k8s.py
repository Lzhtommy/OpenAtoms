"""K8sProvider (ARCHITECTURE §8.1 production): one Pod per chat, workspace on a shared RWX PVC via subPath,
optional gVisor RuntimeClass, resource limits, no service-account token, warm-pool slots.

API access (plain REST, no client library):
  in-cluster  : service-account token + CA from /var/run/secrets/kubernetes.io/serviceaccount
  out-of-cluster (dev, e.g. kind): OA_K8S_API + OA_K8S_TOKEN (+ OA_K8S_CA path or OA_K8S_INSECURE=1)

Pod reachability (OA_K8S_ACCESS):
  cluster     (default in-cluster) : talk to podIP:49999 / podIP:5173 directly
  portforward (default out-of-cluster): `kubectl port-forward pod/<name> :49999 :5173` per pod (OA_KUBECTL)
                Does not work with gVisor's netstack (CRI port-forward dials 127.0.0.1 in the pod netns, where netstack
                listeners don't exist): use a network=host runsc handler for that (scripts/kind_gvisor.sh).

The api/worker must mount the same PVC at settings.data_dir so workspaces are shared (storage/compute separation).
Pods mount <pvc>/<workspace path relative to data_dir> at /workspace (pool slots: <pvc>/pool/<slot> at /slot).
OA_K8S_RUNTIME_CLASS="" disables runtimeClassName (kind/minikube have no gVisor).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import shutil
from pathlib import Path

import httpx

from oa.config import settings
from oa.sandbox.providers import DEV_PORT, SANDBOXD_PORT, Handle, _gateway_port

log = logging.getLogger("oa.sandbox.k8s")
SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")
_FWD_RE = re.compile(r"Forwarding from 127\.0\.0\.1:(\d+) -> (\d+)")


class _Forward:
    def __init__(self, proc: asyncio.subprocess.Process, ports: dict[int, int]) -> None:
        self.proc = proc
        self.ports = ports  # remote → local

    def alive(self) -> bool:
        return self.proc.returncode is None


class K8sProvider:
    name = "k8s"

    def __init__(self) -> None:
        self.ns = settings.k8s_namespace
        api = os.environ.get("OA_K8S_API")
        token = os.environ.get("OA_K8S_TOKEN")
        verify: str | bool = os.environ.get("OA_K8S_CA") or (os.environ.get("OA_K8S_INSECURE") != "1")
        in_cluster = False
        if not api:
            host, port = os.environ.get("KUBERNETES_SERVICE_HOST"), os.environ.get("KUBERNETES_SERVICE_PORT", "443")
            if not host:
                raise RuntimeError("K8sProvider requires in-cluster config or OA_K8S_API/OA_K8S_TOKEN")
            api = f"https://{host}:{port}"
            token = (SA_DIR / "token").read_text().strip()
            verify = str(SA_DIR / "ca.crt")
            in_cluster = True
        self.access = os.environ.get("OA_K8S_ACCESS") or ("cluster" if in_cluster else "portforward")
        self.kubectl = os.environ.get("OA_KUBECTL") or shutil.which("kubectl") or "kubectl"
        self.image_pull_policy = os.environ.get("OA_K8S_IMAGE_PULL_POLICY", "IfNotPresent")
        self.http = httpx.AsyncClient(base_url=api, headers={"Authorization": f"Bearer {token}"}, verify=verify,
                                      timeout=30, trust_env=False)
        self._fwd: dict[str, _Forward] = {}

    # ------------------------------------------------------------ manifests
    def _pod(self, chat_id: str) -> str:
        return f"oa-sbx-{chat_id}"

    def _sub_path(self, path: Path) -> str:
        return str(Path(path).resolve().relative_to(settings.data_dir.resolve()))

    def _manifest(self, name: str, token: str, sub_path: str, mount: str, root: str, labels: dict[str, str]) -> dict:
        spec: dict = {
            "automountServiceAccountToken": False,
            "enableServiceLinks": False,
            "restartPolicy": "Never",
            "securityContext": {"runAsUser": settings.k8s_run_as_user, "runAsGroup": settings.k8s_run_as_user,
                                "fsGroup": settings.k8s_run_as_user},
            "containers": [{
                "name": "sandbox",
                "image": settings.sandbox_image,
                "imagePullPolicy": self.image_pull_policy,
                # explicit: the image ENTRYPOINT hard-codes --root /workspace (would break pool slots)
                "command": ["/usr/bin/tini", "--", "sandboxd"],
                "args": ["--listen", f":{SANDBOXD_PORT}", "--root", root],
                "env": [
                    {"name": "SANDBOXD_TOKEN", "value": token},
                    {"name": "SANDBOXD_ROOT", "value": root},
                    {"name": "SANDBOXD_LISTEN", "value": f":{SANDBOXD_PORT}"},
                ],
                "ports": [{"containerPort": SANDBOXD_PORT}, {"containerPort": DEV_PORT}],
                "resources": {
                    "requests": {"cpu": os.environ.get("OA_K8S_CPU_REQUEST", "250m"),
                                 "memory": os.environ.get("OA_K8S_MEM_REQUEST", "512Mi")},
                    "limits": {"cpu": os.environ.get("OA_K8S_CPU_LIMIT", "2"),
                               "memory": os.environ.get("OA_K8S_MEM_LIMIT", "2Gi"),
                               "ephemeral-storage": "4Gi"},
                },
                "readinessProbe": {"httpGet": {"path": "/health", "port": SANDBOXD_PORT}, "periodSeconds": 1},
                "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["NET_RAW"]}},
                "volumeMounts": [{"name": "ws", "mountPath": mount, "subPath": sub_path}],
            }],
            "volumes": [{"name": "ws", "persistentVolumeClaim": {"claimName": settings.k8s_workspace_pvc}}],
        }
        if settings.k8s_runtime_class:
            spec["runtimeClassName"] = settings.k8s_runtime_class
        return {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {"name": name, "labels": {"app": "oa-sandbox", **labels}},
            "spec": spec,
        }

    # ------------------------------------------------------------ REST helpers
    async def _get(self, name: str) -> dict | None:
        r = await self.http.get(f"/api/v1/namespaces/{self.ns}/pods/{name}")
        return r.json() if r.status_code == 200 else None

    async def _create(self, manifest: dict) -> None:
        r = await self.http.post(f"/api/v1/namespaces/{self.ns}/pods", json=manifest)
        if r.status_code >= 400:
            raise RuntimeError(f"create pod failed: {r.status_code} {r.text[:300]}")

    async def _delete(self, name: str) -> None:
        await self._unforward(name)
        await self.http.delete(f"/api/v1/namespaces/{self.ns}/pods/{name}", params={"gracePeriodSeconds": 2})

    async def _wait_running(self, name: str, timeout: float = 180) -> dict:
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        while loop.time() < end:
            pod = await self._get(name)
            st = (pod or {}).get("status", {})
            if st.get("phase") in ("Failed", "Succeeded"):
                raise RuntimeError(f"sandbox pod {name} ended: {st.get('phase')}")
            if st.get("podIP") and st.get("phase") == "Running":
                return pod  # type: ignore[return-value]
            await asyncio.sleep(0.3)
        raise RuntimeError(f"sandbox pod {name} did not start in time")

    # ------------------------------------------------------------ access
    async def _forward(self, name: str) -> _Forward:
        cur = self._fwd.get(name)
        if cur and cur.alive():
            return cur
        proc = await asyncio.create_subprocess_exec(
            self.kubectl, "-n", self.ns, "port-forward", f"pod/{name}", f":{SANDBOXD_PORT}", f":{DEV_PORT}",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        ports: dict[int, int] = {}
        assert proc.stdout is not None
        try:
            while len(ports) < 2:
                line = (await asyncio.wait_for(proc.stdout.readline(), 20)).decode()
                if not line:
                    raise RuntimeError("kubectl port-forward exited")
                m = _FWD_RE.search(line)
                if m:
                    ports[int(m.group(2))] = int(m.group(1))
        except Exception:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            raise
        fwd = _Forward(proc, ports)
        self._fwd[name] = fwd
        asyncio.create_task(self._drain(proc))
        return fwd

    @staticmethod
    async def _drain(proc: asyncio.subprocess.Process) -> None:
        with contextlib.suppress(Exception):
            assert proc.stdout is not None
            while await proc.stdout.readline():
                pass

    async def _unforward(self, name: str) -> None:
        f = self._fwd.pop(name, None)
        if f and f.alive():
            with contextlib.suppress(ProcessLookupError):
                f.proc.kill()

    async def _handle(self, name: str, pod: dict, token: str) -> Handle:
        dev_env = {"PORT": str(DEV_PORT), "OA_HMR_CLIENT_PORT": _gateway_port()}
        if self.access == "portforward":
            f = await self._forward(name)
            return Handle(pod["metadata"]["uid"], f"http://127.0.0.1:{f.ports[SANDBOXD_PORT]}",
                          {str(DEV_PORT): f"http://127.0.0.1:{f.ports[DEV_PORT]}"}, dev_env)
        ip = pod["status"]["podIP"]
        return Handle(pod["metadata"]["uid"], f"http://{ip}:{SANDBOXD_PORT}", {str(DEV_PORT): f"http://{ip}:{DEV_PORT}"},
                      dev_env)

    # ------------------------------------------------------------ Provider protocol
    async def start(self, chat_id: str, workspace: Path, token: str, prev: Handle | None) -> Handle:
        name = self._pod(chat_id)
        pod = await self._get(name)
        if pod and (pod.get("status", {}).get("phase") in ("Failed", "Succeeded")
                    or _pod_token(pod) != token):
            # the manager hands us a fresh token on every start; a pod with another token is unusable
            await self._delete(name)
            await self._wait_gone(name)
            pod = None
        if pod is None:
            workspace.mkdir(parents=True, exist_ok=True)
            await self._create(self._manifest(name, token, self._sub_path(workspace), "/workspace", "/workspace",
                                              {"openatoms.chat": chat_id}))
        pod = await self._wait_running(name)
        return await self._handle(name, pod, token)

    async def _wait_gone(self, name: str, timeout: float = 60) -> None:
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        while loop.time() < end:
            if await self._get(name) is None:
                return
            await asyncio.sleep(0.5)

    async def stop(self, chat_id: str, handle: Handle) -> None:
        await self.destroy(chat_id, handle)  # workspace lives on the PVC; pods are disposable

    async def destroy(self, chat_id: str, handle: Handle | None) -> None:
        await self._delete(self._pod(chat_id))

    async def alive(self, handle: Handle) -> bool:
        if not handle.endpoint:
            return False
        if self.access == "portforward":
            port = handle.endpoint.rsplit(":", 1)[-1]
            return any(f.alive() and str(f.ports.get(SANDBOXD_PORT)) == port for f in self._fwd.values())
        return True

    # ------------------------------------------------------------ warm pool
    async def start_slot(self, slot_id: str, slot_dir: Path, token: str) -> Handle:
        name = f"oa-pool-{slot_id}"
        await self._create(self._manifest(name, token, self._sub_path(slot_dir), "/slot", "/slot/ws",
                                          {"openatoms.pool": "1", "openatoms.slot": slot_id}))
        pod = await self._wait_running(name)
        return await self._handle(name, pod, token)

    async def destroy_slot(self, slot_id: str, external_id: str | None) -> None:
        await self._delete(f"oa-pool-{slot_id}")

    async def list_slots(self) -> list[tuple[str, str]]:
        r = await self.http.get(f"/api/v1/namespaces/{self.ns}/pods", params={"labelSelector": "openatoms.pool=1"})
        if r.status_code != 200:
            return []
        return [(p["metadata"]["labels"].get("openatoms.slot", ""), p["metadata"]["uid"]) for p in r.json()["items"]]


def _pod_token(pod: dict) -> str | None:
    for e in pod["spec"]["containers"][0].get("env", []):
        if e["name"] == "SANDBOXD_TOKEN":
            return e.get("value")
    return None
