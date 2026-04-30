from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class PullRequest:
    number: int
    title: str
    author: str
    repository: str
    base_branch: str
    head_branch: str
    url: str
    diff: str = ""
    description: str = ""
    labels: list = None

    def __post_init__(self):
        if self.labels is None:
            self.labels = []


@dataclass
class Commit:
    sha: str
    message: str
    author: str
    timestamp: str
    files_changed: list = None

    def __post_init__(self):
        if self.files_changed is None:
            self.files_changed = []


class BaseGitProvider(ABC):
    """Abstract interface all git providers must implement.

    Implement this to add support for GitHub, GitLab, Bitbucket, etc.
    """

    @abstractmethod
    def get_pull_request(self, repo: str, pr_number: int) -> PullRequest:
        """Fetch PR metadata and diff."""

    @abstractmethod
    def post_comment(self, repo: str, pr_number: int, body: str) -> None:
        """Post a comment on a PR."""

    @abstractmethod
    def get_recent_commits(self, repo: str, limit: int = 10) -> list[Commit]:
        """Fetch the most recent commits on the default branch."""

    @abstractmethod
    def block_merge(self, repo: str, pr_number: int, reason: str) -> None:
        """Set a failing status check to block merge."""

    @abstractmethod
    def verify_webhook(self, payload: str, headers: dict) -> bool:
        """Validate incoming webhook signature."""
