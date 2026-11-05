"""Capa de recuperación + generación grounded (RAG) con LCEL.

Flujo de get_rag_response(query):
    a. búsqueda de similitud en ChromaDB (top_k fragmentos con su distancia)
    b. armado del contexto numerado [1]..[k] con la fuente de cada fragmento
    c. llamada asíncrona a la cadena LCEL: rag_prompt | (llm | PydanticOutputParser).with_retry()
    d. ensamblado de RAGResponse: los números de fragmento citados por el LLM se reemplazan
       por las referencias reales de la metadata
"""

import logging
import time
from dataclasses import dataclass
from functools import cache

from langchain_core.documents import Document
from langchain_core.exceptions import ModelError, ModelRateLimitError, OutputParserException
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable
from langchain_core.vectorstores import VectorStore

from config import rag_settings_from_env
from embeddings import embeddings_settings_from_env, get_embeddings
from llm import get_chat_model, settings_from_env
from prompts import parser, rag_prompt
from schemas import RESPUESTA_SIN_CONTEXTO, RAGResponse, Referencia, RespuestaGenerada
from vectorstore import abrir_vectorstore, contar_chunks

logger = logging.getLogger(__name__)

# Intentos por defecto (1 + LLM_MAX_RETRIES=2). El SDK no reintenta: todos los reintentos son
# de la cadena, así cada uno pasa por es_reintentable y por el rate limiter del modelo.
MAX_ATTEMPTS = 3
# Backoff exponencial con jitter entre intentos: ~4 s y ~8 s, margen para los 503 por alta demanda.
BACKOFF = {"initial": 4, "max": 30, "exp_base": 2, "jitter": 1}
ERROR_MAX_CHARS = 200
LARGO_EXTRACTO = 160


@dataclass(frozen=True)
class FragmentoRecuperado:
    numero: int
    documento: Document
    distancia: float

    def como_contexto(self) -> str:
        meta = self.documento.metadata
        encabezado = f"[{self.numero}] Fuente: {meta.get('source', '?')} — Sección: {meta.get('seccion', '?')}"
        return f"{encabezado}\n{self.documento.page_content}"

    def como_referencia(self) -> Referencia:
        meta = self.documento.metadata
        return Referencia(
            fragmento=self.numero,
            archivo=meta.get("source", "?"),
            titulo=meta.get("titulo", "?"),
            seccion=meta.get("seccion", "?"),
            chunk_id=f"{meta.get('source', '?')}::chunk-{int(meta.get('chunk_index', -1)):03d}",
            distancia=round(self.distancia, 4),
            extracto=" ".join(self.documento.page_content.split())[:LARGO_EXTRACTO],
        )


def resumir_error(exc: BaseException) -> str:
    """Primera línea del error, recortada: los proveedores incluyen el JSON completo de la respuesta."""
    linea = (str(exc).strip().splitlines() or [""])[0]
    return linea if len(linea) <= ERROR_MAX_CHARS else linea[:ERROR_MAX_CHARS] + "..."


def cuota_diaria_agotada(exc: BaseException) -> bool:
    """Un 429 por cupo diario (p. ej. Gemini free tier: 20 requests/día en los modelos Flash) no se
    resuelve esperando unos segundos, a diferencia del límite por minuto. Gemini lo indica con un
    quotaId "...PerDay..." dentro del mensaje."""
    return isinstance(exc, ModelRateLimitError) and "PerDay" in str(exc)


def es_reintentable(exc: BaseException) -> bool:
    """Política de reintento de la cadena (ver tabla de errores en el README).

    - Salida que no cumple el esquema (JSON inválido o incoherente): sí, el modelo puede
      responder bien en el siguiente intento.
    - Cupo diario del proveedor agotado: no, se renueva recién al día siguiente.
    - Otro error del proveedor: solo si es transitorio (rate limit por minuto, timeout, 5xx),
      según `ModelError.is_retryable`. Una API key inválida o un modelo inexistente no se
      arreglan reintentando.
    """
    detalle = resumir_error(exc)
    if isinstance(exc, OutputParserException):
        reintentar = True
    elif cuota_diaria_agotada(exc):
        reintentar = False
        detalle = "cuota diaria del proveedor agotada; se renueva al día siguiente"
    elif isinstance(exc, ModelError):
        reintentar = exc.is_retryable
    else:
        reintentar = False
    logger.warning(
        "Intento fallido (%s: %s) -> %s",
        type(exc).__name__,
        detalle,
        "reintentable" if reintentar else "no reintentable",
    )
    return reintentar


def build_generation_chain(model: BaseChatModel, intentos: int = MAX_ATTEMPTS) -> Runnable[dict[str, str], RespuestaGenerada]:
    """prompt | (modelo | parser) con reintentos ante salidas inválidas o fallos transitorios."""
    # El retry envuelve modelo + parser: un JSON inválido o incoherente (p. ej. respuesta
    # "encontrada" sin fragmentos citados) se vuelve a pedir. El prompt queda afuera porque
    # formatearlo es determinístico.
    return rag_prompt | (model | parser).with_retry(
        retry_if_exception_type=es_reintentable,
        stop_after_attempt=intentos,
        wait_exponential_jitter=True,
        exponential_jitter_params=BACKOFF,
    )


class RAGPipeline:
    """Encapsula retriever + cadena de generación. Recibe sus dependencias para poder probarlo."""

    def __init__(
        self, vectorstore: VectorStore, model: BaseChatModel, top_k: int = 4, intentos: int = MAX_ATTEMPTS
    ) -> None:
        self.vectorstore = vectorstore
        self.top_k = top_k
        self.chain = build_generation_chain(model, intentos)

    async def recuperar(self, query: str) -> list[FragmentoRecuperado]:
        """Convierte la consulta en embedding (mismo modelo que la indexación) y trae los top_k."""
        resultados = await self.vectorstore.asimilarity_search_with_score(query, k=self.top_k)
        return [
            FragmentoRecuperado(numero=i, documento=doc, distancia=float(distancia))
            for i, (doc, distancia) in enumerate(resultados, start=1)
        ]

    async def get_rag_response(self, query: str) -> RAGResponse:
        if not query or not query.strip():
            raise ValueError("la consulta no puede estar vacía")
        query = query.strip()
        inicio = time.perf_counter()

        fragmentos = await self.recuperar(query)
        logger.info(
            "Recuperados %d fragmentos para %r: %s",
            len(fragmentos),
            query[:60],
            ", ".join(f"{f.documento.metadata.get('source')}({f.distancia:.3f})" for f in fragmentos),
        )
        if not fragmentos:
            # Sin contexto no hay nada que fundamentar: se responde sin gastar una llamada al LLM.
            return RAGResponse(
                pregunta=query,
                respuesta=f"{RESPUESTA_SIN_CONTEXTO} La base de conocimiento no devolvió fragmentos.",
                respuesta_encontrada=False,
                fragmentos_recuperados=0,
                latencia_s=round(time.perf_counter() - inicio, 3),
            )

        contexto = "\n\n---\n\n".join(f.como_contexto() for f in fragmentos)
        generada: RespuestaGenerada = await self.chain.ainvoke({"contexto": contexto, "pregunta": query})

        por_numero = {f.numero: f for f in fragmentos}
        invalidos = [n for n in generada.fragmentos_usados if n not in por_numero]
        if invalidos:
            logger.warning("El modelo citó fragmentos inexistentes %s: se descartan", invalidos)
        referencias = [por_numero[n].como_referencia() for n in generada.fragmentos_usados if n in por_numero]

        respuesta = RAGResponse(
            pregunta=query,
            respuesta=generada.respuesta,
            respuesta_encontrada=generada.respuesta_encontrada and bool(referencias),
            referencias=referencias,
            fragmentos_recuperados=len(fragmentos),
            latencia_s=round(time.perf_counter() - inicio, 3),
        )
        logger.info(
            "Respuesta generada en %.2fs (encontrada=%s, fuentes=%s)",
            respuesta.latencia_s,
            respuesta.respuesta_encontrada,
            respuesta.fuentes or "-",
        )
        return respuesta


@cache
def _default_pipeline() -> RAGPipeline:
    settings = rag_settings_from_env()
    emb_settings = embeddings_settings_from_env()
    vectorstore = abrir_vectorstore(settings, get_embeddings(emb_settings), emb_settings.identificador)
    if contar_chunks(vectorstore) == 0:
        raise RuntimeError("la colección está vacía: corré primero  python ingest.py")
    intentos = settings_from_env().max_retries + 1
    return RAGPipeline(vectorstore, get_chat_model(), top_k=settings.top_k, intentos=intentos)


async def get_rag_response(query: str) -> RAGResponse:
    """Punto de entrada pedido por la consigna: consulta -> respuesta validada con referencias."""
    return await _default_pipeline().get_rag_response(query)
