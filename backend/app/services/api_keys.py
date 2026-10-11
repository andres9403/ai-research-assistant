"""API keys entered in the app (issues #8 and #12): checked with the provider, then saved to `.env`.

A saved key goes into the git-ignored `.env` that config.py reads, so it survives
a restart exactly as if it had been typed there, and it applies to the running
backend at once. Keys are never returned by the API, logged, or kept in the browser.
"""

import os
import re
from pathlib import Path

from app import config
from app.config import settings
from app.services import http, llm, search

ENV_VARS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "semantic_scholar": "SEMANTIC_SCHOLAR_API_KEY",
}
# Provider keys are letters, digits, "-" and "_". Anything else, a newline above
# all, could add or change other settings in `.env`.
KEY_FORMAT = re.compile(r"[A-Za-z0-9_-]{20,400}")


class InvalidKey(Exception):
    """The text can't be an API key; the message is safe to show the user."""


def save(name: str, key: str) -> None:
    """Check `key` with provider `name`, then save it to `.env` and use it from now on.

    Raises InvalidKey for text that isn't key-shaped, and LLMError (or
    search.KeyCheckError for Semantic Scholar) when the key can't be confirmed;
    nothing is saved in any of these cases.
    """
    key = key.strip()
    if not KEY_FORMAT.fullmatch(key):
        raise InvalidKey("That doesn't look like an API key. Paste the whole key, with no spaces.")
    if name == "semantic_scholar":
        with http.make_client() as client:
            search.check_key(client, key)
    else:
        llm.check_key(name, key)
    write_env(config.ENV_FILE, ENV_VARS[name], key)
    setattr(settings, f"{name}_api_key", key)


def remove(name: str) -> None:
    """Forget provider `name`'s key, in `.env` and in the running backend."""
    write_env(config.ENV_FILE, ENV_VARS[name], "")
    setattr(settings, f"{name}_api_key", None)


def write_env(path: Path, var: str, value: str) -> None:
    """Set `var=value` in the dotenv file at `path`, keeping every other line.

    The first assignment of `var` is replaced in place and any later ones are
    dropped; a file without one gets it appended. The file is replaced atomically
    and readable by its owner only, since it holds secrets.
    """
    assignment = re.compile(rf"^\s*(export\s+)?{re.escape(var)}\s*=")
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    out, done = [], False
    for line in lines:
        if assignment.match(line):
            if not done:
                out.append(f"{var}={value}")
                done = True
            continue
        out.append(line)
    if not done:
        out.append(f"{var}={value}")

    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    os.chmod(tmp, 0o600)  # in case a stale tmp file already existed with wider permissions
    os.replace(tmp, path)
