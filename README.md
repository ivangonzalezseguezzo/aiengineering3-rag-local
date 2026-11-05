# Sistema de Recuperación Semántica Local (RAG)

Pre-entrega 3 de **AI Engineering (Coderhouse)**: un flujo RAG de punta a punta con **ChromaDB** como memoria vectorial local, **LangChain (LCEL)** para la generación y **Pydantic** para validar la salida.

El sistema responde preguntas sobre la documentación interna de ingeniería de *Órbita Pagos*, una fintech ficticia: gestión de incidentes, despliegues, guardias y estándares de APIs. Responde **solo** con lo que está en los documentos. Si la respuesta no está, dice **"No lo sé."** y no cita ninguna fuente.

## Ejemplo

```python
resultado = await get_rag_response("¿Qué porcentaje del tráfico recibe un despliegue en la primera etapa del canary?")
print(resultado.model_dump_json(indent=2))
```

Salida real (`RAGResponse`) tomada de [`evidencias/resultado_pruebas.json`](evidencias/resultado_pruebas.json), generada con el modelo por defecto, `gemini-3.5-flash-lite`:

```json
{
  "pregunta": "¿Qué porcentaje del tráfico recibe un despliegue en la primera etapa del canary y durante cuánto tiempo?",
  "respuesta": "En la primera etapa del canary, el despliegue recibe un 5 % del tráfico durante un tiempo mínimo de 15 minutos.",
  "respuesta_encontrada": true,
  "referencias": [
    {
      "fragmento": 1,
      "archivo": "politica_despliegues.md",
      "titulo": "Política de despliegues a producción",
      "seccion": "Despliegue progresivo (canary)",
      "chunk_id": "politica_despliegues.md::chunk-001",
      "distancia": 0.1461,
      "extracto": "5. El cambio está asociado a un ticket con la descripción del riesgo y el plan de rollback. ## Despliegue progresivo (canary) Ningún servicio tier 1 se desplieg"
    }
  ],
  "fragmentos_recuperados": 4,
  "latencia_s": 18.311
}
```

Y la pregunta trampa:

```json
{
  "pregunta": "¿Cuántos días de vacaciones anuales les corresponden a los ingenieros que hacen guardias?",
  "respuesta": "No lo sé. La documentación provista no contiene información sobre la cantidad de días de vacaciones anuales que les corresponden a los ingenieros.",
  "respuesta_encontrada": false,
  "referencias": [],
  "fragmentos_recuperados": 4,
  "latencia_s": 12.221
}
```

## Arquitectura

```
                       INGESTA (python ingest.py)
 data/*.md ──► limpiar_texto ──► splitter por tokens ──► embeddings ──► ChromaDB persistente
               (ruido, espacios,   (500 tokens, overlap 50,   (mismo modelo     (./vectorstore,
                saltos repetidos)   corta en oraciones)        siempre)          distancia coseno)

                       CONSULTA (get_rag_response)
 pregunta ──► embedding ──► top-4 de ChromaDB ──► contexto numerado [1]..[4]
                                                        │
              rag_prompt | (LLM | PydanticOutputParser).with_retry(3)
                                                        │
                        RespuestaGenerada {respuesta, respuesta_encontrada, fragmentos_usados}
                                                        │
              los números citados se reemplazan por la metadata real de cada fragmento
                                                        ▼
                     RAGResponse {respuesta, referencias[archivo, sección, chunk, distancia]}
```

## Estructura

```
├── data/                   # Dataset de ejemplo: 4 documentos .md (≈1.300 tokens cada uno)
├── config.py               # RAGSettings: rutas, chunking y top_k, validados con Pydantic
├── embeddings.py           # Fábrica de embeddings (HuggingFace local / Gemini / OpenAI)
├── vectorstore.py          # Apertura de ChromaDB persistente + control del modelo de embeddings
├── ingest.py               # Script de ingesta incremental: limpieza, chunking y upsert
├── schemas.py              # Contratos Pydantic: RespuestaGenerada, Referencia, RAGResponse
├── prompts.py              # Prompt grounded ("filtro de veracidad") + PydanticOutputParser
├── llm.py                  # Fábrica del chat model (Gemini / OpenAI / Anthropic), igual que en la PE2
├── rag_chain.py            # RAGPipeline y get_rag_response(query) asíncrona
├── pruebas_rag.py          # Las dos pruebas pedidas (pregunta en contexto + pregunta trampa)
├── consultar.py            # CLI para hacer preguntas (una o en modo interactivo)
├── logging_setup.py        # Configuración de logs compartida
├── tests/                  # Tests offline con pytest (sin API keys ni red)
├── evidencias/             # resultado_pruebas.json generado por pruebas_rag.py
├── vectorstore/            # Índice de ChromaDB: NO se sube (.gitignore), se regenera con python ingest.py
├── requirements.txt / requirements-dev.txt
└── .env.example            # Plantilla del .env (el .env real tampoco se sube)
```

## Decisiones de diseño

### Dataset
Son cuatro documentos Markdown sobre un mismo tema: el handbook de ingeniería de una fintech ficticia. Tienen datos concretos y verificables (porcentajes, plazos, nombres de herramientas). Así se puede comprobar si una respuesta salió del contexto o si el modelo la inventó.

### Chunking
- **Se mide en tokens, no en caracteres**: `RecursiveCharacterTextSplitter.from_tiktoken_encoder` con `cl100k_base`, `chunk_size=500` y `chunk_overlap=50`, que es el mínimo que pide la consigna. `RAGSettings` rechaza valores menores.
- **La pieza mínima es la oración.** El splitter corta primero en fin de párrafo o de oración, después en línea y por último en palabra. Esto importa por cómo funciona el overlap: el splitter solo repite piezas finales que entren en 50 tokens. Si cortara por sección o por párrafo (más de 50 tokens cada uno), el overlap quedaría vacío. Cortando en oraciones, cada chunk repite al comienzo 1 o 2 oraciones completas del anterior. El test `test_corpus_real_respeta_limite_de_tokens` lo verifica.
- **Limpieza previa**: normaliza Unicode, saca caracteres de control y basura de PDFs (`■`, espacios de ancho cero) y colapsa espacios y saltos repetidos. Conserva los saltos de párrafo, porque el splitter los usa para cortar.
- **Metadata por chunk**: archivo, título del documento, sección dominante (el encabezado que más texto aporta al chunk), índice, cantidad de tokens, posición en el texto y hash del archivo.
- Resultado sobre el dataset: 12 chunks de entre 185 y 491 tokens (3 por documento).

### Embeddings
Por defecto se usa `intfloat/multilingual-e5-small`, que corre localmente sin API key. Se eligió por tres motivos:
- **Es multilingüe**: el corpus está en español. `all-MiniLM-L6-v2` está entrenado en inglés.
- **Acepta hasta 512 tokens**, así que un chunk de 500 tokens se embebe completo. Se verificó con su propio tokenizador: el chunk más largo tiene 493.
- **Usa prefijos `query:` / `passage:`**, con los que fue entrenado. `embeddings.py` los agrega solos.

Se puede cambiar a Gemini u OpenAI con `EMBEDDINGS_PROVIDER`.

**Mismo modelo para indexar y consultar** (el error #1 de la consigna): la colección guarda en su metadata el identificador `proveedor:modelo`. `vectorstore.py` se niega a abrirla si el entorno configura otro modelo, y pide reindexar con `--rebuild`.

### Persistencia e ingesta incremental
- `chromadb.PersistentClient` en `./vectorstore`, con distancia coseno (`hnsw:space = cosine`).
- **IDs deterministas** (`archivo::chunk-NNN`): reindexar un archivo pisa sus chunks en lugar de duplicarlos.
- **Antes de indexar se compara el hash** de cada archivo con el guardado en la colección:
  - si no cambió, se saltea sin recalcular embeddings;
  - si cambió, se reemplazan solo sus chunks;
  - si se borró de `data/`, se eliminan sus chunks.

  El hash incluye la configuración de chunking, así que cambiar `chunk_size` también dispara la reindexación.

### Recuperación
- `asimilarity_search_with_score` con `top_k=4`. La consigna recomienda entre 3 y 5 para evitar el "contexto infinito" y el *lost in the middle*, y la configuración lo valida.
- Cada fragmento entra al prompt numerado y con su fuente: `[1] Fuente: politica_despliegues.md — Sección: ...`.

### Generación grounded
- **El system prompt actúa como filtro de veracidad**: el modelo solo puede usar el contexto y, si la respuesta no está, empieza con "No lo sé." y marca `respuesta_encontrada=false`.
- **El contexto va entre etiquetas `<contexto>`** y el prompt indica tratarlo como datos, lo que reduce el riesgo de prompt injection.
- **Variables de template**: pregunta y contexto entran así, no con f-strings, igual que en la PE2.
- **Cadena LCEL**: `rag_prompt | (llm | PydanticOutputParser).with_retry(stop_after_attempt=3)`. Se reintenta si la salida no es JSON válido o si es incoherente, por ejemplo `respuesta_encontrada=true` sin citar ningún fragmento.

### Referencias que el modelo no puede inventar
El LLM no escribe nombres de archivo: solo devuelve los **números** de los fragmentos que usó. `rag_chain.py` reemplaza esos números por la metadata real de ChromaDB y descarta los que no existen. Así las fuentes de `RAGResponse` siempre corresponden a fragmentos que se recuperaron de verdad.

### Asincronía
- `get_rag_response` es `async` de punta a punta: la búsqueda en Chroma usa `asimilarity_search_with_score` y la cadena se invoca con `ainvoke`.
- `pruebas_rag.py` lanza las dos consultas en paralelo con `asyncio.gather`.

### Reintentos
`.with_retry()` envuelve **modelo + parser** y decide qué reintentar con `es_reintentable` (en `rag_chain.py`):

| Error | ¿Se reintenta? | Motivo |
|---|---|---|
| `OutputParserException` (JSON inválido o incoherente) | Sí | El modelo puede responder bien en el siguiente intento |
| Rate limit por minuto, timeout, conexión, error 5xx | Sí | Fallos transitorios (`ModelError.is_retryable`) |
| Rate limit por cuota **diaria** agotada | No | Se renueva al día siguiente: esperar segundos no sirve (`cuota_diaria_agotada` lo detecta por el `quotaId` `...PerDay...` que envía Gemini) |
| API key inválida, permisos, request inválido, modelo inexistente | No | Reintentar no lo arregla |

Son 1 intento + `LLM_MAX_RETRIES` (3 intentos por defecto), con backoff exponencial y jitter (unos 4 s y luego unos 8 s, con un tope de 30 s), para dar margen a los 503 por alta demanda. En los tests el backoff se anula con `monkeypatch`. Los mensajes de error se recortan a 200 caracteres (`resumir_error`), porque Google devuelve el JSON completo de la respuesta en cada error.

**Los reintentos son solo de la cadena, no del SDK.** El SDK de Gemini reintenta cualquier 429, incluido el de cupo diario, sin distinguirlos y sin pasar por el rate limiter. Por eso `llm.py` crea los chat models sin reintentos internos (`reintentos_del_sdk`: `max_retries=1` en Gemini, que en esa librería significa un único intento, y `0` en OpenAI y Anthropic). Es la misma política que las pre-entregas 2, 4 y 5.

### Proveedor del LLM
`llm.py` es el mismo módulo de la pre-entrega 2: variables de entorno validadas con Pydantic (`LLMSettings`) y un proveedor elegible por `LLM_PROVIDER`. Soporta `ChatGoogleGenerativeAI` (por defecto), `ChatOpenAI` y `ChatAnthropic`.

#### Modelos por defecto
Son identificadores reales de cada API (verificados en noviembre de 2026) y se pueden cambiar en el `.env`:

| Proveedor | Modelo por defecto | Estado | Fuente |
|---|---|---|---|
| Gemini | `gemini-3.5-flash-lite` | Estable. Es el de mayor cupo gratuito (ver abajo); `gemini-3.8-flash` también funciona | [Modelos de Gemini](https://aistudio.google.com/docs/models) |
| OpenAI | `gpt-4o-mini` | Deprecado, se da de baja el 14/01/2027 (reemplazo: `gpt-6-luna`) | [Deprecaciones de OpenAI](https://developers.openai.com/api/docs/deprecations) |
| Anthropic | `claude-sonnet-5-5` | Vigente | [Modelos de Claude](https://platform.claude.com/docs/en/about-claude/models/overview) |

Solo se probó contra la API real el proveedor por defecto (Gemini).

#### `temperature` entre proveedores
No todos los proveedores aceptan este parámetro. Para que la configuración no tenga efectos distintos sin aviso, `llm.py` aplica esta regla (`soporta_temperature`):

| Proveedor / modelo | Qué pasa con `LLM_TEMPERATURE` |
|---|---|
| OpenAI, Gemini 2.x | Se envía tal cual |
| Gemini 3.x | No se envía: Google lo declaró deprecado e ignorado desde `gemini-3.6-flash` y `gemini-3.5-flash-lite`, y se omite en toda la familia `gemini-3*` |
| Anthropic (Claude Opus 4.7 en adelante, incluida la familia 5.5) | No se envía: la API responde 400 |

En los dos últimos casos se registra un `WARNING`. En un RAG el determinismo que importa es el grounding, y ese se consigue con el prompt (reglas explícitas, contexto delimitado) y con el esquema, no con sampling.

#### Razonamiento de Gemini 3 (`LLM_THINKING_LEVEL`)
El razonamiento interno consume del mismo `max_tokens`, y responder con datos que ya vienen en el contexto no requiere pensar a fondo. Por eso el valor por defecto es `low`: baja la latencia y el riesgo de que el JSON quede truncado. Solo se envía a los modelos `gemini-3*`; a Gemini 2.x, OpenAI y Anthropic no.

Qué se probó contra la API real: `low` funciona en `gemini-3.5-flash-lite` y `gemini-3.8-flash`. Con `gemini-3.6-flash` se usó `medium` en una corrida anterior: según la documentación de Google, ese modelo acepta solo `medium` o `high`. Si un modelo rechaza el nivel configurado, se cambia `LLM_THINKING_LEVEL` en el `.env`.

#### Cupo de requests (`LLM_REQUESTS_PER_MINUTE`)
El plan gratuito de Gemini limita las requests por minuto y por día. El cupo es por proyecto **y** por modelo:

| Modelos | Requests/min | Tokens/min | Requests/día |
|---|---|---|---|
| Flash (`gemini-3.8-flash`, `gemini-3.6-flash`, `gemini-3-flash`, `gemini-2.5-flash`) | 5 | 250K | 20 |
| Flash Lite (`gemini-3.5-flash-lite`, `gemini-3.1-flash-lite`) | 15 | 250K | 500 |

*Valores del panel de AI Studio en noviembre de 2026. Google aclara que pueden cambiar; los de cada proyecto se ven en [aistudio.google.com/rate-limit](https://aistudio.google.com/rate-limit). El cupo diario se reinicia a la medianoche del Pacífico.*

Por eso el modelo por defecto es **`gemini-3.5-flash-lite`**. Al principio era `gemini-3.8-flash`, y su cupo de 20 requests por día se agotó en una tarde de pruebas.

`llm.py` usa un único `InMemoryRateLimiter` por cupo (`_rate_limiter`, con `@cache`), compartido por todos los modelos que se crean y pasado como `rate_limiter=` al chat model. Las consultas de `asyncio.gather` corren a la vez, pero las llamadas al modelo se espacian: con `LLM_REQUESTS_PER_MINUTE=12` sale una cada 5 segundos. Para un modelo Flash conviene bajarlo a 4. Si el plan es pago, se borra la variable y no hay límite.

Limitación: el limitador es por proceso. Dos scripts corriendo a la vez comparten el cupo de la API key y se pisan.

### Errores que puede lanzar `get_rag_response`
Que la respuesta no esté en los documentos **no es un error**: se devuelve un `RAGResponse` normal con `respuesta_encontrada=false`, la respuesta empezando con "No lo sé." y sin referencias.

| Excepción | Cuándo ocurre | ¿Se reintentó? | Cómo reaccionar |
|---|---|---|---|
| `ValueError` | La consulta está vacía | No llega al modelo | Validar la entrada |
| `pydantic.ValidationError` | Configuración inválida en el `.env` (falta la API key, `RAG_TOP_K` fuera de 3–5, etc.) | No | Corregir el `.env` |
| `RuntimeError` | La colección está vacía | No | Correr `python ingest.py` |
| `EmbeddingsMismatchError` (subclase de `RuntimeError`) | La colección se indexó con otro modelo de embeddings | No | Correr `python ingest.py --rebuild` |
| `OutputParserException` | La salida no cumplió el esquema en ninguno de los intentos | Sí (3 intentos por defecto) | Revisar el prompt o subir `LLM_MAX_TOKENS` |
| `ModelRateLimitError` con `...PerDay...` en el mensaje | Se agotó el cupo diario del modelo | No (una sola request) | Esperar al reinicio diario o cambiar `GEMINI_MODEL` |
| `ModelError` con `is_retryable=True` (rate limit por minuto, timeout, 5xx) | Falla transitoria del proveedor | Sí (3 intentos por defecto) | Reintentar más tarde |
| `ModelError` con `is_retryable=False` (API key, permisos, modelo inexistente) | Error de configuración | No | Corregir el `.env` |

Las excepciones del proveedor son subclases de `langchain_core.exceptions.ModelError`, iguales en los tres proveedores:

```python
from langchain_core.exceptions import ModelError, OutputParserException
from rag_chain import get_rag_response

try:
    resultado = await get_rag_response(pregunta)
except OutputParserException:
    ...  # el modelo no devolvió un JSON válido
except ModelError as exc:
    ...  # exc.is_retryable indica si conviene reintentar más tarde
```

## Instalación

Requiere Python 3.12 o superior.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    |    Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # y completá la API key del LLM elegido
```

La primera ejecución descarga el modelo de embeddings (~470 MB) desde HuggingFace.

## Ejecución

```bash
# 1. Ingesta: lee data/, fragmenta y guarda en ./vectorstore (incremental)
python ingest.py
python ingest.py --rebuild     # reindexa todo desde cero

# 2. Las dos pruebas de la consigna (deja evidencia en evidencias/resultado_pruebas.json)
python pruebas_rag.py

# 3. Preguntas libres
python consultar.py "¿Quién recibe la página si el primario no confirma en 5 minutos?"
python consultar.py            # modo interactivo
```

Si corrés `python ingest.py` dos veces seguidas, la segunda informa *"La colección ya estaba al día: no se recalculó ningún embedding."*

La carpeta `vectorstore/` no está en el repo (está en `.gitignore`). Al clonarlo, el primer `python ingest.py` la crea. `pruebas_rag.py` también sincroniza el índice antes de preguntar. La ingesta usa embeddings locales, así que no consume requests del LLM.

## Pruebas

### Pruebas de punta a punta (`pruebas_rag.py`, requieren API key)

| Caso | Pregunta | Qué se verifica |
|---|---|---|
| En contexto | ¿Qué porcentaje del tráfico recibe un despliegue en la primera etapa del canary y durante cuánto tiempo? | `respuesta_encontrada=true` y cita `politica_despliegues.md` |
| Trampa | ¿Cuántos días de vacaciones anuales les corresponden a los ingenieros que hacen guardias? | `respuesta_encontrada=false`, empieza con "No lo sé." y sin referencias |

La pregunta trampa es deliberadamente difícil. Habla de guardias, que sí están documentadas: el retriever trae la sección "Compensación" de la guía de guardias, que menciona francos compensatorios pero no vacaciones. Lo que se prueba es que el modelo no complete el hueco con conocimiento general.

El script imprime cada verificación como `[OK]` o `[FALLA]`, sale con código 1 si alguna falla y guarda la evidencia completa en `evidencias/resultado_pruebas.json`. Ese JSON incluye LLM, embeddings, top_k, configuración de chunking y la salida de cada caso.

**Resultado de la corrida real: 2/2 pruebas aprobadas.** La respuesta coincide con la tabla del documento (5 % durante 15 minutos), y en la trampa el modelo no confundió "vacaciones" con los francos compensatorios que sí aparecen en el contexto recuperado.

La evidencia se generó el 2026-11-05 con la configuración por defecto (`gemini-3.5-flash-lite`, `LLM_THINKING_LEVEL=low`), partiendo de un clon limpio del repositorio: sin `vectorstore/`, así que `pruebas_rag.py` construyó el índice desde cero (12 chunks) antes de preguntar. Usó 2 requests al LLM. Una corrida anterior con `gemini-3.6-flash` (`LLM_THINKING_LEVEL=medium`) también había dado 2/2.

### Tests offline (`pytest`, sin API keys ni red)

```bash
pip install -r requirements-dev.txt
pytest
```

Usan embeddings deterministas falsos y un LLM falso (`FakeListChatModel`). Cubren:
- **Limpieza y chunking**: ningún chunk supera los 500 tokens, el overlap está presente y los IDs son deterministas.
- **Ingesta incremental**: sin cambios no se reindexa; se reindexa lo modificado y se borra lo eliminado.
- **Persistencia y modelo de embeddings**: la colección persiste en disco y se rechaza abrirla con otro modelo de embeddings.
- **Referencias**: se mapean a la metadata real y se descartan las citas a fragmentos inexistentes.
- **Pregunta trampa y reintentos**: la trampa devuelve "No lo sé" sin fuentes; una salida inválida o incoherente y un rate limit se reintentan; la cuota diaria y una API key inválida no; la cantidad de intentos es configurable.
- **Configuración del LLM**: modelos por defecto, regla de `temperature` por proveedor, `thinking_level`, cupo de requests compartido, SDK sin reintentos internos y rangos inválidos.

## Cómo cubre la rúbrica

| Criterio | Dónde |
|---|---|
| Chunking e ingesta (25 %) | `ingest.py`: limpieza, splitter por tokens 500/50 cortando en oraciones, metadata, ingesta incremental |
| Arquitectura asíncrona y recuperación (35 %) | `rag_chain.py`: `get_rag_response` async, `asimilarity_search_with_score`, `ainvoke`, `asyncio.gather` en las pruebas |
| Validación con Pydantic (20 %) | `schemas.py` + `PydanticOutputParser`: respuesta estructurada con referencias trazables |
| Robustez y evidencia (20 %) | Pregunta trampa, `evidencias/resultado_pruebas.json`, tests offline, API keys solo en `.env` |
