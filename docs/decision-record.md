# Registro de decisiones arquitectónicas DFSha

Estado: propuesta grupal para validar con el profesor antes de la entrega. Arquitectura asignada asumida: Opción 1, cliente/servidor S2S.

## Matriz comparativa

| Aspecto | Salomé | María Fernanda | María del Pilar | Decisión de equipo |
|---|---|---|---|---|
| Control | 3 nodos de metadatos con consenso Raft | Un NameNode con estado en RAM y log en disco | Coordinador primario, respaldo y elección segura propuesta | Un controlador con metadata JSON atómica para el MVP; se documenta como SPOF |
| DataNodes | Al menos 4 | Al menos 3 | Escalables, cantidad por definir | 3 servicios separados; factor objetivo 3 |
| Bloque | 8 MiB configurable | 64 MB fijo | Hasta 4 MiB | 8 MiB configurable por entorno |
| Replicación | Pipeline, R=3, quórum W=2 | Pipeline, R=3 | Réplicas con umbral inicial de 2 copias | Cliente envía a nodos asignados; commit con 2 recibos durables, reparación posterior |
| Metadatos y consistencia | Manifiestos inmutables, versiones, copy-on-write, Raft | Temporales y visibilidad en `close()` | Versiones y publicación coordinada; elección segura sin protocolo detallado | Manifiestos versionados, bloque inmutable y publicación atómica; control optimista por versión |
| Escritura | Creación, reemplazo, anexado y modificación parcial por bloques | Creación y reemplazo total | Admite los cuatro modos, sin POSIX completo | Creación, reemplazo y offset; anexado fuera del alcance; offset reescribe el archivo completo |
| Protocolos | REST/HTTPS, HTTP binario, gRPC interno | REST/HTTP + gRPC de datos | REST/HTTPS y transferencia HTTP binaria | REST/JSON para control; HTTP binario para datos; TLS activable |
| Seguridad | TLS/mTLS, tokens, cifrado cliente AES-GCM | JWT, TLS, AES-256 en DataNodes | TLS, autorización y cifrado en reposo recomendado | PBKDF2 en seed, Bearer HMAC, capacidades por bloque, aislamiento por usuario y TLS; sin cifrado en reposo |
| Riesgo de alcance | Alto: Raft, 7 VMs y cifrado cliente | Medio: simplifica escritura, pero maestro único | Medio/alto: failover no especificado completamente | Bajo/medio para el prototipo; sacrifica HA del controlador y cifra en tránsito solo con TLS activo |

## Decisiones adoptadas

| ID | Decisión | Alternativa descartada | Razón y consecuencia |
|---|---|---|---|
| D1 | Opción 1 con servicios separados | P2P o simulación monolítica | Respeta la arquitectura indicada y permite mostrar comunicaciones entre procesos y nodos. |
| D2 | Controlador único con JSON atómico y durable | Implementar Raft en tres controladores | Mantiene el prototipo alcanzable; no ofrece disponibilidad del plano de control ante caída del controlador. |
| D3 | Bloques de 8 MiB, R=3 objetivo, W=2 | 64 MB o 4 MiB; confirmar con todas las réplicas | Reduce el costo de reintento frente a bloques muy grandes y limita overhead de metadatos; dos copias permiten confirmar con una tercera fuera de servicio. |
| D4 | Transferencia directa cliente → hasta tres DataNodes | Pasar todo el tráfico pesado por el controlador o usar pipeline | Evita cuello de botella del control y mantiene el envío implementable; el cliente genera más tráfico que en pipeline. |
| D5 | Hash SHA-256, fsync, recibos firmados y publicación por commit | Publicar bloques mientras llegan | Impide exponer bloques parciales; los recibos prueban que cada DataNode persistió el contenido aceptado. |
| D6 | Versiones inmutables y `base_version`; lock de archivo con lease | Escrituras in-place con semántica POSIX | Reduce corrupción y detecta conflictos; los locks no tienen fencing token distribuido. |
| D7 | `write(offset, bytes)` vuelve a cargar el archivo completo | Reescribir solo bloques modificados | Simplifica el primer cliente; limita rendimiento en archivos grandes y debe figurar como limitación. `append` no se ofrece. |
| D8 | Autenticación HMAC Bearer, permisos por usuario y TLS opcional | Direcciones de nodos fijas o confiar en IDs de bloque | Las capacidades se limitan a operación, bloque y nodo; TLS debe habilitarse y verificarse en la demostración de seguridad. |
| D9 | Sin cifrado en reposo | AES en DataNodes o cifrado cliente | Se priorizan autenticación, autorización y TLS en el tiempo disponible; no se debe afirmar que los datos estén cifrados en disco. |

## Arquitectura y flujo

```mermaid
flowchart LR
  CLI[CLI / cliente] -->|REST: login, rutas, locks, manifiesto| C[Controlador REST]
  C -->|JSON durable: usuarios, rutas, versiones, ubicación| M[(metadata.json)]
  CLI -->|HTTP(S) binario + capability| D1[DataNode 1]
  CLI -->|HTTP(S) binario + capability| D2[DataNode 2]
  CLI -->|HTTP(S) binario + capability| D3[DataNode 3]
  D1 -->|registro / heartbeat| C
  D2 -->|registro / heartbeat| C
  D3 -->|registro / heartbeat| C
  C -->|repair y GC| D1
  C -->|repair y GC| D2
  C -->|repair y GC| D3
```

**Carga:** login → `POST /v1/uploads` con tamaño y hashes → recibir bloques, nodos y capacidades → enviar bloques en temporales a los DataNodes → validar al menos dos recibos firmados por bloque → `POST /v1/uploads/{id}/commit` → publicar nueva versión de manera atómica.

**Lectura:** login → `GET /v1/files/manifest` para una ruta y versión → seleccionar una réplica autorizada por bloque → verificar hash de cada bloque → reconstruir y verificar SHA-256 global.

## Interfaces y comportamiento

- `POST /v1/auth/login`: valida usuario y emite Bearer de acceso.
- `GET /v1/cluster`: devuelve tamaño de bloque, quórum y DataNodes vivos.
- `PUT/GET/DELETE /v1/dirs`: crear, listar y eliminar directorios vacíos.
- `POST /v1/uploads`: asignar bloques, hash esperado y targets, con `base_version` opcional para reemplazo.
- `PUT /v1/blocks/{block_id}` en cada DataNode: recibe bytes binarios, tamaño/hash esperados y capability; devuelve un recibo HMAC tras `fsync` y rename atómico.
- `POST /v1/uploads/{upload_id}/commit`: comprueba recibos, quórum y versión base antes de publicar.
- `GET /v1/files/manifest?path=…&version=…`: manifiesto dinámico con réplicas vivas y capacidades de lectura.
- `GET /v1/blocks/{block_id}` en DataNode: entrega bytes solo con capability válida.
- `POST /v1/locks` y `DELETE /v1/locks/{id}`: leases para escritores.
- `/internal/nodes/*`: registro/heartbeat autenticado por secreto interno; `/internal/blocks/*` aplica reparación autenticada.

Errores que la CLI debe mostrar y que deben incluirse en la evidencia: `401` credenciales/token, `403` usuario o capability no autorizados, `404` ruta/bloque inexistente, `409` versión conflictiva, `423` lock ocupado, `422` hash inválido y `503` quórum insuficiente.

## Responsabilidades sugeridas

- Integrante 1: controlador, metadatos, autenticación, rutas, versiones y locks.
- Integrante 2: DataNodes, persistencia de bloques, checksums, réplicas y recuperación.
- Integrante 3: cliente/CLI, despliegue, métricas, evidencia e informe.
- Las tres deben revisar el sistema completo, ejecutar la demo y explicar las decisiones. Mantengan commits y tareas atribuibles a cada integrante.

## Aprobaciones y pendientes

- **Aprobado por el profesor, según confirmación del equipo (7 de noviembre de 2026):** `write()` permite crear, reemplazar y modificar por offset; no incluye anexado y una modificación parcial vuelve a cargar el archivo completo. Una escritura se confirma con W=2 de R=3. Conserven el mensaje/acta de aprobación como evidencia para el informe.
- Confirmar acceso, cuotas y número de VMs AWS Academy, y apuntar el entorno real usado en el informe.
- Antes de la entrega, adjuntar resultados reales de pruebas y registrar cambios frente a esta matriz. No presentar medidas planeadas como medidas ejecutadas.
