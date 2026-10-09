"""The LLM providers behind one small interface: Anthropic and OpenAI, chosen per request.

A provider is available when its API key is set in `.env`. Every call asks for
JSON that matches a schema (structured outputs on both APIs), so callers get a
dict back and never parse free text.

Tests replace `available()` with fakes; nothing here may reach a real API in tests.
"""

import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

import anthropic
import openai

from app.config import settings

log = logging.getLogger(__name__)

# Cheap, fast tiers by default (planning Q13); `.env` can override each.
DEFAULT_MODELS = {"anthropic": "claude-haiku-5-5", "openai": "gpt-5.4-mini"}
LABELS = {"anthropic": "Anthropic Claude", "openai": "OpenAI"}
PROVIDERS = tuple(DEFAULT_MODELS)
REASONING_MODEL = re.compile(r"^(gpt-5|o\d)")  # OpenAI models that take reasoning_effort


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


def _parse(text: str) -> dict:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError("The model returned malformed JSON.") from exc
    if not isinstance(data, dict):
        raise LLMError("The model returned JSON of the wrong shape.")
    return data


@lru_cache(maxsize=4)
def _build(name: str, api_key: str, model: str) -> LLMProvider:
    cls = AnthropicProvider if name == "anthropic" else OpenAIProvider
    return cls(api_key, model)


def available() -> dict[str, LLMProvider]:
    """The providers that have an API key, keyed by name, in display order."""
    configured = {
        "anthropic": (settings.anthropic_api_key, settings.anthropic_model),
        "openai": (settings.openai_api_key, settings.openai_model),
    }
    return {
        name: _build(name, key, model or DEFAULT_MODELS[name])
        for name, (key, model) in configured.items()
        if key
    }


def default_name(providers: dict[str, LLMProvider]) -> str | None:
    """The `.env` default if it has a key, else the first provider that does."""
    if settings.llm_provider in providers:
        return settings.llm_provider
    return next(iter(providers), None)


def resolve(name: str | None = None) -> LLMProvider:
    """The provider called `name`, or the default one. Raises LLMNotConfigured."""
    providers = available()
    if not providers:
        raise LLMNotConfigured(
            "No LLM is configured. Add ANTHROPIC_API_KEY or OPENAI_API_KEY to .env and restart the backend."
        )
    if name is None:
        return providers[default_name(providers)]
    if name not in PROVIDERS:
        raise LLMNotConfigured(f"Unknown LLM provider {name!r}.")
    if name not in providers:
        raise LLMNotConfigured(f"{LABELS[name]} has no API key in .env.")
    return providers[name]
