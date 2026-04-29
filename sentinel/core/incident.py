from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
import uuid

from .severity import Severity


@dataclass
class Incident:
    service_name: str
    severity: Severity
    alert_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: str = "OPEN"
    root_cause: str = ""
    runbook: dict = field(default_factory=dict)
    impact_scope: dict = field(default_factory=dict)
    log_analysis: dict = field(default_factory=dict)
    incident_report: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "alert_id": self.alert_id,
            "service_name": self.service_name,
            "severity": self.severity.value,
            "created_at": self.created_at.isoformat(),
            "status": self.status,
            "root_cause": self.root_cause,
            "runbook": self.runbook,
            "impact_scope": self.impact_scope,
            "log_analysis": self.log_analysis,
            "incident_report": self.incident_report,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Incident":
        return cls(
            alert_id=data.get("alert_id", str(uuid.uuid4())),
            service_name=data["service_name"],
            severity=Severity(data.get("severity", "P3")),
            created_at=datetime.fromisoformat(data["created_at"]) if "created_at" in data else datetime.now(timezone.utc),
            status=data.get("status", "OPEN"),
            root_cause=data.get("root_cause", ""),
            runbook=data.get("runbook", {}),
            impact_scope=data.get("impact_scope", {}),
            log_analysis=data.get("log_analysis", {}),
            incident_report=data.get("incident_report", {}),
            metadata=data.get("metadata", {}),
        )
