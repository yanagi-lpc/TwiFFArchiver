from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


class AuthError(RuntimeError):
    """Missing or invalid credentials."""


@dataclass(frozen=True, slots=True)
class Credentials:
    auth_token: str
    ct0: str

    def as_cookie_header(self) -> str:
        return f"auth_token={self.auth_token}; ct0={self.ct0}"

    def to_dict(self) -> dict[str, str]:
        return {"auth_token": self.auth_token, "ct0": self.ct0}


def config_dir() -> Path:
    override = os.environ.get("TWIFFARCHIVER_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "twiffarchiver"


def credentials_path() -> Path:
    return config_dir() / "credentials.json"


def save_credentials(creds: Credentials) -> Path:
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(creds.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def load_credentials() -> Credentials:
    env_token = os.environ.get("TWIFFARCHIVER_AUTH_TOKEN") or os.environ.get("AUTH_TOKEN")
    env_ct0 = os.environ.get("TWIFFARCHIVER_CT0") or os.environ.get("CT0")
    if env_token and env_ct0:
        return Credentials(auth_token=env_token.strip(), ct0=env_ct0.strip())

    path = credentials_path()
    if not path.is_file():
        raise AuthError(
            "Credentials not found. Run: twiffarchiver auth set "
            "--auth-token <token> --ct0 <ct0>"
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    token = (raw.get("auth_token") or "").strip()
    ct0 = (raw.get("ct0") or "").strip()
    if not token or not ct0:
        raise AuthError(f"Invalid credentials file: {path}")
    return Credentials(auth_token=token, ct0=ct0)


def load_from_browser(browser: str = "chrome") -> Credentials:
    """Read auth_token / ct0 from a local browser cookie store."""
    try:
        import browser_cookie3  # type: ignore
    except ImportError as exc:
        raise AuthError(
            "browser-cookie3 is required for --from-browser. "
            "Install with: pip install 'twiffarchiver[browser]'"
        ) from exc

    loaders = {
        "chrome": browser_cookie3.chrome,
        "firefox": browser_cookie3.firefox,
        "edge": browser_cookie3.edge,
        "brave": getattr(browser_cookie3, "brave", None),
    }
    loader = loaders.get(browser.lower())
    if loader is None:
        raise AuthError(f"Unsupported browser: {browser}")

    jar = loader(domain_name=".x.com")
    cookies = {c.name: c.value for c in jar}
    if "auth_token" not in cookies or "ct0" not in cookies:
        jar = loader(domain_name=".twitter.com")
        cookies.update({c.name: c.value for c in jar})

    token = cookies.get("auth_token")
    ct0 = cookies.get("ct0")
    if not token or not ct0:
        raise AuthError(
            f"Could not find auth_token/ct0 in {browser}. "
            "Log into x.com in that browser, then retry."
        )
    return Credentials(auth_token=token, ct0=ct0)
