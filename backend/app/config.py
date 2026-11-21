import os
from dataclasses import dataclass, field
from pathlib import Path


def csv_env(name: str, default: str = "") -> tuple[str, ...]:
    return tuple(value.strip() for value in os.getenv(name, default).split(","))


@dataclass(frozen=True)
class ProviderKey:
    id: str
    model: str
    secret: str = field(repr=False)


@dataclass(frozen=True)
class Settings:
    database_path: Path
    allowed_origins: tuple[str, ...]
    enable_failure_simulation: bool
    providers: tuple[ProviderKey, ...]
    timeout_seconds: float

    @classmethod
    def from_env(cls) -> "Settings":
        url = os.getenv("DATABASE_URL", "sqlite:////data/app.db")
        prefix = "sqlite:///"
        if not url.startswith(prefix) or url[len(prefix):] in {"", ":memory:"}:
            raise ValueError("DATABASE_URL must point to a SQLite file")

        keys = csv_env("LLM_API_KEYS")
        if len(keys) < 2 or not all(keys) or len(set(keys)) != len(keys):
            raise ValueError("LLM_API_KEYS must contain at least two distinct keys")
        models = csv_env("LLM_MODELS", "gemini/gemini-3.1-flash-lite")
        if len(models) == 1:
            models = models * len(keys)
        if len(models) != len(keys) or any("/" not in model for model in models):
            raise ValueError("LLM_MODELS must contain one provider/model or one per key")

        timeout = float(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "15"))
        if not 0 < timeout <= 120:
            raise ValueError("LLM_REQUEST_TIMEOUT_SECONDS must be between 0 and 120")
        return cls(
            database_path=Path(url[len(prefix):]).expanduser(),
            allowed_origins=csv_env("ALLOWED_ORIGINS", "http://localhost:3000"),
            enable_failure_simulation=os.getenv("ENABLE_FAILURE_SIMULATION", "").lower() == "true",
            providers=tuple(
                ProviderKey(f"key_{index}", model, key)
                for index, (model, key) in enumerate(zip(models, keys), start=1)
            ),
            timeout_seconds=timeout,
        )
