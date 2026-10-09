# Contratos de integración — Tarea 1 INF326

Este documento define los contratos entre los componentes del sistema:

- el dataset `data/sismos.json`,
- el mensaje que se publica en RabbitMQ y la topología de RabbitMQ,
- el endpoint HTTP `GET /earthquakes/{id}`.

Este documento no incluye la implementación de ningún componente. Cada sección indica quién produce y quién consume cada dato, para que el publisher/servicio HTTP y los suscriptores puedan implementarse en paralelo.

## 0. Convenciones comunes

- **Codificación:** JSON en UTF-8.
- **Nombres de campos:** en español y `snake_case`. Son idénticos en el dataset, el mensaje y la respuesta HTTP. El orden de las claves en un objeto JSON no es significativo.
- **Coordenadas:** grados decimales WGS84, siempre como número JSON (nunca como string). El signo indica el hemisferio: latitud negativa = Sur, longitud negativa = Oeste.
- **Números:** se comparan por su valor numérico, no por cómo están escritos. `74`, `74.0` y `74.00` representan el mismo valor; también `-36.10` y `-36.1`. Los consumidores deben tratar `latitud`, `longitud`, `profundidad_km` y `magnitud` como `float`.
- **Fechas:** texto en formato ISO 8601.
  - Fecha UTC: termina en `Z` (`YYYY-MM-DDTHH:MM:SSZ`).
  - Fecha local: lleva el desfase explícito (`YYYY-MM-DDTHH:MM:SS±HH:MM`).
- **Profundidad y distancias:** en kilómetros.

## 1. Dataset `data/sismos.json`

El servicio HTTP y el publisher leen este archivo. Ningún componente lo modifica.

### 1.1 Datos que publica la fuente (CSN)

Las etiquetas y los valores de la tabla se copiaron literalmente del informe del Centro Sismológico Nacional para el evento 385887 (https://www.sismologia.cl/sismicidad/informes/2026/10/385887.html), consultado el 2026-10-08:

| Etiqueta CSN | Valor publicado |
|---|---|
| Referencia | `36 km al SO de Linares` |
| Hora Local | `19:25:46 08/10/2026` |
| Hora UTC | `22:25:46 08/10/2026` |
| Latitud | `-36.10` |
| Longitud | `-71.84` |
| Profundidad | `74 km` |
| Magnitud | `2.5 MLv` (valor + escala) |

- El CSN no publica un campo "id". El número `385887` solo aparece en la URL del informe (`/sismicidad/informes/AAAA/MM/NNNNNN.html`).
- El informe también incluye un campo "Observaciones", que el sistema no utiliza.
- El CSN puede revisar posteriormente los valores de un informe. El dataset guarda los valores vigentes en la fecha de consulta.

### 1.2 Formato propio del sistema

La raíz del archivo es un **arreglo JSON** de objetos. Cada `id` aparece una sola vez.

| Campo | Tipo JSON | Obligatorio | Unidad / formato | Origen |
|---|---|---|---|---|
| `id` | string | Sí | Patrón `^(csn\|sim)-[0-9]{1,9}$` (ver 1.3) | **Propio del sistema** |
| `latitud` | number | Sí | Grados decimales, rango [-90, 90] | CSN "Latitud" |
| `longitud` | number | Sí | Grados decimales, rango [-180, 180] | CSN "Longitud" |
| `fecha_utc` | string | Sí | `YYYY-MM-DDTHH:MM:SSZ` | CSN "Hora UTC", reformateada a ISO 8601 |
| `profundidad_km` | number | Sí | km, ≥ 0 | CSN "Profundidad", sin el texto " km" |
| `magnitud` | number | Sí | Sin unidad | Parte numérica de CSN "Magnitud" |
| `tipo_magnitud` | string | Sí | Escala tal cual (`MLv`, `Mw`, `Ml`, …) | Sufijo de CSN "Magnitud" |
| `referencia` | string | Sí | Texto libre | CSN "Referencia" |
| `fecha_local` | string | No | `YYYY-MM-DDTHH:MM:SS±HH:MM` | CSN "Hora Local" + desfase horario (el desfase lo agrega el sistema) |
| `fuente_url` | string | No | URL absoluta del informe | **Propio del sistema** (enlace al CSN) |

En el dataset, un campo opcional se **omite** cuando no tiene valor; no se escribe `null`.

### 1.3 Identificador y origen de los datos

El patrón exacto del `id` es `^(csn|sim)-[0-9]{1,9}$`: solo minúsculas, un prefijo, un guion y entre 1 y 9 dígitos.

- **Datos reales** (`csn-<número del informe CSN>`, por ejemplo `csn-385887`):
  - Sus valores se copian del informe CSN, que se indica en `fuente_url`.
  - No se permite inventar ni ajustar ningún valor.
- **Datos ficticios de prueba** (`sim-<número>`, por ejemplo `sim-001`):
  - No llevan `fuente_url`.
  - Su `referencia` debe decir explícitamente que el evento es ficticio.
  - Se usan, por ejemplo, para forzar casos a menos y a más de 500 km de cada ciudad.

### 1.4 Ejemplo

- El primer registro es **real**: es el evento 385887 del CSN y sus valores corresponden a la tabla 1.1.
- El segundo es **ficticio** y omite los campos opcionales.

```json
[
  {
    "id": "csn-385887",
    "latitud": -36.1,
    "longitud": -71.84,
    "fecha_utc": "2026-10-08T22:25:46Z",
    "fecha_local": "2026-10-08T19:25:46-03:00",
    "profundidad_km": 74.0,
    "magnitud": 2.5,
    "tipo_magnitud": "MLv",
    "referencia": "36 km al SO de Linares",
    "fuente_url": "https://www.sismologia.cl/sismicidad/informes/2026/10/385887.html"
  },
  {
    "id": "sim-001",
    "latitud": -33.05,
    "longitud": -71.62,
    "fecha_utc": "2026-10-01T12:00:00Z",
    "profundidad_km": 30.0,
    "magnitud": 5.1,
    "tipo_magnitud": "Mw",
    "referencia": "Evento ficticio de prueba cerca de Valparaíso"
  }
]
```

## 2. RabbitMQ

### 2.1 Topología

| Elemento | Valor |
|---|---|
| Broker | `localhost:5672`, usuario/clave `guest`/`guest` (valores por defecto de RabbitMQ) |
| Exchange | Nombre `sismos`, tipo `fanout`, `durable=True` |
| Routing key | `""` (cadena vacía; el exchange `fanout` la ignora) |
| Colas | Una por ciudad (ver 2.3); todas con `durable=True`, `exclusive=False`, `auto_delete=False` |
| Binding | Cada cola se enlaza al exchange `sismos` |

**Quién declara qué:**

- El publisher declara el exchange.
- Cada suscriptor declara el exchange, su propia cola y el binding.
- Ambos deben declarar el exchange con exactamente los mismos parámetros, y cada cola debe declararse siempre con los mismos parámetros. Si no coinciden, RabbitMQ rechaza la declaración con `PRECONDITION_FAILED`.
- Los nombres de las colas no usan tildes.

**Orden de arranque:** un exchange `fanout` descarta los mensajes cuando no tiene colas enlazadas. Por eso los cinco suscriptores deben haberse iniciado al menos una vez (para declarar sus colas) antes de que el publisher publique. Una vez creadas, las colas durables siguen existiendo aunque el suscriptor se detenga, y acumulan los mensajes hasta que vuelva.

### 2.2 Mensaje

El cuerpo del mensaje es un objeto JSON codificado en UTF-8, con **exactamente** tres campos. El publisher lo envía con estas propiedades:

- `content_type = "application/json"`,
- `delivery_mode = 2` (mensaje persistente; en `pika`, `pika.DeliveryMode.Persistent`).

| Campo | Tipo JSON | Significado | Unidad |
|---|---|---|---|
| `id` | string | Identificador del sismo; es el mismo `id` del dataset y de la ruta HTTP | Patrón de 1.3 |
| `latitud` | number | Latitud del epicentro | Grados decimales, rango [-90, 90] |
| `longitud` | number | Longitud del epicentro | Grados decimales, rango [-180, 180] |

Ejemplo:

```json
{"id": "csn-385887", "latitud": -36.1, "longitud": -71.84}
```

El mensaje no incluye magnitud, fechas, profundidad ni referencia. Esos datos se obtienen por HTTP.

Un mensaje se considera **mal formado** en cualquiera de estos casos:

- el cuerpo no es un objeto JSON válido en UTF-8;
- falta alguno de los tres campos;
- un campo tiene un tipo distinto al de la tabla;
- `latitud` o `longitud` están fuera de su rango.

### 2.3 Suscriptores y ciudades

Hay un suscriptor por ciudad. Cada suscriptor consume solo su cola y usa solo las coordenadas de su ciudad, que son fijas y se definen en esta tabla:

| Ciudad | Cola | Latitud | Longitud |
|---|---|---|---|
| Arica | `sismos.arica` | -18.4746 | -70.29792 |
| Coquimbo | `sismos.coquimbo` | -29.95332 | -71.33947 |
| Valparaíso | `sismos.valparaiso` | -33.036 | -71.62963 |
| Concepción | `sismos.concepcion` | -36.82699 | -73.04977 |
| Punta Arenas | `sismos.punta_arenas` | -53.16282 | -70.90922 |

**Filtro por distancia:**

- El suscriptor calcula la distancia geodésica, en km y sobre el elipsoide WGS84, entre el epicentro del mensaje y su ciudad. Por ejemplo, con `geopy.distance.geodesic`.
- Consulta HTTP **solo si la distancia es estrictamente menor que 500 km**. Si la distancia es de 500 km o más, no consulta.
- Distintos métodos de cálculo (por ejemplo, haversine sobre una esfera) pueden diferir en unos pocos km. Por eso los casos de prueba ficticios no deben ubicarse a menos de 5 km del umbral de 500 km.

### 2.4 Confirmación de mensajes (`ack`/`nack`)

- Los suscriptores consumen con **confirmación manual** (`auto_ack=False`).
- Cada mensaje se confirma una sola vez, al terminar de procesarlo.
- **Ningún mensaje se reencola nunca** (`requeue=True` está prohibido), para que ningún mensaje quede reintentándose indefinidamente.

| Situación | Acción del suscriptor | Confirmación |
|---|---|---|
| Distancia ≥ 500 km | No consulta HTTP | `basic_ack` |
| Distancia < 500 km y HTTP `200` | Usa los detalles del sismo | `basic_ack` |
| Mensaje mal formado (ver 2.2) | Registra el error | `basic_nack(requeue=False)` |
| HTTP `404` o `422` | Registra el error | `basic_nack(requeue=False)` |
| Cualquier otro código HTTP, error de conexión o timeout (límite de **5 s** por petición) | Registra el error | `basic_nack(requeue=False)` |
| Excepción inesperada al procesar | Registra el error; el suscriptor sigue consumiendo | `basic_nack(requeue=False)` |

Como no se configura un dead-letter exchange, `basic_nack(requeue=False)` **elimina** el mensaje. El único registro del mensaje que queda es el log del suscriptor. En los casos de `basic_ack` y de `basic_nack` el resultado es el mismo para la cola (el mensaje sale de ella); la diferencia solo indica si el procesamiento terminó bien.

### 2.5 Persistencia y garantías

La configuración mínima del sistema tiene tres niveles de persistencia, y cada uno cubre algo distinto:

| Configuración | Qué sobrevive a un reinicio del broker |
|---|---|
| Exchange `durable=True` | La definición del exchange |
| Colas `durable=True` | La definición de las colas y sus bindings |
| Mensajes `delivery_mode=2` | Los mensajes que ya estaban en una cola durable y que el broker alcanzó a escribir en disco |

**Lo que esta configuración no garantiza** (se acepta para el alcance de la tarea):

- **Mensajes en tránsito:** no se usan *publisher confirms*. Un mensaje publicado justo antes de una caída del broker puede perderse sin que el publisher lo sepa.
- **Mensajes sin colas:** los mensajes publicados antes de que existan las colas se pierden (ver "Orden de arranque" en 2.1).
- **Duplicados:** si un suscriptor se cae después de procesar un mensaje pero antes de confirmarlo, RabbitMQ se lo vuelve a entregar. El suscriptor puede procesar el mismo sismo dos veces; para esta tarea se acepta.
- **Fallas descartadas:** los mensajes descartados por un fallo HTTP no se recuperan.

## 3. Servicio HTTP

**URL base:** `http://localhost:8000` (puerto por defecto de uvicorn).

### `GET /earthquakes/{id}`

**Parámetro `{id}`:** string de la ruta. Es el identificador del sismo y debe cumplir el patrón `^(csn|sim)-[0-9]{1,9}$`.

| Caso | Código | Cuerpo |
|---|---|---|
| El id existe | `200` | Objeto con los 10 campos del dataset (ver abajo) |
| El formato es válido, pero el id no existe (por ejemplo `csn-999999`) | `404` | `{"detail": "Sismo no encontrado"}` |
| El formato es inválido (por ejemplo `abc`, `CSN-385887`, `csn-`) | `422` | Cuerpo de validación por defecto de FastAPI: `{"detail": [ ... ]}` |

**Validación que debe implementarse en FastAPI:**

- **422 por formato inválido.** El código `422` solo se produce si el patrón se declara en el parámetro de ruta, por ejemplo:

  ```python
  id: str = Path(pattern=r"^(csn|sim)-[0-9]{1,9}$")
  ```

  - En versiones de FastAPI anteriores a 0.100 el argumento se llama `regex=` en vez de `pattern=`.
  - Si el patrón no se declara, un id mal formado llega al handler y termina en `404`, lo que incumple este contrato.
- **404 por id inexistente.** Se produce con `raise HTTPException(status_code=404, detail="Sismo no encontrado")`. FastAPI lo serializa exactamente como `{"detail": "Sismo no encontrado"}`.
- **Rutas que FastAPI ya responde con `404`:** `/earthquakes/` (sin id) y cualquier id que contenga `/` no coinciden con la ruta, así que FastAPI devuelve `404` por sí mismo. No hace falta manejar estos casos.

**Respuesta `200`:**

- Tiene `Content-Type: application/json`.
- Siempre contiene los **10 campos** de la sección 1.2, con los mismos nombres, tipos y unidades que el dataset.
- Si un campo opcional (`fecha_local`, `fuente_url`) está omitido en el dataset, la respuesta lo incluye con valor `null`; nunca lo omite.
- Así la respuesta tiene siempre la misma forma y el suscriptor no necesita comprobar si una clave existe.

**Ejemplo con un sismo real:**

```
GET http://localhost:8000/earthquakes/csn-385887
→ 200 OK
```

```json
{
  "id": "csn-385887",
  "latitud": -36.1,
  "longitud": -71.84,
  "fecha_utc": "2026-10-08T22:25:46Z",
  "fecha_local": "2026-10-08T19:25:46-03:00",
  "profundidad_km": 74.0,
  "magnitud": 2.5,
  "tipo_magnitud": "MLv",
  "referencia": "36 km al SO de Linares",
  "fuente_url": "https://www.sismologia.cl/sismicidad/informes/2026/10/385887.html"
}
```

**Ejemplo con un sismo ficticio (los opcionales van en `null`):**

```
GET http://localhost:8000/earthquakes/sim-001
→ 200 OK
```

```json
{
  "id": "sim-001",
  "latitud": -33.05,
  "longitud": -71.62,
  "fecha_utc": "2026-10-01T12:00:00Z",
  "fecha_local": null,
  "profundidad_km": 30.0,
  "magnitud": 5.1,
  "tipo_magnitud": "Mw",
  "referencia": "Evento ficticio de prueba cerca de Valparaíso",
  "fuente_url": null
}
```

**Ejemplo de error por id inexistente:**

```
GET http://localhost:8000/earthquakes/csn-999999
→ 404 Not Found
```

```json
{"detail": "Sismo no encontrado"}
```

**Ejemplo de error por formato inválido:**

```
GET http://localhost:8000/earthquakes/abc
→ 422 Unprocessable Entity
```

Cuerpo: el de validación por defecto de FastAPI. Su contenido exacto depende de la versión y el suscriptor no lo interpreta.

**Comportamiento del suscriptor:**

- Con `200`, usa los datos de la respuesta.
- Con cualquier otro código, con un error de conexión o con un timeout de 5 s, registra el error y descarta el mensaje sin reintentar.
- La confirmación del mensaje en cada caso se define en 2.4.

## 4. Reglas de consistencia

1. El `id` es la misma cadena exacta en el dataset, el mensaje y la ruta HTTP. No se transforma, y es único dentro del dataset.
2. El publisher solo publica ids que existen en `data/sismos.json`. Por lo tanto, un `404` en la integración indica un error del publisher o del dataset.
3. El publisher toma `latitud` y `longitud` del registro del dataset sin redondear ni transformar.
4. La respuesta `200` es el registro del dataset con los opcionales omitidos completados con `null`. No hay campos renombrados, derivados ni duplicados.
5. Todas las coordenadas usan grados decimales con signo según el hemisferio. Todas las profundidades y distancias se expresan en km.
6. Cada suscriptor usa las coordenadas de su ciudad definidas en 2.3 y consulta HTTP solo si la distancia geodésica es estrictamente menor que 500 km.

## 5. Decisiones

### 5.1 Cerradas en esta revisión

| Tema | Decisión | Motivo |
|---|---|---|
| Formato del `id` | `^(csn\|sim)-[0-9]{1,9}$` | Distingue datos reales de ficticios y permite validar el formato de forma simple |
| Id con formato inválido | `422`, declarando el patrón en `Path` | Es el comportamiento estándar de FastAPI y lo distingue de un id inexistente |
| Raíz del dataset | Arreglo JSON | Es lo más simple; no se necesitan metadatos |
| Idioma | Ruta en inglés (viene del enunciado) y campos en español | La ruta la fija el enunciado; los campos mantienen los nombres acordados |
| Topología RabbitMQ | Sección 2.1 | Ambos lados necesitan nombres y parámetros idénticos |
| URL base HTTP | `http://localhost:8000` | Puerto por defecto de uvicorn |
| Coordenadas de las ciudades | Tabla 2.3 | Las fija el equipo; todos los suscriptores usan la misma fuente |
| Confirmación de mensajes | `ack` si el procesamiento termina bien; `nack(requeue=False)` en cualquier error (2.4) | Ningún mensaje se reintenta indefinidamente |
| Persistencia | Exchange y colas durables, mensajes persistentes, sin *publisher confirms* (2.5) | Configuración mínima con garantías conocidas |

### 5.2 Por confirmar con el equipo

1. **Enunciado de la tarea.** Verificar que el enunciado no exija nombres concretos de exchange, colas, puertos o campos. Si los exige, esos nombres reemplazan a los de este documento.
