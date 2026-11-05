"""Módulo de ingesta: /data -> limpieza -> chunking por tokens -> ChromaDB persistente.

La ingesta es incremental: cada chunk guarda el hash del archivo de origen, así que en las
corridas siguientes solo se reindexan los archivos nuevos o modificados y se borran los
chunks de archivos eliminados. Correrla dos veces seguidas no duplica nada ni recalcula
embeddings.

Uso:
    python ingest.py            # ingesta incremental
    python ingest.py --rebuild  # borra la colección y reindexa todo (p. ej. al cambiar de modelo de embeddings)
"""

import argparse
import hashlib
import logging
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import tiktoken
from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pydantic import ValidationError

from config import RAGSettings, rag_settings_from_env
from embeddings import embeddings_settings_from_env, get_embeddings
from logging_setup import configurar_logging
from vectorstore import abrir_vectorstore, borrar_coleccion, contar_chunks

logger = logging.getLogger(__name__)

EXTENSIONES_SOPORTADAS = (".md", ".txt")

# Caracteres de control, de ancho cero y basura típica de PDFs convertidos (conserva \n y \t).
_CARACTERES_BASURA = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‍﻿■□▪●]")
_ENCABEZADO_MD = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$", re.MULTILINE)

# Jerarquía del splitter: fin de párrafo u oración > línea > palabra > carácter.
# El fin de oración exige que antes del signo no haya un dígito, para no separar "1." de su ítem.
SEPARADORES = [r"\n{2,}|(?<=\D[.!?:;])[ \t\n]+", r"\n", r" ", r""]


@dataclass(frozen=True)
class DocumentoFuente:
    archivo: str
    titulo: str
    texto: str  # ya limpio
    content_hash: str


@dataclass
class ResumenIngesta:
    nuevos: list[str] = field(default_factory=list)
    actualizados: list[str] = field(default_factory=list)
    sin_cambios: list[str] = field(default_factory=list)
    eliminados: list[str] = field(default_factory=list)
    chunks_agregados: int = 0
    chunks_borrados: int = 0
    total_chunks: int = 0

    @property
    def hubo_cambios(self) -> bool:
        return bool(self.nuevos or self.actualizados or self.eliminados)


def limpiar_texto(texto: str) -> str:
    """Normaliza el texto sin destruir la estructura que usa el splitter (párrafos y encabezados)."""
    texto = unicodedata.normalize("NFC", texto)
    texto = texto.replace("\r\n", "\n").replace("\r", "\n")
    texto = _CARACTERES_BASURA.sub("", texto)
    texto = re.sub(r"[ \t]+\n", "\n", texto)  # espacios al final de línea
    texto = re.sub(r"(?<=\S)[ \t]{2,}", " ", texto)  # espacios repetidos dentro de una línea
    texto = re.sub(r"\n{3,}", "\n\n", texto)  # como máximo una línea en blanco entre párrafos
    return texto.strip()


def _titulo(texto: str, archivo: Path) -> str:
    match = _ENCABEZADO_MD.search(texto)
    return match.group(2) if match and len(match.group(1)) == 1 else archivo.stem.replace("_", " ").capitalize()


def _hash(texto: str, settings: RAGSettings) -> str:
    # Incluye la configuración de chunking: si cambia el tamaño de chunk, el archivo se reindexa.
    firma = f"{settings.chunk_size}:{settings.chunk_overlap}:{settings.token_encoding}\n{texto}"
    return hashlib.sha256(firma.encode("utf-8")).hexdigest()[:16]


def cargar_documentos(settings: RAGSettings) -> list[DocumentoFuente]:
    """Lee los .md/.txt de data_dir, los limpia y descarta los vacíos."""
    if not settings.data_dir.is_dir():
        raise FileNotFoundError(f"no existe la carpeta de documentos: {settings.data_dir}")

    documentos: list[DocumentoFuente] = []
    for ruta in sorted(settings.data_dir.iterdir()):
        if ruta.suffix.lower() not in EXTENSIONES_SOPORTADAS or not ruta.is_file():
            continue
        texto = limpiar_texto(ruta.read_text(encoding="utf-8"))
        if not texto:
            logger.warning("Se omite %s: está vacío", ruta.name)
            continue
        documentos.append(DocumentoFuente(ruta.name, _titulo(texto, ruta), texto, _hash(texto, settings)))
    return documentos


def crear_splitter(settings: RAGSettings) -> RecursiveCharacterTextSplitter:
    """Splitter recursivo que mide en tokens y nunca corta una oración por la mitad.

    El splitter parte el texto con el primer separador y después junta las piezas hasta
    chunk_size; el overlap se arma repitiendo piezas finales que sumen como máximo
    chunk_overlap tokens. Por eso la pieza mínima es la oración: si fuera la sección o el
    párrafo (más de 50 tokens), el overlap quedaría vacío.
    """
    return RecursiveCharacterTextSplitter.from_tiktoken_encoder(
        encoding_name=settings.token_encoding,
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        separators=SEPARADORES,
        is_separator_regex=True,
    )


def _posiciones(texto: str, chunks: list[str]) -> list[int]:
    """Posición de cada chunk en el texto original.

    No se usa `add_start_index` del splitter porque, cuando el overlap se mide en tokens,
    lo resta como si fueran caracteres y devuelve -1 para los chunks que solapan.
    """
    posiciones: list[int] = []
    desde = 0
    for chunk in chunks:
        pos = texto.find(chunk, desde)
        posiciones.append(pos)
        desde = pos + 1
    return posiciones


def _secciones(texto: str) -> list[tuple[int, str]]:
    """Posición de inicio y título de cada encabezado del documento."""
    return [(m.start(), m.group(2)) for m in _ENCABEZADO_MD.finditer(texto)]


def _seccion_dominante(secciones: list[tuple[int, str]], inicio: int, fin: int, por_defecto: str) -> str:
    """La sección que más caracteres aporta al chunk [inicio, fin)."""
    mejor, mejor_solape = por_defecto, 0
    for i, (pos, titulo) in enumerate(secciones):
        fin_seccion = secciones[i + 1][0] if i + 1 < len(secciones) else fin
        solape = min(fin, fin_seccion) - max(inicio, pos)
        if solape > mejor_solape:
            mejor, mejor_solape = titulo, solape
    return mejor


def fragmentar(documento: DocumentoFuente, settings: RAGSettings) -> tuple[list[Document], list[str]]:
    """Devuelve los chunks del documento con su metadata y sus ids deterministas."""
    splitter = crear_splitter(settings)
    tokenizer = tiktoken.get_encoding(settings.token_encoding)
    secciones = _secciones(documento.texto)
    textos = splitter.split_text(documento.texto)

    chunks: list[Document] = []
    ids: list[str] = []
    for indice, (texto, inicio) in enumerate(zip(textos, _posiciones(documento.texto, textos))):
        metadata = {
            "source": documento.archivo,
            "titulo": documento.titulo,
            "seccion": _seccion_dominante(secciones, inicio, inicio + len(texto), documento.titulo),
            "chunk_index": indice,
            "n_tokens": len(tokenizer.encode(texto)),
            "start_index": inicio,
            "content_hash": documento.content_hash,
        }
        chunks.append(Document(page_content=texto, metadata=metadata))
        # Id determinista: reindexar el mismo archivo pisa sus chunks en lugar de duplicarlos.
        ids.append(f"{documento.archivo}::chunk-{indice:03d}")
    return chunks, ids


def _estado_indexado(vectorstore: Chroma) -> dict[str, tuple[str, list[str]]]:
    """{archivo: (hash, ids)} de lo que ya está en la colección."""
    actual = vectorstore.get(include=["metadatas"])
    estado: dict[str, tuple[str, list[str]]] = {}
    for chunk_id, metadata in zip(actual["ids"], actual["metadatas"]):
        archivo = metadata.get("source", "?")
        _, ids = estado.setdefault(archivo, (metadata.get("content_hash", ""), []))
        ids.append(chunk_id)
    return estado


def ingestar(vectorstore: Chroma, settings: RAGSettings) -> ResumenIngesta:
    """Sincroniza la colección con el contenido de data_dir."""
    documentos = cargar_documentos(settings)
    if not documentos:
        raise FileNotFoundError(f"no hay archivos {EXTENSIONES_SOPORTADAS} en {settings.data_dir}")

    indexado = _estado_indexado(vectorstore)
    resumen = ResumenIngesta()

    for documento in documentos:
        hash_previo, ids_previos = indexado.pop(documento.archivo, (None, []))
        if hash_previo == documento.content_hash:
            resumen.sin_cambios.append(documento.archivo)
            continue

        if ids_previos:
            vectorstore.delete(ids=ids_previos)
            resumen.chunks_borrados += len(ids_previos)
            resumen.actualizados.append(documento.archivo)
        else:
            resumen.nuevos.append(documento.archivo)

        chunks, ids = fragmentar(documento, settings)
        vectorstore.add_documents(chunks, ids=ids)
        resumen.chunks_agregados += len(chunks)
        logger.info(
            "Indexado %s: %d chunks (%s tokens)",
            documento.archivo,
            len(chunks),
            ", ".join(str(c.metadata["n_tokens"]) for c in chunks),
        )

    # Lo que queda en `indexado` son archivos que ya no existen en data_dir.
    for archivo, (_, ids) in indexado.items():
        vectorstore.delete(ids=ids)
        resumen.chunks_borrados += len(ids)
        resumen.eliminados.append(archivo)
        logger.info("Eliminados %d chunks de %s (el archivo ya no está en data/)", len(ids), archivo)

    resumen.total_chunks = contar_chunks(vectorstore)
    return resumen


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingesta de /data en ChromaDB")
    parser.add_argument("--rebuild", action="store_true", help="borra la colección y reindexa todo")
    args = parser.parse_args()

    load_dotenv()
    configurar_logging()
    try:
        settings = rag_settings_from_env()
        emb_settings = embeddings_settings_from_env()
    except ValidationError as exc:
        sys.exit("Configuración inválida: " + exc.errors()[0]["msg"])

    if args.rebuild:
        borrar_coleccion(settings)

    logger.info(
        "Embeddings: %s | chunk_size=%d overlap=%d",
        emb_settings.identificador,
        settings.chunk_size,
        settings.chunk_overlap,
    )
    vectorstore = abrir_vectorstore(settings, get_embeddings(emb_settings), emb_settings.identificador)
    resumen = ingestar(vectorstore, settings)

    print("\n=== Resumen de la ingesta ===")
    print("Nuevos:        " + (", ".join(resumen.nuevos) or "-"))
    print("Actualizados:  " + (", ".join(resumen.actualizados) or "-"))
    print("Sin cambios:   " + (", ".join(resumen.sin_cambios) or "-"))
    print("Eliminados:    " + (", ".join(resumen.eliminados) or "-"))
    print(f"Chunks agregados: {resumen.chunks_agregados} | borrados: {resumen.chunks_borrados}")
    print(f"Total en la colección '{settings.collection_name}': {resumen.total_chunks}")
    if not resumen.hubo_cambios:
        print("La colección ya estaba al día: no se recalculó ningún embedding.")


if __name__ == "__main__":
    main()
