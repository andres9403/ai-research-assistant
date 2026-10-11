from typing import Literal

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.schemas import ApiKeyIn, LLMProviderOut, LLMSettingsOut, OllamaOut, SearchSettingsOut
from app.services import api_keys, llm, search

router = APIRouter(prefix="/api/settings", tags=["settings"])

KeyedProvider = Literal["anthropic", "openai"]
SearchProvider = Literal["semantic_scholar"]


@router.get("/llm", response_model=LLMSettingsOut)
def llm_settings() -> LLMSettingsOut:
    ollama = llm.ollama_status()
    providers = llm.available(ollama)
    return LLMSettingsOut(
        default=llm.default_name(providers),
        providers=[
            LLMProviderOut(name=name, label=llm.LABELS[name], model=p.model) for name, p in providers.items()
        ],
        ollama=OllamaOut(model=ollama.model, running=ollama.running),
    )


@router.put("/llm/keys/{provider}", response_model=LLMSettingsOut)
def set_api_key(provider: KeyedProvider, body: ApiKeyIn) -> LLMSettingsOut:
    """Check a key with the provider, save it to `.env` and start using it. The key is never returned."""
    try:
        api_keys.save(provider, body.key.get_secret_value())
    except api_keys.InvalidKey as exc:
        raise HTTPException(422, str(exc)) from exc
    except llm.LLMError as exc:
        raise HTTPException(400, str(exc)) from exc
    return llm_settings()


@router.delete("/llm/keys/{provider}", response_model=LLMSettingsOut)
def remove_api_key(provider: KeyedProvider) -> LLMSettingsOut:
    api_keys.remove(provider)
    return llm_settings()


@router.get("/search", response_model=SearchSettingsOut)
def search_settings() -> SearchSettingsOut:
    return SearchSettingsOut(semantic_scholar_key=bool(settings.semantic_scholar_api_key))


@router.put("/search/keys/{provider}", response_model=SearchSettingsOut)
def set_search_key(provider: SearchProvider, body: ApiKeyIn) -> SearchSettingsOut:
    """Check a Semantic Scholar key, save it to `.env` and use it for search and upload lookups at once."""
    try:
        api_keys.save(provider, body.key.get_secret_value())
    except api_keys.InvalidKey as exc:
        raise HTTPException(422, str(exc)) from exc
    except search.KeyCheckError as exc:
        raise HTTPException(400, str(exc)) from exc
    return search_settings()


@router.delete("/search/keys/{provider}", response_model=SearchSettingsOut)
def remove_search_key(provider: SearchProvider) -> SearchSettingsOut:
    api_keys.remove(provider)
    return search_settings()
