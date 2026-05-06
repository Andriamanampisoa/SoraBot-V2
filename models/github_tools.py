##
## SORABOT, 2026
## github_tools.py
## File description:
## The GitHubTools class to manage GitHub operations: branches, commits, PRs.
##

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from github import Github, GithubException
from git import Repo, GitCommandError

from models.token_store import TokenStore
from models.github_exceptions import GitHubAuthenticationError, GitHubOperationError

class GitHubTools:
    """
    Manage GitHub operations: branches, commits, PRs.
    """

    def __init__(
        self,
        repo_owner: str,
        repo_name: str,
        github_token: Optional[str] = None,
        user_id: Optional[str] = None,
    ):
        # Priority: user token > provided token > env token
        self.github_token = self._resolve_token(user_id=user_id, provided_token=github_token)
        self.repo_owner = repo_owner
        self.repo_name = repo_name
        self.repo_path = Path(__file__).resolve().parents[1]

        if not self.github_token:
            raise ValueError("GITHUB_TOKEN environment variable not set")

        try:
            self.gh = Github(self.github_token)
            self.repo = self.gh.get_repo(f"{repo_owner}/{repo_name}")
            self.git_repo = Repo(self.repo_path)
        except GithubException as e:
            # Detect authentication errors
            if "401" in str(e) or "Unauthorized" in str(e):
                raise GitHubAuthenticationError(
                    f"Invalid GitHub token. Please verify your token permissions.",
                    requires_user_token=True
                )
            elif "403" in str(e) or "Forbidden" in str(e):
                raise GitHubAuthenticationError(
                    f"Insufficient permissions on repository {repo_owner}/{repo_name}. "
                    f"Your token needs 'repo' and 'workflow' scopes.",
                    requires_user_token=True
                )
            elif "404" in str(e) or "Not Found" in str(e):
                raise GitHubAuthenticationError(
                    f"Repository {repo_owner}/{repo_name} not found or not accessible. "
                    f"Verify the repository path and your token permissions.",
                    requires_user_token=True
                )
            else:
                raise GitHubOperationError(f"GitHub error: {e}")

    @staticmethod
    def _resolve_token(user_id: Optional[str] = None, provided_token: Optional[str] = None) -> str:
        """
        Resolve token priority: user token > provided > env.
        """
        if user_id:
            try:
                token_store = TokenStore()
                user_token = token_store.get_token(user_id, "github")
                if user_token:
                    return user_token
            except Exception:
                pass

        if provided_token:
            return provided_token
        return os.getenv("GITHUB_TOKEN", "")

    def _pull_request_to_dict(self, pull_request) -> dict:
        """
        Convert a PullRequest object to a dictionary with relevant fields.
        """
        return {
            "number": pull_request.number,
            "title": pull_request.title,
            "state": pull_request.state,
            "draft": getattr(pull_request, "draft", False),
            "mergeable_state": getattr(pull_request, "mergeable_state", None),
            "mergeable": getattr(pull_request, "mergeable", None),
            "merged": bool(getattr(pull_request, "merged_at", None)),
            "head_branch": pull_request.head.ref,
            "base_branch": pull_request.base.ref,
            "author": pull_request.user.login if pull_request.user else None,
            "url": pull_request.html_url,
            "body": pull_request.body,
            "labels": [label.name for label in pull_request.labels],
            "comments": getattr(pull_request, "comments", None),
            "review_comments": getattr(pull_request, "review_comments", None),
            "commits": getattr(pull_request, "commits", None),
            "changed_files": getattr(pull_request, "changed_files", None),
            "additions": getattr(pull_request, "additions", None),
            "deletions": getattr(pull_request, "deletions", None),
            "created_at": pull_request.created_at.isoformat() if pull_request.created_at else None,
            "closed_at": pull_request.closed_at.isoformat() if pull_request.closed_at else None,
            "updated_at": pull_request.updated_at.isoformat() if pull_request.updated_at else None,
        }

    def _collect_pull_requests(self, pulls, limit: int, base_branch: Optional[str] = None) -> list[dict]:
        """
        Collect pull requests up to a specified limit, optionally filtered by base branch.
        """
        items = []

        for pull_request in pulls:
            if len(items) >= limit:
                break
            if base_branch and pull_request.base.ref != base_branch:
                continue

            items.append(self._pull_request_to_dict(pull_request))
        return items

    def _get_repository_content_text(self, candidate_paths: list[str]) -> Optional[str]:
        """
        Try to retrieve text content from a list of potential file paths in the repository.
        Returns the content of the first found file, or None if none are found.
        This is used for fetching CONTRIBUTING.md and PR templates.
        """
        for path in candidate_paths:
            try:
                content_file = self.repo.get_contents(path)
                if isinstance(content_file, list):
                    continue
                return content_file.decoded_content.decode("utf-8", errors="replace")
            except GithubException:
                continue
        return None

    def create_branch(self, branch_name: str, base_branch: str = "main") -> dict:
        """
        Create a new branch from base_branch.
        """
        try:
            base_ref = self.git_repo.remotes.origin.refs[base_branch]
            self.git_repo.create_head(branch_name, base_ref)
            self.git_repo.remotes.origin.push(branch_name)
            return {"success": True, "branch": branch_name}
        except GitCommandError as e:
            return {"success": False, "error": str(e)}

    def checkout_branch(self, branch_name: str) -> dict:
        """
        Checkout to a specific branch.
        """
        try:
            self.git_repo.heads[branch_name].checkout()
            return {"success": True, "branch": branch_name}
        except GitCommandError as e:
            return {"success": False, "error": str(e)}

    def commit_changes(self, files: list[str], message: str, author_name: str = "SoraBot") -> dict:
        """
        Stage and commit changes.
        """
        try:
            for file in files:
                self.git_repo.index.add([file])
            self.git_repo.index.commit(
                message, author_name=author_name, committer_name="SoraBot"
            )
            return {"success": True, "message": message}
        except GitCommandError as e:
            return {"success": False, "error": str(e)}

    def push_branch(self, branch_name: str) -> dict:
        """
        Push branch to origin.
        """
        try:
            self.git_repo.remotes.origin.push(branch_name)
            return {"success": True, "branch": branch_name}
        except GitCommandError as e:
            return {"success": False, "error": str(e)}

    def create_pull_request(self, branch_name: str, title: str, body: str = "", base_branch: str = "main",
        reviewers: Optional[list[str]] = None) -> dict:
        """
        Create a pull request from branch_name to base_branch, optionally with reviewers.
        """
        try:
            pr = self.repo.create_pull(
                title=title, body=body, head=branch_name, base=base_branch
            )
            result = {
                "success": True,
                "pr_number": pr.number,
                "pr_url": pr.html_url,
            }

            if reviewers:
                assign_result = self.assign_reviewers(pr.number, reviewers)
                result["reviewers_assigned"] = assign_result.get("assigned_reviewers", [])
                result["reviewers_errors"] = assign_result.get("errors", [])
            return result

        except GithubException as e:
            return {"success": False, "error": str(e)}

    def assign_reviewers(self, pr_number: int, reviewers: list[str]) -> dict:
        """
        Assign reviewers to a pull request.
        """
        try:
            pr = self.repo.get_pull(pr_number)
            assigned = []
            errors = []

            for reviewer in reviewers:
                try:
                    # Convert username to NamedUser object
                    user_obj = self.gh.get_user(reviewer)
                    # Create review request with NamedUser object
                    pr.create_review_request(reviewers=[user_obj])
                    assigned.append(reviewer)
                except GithubException as e:
                    errors.append(f"Could not assign {reviewer}: {str(e)}")
            return {
                "success": len(assigned) > 0 or len(reviewers) == 0,
                "assigned_reviewers": assigned,
                "errors": errors,
            }

        except GithubException as e:
            return {
                "success": False,
                "assigned_reviewers": [],
                "errors": [str(e)],
            }

    def add_reviewer_to_pr(self, pr_number: int, reviewer: str) -> dict:
        """
        Add a single reviewer to an existing pull request.
        """
        try:
            pr = self.repo.get_pull(pr_number)
            user_obj = self.gh.get_user(reviewer)
            pr.create_review_request(reviewers=[user_obj])
            return {
                "success": True,
                "pr_number": pr_number,
                "reviewer": reviewer,
                "message": f"Reviewer {reviewer} added to PR #{pr_number}",
            }
        except GithubException as e:
            return {
                "success": False,
                "pr_number": pr_number,
                "reviewer": reviewer,
                "error": str(e),
            }

    def list_open_pull_requests(self, limit: int = 20, base_branch: Optional[str] = None) -> dict:
        """
        List open pull requests for the repository.
        """
        try:
            pulls = self.repo.get_pulls(state="open", sort="updated", direction="desc")
            items = self._collect_pull_requests(pulls, limit=limit, base_branch=base_branch)
            return {
                "success": True,
                "repo_full_name": f"{self.repo_owner}/{self.repo_name}",
                "count": len(items),
                "pull_requests": items,
            }

        except GithubException as e:
            return {"success": False, "error": str(e)}

    def get_pull_request_status(self, pull_number: int) -> dict:
        """
        Return a detailed status summary for one PR.
        """
        try:
            pull_request = self.repo.get_pull(pull_number)
            return {
                "success": True,
                "repo_full_name": f"{self.repo_owner}/{self.repo_name}",
                **self._pull_request_to_dict(pull_request),
            }

        except GithubException as e:
            return {"success": False, "error": str(e)}

    def find_pull_requests_by_branch(self, branch_name: str) -> dict:
        """
        Find pull requests originating from a specific branch.
        """
        try:
            pulls = self.repo.get_pulls(state="all", head=f"{self.repo_owner}:{branch_name}")
            items = [self._pull_request_to_dict(pull_request) for pull_request in pulls]
            return {
                "success": True,
                "repo_full_name": f"{self.repo_owner}/{self.repo_name}",
                "count": len(items),
                "pull_requests": items,
            }

        except GithubException as e:
            return {"success": False, "error": str(e)}

    def get_pull_request_by_branch(self, branch_name: str) -> dict:
        """
        Return the most relevant PR for a branch, preferring open PRs.
        """
        try:
            pulls = self.repo.get_pulls(state="all", head=f"{self.repo_owner}:{branch_name}")
            items = [self._pull_request_to_dict(pull_request) for pull_request in pulls]

            if not items:
                return {"success": True, "repo_full_name": f"{self.repo_owner}/{self.repo_name}", "count": 0, "pull_requests": []}

            open_items = [item for item in items if item["state"] == "open" and not item.get("merged")]
            preferred = open_items[0] if open_items else items[0]
            return {
                "success": True,
                "repo_full_name": f"{self.repo_owner}/{self.repo_name}",
                "count": len(items),
                "preferred_pull_request": preferred,
                "pull_requests": items,
            }

        except GithubException as e:
            return {"success": False, "error": str(e)}

    def get_pull_request_body_by_branch(self, branch_name: str) -> dict:
        """
        Get the description/body of the most relevant PR for a branch.
        """
        result = self.get_pull_request_by_branch(branch_name)

        if not result.get("success"):
            return result

        preferred = result.get("preferred_pull_request")
        if not preferred:
            return {
                "success": True,
                "repo_full_name": f"{self.repo_owner}/{self.repo_name}",
                "count": 0,
                "message": "Aucune PR trouvée pour cette branche.",
                "pull_request": None,
            }
        return {
            "success": True,
            "repo_full_name": f"{self.repo_owner}/{self.repo_name}",
            "count": result.get("count", 0),
            "pull_request": preferred,
            "pull_requests": result.get("pull_requests", []),
        }

    def get_pr_context(self, branch_name: str, base_branch: str = "main") -> dict:
        """
        Collect branch diff data and contribution guidelines for PR drafting.
        """
        try:
            base_commit = self.repo.get_branch(base_branch).commit.sha
            head_commit = self.repo.get_branch(branch_name).commit.sha
            comparison = self.repo.compare(base_commit, head_commit)

            files_changed = []
            for file in comparison.files:
                files_changed.append(
                    {
                        "filename": file.filename,
                        "status": file.status,
                        "additions": getattr(file, "additions", None),
                        "deletions": getattr(file, "deletions", None),
                        "changes": getattr(file, "changes", None),
                    }
                )

            commit_messages = [commit.commit.message for commit in comparison.commits]
            return {
                "success": True,
                "repo_full_name": f"{self.repo_owner}/{self.repo_name}",
                "base_branch": base_branch,
                "head_branch": branch_name,
                "files_changed": files_changed,
                "commit_messages": commit_messages,
                "contributing_guidelines": self.get_contributing_guidelines(),
                "pull_request_template": self.get_pull_request_template(),
            }

        except GithubException as e:
            return {"success": False, "error": str(e)}

    def get_contributing_guidelines(self) -> Optional[str]:
        """
        Return CONTRIBUTING.md content if it exists in common locations.
        """
        return self._get_repository_content_text([
            "CONTRIBUTING.md",
            ".github/CONTRIBUTING.md",
            "docs/CONTRIBUTING.md",
        ])

    def get_pull_request_template(self) -> Optional[str]:
        """
        Return a pull request template if the repository defines one.
        """
        return self._get_repository_content_text([
            ".github/pull_request_template.md",
            "pull_request_template.md",
            "PULL_REQUEST_TEMPLATE.md",
        ])

    def get_pr_conflicts(self, branch_name: str, base_branch: str = "main") -> dict:
        """
        Check for merge conflicts between branch_name and base_branch.
        """
        try:
            base_commit = self.repo.get_branch(base_branch).commit.sha
            branch_commit = self.repo.get_branch(branch_name).commit.sha
            comparison = self.repo.compare(base_commit, branch_commit)
            conflicting_files = []

            for file in comparison.files:
                if file.status == "modified":
                    conflicting_files.append(file.filename)
            return {
                "success": True,
                "has_conflicts": len(conflicting_files) > 0,
                "conflicting_files": conflicting_files,
            }

        except GithubException as e:
            return {"success": False, "error": str(e)}

    def get_branch_info(self, branch_name: str) -> dict:
        """
        Get information about a branch.
        """
        try:
            branch = self.repo.get_branch(branch_name)
            return {
                "success": True,
                "branch": branch_name,
                "commit_sha": branch.commit.sha,
                "commit_message": branch.commit.commit.message,
            }

        except GithubException as e:
            return {"success": False, "error": str(e)}
