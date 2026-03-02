"""LLM client factory — reads provider/key from environment."""
import os
from openai import OpenAI

PROVIDER_URLS = {
    "openrouter": "https://openrouter.ai/api/v1",
    "anthropic": "https://api.anthropic.com/v1/",
}


def get_provider():
    return os.environ.get("LLM_PROVIDER", "openrouter").lower()


def get_client():
    provider = get_provider()
    api_key = os.environ.get("LLM_API_KEY", "") or os.environ.get("OPENROUTER_API_KEY", "")
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
