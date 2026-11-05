# Runbook de gestión de incidentes

Este documento define cómo Órbita Pagos detecta, clasifica, mitiga y cierra los incidentes que afectan a la plataforma de cobros. Aplica a todos los equipos de ingeniería, incluidos los proveedores externos con acceso a producción. La versión vigente es la 3.2, aprobada por el comité de confiabilidad en septiembre de 2026.

## Qué es un incidente

Un incidente es cualquier evento no planificado que degrada o interrumpe un servicio en producción, o que pone en riesgo la integridad o la confidencialidad de los datos. Ante la duda, se declara: cerrar un incidente que resultó menor es barato; detectar tarde uno grave es caro.

Cualquier persona de ingeniería puede declarar un incidente con el comando `/incidente declarar` del bot de Slack Faro. El bot crea el canal `#inc-AAAAMMDD-servicio`, abre el puente de voz y registra la hora de inicio, que después se usa para medir los tiempos de respuesta.

## Niveles de severidad

| Severidad | Criterio | Ejemplos |
|---|---|---|
| SEV1 | Caída total de cobros, pérdida o corrupción de datos, o incidente de seguridad confirmado. Afecta a más del 25 % de las transacciones. | La API de cobros devuelve 503; se filtró una credencial de producción. |
| SEV2 | Degradación significativa: más del 5 % de errores o latencia p95 de cobros por encima de 2 segundos durante más de 10 minutos. | Un adquirente rechaza el 8 % de las tarjetas; el checkout tarda 4 segundos. |
| SEV3 | Impacto parcial que no afecta a los cobros. | Los reportes de conciliación se generan con 3 horas de atraso. |
| SEV4 | Sin impacto para clientes. | Un tablero interno muestra datos desactualizados. |

La severidad puede subir o bajar durante el incidente. Quien la cambia deja constancia en el canal con el motivo.

## Tiempos de respuesta

Para SEV1 rigen los siguientes objetivos, medidos desde la declaración del incidente:

- Confirmación (ack) de la persona de guardia: 5 minutos.
- Incident Commander asignado: 10 minutos.
- Primera comunicación en la página de estado para clientes: 15 minutos.
- Actualizaciones de estado: cada 30 minutos hasta la mitigación.

Para SEV2 el ack es de 15 minutos y las actualizaciones, cada 60 minutos. Los SEV3 se atienden el siguiente día hábil y los SEV4 se cargan al backlog del equipo dueño del servicio.

## Roles durante un incidente

- **Incident Commander (IC):** coordina, prioriza y toma decisiones. No ejecuta cambios técnicos: si el IC empieza a tipear comandos, nadie está coordinando. Para SEV1 y SEV2 el IC sale de la rotación de IC del área de Confiabilidad.
- **Responsable técnico:** lidera el diagnóstico y la mitigación. Suele ser la persona de guardia del servicio afectado.
- **Responsable de comunicaciones:** redacta las actualizaciones para la página de estado y para las áreas comerciales. Toda comunicación externa pasa por esta persona.
- **Escriba:** registra en el canal la línea de tiempo: qué se observó, qué se decidió y a qué hora.

## Mitigar primero, diagnosticar después

La prioridad es restablecer el servicio, no encontrar la causa raíz. Las acciones de mitigación preferidas, en este orden, son:

1. Revertir el último despliegue o desactivar el feature flag involucrado.
2. Redirigir el tráfico a otra región o a otro adquirente.
3. Escalar horizontalmente el servicio afectado.
4. Aplicar un hotfix, solo si las opciones anteriores no alcanzan.

Toda acción se anuncia en el canal antes de ejecutarse, con el formato "voy a hacer X porque Y". Así el IC puede frenar dos cambios simultáneos que se pisen entre sí.

## Cierre y postmortem

Un incidente se da por mitigado cuando las métricas vuelven a su línea de base durante 30 minutos seguidos. El IC lo cierra con `/incidente cerrar` y el bot calcula la duración total y el tiempo hasta la mitigación.

Todo SEV1 y SEV2 requiere un postmortem sin culpables (blameless) publicado dentro de los 5 días hábiles posteriores al cierre. El documento incluye la línea de tiempo, el impacto cuantificado (transacciones afectadas y monto), la causa raíz, los factores contribuyentes y una lista de acciones correctivas, cada una con dueño y fecha comprometida. Los postmortems se revisan en la reunión semanal de confiabilidad de los jueves a las 11:00.

Las acciones correctivas de un SEV1 tienen prioridad sobre el trabajo de producto del equipo dueño hasta completarse. Si una acción vence sin cerrarse, el caso se escala al director de ingeniería del área.
