# bill_agent — Automated Invoice Processing

A production-minded AI agent that reads supplier invoices from PDFs, scans and
photos, extracts the data with an LLM, validates it against business rules, and
writes it into monthly Excel files. Uncertain cases are escalated for human
review instead of being silently mis-booked.

Built as a portfolio project from a realistic client simulation (automotive
parts wholesaler, 30–50 supplier invoices per day, Excel handoff to a tax
advisor).

> **Language note:** This tool processes **German and Austrian** invoices and
> its operator UI is in **German** — that is a product requirement, not an
> oversight. Source code and this documentation are in English; the end-user
> guide ([`ANLEITUNG.md`](ANLEITUNG.md)) is in German, for the people who
> actually operate the tool.

---

## Core idea

With accounting data, a wrong number is worse than no number. The agent is
built around a strict trust boundary: **everything the LLM returns is treated
as untrusted until it survives validation.** Whatever fails the checks is not
booked — it is handed to a human with a clear, itemized reason.

## Features

- **Three input paths:** digital PDFs (text layer), scanned PDFs and photos
  (JPG/PNG). Text-layer detection decides automatically whether OCR is needed.
- **Provider-agnostic:** Ollama (local), Google Gemini, and any
  OpenAI-compatible service (Groq, Mistral, OpenRouter) — switchable via `.env`
  alone, no code changes.
- **Resilient extraction:** enforced JSON output, one automatic repair attempt
  on invalid output, then escalation.
- **Business-rule validation:** totals reconciliation (net + tax = gross),
  per-line tax plausibility, VAT-ID format, due-date derivation from payment
  terms, duplicate detection, future-date detection.
- **Exact figures:** all monetary amounts as `Decimal`, never `float`.
- **Fail-safe:** an outage of the AI service aborts the run cleanly — nothing
  is lost, nothing is booked twice, and a crash on invoice 17 leaves 1–16
  untouched.
- **UI (Streamlit)** for non-technical operators, plus a CLI for automation.
- **Traceability:** structured logging with a per-run ID.

## Security & privacy

- LLM output is treated as data, not as instructions (prompt-injection
  hardening).
- Invoice contents never appear in logs.
- API keys live only in `.env`, never in code; `.env` is git-ignored.
- For sensitive real-world data, fully local processing via Ollama is available
  — no data leaves the machine.

## Architecture

Modular by design, one responsibility per file:

| File | Responsibility |
|---|---|
| `models.py` | Pydantic schema — the trust boundary for LLM output |
| `ingest.py` | File and text-layer detection |
| `ocr.py` | OCR for scans/photos (isolated, swappable) |
| `llm.py` | Provider abstraction + retry/backoff |
| `validation.py` | Business rules and escalation reasons |
| `excel_writer.py` | Monthly workbooks, dedup, formatting |
| `main.py` | Orchestration with per-invoice error isolation |
| `app.py` | Streamlit UI |

The provider abstraction uses a shared base class with common transport
(timeout, exponential backoff, handling of transient errors including rate
limits); concrete providers supply only the payload shape and a startup check.
Adding a provider is a small subclass.

**Stack:** Python 3.11+, Pydantic, pdfplumber, pytesseract, pypdfium2,
openpyxl, Streamlit, requests.

## Quick start (development)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # set provider + key
# OCR system package (for scans/photos):
#   sudo apt install tesseract-ocr tesseract-ocr-deu
streamlit run app.py
```

This repository does not ship sample invoices. Generate synthetic test data
(varied layouts, scans, photos, and deliberate error cases) locally:

```bash
python tools/make_test_invoices.py
```

## Provider configuration

```bash
# Local, free, no data leaves the machine
LLM_PROVIDER=ollama
OLLAMA_MODEL=qwen3-coder

# Google Gemini (free tier available)
LLM_PROVIDER=gemini
GEMINI_MODEL=gemini-3.5-flash
GEMINI_API_KEY=...

# OpenAI-compatible (Groq, Mistral, OpenRouter ...)
LLM_PROVIDER=openai_compat
OPENAI_COMPAT_BASE_URL=https://api.groq.com/openai/v1
OPENAI_COMPAT_MODEL=llama-3.3-70b-versatile
OPENAI_COMPAT_API_KEY=...
```

> Free tiers may train on submitted data — use synthetic data only. Real
> customer data belongs on a paid account under a data-processing agreement, or
> on a local model (Ollama).

## End-user documentation

Windows setup for operators (double-click launch, key entry, day-to-day use) is
in [`ANLEITUNG.md`](ANLEITUNG.md) — in German, matching the tool's audience.

## Status

Working and tested against a local model (Ollama) and a cloud model (Gemini). A
native Claude API provider is scaffolded but not yet enabled.

## License

Portfolio / demonstration project.
