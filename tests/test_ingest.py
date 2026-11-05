import pytest

from config import RAGSettings
from ingest import cargar_documentos, fragmentar, ingestar, limpiar_texto
from vectorstore import EmbeddingsMismatchError, abrir_vectorstore, contar_chunks

from .conftest import EMBEDDINGS_ID, escribir, texto_largo


def test_limpiar_texto_quita_ruido_y_conserva_parrafos():
    sucio = "Título■■\r\n\r\n\r\n\r\nLínea   con    espacios   \nOtra​ línea\n\n\n\nFin"
    limpio = limpiar_texto(sucio)
    assert "■" not in limpio and "​" not in limpio
    assert "\n\n\n" not in limpio
    assert "Línea con espacios\nOtra línea" in limpio
    assert limpio.endswith("Fin")


def test_corpus_real_respeta_limite_de_tokens():
    """Los documentos de data/ se parten en chunks de como máximo chunk_size tokens."""
    settings = RAGSettings()
    documentos = cargar_documentos(settings)
    assert len(documentos) >= 3

    total_chunks, bordes, bordes_con_overlap = 0, 0, 0
    for documento in documentos:
        chunks, ids = fragmentar(documento, settings)
        total_chunks += len(chunks)
        assert len(ids) == len(set(ids)) == len(chunks)
        for chunk in chunks:
            assert chunk.metadata["n_tokens"] <= settings.chunk_size
            assert chunk.metadata["source"] == documento.archivo
            assert chunk.metadata["seccion"]
        for previo, siguiente in zip(chunks, chunks[1:]):
            bordes += 1
            bordes_con_overlap += siguiente.metadata["start_index"] < previo.metadata["start_index"] + len(
                previo.page_content
            )
    assert total_chunks > len(documentos), "algún documento debería generar más de un chunk"
    # Solo no hay overlap cuando la última oración de un chunk supera por sí sola los 50 tokens.
    assert bordes_con_overlap >= bordes * 0.7


def test_fragmentar_es_determinista_y_solapa(settings: RAGSettings):
    escribir(settings, "largo.md", texto_largo("despliegues", parrafos=20))
    [documento] = cargar_documentos(settings)

    chunks_1, ids_1 = fragmentar(documento, settings)
    chunks_2, ids_2 = fragmentar(documento, settings)
    assert ids_1 == ids_2 and [c.page_content for c in chunks_1] == [c.page_content for c in chunks_2]
    assert len(chunks_1) >= 3

    # Con overlap, el inicio de cada chunk cae antes del final del anterior.
    for previo, siguiente in zip(chunks_1, chunks_1[1:]):
        fin_previo = previo.metadata["start_index"] + len(previo.page_content)
        assert siguiente.metadata["start_index"] < fin_previo


def test_ingesta_incremental(settings: RAGSettings, vectorstore):
    escribir(settings, "a.md", texto_largo("alertas"))
    escribir(settings, "b.md", texto_largo("backups"))

    primera = ingestar(vectorstore, settings)
    assert sorted(primera.nuevos) == ["a.md", "b.md"]
    total = primera.total_chunks
    assert total == primera.chunks_agregados > 2

    # Segunda corrida sin cambios: no se reindexa nada.
    segunda = ingestar(vectorstore, settings)
    assert not segunda.hubo_cambios
    assert sorted(segunda.sin_cambios) == ["a.md", "b.md"]
    assert contar_chunks(vectorstore) == total

    # Se modifica un archivo y se borra el otro.
    escribir(settings, "a.md", texto_largo("alertas actualizadas", parrafos=4))
    (settings.data_dir / "b.md").unlink()
    tercera = ingestar(vectorstore, settings)
    assert tercera.actualizados == ["a.md"]
    assert tercera.eliminados == ["b.md"]
    fuentes = {m["source"] for m in vectorstore.get(include=["metadatas"])["metadatas"]}
    assert fuentes == {"a.md"}


def test_el_indice_persiste_en_disco(settings: RAGSettings, embeddings, vectorstore):
    escribir(settings, "a.md", texto_largo("persistencia"))
    total = ingestar(vectorstore, settings).total_chunks

    reabierto = abrir_vectorstore(settings, embeddings, EMBEDDINGS_ID)
    assert contar_chunks(reabierto) == total


def test_rechaza_abrir_con_otro_modelo_de_embeddings(settings: RAGSettings, embeddings, vectorstore):
    escribir(settings, "a.md", texto_largo("embeddings"))
    ingestar(vectorstore, settings)

    with pytest.raises(EmbeddingsMismatchError):
        abrir_vectorstore(settings, embeddings, "otro:modelo")
