# DFSha informe técnico grupal

> Completar con resultados reales del clúster. No convertir hipótesis de diseño en resultados medidos.

## 1. Resumen y alcance

- Integrantes y roles:
- Arquitectura asignada por el profesor:
- Cambios de alcance aprobados:
- Qué funciona y qué límites permanecen:

## 2. Comparación y decisiones

Resumir la matriz de `decision-record.md`, anotar la aprobación de simplificaciones RF3/RNF y actualizar cada decisión que haya cambiado durante la implementación.

| Decisión | Valor final implementado | Cambio vs. propuestas individuales | Justificación |
|---|---|---|---|
| Bloque / réplica / quórum | | | |
| Semántica `write()` | | | |
| Protocolos / seguridad | | | |
| Metadatos / consistencia | | | |

## 3. Arquitectura e interfaces

Insertar diagrama de componentes y despliegue real. Documentar APIs, modelos de mensajes, respuestas de error, almacenamiento de metadatos y protocolo de commit.

## 4. Algoritmos y garantías

- Asignación y localización de bloques:
- Confirmación de escritura (condición exacta de quórum y persistencia):
- Visibilidad y versiones observables:
- Concurrencia y locks:
- Integridad y aborto de una carga incompleta:
- Heartbeats, caída, fallback, reparación y GC:
- Autenticación, autorización y cifrado realmente habilitados:

## 5. Despliegue reproducible

| Rol | VM / contenedor | Host/IP privada | Puertos permitidos | Volumen |
|---|---|---|---|---|
| Control | | | | |
| DataNode 1 | | | | |
| DataNode 2 | | | | |
| DataNode 3 | | | | |
| Cliente | | | | |

Adjuntar comandos de instalación, configuración no secreta, arranque y verificación. No adjuntar tokens, contraseñas, llaves privadas ni datos sensibles.

## 6. Pruebas, resultados y análisis

Para cada prueba indicar fecha, commit, configuración, comando, resultado esperado, resultado obtenido y evidencia (log, captura, hash o CSV).

| Prueba | Setup | Resultado esperado | Resultado obtenido | Evidencia |
|---|---|---|---|---|
| Carga/recuperación/hash | | | | |
| RF1/RF3 | | | | |
| Escritura concurrente | | | | |
| Falla de DataNode / lectura | | | | |
| Falla durante carga / quórum | | | | |
| Réplica retrasada / reparación | | | | |
| Usuario permitido y denegado | | | | |
| TLS/cifrado en disco | | | | |
| Rendimiento: 1 cliente | | | | |
| Rendimiento: varios clientes | | | | |

Reportar mediana y percentil 95 si se tomaron suficientes muestras, throughput agregado, tamaños, concurrencia, topología y configuración. Explicar cuellos de botella y diferencias frente a hipótesis.

## 7. Limitaciones y trabajo futuro

Listar explícitamente SPOF, comportamiento no implementado, simplificaciones RF3, controles de seguridad ausentes y condiciones que no se probaron.

## 8. Contribuciones

| Integrante | Componentes / documentación | Commits o evidencia | Parte que puede defender |
|---|---|---|---|
| | | | |
| | | | |
| | | | |

## Guion sugerido del video de 10–15 minutos

1. (1 min) Problema, objetivos y alcance aprobado.
2. (2 min) Arquitectura, nodos y flujo de carga/lectura.
3. (3 min) Ejecutar RF1, `put/get`, hashes y distribución de bloques.
4. (2 min) Mostrar versiones, lock y conflicto de escritura.
5. (2 min) Detener un DataNode y mostrar fallback, quórum o recuperación.
6. (2 min) Seguridad y resultados medidos.
7. (1 min) Limitaciones, decisiones y aportes del equipo.

Cada integrante debe aparecer explicando al menos una decisión y poder responder preguntas sobre el flujo completo.
