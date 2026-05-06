##
## SORABOT, 2026
## chat_agent_utils.py
## File description:
## Utility functions for parsing user messages and formatting PR data for the Discord chat agent.
##

from __future__ import annotations

import json
import re
from typing import Optional

def parse_json_object(text: str) -> dict:
    """
    Parse the first JSON object found in a text blob.
    """
    if not text:
        return {}

    candidate = text
    if "```" in candidate:
        match = re.search(r"\{[\s\S]*\}", candidate)
        if match:
            candidate = match.group(0)

    try:
        data = json.loads(candidate)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def extract_branch_name(message: str) -> Optional[str]:
    """Extract branch name from a short command-like message."""
    words = message.split()
    for index, word in enumerate(words):
        if word in ["branche", "branch", "#"] and index + 1 < len(words):
            return words[index + 1].strip("#")
    return None

def extract_repo_target(message: str) -> tuple[Optional[str], Optional[str]]:
    """
    Extract target owner/repo from free text.
    Supported examples:
    - "dans le repo Test-AI-Agent"
    - "dans le répo Test-AI-Agent"
    - "repo:Andriamanitra/Test-AI-Agent"
    - "pour Andriamanitra/Test-AI-Agent"
    """
    clean_message = message.strip()
    explicit = re.search(r"repo:([\w.-]+(?:/[\w.-]+)?)", clean_message, flags=re.IGNORECASE)

    if explicit:
        return _split_owner_repo(explicit.group(1))

    owner_repo = re.search(r"\b([\w.-]+)/([\w.-]+)\b", clean_message)
    if owner_repo:
        return owner_repo.group(1), owner_repo.group(2)

    natural = re.search(
        r"(?:dans|in|into)\s+(?:le|la|du|de\s+la|the)?\s*(?:repo|répo|depot|dépôt)?\s*([\w.-]+)",
        clean_message,
        flags=re.IGNORECASE,
    )
    if natural:
        return None, natural.group(1)
    return None, None

def extract_pr_numbers(message: str) -> list[int]:
    """
    Extract PR numbers from a free-form message.
    """
    numbers = set()
    explicit_patterns = [
        r"(?:pull\s*request|pr|status\s+pr|statut\s+pr|détails?\s+pr|details?\s+pr|numéro|numero)\s*#?(\d{1,6})",
        r"#(\d{1,6})",
    ]

    for pattern in explicit_patterns:
        for match in re.findall(pattern, message, flags=re.IGNORECASE):
            try:
                numbers.add(int(match))
            except ValueError:
                continue

    for match in re.findall(r"/pull/(\d{1,6})", message, flags=re.IGNORECASE):
        try:
            numbers.add(int(match))
        except ValueError:
            continue
    return sorted(numbers)

def extract_pr_title(message: str) -> str:
    """
    Extract a potential PR title fragment from the message."""
    lowered = message.lower()
    patterns = [
        r"(?:pr|pull request|pull).{0,20}?(?:nommée|nomme|intitulée|title|titre)\s+(.+)$",
        r"(?:du|de la|de)\s+pr\s+(.+)$",
    ]

    for pattern in patterns:
        match = re.search(pattern, lowered, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip().strip("\"'.,;:!?")
    return ""

def extract_branch_from_message(message: str) -> str:
    """
    Try to infer a branch name from a free-form message.
    """
    patterns = [
        r"(?:branche|branch|sur)\s+([\w./-]+)",
        r"(?:pr|pull request)\s+([\w./-]+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, message, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip().strip("\"'.,;:!?")
    return ""


def format_pr_description(pr_data: dict, body: str) -> str:
    """
    Format a PR description for Discord output.
    """
    lines = [
        f"PR #{pr_data.get('number')} - {pr_data.get('title')}",
        f"Statut: {pr_data.get('state')} | Draft: {'oui' if pr_data.get('draft') else 'non'} | Mergeable: {pr_data.get('mergeable_state', 'unknown')}",
        f"Branche: {pr_data.get('head_branch')} -> {pr_data.get('base_branch')}",
        f"URL: {pr_data.get('url')}",
        "",
        "Description:",
        body.strip() or "Aucune description disponible.",
    ]

    labels = pr_data.get("labels") or []
    if labels:
        lines.insert(4, f"Labels: {', '.join(labels)}")
    return "\n".join(lines)


def extract_reviewers(message: str) -> list[str]:
    """
    Extract reviewer usernames from a message.
    Supported formats:
    - "reviewer:user1,user2"
    - "reviewers: @user1 @user2"
    - "avec user1 user2"
    - "ajoutes/assigner/add user1" or "ajoutes user1 en reviewer"
    """
    reviewers = []

    match = re.search(r"reviewers?\s*:\s*([\w\s,@-]+)", message, flags=re.IGNORECASE)
    if match:
        reviewer_str = match.group(1)
        reviewers.extend([r.strip().lstrip("@") for r in re.split(r"[,\s]+", reviewer_str) if r.strip()])
        return reviewers

    match = re.search(r"avec\s+([\w\s@-]+)", message, flags=re.IGNORECASE)
    if match:
        reviewer_str = match.group(1)
        reviewers.extend([r.strip().lstrip("@") for r in reviewer_str.split() if r.strip()])
        return reviewers

    match = re.search(r"(?:ajoutes?|assigne|assign)\s+([\w@\s-]+?)(?:\s+en\s+reviewer|\s+[àas]\s+la|\s+sur\s+|$)", message, flags=re.IGNORECASE)
    if match:
        reviewer_str = match.group(1)
        reviewers.extend([r.strip().lstrip("@") for r in reviewer_str.split() if r.strip()])
        return reviewers

    mentions = re.findall(r"@([\w-]+)", message)
    if mentions:
        reviewers.extend(mentions)
        return reviewers
    return reviewers

def _split_owner_repo(token: str) -> tuple[Optional[str], Optional[str]]:
    """
    Split a token into owner and repository components.
    """
    token = token.strip().strip("\"'.,;:!?")
    if "/" in token:
        owner, repo = token.split("/", 1)
        return owner or None, repo or None
    return None, token or None
