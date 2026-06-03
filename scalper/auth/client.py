"""Authenticated Schwab client construction (Phase 0).

Wraps ``schwab-py`` OAuth so the rest of the code gets a ready-to-use client
with automatic token refresh. Secrets are read from the environment (loaded
from ``scalper/.env`` via :func:`python-dotenv`) and are **never logged**.

``schwab-py`` is imported lazily inside the functions so this module — and the
config/backtest code that may import it — stays importable without the
dependency or any network access (important for tests).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv


@dataclass(frozen=True)
class SchwabCredentials:
    """Schwab developer-app credentials and token location.

    None of these fields should ever be logged or echoed.
    """

    app_key: str
    app_secret: str
    callback_url: str
    token_path: str

    @property
    def is_complete(self) -> bool:
        return bool(self.app_key and self.app_secret and self.callback_url)


def load_credentials(env_path: Optional[str | os.PathLike[str]] = None) -> SchwabCredentials:
    """Load Schwab credentials from a ``.env`` file / the environment.

    Args:
        env_path: Optional explicit path to a ``.env`` file. If omitted, the
            standard search (``scalper/.env`` then CWD) is used.

    Returns:
        A populated :class:`SchwabCredentials`.

    Raises:
        RuntimeError: If any required credential is missing. The error message
            deliberately names only which variable is missing, never a value.
    """
    if env_path is not None:
        load_dotenv(env_path)
    else:
        # Prefer scalper/.env (next to this package), fall back to CWD .env.
        package_env = Path(__file__).resolve().parent.parent / ".env"
        if package_env.exists():
            load_dotenv(package_env)
        else:
            load_dotenv()

    app_key = os.environ.get("SCHWAB_APP_KEY", "")
    app_secret = os.environ.get("SCHWAB_APP_SECRET", "")
    callback_url = os.environ.get("SCHWAB_CALLBACK_URL", "https://127.0.0.1:8182/")
    token_path = os.environ.get("SCHWAB_TOKEN_PATH", "scalper/token.json")

    missing = [
        name
        for name, value in (
            ("SCHWAB_APP_KEY", app_key),
            ("SCHWAB_APP_SECRET", app_secret),
            ("SCHWAB_CALLBACK_URL", callback_url),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            "Missing Schwab credentials: "
            + ", ".join(missing)
            + ". Copy scalper/.env.example to scalper/.env and fill it in."
        )

    return SchwabCredentials(
        app_key=app_key,
        app_secret=app_secret,
        callback_url=callback_url,
        token_path=token_path,
    )


def build_client(
    credentials: Optional[SchwabCredentials] = None,
    *,
    interactive: bool = True,
) -> Any:
    """Build an authenticated ``schwab-py`` client with token refresh.

    If a valid token already exists at ``token_path`` it is reused and
    refreshed automatically; otherwise the OAuth login flow runs (which
    requires a browser and is therefore only appropriate when ``interactive``
    is True).

    Args:
        credentials: Credentials to use; loaded from the environment if omitted.
        interactive: When False, refuse to start the browser login flow and
            instead require a pre-existing token (suitable for headless/CI).

    Returns:
        An authenticated ``schwab.client.Client`` instance.

    Raises:
        RuntimeError: If credentials are incomplete, or a token is required but
            absent in non-interactive mode.
    """
    creds = credentials or load_credentials()
    if not creds.is_complete:
        raise RuntimeError("Incomplete Schwab credentials; cannot build client.")

    # Imported lazily — keeps the module importable without schwab-py.
    from schwab import auth  # type: ignore import-not-found

    token_path = creds.token_path
    token_exists = Path(token_path).exists()

    if token_exists:
        # Reuse the stored token; schwab-py refreshes it automatically.
        return auth.client_from_token_file(
            token_path=token_path,
            api_key=creds.app_key,
            app_secret=creds.app_secret,
        )

    if not interactive:
        raise RuntimeError(
            f"No Schwab token at {token_path!r} and interactive login is "
            "disabled. Run the OAuth flow once interactively to create it."
        )

    # First-time login: opens a browser, completes OAuth, persists the token.
    return auth.easy_client(
        api_key=creds.app_key,
        app_secret=creds.app_secret,
        callback_url=creds.callback_url,
        token_path=token_path,
    )
