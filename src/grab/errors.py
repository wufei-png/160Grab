class SessionExpiredError(RuntimeError):
    """Raised when polling loses the authenticated 91160 browser session."""


class TransientSessionRefreshError(RuntimeError):
    """Raised when a best-effort session refresh hits a transient browser failure."""
