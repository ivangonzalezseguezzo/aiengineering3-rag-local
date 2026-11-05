"""Pruebas de punta a punta del sistema RAG (requieren un .env con la API key del LLM).

1. Una pregunta cuya respuesta está en los documentos: debe responder citando la fuente correcta.
2. Una pregunta trampa cuya respuesta NO está: debe decir "No lo sé" y no citar ninguna fuente.

La pregunta trampa es deliberadamente cercana al corpus (habla de guardias, que sí están
documentadas, pero pregunta por vacaciones, que no): el retriever trae fragmentos parecidos
y lo que se prueba es que el modelo no complete el hueco con conocimiento general.

Antes de preguntar se sincroniza el índice (ingesta incremental: si nada cambió, no
reindexa). Las dos consultas corren en paralelo con asyncio.gather y el resultado queda en
evidencias/resultado_pruebas.json.

Uso:  python pruebas_rag.py
"""

import asyncio
import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.exceptions import ModelError, OutputParserException
from pydantic import ValidationError

from config import BASE_DIR, rag_settings_from_env
from embeddings import embeddings_settings_from_env, get_embeddings
from ingest import ingestar
from llm import get_chat_model, settings_from_env
from logging_setup import configurar_logging
from rag_chain import RAGPipeline, resumir_error
from schemas import RESPUESTA_SIN_CONTEXTO, RAGResponse
from vectorstore import abrir_vectorstore

logger = logging.getLogger(__name__)

ARCHIVO_EVIDENCIA = BASE_DIR / "evidencias" / "resultado_pruebas.json"


@dataclass(frozen=True)
class CasoPrueba:
    nombre: str
    pregunta: str
    espera_respuesta: bool
    fuente_esperada: str | None = None


CASOS = (
    CasoPrueba(
        nombre="Pregunta con respuesta en los documentos",
        pregunta="¿Qué porcentaje del tráfico recibe un despliegue en la primera etapa del canary y durante cuánto tiempo?",
        espera_respuesta=True,
        fuente_esperada="politica_despliegues.md",
    ),
    CasoPrueba(
        nombre="Pregunta trampa (fuera de los documentos)",
        pregunta="¿Cuántos días de vacaciones anuales les corresponden a los ingenieros que hacen guardias?",
        espera_respuesta=False,
    ),
)


def verificar(caso: CasoPrueba, respuesta: RAGResponse) -> dict[str, bool]:
    if caso.espera_respuesta:
        return {
            "respuesta_encontrada": respuesta.respuesta_encontrada,
            "cita_la_fuente_esperada": caso.fuente_esperada in respuesta.fuentes,
            "no_dice_no_lo_se": not respuesta.respuesta.startswith(RESPUESTA_SIN_CONTEXTO),
        }
    return {
        "respuesta_no_encontrada": not respuesta.respuesta_encontrada,
        "responde_no_lo_se": respuesta.respuesta.startswith(RESPUESTA_SIN_CONTEXTO),
        "sin_referencias": not respuesta.referencias,
    }


async def main() -> int:
    settings = rag_settings_from_env()
    emb_settings = embeddings_settings_from_env()
    llm_settings = settings_from_env()

    vectorstore = abrir_vectorstore(settings, get_embeddings(emb_settings), emb_settings.identificador)
    resumen = ingestar(vectorstore, settings)
    logger.info("Índice sincronizado: %d chunks (cambios: %s)", resumen.total_chunks, resumen.hubo_cambios)

    pipeline = RAGPipeline(vectorstore, get_chat_model(), top_k=settings.top_k, intentos=llm_settings.max_retries + 1)
    respuestas = await asyncio.gather(*(pipeline.get_rag_response(c.pregunta) for c in CASOS))

    resultados = []
    for caso, respuesta in zip(CASOS, respuestas):
        checks = verificar(caso, respuesta)
        aprobado = all(checks.values())
        resultados.append(
            {"caso": caso.nombre, "aprobado": aprobado, "verificaciones": checks, "salida": respuesta.model_dump()}
        )
        print(f"\n=== {caso.nombre} ===")
        print(respuesta.model_dump_json(indent=2))
        for nombre, ok in checks.items():
            print(f"  [{'OK' if ok else 'FALLA'}] {nombre}")

    ARCHIVO_EVIDENCIA.parent.mkdir(parents=True, exist_ok=True)
    evidencia = {
        "fecha": datetime.now().isoformat(timespec="seconds"),
        "llm": f"{llm_settings.provider}:{llm_settings.model}",
        "embeddings": emb_settings.identificador,
        "top_k": settings.top_k,
        "chunking": {"chunk_size": settings.chunk_size, "chunk_overlap": settings.chunk_overlap},
        "casos": resultados,
    }
    ARCHIVO_EVIDENCIA.write_text(json.dumps(evidencia, ensure_ascii=False, indent=2), encoding="utf-8")

    aprobados = sum(r["aprobado"] for r in resultados)
    print(f"\nResumen: {aprobados}/{len(resultados)} pruebas aprobadas. Evidencia en {Path(ARCHIVO_EVIDENCIA).relative_to(BASE_DIR)}")
    return 0 if aprobados == len(resultados) else 1


if __name__ == "__main__":
    load_dotenv()
    configurar_logging()
    try:
        settings_from_env()
        embeddings_settings_from_env()
        rag_settings_from_env()
    except ValidationError as exc:
        sys.exit("Configuración inválida: " + exc.errors()[0]["msg"])
    try:
        sys.exit(asyncio.run(main()))
    except (ModelError, OutputParserException, RuntimeError) as exc:
        # Errores esperables (cupo agotado, API key inválida, índice incompatible): mensaje claro, sin traceback.
        sys.exit(f"No se pudieron completar las pruebas ({type(exc).__name__}): {resumir_error(exc)}")
