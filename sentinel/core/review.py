from dataclasses import dataclass, field
from datetime import datetime, timezone
import uuid


@dataclass
class PRReview:
    pr_id: str
    pr_title: str
    pr_author: str
    repository: str
    review_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    reviewed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    risk_level: str = "unknown"
    issues: list = field(default_factory=list)
    edge_cases_missed: list = field(default_factory=list)
    recommendations: list = field(default_factory=list)
    summary: str = ""
    blocked_merge: bool = False
    diff_size_lines: int = 0
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "pr_id": self.pr_id,
            "pr_title": self.pr_title,
            "pr_author": self.pr_author,
            "repository": self.repository,
            "review_id": self.review_id,
            "reviewed_at": self.reviewed_at.isoformat(),
            "risk_level": self.risk_level,
            "issues": self.issues,
            "edge_cases_missed": self.edge_cases_missed,
            "recommendations": self.recommendations,
            "summary": self.summary,
            "blocked_merge": self.blocked_merge,
            "diff_size_lines": self.diff_size_lines,
            "metadata": self.metadata,
        }

    def format_comment(self) -> str:
        emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(self.risk_level, "⚪")
        issues_md = "\n".join(f"- {i}" for i in self.issues) or "_None found_"
        edge_cases_md = "\n".join(f"- {e}" for e in self.edge_cases_missed) or "_None found_"
        recs_md = "\n".join(f"- {r}" for r in self.recommendations) or "_None_"

        return (
            f"## Sentinel Security Review {emoji}\n\n"
            f"**Severity:** `{self.risk_level.upper()}`  |  "
            f"**Review ID:** `{self.review_id}`\n\n"
            f"### Summary\n{self.summary}\n\n"
            f"### Security Issues\n{issues_md}\n\n"
            f"### Edge Cases Missed\n{edge_cases_md}\n\n"
            f"### Recommendations\n{recs_md}\n\n"
            f"---\n_Powered by [Sentinel](https://github.com/VishalVinayRam/Project-KEMM)_"
        )
