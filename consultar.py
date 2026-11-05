"""Consulta el sistema RAG desde la terminal.

Uso:
    python consultar.py "¿Quién recibe la página si el primario no confirma?"   # una sola pregunta
    python consultar.py                                                          # modo interactivo
"""

import asyncio
import sys

from dotenv import load_dotenv
from langchain_core.exceptions import ModelError, OutputParserException
from pydantic import ValidationError

from config import rag_settings_from_env
from embeddings import embeddings_settings_from_env
from llm import settings_from_env
from logging_setup import configurar_logging
from rag_chain import get_rag_response, resumir_error
from schemas import RAGResponse

SALIDAS = {"", "salir", "exit", "quit"}


def mostrar(respuesta: RAGResponse) -> None:
    print("\nRespuesta: " + respuesta.respuesta)
    if respuesta.referencias:
        print("Fuentes:")
        for ref in respuesta.referencias:
            print(f"  [{ref.fragmento}] {ref.archivo} › {ref.seccion} (distancia {ref.distancia:.3f})")
    print(f"({respuesta.fragmentos_recuperados} fragmentos recuperados, {respuesta.latencia_s:.2f}s)")


async def preguntar(pregunta: str) -> bool:
    """Muestra la respuesta o, ante un error del proveedor o del parseo, un mensaje claro sin traceback."""
    try:
        mostrar(await get_rag_response(pregunta))
        return True
    except (ModelError, OutputParserException) as exc:
        reintentar = isinstance(exc, ModelError) and exc.is_retryable
        print(f"\nError ({type(exc).__name__}): {resumir_error(exc)}")
        print("Es transitorio: probá de nuevo más tarde." if reintentar else "Revisá la configuración del .env.")
        return False


async def interactivo() -> None:
    print("Consultá la documentación de ingeniería de Órbita Pagos (Enter vacío o 'salir' para terminar).")
    while True:
        # input() es bloqueante: se ejecuta en un hilo para no trabar el event loop.
        pregunta = (await asyncio.to_thread(input, "\nPregunta: ")).strip()
        if pregunta.lower() in SALIDAS:
            break
        await preguntar(pregunta)  # un error no corta la sesión


async def main(argumentos: list[str]) -> int:
    if argumentos:
        return 0 if await preguntar(" ".join(argumentos)) else 1
    await interactivo()
    return 0


if __name__ == "__main__":
    load_dotenv()
    configurar_logging()
    try:
        settings_from_env()
        embeddings_settings_from_env()
        rag_settings_from_env()  # p. ej. RAG_TOP_K fuera de 3-5: mensaje claro en vez de traceback
    except ValidationError as exc:
        sys.exit("Configuración inválida: " + exc.errors()[0]["msg"])
    try:
        sys.exit(asyncio.run(main(sys.argv[1:])))
    except RuntimeError as exc:  # colección vacía o indexada con otro modelo de embeddings
        sys.exit(f"Error: {exc}")
    except (KeyboardInterrupt, EOFError):
        pass
