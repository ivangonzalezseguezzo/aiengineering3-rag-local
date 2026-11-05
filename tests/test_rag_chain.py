import json

from typing import Any

import pytest
from langchain_core.exceptions import ModelAuthenticationError, ModelRateLimitError, OutputParserException
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from config import RAGSettings
from ingest import ingestar
from rag_chain import ERROR_MAX_CHARS, MAX_ATTEMPTS, RAGPipeline, resumir_error
from schemas import RESPUESTA_SIN_CONTEXTO, RespuestaGenerada

from .conftest import escribir, texto_largo


class ModeloConFallas(FakeListChatModel):
    """Modelo falso que primero lanza los errores de `fallas` y después responde normalmente."""

    fallas: list[Exception] = []
    llamadas: int = 0

    def _call(self, *args: Any, **kwargs: Any) -> str:
        self.llamadas += 1
        if self.fallas:
            raise self.fallas.pop(0)
        return super()._call(*args, **kwargs)


def respuesta_json(respuesta: str, encontrada: bool, fragmentos: list[int]) -> str:
    return json.dumps(
        {"respuesta": respuesta, "respuesta_encontrada": encontrada, "fragmentos_usados": fragmentos},
        ensure_ascii=False,
    )


@pytest.fixture
def indexado(settings: RAGSettings, vectorstore):
    escribir(settings, "despliegues.md", texto_largo("despliegues"))
    escribir(settings, "guardias.md", texto_largo("guardias"))
    ingestar(vectorstore, settings)
    return vectorstore


async def test_respuesta_con_referencias_reales(indexado):
    modelo = FakeListChatModel(responses=[respuesta_json("El canary arranca con 5 % del tráfico.", True, [1, 2])])
    pipeline = RAGPipeline(indexado, modelo, top_k=3)

    resultado = await pipeline.get_rag_response("¿Cómo arranca el canary?")

    assert resultado.respuesta_encontrada
    assert resultado.fragmentos_recuperados == 3
    assert [ref.fragmento for ref in resultado.referencias] == [1, 2]
    fragmentos = await pipeline.recuperar("¿Cómo arranca el canary?")
    assert resultado.referencias[0].archivo == fragmentos[0].documento.metadata["source"]
    assert all(ref.chunk_id.startswith(ref.archivo + "::chunk-") for ref in resultado.referencias)


async def test_pregunta_trampa_responde_no_lo_se_sin_fuentes(indexado):
    modelo = FakeListChatModel(responses=[respuesta_json("Los documentos no mencionan vacaciones.", False, [3])])
    pipeline = RAGPipeline(indexado, modelo, top_k=3)

    resultado = await pipeline.get_rag_response("¿Cuántos días de vacaciones hay?")

    assert not resultado.respuesta_encontrada
    assert resultado.respuesta.startswith(RESPUESTA_SIN_CONTEXTO)
    assert resultado.referencias == []


async def test_reintenta_si_la_salida_no_cumple_el_esquema(indexado):
    modelo = FakeListChatModel(
        responses=[
            "esto no es JSON",
            respuesta_json("Respuesta encontrada pero sin citar", True, []),  # incoherente: se rechaza
            respuesta_json("Respuesta válida.", True, [1]),
        ]
    )
    pipeline = RAGPipeline(indexado, modelo, top_k=3)

    resultado = await pipeline.get_rag_response("pregunta")

    assert resultado.respuesta == "Respuesta válida."
    assert [ref.fragmento for ref in resultado.referencias] == [1]


async def test_salida_siempre_invalida_agota_reintentos(indexado):
    modelo = FakeListChatModel(responses=["no es JSON"] * MAX_ATTEMPTS)
    pipeline = RAGPipeline(indexado, modelo, top_k=3)

    with pytest.raises(OutputParserException):
        await pipeline.get_rag_response("pregunta")


async def test_cantidad_de_intentos_configurable(indexado):
    # LLM_MAX_RETRIES=1 -> 2 intentos en total.
    modelo = ModeloConFallas(fallas=[ModelRateLimitError("429 por minuto")] * 2, responses=["{}"])
    pipeline = RAGPipeline(indexado, modelo, top_k=3, intentos=2)

    with pytest.raises(ModelRateLimitError):
        await pipeline.get_rag_response("pregunta")
    assert modelo.llamadas == 2


async def test_rate_limit_del_proveedor_se_reintenta(indexado):
    modelo = ModeloConFallas(
        fallas=[ModelRateLimitError("429 cupo agotado")], responses=[respuesta_json("Dato.", True, [1])]
    )
    pipeline = RAGPipeline(indexado, modelo, top_k=3)

    resultado = await pipeline.get_rag_response("pregunta")

    assert resultado.respuesta == "Dato."
    assert modelo.llamadas == 2


async def test_cuota_diaria_agotada_no_se_reintenta(indexado):
    error = ModelRateLimitError("429 RESOURCE_EXHAUSTED quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier")
    modelo = ModeloConFallas(fallas=[error], responses=[respuesta_json("Dato.", True, [1])])
    pipeline = RAGPipeline(indexado, modelo, top_k=3)

    with pytest.raises(ModelRateLimitError):
        await pipeline.get_rag_response("pregunta")
    assert modelo.llamadas == 1


def test_resumir_error_recorta_el_json_del_proveedor():
    error = ModelRateLimitError("429 RESOURCE_EXHAUSTED " + "{'error': ...} " * 100 + "\nsegunda línea")
    resumen = resumir_error(error)
    assert len(resumen) <= ERROR_MAX_CHARS + 3
    assert resumen.endswith("...")
    assert "segunda línea" not in resumen


async def test_api_key_invalida_no_se_reintenta(indexado):
    modelo = ModeloConFallas(fallas=[ModelAuthenticationError("401 API key inválida")], responses=["{}"])
    pipeline = RAGPipeline(indexado, modelo, top_k=3)

    with pytest.raises(ModelAuthenticationError):
        await pipeline.get_rag_response("pregunta")
    assert modelo.llamadas == 1


async def test_descarta_fragmentos_citados_que_no_existen(indexado):
    modelo = FakeListChatModel(responses=[respuesta_json("Dato.", True, [2, 9])])
    pipeline = RAGPipeline(indexado, modelo, top_k=3)

    resultado = await pipeline.get_rag_response("pregunta")

    assert [ref.fragmento for ref in resultado.referencias] == [2]


async def test_consulta_vacia_es_un_error(indexado):
    pipeline = RAGPipeline(indexado, FakeListChatModel(responses=["{}"]), top_k=3)
    with pytest.raises(ValueError):
        await pipeline.get_rag_response("   ")


def test_esquema_fuerza_no_lo_se_cuando_no_hay_respuesta():
    generada = RespuestaGenerada(respuesta="No figura en los documentos.", respuesta_encontrada=False, fragmentos_usados=[1])
    assert generada.respuesta.startswith(RESPUESTA_SIN_CONTEXTO)
    assert generada.fragmentos_usados == []
