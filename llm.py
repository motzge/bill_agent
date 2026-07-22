"""LLM providers for bill_agent.

One small interface (LLMProvider) on a shared backbone (_ChatProvider:
prompt templates + extract/repair, _post_with_retry: transport). Concrete
providers only supply payload shape and startup verification:

  OllamaProvider       local dev (qwen3-coder)
  OpenAICompatProvider any /chat/completions endpoint -- Gemini, Groq,
                       Mistral, OpenRouter; used for free-tier testing
  Claude provider      lands with the API migration

Everything returned here is UNTRUSTED text. Parsing and validation happen
behind models.invoice_from_llm_output -- never here.

Free-tier reminder: those endpoints may train on submitted data.
Synthetic test invoices only -- never real customer documents.
"""

from __future__ import annotations

import logging
import time
from typing import Protocol

import requests

from config import BASE_DIR, LLM_BACKOFF_BASE_SECONDS, LLM_MAX_RETRIES, Settings
from models import InvoiceSource

logger = logging.getLogger(__name__)

PROMPTS_DIR = BASE_DIR / "prompts"
EXTRACT_PROMPT = "extract_v1.txt"
REPAIR_PROMPT = "repair_v1.txt"

MAX_BACKOFF_SECONDS = 90  #free-tier Retry-After can be a full minute

_TRANSIENT = (requests.Timeout, requests.ConnectionError)


class LlmError(RuntimeError):
    """LLM call failed for good (after retries) or was rejected."""


class LLMProvider(Protocol):
    def verify(self) -> None:
        """Startup check: raise LlmError if the provider cannot work."""
        ...

    def extract(self, source: InvoiceSource) -> str:
        """Return the raw model output for one invoice."""
        ...

    def repair(self, source: InvoiceSource, previous_output: str, error: str) -> str:
        """One shot at fixing schema-invalid output. Caller escalates after."""
        ...


def create_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "ollama":
        return OllamaProvider(settings)
    if settings.llm_provider == "openai_compat":
        return OpenAICompatProvider(settings)
    if settings.llm_provider == "gemini":
        return GeminiProvider(settings)
    #settings guarantees the value is a supported provider name
    raise NotImplementedError(
        "Claude provider lands with the API migration (LLM_PROVIDER=claude)."
    )


def _load_prompt(filename: str) -> str:
    path = PROMPTS_DIR / filename
    if not path.is_file():
        raise LlmError(f"Prompt file missing: {path}")
    return path.read_text(encoding="utf-8")


def _fill(template: str, **values: str) -> str:
    """Placeholder substitution via replace, NOT str.format: invoice text and
    previous JSON output are full of curly braces and would blow up format().
    """
    for key, value in values.items():
        template = template.replace("{" + key + "}", value)
    return template


def _post_with_retry(
    url: str,
    *,
    payload: dict,
    timeout: int,
    headers: dict | None = None,
    description: str = "LLM call",
) -> requests.Response:
    """POST with exponential backoff on transient failures only.

    Transient: network errors, 5xx, and 429 (rate limits are business as
    usual on free tiers; Retry-After is honored). Any other 4xx is our bug
    or our key -- retrying the same wrong question does not help: fail fast.

    Waits grow 5s -> 15s -> 45s: an overloaded provider (HTTP 503) needs
    more than a couple of seconds, and aborting a whole morning batch over
    a brief outage is worse than waiting a minute.
    """
    last_error: Exception | None = None

    for attempt in range(1, LLM_MAX_RETRIES + 1):
        delay = LLM_BACKOFF_BASE_SECONDS * 3 ** (attempt - 1)
        try:
            response = requests.post(url, json=payload, timeout=timeout, headers=headers or {})
        except _TRANSIENT as exc:
            last_error = exc
            logger.warning(
                "%s attempt %d/%d failed: %s",
                description, attempt, LLM_MAX_RETRIES, type(exc).__name__,
            )
        else:
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After", "").strip()
                if retry_after.isdigit():
                    delay = max(delay, int(retry_after))
                last_error = LlmError("rate limited (HTTP 429)")
                logger.warning(
                    "%s attempt %d/%d: rate limited",
                    description, attempt, LLM_MAX_RETRIES,
                )
            elif response.status_code >= 500:
                last_error = LlmError(f"server error (HTTP {response.status_code})")
                logger.warning(
                    "%s attempt %d/%d: HTTP %d",
                    description, attempt, LLM_MAX_RETRIES, response.status_code,
                )
            elif response.status_code != 200:
                raise LlmError(
                    f"{description} rejected (HTTP {response.status_code}): "
                    f"{response.text[:200]}"
                )
            else:
                return response

        if attempt < LLM_MAX_RETRIES:
            wait = min(delay, MAX_BACKOFF_SECONDS)
            logger.debug("Retrying in %ds", wait)
            time.sleep(wait)

    raise LlmError(f"{description} failed after {LLM_MAX_RETRIES} attempts") from last_error


class _ChatProvider:
    """Shared backbone: templates + extract/repair. Subclasses implement
    _chat() (payload shape + response parsing) and verify()."""

    def __init__(self) -> None:
        self._extract_template = _load_prompt(EXTRACT_PROMPT)
        self._repair_template = _load_prompt(REPAIR_PROMPT)

    def extract(self, source: InvoiceSource) -> str:
        if not source.text:
            #main must run OCR first - reaching this line is a pipeline bug
            raise ValueError(f"extract() called without text: {source.path.name}")
        return self._chat(_fill(self._extract_template, invoice_text=source.text))

    def repair(self, source: InvoiceSource, previous_output: str, error: str) -> str:
        prompt = _fill(
            self._repair_template,
            invoice_text=source.text or "",
            previous_output=previous_output,
            validation_errors=error,
        )
        return self._chat(prompt)

    def _chat(self, prompt: str) -> str:
        raise NotImplementedError


class OllamaProvider(_ChatProvider):
    """Talks to a local Ollama server via plain HTTP."""

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._base_url = settings.ollama_base_url
        self._url = f"{settings.ollama_base_url}/api/chat"
        self._model = settings.ollama_model
        self._timeout = settings.llm_timeout_seconds

    def verify(self) -> None:
        """Fail loud at startup: is Ollama reachable, is the model installed?

        Without this, a typo in OLLAMA_MODEL only surfaces at invoice #1 as
        an aborted run. Message is German -- it goes straight to the operator.
        """
        try:
            response = requests.get(f"{self._base_url}/api/tags", timeout=10)
            response.raise_for_status()
            models = [m.get("name", "") for m in response.json().get("models", [])]
        except (requests.RequestException, ValueError) as exc:
            raise LlmError(
                f"Ollama ist unter {self._base_url} nicht erreichbar "
                f"({type(exc).__name__}). Läuft der Ollama-Dienst?"
            ) from exc
        if not any(name == self._model or name.split(":")[0] == self._model
                   for name in models):
            raise LlmError(
                f"Das Modell '{self._model}' ist in Ollama nicht installiert. "
                f"Verfügbar: {', '.join(models) or 'keine Modelle'}"
            )
        logger.debug("Ollama reachable, model %s available", self._model)

    def _chat(self, prompt: str) -> str:
        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "format": "json",  #Ollama constrains output to valid JSON
            "options": {"temperature": 0},
        }
        response = _post_with_retry(
            self._url, payload=payload, timeout=self._timeout, description="Ollama call"
        )
        try:
            content = response.json()["message"]["content"]
        except (ValueError, KeyError, TypeError) as exc:
            raise LlmError("Unexpected Ollama response shape") from exc
        return _require_text(content)


class OpenAICompatProvider(_ChatProvider):
    """Any OpenAI-compatible /chat/completions endpoint.

    Defaults in config point at Google Gemini; Groq, Mistral and OpenRouter
    work by changing OPENAI_COMPAT_BASE_URL and OPENAI_COMPAT_MODEL in .env.
    """

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._base_url = settings.openai_compat_base_url
        self._url = f"{self._base_url}/chat/completions"
        self._model = settings.openai_compat_model
        self._headers = {"Authorization": f"Bearer {settings.openai_compat_api_key}"}
        self._timeout = settings.llm_timeout_seconds

    def verify(self) -> None:
        try:
            response = requests.get(
                f"{self._base_url}/models", headers=self._headers, timeout=10
            )
        except requests.RequestException as exc:
            raise LlmError(
                f"API-Endpoint {self._base_url} ist nicht erreichbar "
                f"({type(exc).__name__})."
            ) from exc
        if response.status_code in (401, 403):
            raise LlmError(
                f"Der API-Key wurde vom Anbieter abgelehnt (HTTP "
                f"{response.status_code}). Bitte den Key in der .env prüfen."
            )
        if response.status_code != 200:
            raise LlmError(
                f"Unerwartete Antwort vom API-Endpoint (HTTP {response.status_code})."
            )
        try:
            model_ids = [m.get("id", "") for m in response.json().get("data", [])]
        except ValueError:
            model_ids = []
        if model_ids and not any(self._model in model_id for model_id in model_ids):
            #soft check only: some providers list models under prefixed ids
            logger.warning(
                "Model %r not found among %d provider models; continuing anyway",
                self._model, len(model_ids),
            )
        logger.debug("OpenAI-compat endpoint reachable, using model %s", self._model)

    def _chat(self, prompt: str) -> str:
        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        response = _post_with_retry(
            self._url, payload=payload, timeout=self._timeout,
            headers=self._headers, description="LLM call",
        )
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LlmError("Unexpected response shape from OpenAI-compatible endpoint") from exc
        return _require_text(content)


class GeminiProvider(_ChatProvider):
    """Native Gemini endpoint (models/{model}:generateContent).

    Exists because Google's new "AQ." auth keys are (still) rejected on the
    OpenAI-compatible path during the 2026 key-format transition, while the
    native path accepts them. Payload shape differs: contents/parts instead
    of messages, x-goog-api-key header instead of Bearer, and JSON output
    is forced via responseMimeType.
    """

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._base_url = settings.gemini_base_url
        self._model = settings.gemini_model
        self._headers = {"x-goog-api-key": settings.gemini_api_key}
        self._timeout = settings.llm_timeout_seconds

    def verify(self) -> None:
        try:
            response = requests.get(
                f"{self._base_url}/models", headers=self._headers, timeout=10
            )
        except requests.RequestException as exc:
            raise LlmError(
                f"Die Gemini-API ist nicht erreichbar ({type(exc).__name__})."
            ) from exc
        #Google answers bad keys with 400 INVALID_ARGUMENT, not only 401/403
        if response.status_code in (400, 401, 403):
            raise LlmError(
                f"Der Gemini-API-Key wurde abgelehnt (HTTP {response.status_code}). "
                "Bitte den Key in der .env prüfen."
            )
        if response.status_code != 200:
            raise LlmError(
                f"Unerwartete Antwort von der Gemini-API (HTTP {response.status_code})."
            )
        try:
            names = [m.get("name", "") for m in response.json().get("models", [])]
        except ValueError:
            names = []
        if names and not any(self._model in name for name in names):
            logger.warning(
                "Model %r not found among %d Gemini models; continuing anyway",
                self._model, len(names),
            )
        logger.debug("Gemini reachable, using model %s", self._model)

    def _chat(self, prompt: str) -> str:
        url = f"{self._base_url}/models/{self._model}:generateContent"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
            },
        }
        response = _post_with_retry(
            url, payload=payload, timeout=self._timeout,
            headers=self._headers, description="Gemini call",
        )
        try:
            content = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LlmError("Unexpected Gemini response shape") from exc
        return _require_text(content)


def _require_text(content: object) -> str:
    if not isinstance(content, str) or not content.strip():
        raise LlmError("Provider returned empty content")
    logger.debug("LLM returned %d chars", len(content))
    return content