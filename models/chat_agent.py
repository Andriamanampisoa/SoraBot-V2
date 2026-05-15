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

class AgentState(TypedDict, total=False):
    message: str
    author_name: str
    channel_name: str
    user_id: str
    api_key: Optional[str]
    conversation_history: list[dict]
    request_type: str
    target_repo: str
    target_owner: str
    target_branch: str
    target_pr_numbers: list[int]
    target_pr_title: str
    repository_snapshot: str
    github_branch: Optional[str]
    github_pr_url: Optional[str]
    execution_log: str
    response: str

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

    def handle_message(self, message: str, author_name: str, channel_name: str,
            user_id: Optional[str] = None, api_key: Optional[str] = None) -> str:
        """
        Main entry point to handle an incoming message and generate a response.
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
            "conversation_history": conversation_history,
            "execution_log": "",
        }
        result = self.workflow.invoke(state)
        response = result.get("response", "")
        self.memory.add_exchange(user_id, message.strip(), response)
        return response

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

        if not request_type:
            lowered = message.lower()
            if any(keyword in lowered for keyword in ["créer pr", "pull request", "merge request", "ouvrir une pr"]):
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
        }

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
        Execute GitHub operations if needed based on request type.
        """
        request_type = state.get("request_type", "general_assistance")
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

        prompt = self._build_response_prompt(
            request_type=request_type,
            message=message,
            author_name=author_name,
            channel_name=state.get("channel_name", ""),
            repository_snapshot=repository_snapshot,
            execution_log=execution_log,
            conversation_history=conversation_history,
        )

        api_key = state.get("api_key")
        response = self._chat_with_llm(prompt, temperature=0.2, api_key=api_key)
        if state.get("github_pr_url"):
            response += f"\n\nPR URL: {state['github_pr_url']}"
        if state.get("github_branch"):
            response += f"\nBranch: {state['github_branch']}"
        return {**state, "response": response}

    def _build_general_assistance_prompt(self, message: str, author_name: str, channel_name: str, 
        conversation_history: list[dict] | None = None) -> list[dict]:
        """
        Build a prompt for general assistance requests.
        """
        if conversation_history is None:
            conversation_history = []
        history_context = ""
        if conversation_history:
            history_lines = ["=== Historique de la conversation ==="]
            for msg in conversation_history[-5:]:
                role_display = "Utilisateur" if msg.get("role") == "user" else "SoraBot"
                content = msg.get("content", "")
                if len(content) > 150:
                    content = content[:150] + "..."
                history_lines.append(f"{role_display}: {content}")
            history_context = "\n".join(history_lines) + "\n\n"

        user_content = (
            f"{history_context}"
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
                    "Utilise le contexte de la conversation précédente pour des réponses cohérentes."
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
    ) -> list[dict]:
        if conversation_history is None:
            conversation_history = []

        history_context = ""
        if conversation_history:
            history_lines = ["=== Historique de la conversation ==="]
            for msg in conversation_history[-5:]:
                role_display = "Utilisateur" if msg.get("role") == "user" else "SoraBot"
                content = msg.get("content", "")
                if len(content) > 150:
                    content = content[:150] + "..."
                history_lines.append(f"{role_display}: {content}")
            history_context = "\n".join(history_lines) + "\n\n"

        user_content = (
            f"{history_context}"
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
                    "Utilise le contexte de la conversation précédente pour des réponses cohérentes."
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
    ) -> list[dict]:
        """Build the final LLM prompt, keeping general chat and GitHub-assisted replies distinct."""
        if conversation_history is None:
            conversation_history = []

        if request_type == "general_assistance":
            return self._build_general_assistance_prompt(message, author_name, channel_name, conversation_history)

        return self._build_github_assistance_prompt(
            request_type=request_type,
            message=message,
            author_name=author_name,
            channel_name=channel_name,
            repository_snapshot=repository_snapshot,
            execution_log=execution_log,
            conversation_history=conversation_history,
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
                    "Tu analyses une commande Discord pour un agent GitHub. "
                    "Réponds uniquement en JSON strict, sans texte autour. "
                    "Schéma: "
                    "{"
                    '"request_type": "create_pr|create_branch|list_open_prs|pr_status|pr_description|add_reviewer|bug_fix|merge_conflict|general_assistance", '
                    '"target_owner": "string|null", '
                    '"target_repo": "string|null", '
                    '"target_branch": "string|null"'
                    ', '
                    '"target_pr_numbers": [1, 2]'
                    ', '
                    '"target_pr_title": "string|null"'
                    "}. "
                    "Si une valeur est inconnue, mets null."
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
