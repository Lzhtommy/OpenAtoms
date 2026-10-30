"""K8sProvider pod manifests: uid shared with api/worker (shared PVC) and the gVisor RuntimeClass."""
from __future__ import annotations

from oa.config import settings
from oa.sandbox.k8s import K8sProvider


def _provider(monkeypatch) -> K8sProvider:
    monkeypatch.setenv("OA_K8S_API", "https://127.0.0.1:6443")
    monkeypatch.setenv("OA_K8S_TOKEN", "t")
    monkeypatch.setenv("OA_K8S_INSECURE", "1")
    return K8sProvider()


def test_pod_runs_as_configured_uid_with_gvisor(monkeypatch):
    p = _provider(monkeypatch)
    spec = p._manifest("oa-sbx-x", "tok", "chats/x/workspace", "/workspace", "/workspace", {"app": "oa-sandbox"})["spec"]
    assert spec["securityContext"] == {"runAsUser": 1000, "runAsGroup": 1000, "fsGroup": 1000}
    assert spec["runtimeClassName"] == "gvisor"
    assert spec["automountServiceAccountToken"] is False


def test_uid_and_runtime_class_are_configurable(monkeypatch):
    monkeypatch.setattr(settings, "k8s_run_as_user", 0)
    monkeypatch.setattr(settings, "k8s_runtime_class", "")
    spec = _provider(monkeypatch)._manifest("n", "tok", "pool/s1", "/slot", "/slot/ws", {})["spec"]
    assert spec["securityContext"]["runAsUser"] == 0 and "runtimeClassName" not in spec
