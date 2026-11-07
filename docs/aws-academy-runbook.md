# Despliegue DFSha en AWS Academy: guía de ejecución

Este runbook deja lista la secuencia para desplegar el MVP en AWS Academy. Está escrito para la arquitectura Opción 1 y **no presupone acceso, cuotas ni recursos ya creados**. No compartan credenciales AWS, llaves privadas ni secretos del sistema en GitHub o en el informe.

## 1. Topología objetivo

Usar una VPC/red privada y procesos Docker separados entre máquinas virtuales:

| VM | Servicio | Datos persistentes | Endpoint anunciado |
|---|---|---|---|
| `dfs-control` | Controlador/Namenode | `/data` del compose de control | `https://<DNS-privado-control>:8000` |
| `dfs-dn1` | DataNode `dn1` | `/data/runtime/dn1` | `https://<DNS-privado-dn1>:8001` |
| `dfs-dn2` | DataNode `dn2` | `/data/runtime/dn2` | `https://<DNS-privado-dn2>:8001` |
| `dfs-dn3` | DataNode `dn3` | `/data/runtime/dn3` | `https://<DNS-privado-dn3>:8001` |

Ejecuten la CLI y las pruebas desde `dfs-control` (o desde otra máquina autorizada dentro de la misma red). Así, las direcciones privadas que el controlador anuncia para los DataNodes son alcanzables por el cliente. Verifiquen primero los límites de VMs, CPU, RAM, disco y duración del laboratorio; si no permiten cuatro VMs, registren la topología que realmente autoriza AWS Academy y consulten al profesor antes de cambiar la demostración requerida.

## 2. Reglas de red

Restringir los grupos de seguridad a la VPC y a los grupos de seguridad del equipo:

| Destino | Puerto | Origen permitido | Uso |
|---|---:|---|---|
| Control | TCP 8000 | DataNodes y máquina cliente del equipo | API, health, registro/heartbeat y CLI |
| Cada DataNode | TCP 8001 | Control, otros DataNodes y máquina cliente del equipo | Heartbeat interno y transferencia de bloques |
| Todas las VMs | TCP 22 | IP/rango autorizado por el laboratorio | Administración SSH; limitar según las reglas del curso |

No expongan 8000/8001 al público. La política exacta debe reflejar desde dónde ejecutan la CLI; si el cliente corre en `dfs-control`, incluyan el grupo de seguridad de control como origen de TCP 8001.

## 3. Preparar las máquinas

1. Inicien las VMs dentro de la red privada asignada y anoten nombre DNS privado, IP privada, tipo de instancia, disco, zona de disponibilidad y grupo de seguridad.
2. Instalen Docker Engine y Docker Compose v2 siguiendo el método permitido por la imagen/curso.
3. Copien el directorio `DFSha-MVP` a cada VM (o clonen el repositorio cuando exista). Usen el mismo commit en todas.
4. En cada DataNode, usen almacenamiento persistente para el volumen de bloques; en control, persistan `/data`.
5. Configuren certificados TLS firmados por la misma CA del proyecto. Cada certificado de servidor debe incluir en SAN el DNS/IP con el que otros procesos contactan a ese host. Usen certificados/llaves por VM; no reutilicen el certificado de laboratorio local `dev.crt/dev.key` en AWS.

## 4. Secretos y configuración

Genere dos secretos aleatorios y manténgalos fuera del repositorio y del historial del shell:

```bash
openssl rand -hex 32
openssl rand -hex 32
```

- Use el primer valor como `DFS_TOKEN_SECRET` y el segundo como `DFS_INTERNAL_SECRET`.
- El control y los tres DataNodes deben compartir los valores correspondientes.
- No los peguen en comandos que queden en el historial, capturas, informe, chat ni archivos versionados. En cada VM léanlos de forma interactiva o usen el mecanismo de secretos autorizado por el curso.
- Cambien las credenciales de demostración de `examples/metadata.seed.json` antes del primer arranque y conserven la semilla sin secretos personales.

En cada host, creen el directorio `certs/` con el certificado/llave propios del rol y la CA:

```text
certs/server.crt
certs/server.key
certs/ca.crt
```

Permitan lectura de `server.key` solo al usuario/grupo que ejecuta el servicio. No incluyan `certs/` en el repositorio.

## 5. Arrancar el plano de control

En `dfs-control`, desde la raíz de `DFSha-MVP`. Introduzcan los secretos en los prompts sin eco para que no queden en el historial del shell:

```bash
read -rsp 'DFS_TOKEN_SECRET: ' DFS_TOKEN_SECRET; export DFS_TOKEN_SECRET; echo
read -rsp 'DFS_INTERNAL_SECRET: ' DFS_INTERNAL_SECRET; export DFS_INTERNAL_SECRET; echo
docker compose -f docker-compose.control.yml up --build -d
docker compose -f docker-compose.control.yml ps
curl --fail --cacert certs/ca.crt https://<DNS-privado-control>:8000/health
```

Verifiquen `/health` local antes de iniciar DataNodes. Para validar TLS por nombre, usen el DNS que corresponda al certificado y a la red, por ejemplo `curl --cacert certs/ca.crt https://<DNS-privado-control>:8000/health`.

## 6. Arrancar cada DataNode

En cada VM de datos, desde su copia del mismo commit del MVP, definan los valores propios. Ejemplo para `dn1`; introduzcan los secretos en los prompts interactivos:

```bash
export NODE_ID=dn1
export NODE_PORT=8001
export NAMENODE_URL='https://<DNS-privado-control>:8000'
export NODE_INTERNAL_URL='https://<DNS-privado-dn1>:8001'
export NODE_EXTERNAL_URL="$NODE_INTERNAL_URL"
read -rsp 'DFS_TOKEN_SECRET: ' DFS_TOKEN_SECRET; export DFS_TOKEN_SECRET; echo
read -rsp 'DFS_INTERNAL_SECRET: ' DFS_INTERNAL_SECRET; export DFS_INTERNAL_SECRET; echo
docker compose -f docker-compose.datanode.yml up --build -d
docker compose -f docker-compose.datanode.yml ps
```

Repitan con `NODE_ID=dn2`/`dn3` y sus DNS privados. En los tres nodos, `NODE_EXTERNAL_URL` debe ser una URL alcanzable desde la CLI y el nombre debe coincidir con el SAN del certificado. El compose de rol asigna por defecto el puerto 8001.

## 7. Confirmar comunicación distribuida

Desde la VM cliente:

1. Comprueben conectividad DNS/TCP/TLS al controlador y a cada DataNode.
2. Consulten `https://<DNS-privado-control>:8000/health` validando la CA.
3. Ejecuten la CLI con `DFS_NAMENODE_URL=https://<DNS-privado-control>:8000` y `DFS_CA_BUNDLE` apuntando a `ca.crt`; autentíquense y consulten el estado del clúster.
4. Confirmen que aparecen tres DataNodes vivos y que las URLs anunciadas corresponden a los DNS privados esperados.
5. Desde la CLI, carguen un archivo de más de 8 MiB; revisen manifiesto y almacenamiento en más de un host, descarguen y comparen SHA-256.

No consideren válida la evidencia de despliegue multinodo si solo ven tres contenedores en una sola VM. Guarden una captura de la topología y salidas de terminal sin tokens, contraseñas ni llaves.

## 8. Pruebas y carpeta de evidencias

Desde la máquina cliente, con dependencias de Python instaladas:

```bash
export DFS_NAMENODE_URL='https://<DNS-privado-control>:8000'
export DFS_CA_BUNDLE="$PWD/certs/ca.crt"
python3 scripts/acceptance.py
python3 scripts/benchmark.py --size-mib 32 --clients 4
```

La prueba `scripts/failure_demo.py` está escrita para Compose local y no detiene VMs AWS. Para AWS, registren manualmente el procedimiento: observen tres nodos, detengan `dn1`, esperen que el heartbeat lo marque caído, prueben carga/lectura con los dos nodos restantes, vuelvan a iniciar `dn1` y observen reparación. No detengan una VM antes de guardar el estado inicial y asegúrense de que sus datos estén en almacenamiento persistente.

Guarden resultados no sensibles bajo `evidence/aws/`:

- `topology.md`: fechas, commit, roles, tipos de VM, DNS privado, discos y reglas de red (sin IPs públicas sensibles si el curso no lo permite).
- `acceptance.txt`: comandos, salida, código de retorno y hashes.
- `failure.txt`: hora de parada/recuperación, estado de nodos, comportamiento y logs relevantes.
- `benchmark.csv`: tamaño, concurrencia, repeticiones, duración y throughput.
- capturas del panel/red y terminal sin credenciales.

## 9. Limpieza y costos

Al terminar la demostración, guarden resultados y apaguen/eliminen únicamente los recursos temporales según las reglas del laboratorio. Verifiquen volúmenes y direcciones asignadas para no dejar recursos facturables activos. No ejecuten creación de VMs sin revisar primero cuota, saldo/duración y autorización del equipo.

## Bloqueo actual

Al preparar este runbook, AWS CLI estaba instalado en el entorno de trabajo, pero no se encontró una sesión/credencial válida de AWS Academy. Este archivo prepara el procedimiento; el despliegue debe ejecutarse desde el laboratorio del equipo cuando haya sesión y cuota disponibles.
