"""Fixtures compartidas. Los tests no usan red: embeddings y LLM son falsos y deterministas."""

from pathlib import Path

import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding

from config import RAGSettings
from vectorstore import abrir_vectorstore

EMBEDDINGS_ID = "fake:deterministic-32"


@pytest.fixture(autouse=True)
def backoff_instantaneo(monkeypatch: pytest.MonkeyPatch):
    """En los tests no hace falta esperar los ~4 s y ~8 s del backoff real entre reintentos."""
    monkeypatch.setattr("rag_chain.BACKOFF", {"initial": 0.01, "max": 0.01, "exp_base": 1, "jitter": 0})


@pytest.fixture
def settings(tmp_path: Path) -> RAGSettings:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return RAGSettings(data_dir=data_dir, persist_dir=tmp_path / "vectorstore", collection_name="tests_rag")


@pytest.fixture
def embeddings() -> DeterministicFakeEmbedding:
    return DeterministicFakeEmbedding(size=32)


@pytest.fixture
def vectorstore(settings: RAGSettings, embeddings: DeterministicFakeEmbedding):
    return abrir_vectorstore(settings, embeddings, EMBEDDINGS_ID)


def escribir(settings: RAGSettings, nombre: str, contenido: str) -> Path:
    ruta = settings.data_dir / nombre
    ruta.write_text(contenido, encoding="utf-8")
    return ruta


def texto_largo(tema: str, parrafos: int = 12) -> str:
    """Documento Markdown de varios cientos de tokens para forzar más de un chunk."""
    cuerpo = "\n\n".join(
        f"## Sección {i} de {tema}\n\n"
        + " ".join(f"El punto {i}.{j} de {tema} describe un procedimiento operativo detallado." for j in range(12))
        for i in range(parrafos)
    )
    return f"# Documento sobre {tema}\n\n{cuerpo}\n"
