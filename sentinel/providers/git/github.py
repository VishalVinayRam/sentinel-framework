import hashlib
import hmac

import requests

from sentinel.providers.base.git import BaseGitProvider, Commit, PullRequest

GITHUB_API = "https://api.github.com"


class GitHubProvider(BaseGitProvider):
    def __init__(self, token: str, webhook_secret: str = ""):
        self._token = token
        self._webhook_secret = webhook_secret
        self._headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
        }

    def get_pull_request(self, repo: str, pr_number: int) -> PullRequest:
        meta = requests.get(f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}", headers=self._headers)
        meta.raise_for_status()
        data = meta.json()

        diff_resp = requests.get(
            f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}",
            headers={**self._headers, "Accept": "application/vnd.github.v3.diff"},
        )
        diff_resp.raise_for_status()

        return PullRequest(
            number=data["number"],
            title=data["title"],
            author=data["user"]["login"],
            repository=repo,
            base_branch=data["base"]["ref"],
            head_branch=data["head"]["ref"],
            url=data["html_url"],
            diff=diff_resp.text[:10000],
            description=data.get("body") or "",
            labels=[l["name"] for l in data.get("labels", [])],
        )

    def post_comment(self, repo: str, pr_number: int, body: str) -> None:
        resp = requests.post(
            f"{GITHUB_API}/repos/{repo}/issues/{pr_number}/comments",
            headers=self._headers,
            json={"body": body},
        )
        resp.raise_for_status()

    def get_recent_commits(self, repo: str, limit: int = 10) -> list[Commit]:
        resp = requests.get(
            f"{GITHUB_API}/repos/{repo}/commits",
            headers=self._headers,
            params={"per_page": limit},
        )
        resp.raise_for_status()
        return [
            Commit(
                sha=c["sha"][:8],
                message=c["commit"]["message"][:120],
                author=c["commit"]["author"]["name"],
                timestamp=c["commit"]["author"]["date"],
            )
            for c in resp.json()
        ]

    def block_merge(self, repo: str, pr_number: int, reason: str) -> None:
        pr = requests.get(f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}", headers=self._headers).json()
        sha = pr["head"]["sha"]
        requests.post(
            f"{GITHUB_API}/repos/{repo}/statuses/{sha}",
            headers=self._headers,
            json={
                "state": "failure",
                "description": reason[:140],
                "context": "sentinel/security-review",
            },
        ).raise_for_status()

    def verify_webhook(self, payload: str, headers: dict) -> bool:
        if not self._webhook_secret:
            return True
        sig = headers.get("X-Hub-Signature-256", "")
        expected = "sha256=" + hmac.new(
            self._webhook_secret.encode(), payload.encode(), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(sig, expected)
