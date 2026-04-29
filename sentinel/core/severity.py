from enum import Enum


class Severity(str, Enum):
    P1 = "P1"  # Critical — full outage, data loss risk
    P2 = "P2"  # Major — significant degradation
    P3 = "P3"  # Minor — partial degradation
    P4 = "P4"  # Informational — no user impact

    def is_critical(self) -> bool:
        return self == Severity.P1

    def escalate(self) -> "Severity":
        order = [Severity.P4, Severity.P3, Severity.P2, Severity.P1]
        idx = order.index(self)
        return order[min(idx + 1, len(order) - 1)]

    def label(self) -> str:
        labels = {
            Severity.P1: "CRITICAL",
            Severity.P2: "HIGH",
            Severity.P3: "MEDIUM",
            Severity.P4: "LOW",
        }
        return labels[self]
