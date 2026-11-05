# Guía de guardias (on-call)

Esta guía describe cómo funcionan las guardias de ingeniería en Órbita Pagos: quiénes las hacen, cómo se escalan las alertas y qué se espera de la persona de guardia. Complementa al runbook de gestión de incidentes.

## Estructura de la rotación

Cada equipo dueño de un servicio en producción tiene su propia rotación semanal. La guardia empieza el lunes a las 10:00 y termina el lunes siguiente a la misma hora. En cada turno hay dos roles:

- **Primario:** recibe todas las páginas y es la primera persona en responder.
- **Secundario:** respalda al primario y recibe la página si el primario no responde.

Nadie hace más de una semana de guardia cada cuatro semanas. Si un equipo tiene menos de cuatro personas en la rotación, comparte la guardia con un equipo afín, definido por el área de Confiabilidad.

Las personas que ingresan a un equipo hacen dos rotaciones como sombra (shadow) antes de quedar como primarias: reciben las páginas, pero la responsabilidad sigue en el primario titular.

## Escalamiento de alertas

Las alertas se envían con Sirena, el sistema interno de paginado. La cadena de escalamiento para alertas críticas es:

1. La página llega al primario.
2. Si el primario no confirma (ack) en 5 minutos, Sirena llama al secundario.
3. Si nadie confirma a los 15 minutos de la primera página, Sirena escala al engineering manager del equipo.
4. A los 30 minutos sin confirmación, escala al director de ingeniería del área.

Las alertas no críticas se envían solo por chat y se revisan dentro del horario laboral.

## Qué se espera de la persona de guardia

Durante su turno, la persona primaria:

- Tiene la laptop de trabajo y una conexión estable a no más de 30 minutos de distancia.
- Confirma las páginas dentro de los tiempos de escalamiento, incluso fuera del horario laboral.
- Evalúa la alerta y, si corresponde, declara un incidente con `/incidente declarar` y asume el rol de responsable técnico.
- Registra en el documento de traspaso cada alerta recibida y lo que hizo al respecto.

La persona de guardia no tiene que resolver todo sola. Pedir ayuda temprano es una buena práctica, no una señal de debilidad.

## Calidad de las alertas

Una alerta que despierta a alguien tiene que ser accionable. Por eso:

- Toda alerta crítica tiene enlazado su runbook, con los pasos de diagnóstico y mitigación.
- Una alerta que se dispara más de 3 veces en una semana sin requerir acción se marca como ruidosa y se revisa en el traspaso.
- Las alertas ruidosas que siguen sin corregirse después de dos semanas se degradan a no críticas hasta que el equipo dueño las ajuste.

El objetivo de Órbita es que ninguna rotación reciba más de 2 páginas fuera de horario por semana, en promedio trimestral.

## Traspaso de guardia

Cada lunes a las 10:00 la persona saliente y la entrante tienen una reunión de traspaso de 15 minutos. El documento de traspaso incluye:

- Incidentes abiertos y su estado.
- Alertas ruidosas detectadas durante la semana.
- Despliegues o migraciones programados para la semana entrante.
- Cualquier riesgo conocido, por ejemplo un proveedor con degradación intermitente.

## Compensación

La guardia se compensa de dos formas:

- Un adicional del 15 % sobre el salario semanal por cada semana de guardia como primario, y del 7 % como secundario.
- Medio día de franco compensatorio por cada incidente atendido entre las 22:00 y las 07:00, a tomar dentro de los 30 días siguientes.

Los francos compensatorios se cargan en el sistema de Recursos Humanos con el número de incidente como justificación.

## Intercambios de turno

Los intercambios de guardia se acuerdan entre las personas involucradas y se registran en Sirena con al menos 48 horas de anticipación. Si el cambio es por una urgencia, alcanza con avisar al engineering manager y actualizar Sirena antes del inicio del turno.
