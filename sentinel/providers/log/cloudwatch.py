"""
CloudWatch Logs provider — fetches error log lines for a given service.

Requires IAM permissions: logs:StartQuery, logs:GetQueryResults.
For local testing with Floci, results will be empty (Floci does not emulate CW Logs Insights).

Usage:
    provider = CloudWatchLogsProvider(
        region="us-east-1",
        log_group_prefix="/ecs/",   # appended with service name
    )
    logs = provider.fetch_errors("payments-service", minutes=30)
"""

import time
from dataclasses import dataclass, field
from typing import Optional

import boto3


@dataclass
class LogLine:
    timestamp: str
    message: str
    log_stream: str = ""
    level: str = ""


class CloudWatchLogsProvider:
    """Fetches recent error logs from CloudWatch Logs Insights."""

    def __init__(
        self,
        region: str = "us-east-1",
        log_group_prefix: str = "/ecs/",
        endpoint_url: Optional[str] = None,
        aws_access_key_id: Optional[str] = None,
        aws_secret_access_key: Optional[str] = None,
    ):
        kwargs: dict = {"region_name": region}
        if endpoint_url:
            kwargs["endpoint_url"] = endpoint_url
        if aws_access_key_id:
            kwargs["aws_access_key_id"] = aws_access_key_id
            kwargs["aws_secret_access_key"] = aws_secret_access_key or ""
        self._client = boto3.client("logs", **kwargs)
        self._prefix = log_group_prefix.rstrip("/")

    def fetch_errors(self, service: str, minutes: int = 30, limit: int = 100) -> list[LogLine]:
        """Run a Logs Insights query and return matching error lines."""
        log_group = f"{self._prefix}/{service}"
        end_sec   = int(time.time())
        start_sec = end_sec - minutes * 60

        query = (
            "fields @timestamp, @logStream, @message | "
            "filter @message like /(?i)(error|exception|fatal|timeout|oom|panic|crash)/ | "
            "sort @timestamp desc | "
            f"limit {limit}"
        )

        try:
            resp     = self._client.start_query(
                logGroupName=log_group,
                startTime=start_sec,
                endTime=end_sec,
                queryString=query,
            )
            query_id = resp["queryId"]
        except self._client.exceptions.ResourceNotFoundException:
            return []
        except Exception as e:
            print(f"[cloudwatch] start_query failed for {log_group}: {e}")
            return []

        # Poll until complete (max 15 s)
        for _ in range(15):
            result = self._client.get_query_results(queryId=query_id)
            if result["status"] in ("Complete", "Failed", "Cancelled", "Timeout"):
                break
            time.sleep(1)

        lines = []
        for row in result.get("results", []):
            fields = {f["field"]: f["value"] for f in row}
            msg = fields.get("@message", "").strip()
            if msg:
                lines.append(LogLine(
                    timestamp=fields.get("@timestamp", ""),
                    message=msg,
                    log_stream=fields.get("@logStream", ""),
                    level=_detect_level(msg),
                ))
        return lines

    def fetch_as_text(self, service: str, minutes: int = 30, limit: int = 100) -> str:
        """Convenience method — returns log lines as a plain-text block."""
        lines = self.fetch_errors(service, minutes, limit)
        return "\n".join(f"{l.timestamp}  [{l.level}]  {l.message}" for l in lines)

    def list_log_groups(self, prefix: str = "") -> list[str]:
        """List available log group names (useful for service discovery)."""
        groups = []
        paginator = self._client.get_paginator("describe_log_groups")
        for page in paginator.paginate(logGroupNamePrefix=prefix or self._prefix):
            groups.extend(g["logGroupName"] for g in page.get("logGroups", []))
        return groups


def _detect_level(message: str) -> str:
    msg = message.upper()
    for level in ("FATAL", "CRITICAL", "ERROR", "WARN", "WARNING", "INFO", "DEBUG"):
        if level in msg:
            return level
    return "UNKNOWN"
