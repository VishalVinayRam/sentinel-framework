import requests

from sentinel.providers.base.alerting import AlertPayload, BaseAlertingProvider

PAGERDUTY_API = "https://events.pagerduty.com/v2/enqueue"

SEVERITY_MAP = {
    "P1": "critical",
    "P2": "error",
    "P3": "warning",
    "P4": "info",
    "critical": "critical",
    "high": "error",
    "medium": "warning",
    "low": "info",
}


class PagerDutyProvider(BaseAlertingProvider):
    def __init__(self, routing_key: str):
        self._key = routing_key

    def send_alert(self, payload: AlertPayload) -> None:
        body = {
            "routing_key": self._key,
            "event_action": "trigger",
            "dedup_key": payload.incident_id,
            "payload": {
                "summary": payload.title,
                "severity": SEVERITY_MAP.get(payload.severity, "warning"),
                "source": payload.service_name,
                "custom_details": {
                    "incident_id": payload.incident_id,
                    "root_cause": payload.body,
                    "runbook": payload.runbook_url,
                    **payload.metadata,
                },
            },
        }
        if payload.runbook_url:
            body["links"] = [{"href": payload.runbook_url, "text": "Runbook"}]

        requests.post(PAGERDUTY_API, json=body, timeout=10).raise_for_status()

    def send_resolved(self, incident_id: str, resolution_summary: str) -> None:
        requests.post(PAGERDUTY_API, json={
            "routing_key": self._key,
            "event_action": "resolve",
            "dedup_key": incident_id,
            "payload": {"summary": resolution_summary, "severity": "info", "source": "sentinel"},
        }, timeout=10).raise_for_status()

    def health_check(self) -> bool:
        return bool(self._key)
