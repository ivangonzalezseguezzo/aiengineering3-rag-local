# Política de despliegues a producción

Esta política regula cómo se llevan cambios de código y de configuración a producción en Órbita Pagos. Su objetivo es que desplegar sea un evento frecuente, aburrido y reversible. Aplica a todos los servicios que corren en los clústeres de producción, incluidos los jobs batch y las funciones serverless.

## Ventanas de despliegue

Los despliegues normales se hacen de lunes a jueves, entre las 10:00 y las 16:00 (hora de Argentina). Los viernes solo se permite desplegar hasta las 13:00, y los fines de semana y feriados solo se despliegan hotfixes asociados a un incidente abierto.

La razón es simple: un despliegue fuera de horario tiene menos gente disponible para detectar y revertir un problema.

## Congelamiento de cambios (freeze)

Hay dos tipos de congelamiento:

- **Freeze de fin de año:** del 20 de diciembre al 6 de enero inclusive.
- **Freeze de eventos comerciales:** desde 48 horas antes hasta el final de Hot Sale, Cyber Monday y Black Friday.

Durante un freeze solo se despliegan hotfixes de incidentes SEV1 o SEV2. Cualquier otra excepción requiere la aprobación escrita del VP de Ingeniería, registrada en el ticket del cambio.

## Requisitos antes de desplegar

Un cambio está listo para producción cuando cumple todo lo siguiente:

1. El pull request tiene al menos dos aprobaciones, y una de ellas es de un integrante del equipo dueño del servicio.
2. La suite de tests pasa completa en el pipeline de integración continua.
3. Los servicios tier 1 (cobros, autorizaciones, antifraude y billetera) mantienen una cobertura de tests de al menos 80 %.
4. El escaneo de dependencias no reporta vulnerabilidades críticas ni altas sin una excepción aprobada por Seguridad.
5. El cambio está asociado a un ticket con la descripción del riesgo y el plan de rollback.

## Despliegue progresivo (canary)

Ningún servicio tier 1 se despliega de una sola vez al 100 % del tráfico. El pipeline usa Argo Rollouts con estas etapas:

| Etapa | Tráfico | Duración mínima |
|---|---|---|
| 1 | 5 % | 15 minutos |
| 2 | 25 % | 30 minutos |
| 3 | 100 % | — |

Para avanzar de etapa, la versión nueva tiene que mantener una tasa de errores menor al 1 % y una latencia p95 que no supere en más de un 10 % a la de la versión estable. El análisis es automático: compara las métricas de la versión canary contra las de la versión estable en la misma ventana de tiempo.

Los servicios tier 2 y tier 3 pueden saltear la etapa 2, pero nunca la etapa 1.

## Rollback

El rollback es automático si, durante cualquier etapa del canary, la tasa de errores supera el 2 % durante 3 minutos consecutivos. También puede dispararlo manualmente cualquier persona del equipo dueño o la persona de guardia, sin pedir permiso, con el comando `orbita deploy rollback <servicio>`.

Después de un rollback automático, el pipeline bloquea nuevos despliegues de ese servicio hasta que alguien del equipo dueño registre en el ticket la causa del fallo.

## Feature flags

Las funcionalidades nuevas se activan con feature flags desde la plataforma interna Interruptor, no con despliegues. Así se separa el momento de desplegar del momento de liberar.

Reglas para los flags:

- Todo flag tiene un dueño y una fecha de vencimiento.
- Los flags temporales de lanzamiento se eliminan del código dentro de los 30 días posteriores a quedar activos al 100 %.
- Los flags que controlan comportamiento en producción (kill switches) son permanentes y se documentan en el runbook del servicio.

## Migraciones de base de datos

Los cambios de esquema siguen el patrón expand/contract: primero se agrega lo nuevo de forma compatible (columnas nulas, tablas nuevas), después se despliega el código que lo usa y recién al final se elimina lo viejo. Una migración nunca viaja en el mismo despliegue que el código que depende de ella.

Las migraciones que bloquean tablas de más de 10 millones de filas se ejecutan en la ventana de mantenimiento de los martes de 06:00 a 07:00, con aviso previo de 48 horas en #deploys.

## Trazabilidad

Cada despliegue publica automáticamente en el canal #deploys la versión, el autor, el ticket asociado y el resultado de cada etapa del canary. Ese registro es la primera fuente que se consulta cuando se abre un incidente.
