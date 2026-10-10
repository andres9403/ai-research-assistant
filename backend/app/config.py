from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    data_dir: Path = REPO_ROOT / "data"
    database_url: str | None = None
    cors_origins: list[str] = ["http://localhost:5173"]

    llm_provider: str = "anthropic"
    anthropic_api_key: str | None = None
    anthropic_model: str | None = None
    openai_api_key: str | None = None
    openai_model: str | None = None
    llm_timeout: float = 90.0  # seconds per LLM request
    # A local Ollama server needs no key; blank OLLAMA_URL turns detection off.
    ollama_url: str = "http://localhost:11434"
    ollama_model: str | None = None
    ollama_timeout: float = 900.0  # seconds per request; a CPU-only summary took ~6 minutes in testing
    ollama_num_ctx: int = 16384  # tokens; Ollama's default context would cut summary input short
    semantic_scholar_api_key: str | None = None

    @property
    def db_url(self) -> str:
        return self.database_url or f"sqlite:///{self.data_dir / 'app.db'}"

    @property
    def pdf_dir(self) -> Path:
        return self.data_dir / "pdfs"

    @property
    def model_dir(self) -> Path:
        """Where the local embedding model is downloaded on first use."""
        return self.data_dir / "models"


settings = Settings()
