"""Central configuration for bill_agent.

Loads environment variables from .env, validates them, and exposes one
immutable Settings object. Fails loud at startup: a half-configured run
must never start.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

BASE_DIR = Path(__file__).resolve().parent

#Folder layout - created on startup via ensure_directories()
INPUT_DIR = BASE_DIR / "input"
PROCESSED_DIR = BASE_DIR / "processed"
REVIEW_DIR = BASE_DIR / "review"
OUTPUT_DIR = BASE_DIR / "output"
LOGS_DIR = BASE_DIR / "logs"

ALL_DIRS = (INPUT_DIR, PROCESSED_DIR, REVIEW_DIR, OUTPUT_DIR, LOGS_DIR)

#Business/tuning constants live here, NOT in .env - editing an env file must not be able to weaken validation.
GROSS_TOTAL_TOLERANCE = Decimal("0.02")  #abs tolerance: sum(net+tax) vs gross_total
TEXT_LAYER_MIN_CHARS = 100  #fewer chars from pdfplumber => treat PDF as scanned
LLM_MAX_RETRIES = 4  #transient errors only (timeout, 5xx, 429, connection)
LLM_BACKOFF_BASE_SECONDS = 5  #waits: 5s, 15s, 45s -- survives ~1 min of outage
REPAIR_ATTEMPTS = 1  #one repair prompt on invalid LLM output, then escalate
PROCESSED_RETENTION_DAYS = 60  #auto-delete booked originals after this

SUPPORTED_PROVIDERS = ("ollama", "openai_compat", "gemini", "claude")

SUPPORTED_EXTENSIONS = (".pdf", ".jpg", ".jpeg", ".png")

#Which providers need an API key, and how to recognize a valid one.
#env_var: where the key lives in .env
#prefix:  expected start of the key ("" = no prefix check, provider formats vary)
#label:   provider name for user-facing messages
#Ollama is absent on purpose: local, no key.
PROVIDER_KEYS = {
    "claude": {"env_var": "ANTHROPIC_API_KEY", "prefix": "sk-ant-", "label": "Anthropic"},
    "gemini": {"env_var": "GEMINI_API_KEY", "prefix": "", "label": "Gemini"},
    "openai_compat": {"env_var": "OPENAI_COMPAT_API_KEY", "prefix": "", "label": "API"},
}


class ConfigError(RuntimeError):
    """Required configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    llm_provider: str
    llm_timeout_seconds: int
    ollama_base_url: str
    ollama_model: str
    openai_compat_base_url: str
    openai_compat_model: str
    openai_compat_api_key: str | None
    gemini_base_url: str
    gemini_model: str
    gemini_api_key: str | None
    anthropic_api_key: str | None


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(f"Missing required environment variable: {name}")
    return value


def _positive_int(name: str, default: str) -> int:
    raw = os.getenv(name, default).strip()
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer, got: {raw!r}") from None
    if value <= 0:
        raise ConfigError(f"{name} must be > 0, got: {value}")
    return value


def load_settings() -> Settings:
    """Read .env + environment, validate, return immutable settings."""
    load_dotenv(BASE_DIR / ".env")

    provider = os.getenv("LLM_PROVIDER", "ollama").strip().lower()
    if provider not in SUPPORTED_PROVIDERS:
        raise ConfigError(
            f"LLM_PROVIDER must be one of {SUPPORTED_PROVIDERS}, got: {provider!r}"
        )

    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip() or None
    if provider == "claude" and api_key is None:
        raise ConfigError("LLM_PROVIDER=claude requires ANTHROPIC_API_KEY")

    compat_key = os.getenv("OPENAI_COMPAT_API_KEY", "").strip() or None
    if provider == "openai_compat" and compat_key is None:
        raise ConfigError("LLM_PROVIDER=openai_compat requires OPENAI_COMPAT_API_KEY")

    gemini_key = os.getenv("GEMINI_API_KEY", "").strip() or None
    if provider == "gemini" and gemini_key is None:
        raise ConfigError("LLM_PROVIDER=gemini requires GEMINI_API_KEY")

    return Settings(
        llm_provider=provider,
        llm_timeout_seconds=_positive_int("LLM_TIMEOUT_SECONDS", "120"),
        ollama_base_url=os.getenv(
            "OLLAMA_BASE_URL", "http://localhost:11434"
        ).strip().rstrip("/"),
        ollama_model=_require("OLLAMA_MODEL") if provider == "ollama" else "",
        openai_compat_base_url=os.getenv(
            "OPENAI_COMPAT_BASE_URL",
            "https://generativelanguage.googleapis.com/v1beta/openai",
        ).strip().rstrip("/"),
        openai_compat_model=os.getenv("OPENAI_COMPAT_MODEL", "gemini-2.5-flash").strip(),
        openai_compat_api_key=compat_key,
        gemini_base_url=os.getenv(
            "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"
        ).strip().rstrip("/"),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash").strip(),
        gemini_api_key=gemini_key,
        anthropic_api_key=api_key,
    )


def _active_provider() -> str:
    """Provider from a fresh .env read, falling back to the process env."""
    values = dotenv_values(BASE_DIR / ".env")
    return (values.get("LLM_PROVIDER") or os.getenv("LLM_PROVIDER") or "ollama").strip().lower()


def needs_api_key() -> bool:
    """True when the active provider needs an API key that is not set yet.

    Lightweight UI check: reads .env fresh from disk (dotenv_values does not
    touch os.environ), so it reflects a just-saved key immediately. Providers
    without a key (Ollama) always return False.
    """
    provider = _active_provider()
    spec = PROVIDER_KEYS.get(provider)
    if spec is None:
        return False
    values = dotenv_values(BASE_DIR / ".env")
    key = (values.get(spec["env_var"]) or os.getenv(spec["env_var"]) or "")
    return not key.strip()


def api_key_label() -> str:
    """Provider name for the setup UI, e.g. 'Anthropic'. '' if no key needed."""
    spec = PROVIDER_KEYS.get(_active_provider())
    return spec["label"] if spec else ""


def save_api_key(key: str) -> None:
    """Write the active provider's API key into .env (replacing old/commented
    lines). User-facing German errors: this is called from the operator UI.
    """
    provider = _active_provider()
    spec = PROVIDER_KEYS.get(provider)
    if spec is None:
        raise ConfigError(f"Der Anbieter '{provider}' benötigt keinen API-Key.")

    key = key.strip()
    if len(key) < 20:
        raise ConfigError("Der API-Key ist zu kurz. Bitte den Key vollständig einfügen.")
    if spec["prefix"] and not key.startswith(spec["prefix"]):
        raise ConfigError(
            f"Das sieht nicht wie ein {spec['label']}-API-Key aus "
            f"(er beginnt mit '{spec['prefix']}'). Bitte den Key vollständig einfügen."
        )

    env_var = spec["env_var"]
    env_path = BASE_DIR / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    out: list[str] = []
    replaced = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(env_var) or stripped.startswith(f"# {env_var}"):
            if not replaced:
                out.append(f"{env_var}={key}")
                replaced = True
            continue  #drop duplicates and commented leftovers
        out.append(line)
    if not replaced:
        out.append(f"{env_var}={key}")
    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    try:
        os.chmod(env_path, 0o600)  #owner-only where the OS supports it
    except OSError:
        pass
    os.environ[env_var] = key  #current process sees it immediately


def ensure_directories() -> None:
    """Create the working folders if missing. Safe to call on every run."""
    for directory in ALL_DIRS:
        directory.mkdir(parents=True, exist_ok=True)