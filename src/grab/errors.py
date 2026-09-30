class SessionExpiredError(RuntimeError):
    """Raised when polling loses the authenticated 91160 browser session."""


class TransientSessionRefreshError(RuntimeError):
    """Raised when a best-effort session refresh hits a transient browser failure."""


class UnknownSessionError(RuntimeError):
    """Unverified session/schema: stop and ask for manual inspection."""

    def __init__(self, failure_class="unknown"):
        self.failure_class = failure_class
        super().__init__(f"Read-only session requires inspection ({failure_class}).")


class ReadRetryExhausted(RuntimeError):
    """Read-only consecutive failure budget exhausted; no submission was retried."""


class BrowserLaunchError(RuntimeError):
    """Selected browser failed to launch; no fallback is permitted."""
