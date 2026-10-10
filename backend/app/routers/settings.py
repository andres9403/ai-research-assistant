from fastapi import APIRouter

from app.schemas import LLMProviderOut, LLMSettingsOut, OllamaOut
from app.services import llm

router = APIRouter(prefix="/api/settings", tags=["settings"])


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
