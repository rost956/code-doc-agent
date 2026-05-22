from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from code_agent.indexer import ProjectIndexer


@dataclass(frozen=True)
class GitHubRepoRef:
    owner: str
    repo: str
    normalized_url: str
    clone_url: str


class GitHubRepositoryManager:
    """Clone GitHub repositories and build per-conversation code indexes."""

    def __init__(
        self,
        repositories_dir: str | Path = "data/repositories",
        indexes_dir: str | Path = "data/indexes",
        default_token: str | None = None,
    ):
        self.repositories_dir = Path(repositories_dir)
        self.indexes_dir = Path(indexes_dir)
        self.default_token = (default_token or "").strip() or None
        self.repositories_dir.mkdir(parents=True, exist_ok=True)
        self.indexes_dir.mkdir(parents=True, exist_ok=True)

    def connect(
        self,
        conversation_id: str,
        url: str,
        token: str | None = None,
        branch: str | None = None,
    ) -> dict[str, Any]:
        """Clone repository, build index and return metadata for the conversation."""
        safe_conversation_id = self._safe_name(conversation_id)
        repo_ref = self.parse_url(url)
        branch = (branch or "").strip() or None
        access_token = (token or "").strip() or self.default_token

        conversation_repo_dir = self.repositories_dir / safe_conversation_id
        conversation_index_dir = self.indexes_dir / safe_conversation_id
        local_path = conversation_repo_dir / f"{repo_ref.owner}__{repo_ref.repo}"
        index_path = conversation_index_dir / "code_index.json"

        if conversation_repo_dir.exists():
            shutil.rmtree(conversation_repo_dir)
        if conversation_index_dir.exists():
            shutil.rmtree(conversation_index_dir)

        conversation_repo_dir.mkdir(parents=True, exist_ok=True)
        conversation_index_dir.mkdir(parents=True, exist_ok=True)

        clone_url = self._make_authenticated_clone_url(repo_ref, access_token)
        self._clone_repository(clone_url=clone_url, local_path=local_path, branch=branch)

        indexer = ProjectIndexer(local_path)
        indexer.save(index_path)
        index_data = indexer.build()
        symbol_count = len(index_data.get("symbols", []))

        return {
            "provider": "github",
            "owner": repo_ref.owner,
            "repo": repo_ref.repo,
            "url": repo_ref.normalized_url,
            "branch": branch,
            "local_path": str(local_path),
            "index_path": str(index_path),
            "symbol_count": symbol_count,
        }

    def delete_conversation_resources(self, conversation_id: str) -> None:
        safe_conversation_id = self._safe_name(conversation_id)
        for root in (self.repositories_dir, self.indexes_dir):
            path = root / safe_conversation_id
            if path.exists():
                shutil.rmtree(path)

    def parse_url(self, url: str) -> GitHubRepoRef:
        raw = (url or "").strip()
        if not raw:
            raise ValueError("GitHub URL is empty")

        owner: str | None = None
        repo: str | None = None

        # SSH: git@github.com:owner/repo.git
        ssh_match = re.match(r"^git@github\.com:(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+?)(?:\.git)?/?$", raw)
        if ssh_match:
            owner = ssh_match.group("owner")
            repo = ssh_match.group("repo")
        else:
            candidate = raw
            if not candidate.startswith(("http://", "https://")):
                candidate = "https://" + candidate
            parsed = urlparse(candidate)
            host = parsed.netloc.lower()
            if host not in {"github.com", "www.github.com"}:
                raise ValueError("Only github.com repositories are supported")
            parts = [part for part in parsed.path.split("/") if part]
            if len(parts) < 2:
                raise ValueError("GitHub URL must contain owner and repository name")
            owner = parts[0]
            repo = parts[1]
            if repo.endswith(".git"):
                repo = repo[:-4]

        if not owner or not repo:
            raise ValueError("Could not parse GitHub repository URL")

        owner = self._safe_repo_part(owner)
        repo = self._safe_repo_part(repo)
        normalized_url = f"https://github.com/{owner}/{repo}"
        clone_url = f"https://github.com/{owner}/{repo}.git"
        return GitHubRepoRef(owner=owner, repo=repo, normalized_url=normalized_url, clone_url=clone_url)

    def _clone_repository(self, clone_url: str, local_path: Path, branch: str | None = None) -> None:
        command = ["git", "clone", "--depth", "1"]
        if branch:
            command.extend(["--branch", branch])
        command.extend([clone_url, str(local_path)])

        try:
            completed = subprocess.run(
                command,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError as exc:
            raise RuntimeError("Git is not installed or is not available in PATH") from exc

        if completed.returncode != 0:
            stderr = self._redact_secret(completed.stderr.strip(), clone_url)
            stdout = self._redact_secret(completed.stdout.strip(), clone_url)
            details = stderr or stdout or f"git clone failed with exit code {completed.returncode}"
            raise RuntimeError(details)

    def _make_authenticated_clone_url(self, repo_ref: GitHubRepoRef, token: str | None) -> str:
        if not token:
            return repo_ref.clone_url
        safe_token = quote(token, safe="")
        return f"https://x-access-token:{safe_token}@github.com/{repo_ref.owner}/{repo_ref.repo}.git"

    def _redact_secret(self, text: str, clone_url: str) -> str:
        if not text:
            return text
        redacted = text.replace(clone_url, "https://github.com/***/***.git")
        redacted = re.sub(r"x-access-token:[^@\s]+@", "x-access-token:***@", redacted)
        return redacted

    def _safe_name(self, value: str) -> str:
        safe = "".join(ch for ch in value if ch.isalnum() or ch in {"-", "_"})
        if not safe:
            raise ValueError("Invalid conversation id")
        return safe

    def _safe_repo_part(self, value: str) -> str:
        if not re.match(r"^[A-Za-z0-9_.-]+$", value):
            raise ValueError("Invalid GitHub repository URL")
        return value
