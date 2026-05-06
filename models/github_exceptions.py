##
## SORABOT, 2026
## github_exceptions.py
## File description:
## Custom exceptions for GitHub-related operations, such as authentication errors and operation failures.
##

class GitHubAuthenticationError(Exception):
    """
    Raised when GitHub authentication or permissions are insufficient.
    """

    def __init__(self, message: str, requires_user_token: bool = False):
        self.message = message
        self.requires_user_token = requires_user_token
        super().__init__(message)

class GitHubOperationError(Exception):
    """Raised for general GitHub operation failures."""
    pass
