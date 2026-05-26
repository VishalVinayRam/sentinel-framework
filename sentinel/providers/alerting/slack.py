import requests

from sentinel.providers.base.alerting import AlertPayload, BaseAlertingProvider

SEVERITY_COLORS = {
    "P1": "#FF0000",
    "P2": "#FF6600",
    "P3": "#FFD700",
    "P4": "#36A64F",
    "critical": "#FF0000",
    "high": "#FF6600",
    "medium": "#FFD700",
    "low": "#36A64F",
}


class SlackProvider(BaseAlertingProvider):
    def __init__(self, webhook_url: str, channel: str = "", username: str = "Sentinel"):
        self._webhook = webhook_url
        self._channel = channel
        self._username = username

    def send_alert(self, payload: AlertPayload) -> None:
        color = SEVERITY_COLORS.get(payload.severity, "#808080")
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"🚨 {payload.title}"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Severity:*\n`{payload.severity}`"},
                    {"type": "mrkdwn", "text": f"*Service:*\n{payload.service_name}"},
                    {"type": "mrkdwn", "text": f"*Incident ID:*\n`{payload.incident_id}`"},
                    {"type": "mrkdwn", "text": "*Status:*\nInvestigating"},
                ],
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Root cause:*\n{payload.body}"},
            },
        ]

        if payload.runbook_url:
            blocks.append({
                "type": "actions",
                "elements": [{
                    "type": "button",
                    "text": {"type": "plain_text", "text": "View Runbook"},
                    "url": payload.runbook_url,
                    "style": "primary",
                }],
            })

        message = {
            "username": self._username,
            "attachments": [{
                "color": color,
                "blocks": blocks,
                "fallback": payload.title,
            }],
        }
        if self._channel:
            message["channel"] = self._channel

        resp = requests.post(self._webhook, json=message, timeout=10)
        resp.raise_for_status()

    def send_resolved(self, incident_id: str, resolution_summary: str) -> None:
        message = {
            "username": self._username,
            "attachments": [{
                "color": "#36A64F",
                "blocks": [
                    {
                        "type": "header",
                        "text": {"type": "plain_text", "text": "✅ Incident Resolved"},
                    },
                    {
                        "type": "section",
                        "fields": [
                            {"type": "mrkdwn", "text": f"*Incident ID:*\n`{incident_id}`"},
                            {"type": "mrkdwn", "text": f"*Resolution:*\n{resolution_summary}"},
                        ],
                    },
                ],
                "fallback": f"Incident {incident_id} resolved",
            }],
        }
        if self._channel:
            message["channel"] = self._channel
        requests.post(self._webhook, json=message, timeout=10).raise_for_status()

    def health_check(self) -> bool:
        try:
            resp = requests.post(self._webhook, json={"text": "sentinel health check ping"}, timeout=5)
            return resp.status_code == 200
        except Exception:
            return False
