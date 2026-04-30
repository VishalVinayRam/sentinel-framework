from abc import ABC, abstractmethod
from dataclasses import dataclass

from sentinel.core.incident import Incident


@dataclass
class AlertPayload:
    title: str
    body: str
    severity: str
    incident_id: str
    service_name: str
    runbook_url: str = ""
    metadata: dict = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class BaseAlertingProvider(ABC):
    """Abstract interface for outbound alerting channels.

    Implement this to support Slack, PagerDuty, OpsGenie, email, etc.
    """

    @abstractmethod
    def send_alert(self, payload: AlertPayload) -> None:
        """Send an incident alert to the configured channel."""

    @abstractmethod
    def send_resolved(self, incident_id: str, resolution_summary: str) -> None:
        """Notify that an incident has been resolved."""

    @abstractmethod
    def health_check(self) -> bool:
        """Return True if the alerting backend is reachable."""

    def alert_from_incident(self, incident: Incident, runbook_url: str = "") -> None:
        payload = AlertPayload(
            title=f"[{incident.severity.label()}] Incident in {incident.service_name}",
            body=incident.root_cause or "Root cause analysis in progress.",
            severity=incident.severity.value,
            incident_id=incident.alert_id,
            service_name=incident.service_name,
            runbook_url=runbook_url,
        )
        self.send_alert(payload)
