"""The LLM providers behind one small interface: Anthropic, OpenAI and a local Ollama, chosen per request.

Anthropic and OpenAI are available when their API key is set in `.env`. Ollama
needs no key: it is available while its server answers at OLLAMA_URL and has
the model pulled, checked live so pulling a model needs no backend restart.
Every call asks for JSON that matches a schema (structured outputs on all
three), so callers get a dict back and never parse free text.

Tests replace `available()` with fakes, and the conftest network mock stands in
for Ollama; nothing here may reach a real API in tests.
"""

import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

import anthropic
import httpx
import openai

from app.config import settings
from app.services import http

log = logging.getLogger(__name__)

# Cheap, fast tiers by default (planning Q13); `.env` can override each. The
# Ollama default runs on a CPU and, unlike the 3B models tried, cites excerpts (issue #8).
DEFAULT_MODELS = {"anthropic": "claude-haiku-5-5", "openai": "gpt-5.4-mini", "ollama": "gemma3:4b"}
LABELS = {"anthropic": "Anthropic Claude", "openai": "OpenAI", "ollama": "Ollama (local)"}
PROVIDERS = tuple(DEFAULT_MODELS)
REASONING_MODEL = re.compile(r"^(gpt-5|o\d)")  # OpenAI models that take reasoning_effort
OLLAMA_PROBE_TIMEOUT = 2.0  # seconds; a stopped server refuses the connection at once
# Tokens. Answers and summaries need a few hundred; at a few tokens/s on a CPU, a small
# model stuck repeating itself would otherwise run until OLLAMA_TIMEOUT.
OLLAMA_MAX_OUTPUT_TOKENS = 1024


class LLMError(Exception):
    """An LLM call failed; the message is safe to show the user."""


class LLMNotConfigured(LLMError):
    pass


@dataclass
class LLMResult:
    data: dict
    input_tokens: int
    output_tokens: int


class LLMProvider(Protocol):
    name: str
    model: str

    def complete_json(
        self, *, system: str, prompt: str, schema: dict, schema_name: str, max_tokens: int
    ) -> LLMResult: ...


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, model: str):
        self.model = model
        self.client = anthropic.Anthropic(api_key=api_key, timeout=settings.llm_timeout, max_retries=2)

    def complete_json(self, *, system, prompt, schema, schema_name, max_tokens) -> LLMResult:
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                # Low effort: these are reading tasks, and it keeps thinking tokens down.
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
            )
        except anthropic.AuthenticationError as exc:
            raise LLMError("Anthropic rejected the API key. Check ANTHROPIC_API_KEY in .env.") from exc
        except anthropic.PermissionDeniedError as exc:
            raise LLMError(f"The Anthropic API key can't use this model ({self.model}).") from exc
        except anthropic.NotFoundError as exc:
            raise LLMError(f"Anthropic doesn't know the model {self.model!r}. Check ANTHROPIC_MODEL.") from exc
        except anthropic.RateLimitError as exc:
            raise LLMError("Anthropic is rate-limiting requests. Wait a minute and try again.") from exc
        except anthropic.APIStatusError as exc:
            log.warning("Anthropic API error %s: %s", exc.status_code, exc.message)
            raise LLMError(f"The Anthropic API returned an error (HTTP {exc.status_code}).") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("Couldn't reach the Anthropic API. Check your internet connection.") from exc

        if response.stop_reason == "refusal":
            raise LLMError("The model declined to answer this request.")
        if response.stop_reason == "max_tokens":
            raise LLMError("The model's answer was cut off before it finished.")
        text = next((b.text for b in response.content if b.type == "text"), "")
        return LLMResult(
            data=_parse(text),
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str, model: str):
        self.model = model
        self.client = openai.OpenAI(api_key=api_key, timeout=settings.llm_timeout, max_retries=2)

    def complete_json(self, *, system, prompt, schema, schema_name, max_tokens) -> LLMResult:
        extra = {}
        if REASONING_MODEL.match(self.model):
            extra["reasoning_effort"] = "low"  # reasoning models only; others reject it
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                max_completion_tokens=max_tokens,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": schema_name, "schema": schema, "strict": True},
                },
                **extra,
            )
        except openai.AuthenticationError as exc:
            raise LLMError("OpenAI rejected the API key. Check OPENAI_API_KEY in .env.") from exc
        except openai.PermissionDeniedError as exc:
            raise LLMError(f"The OpenAI API key can't use this model ({self.model}).") from exc
        except openai.NotFoundError as exc:
            raise LLMError(f"OpenAI doesn't know the model {self.model!r}. Check OPENAI_MODEL.") from exc
        except openai.RateLimitError as exc:
            raise LLMError("OpenAI is rate-limiting requests or the account is out of credit.") from exc
        except openai.APIStatusError as exc:
            log.warning("OpenAI API error %s: %s", exc.status_code, exc.message)
            raise LLMError(f"The OpenAI API returned an error (HTTP {exc.status_code}).") from exc
        except openai.APIConnectionError as exc:
            raise LLMError("Couldn't reach the OpenAI API. Check your internet connection.") from exc

        choice = response.choices[0]
        if choice.message.refusal:
            raise LLMError("The model declined to answer this request.")
        if choice.finish_reason == "length":
            raise LLMError("The model's answer was cut off before it finished.")
        usage = response.usage
        return LLMResult(
            data=_parse(choice.message.content or ""),
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
        )


class OllamaProvider:
    """A model served by a local Ollama, through its native chat API.

    The native API, unlike Ollama's OpenAI-compatible one, takes `num_ctx`;
    without it Ollama's default context silently truncates summary input.
    """

    name = "ollama"

    def __init__(self, base_url: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.model = model

    def complete_json(self, *, system, prompt, schema, schema_name, max_tokens) -> LLMResult:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "format": schema,
            "stream": False,
            "options": {
                "num_ctx": settings.ollama_num_ctx,
                "num_predict": min(max_tokens, OLLAMA_MAX_OUTPUT_TOKENS),
            },
        }
        try:
            with http.make_client(timeout=settings.ollama_timeout) as client:
                response = client.post(f"{self.base_url}/api/chat", json=body)
        except httpx.TimeoutException as exc:
            raise LLMError(
                f"Ollama took longer than {settings.ollama_timeout:.0f} seconds. "
                "Raise OLLAMA_TIMEOUT or use a smaller model."
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Couldn't reach Ollama at {self.base_url}. Check that it is running.") from exc

        if response.status_code == 404:
            raise LLMError(f"Ollama doesn't have the model {self.model!r}. Run: ollama pull {self.model}")
        if response.is_error:
            log.warning("Ollama error %s: %s", response.status_code, response.text[:500])
            raise LLMError(f"Ollama returned an error (HTTP {response.status_code}).")
        try:
            data = response.json()
        except ValueError as exc:
            raise LLMError("Ollama returned a response that isn't JSON.") from exc
        if data.get("done_reason") == "length":
            raise LLMError("The model's answer was cut off before it finished.")
        return LLMResult(
            data=_parse((data.get("message") or {}).get("content") or ""),
            input_tokens=data.get("prompt_eval_count") or 0,
            output_tokens=data.get("eval_count") or 0,
        )


def _parse(text: str) -> dict:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError("The model returned malformed JSON.") from exc
    if not isinstance(data, dict):
        raise LLMError("The model returned JSON of the wrong shape.")
    return data


@lru_cache(maxsize=8)
def _build(name: str, credential: str, model: str) -> LLMProvider:
    """A provider; `credential` is the API key, or the server URL for Ollama."""
    cls = {"anthropic": AnthropicProvider, "openai": OpenAIProvider, "ollama": OllamaProvider}[name]
    return cls(credential, model)


@dataclass
class OllamaStatus:
    model: str  # the model the app uses, pulled or not
    running: bool
    pulled: bool


def _with_tag(model: str) -> str:
    """Ollama lists `llama3.2` as `llama3.2:latest`."""
    return model if ":" in model else f"{model}:latest"


def ollama_status() -> OllamaStatus:
    """Whether a local Ollama answers at OLLAMA_URL and has the model pulled."""
    model = settings.ollama_model or DEFAULT_MODELS["ollama"]
    if not settings.ollama_url:
        return OllamaStatus(model, running=False, pulled=False)
    try:
        with http.make_client(timeout=OLLAMA_PROBE_TIMEOUT) as client:
            response = client.get(f"{settings.ollama_url.rstrip('/')}/api/tags")
            response.raise_for_status()
            names = {m["name"] for m in response.json()["models"]}
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return OllamaStatus(model, running=False, pulled=False)
    return OllamaStatus(model, running=True, pulled=_with_tag(model) in names)


def available(ollama: OllamaStatus | None = None) -> dict[str, LLMProvider]:
    """The providers that can answer now, keyed by name, in display order.

    Pass `ollama` to reuse a status the caller already fetched.
    """
    configured = {
        "anthropic": (settings.anthropic_api_key, settings.anthropic_model),
        "openai": (settings.openai_api_key, settings.openai_model),
    }
    providers = {
        name: _build(name, key, model or DEFAULT_MODELS[name])
        for name, (key, model) in configured.items()
        if key
    }
    ollama = ollama or ollama_status()
    if ollama.pulled:
        providers["ollama"] = _build("ollama", settings.ollama_url, ollama.model)
    return providers


def setup_hint(ollama: OllamaStatus) -> str:
    """What to do to get an LLM, for a user who has none."""
    if ollama.running:
        return (
            f"Ollama is running but doesn't have {ollama.model}. Run: ollama pull {ollama.model} "
            "(or add ANTHROPIC_API_KEY or OPENAI_API_KEY to .env and restart the backend)."
        )
    return (
        f"Install Ollama from https://ollama.com and run: ollama pull {ollama.model} "
        "(no key needed), or add ANTHROPIC_API_KEY or OPENAI_API_KEY to .env and restart the backend."
    )


def default_name(providers: dict[str, LLMProvider]) -> str | None:
    """The `.env` default if it has a key, else the first provider that does."""
    if settings.llm_provider in providers:
        return settings.llm_provider
    return next(iter(providers), None)


def resolve(name: str | None = None) -> LLMProvider:
    """The provider called `name`, or the default one. Raises LLMNotConfigured."""
    ollama = ollama_status()
    providers = available(ollama)
    if not providers:
        raise LLMNotConfigured(f"No LLM is set up. {setup_hint(ollama)}")
    if name is None:
        return providers[default_name(providers)]
    if name not in PROVIDERS:
        raise LLMNotConfigured(f"Unknown LLM provider {name!r}.")
    if name == "ollama" and name not in providers:
        if ollama.running:
            raise LLMNotConfigured(f"Ollama doesn't have {ollama.model}. Run: ollama pull {ollama.model}")
        raise LLMNotConfigured(f"Ollama isn't running at {settings.ollama_url or '(OLLAMA_URL is blank)'}.")
    if name not in providers:
        raise LLMNotConfigured(f"{LABELS[name]} has no API key in .env.")
    return providers[name]
