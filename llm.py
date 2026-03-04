"""LLM client factory — reads provider/key from config.yaml, falls back to env."""
import os
import time
from pathlib import Path

import yaml
from openai import OpenAI, RateLimitError, APIError

PROVIDER_URLS = {
    "openrouter": "https://openrouter.ai/api/v1",
    "anthropic": "https://api.anthropic.com/v1/",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
}


def _load_llm_config() -> dict:
    """Read the llm: block from config.yaml (provider + api_key)."""
    cfg_path = Path(__file__).parent / "config.yaml"
    if cfg_path.exists():
        cfg = yaml.safe_load(cfg_path.read_text()) or {}
        return cfg.get("llm", {}) or {}
    return {}


def _load_vertex_config() -> dict:
    """Read the vertex: block from config.yaml (project + location)."""
    cfg_path = Path(__file__).parent / "config.yaml"
    if cfg_path.exists():
        cfg = yaml.safe_load(cfg_path.read_text()) or {}
        return cfg.get("vertex", {}) or {}
    return {}


def get_provider():
    llm_cfg = _load_llm_config()
    provider = llm_cfg.get("provider", "") or os.environ.get("LLM_PROVIDER", "openrouter")
    return provider.lower()


def _get_vertex_token() -> str:
    """Get a fresh access token from Application Default Credentials."""
    try:
        import google.auth
        import google.auth.transport.requests
    except ImportError:
        raise RuntimeError(
            "google-auth is required for Vertex AI. Install it with: pip install google-auth"
        )

    try:
        credentials, _ = google.auth.default()
        credentials.refresh(google.auth.transport.requests.Request())
    except google.auth.exceptions.DefaultCredentialsError:
        raise RuntimeError(
            "Vertex AI requires Google Cloud credentials. Run:\n"
            "  gcloud auth application-default login\n"
            "Then restart the agent."
        )
    return credentials.token


def get_client():
    provider = get_provider()
    llm_cfg = _load_llm_config()

    if provider == "vertex":
        vertex_cfg = _load_vertex_config()
        project = vertex_cfg.get("project", "") or os.environ.get("VERTEX_PROJECT", "")
        if not project:
            raise RuntimeError(
                "Vertex AI requires a GCP project ID. Set it in config.yaml under vertex.project\n"
                "or via the dashboard Configure tab, or set VERTEX_PROJECT env var."
            )
        token = _get_vertex_token()
        base_url = (
            f"https://aiplatform.googleapis.com/v1beta1/"
            f"projects/{project}/locations/global/endpoints/openapi"
        )
        return OpenAI(base_url=base_url, api_key=token)

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
    if provider == "vertex":
        # Vertex AI OpenAI endpoint requires "google/model-name" format
        if "/" not in model:
            model = f"google/{model}"
        return model
    if provider == "gemini":
        # Google AI Studio uses bare model names: "gemini-2.0-flash"
        if "/" in model:
            model = model.split("/", 1)[1]
        return model
    return model


def completions_with_retry(client, max_retries=5, **kwargs):
    """Call client.chat.completions.create with exponential backoff on 429/5xx errors."""
    for attempt in range(max_retries):
        try:
            return client.chat.completions.create(**kwargs)
        except RateLimitError as e:
            if attempt == max_retries - 1:
                raise
            wait = min(2 ** attempt * 5, 60)  # 5s, 10s, 20s, 40s, 60s
            print(f"  Rate limited (429). Retrying in {wait}s... (attempt {attempt + 1}/{max_retries})")
            time.sleep(wait)
        except APIError as e:
            if e.status_code and e.status_code >= 500 and attempt < max_retries - 1:
                wait = min(2 ** attempt * 5, 60)
                print(f"  Server error ({e.status_code}). Retrying in {wait}s... (attempt {attempt + 1}/{max_retries})")
                time.sleep(wait)
            else:
                raise
