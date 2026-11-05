"""Contratos de datos del sistema RAG.

Se separan dos modelos a propósito:
- `RespuestaGenerada` es lo único que produce el LLM: el texto, si encontró la respuesta y
  los NÚMEROS de los fragmentos que usó. Se parsea con PydanticOutputParser.
- `RAGResponse` es lo que devuelve get_rag_response: el código reemplaza esos números por
  las referencias reales (archivo, sección, chunk y distancia) tomadas de la metadata de
  ChromaDB. Así el modelo no puede inventar una fuente: solo puede elegir entre las que
  efectivamente se recuperaron.
"""

from pydantic import BaseModel, Field, field_validator, model_validator

RESPUESTA_SIN_CONTEXTO = "No lo sé."


class RespuestaGenerada(BaseModel):
    """Respuesta del asistente basada exclusivamente en los fragmentos del contexto."""

    respuesta: str = Field(
        description=(
            "Respuesta en español, concisa (máximo 4 oraciones), usando solo datos del contexto. "
            f"Si el contexto no alcanza para responder, empezá exactamente con '{RESPUESTA_SIN_CONTEXTO}' "
            "y aclarás en una oración qué información falta."
        )
    )
    respuesta_encontrada: bool = Field(
        description="true si el contexto contiene la información para responder; false en caso contrario."
    )
    fragmentos_usados: list[int] = Field(
        default_factory=list,
        description=(
            "Números [n] de los fragmentos del contexto de los que sale la respuesta. "
            "Vacío si respuesta_encontrada es false."
        ),
    )

    @field_validator("respuesta")
    @classmethod
    def respuesta_no_vacia(cls, valor: str) -> str:
        limpio = " ".join(valor.split())
        if not limpio:
            raise ValueError("la respuesta no puede estar vacía")
        return limpio

    @model_validator(mode="after")
    def coherencia(self) -> "RespuestaGenerada":
        if self.respuesta_encontrada and not self.fragmentos_usados:
            # Una respuesta "encontrada" sin citar fragmentos no es verificable: se rechaza y se reintenta.
            raise ValueError("si respuesta_encontrada es true hay que citar al menos un fragmento")
        if not self.respuesta_encontrada:
            self.fragmentos_usados = []
            if not self.respuesta.startswith(RESPUESTA_SIN_CONTEXTO):
                self.respuesta = f"{RESPUESTA_SIN_CONTEXTO} {self.respuesta}"
        # Sin duplicados y en orden.
        self.fragmentos_usados = sorted(set(self.fragmentos_usados))
        return self


class Referencia(BaseModel):
    """Fragmento recuperado de ChromaDB que el modelo usó para responder."""

    fragmento: int = Field(description="Número del fragmento dentro del contexto enviado al LLM")
    archivo: str
    titulo: str
    seccion: str
    chunk_id: str
    distancia: float = Field(description="Distancia coseno a la consulta (0 = idéntico)")
    extracto: str = Field(description="Primeros caracteres del fragmento, para inspección rápida")


class RAGResponse(BaseModel):
    """Salida final de get_rag_response: respuesta + referencias trazables."""

    pregunta: str
    respuesta: str
    respuesta_encontrada: bool
    referencias: list[Referencia] = Field(default_factory=list)
    fragmentos_recuperados: int = Field(ge=0)
    latencia_s: float = Field(ge=0)

    @property
    def fuentes(self) -> list[str]:
        """Archivos citados, sin repetir."""
        return sorted({ref.archivo for ref in self.referencias})
