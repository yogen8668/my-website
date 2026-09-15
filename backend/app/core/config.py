"""Application settings (§66, §78). No secrets in source — all from env."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    app_env: str = "development"
    app_name: str = "THERMOX"
    log_level: str = "INFO"

    database_url: str = Field(
        default="postgresql+psycopg://thermox:thermox@db:5432/thermox",
        description="SQLAlchemy URL. Never committed with real credentials.",
    )

    secret_key: str = Field(default="change-me-in-production")
    cors_origins: str = "http://localhost:5173"

    #: F-5 — the user-approved decision: reject rather than silently normalise.
    composition_sum_tolerance: float = 1e-6

    #: F-2 — when True, a failed kij lookup is a hard error instead of a warning.
    #: Recommended True in production. Left False by default because it changes
    #: the failure mode of the original model and needs a deliberate choice.
    require_interaction_parameters: bool = False

    #: Default gauge reference pressure, Pa. Override per site elevation.
    p_atm_default: float = 101325.0

    #: Practical Peng-Robinson advisory band. Outside these, results are still
    #: returned but carry a CONDITION_OUT_OF_RANGE warning.
    t_min_k: float = 100.0
    t_max_k: float = 1200.0
    p_max_pa: float = 2.0e7

    #: F-3 — component slates pre-built at startup so no user request pays the
    #: multi-second flasher build. Comma-separated CAS lists, semicolon between
    #: slates. Empty disables warm-up.
    warm_slates: str = ""

    calculation_timeout_s: float = 120.0

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def warm_slate_list(self) -> list[list[str]]:
        out: list[list[str]] = []
        for slate in self.warm_slates.split(";"):
            cas = [c.strip() for c in slate.split(",") if c.strip()]
            if cas:
                out.append(cas)
        return out


@lru_cache
def get_settings() -> Settings:
    return Settings()
