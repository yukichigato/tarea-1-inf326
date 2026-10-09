# Tarea 1 INF326 — Notificación de sismos

Sistema de notificación de sismos con **publish-subscribe** (RabbitMQ) y **pull** por HTTP (FastAPI):

- `publisher/publish.py` publica un evento mínimo `{id, latitud, longitud}` por sismo en el exchange fanout `sismos`.
- `subscriber/subscriber.py` es un suscriptor por ciudad (Arica, Coquimbo, Valparaíso, Concepción y Punta Arenas), cada uno con su propia cola. Si el epicentro está a menos de 500 km, pide el detalle al servicio HTTP.
- `http_service/main.py` sirve el detalle en `GET /earthquakes/{id}` a partir de `data/sismos.json`, que se carga en memoria.

Los contratos entre componentes (dataset, mensaje, topología y endpoint) están en [`CONTRATOS.md`](CONTRATOS.md).

## Integrantes

| Nombre | Rol |
|---|---|
| _(completar)_ | _(completar)_ |
| _(completar)_ | _(completar)_ |

## Ejecución

### Prerrequisitos

- Python 3.10 o superior (probado con 3.12).
- Docker, para ejecutar RabbitMQ.
- Puertos libres: `5672` (AMQP), `15672` (panel de RabbitMQ) y `8000` (servicio HTTP).

Todos los comandos se ejecutan desde la raíz del repositorio. Están escritos para Linux, macOS o WSL. En Windows (PowerShell), el entorno virtual se activa con `.venv\Scripts\Activate.ps1`.

### 1. Instalar dependencias

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Iniciar RabbitMQ

```bash
docker run -d --name rabbitmq-sismos --user rabbitmq \
  -p 5672:5672 -p 15672:15672 rabbitmq:4-management
```

- RabbitMQ tarda unos segundos en aceptar conexiones. Con `docker logs rabbitmq-sismos` se puede esperar a que aparezca `Server startup complete`.
- El panel de administración queda en http://localhost:15672, con usuario y clave `guest`/`guest`.
- La opción `--user rabbitmq` evita un error de permisos sobre `.erlang.cookie` (`eacces`) que ocurre en algunos entornos Docker, como WSL.

### 3. Iniciar el servicio HTTP (terminal 1)

```bash
source .venv/bin/activate
uvicorn http_service.main:app
```

Para comprobarlo:

```bash
curl -i http://localhost:8000/earthquakes/csn-385887   # 200 con el detalle
curl -i http://localhost:8000/earthquakes/csn-999999   # 404 {"detail": "Sismo no encontrado"}
curl -i http://localhost:8000/earthquakes/abc          # 422, formato de id inválido
```

La documentación interactiva queda en http://localhost:8000/docs.

### 4. Iniciar los cinco suscriptores (terminales 2 a 6)

Se inicia uno por terminal:

```bash
source .venv/bin/activate
python -m subscriber.subscriber arica
python -m subscriber.subscriber coquimbo
python -m subscriber.subscriber valparaiso
python -m subscriber.subscriber concepcion
python -m subscriber.subscriber punta_arenas
```

Cada suscriptor escribe `Esperando sismos en 'sismos.<ciudad>'`. Por defecto consulta `http://localhost:8000`; para usar otra URL se define la variable de entorno `SISMOS_API_URL`.

**Importante:** los suscriptores deben iniciarse, al menos una vez, **antes** de publicar. El exchange fanout descarta los mensajes cuando todavía no hay colas enlazadas. También se puede crear la topología sin iniciar los suscriptores con `python -m mensajeria`. Las colas son durables y acumulan los mensajes mientras un suscriptor está detenido.

### 5. Publicar sismos (terminal 7)

```bash
source .venv/bin/activate
python -m publisher.publish            # publica todos los sismos de data/sismos.json
python -m publisher.publish sim-001    # publica solo los ids indicados
```

El publisher valida el dataset completo antes de conectarse y solo publica ids que existen en él.

### 6. Resultado esperado

Al publicar `sim-001` (evento ficticio cerca de Valparaíso), **todas** las ciudades reciben el evento, pero **solo Valparaíso, Coquimbo y Concepción** consultan el servicio HTTP:

```
[Arica] sim-001 a 1620.2 km (≥ 500 km): sin consulta HTTP → ack
[Coquimbo] sim-001 a 344.4 km: consulta HTTP 200 → M5.1 Mw, prof. 30.0 km, 2026-10-01T12:00:00Z, Evento ficticio de prueba cerca de Valparaíso → ack
[Valparaíso] sim-001 a 1.8 km: consulta HTTP 200 → M5.1 Mw, prof. 30.0 km, 2026-10-01T12:00:00Z, Evento ficticio de prueba cerca de Valparaíso → ack
[Concepción] sim-001 a 438.9 km: consulta HTTP 200 → M5.1 Mw, prof. 30.0 km, 2026-10-01T12:00:00Z, Evento ficticio de prueba cerca de Valparaíso → ack
[Punta Arenas] sim-001 a 2235.2 km (≥ 500 km): sin consulta HTTP → ack
```

En el log de uvicorn aparecen exactamente tres `GET /earthquakes/sim-001 ... 200 OK`.

Ciudades que consultan HTTP para cada sismo del dataset:

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

Si el servicio HTTP está detenido, las ciudades interesadas registran el error de conexión y descartan el mensaje (`nack` sin reencolar). Los suscriptores siguen consumiendo.

### 7. Pruebas automatizadas

No necesitan RabbitMQ ni el servicio HTTP en ejecución:

```bash
python -m unittest discover -s tests -v
```

### 8. Detener todo

Se detiene cada proceso con `Ctrl+C`. Después se elimina el contenedor:

```bash
docker rm -f rabbitmq-sismos
```

## Consideraciones de la implementación

- Los datos reales (`csn-*`) se copiaron sin modificar de los informes del [Centro Sismológico Nacional](https://www.sismologia.cl) y cada uno enlaza su informe en `fuente_url`. Los eventos `sim-*` son ficticios y sirven para probar los distintos escenarios. Los ficticios están a más de 5 km del umbral de 500 km.
- El servicio HTTP mantiene los sismos en memoria y no ofrece un `POST`, porque el enunciado asume que la información ya está cargada.
- La distancia es geodésica sobre el elipsoide WGS84 (`geopy`) y el umbral es estricto: solo se consulta si la distancia es menor que 500 km.
