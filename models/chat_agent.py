##
## SORABOT, 2026
## chat_agent.py
## File description:
## The DiscordChatAgent class that processes user messages, interacts with GitHub, and generates responses using an LLM.
##

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional, TypedDict

from langgraph.graph import END, StateGraph

from models.chat_agent_utils import (
    extract_branch_from_message,
    extract_branch_name,
    extract_pr_numbers,
    extract_pr_title,
    extract_repo_target,
    extract_reviewers,
    format_pr_description,
    parse_json_object,
)
from models.conversation_memory import ConversationMemory
from models.llm import LLMClient
from models.github_tools import GitHubTools
from models.github_exceptions import GitHubAuthenticationError
from models.discord_event_tools import (
    describe_missing_fields,
    format_event_summary,
    is_event_ready,
    missing_event_fields,
    normalize_event_payload,
    search_web,
)

class AgentState(TypedDict, total=False):
    message: str
    author_name: str
    channel_name: str
    user_id: str
    api_key: Optional[str]
    environment_context: str
    conversation_history: list[dict]
    request_type: str
    target_repo: str
    target_owner: str
    target_branch: str
    target_pr_numbers: list[int]
    target_pr_title: str
    event_draft: dict
    pending_discord_event: dict
    repository_snapshot: str
    github_branch: Optional[str]
    github_pr_url: Optional[str]
    execution_log: str
    response: str


SELF_AWARENESS_INSTRUCTIONS = (
    "Tu as conscience de ton identité et de ton environnement Discord. "
    "Le bloc \"Contexte environnement\" décrit qui tu es, le serveur, le salon et ton interlocuteur. "
    "Utilise ces informations quand c'est pertinent, sans les réciter systématiquement. "
    "Si ton interlocuteur est un autre bot, reste coopératif, clair, et évite les échanges stériles. "
    "Ne prétends pas voir ou faire des choses hors de ce contexte et de tes capacités réelles. "
    "Tu peux créer des événements Discord planifiés (Scheduled Events) quand on te le demande: "
    "événements externes avec lieu, ou vocaux/stage si un salon est précisé. "
    "Si des informations manquent, pose des questions précises avant de créer."
)

EVENT_REQUEST_TYPES = {
    "create_discord_event",
    "research_event",
    "confirm_discord_event",
}

class DiscordChatAgent:
    """
    A chat agent that processes user messages, interacts with GitHub, and generates responses using an LLM.
    """
    def __init__(self, llm_client):
        self.llm_client = llm_client
        self.repo_root = Path(__file__).resolve().parents[1]
        self.memory = ConversationMemory(memory_dir=self.repo_root / "conversation_memory")
        self.workflow = self._build_workflow()

    def _chat_with_llm(self, llm_prompt, temperature=0.2, api_key: str | None = None):
        """
        Helper to call the LLM. If `api_key` is provided, instantiate a temporary LLMClient using that key.
        """
        try:
            if api_key:
                client = LLMClient(api_key=api_key)
                return client.chat(llm_prompt, temperature=temperature)
            return self.llm_client.chat(llm_prompt, temperature=temperature)
        except Exception as exc:
            return f"Erreur LLM: {exc}"

    def _build_workflow(self):
        """
        Build the agent's workflow graph.
        """
        graph = StateGraph(AgentState)
        graph.add_node("classify", self._classify_request)
        graph.add_node("collect_context", self._collect_context)
        graph.add_node("execute_action", self._execute_action)
        graph.add_node("draft_response", self._draft_response)
        graph.set_entry_point("classify")
        graph.add_edge("classify", "collect_context")
        graph.add_edge("collect_context", "execute_action")
        graph.add_edge("execute_action", "draft_response")
        graph.add_edge("draft_response", END)
        return graph.compile()

    def handle_message(
        self,
        message: str,
        author_name: str,
        channel_name: str,
        user_id: Optional[str] = None,
        api_key: Optional[str] = None,
        environment_context: Optional[str] = None,
    ) -> dict:
        """
        Main entry point to handle an incoming message.

        Returns a dict with:
        - response: text reply for Discord
        - pending_discord_event: optional payload for the bot to create a scheduled event
        """
        if not user_id:
            user_id = author_name

        conversation_history = self.memory.get_conversation_history(user_id, max_messages=10)
        state = {
            "message": message.strip(),
            "author_name": author_name,
            "channel_name": channel_name,
            "user_id": user_id,
            "api_key": api_key,
            "environment_context": environment_context or "",
            "conversation_history": conversation_history,
            "execution_log": "",
            "event_draft": {},
            "pending_discord_event": {},
        }
        result = self.workflow.invoke(state)
        response = result.get("response", "")
        pending_event = result.get("pending_discord_event") or {}
        self.memory.add_exchange(user_id, message.strip(), response)
        return {
            "response": response,
            "pending_discord_event": pending_event if pending_event else None,
        }

    def _classify_request(self, state: AgentState) -> AgentState:
        """
        Classify the user's request type and extract relevant parameters using the LLM.
        """
        message = state.get("message", "")
        conversation_history = state.get("conversation_history", [])
        llm_intent = self._extract_intent_with_llm(
            message,
            conversation_history,
            api_key=state.get("api_key"),
        )
        request_type = llm_intent.get("request_type")
        event_draft = normalize_event_payload(llm_intent.get("event") or {})

        if not request_type:
            lowered = message.lower()
            wants_event_create = any(
                keyword in lowered
                for keyword in [
                    "créer un event",
                    "creer un event",
                    "crée un event",
                    "cree un event",
                    "créer un événement",
                    "creer un evenement",
                    "crée un événement",
                    "cree un evenement",
                    "planifie un event",
                    "planifier un event",
                    "scheduled event",
                    "crée l'event",
                    "cree l'event",
                    "créer l'événement",
                    "creer l'evenement",
                ]
            )
            wants_event_research = any(
                keyword in lowered
                for keyword in [
                    "trouve moi quand",
                    "trouve quand",
                    "recherche l'événement",
                    "recherche l'evenement",
                    "quand se déroule",
                    "quand se deroule",
                    "à quelle date",
                    "a quelle date",
                ]
            )
            if wants_event_create and wants_event_research:
                request_type = "create_discord_event"
                event_draft = normalize_event_payload({**event_draft, "needs_research": True})
            elif wants_event_create:
                request_type = "create_discord_event"
            elif wants_event_research:
                request_type = "research_event"
            elif any(keyword in lowered for keyword in ["oui", "ok", "confirme", "vas-y", "crée-le", "cree-le", "go"]):
                if self._history_mentions_event_proposal(conversation_history):
                    request_type = "confirm_discord_event"
            elif any(keyword in lowered for keyword in ["créer pr", "pull request", "merge request", "ouvrir une pr"]):
                request_type = "create_pr"
            elif any(keyword in lowered for keyword in ["branche", "créer branche", "nouvelle branche"]):
                request_type = "create_branch"
            elif any(keyword in lowered for keyword in ["status pr", "statut pr", "statut des pr", "status des pr", "état pr", "liste pr", "pr ouvertes", "pr en cours", "open pr", "open pulls"]):
                request_type = "list_open_prs"
            elif any(keyword in lowered for keyword in ["statut de la pr", "status de la pr", "état de la pr", "détails pr", "details pr", "statut pr #", "status pr #"]):
                request_type = "pr_status"
            elif any(keyword in lowered for keyword in ["description de la pr", "description pr", "body pr", "contenu de la pr", "résumé de la pr", "detail de la pr", "détail de la pr"]):
                request_type = "pr_description"
            elif any(keyword in lowered for keyword in ["reviewer", "ajouter reviewer", "ajout reviewer", "add reviewer", "assigner reviewer", "assign reviewer"]):
                request_type = "add_reviewer"
            elif any(keyword in lowered for keyword in ["bug", "erreur", "fix", "corrige", "problème", "issue"]):
                request_type = "bug_fix"
            elif any(keyword in lowered for keyword in ["conflict", "conflit", "merge"]):
                request_type = "merge_conflict"
            else:
                request_type = "general_assistance"

        target_owner = llm_intent.get("target_owner")
        target_repo = llm_intent.get("target_repo")
        target_branch = llm_intent.get("target_branch")
        target_pr_numbers = llm_intent.get("target_pr_numbers") or []
        target_pr_title = llm_intent.get("target_pr_title") or ""

        if not target_owner and not target_repo:
            target_owner, target_repo = extract_repo_target(message.lower())

        if not target_branch:
            target_branch = extract_branch_name(message.lower())

        if not target_pr_numbers:
            target_pr_numbers = extract_pr_numbers(message)

        if not target_pr_title:
            target_pr_title = extract_pr_title(message)

        return {
            **state,
            "request_type": request_type,
            "target_owner": target_owner,
            "target_repo": target_repo,
            "target_branch": target_branch,
            "target_pr_numbers": target_pr_numbers,
            "target_pr_title": target_pr_title,
            "event_draft": event_draft,
        }

    def _history_mentions_event_proposal(self, conversation_history: list[dict]) -> bool:
        """
        Detect whether the recent history contains an event draft awaiting confirmation.
        """
        for msg in reversed(conversation_history[-6:]):
            content = (msg.get("content") or "").lower()
            if msg.get("role") not in ("assistant", "bot"):
                continue
            if any(
                marker in content
                for marker in (
                    "événement proposé",
                    "evenement propose",
                    "je peux créer l'événement",
                    "je peux creer l'evenement",
                    "confirmes-tu",
                    "confirmes tu",
                    "draft événement",
                    "draft evenement",
                )
            ):
                return True
        return False

    def _collect_context(self, state: AgentState) -> AgentState:
        """
        Collect any additional context needed to execute the request, such as repository snapshots.
        """
        repository_snapshot = self._git_snapshot()
        return {**state, "repository_snapshot": repository_snapshot}

    def _get_github_tools(self, state: AgentState) -> tuple[Optional[GitHubTools], str, str, str]:
        """
        Initialize and return the GitHubTools instance with the necessary credentials and repository information.
        """
        target_repo = state.get("target_repo", "")
        target_owner = state.get("target_owner", "")
        user_id = state.get("user_id", "")

        if not target_repo and not os.getenv("GITHUB_REPO_NAME"):
            return None, "", "", "Error: repository not specified. Use 'dans nom-du-repo' or 'pour owner/repo'."

        repo_name = target_repo or os.getenv("GITHUB_REPO_NAME", "")
        repo_owner = target_owner or os.getenv("GITHUB_REPO_OWNER", "")

        try:
            return GitHubTools(repo_owner, repo_name, user_id=user_id), repo_owner, repo_name, ""
        except GitHubAuthenticationError as exc:
            suggestion = (
                f"\n\n**Suggestion:** Your current access to `{repo_owner}/{repo_name}` is insufficient.\n"
                f"Would you like to link your own GitHub token? Here's how:\n\n"
                f"**Step 1:** Create a personal GitHub token:\n"
                f"→ Go to https://github.com/settings/tokens/new\n"
                f"→ Select scopes: `repo`, `workflow`\n"
                f"→ Copy the token\n\n"
                f"**Step 2:** Link it to SoraBot:\n"
                f"→ Use `/link-github <your_token>`\n\n"
                f"Then try again!"
            )
            error_msg = f"{exc.message}{suggestion}"
            return None, repo_owner, repo_name, error_msg
        except ValueError as exc:
            return None, repo_owner, repo_name, f"Error: GitHub setup failed: {exc}"

    def _format_pull_request_lines(self, pull_requests: list[dict], repo_owner: str,
        repo_name: str, heading: str) -> str:
        """
        Format a list of pull requests into a readable string.
        """
        if not pull_requests:
            return f"No pull requests on {repo_owner}/{repo_name}."

        lines = [f"{heading} {repo_owner}/{repo_name} ({len(pull_requests)}):"]
        for pull_request in pull_requests[:10]:
            draft_label = "draft" if pull_request.get("draft") else "ready"
            mergeable_state = pull_request.get("mergeable_state") or "unknown"
            lines.append(
                f"- #{pull_request['number']} {pull_request['title']} [{draft_label}, {mergeable_state}] "
                f"({pull_request['head_branch']} -> {pull_request['base_branch']})"
            )
        if len(pull_requests) > 10:
            lines.append(f"... and {len(pull_requests) - 10} more")
        return "\n".join(lines)

    def _handle_create_branch_action(self, github_tools: GitHubTools, state: AgentState,
        repo_owner: str, repo_name: str, message: str, parsed_branch_name: str,) -> tuple[AgentState, str]:
        branch_name = parsed_branch_name or extract_branch_name(message)
        if not branch_name:
            return state, "Warning: branch name not detected."

        result = github_tools.create_branch(branch_name)
        if result["success"]:
            updated_state = {**state, "github_branch": branch_name}
            return updated_state, f"Branch created: `{branch_name}` in {repo_owner}/{repo_name}."

        return state, f"Error creating branch: {result['error']}"

    def _handle_create_pr_action(self, github_tools: GitHubTools, state: AgentState, repo_owner: str,
        repo_name: str, message: str, parsed_branch_name: str,) -> tuple[AgentState, str]:
        """
        Handle the action of creating a pull request.
        """
        branch_name = parsed_branch_name or extract_branch_name(message)

        if not branch_name:
            return state, "Warning: branch name not detected."

        conflict_result = github_tools.get_pr_conflicts(branch_name)
        if not conflict_result["success"]:
            return state, f"Error checking conflicts: {conflict_result.get('error', 'Unknown')}"

        if conflict_result["has_conflicts"]:
            return state, f"Conflicts detected: {', '.join(conflict_result['conflicting_files'])}"

        pr_context = github_tools.get_pr_context(branch_name)
        if not pr_context.get("success"):
            execution_log = f"Error reading pull request context: {pr_context.get('error', 'Unknown')}"
            return {**state, "execution_log": execution_log}, execution_log

        pr_metadata = self._generate_pr_metadata(pr_context, state)
        reviewers = extract_reviewers(message)
        pr_result = github_tools.create_pull_request(
            branch_name,
            title=pr_metadata["title"],
            body=pr_metadata["body"],
            reviewers=reviewers if reviewers else None,
        )

        if pr_result["success"]:
            updated_state = {**state, "github_pr_url": pr_result["pr_url"]}
            execution_log = (
                f"Pull request created on {repo_owner}/{repo_name}: {pr_result['pr_url']}\n"
                f"Title: {pr_metadata['title']}"
            )
            if pr_result.get("reviewers_assigned"):
                execution_log += f"\nReviewers assigned: {', '.join(pr_result['reviewers_assigned'])}"
            if pr_result.get("reviewers_errors"):
                execution_log += f"\nReviewer errors: {'; '.join(pr_result['reviewers_errors'])}"
            return updated_state, execution_log
        return state, f"Error creating pull request: {pr_result['error']}"

    def _handle_list_open_prs_action(self, github_tools: GitHubTools, repo_owner: str,
        repo_name: str,) -> str:
        pr_result = github_tools.list_open_pull_requests(limit=20)
        if not pr_result["success"]:
            return f"Error listing pull requests: {pr_result.get('error', 'Unknown')}"

        pull_requests = pr_result.get("pull_requests", [])
        if not pull_requests:
            return f"No open pull requests on {repo_owner}/{repo_name}."

        return self._format_pull_request_lines(pull_requests, repo_owner, repo_name, "Open pull requests on")

    def _handle_pr_status_action(self, github_tools: GitHubTools, repo_owner: str,
        repo_name: str, parsed_pr_numbers: list[int],) -> str:
        """
        Handle the action of checking pull request status.
        """
        if parsed_pr_numbers:
            return self._build_pr_status_list(github_tools, parsed_pr_numbers, repo_owner, repo_name)

        pr_result = github_tools.list_open_pull_requests(limit=20)
        if not pr_result["success"]:
            return f"Error checking pull request status: {pr_result.get('error', 'Unknown')}"

        pull_requests = pr_result.get("pull_requests", [])
        if not pull_requests:
            return f"No pull requests in progress on {repo_owner}/{repo_name}."
        return self._format_pull_request_lines(pull_requests, repo_owner, repo_name, "Pull requests in progress on")

    def _build_pr_status_list(self, github_tools: GitHubTools,
        parsed_pr_numbers: list[int], repo_owner: str, repo_name: str,) -> str:
        """
        Build a list of pull request statuses.
        """
        statuses = []

        for pr_number in parsed_pr_numbers:
            pr_result = github_tools.get_pull_request_status(int(pr_number))
            if pr_result["success"]:
                draft_label = "draft" if pr_result.get("draft") else "ready"
                merged_label = "merged" if pr_result.get("merged") else pr_result.get("state", "unknown")
                statuses.append(
                    f"- #{pr_result['number']} {pr_result['title']} [{draft_label}, {merged_label}, {pr_result.get('mergeable_state', 'unknown')}] "
                    f"({pr_result.get('head_branch')} -> {pr_result.get('base_branch')}) {pr_result.get('url')}"
                )
            else:
                statuses.append(f"- #{pr_number} Error: {pr_result.get('error', 'Unknown')}")
        return f"Pull request status on {repo_owner}/{repo_name}:\n" + "\n".join(statuses)

    def _handle_pr_description_action(self, github_tools: GitHubTools, message: str,
        parsed_branch_name: str, parsed_pr_numbers: list[int], parsed_pr_title: str,) -> str:
        """
        Handle the action of fetching a pull request description.
        """
        if parsed_pr_numbers:
            return self._describe_pull_request_by_number(github_tools, int(parsed_pr_numbers[0]))

        branch_hint = parsed_branch_name or extract_branch_from_message(message)
        if branch_hint:
            return self._describe_pull_request_by_branch(github_tools, branch_hint)

        if parsed_pr_title:
            return self._describe_pull_request_by_title(github_tools, parsed_pr_title)
        return "Provide a pull request number, branch, or title fragment so I can read its description."

    def _describe_pull_request_by_number(self, github_tools: GitHubTools, pr_number: int) -> str:
        """
        Describe a pull request by its number.
        """
        pr_result = github_tools.get_pull_request_status(pr_number)

        if not pr_result["success"]:
            return f"Error reading pull request #{pr_number}: {pr_result.get('error', 'Unknown')}"

        body = pr_result.get("body") or "Aucune description disponible."
        return format_pr_description(pr_result, body)

    def _describe_pull_request_by_branch(self, github_tools: GitHubTools, branch_hint: str) -> str:
        """
        Describe a pull request by its branch.
        """
        pr_result = github_tools.get_pull_request_body_by_branch(branch_hint)
        if not pr_result["success"]:
            return f"Error reading pull request: {pr_result.get('error', 'Unknown')}"

        pr_data = pr_result.get("pull_request")
        if not pr_data:
            return f"No pull request found for branch `{branch_hint}`."
        return format_pr_description(pr_data, pr_data.get("body") or "Aucune description disponible.")

    def _describe_pull_request_by_title(self, github_tools: GitHubTools, parsed_pr_title: str) -> str:
        """
        Describe a pull request by searching for a title fragment.
        """
        pr_result = github_tools.list_open_pull_requests(limit=20)

        if not pr_result["success"]:
            return f"Error listing pull requests: {pr_result.get('error', 'Unknown')}"

        match = next(
            (
                item for item in pr_result.get("pull_requests", [])
                if parsed_pr_title.lower() in item.get("title", "").lower()
            ),
            None,
        )
        if not match:
            return f"No pull request found for title `{parsed_pr_title}`."
        return format_pr_description(match, match.get("body") or "Aucune description disponible.")

    def _handle_merge_conflict_action(self, github_tools: GitHubTools) -> str:
        """
        Handle the action of checking for merge conflicts on the current branch.
        """
        current_branch = self._get_current_branch()
        conflict_result = github_tools.get_pr_conflicts(current_branch)
        if not conflict_result["success"]:
            return f"Error: {conflict_result.get('error', 'Unknown')}"

        if conflict_result["has_conflicts"]:
            return f"Conflicts detected on `{current_branch}`: {', '.join(conflict_result['conflicting_files'])}"
        return f"No conflicts on `{current_branch}`."

    def _handle_add_reviewer_action(self, github_tools: GitHubTools, message: str, pr_numbers: list[int],) -> str:
        """
        Add reviewers to an existing pull request.
        """
        if not pr_numbers:
            return "Error: No PR number found in your message. Please specify the PR number (e.g., 'PR #7')."

        reviewers = extract_reviewers(message)
        if not reviewers:
            return "Error: No reviewers found in your message. Please specify reviewers (e.g., 'reviewer:alice,bob' or '@alice @bob')."

        results = []
        for pr_number in pr_numbers:
            pr_results = github_tools.assign_reviewers(pr_number, reviewers)
            if pr_results["success"]:
                assigned_str = ", ".join(pr_results.get("assigned_reviewers", []))
                results.append(f"✅ PR #{pr_number}: Added {assigned_str} as reviewer(s)")
            else:
                errors_str = " | ".join(pr_results.get("errors", ["Unknown error"]))
                results.append(f"❌ PR #{pr_number}: {errors_str}")
        return "\n".join(results)

    def _execute_action(self, state: AgentState) -> AgentState:
        """
        Execute operations if needed based on request type.
        """
        request_type = state.get("request_type", "general_assistance")

        if request_type in EVENT_REQUEST_TYPES:
            return self._execute_discord_event_action(state)

        if request_type == "general_assistance":
            return {**state, "execution_log": "", "pending_discord_event": {}}

        execution_log = ""
        github_tools, repo_owner, repo_name, setup_error = self._get_github_tools(state)

        if setup_error:
            return {**state, "execution_log": setup_error}

        message = state.get("message", "").lower()
        parsed_branch_name = state.get("target_branch", "")
        parsed_pr_numbers = state.get("target_pr_numbers", [])
        parsed_pr_title = state.get("target_pr_title", "")

        try:
            if request_type == "create_branch":
                state, execution_log = self._handle_create_branch_action(
                    github_tools,
                    state,
                    repo_owner,
                    repo_name,
                    message,
                    parsed_branch_name,
                )
            elif request_type == "create_pr":
                state, execution_log = self._handle_create_pr_action(
                    github_tools,
                    state,
                    repo_owner,
                    repo_name,
                    message,
                    parsed_branch_name,
                )
            elif request_type == "list_open_prs":
                execution_log = self._handle_list_open_prs_action(github_tools, repo_owner, repo_name)
            elif request_type == "pr_status":
                execution_log = self._handle_pr_status_action(
                    github_tools,
                    repo_owner,
                    repo_name,
                    parsed_pr_numbers,
                )
            elif request_type == "pr_description":
                execution_log = self._handle_pr_description_action(
                    github_tools,
                    message,
                    parsed_branch_name,
                    parsed_pr_numbers,
                    parsed_pr_title,
                )
            elif request_type == "merge_conflict":
                execution_log = self._handle_merge_conflict_action(github_tools)
            elif request_type == "add_reviewer":
                execution_log = self._handle_add_reviewer_action(
                    github_tools,
                    message,
                    parsed_pr_numbers,
                )

        except Exception as e:
            execution_log = f"Execution error: {str(e)}"
        return {**state, "execution_log": execution_log}

    def _execute_discord_event_action(self, state: AgentState) -> AgentState:
        """
        Research and/or prepare a Discord scheduled event payload.
        """
        request_type = state.get("request_type", "create_discord_event")
        event_draft = normalize_event_payload(state.get("event_draft") or {})
        api_key = state.get("api_key")
        message = state.get("message", "")
        conversation_history = state.get("conversation_history", [])

        research_notes = ""
        did_research_this_turn = False
        if request_type == "research_event" or event_draft.get("needs_research"):
            did_research_this_turn = True
            query = event_draft.get("research_query") or event_draft.get("name") or message
            research_notes = search_web(query)
            researched = self._extract_event_from_research(
                query=query,
                research_notes=research_notes,
                existing_draft=event_draft,
                api_key=api_key,
            )
            event_draft = normalize_event_payload({**event_draft, **researched})

        if request_type == "confirm_discord_event":
            history_draft = self._extract_event_from_history(
                conversation_history,
                api_key=api_key,
            )
            event_draft = normalize_event_payload({**history_draft, **event_draft, "confirmed": True})

        missing = missing_event_fields(event_draft)
        pending_event: dict = {}

        # After research, always propose first and wait for explicit confirmation.
        # Immediate creation is only for create requests that already had full details.
        should_create = is_event_ready(event_draft) and (
            request_type == "confirm_discord_event"
            or event_draft.get("confirmed")
            or (request_type == "create_discord_event" and not did_research_this_turn)
        )

        if should_create:
            pending_event = {
                "name": event_draft["name"],
                "description": event_draft.get("description"),
                "start_time": event_draft["start_time"],
                "end_time": event_draft.get("end_time"),
                "entity_type": event_draft.get("entity_type") or "external",
                "location": event_draft.get("location"),
                "channel_id": event_draft.get("channel_id"),
            }
            execution_log = (
                "Discord event ready to create.\n"
                f"{format_event_summary(pending_event)}"
            )
        elif is_event_ready(event_draft):
            execution_log = (
                "Research complete. Event proposal ready, waiting for confirmation.\n"
                f"{format_event_summary(event_draft)}"
            )
            if research_notes:
                execution_log += f"\nResearch notes:\n{research_notes}"
        else:
            execution_log = (
                "Incomplete event information.\n"
                f"Current draft:\n{format_event_summary(event_draft)}\n"
                f"Missing fields: {describe_missing_fields(missing)}"
            )
            if research_notes:
                execution_log += f"\nResearch notes:\n{research_notes}"

        return {
            **state,
            "event_draft": event_draft,
            "pending_discord_event": pending_event,
            "execution_log": execution_log,
        }

    def _extract_event_from_research(
        self,
        *,
        query: str,
        research_notes: str,
        existing_draft: dict,
        api_key: Optional[str],
    ) -> dict:
        """
        Use the LLM to turn web research notes into a structured event draft.
        """
        prompt = [
            {
                "role": "system",
                "content": (
                    "Tu extrais les détails d'un événement réel à partir de notes de recherche. "
                    "Réponds uniquement en JSON strict. "
                    "Schéma: {"
                    '"name":"string|null",'
                    '"description":"string|null",'
                    '"start_time":"ISO-8601|null",'
                    '"end_time":"ISO-8601|null",'
                    '"entity_type":"external|voice|stage",'
                    '"location":"string|null",'
                    '"channel_id":null,'
                    '"confirmed":false'
                    "}. "
                    "Fuseau horaire par défaut: Europe/Paris. "
                    "Si l'année n'est pas claire, préfère l'occurrence la plus proche dans le futur. "
                    "Pour un lieu physique, entity_type=external. "
                    "Si la recherche web a échoué, tu peux utiliser tes connaissances avec prudence; "
                    "mets null dès qu'une info n'est pas fiable."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Requête: {query}\n\n"
                    f"Draft existant:\n{format_event_summary(existing_draft)}\n\n"
                    f"Notes de recherche:\n{research_notes}"
                ),
            },
        ]
        response = self._chat_with_llm(prompt, temperature=0, api_key=api_key)
        return parse_json_object((response or "").strip())

    def _extract_event_from_history(
        self,
        conversation_history: list[dict],
        *,
        api_key: Optional[str],
    ) -> dict:
        """
        Recover an event draft previously proposed in the conversation.
        """
        history_text = self._format_history_context(conversation_history) or "(vide)"
        prompt = [
            {
                "role": "system",
                "content": (
                    "À partir de l'historique, reconstitue le draft d'événement Discord déjà proposé. "
                    "Réponds uniquement en JSON strict avec le schéma: "
                    "{"
                    '"name":"string|null",'
                    '"description":"string|null",'
                    '"start_time":"ISO-8601|null",'
                    '"end_time":"ISO-8601|null",'
                    '"entity_type":"external|voice|stage",'
                    '"location":"string|null",'
                    '"channel_id":null'
                    "}."
                ),
            },
            {"role": "user", "content": history_text},
        ]
        response = self._chat_with_llm(prompt, temperature=0, api_key=api_key)
        return parse_json_object((response or "").strip())

    def _draft_response(self, state: AgentState) -> AgentState:
        """
        Draft a response to the user based on the request type, message, context, and execution results.
        """
        request_type = state.get("request_type", "general_assistance")
        message = state.get("message", "")
        author_name = state.get("author_name", "")
        repository_snapshot = state.get("repository_snapshot", "")
        execution_log = state.get("execution_log", "")
        conversation_history = state.get("conversation_history", [])
        environment_context = state.get("environment_context", "")
        event_draft = state.get("event_draft") or {}
        pending_event = state.get("pending_discord_event") or {}

        prompt = self._build_response_prompt(
            request_type=request_type,
            message=message,
            author_name=author_name,
            channel_name=state.get("channel_name", ""),
            repository_snapshot=repository_snapshot,
            execution_log=execution_log,
            conversation_history=conversation_history,
            environment_context=environment_context,
            event_draft=event_draft,
            pending_discord_event=pending_event,
        )

        api_key = state.get("api_key")
        response = self._chat_with_llm(prompt, temperature=0.2, api_key=api_key)
        if state.get("github_pr_url"):
            response += f"\n\nPR URL: {state['github_pr_url']}"
        if state.get("github_branch"):
            response += f"\nBranch: {state['github_branch']}"
        return {**state, "response": response}

    def _format_history_context(self, conversation_history: list[dict]) -> str:
        if not conversation_history:
            return ""

        history_lines = ["=== Historique de la conversation ==="]
        for msg in conversation_history[-5:]:
            role_display = "Utilisateur" if msg.get("role") == "user" else "SoraBot"
            content = msg.get("content", "")
            if len(content) > 150:
                content = content[:150] + "..."
            history_lines.append(f"{role_display}: {content}")
        return "\n".join(history_lines) + "\n\n"

    def _format_environment_block(self, environment_context: str) -> str:
        if not environment_context.strip():
            return ""
        return f"=== Contexte environnement ===\n{environment_context.strip()}\n\n"

    def _build_general_assistance_prompt(
        self,
        message: str,
        author_name: str,
        channel_name: str,
        conversation_history: list[dict] | None = None,
        environment_context: str = "",
    ) -> list[dict]:
        """
        Build a prompt for general assistance requests.
        """
        if conversation_history is None:
            conversation_history = []

        user_content = (
            f"{self._format_history_context(conversation_history)}"
            f"{self._format_environment_block(environment_context)}"
            f"Auteur: {author_name}\n"
            f"Canal: {channel_name}\n"
            f"Message: {message}"
        )

        return [
            {
                "role": "system",
                "content": (
                    "Tu es SoraBot, un assistant conversationnel direct et professionnel. "
                    "Réponds en français, clairement, naturellement, et sans jargon inutile. "
                    "Si l'utilisateur pose une question simple, réponds simplement. "
                    "Si la demande est technique, structure la réponse avec des étapes concrètes. "
                    "Utilise le contexte de la conversation précédente pour des réponses cohérentes. "
                    f"{SELF_AWARENESS_INSTRUCTIONS}"
                ),
            },
            {
                "role": "user",
                "content": user_content,
            },
        ]

    def _build_github_assistance_prompt(
        self,
        request_type: str,
        message: str,
        author_name: str,
        channel_name: str,
        repository_snapshot: str,
        execution_log: str,
        conversation_history: list[dict] | None = None,
        environment_context: str = "",
    ) -> list[dict]:
        if conversation_history is None:
            conversation_history = []

        user_content = (
            f"{self._format_history_context(conversation_history)}"
            f"{self._format_environment_block(environment_context)}"
            f"Auteur: {author_name}\n"
            f"Canal: {channel_name}\n"
            f"Type de demande: {request_type}\n"
            f"Message: {message}\n\n"
            f"Contexte dépôt:\n{repository_snapshot}\n\n"
            f"Résultat exécution:\n{execution_log if execution_log else '(pas d\'action)'}"
        )

        return [
            {
                "role": "system",
                "content": (
                    "Tu es SoraBot, un agent orienté engineering et GitHub. "
                    "Réponds en français, de manière concise, professionnelle et actionnable. "
                    "Explique ce qui a été fait, ce qui reste à faire, et les risques éventuels. "
                    "Utilise le contexte de la conversation précédente pour des réponses cohérentes. "
                    f"{SELF_AWARENESS_INSTRUCTIONS}"
                ),
            },
            {
                "role": "user",
                "content": user_content,
            },
        ]

    def _build_event_assistance_prompt(
        self,
        request_type: str,
        message: str,
        author_name: str,
        channel_name: str,
        execution_log: str,
        event_draft: dict,
        pending_discord_event: dict,
        conversation_history: list[dict] | None = None,
        environment_context: str = "",
    ) -> list[dict]:
        if conversation_history is None:
            conversation_history = []

        missing = missing_event_fields(event_draft)
        user_content = (
            f"{self._format_history_context(conversation_history)}"
            f"{self._format_environment_block(environment_context)}"
            f"Auteur: {author_name}\n"
            f"Canal: {channel_name}\n"
            f"Type de demande: {request_type}\n"
            f"Message: {message}\n\n"
            f"Draft événement:\n{format_event_summary(event_draft)}\n"
            f"Champs manquants: {describe_missing_fields(missing) if missing else 'aucun'}\n"
            f"Création imminente: {'oui' if pending_discord_event else 'non'}\n\n"
            f"Résultat exécution:\n{execution_log if execution_log else '(pas d\'action)'}"
        )

        return [
            {
                "role": "system",
                "content": (
                    "Tu es SoraBot, capable de créer des événements Discord planifiés. "
                    "Réponds en français, clairement et brièvement. "
                    "Si des champs manquent, pose uniquement les questions nécessaires. "
                    "Si une recherche a produit une proposition complète mais non confirmée, "
                    "présente le résumé sous le titre 'Événement proposé' et demande confirmation. "
                    "Si la création est imminente (pending), confirme ce qui va être créé "
                    "sans inventer d'URL Discord. "
                    "Ne prétends jamais qu'un événement a déjà été créé côté Discord "
                    "si 'Création imminente' vaut non. "
                    f"{SELF_AWARENESS_INSTRUCTIONS}"
                ),
            },
            {
                "role": "user",
                "content": user_content,
            },
        ]

    def _build_response_prompt(
        self,
        request_type: str,
        message: str,
        author_name: str,
        channel_name: str,
        repository_snapshot: str,
        execution_log: str,
        conversation_history: list[dict] | None = None,
        environment_context: str = "",
        event_draft: dict | None = None,
        pending_discord_event: dict | None = None,
    ) -> list[dict]:
        """Build the final LLM prompt, keeping general chat, events, and GitHub replies distinct."""
        if conversation_history is None:
            conversation_history = []

        if request_type == "general_assistance":
            return self._build_general_assistance_prompt(
                message,
                author_name,
                channel_name,
                conversation_history,
                environment_context,
            )

        if request_type in EVENT_REQUEST_TYPES:
            return self._build_event_assistance_prompt(
                request_type=request_type,
                message=message,
                author_name=author_name,
                channel_name=channel_name,
                execution_log=execution_log,
                event_draft=event_draft or {},
                pending_discord_event=pending_discord_event or {},
                conversation_history=conversation_history,
                environment_context=environment_context,
            )

        return self._build_github_assistance_prompt(
            request_type=request_type,
            message=message,
            author_name=author_name,
            channel_name=channel_name,
            repository_snapshot=repository_snapshot,
            execution_log=execution_log,
            conversation_history=conversation_history,
            environment_context=environment_context,
        )

    def _extract_intent_with_llm(
        self,
        message: str,
        conversation_history: list[dict] | None = None,
        api_key: str | None = None,
    ) -> dict:
        """
        Use the LLM to extract action parameters from free-form text, with conversation context.
        """
        if conversation_history is None:
            conversation_history = []

        context_lines = []
        if conversation_history:
            context_lines.append("Previous conversation context:")
            for msg in conversation_history[-5:]:
                role = msg.get("role", "unknown").upper()
                content = msg.get("content", "")
                if len(content) > 200:
                    content = content[:200] + "..."
                context_lines.append(f"{role}: {content}")
            context_lines.append("\nCurrent request:")

        conversation_context = "\n".join(context_lines) if context_lines else ""
        prompt = [
            {
                "role": "system",
                "content": (
                    "Tu analyses une commande Discord pour SoraBot (GitHub + événements Discord). "
                    "Réponds uniquement en JSON strict, sans texte autour. "
                    "Schéma: "
                    "{"
                    '"request_type": "create_pr|create_branch|list_open_prs|pr_status|pr_description|add_reviewer|bug_fix|merge_conflict|create_discord_event|research_event|confirm_discord_event|general_assistance", '
                    '"target_owner": "string|null", '
                    '"target_repo": "string|null", '
                    '"target_branch": "string|null", '
                    '"target_pr_numbers": [1, 2], '
                    '"target_pr_title": "string|null", '
                    '"event": {'
                    '"name":"string|null",'
                    '"description":"string|null",'
                    '"start_time":"ISO-8601 avec offset Europe/Paris si possible|null",'
                    '"end_time":"ISO-8601|null",'
                    '"entity_type":"external|voice|stage|null",'
                    '"location":"string|null",'
                    '"channel_id":"number|null",'
                    '"needs_research":false,'
                    '"research_query":"string|null",'
                    '"confirmed":false'
                    "}"
                    "}. "
                    "Règles événements: "
                    "- Si l'utilisateur veut créer un événement Discord planifié => create_discord_event. "
                    "- S'il demande seulement de trouver la date d'un événement réel => research_event. "
                    "- S'il demande de trouver la date ET de créer l'événement => create_discord_event avec needs_research=true (recherche puis proposition, création seulement après confirm_discord_event). "
                    "- S'il confirme une proposition précédente (oui/ok/vas-y/crée-le) => confirm_discord_event. "
                    "- Lieu physique => entity_type=external et renseigner location. "
                    "- Dates relatives sans année: choisir la prochaine occurrence future, fuseau Europe/Paris. "
                    "- Si une valeur est inconnue, mets null."
                ),
            },
            {
                "role": "user",
                "content": f"{conversation_context}\n{message}",
            },
        ]

        response = self._chat_with_llm(prompt, temperature=0, api_key=api_key)
        cleaned = (response or "").strip()
        if cleaned.startswith("Erreur LLM"):
            return {}

        data = parse_json_object(cleaned)
        if not data:
            return {}
        return {
            "request_type": data.get("request_type") or None,
            "target_owner": data.get("target_owner") or None,
            "target_repo": data.get("target_repo") or None,
            "target_branch": data.get("target_branch") or None,
            "target_pr_numbers": data.get("target_pr_numbers") or None,
            "target_pr_title": data.get("target_pr_title") or None,
            "event": data.get("event") if isinstance(data.get("event"), dict) else {},
        }

    def _generate_pr_metadata(self, pr_context: dict, state: AgentState) -> dict:
        """
        Generate a PR title and body from branch context and repo guidelines.
        """
        files_changed = pr_context.get("files_changed", [])
        commit_messages = pr_context.get("commit_messages", [])
        contributing_guidelines = pr_context.get("contributing_guidelines")
        pull_request_template = pr_context.get("pull_request_template")
        branch_name = pr_context.get("head_branch", state.get("target_branch", ""))
        repo_full_name = pr_context.get("repo_full_name", "")

        compact_files = self._format_pr_files_summary(files_changed)
        commit_summary = self._format_pr_commit_summary(commit_messages)

        prompt = self._build_pr_metadata_prompt(
            repo_full_name=repo_full_name,
            branch_name=branch_name,
            author_name=state.get("author_name", ""),
            channel_name=state.get("channel_name", ""),
            compact_files=compact_files,
            commit_summary=commit_summary,
            contributing_guidelines=contributing_guidelines,
            pull_request_template=pull_request_template,
        )

        response = self._chat_with_llm(prompt, temperature=0.2, api_key=state.get("api_key"))
        parsed = parse_json_object((response or "").strip())
        if parsed.get("title") and parsed.get("body"):
            return {"title": parsed["title"], "body": parsed["body"]}
        return self._build_pr_metadata_fallback(branch_name, compact_files, contributing_guidelines)

    def _build_pr_metadata_prompt(self, repo_full_name: str, branch_name: str, author_name: str,
        channel_name: str, compact_files: list[str], commit_summary: list[str], contributing_guidelines: Optional[str],
        pull_request_template: Optional[str],) -> list[dict]:
        """
        Build a prompt to generate PR title and description, including branch context and contribution guidelines.
        """
        return [
            {
                "role": "system",
                "content": (
                    "Tu rédiges un titre et une description de pull request. "
                    "Tu dois répondre en JSON strict uniquement, sans texte autour. "
                    "Schéma: {\"title\": \"string\", \"body\": \"string\"}. "
                    "Le titre doit être court, clair, orienté résultat. "
                    "La description doit résumer le changement, l'impact, les tests réalisés, "
                    "et toute note utile pour les reviewers. "
                    "Si des consignes de CONTRIBUTING.md ou de template PR existent, respecte-les."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Repository: {repo_full_name}\n"
                    f"Branche: {branch_name}\n"
                    f"Auteur: {author_name}\n"
                    f"Canal: {channel_name}\n\n"
                    f"Fichiers modifiés:\n{chr(10).join(compact_files) if compact_files else '- Aucun fichier détecté'}\n\n"
                    f"Messages de commit:\n{chr(10).join(commit_summary) if commit_summary else '- Aucun message'}\n\n"
                    f"CONTRIBUTING.md / normes de contribution:\n{contributing_guidelines or 'Aucune consigne trouvée'}\n\n"
                    f"Template PR:\n{pull_request_template or 'Aucun template trouvé'}\n\n"
                    "Rédige le meilleur titre et la meilleure description possibles pour cette PR."
                ),
            },
        ]

    def _format_pr_files_summary(self, files_changed: list[dict]) -> list[str]:
        """
        Format a summary of the changed files.
        """
        return [
            f"- {file_entry.get('filename')} ({file_entry.get('status')}, +{file_entry.get('additions')}, -{file_entry.get('deletions')})"
            for file_entry in files_changed[:12]
        ]

    def _format_pr_commit_summary(self, commit_messages: list[str]) -> list[str]:
        return [f"- {message}" for message in commit_messages]

    def _build_pr_metadata_fallback(self, branch_name: str, compact_files: list[str],
        contributing_guidelines: Optional[str],) -> dict:
        """
        Build a fallback PR title and body if LLM generation fails, using branch name and file summary.
        """
        fallback_title = branch_name.replace("-", " ").strip().title() if branch_name else "Auto PR"
        fallback_body = [
            "## Summary",
            f"- Automated PR for branch `{branch_name}`.",
            "",
            "## Changed files",
        ]
        fallback_body.extend(compact_files or ["- No file summary available."])
        if contributing_guidelines:
            fallback_body.extend(["", "## Contribution notes", contributing_guidelines[:1500]])
        return {"title": fallback_title, "body": "\n".join(fallback_body)}

    def _get_current_branch(self) -> str:
        """
        Get current git branch name.
        """
        try:
            result = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
            return result.stdout.strip() or "main"
        except Exception:
            return "main"

    def _git_snapshot(self) -> str:
        """
        Get a snapshot of the current git repository state, including current branch, status, and recent commits.
        """
        commands = [
            ["git", "branch", "--show-current"],
            ["git", "status", "--short", "--branch"],
            ["git", "log", "--oneline", "-3"],
        ]
        sections = []

        for command in commands:
            try:
                completed = subprocess.run(
                    command,
                    cwd=self.repo_root,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                output = completed.stdout.strip() or completed.stderr.strip() or "Aucune sortie"
            except Exception as exc:
                output = f"Erreur git: {exc}"

            sections.append(f"$ {' '.join(command)}\n{output}")

        return "\n\n".join(sections)
