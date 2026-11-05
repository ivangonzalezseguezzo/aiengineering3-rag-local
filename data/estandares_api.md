# Estándares de diseño de APIs

Este documento fija las reglas que deben cumplir las APIs HTTP de Órbita Pagos, tanto las públicas (que consumen comercios e integradores) como las internas entre servicios. Las revisiones de diseño verifican estos puntos antes de que una API llegue a producción.

## Estilo y versionado

Las APIs son REST sobre HTTPS y usan JSON en UTF-8. Los recursos se nombran en plural y en inglés (`/v1/payments`, `/v1/refunds`), y los campos usan snake_case.

La versión mayor va en la ruta (`/v1/`, `/v2/`). Se crea una versión nueva solo ante cambios incompatibles: quitar un campo, cambiar su tipo o cambiar el significado de un código de estado. Agregar campos opcionales no es un cambio incompatible y no requiere versión nueva.

Cuando una versión se depreca, se mantiene operativa durante 12 meses desde el anuncio. Las respuestas de la versión deprecada incluyen los headers `Deprecation` y `Sunset` con la fecha de baja.

## Autenticación y autorización

- Las APIs públicas usan OAuth 2.0 con el flujo client credentials. Los tokens de acceso duran 60 minutos.
- La comunicación entre servicios internos usa mTLS con certificados emitidos por la autoridad certificante interna, rotados cada 30 días de forma automática.
- Cada token lleva los scopes mínimos necesarios. Un comercio que solo consulta pagos no recibe el scope `payments:write`.

## Idempotencia

Las operaciones que mueven dinero deben poder reintentarse sin cobrar dos veces. Por eso, todo `POST` a `/v1/payments` y `/v1/refunds` exige el header `Idempotency-Key` con un UUID v4 generado por el cliente.

El servidor guarda la clave y la respuesta durante 24 horas:

- Si llega la misma clave con el mismo cuerpo, devuelve la respuesta original sin volver a procesar.
- Si llega la misma clave con un cuerpo distinto, responde `422 Unprocessable Entity` con el código de error `PAY-4091`.
- Si falta el header, responde `400 Bad Request` con el código `PAY-4000`.

## Límites de uso (rate limiting)

| Grupo de endpoints | Límite por cliente |
|---|---|
| Cobros y reembolsos | 100 solicitudes por segundo |
| Consultas de pagos | 50 solicitudes por segundo |
| Reportes y conciliación | 20 solicitudes por segundo |

Al superar el límite, la API responde `429 Too Many Requests` con el header `Retry-After`, que indica cuántos segundos esperar. Los clientes deben respetar ese header en lugar de reintentar de inmediato.

## Formato de errores

Los errores siguen el estándar RFC 9457 (`application/problem+json`) con los campos `type`, `title`, `status`, `detail` y `trace_id`. Además, cada error de negocio incluye un campo `code` con un código interno del formato `PAY-NNNN`, documentado en el catálogo público de errores.

El `trace_id` es el mismo identificador que aparece en los logs y en las trazas distribuidas, de modo que Soporte pueda encontrar la solicitud exacta a partir del reclamo de un comercio.

## Timeouts y reintentos

- Los clientes externos deben usar un timeout de 10 segundos para las operaciones de cobro.
- Las llamadas entre servicios internos usan un timeout de 2 segundos y hasta 2 reintentos con backoff exponencial y jitter.
- Solo se reintentan operaciones idempotentes: lecturas, o escrituras protegidas con `Idempotency-Key`.

Un servicio interno nunca debe encadenar más de tres llamadas sincrónicas para responder una solicitud. Si lo necesita, el diseño se revisa para usar eventos asincrónicos.

## Paginación

Los listados usan paginación por cursor con los parámetros `limit` y `starting_after`. El valor máximo de `limit` es 100 y el valor por defecto es 20. No se usa paginación por número de página porque da resultados inconsistentes cuando se insertan registros durante la consulta.

## Datos sensibles

Ninguna API devuelve ni registra en logs el número completo de tarjeta (PAN) ni el código de seguridad (CVV), en cumplimiento de PCI DSS. Los números de tarjeta se muestran enmascarados (solo los primeros 6 y los últimos 4 dígitos) y los logs pasan por un filtro que redacta automáticamente cualquier secuencia que parezca un PAN.
