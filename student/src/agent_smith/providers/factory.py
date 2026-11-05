"""Select a configured model provider without exposing credential values."""

from .litellm_client import LiteLLMClient


def create_model_client(provider: str, *, model: str, api_url: str | None = None,
                        json_mode: bool = False) -> LiteLLMClient:
    return LiteLLMClient(provider, model=model, api_url=api_url, json_mode=json_mode)
