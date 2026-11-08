# Matriz de requisitos y brechas del proyecto DFSha

**Base de revisión:** guía `Proyecto1_DFSha-20262.pdf`, propuestas individuales de Salomé Fonseca Arango, María Fernanda Muñoz Acosta y María del Pilar Mantilla Gelves, y código/documentación actualmente presente en este MVP.

**Corte:** 7 de noviembre de 2026. Esta matriz distingue entre capacidad implementada, verificación local y evidencia de despliegue/entrega. No afirma que se haya ejecutado nada en AWS.

**Aprobación reportada por el equipo:** el profesor aprobó la semántica de `write()` (crear, reemplazar y modificar por offset; sin anexado y con reconstrucción completa para la modificación parcial) y confirmar escrituras con dos de tres réplicas. Guarden el correo, mensaje o acta de esa aprobación como evidencia del equipo.

## Estado resumido

| Estado | Significado |
|---|---|
| Implementado local | El MVP declara e incluye la función; la guía o README describe cómo usarla. |
| Verificado local | Existe una prueba automatizada o demostración local descrita en el proyecto/antecedentes de trabajo. No equivale a validación en AWS. |
| Pendiente de evidencia | Falta resultado reproducible, registro, captura, log o medición de la entrega. |
| Pendiente de decisión | Requiere acuerdo del equipo o aprobación del profesor. |
| No implementado | El README del MVP declara la ausencia o el código no ofrece esa garantía. |

## Comparación de las propuestas individuales

| Tema | Salomé | María Fernanda | María del Pilar | Decisión del MVP grupal |
|---|---|---|---|---|
| Arquitectura asignada | Opción 1, S2S, VPC de AWS | Opción 1, S2S, Docker | Opción 1, S2S | Opción 1, cliente/control/DataNodes; verificar que coincide con la asignación del equipo. |
| Metadatos/control | 3 metadatos con Raft, líder y estado replicado | Controlador único, RAM + log transaccional propuesto | Controlador + respaldo con elección segura | Un controlador REST con JSON persistente y reemplazo atómico. No Raft ni alta disponibilidad del controlador. |
| Bloques y transferencia | Bloques inmutables, cifrados por cliente; pipeline DN→DN | 64 MiB; pipeline gRPC | 4 MiB; transferencia binaria directa | Bloques de 8 MiB por defecto; cliente transmite directo a hasta 3 DataNodes. |
| Replicación/confirmación | R=3, W=2, tercera copia asíncrona; 4 DNs | R=3, pipeline | R=3, umbral inicial W=2 | R=3 objetivo, W=2; reparación intenta restaurar las copias faltantes. |
| `write()` | Copy-on-write de bloques modificados; append no definido/limitado en el diseño | Creación/reemplazo total | Creación, reemplazo, append y parcial por bloque | Creación, reemplazo y offset; la escritura parcial vuelve a cargar todo el archivo. Sin append. Simplificación aprobada según confirmación del equipo. |
| Concurrencia | Raft, OCC, locks con fencing tokens, posible fusión de bloques distintos | Lock exclusivo por archivo | Locks, versión base y publicación controlada | Locks con lease, control de versión base y commits de manifiesto; no hay fencing tokens. |
| Seguridad | TLS 1.3/mTLS, JWT, AES-256-GCM desde cliente | JWT, TLS, AES-256 en DataNodes | HTTPS, tokens, cifrado según disponibilidad | Bearer firmado, autorización por usuario y capabilities; TLS opcional. Sin cifrado en reposo. |
| Idea útil adoptada | Bloques inmutables/versionados, commit atómico, recuperación, garantías medibles | Pipeline como alternativa y pruebas de fallo | API de control simple, baseline de dos réplicas | Se priorizó simplicidad implementable: manifiestos/versiones y commit de Salomé; pruebas de fallos de Fernanda; API REST y W=2 de María del Pilar. |

## Trazabilidad de requisitos grupales y evaluación

| Requisito/criterio | Peso grupal asociado | Estado en MVP | Evidencia actual o local | Falta para cerrar |
|---|---:|---|---|---|
| Comparar propuestas y registrar decisiones | Documentación (parte del 8 %) | Implementado parcialmente | `docs/decision-record.md` recoge decisiones, alternativas y pendientes. Esta matriz consolida diferencias de las tres propuestas. | Completar con el equipo, registrar integrantes, fecha de consenso, cambios finales y aprobación del profesor. |
| RF1: jerarquía, `ls`, `cd`, `mkdir`, `rmdir`, `rm`, aislamiento por usuario | 15 % RF1–RF3 | Implementado; smoke test local documentado | CLI y `scripts/acceptance.py` prueban `cd/pwd`, directorios, borrado y denegación entre usuarios. | Ejecutar y archivar resultados en el commit de entrega y repetir sobre el despliegue AWS. |
| RF2: `put/get` y reconstrucción íntegra | 15 % RF1–RF3 | Implementado; verificado local | Prueba de aceptación genera archivo >20 MiB, verifica varios bloques, réplicas y SHA-256. | Guardar salida, hashes, tamaño, manifiesto/topología y repetir en AWS con dirección de red real. |
| RF3: `open/read/write/lock/close` según semántica acordada | 15 % RF1–RF3 | Parcialmente implementado; simplificación aprobada según equipo | `acceptance.py` cubre lectura, escritura por offset, versiones y lock en conflicto; README declara que la escritura parcial reconstruye el archivo entero y no hay append. | Conservar evidencia de aprobación y demostrar claramente las primitivas RF3, incluido `close`. |
| Distribución, escalabilidad y comunicación | 12 % | Parcial; local distribuido en contenedores | Tres DataNodes separados y transferencia directa; descubrimiento dinámico por manifiesto. | Desplegar en VMs AWS Academy separadas y demostrar tráfico entre ellas. Documentar topología, direcciones privadas, reglas SG y datos distribuidos por nodo. El Compose local de un host no satisface esta prueba. |
| Replicación, disponibilidad y consistencia | 15 % | Parcial; funcionalidad local disponible | W=2/R=3, hashes, publicación de manifiesto, versiones; `failure_demo.py` prueba caída de DN y reparación cuando se corre. | Conservar salida/logs de caída, carga y reparación. Probar desconexión/retraso de una réplica y carga que no alcanza quórum. Documentar límites y condición exacta de lectura/escritura. |
| Seguridad y pruebas de rendimiento | 10 % | Parcial | Autenticación/autorización y prueba de acceso cruzado; overlay TLS y benchmark local disponibles. README reconoce que no cifra en reposo. | En AWS demostrar TLS con certificados válidos para la red desplegada, acceso permitido/denegado y controles internos. Resolver la brecha de cifrado en reposo o registrar aprobación/limitación. Guardar datos y análisis de benchmark en al menos dos condiciones. |
| Documentación, video y defensa | 8 % | Pendiente de entrega | Hay README, registro de decisiones y plantilla de informe/guion. | Completar informe con resultados reales, repositorio remoto y contribuciones identificables, video de 10–15 min y preparación de defensa individual para las tres. |

## Requisitos funcionales/no funcionales con brechas técnicas

| ID | Requisito de la guía | Estado del MVP | Brecha/riesgo que hay que resolver o declarar |
|---|---|---|---|
| RF1 | Sistema jerárquico y espacio privado por usuario | Implementado | Confirmar comportamiento de rutas y comandos con pruebas guardadas en el despliegue final. |
| RF2 | Transferencia de archivos completos | Implementado | Evidencia de varios bloques en nodos distintos y recuperación con SHA-256 en AWS. |
| RF3 | Primitivas de acceso y semántica explícita | Parcial | Aprobación de semántica; append no existe y modificación por offset retransfiere archivo entero. Demostrar todas las primitivas exigidas. |
| RNF1 | Escalabilidad en usuarios, número/tamaño de archivos | Parcial/no medida | Hay distribución y benchmark, pero faltan experimentos de crecimiento, límites observados y análisis. El JSON único de metadatos limita el crecimiento. |
| RNF2 | Disponibilidad y redundancia ante fallos | Parcial | Falla de DataNode y reparación están implementadas; falta evidencia repetible en AWS. Caída del controlador interrumpe control: no hay HA de metadatos. |
| RNF3 | Consistencia y tratamiento de réplicas | Parcial | Commit con W=2 y bloques versionados; no hay consenso para controlador único. Medir/mostrar réplica lenta/desconectada, versión visible y rechazo con quórum insuficiente. |
| RNF4 | Archivos y bloques distribuidos entre nodos | Implementado local / despliegue pendiente | Probar distribución física y comunicación entre VMs, no inferirla solo del manifiesto local. |
| RNF5 | Rendimiento y acceso concurrente | Parcial | Hay benchmark de 1 y varios clientes; faltan resultados reproducibles del entorno objetivo, varias ejecuciones y análisis. Los locks prueban conflicto, no acceso concurrente a bloques independientes. |
| RNF6 | Tránsito, reposo, autenticación, autorización, comunicaciones internas | Parcial | TLS es opcional; local HTTP no cifra. No hay cifrado en reposo. Sustituir secretos demo, usar certificados apropiados y probar límites de red en AWS; o aprobar/documentar explícitamente las limitaciones. |
| RNF7 | Transparencia, sin direcciones fijas en cliente | Implementado en diseño/código | Cliente obtiene nodos del manifiesto/cluster. Evidenciar con configuración real y cliente sin IPs de DataNodes codificadas. |

## Decisiones que requieren acuerdo del equipo/profesor

1. Confirmar que el grupo tiene asignada **Opción 1**. Las tres propuestas revisadas dicen Opción 1, pero la opción válida es la asignada por el profesor.
2. Aprobación de `write()` sin append y con modificación parcial que rehace el archivo, y confirmar con W=2 de R=3: aprobada según confirmación del equipo. Guardar el mensaje/acta y reflejar la semántica final en el informe.
3. Acordar cómo responderán en rúbrica a **cifrado en reposo**: implementarlo (por ejemplo, cifrado de volumen o cifrado de bloques) o pedir confirmación de que se acepta como limitación. El requisito RNF6 lo enumera expresamente; no basta con ocultarlo en la lista de limitaciones.
4. Confirmar cuotas y topología permitida en AWS Academy (número/tipo de VMs, conectividad privada, volúmenes persistentes, uso de certificados). El despliegue multinodo real sí es exigencia de la fase grupal.
5. Acordar en el equipo qué resultados y cambios se incorporan al diseño final y asignar responsables sin dejar la defensa completa a una sola persona.

## Próximas acciones priorizadas

| Prioridad | Acción | Resultado verificable |
|---:|---|---|
| 1 | Consenso de arquitectura y semántica; presentar simplificaciones al profesor | Aprobación recibida según el equipo; falta conservar el mensaje/acta y registrar el consenso final. |
| 2 | Confirmar acceso/cuotas AWS y diseñar topología de VMs/puertos | Runbook disponible en `docs/aws-academy-runbook.md`; falta sesión de AWS Academy y confirmar cuotas antes de desplegar. |
| 3 | Desplegar control y al menos tres DataNodes como servicios separados en VMs | Servicios saludables y `GET /v1/cluster` muestra nodos vivos desde una red distinta. |
| 4 | Ejecutar matriz de pruebas mínimas y guardar salidas | Carpeta de evidencias con comandos, fecha, commit, logs, hashes, capturas y CSV de métricas. |
| 5 | Cerrar seguridad: TLS en todos los canales usados y decisión/evidencia de cifrado en reposo | Pruebas verificables de acceso y cifrado; limitaciones aprobadas si alguna capacidad no se implementa. |
| 6 | Completar informe, contribuciones y video | Entregables grupales listos y demo ensayada por las tres. |

## Checklist de evidencia que todavía debe recolectarse

- [ ] Mensaje/acta que evidencie la aprobación reportada de las simplificaciones de `write()` y quórum.
- [ ] Confirmación de la opción arquitectónica asignada al equipo.
- [ ] Repositorio GitHub con historial/commits atribuibles a cada integrante e instrucciones reproducibles.
- [ ] Diagrama de arquitectura y diagrama de despliegue real en AWS Academy.
- [ ] Carga/descarga de archivo grande en AWS, manifiesto con bloques colocados y hashes idénticos.
- [ ] RF1 y RF3 con comandos, resultados esperados y resultados observados.
- [ ] Dos clientes sobre la misma ruta: lock/conflicto sin sobrescritura silenciosa.
- [ ] Falla de DataNode, lectura desde réplica y restauración de la redundancia.
- [ ] Réplica lenta/desconectada y comportamiento de commit/quórum observable.
- [ ] Acceso autenticado permitido y acceso entre usuarios denegado.
- [ ] TLS/canales internos demostrados y estado real del cifrado en disco.
- [ ] Rendimiento para un cliente y múltiples clientes, con tamaño, topología, repeticiones, latencias y throughput.
- [ ] Informe final, video de 10–15 minutos y defensa practicada por las tres integrantes.
