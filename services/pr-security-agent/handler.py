import json
import hmac
import hashlib
import os
import uuid
from datetime import datetime, timezone

import requests

import sys
sys.path.append("/var/task/shared")
from aws_clients import dynamodb, s3


GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
GITHUB_WEBHOOK_SECRET = os.environ["GITHUB_WEBHOOK_SECRET"]
KSERVE_ENDPOINT = os.environ["KSERVE_ENDPOINT"]
PR_REVIEWS_TABLE = os.environ["PR_REVIEWS_TABLE"]
CODEBASE_BUCKET = os.environ["CODEBASE_BUCKET"]


def handler(event, context):
    body = event.get("body", "")
    if not _verify_signature(body, event.get("headers", {})):
        return {"statusCode": 401, "body": "Invalid signature"}

    payload = json.loads(body)
    if payload.get("action") not in ("opened", "synchronize"):
        return {"statusCode": 200, "body": "Skipped"}

    pr = payload["pull_request"]
    diff = _fetch_pr_diff(payload["repository"]["full_name"], pr["number"])
    analysis = _run_security_analysis(diff, pr)
    _post_pr_comment(payload["repository"]["full_name"], pr["number"], analysis)
    _store_result(pr, analysis)

    return {"statusCode": 200, "body": json.dumps({"review_id": analysis["review_id"]})}


def _verify_signature(body: str, headers: dict) -> bool:
    sig = headers.get("X-Hub-Signature-256", "")
    expected = "sha256=" + hmac.new(
        GITHUB_WEBHOOK_SECRET.encode(), body.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(sig, expected)


def _fetch_pr_diff(repo: str, pr_number: int) -> str:
    resp = requests.get(
        f"https://api.github.com/repos/{repo}/pulls/{pr_number}",
        headers={"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3.diff"},
    )
    resp.raise_for_status()
    return resp.text[:8000]  # cap at 8k chars for inference context


def _run_security_analysis(diff: str, pr: dict) -> dict:
    prompt = f"""You are a security-focused code reviewer. Analyze this pull request diff for:
1. Security vulnerabilities (OWASP Top 10, injection, auth issues)
2. Missed edge cases that could cause production incidents
3. Code structure issues (N+1 queries, missing error handling, race conditions)
4. Missing input validation at system boundaries

PR Title: {pr['title']}
PR Author: {pr['user']['login']}

Diff:
{diff}

Respond in JSON with keys: severity (critical/high/medium/low), issues (list of findings),
edge_cases_missed (list), recommendations (list), summary (one paragraph)."""

    resp = requests.post(
        f"{KSERVE_ENDPOINT}/v1/models/phi-3-lite:predict",
        json={"inputs": [{"name": "text_input", "shape": [1], "datatype": "BYTES", "data": [prompt]}]},
        timeout=60,
    )
    resp.raise_for_status()

    raw = resp.json()["outputs"][0]["data"][0]
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        result = {"severity": "unknown", "summary": raw, "issues": [], "edge_cases_missed": [], "recommendations": []}

    result["review_id"] = str(uuid.uuid4())
    result["analyzed_at"] = datetime.now(timezone.utc).isoformat()
    return result


def _post_pr_comment(repo: str, pr_number: int, analysis: dict) -> None:
    severity_emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(
        analysis.get("severity", "unknown"), "⚪"
    )

    issues_md = "\n".join(f"- {i}" for i in analysis.get("issues", [])) or "_None found_"
    edge_cases_md = "\n".join(f"- {e}" for e in analysis.get("edge_cases_missed", [])) or "_None found_"
    recs_md = "\n".join(f"- {r}" for r in analysis.get("recommendations", [])) or "_None_"

    body = f"""## Sentinel Security Review {severity_emoji}

**Severity:** `{analysis.get('severity', 'unknown').upper()}`
**Review ID:** `{analysis['review_id']}`

### Summary
{analysis.get('summary', 'N/A')}

### Security Issues
{issues_md}

### Edge Cases Missed
{edge_cases_md}

### Recommendations
{recs_md}

---
_Powered by Sentinel · Phi-3 via KServe_"""

    requests.post(
        f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments",
        headers={"Authorization": f"token {GITHUB_TOKEN}"},
        json={"body": body},
    ).raise_for_status()


def _store_result(pr: dict, analysis: dict) -> None:
    table = dynamodb().Table(PR_REVIEWS_TABLE)
    table.put_item(Item={
        "pr_id": str(pr["number"]),
        "reviewed_at": analysis["analyzed_at"],
        "risk_level": analysis.get("severity", "unknown"),
        "review_id": analysis["review_id"],
        "pr_title": pr["title"],
        "pr_author": pr["user"]["login"],
        "issues_count": len(analysis.get("issues", [])),
        "edge_cases_count": len(analysis.get("edge_cases_missed", [])),
    })
