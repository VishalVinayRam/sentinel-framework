import hmac

import requests

from sentinel.providers.base.git import BaseGitProvider, Commit, PullRequest

GITLAB_API = "https://gitlab.com/api/v4"


class GitLabProvider(BaseGitProvider):
    def __init__(self, token: str, webhook_secret: str = "", base_url: str = GITLAB_API):
        self._token = token
        self._webhook_secret = webhook_secret
        self._base_url = base_url.rstrip("/")
        self._headers = {"PRIVATE-TOKEN": token}

    def _project_id(self, repo: str) -> str:
        return repo.replace("/", "%2F")

    def get_pull_request(self, repo: str, pr_number: int) -> PullRequest:
        pid = self._project_id(repo)
        resp = requests.get(
            f"{self._base_url}/projects/{pid}/merge_requests/{pr_number}",
            headers=self._headers,
        )
        resp.raise_for_status()
        data = resp.json()

        diff_resp = requests.get(
            f"{self._base_url}/projects/{pid}/merge_requests/{pr_number}/changes",
            headers=self._headers,
        )
        diff_text = ""
        if diff_resp.ok:
            changes = diff_resp.json().get("changes", [])
            diff_text = "\n".join(c.get("diff", "") for c in changes)[:10000]

        return PullRequest(
            number=data["iid"],
            title=data["title"],
            author=data["author"]["username"],
            repository=repo,
            base_branch=data["target_branch"],
            head_branch=data["source_branch"],
            url=data["web_url"],
            diff=diff_text,
            description=data.get("description") or "",
        )

    def post_comment(self, repo: str, pr_number: int, body: str) -> None:
        pid = self._project_id(repo)
        requests.post(
            f"{self._base_url}/projects/{pid}/merge_requests/{pr_number}/notes",
            headers=self._headers,
            json={"body": body},
        ).raise_for_status()

    def get_recent_commits(self, repo: str, limit: int = 10) -> list[Commit]:
        pid = self._project_id(repo)
        resp = requests.get(
            f"{self._base_url}/projects/{pid}/repository/commits",
            headers=self._headers,
            params={"per_page": limit},
        )
        resp.raise_for_status()
        return [
            Commit(sha=c["short_id"], message=c["title"], author=c["author_name"], timestamp=c["created_at"])
            for c in resp.json()
        ]

    def block_merge(self, repo: str, pr_number: int, reason: str) -> None:
        pid = self._project_id(repo)
        requests.put(
            f"{self._base_url}/projects/{pid}/merge_requests/{pr_number}",
            headers=self._headers,
            json={"blocking_discussions_resolved": False, "description": f"[SENTINEL BLOCKED] {reason}"},
        )

    def verify_webhook(self, payload: str, headers: dict) -> bool:
        if not self._webhook_secret:
            return True
        token = headers.get("X-Gitlab-Token", "")
        return hmac.compare_digest(token, self._webhook_secret)
