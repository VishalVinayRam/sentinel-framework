"""Pluggable log sources for sentinel diagnose.

Each source implements fetch(service, minutes) -> str.
The auto_detect() function probes available sources in priority order.
"""

import os
import subprocess
import sys
from typing import Optional


# ── Base ───────────────────────────────────────────────────────────────────

class LogSource:
    name: str = "unknown"

    def is_available(self) -> bool:
        return True

    def fetch(self, service: str, minutes: int = 30) -> str:
        return ""


# ── Kubernetes (kubectl) ────────────────────────────────────────────────────

class KubernetesLogSource(LogSource):
    """Fetch pod logs via kubectl. Works with any cluster kubectl is configured for."""

    name = "kubernetes"

    def __init__(
        self,
        namespace: str = "default",
        container: Optional[str] = None,
        label_selector: Optional[str] = None,
        kubeconfig: Optional[str] = None,
        max_pods: int = 3,
    ):
        self.namespace      = namespace
        self.container      = container
        self.label_selector = label_selector
        self.kubeconfig     = kubeconfig
        self.max_pods       = max_pods

    def is_available(self) -> bool:
        try:
            r = subprocess.run(
                ["kubectl", "version", "--client"],
                capture_output=True, text=True, timeout=5,
            )
            return r.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def fetch(self, service: str, minutes: int = 30) -> str:
        pods = self._find_pods(service)
        if not pods:
            return ""

        raw_lines: list[str] = []
        for pod in pods[: self.max_pods]:
            raw_lines.extend(self._pod_logs(pod, since=f"{minutes}m"))

        return _filter_errors("\n".join(raw_lines))

    # ── helpers ──────────────────────────────────────────────────────────

    def _base_flags(self) -> list[str]:
        flags = ["-n", self.namespace]
        if self.kubeconfig:
            flags += ["--kubeconfig", self.kubeconfig]
        return flags

    def _find_pods(self, service: str) -> list[str]:
        # Try progressively looser selectors until we find pods
        selectors = [
            self.label_selector,
            f"app={service}",
            f"app.kubernetes.io/name={service}",
            f"app={service.split('-')[0]}",   # "auth" from "auth-service"
        ]
        for sel in selectors:
            if not sel:
                continue
            cmd = (
                ["kubectl", "get", "pods"]
                + self._base_flags()
                + ["-l", sel,
                   "--field-selector", "status.phase=Running",
                   "-o", "name", "--no-headers"]
            )
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                pods = [p.replace("pod/", "").strip() for p in r.stdout.splitlines() if p.strip()]
                if pods:
                    return pods
            except subprocess.TimeoutExpired:
                continue
        return []

    def _pod_logs(self, pod: str, since: str) -> list[str]:
        cmd = (
            ["kubectl", "logs", pod]
            + self._base_flags()
            + [f"--since={since}", "--tail=300"]
        )
        if self.container:
            cmd += ["-c", self.container]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
            return r.stdout.splitlines()
        except subprocess.TimeoutExpired:
            return []


# ── AWS CloudWatch ─────────────────────────────────────────────────────────

class CloudWatchLogSource(LogSource):
    """Fetch error logs from CloudWatch Logs Insights."""

    name = "aws"

    def __init__(
        self,
        region: str = "us-east-1",
        log_group_prefix: str = "/ecs/",
        endpoint_url: Optional[str] = None,
    ):
        self.region           = region
        self.log_group_prefix = log_group_prefix
        self.endpoint_url     = endpoint_url

    def is_available(self) -> bool:
        try:
            import boto3  # noqa: F401
            return True
        except ImportError:
            return False

    def fetch(self, service: str, minutes: int = 30) -> str:
        from sentinel.providers.log.cloudwatch import CloudWatchLogsProvider
        provider = CloudWatchLogsProvider(
            region=self.region,
            log_group_prefix=self.log_group_prefix,
            endpoint_url=self.endpoint_url,
        )
        return provider.fetch_as_text(service, minutes=minutes)


# ── Loki ───────────────────────────────────────────────────────────────────

class LokiLogSource(LogSource):
    """Fetch error logs from Grafana Loki via LogQL."""

    name = "loki"

    def __init__(
        self,
        base_url: str = "http://localhost:3100",
        tenant_id: str = "",
        username: str = "",
        password: str = "",
        service_label: str = "service",
    ):
        self.base_url      = base_url
        self.tenant_id     = tenant_id
        self.username      = username
        self.password      = password
        self.service_label = service_label

    def is_available(self) -> bool:
        from sentinel.providers.log.loki import LokiLogsProvider
        return LokiLogsProvider(
            base_url=self.base_url,
            tenant_id=self.tenant_id,
            username=self.username,
            password=self.password,
        ).is_ready()

    def fetch(self, service: str, minutes: int = 30) -> str:
        from sentinel.providers.log.loki import LokiLogsProvider
        return LokiLogsProvider(
            base_url=self.base_url,
            tenant_id=self.tenant_id,
            username=self.username,
            password=self.password,
            service_label=self.service_label,
        ).fetch_as_text(service, minutes=minutes)


# ── File ───────────────────────────────────────────────────────────────────

class FileLogSource(LogSource):
    """Read logs from a local file (last N lines)."""

    name = "file"

    def __init__(self, path: str, tail: int = 500):
        self.path = path
        self.tail = tail

    def is_available(self) -> bool:
        return os.path.isfile(self.path)

    def fetch(self, service: str, minutes: int = 30) -> str:
        try:
            with open(self.path, errors="replace") as f:
                lines = f.readlines()
            return "".join(lines[-self.tail :])
        except OSError:
            return ""


# ── Stdin ──────────────────────────────────────────────────────────────────

class StdinLogSource(LogSource):
    """Read logs piped to stdin (e.g. kubectl logs … | sentinel diagnose …)."""

    name = "stdin"

    def is_available(self) -> bool:
        return not sys.stdin.isatty()

    def fetch(self, service: str, minutes: int = 30) -> str:
        try:
            return sys.stdin.read()
        except Exception:
            return ""


# ── Auto-detect ────────────────────────────────────────────────────────────

def auto_detect(service: str, namespace: str = "default") -> Optional[LogSource]:
    """Return the first available log source, or None.

    Priority: stdin → Kubernetes → Loki (LOKI_URL env) → CloudWatch (AWS env)
    """
    if not sys.stdin.isatty():
        return StdinLogSource()

    k8s = KubernetesLogSource(namespace=namespace)
    if k8s.is_available():
        return k8s

    loki_url = os.environ.get("LOKI_URL", "")
    if loki_url:
        loki = LokiLogSource(
            base_url=loki_url,
            tenant_id=os.environ.get("LOKI_TENANT_ID", ""),
            username=os.environ.get("LOKI_USER", ""),
            password=os.environ.get("LOKI_PASSWORD", ""),
        )
        if loki.is_available():
            return loki

    if os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("AWS_PROFILE"):
        return CloudWatchLogSource(
            region=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        )

    return None


# ── Helpers ────────────────────────────────────────────────────────────────

_ERROR_KEYWORDS = (
    "error", "exception", "fatal", "panic", "crash",
    "timeout", "refused", "oom", "killed", "failed",
    "traceback", "stacktrace", "warn",
)


def _filter_errors(text: str, min_lines: int = 10) -> str:
    """Return error-relevant lines. Falls back to full text if too few match."""
    lines = text.splitlines()
    error_lines = [ln for ln in lines if any(kw in ln.lower() for kw in _ERROR_KEYWORDS)]
    if len(error_lines) < min_lines:
        return "\n".join(lines[-300:])
    return "\n".join(error_lines[-300:])
