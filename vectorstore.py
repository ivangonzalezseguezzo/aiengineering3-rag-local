"""Acceso a la colección persistente de ChromaDB.

Centraliza dos garantías que el resto del código da por sentadas:
1. Persistencia: el cliente es un PersistentClient apuntando a `persist_dir`, así que el
   índice sobrevive entre ejecuciones y no se reconstruye en cada corrida.
2. Consistencia de embeddings: la colección guarda en su metadata con qué modelo se indexó
   y se niega a abrirse con otro. Indexar con un modelo y consultar con otro devuelve ruido.
"""

import logging

import chromadb
from chromadb.config import Settings as ChromaClientSettings
from langchain_chroma import Chroma
from langchain_core.embeddings import Embeddings

from config import RAGSettings

logger = logging.getLogger(__name__)

METADATA_EMBEDDINGS = "embeddings_model"


class EmbeddingsMismatchError(RuntimeError):
    """La colección se indexó con un modelo de embeddings distinto al configurado."""


def _cliente(settings: RAGSettings) -> chromadb.ClientAPI:
    settings.persist_dir.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(
        path=str(settings.persist_dir), settings=ChromaClientSettings(anonymized_telemetry=False)
    )


def _metadata_coleccion(client: chromadb.ClientAPI, nombre: str) -> dict | None:
    """Metadata de la colección si existe, o None si todavía no se creó."""
    try:
        return client.get_collection(nombre).metadata or {}
    except Exception:  # chromadb lanza NotFoundError o ValueError según la versión
        return None


def abrir_vectorstore(settings: RAGSettings, embeddings: Embeddings, embeddings_id: str) -> Chroma:
    """Abre (o crea) la colección verificando que coincida el modelo de embeddings."""
    client = _cliente(settings)
    metadata = _metadata_coleccion(client, settings.collection_name)
    if metadata is not None:
        indexado_con = metadata.get(METADATA_EMBEDDINGS)
        if indexado_con and indexado_con != embeddings_id:
            raise EmbeddingsMismatchError(
                f"la colección '{settings.collection_name}' se indexó con '{indexado_con}' pero el entorno "
                f"configura '{embeddings_id}'. Volvé a indexar con: python ingest.py --rebuild"
            )
    else:
        logger.info("Creando la colección '%s' en %s", settings.collection_name, settings.persist_dir)

    return Chroma(
        client=client,
        collection_name=settings.collection_name,
        embedding_function=embeddings,
        # Distancia coseno (1 - similitud): es la métrica para la que se entrenan los modelos de embeddings.
        collection_metadata={"hnsw:space": "cosine", METADATA_EMBEDDINGS: embeddings_id},
    )


def borrar_coleccion(settings: RAGSettings) -> bool:
    """Elimina la colección completa. Devuelve True si existía."""
    client = _cliente(settings)
    if _metadata_coleccion(client, settings.collection_name) is None:
        return False
    client.delete_collection(settings.collection_name)
    logger.info("Colección '%s' eliminada", settings.collection_name)
    return True


def contar_chunks(vectorstore: Chroma) -> int:
    return vectorstore._collection.count()
