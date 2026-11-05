"""Configuración del sistema RAG: rutas, chunking y recuperación.

Los valores se leen de variables de entorno (ver .env.example) y se validan con Pydantic,
igual que la configuración del LLM. Las restricciones reflejan la consigna: chunks de al
menos 500 tokens con al menos 50 de overlap, y un top_k entre 3 y 5 para no caer en el
"contexto infinito".
"""

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

BASE_DIR = Path(__file__).resolve().parent


class RAGSettings(BaseModel):
    data_dir: Path = BASE_DIR / "data"
    persist_dir: Path = BASE_DIR / "vectorstore"
    collection_name: str = Field(default="orbita_handbook", pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{1,61}[a-zA-Z0-9]$")
    chunk_size: int = Field(default=500, ge=500, le=2000, description="Tamaño máximo de cada chunk, en tokens")
    chunk_overlap: int = Field(default=50, ge=50, description="Tokens compartidos entre chunks consecutivos")
    # cl100k_base es el tokenizador de los modelos de OpenAI: sirve como medida estándar de tokens.
    token_encoding: str = "cl100k_base"
    top_k: int = Field(default=4, ge=3, le=5, description="Fragmentos recuperados que se pasan al LLM")

    @field_validator("data_dir", "persist_dir")
    @classmethod
    def resolver_ruta(cls, ruta: Path) -> Path:
        """Las rutas relativas se interpretan desde la carpeta del proyecto, no desde el cwd."""
        return ruta if ruta.is_absolute() else BASE_DIR / ruta

    @model_validator(mode="after")
    def validar_overlap(self) -> "RAGSettings":
        if self.chunk_overlap >= self.chunk_size // 2:
            raise ValueError("chunk_overlap tiene que ser menor que la mitad de chunk_size")
        return self


def rag_settings_from_env(**overrides: Any) -> RAGSettings:
    """Arma RAGSettings desde variables de entorno; `overrides` tiene prioridad."""
    data: dict[str, Any] = {
        "data_dir": os.getenv("RAG_DATA_DIR"),
        "persist_dir": os.getenv("RAG_PERSIST_DIR"),
        "collection_name": os.getenv("RAG_COLLECTION"),
        "chunk_size": os.getenv("RAG_CHUNK_SIZE"),
        "chunk_overlap": os.getenv("RAG_CHUNK_OVERLAP"),
        "top_k": os.getenv("RAG_TOP_K"),
    }
    data = {k: v for k, v in data.items() if v not in (None, "")}
    data.update(overrides)
    return RAGSettings.model_validate(data)
