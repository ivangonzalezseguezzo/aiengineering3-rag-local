"""Configuración del LLM: modelos por defecto, traducción de temperature y cupo de requests."""

import pytest
from pydantic import ValidationError

from llm import API_KEY_ENVS, get_chat_model, settings_from_env

KEY = "clave-de-prueba"


@pytest.fixture(autouse=True)
def entorno_limpio(monkeypatch: pytest.MonkeyPatch):
    """Que las variables del .env o de la terminal no cambien el resultado de los tests."""
    for nombres in API_KEY_ENVS.values():
        for nombre in nombres:
            monkeypatch.delenv(nombre, raising=False)
    for nombre in ("LLM_PROVIDER", "LLM_TEMPERATURE", "LLM_REQUESTS_PER_MINUTE", "LLM_THINKING_LEVEL"):
        monkeypatch.delenv(nombre, raising=False)
    for proveedor in API_KEY_ENVS:
        monkeypatch.delenv(f"{proveedor.upper()}_MODEL", raising=False)


def test_falta_api_key():
    with pytest.raises(ValidationError, match="falta la API key de gemini"):
        settings_from_env()


@pytest.mark.parametrize(
    ("proveedor", "modelo"),
    [("gemini", "gemini-3.5-flash-lite"), ("openai", "gpt-4o-mini"), ("anthropic", "claude-sonnet-5-5")],
)
def test_modelos_por_defecto(proveedor: str, modelo: str):
    assert settings_from_env(proveedor, api_key=KEY).model == modelo


def test_gemini_3_sin_temperature_y_con_thinking_low(caplog: pytest.LogCaptureFixture):
    modelo = get_chat_model("gemini", api_key=KEY, temperature=0.3)
    assert modelo.temperature is None
    assert modelo.thinking_level == "low"
    assert "no se envía" in caplog.text


def test_thinking_level_se_lee_del_entorno(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LLM_THINKING_LEVEL", "medium")
    modelo = get_chat_model("gemini", api_key=KEY, model="gemini-3.6-flash")
    assert modelo.thinking_level == "medium"


def test_gemini_2_recibe_temperature_sin_thinking():
    modelo = get_chat_model("gemini", api_key=KEY, model="gemini-2.5-flash", temperature=0.3)
    assert modelo.temperature == 0.3
    assert modelo.thinking_level is None


def test_openai_recibe_temperature():
    assert get_chat_model("openai", api_key=KEY, temperature=0.3).temperature == 0.3


def test_anthropic_nunca_recibe_temperature(caplog: pytest.LogCaptureFixture):
    modelo = get_chat_model("anthropic", api_key=KEY, temperature=0.3)
    assert modelo.temperature is None
    assert "no se envía" in caplog.text


@pytest.mark.parametrize(("proveedor", "esperado"), [("gemini", 1), ("openai", 0), ("anthropic", 0)])
def test_sdk_sin_reintentos_internos(proveedor: str, esperado: int):
    # Los reintentos son de la cadena. En langchain-google-genai max_retries cuenta intentos
    # totales (1 = sin reintentos); en OpenAI y Anthropic cuenta reintentos (0 = ninguno).
    assert get_chat_model(proveedor, api_key=KEY).max_retries == esperado


def test_rate_limiter_compartido():
    sin_limite = get_chat_model("gemini", api_key=KEY)
    a = get_chat_model("gemini", api_key=KEY, requests_per_minute=5)
    b = get_chat_model("gemini", api_key=KEY, requests_per_minute=5, max_tokens=16)
    assert sin_limite.rate_limiter is None
    assert a.rate_limiter is b.rate_limiter
    assert a.rate_limiter.requests_per_second == pytest.approx(5 / 60)


@pytest.mark.parametrize("invalido", [{"temperature": 3}, {"max_tokens": 0}, {"thinking_level": "minimal"}])
def test_valida_rangos(invalido: dict):
    with pytest.raises(ValidationError):
        settings_from_env("gemini", api_key=KEY, **invalido)
