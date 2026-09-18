"""Runtime configuration for the minimal local agent."""

from __future__ import annotations

import os
from dataclasses import dataclass
from collections.abc import Mapping, MutableMapping
from pathlib import Path
import re


DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"
DEFAULT_LLM_TIMEOUT_SECONDS = 120
DEFAULT_LLM_NUM_RETRIES = 1
SUPPORTED_PROVIDERS = {"gemini", "groq"}
_ENV_ENTRY = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


@dataclass(frozen=True)
class Settings:
    """Configuration resolved from environment variables.

    Gemini calls use the bank connector's native Vertex-shaped endpoint inside
    this process. Groq uses its OpenAI-compatible endpoint directly.
    """

    provider: str
    model: str
    base_url: str | None
    api_key: str
    llm_timeout_seconds: int
    llm_num_retries: int


def _integer_setting(
    values: Mapping[str, str], name: str, default: int, *, minimum: int
) -> int:
    raw_value = values.get(name)
    if raw_value is None or not raw_value.strip():
        return default
    try:
        value = int(raw_value)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer.") from error
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}.")
    return value


def _gemini_connector_model(model: str) -> str:
    """Route Gemini calls through the in-process connector provider."""
    return model if model.startswith("bank_gemini/") else f"bank_gemini/{model}"


def _groq_compatible_model(model: str) -> str:
    """Tell LiteLLM to use Groq's OpenAI-compatible API."""
    return model if model.startswith("groq/") else f"groq/{model}"


def load_env_file(path: Path, environ: MutableMapping[str, str] | None = None) -> None:
    """Load simple KEY=VALUE entries without overwriting inherited secrets."""
    if not path.is_file():
        return

    target = os.environ if environ is None else environ
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _ENV_ENTRY.match(line)
        if not match:
            raise ValueError(f"Invalid configuration entry in {path.name}")
        name, value = match.groups()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        target.setdefault(name, value)


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Load configuration without reading or persisting secret files."""
    values = os.environ if env is None else env
    provider = values.get("BANK_AGENT_PROVIDER", "gemini").strip().lower()
    if provider not in SUPPORTED_PROVIDERS:
        supported = ", ".join(sorted(SUPPORTED_PROVIDERS))
        raise ValueError(f"BANK_AGENT_PROVIDER must be one of: {supported}.")

    if provider == "gemini":
        configured_model = (
            values.get("BANK_AGENT_MODEL")
            or values.get("BANK_AGENT_GEMINI_MODEL")
            or values.get("GEMINI_MODEL")
            or DEFAULT_GEMINI_MODEL
        )
        model = _gemini_connector_model(configured_model)
        connector_root = (
            values.get("BANK_AGENT_BASE_URL")
            or values.get("BANK_AGENT_GEMINI_BASE_URL")
            or values.get("GEMINI_BASE_URL")
        )
        project = values.get("BANK_AGENT_GEMINI_VERTEX_PROJECT") or values.get("GEMINI_VERTEX_PROJECT")
        location = values.get("BANK_AGENT_GEMINI_VERTEX_LOCATION") or values.get("GEMINI_VERTEX_LOCATION")
        if not connector_root or not project or not location:
            raise ValueError(
                "Gemini requires BANK_AGENT_GEMINI_BASE_URL, "
                "BANK_AGENT_GEMINI_VERTEX_PROJECT, and BANK_AGENT_GEMINI_VERTEX_LOCATION."
            )
        base_url = (
            f"{connector_root.rstrip('/')}/v1/projects/{project}/locations/{location}/publishers/google"
        )
        api_key = (
            values.get("BANK_AGENT_API_KEY")
            or values.get("BANK_AGENT_GEMINI_API_KEY")
            or values.get("GEMINI_API_KEY")
        )
    else:
        configured_model = (
            values.get("BANK_AGENT_MODEL")
            or values.get("BANK_AGENT_GROQ_MODEL")
            or DEFAULT_GROQ_MODEL
        )
        model = _groq_compatible_model(configured_model)
        base_url = values.get("BANK_AGENT_BASE_URL") or values.get(
            "BANK_AGENT_GROQ_BASE_URL"
        )
        api_key = (
            values.get("BANK_AGENT_API_KEY")
            or values.get("BANK_AGENT_GROQ_API_KEY")
            or values.get("GROQ_API_KEY")
        )

    if not api_key:
        raise ValueError(
            f"Set an API key for the configured {provider} provider before starting "
            "bank-agent."
        )

    llm_timeout_seconds = _integer_setting(
        values,
        "BANK_AGENT_LLM_TIMEOUT_SECONDS",
        DEFAULT_LLM_TIMEOUT_SECONDS,
        minimum=1,
    )
    llm_num_retries = _integer_setting(
        values,
        "BANK_AGENT_LLM_NUM_RETRIES",
        DEFAULT_LLM_NUM_RETRIES,
        minimum=0,
    )

    return Settings(
        provider=provider,
        model=model,
        base_url=base_url,
        api_key=api_key,
        llm_timeout_seconds=llm_timeout_seconds,
        llm_num_retries=llm_num_retries,
    )
