# DFSha — prototipo distribuido

Prototipo de sistema de archivos distribuido para el Proyecto 1 de Sistemas Distribuidos. La arquitectura usa una CLI, un plano de control REST con metadatos persistentes y tres DataNodes independientes. Los bloques se transfieren directamente del cliente a los DataNodes y el plano de control solo publica un manifiesto cuando recibe recibos válidos de al menos dos réplicas.

Link al video explicativo: 

https://youtu.be/Jn0-rcJDCng
## Estado y garantías implementadas

- Bloques de 8 MiB por defecto, distribución entre DataNodes y factor objetivo de tres copias. `WRITE_QUORUM=2` permite confirmar con dos copias persistidas; la tercera puede completarse mediante reparación cuando exista un nodo disponible.
- Bloques inmutables por versión, hashes SHA-256 por bloque y archivo, escritura temporal con `fsync` y publicación atómica del manifiesto.
- Historial de versiones para lecturas con versión fija; escrituras por copia al escribir, control de `base_version` y locks con expiración.
- RF1: `ls`, `mkdir`, `rmdir`, `rm`; RF2: `put` y `get`; RF3: `open`, `read`, `write`, `lock` y `unlock` en la CLI.
- Autenticación Bearer firmada, contraseñas PBKDF2 en la semilla, autorización por espacio `/users/<usuario>/` y capacidades de corta duración para leer o escribir bloques.
- Heartbeats, asignación dinámica de DataNodes, reparación de réplicas y recolección periódica de bloques huérfanos.
- TLS opcional para el API del controlador, DataNodes y tráfico interno. La configuración local HTTP existe para facilitar el desarrollo y no cifra el tráfico.

## Límites que deben declararse en el informe

- Hay un solo controlador. Su archivo JSON se guarda con reemplazo atómico y sincronización a disco, pero no existe consenso Raft ni failover del controlador; su caída interrumpe el plano de control.
- El prototipo admite creación, reemplazo y modificación por offset mediante bloques nuevos. La modificación parcial vuelve a subir el archivo completo; `append` no está implementado.
- El contenido no está cifrado en reposo. TLS cifra el tráfico cuando se activa; los hashes comprueban integridad y no reemplazan la coordinación.
- Los locks son leases almacenados en el controlador, sin fencing tokens distribuidos. Para escrituras normales también hay exclusión por sesión de carga y control optimista de versión.
- Las credenciales de ejemplo son solo para demostración. Cambienlas y configuren secretos nuevos antes de desplegar fuera de un entorno de práctica.

## Requisitos

- Docker Engine y Docker Compose v2.
- Python 3.10+ para ejecutar la CLI.
- OpenSSL solo para generar el certificado de demostración TLS.

## Arranque local en Docker

Desde esta carpeta:

```bash
export DFS_TOKEN_SECRET="$(openssl rand -hex 32)"
export DFS_INTERNAL_SECRET="$(openssl rand -hex 32)"
docker compose up --build -d
docker compose ps
```

El controlador queda en `http://localhost:8100`; los DataNodes se publican en los puertos `8101`, `8102` y `8103`. Los puertos internos de los contenedores siguen siendo 8000 y 8001. El volumen de ejecución está aislado en `data/runtime/`; los datos antiguos de `data/dn*` y `data/namenode/` no se modifican.

La semilla crea dos usuarios de demostración:

| Usuario | Contraseña |
|---|---|
| `juan` | `contrasena123` |
| `nico` | `12345678` |

Para usar otras cuentas, editen `examples/metadata.seed.json` antes del primer arranque. Las contraseñas nuevas deben guardarse en formato `pbkdf2_sha256$iteraciones$sal_hex$hash_hex`; el formato en texto plano solo se admite para migrar metadata vieja.

Instalen dependencias de la CLI en un entorno virtual:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Ejemplo de recorrido completo:

```bash
mkdir -p examples/local
dd if=/dev/urandom of=examples/local/video.bin bs=1M count=20
python -m client.cli --user juan put examples/local/video.bin video.bin
python -m client.cli --user juan ls /
python -m client.cli --user juan open video.bin
python -m client.cli --user juan get video.bin examples/local/recovered.bin
sha256sum examples/local/video.bin examples/local/recovered.bin
```

La CLI solicita la contraseña sin mostrarla en pantalla. El cliente descubre direcciones de nodos mediante el manifiesto; no tiene puertos de DataNode codificados en su código.

## RF1 y RF3

```bash
python -m client.cli --user juan mkdir docs
python -m client.cli --user juan ls /
python -m client.cli --user juan read video.bin --offset 0 --length 128 --output examples/local/header.bin
python -m client.cli --user juan write video.bin examples/local/header.bin --offset 0
python -m client.cli --user juan get video.bin examples/local/version-2.bin --version 2
python -m client.cli --user juan lock video.bin --ttl 30
python -m client.cli --user juan unlock <LOCK_ID>
python -m client.cli --user juan shell
```

`shell` mantiene `cd`/`pwd` durante la sesión. `mkdir` crea un nivel a la vez. Las rutas remotas relativas se resuelven debajo del espacio del usuario. Un lock activo provoca `423 LOCKED` a otras escrituras; el lock usado por `put`/`write` se libera al confirmar.

## TLS para demostración local

Genere una CA/certificado autofirmado de laboratorio y arranque con el overlay TLS:

```bash
./scripts/generate-dev-cert.sh
export DFS_TOKEN_SECRET="$(openssl rand -hex 32)"
export DFS_INTERNAL_SECRET="$(openssl rand -hex 32)"
docker compose -f docker-compose.yml -f docker-compose.tls.yml up --build -d
export DFS_SCHEME=https
export DFS_CA_BUNDLE="$PWD/certs/dev.crt"
python -m client.cli --user juan ls /
```

El certificado es compartido por los contenedores solo para simplificar la demostración. No lo usen en AWS ni como PKI real. Para AWS Academy emitan certificados con SANs que coincidan con las direcciones privadas/DNS usadas y restrinjan Security Groups.

## Despliegue en VMs AWS Academy

El Compose local crea una red Docker de un solo host. Para demostrar comunicación entre VMs, usen los archivos de rol:

1. En la VM de control, clonen el repositorio y arranquen `docker-compose.control.yml`. Configure `DFS_TOKEN_SECRET`, `DFS_INTERNAL_SECRET` y la ruta del certificado; copien el certificado/CA al volumen `/certs`.
2. En cada VM de datos, arranquen `docker-compose.datanode.yml` con un `NODE_ID` distinto, la URL del controlador, `NODE_INTERNAL_URL` y `NODE_EXTERNAL_URL` con DNS/IP privada alcanzable. Use el mismo secreto interno y CA.
3. Configure clientes con `DFS_NAMENODE_URL=https://<DNS-control>:8000` y `DFS_CA_BUNDLE` apuntando a la CA.
4. En los Security Groups permita TCP 8000 entre clientes/nodos y controlador, y TCP 8001 entre clientes/controlador y DataNodes. Limite el acceso a la VPC. No exponga el API de control ni los puertos de datos a Internet público.
5. Verifique `/health`, `GET /v1/cluster` autenticado y los heartbeats antes de subir datos. Guarde la topología real y reglas de red en el informe.

El equipo debe confirmar las cuotas de AWS Academy antes de fijar cantidad de VMs. Si solo hay presupuesto para una VM, la demostración con contenedores separados no prueba tráfico entre máquinas virtuales; deben declarar ese límite.

## Interfaces principales

| API | Operación |
|---|---|
| `POST /v1/auth/login` | Login y token Bearer |
| `GET /v1/cluster` | Parámetros y DataNodes disponibles |
| `PUT/GET/DELETE /v1/dirs` | RF1: directorios |
| `POST /v1/uploads` | Preparar carga y asignar bloques |
| `PUT /v1/blocks/{id}` en DataNode | Persistir bloque y devolver recibo firmado |
| `POST /v1/uploads/{id}/commit` | Confirmar quórum y publicar versión |
| `GET /v1/files/manifest?path=…&version=…` | Manifiesto de la versión solicitada |
| `GET /v1/blocks/{id}` en DataNode | Lectura con capacidad temporal |
| `POST/DELETE /v1/locks` | Adquirir y liberar lock |
| `/internal/nodes/*` | Registro y heartbeat entre servicios |

Las rutas `/internal/*` requieren `X-DFSha-Internal`. Las rutas de bloque requieren capacidades firmadas por el controlador y vinculadas al nodo, bloque y operación.

## Evidencias de aceptación

Guarden para cada ejecución fecha, commit Git, tamaño/sha de archivos, salidas de comandos y logs.

1. `put/get`: archivo mayor que 8 MiB, SHA-256 idéntico y bloques visibles en al menos dos DataNodes.
2. RF1/RF3: CRUD de directorio, lectura parcial, escritura por offset y lectura de versión anterior.
3. Concurrencia: adquirir lock desde un cliente; otro cliente intenta escribir y recibe `423`; confirmar que no se pierde una versión.
4. Fallo de nodo: detener un DataNode durante descarga y comprobar fallback; detener uno durante carga y comprobar confirmación si quedan dos copias o rechazo si no se alcanza el quórum.
5. Reparación: volver a conectar o agregar un DataNode, esperar el ciclo de reparación y revisar bloques/replicas en metadata y logs.
6. Seguridad: login fallido, acceso cruzado entre `juan` y `nico` denegado, solicitud de bloque sin capacidad denegada y certificado TLS validado por el cliente.
7. Rendimiento: comparar al menos una condición de cliente frente a varias, medir tiempo y throughput para tamaños y nodos documentados.

Estos escenarios describen qué evidencia recolectar; los resultados deben ser mediciones reales del despliegue del equipo.

Para repetir los escenarios locales automatizados:

```bash
python3 scripts/acceptance.py
python3 scripts/failure_demo.py
python3 scripts/benchmark.py --size-mib 32 --clients 4
```

`failure_demo.py` detiene y vuelve a iniciar solo el servicio Compose `datanode1`, y debe ejecutarse con permisos Docker. El benchmark limpia sus archivos al terminar. Para AWS, `acceptance.py` y `benchmark.py` aceptan `DFS_NAMENODE_URL` y `DFS_CA_BUNDLE`; la prueba de caída debe adaptarse a la VM que el equipo vaya a detener.
