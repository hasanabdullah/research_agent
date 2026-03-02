"""LLM client factory — reads provider/key from config.yaml, falls back to env."""
import os
from pathlib import Path

import yaml
from openai import OpenAI

PROVIDER_URLS = {
    "openrouter": "https://openrouter.ai/api/v1",
    "anthropic": "https://api.anthropic.com/v1/",
}


def _load_llm_config() -> dict:
    """Read the llm: block from config.yaml (provider + api_key)."""
    cfg_path = Path(__file__).parent / "config.yaml"
    if cfg_path.exists():
        cfg = yaml.safe_load(cfg_path.read_text()) or {}
        return cfg.get("llm", {}) or {}
    return {}


def get_provider():
    llm_cfg = _load_llm_config()
    provider = llm_cfg.get("provider", "") or os.environ.get("LLM_PROVIDER", "openrouter")
    return provider.lower()


def get_client():
    provider = get_provider()
    llm_cfg = _load_llm_config()
    api_key = (
        llm_cfg.get("api_key", "")
        or os.environ.get("LLM_API_KEY", "")
        or os.environ.get("OPENROUTER_API_KEY", "")
    )
    base_url = PROVIDER_URLS.get(provider)
    if base_url:
        return OpenAI(base_url=base_url, api_key=api_key)
    return OpenAI(api_key=api_key)


def resolve_model(model):
    """Adapt model name for the active provider."""
    provider = get_provider()
    if provider == "anthropic" and "/" in model:
        # "anthropic/claude-opus-4.6" → "claude-opus-4-6"
        model = model.split("/", 1)[1]
    if provider == "anthropic":
        model = model.replace(".", "-")
    return model
