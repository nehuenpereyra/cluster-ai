"""tests/test_ai_client.py - Pruebas unitarias para el cliente OpenAI compatible."""

import os
from unittest.mock import MagicMock, patch
import pytest
from modules.engine.ai import AIClient, AgnesAIClient, OpenAICompatibleClient
from shared.exceptions import AIClientError, AgnesAIError


def test_ai_client_alias_equality():
    """Verifica que los alias AIClient y AgnesAIClient apunten a OpenAICompatibleClient."""
    assert AIClient is OpenAICompatibleClient
    assert AgnesAIClient is OpenAICompatibleClient
    assert AgnesAIError is AIClientError


def test_ai_client_init_defaults(monkeypatch):
    """Verifica valores por defecto cuando no hay variables configuradas."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    client = OpenAICompatibleClient()
    assert client.api_key == ""
    assert client.base_url == "https://api.openai.com/v1"
    assert client.model == "gpt-4o-mini"
    assert not client.is_local_endpoint


def test_ai_client_init_with_env(monkeypatch):
    """Verifica que tome las variables OPENAI_* configuradas en el entorno."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://apihub.agnes-ai.com/v1")
    monkeypatch.setenv("OPENAI_MODEL", "agnes-2.5-flash")

    client = OpenAICompatibleClient()
    assert client.api_key == "sk-test-key"
    assert client.base_url == "https://apihub.agnes-ai.com/v1"
    assert client.model == "agnes-2.5-flash"


def test_ai_client_local_endpoint(monkeypatch):
    """Verifica detección de endpoint local (Ollama / LM Studio)."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = OpenAICompatibleClient(base_url="http://localhost:11434/v1", model="llama3")
    assert client.is_local_endpoint


@patch("httpx.Client")
def test_ai_client_chat_completion_success(mock_client_class):
    """Verifica que procese correctamente una respuesta 200 con el protocolo OpenAI."""
    mock_post = MagicMock()
    mock_post.status_code = 200
    mock_post.json.return_value = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Recomendación: vectorizar con NumPy y usar cola medium."
                }
            }
        ]
    }

    mock_client_instance = MagicMock()
    mock_client_instance.__enter__.return_value.post.return_value = mock_post
    mock_client_class.return_value = mock_client_instance

    client = OpenAICompatibleClient(
        api_key="sk-test-123",
        base_url="https://api.groq.com/openai/v1",
        model="llama-3.3-70b-versatile"
    )

    messages = [{"role": "user", "content": "Analizar script"}]
    result = client.chat_completion(messages)

    assert "Recomendación: vectorizar con NumPy" in result
    mock_client_instance.__enter__.return_value.post.assert_called_once()
    call_args, call_kwargs = mock_client_instance.__enter__.return_value.post.call_args
    assert call_args[0] == "https://api.groq.com/openai/v1/chat/completions"
    assert call_kwargs["headers"]["Authorization"] == "Bearer sk-test-123"
    assert call_kwargs["json"]["model"] == "llama-3.3-70b-versatile"


def test_ai_client_heuristic_fallback_when_no_key(monkeypatch):
    """Verifica fallback heurístico con advertencia clara si no hay OPENAI_API_KEY."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    client = OpenAICompatibleClient()
    result = client.chat_completion([{"role": "user", "content": "test"}])

    assert "Modo Contingencia Heurístico Activado" in result
    assert "Falta OPENAI_API_KEY en .env" in result
    assert "Zen 3" in result


@patch("httpx.Client")
def test_ai_client_heuristic_fallback_on_401(mock_client_class):
    """Verifica fallback heurístico cuando el servidor responde 401 Unauthorized."""
    mock_post = MagicMock()
    mock_post.status_code = 401
    mock_post.text = "Invalid API Key"

    mock_client_instance = MagicMock()
    mock_client_instance.__enter__.return_value.post.return_value = mock_post
    mock_client_class.return_value = mock_client_instance

    client = OpenAICompatibleClient(api_key="sk-bad-key")
    result = client.chat_completion([{"role": "user", "content": "test"}])

    assert "Modo Contingencia Heurístico Activado" in result
    assert "401 Unauthorized" in result
    assert "OPENAI_API_KEY" in result
