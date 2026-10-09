# Tarea 1 INF326: Notificación de sismos

Sistema de notificación de sismos con **publish-subscribe** (RabbitMQ) y **pull** por HTTP (FastAPI):

- `publisher/publish.py` publica un evento mínimo `{id, latitud, longitud}` por sismo en el exchange fanout `sismos`.
- `subscriber/subscriber.py` es un suscriptor por ciudad (Arica, Coquimbo, Valparaíso, Concepción y Punta Arenas), cada uno con su propia cola. Si el epicentro está a menos de 500 km, pide el detalle al servicio HTTP.
- `http_service/main.py` sirve el detalle en `GET /earthquakes/{id}` a partir de `data/sismos.json`, que se carga en memoria.

Los contratos entre componentes (dataset, mensaje, topología y endpoint) están en [`CONTRATOS.md`](CONTRATOS.md). La discusión de trade-offs y del Back of the Envelope está en [`DISCUSION.md`](DISCUSION.md).

## Integrantes

| Nombre | Rol |
|---|---|
| Andrés Araya | 202287004-4 |
| Baltazar Portilla | 202173112-1 |

## Inicio rápido

**Prerrequisitos:**

- Python 3.10 o superior (probado con 3.12).
- Docker.
- Puertos libres: `5672`, `15672` y `8000`.

Todos los comandos se ejecutan desde la raíz del repositorio, en Linux, macOS o WSL.

```bash
# 1. Instalar dependencias (una vez)
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Iniciar RabbitMQ
docker run -d --name rabbitmq-sismos -p 5672:5672 -p 15672:15672 rabbitmq:3.13-management

# 3. Terminal 1: levantar el servicio HTTP y los cinco suscriptores
python -m levantar

# 4. Terminal 2: publicar un sismo
source .venv/bin/activate
python -m publisher.publish sim-001

# 5. Detener: Ctrl+C en la terminal 1. Luego, opcionalmente:
docker stop rabbitmq-sismos
```

Sobre el paso 2: RabbitMQ tarda unos segundos en aceptar conexiones. Si `python -m levantar` informa que no pudo conectarse, hay que esperar a que `docker logs rabbitmq-sismos` muestre `Server startup complete` y volver a intentarlo.

`python -m levantar` hace lo siguiente:

1. Declara el exchange y las 5 colas (`python -m mensajeria`).
2. Inicia el servicio HTTP y espera a que responda.
3. Inicia los cinco suscriptores y espera a que cada uno esté consumiendo su cola.
4. Recién entonces muestra `Sistema listo` y comienza a mostrar los logs de todos los procesos.

Ctrl+C detiene ordenadamente los procesos que levantó y solo esos. Si un proceso falla al arrancar o termina inesperadamente, `levantar` detiene los demás y termina con error.

Para publicar todos los sismos del dataset: `python -m publisher.publish`.

## Verificación y demostración

### Resultado esperado de `sim-001`

Al publicar `sim-001` (evento ficticio cerca de Valparaíso), **todas** las ciudades reciben el evento, pero **solo Valparaíso, Coquimbo y Concepción** consultan el servicio HTTP:

```
[Arica] sim-001 a 1620.2 km (≥ 500 km): sin consulta HTTP → ack
[Coquimbo] sim-001 a 344.4 km: consulta HTTP 200 → M5.1 Mw, prof. 30.0 km, 2026-10-01T12:00:00Z, Evento ficticio de prueba cerca de Valparaíso → ack
[Valparaíso] sim-001 a 1.8 km: consulta HTTP 200 → M5.1 Mw, prof. 30.0 km, 2026-10-01T12:00:00Z, Evento ficticio de prueba cerca de Valparaíso → ack
[Concepción] sim-001 a 438.9 km: consulta HTTP 200 → M5.1 Mw, prof. 30.0 km, 2026-10-01T12:00:00Z, Evento ficticio de prueba cerca de Valparaíso → ack
[Punta Arenas] sim-001 a 2235.2 km (≥ 500 km): sin consulta HTTP → ack
```

En el log del servicio HTTP (líneas `[http]` en `levantar`) aparecen exactamente tres `GET /earthquakes/sim-001 ... 200 OK`.

Si el servicio HTTP está detenido, las ciudades interesadas registran el error de conexión y descartan el mensaje (`nack` sin reencolar). Los suscriptores siguen consumiendo.

### Ciudades que consultan HTTP para cada sismo del dataset

| id | Tipo | Ciudades a < 500 km |
|---|---|---|
| `csn-385887` | Real (36 km al SO de Linares) | Valparaíso, Concepción |
| `csn-385885` | Real (53 km al O de Arica) | Arica |
| `csn-385851` | Real (17 km al S de Huasco) | Coquimbo, Valparaíso (a 492,7 km) |
| `csn-385813` | Real (73 km al SE de Socaire) | ninguna |
| `sim-001` | Ficticio, cerca de Valparaíso | Valparaíso, Coquimbo, Concepción |
| `sim-002` | Ficticio, Aysén | ninguna |
| `sim-003` | Ficticio, cerca de Punta Arenas | Punta Arenas |
| `sim-004` | Ficticio, 490 km al S de Arica | Arica (bajo el umbral) |
| `sim-005` | Ficticio, 510 km al S de Arica | ninguna (sobre el umbral) |

### Consultas al servicio HTTP

```bash
curl -i http://localhost:8000/earthquakes/csn-385887   # 200 con el detalle
curl -i http://localhost:8000/earthquakes/csn-999999   # 404 {"detail": "Sismo no encontrado"}
curl -i http://localhost:8000/earthquakes/abc          # 422, formato de id inválido
```

La documentación interactiva queda en http://localhost:8000/docs.

### Pruebas automatizadas

No necesitan RabbitMQ ni el servicio HTTP en ejecución:

```bash
python -m unittest discover -s tests -v
```

### Ejecución manual por componente (depuración)

Como alternativa a `python -m levantar`, cada componente se puede iniciar en su propia terminal, con el entorno virtual activado. **No hay que mezclar ambas formas**: dos suscriptores de la misma ciudad se repartirían los mensajes de su cola.

```bash
uvicorn http_service.main:app                 # servicio HTTP
python -m subscriber.subscriber arica         # un suscriptor por terminal:
python -m subscriber.subscriber coquimbo      #   arica, coquimbo, valparaiso,
python -m subscriber.subscriber valparaiso    #   concepcion y punta_arenas
python -m subscriber.subscriber concepcion
python -m subscriber.subscriber punta_arenas
python -m publisher.publish sim-001           # publicar
```

- Cada suscriptor escribe `Esperando sismos en 'sismos.<ciudad>'` cuando está listo.
- Cada proceso se detiene con Ctrl+C.

## Detalles técnicos

- **Orden de arranque:** las colas deben existir **antes** de publicar, porque el exchange fanout descarta los mensajes cuando no hay colas enlazadas.
  - `python -m levantar` las declara automáticamente.
  - En la ejecución manual, se crean iniciando los suscriptores o con `python -m mensajeria`.
  - Las colas son durables y acumulan los mensajes mientras un suscriptor está detenido.
- **Configuración:**
  - RabbitMQ en `localhost:5672`, con usuario y clave `guest`/`guest`. El panel de administración queda en http://localhost:15672.
  - El servicio HTTP escucha en `127.0.0.1:8000`.
  - Los suscriptores consultan `http://localhost:8000`; para usar otra URL se define la variable de entorno `SISMOS_API_URL`.
- **RabbitMQ:**
  - La integración se probó con la imagen `rabbitmq:3.13-management`. No se probó con RabbitMQ 4.x.
  - Si aparece un error de permisos sobre `.erlang.cookie` (`eacces`), que ocurre en algunos entornos Docker, se puede agregar la opción `--user rabbitmq` al comando `docker run`.
  - Para eliminar el contenedor: `docker rm -f rabbitmq-sismos`.
- **Publisher:** valida el dataset completo antes de conectarse y solo publica ids que existen en él.
- **Windows:** el entorno virtual se activa con `.venv\Scripts\Activate.ps1` en PowerShell. `python -m levantar` no se probó en Windows.

## Consideraciones de la implementación

- Los datos reales (`csn-*`) se copiaron sin modificar de los informes del [Centro Sismológico Nacional](https://www.sismologia.cl) y cada uno enlaza su informe en `fuente_url`. Los eventos `sim-*` son ficticios y sirven para probar los distintos escenarios. Los ficticios están a más de 5 km del umbral de 500 km.
- El servicio HTTP mantiene los sismos en memoria y no ofrece un `POST`, porque el enunciado asume que la información ya está cargada.
- La distancia es geodésica sobre el elipsoide WGS84 (`geopy`) y el umbral es estricto: solo se consulta si la distancia es menor que 500 km.
- **Limitaciones de entrega:**
  - Las colas son durables y los mensajes persistentes, pero el publisher no usa *publisher confirms*. Si el broker falla durante el envío, puede quedar una publicación parcial.
  - Ante cualquier error (mensaje inválido, HTTP 404, servicio caído o timeout), el suscriptor descarta el mensaje con `nack` sin reencolar. No hay reintentos ni dead-letter queue.
  - Un mensaje que no alcanzó a confirmarse se vuelve a entregar, así que puede haber consultas y registros duplicados; no hay deduplicación.
  - El detalle está en [`DISCUSION.md`](DISCUSION.md), sección 1.
- **Timeout HTTP:** el suscriptor usa 5 s por operación de red (conectar o leer). No es un límite para la duración total de la petición ni para el procesamiento de un mensaje: cada suscriptor procesa los mensajes de a uno (`prefetch_count=1`), así que los siguientes esperan.
