"""Configuración de logs compartida por los scripts del proyecto."""

import logging
import sys


def configurar_logging(nivel: int = logging.INFO) -> None:
    # La consola de Windows no usa UTF-8 por defecto y rompería las tildes.
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    logging.basicConfig(
        level=nivel,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    # Los clientes HTTP y las librerías de ML loguean mucho; se silencian para leer solo el pipeline.
    for ruidoso in (
        "httpx",
        "httpcore",
        "google_genai",
        "openai",
        "anthropic",
        "chromadb",
        "sentence_transformers",
        "urllib3",
        "huggingface_hub",
    ):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
