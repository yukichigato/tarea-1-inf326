# Contratos de integración — Tarea 1 INF326

Este documento define los contratos entre los tres componentes del sistema:

- el dataset `data/sismos.json`,
- el mensaje que se publica en RabbitMQ,
- el endpoint HTTP `GET /earthquakes/{id}`.

Este documento no incluye la implementación de ningún componente.

## 0. Convenciones comunes

- **Codificación:** JSON en UTF-8.
- **Nombres de campos:** en español y `snake_case`. Son idénticos en el dataset, el mensaje y la respuesta HTTP.
- **Coordenadas:** grados decimales WGS84 de tipo `number`. El signo indica el hemisferio: latitud negativa = Sur, longitud negativa = Oeste.
- **Fechas:** texto en formato ISO 8601.
  - Fecha UTC: termina en `Z` (`YYYY-MM-DDTHH:MM:SSZ`).
  - Fecha local: lleva el desfase explícito (`YYYY-MM-DDTHH:MM:SS±HH:MM`).
- **Profundidad y distancias:** en kilómetros (`number`).

## 1. Dataset `data/sismos.json`

### 1.1 Datos que publica la fuente (CSN)

Las etiquetas de la tabla aparecen tal cual en un informe real del Centro Sismológico Nacional (evento 385887, https://www.sismologia.cl/sismicidad/informes/2026/10/385887.html):

| Etiqueta CSN | Valor publicado |
|---|---|
| Referencia | `36 km al SO de Linares` |
| Hora Local | `19:25:46 08/10/2026` |
| Hora UTC | `22:25:46 08/10/2026` |
| Latitud | `-36.10` |
| Longitud | `-71.84` |
| Profundidad | `74 km` |
| Magnitud | `2.5 MLv` (valor + escala) |

El CSN no publica un campo "id". El número `385887` solo aparece en la URL del informe (`/sismicidad/informes/AAAA/MM/NNNNNN.html`).

### 1.2 Formato propio del sistema

El archivo es un **arreglo JSON** de objetos (propuesta, ver sección 5). Cada `id` debe aparecer una sola vez.

| Campo | Tipo | Obligatorio | Unidad / formato | Origen |
|---|---|---|---|---|
| `id` | string | Sí | Patrón `^[a-z0-9-]{1,64}$`, distingue mayúsculas | **Propio del sistema** |
| `latitud` | number | Sí | Grados decimales, rango [-90, 90] | CSN "Latitud" |
| `longitud` | number | Sí | Grados decimales, rango [-180, 180] | CSN "Longitud" |
| `fecha_utc` | string | Sí | `YYYY-MM-DDTHH:MM:SSZ` | CSN "Hora UTC", reformateada a ISO 8601 |
| `profundidad_km` | number | Sí | km, ≥ 0 | CSN "Profundidad", sin el texto " km" |
| `magnitud` | number | Sí | Sin unidad | Parte numérica de CSN "Magnitud" |
| `tipo_magnitud` | string | Sí | Escala tal cual (`MLv`, `Mw`, `Ml`, …) | Sufijo de CSN "Magnitud" |
| `referencia` | string | Sí | Texto libre | CSN "Referencia" |
| `fecha_local` | string | No | `YYYY-MM-DDTHH:MM:SS±HH:MM` | CSN "Hora Local" + desfase horario (el desfase lo agrega el sistema) |
| `fuente_url` | string | No | URL absoluta del informe | **Propio del sistema** (enlace al CSN) |

### 1.3 Datos reales y datos ficticios

- **Reales:** su `id` comienza con `csn-` y sus valores provienen del informe CSN indicado en `fuente_url`.
- **Ficticios (de prueba):** su `id` comienza con `sim-`. No tienen `fuente_url`, y su `referencia` debe indicar explícitamente que el evento es ficticio.

### 1.4 Ejemplo

El primer registro es **real**: es el evento 385887 del CSN. El segundo es **ficticio** y muestra que los campos opcionales pueden omitirse.

```json
[
  {
    "id": "csn-385887",
    "latitud": -36.10,
    "longitud": -71.84,
    "fecha_utc": "2026-10-08T22:25:46Z",
    "fecha_local": "2026-10-08T19:25:46-03:00",
    "profundidad_km": 74,
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
    "profundidad_km": 30,
    "magnitud": 5.1,
    "tipo_magnitud": "Mw",
    "referencia": "Evento ficticio de prueba cerca de Valparaíso"
  }
]
```

## 2. Mensaje RabbitMQ

El cuerpo del mensaje es un objeto JSON con **exactamente** tres campos. El publisher lo envía con `content_type = application/json`.

| Campo | Tipo | Significado | Unidad |
|---|---|---|---|
| `id` | string | Identificador del sismo; es el mismo `id` del dataset y del endpoint HTTP | — |
| `latitud` | number | Latitud del epicentro | Grados decimales, rango [-90, 90] |
| `longitud` | number | Longitud del epicentro | Grados decimales, rango [-180, 180] |

Ejemplo:

```json
{"id": "csn-385887", "latitud": -36.10, "longitud": -71.84}
```

El mensaje no incluye magnitud, fechas, profundidad ni referencia. Esos datos se obtienen por HTTP.

## 3. Servicio HTTP

### `GET /earthquakes/{id}`

**Parámetro `{id}`:** string. Es el identificador del sismo y debe cumplir el patrón del dataset `^[a-z0-9-]{1,64}$`.

| Caso | Código | Cuerpo |
|---|---|---|
| El id existe | `200` | Objeto con los 10 campos del dataset (ver abajo) |
| El formato es válido, pero el id no existe | `404` | `{"detail": "Sismo no encontrado"}` |
| El formato es inválido (por ejemplo `ABC!`) | `422` | Cuerpo de validación por defecto de FastAPI: `{"detail": [ ... ]}` (propuesta, ver sección 5) |

**Respuesta `200`:**

- Siempre contiene los **10 campos** de la sección 1.2, con los mismos nombres, tipos y unidades que el dataset.
- Si un campo opcional (`fecha_local`, `fuente_url`) no está en el dataset, se devuelve con valor `null`; nunca se omite.
- Así la respuesta tiene siempre la misma forma y el suscriptor no necesita comprobar si una clave existe.

**Ejemplo con un sismo real:**

```
GET /earthquakes/csn-385887
→ 200 OK
```

```json
{
  "id": "csn-385887",
  "latitud": -36.10,
  "longitud": -71.84,
  "fecha_utc": "2026-10-08T22:25:46Z",
  "fecha_local": "2026-10-08T19:25:46-03:00",
  "profundidad_km": 74,
  "magnitud": 2.5,
  "tipo_magnitud": "MLv",
  "referencia": "36 km al SO de Linares",
  "fuente_url": "https://www.sismologia.cl/sismicidad/informes/2026/10/385887.html"
}
```

**Ejemplo con un sismo ficticio (los opcionales van en `null`):**

```
GET /earthquakes/sim-001
→ 200 OK
```

```json
{
  "id": "sim-001",
  "latitud": -33.05,
  "longitud": -71.62,
  "fecha_utc": "2026-10-01T12:00:00Z",
  "fecha_local": null,
  "profundidad_km": 30,
  "magnitud": 5.1,
  "tipo_magnitud": "Mw",
  "referencia": "Evento ficticio de prueba cerca de Valparaíso",
  "fuente_url": null
}
```

**Ejemplo de error (id inexistente):**

```
GET /earthquakes/csn-999999
→ 404 Not Found
```

```json
{"detail": "Sismo no encontrado"}
```

**Comportamiento del suscriptor:**

- Con `200`, usa los datos de la respuesta.
- Con cualquier otro código, registra el error y descarta el sismo sin reintentar.

## 4. Reglas de consistencia

1. El `id` es la misma cadena exacta en el dataset, el mensaje y la ruta HTTP. No se transforma ni cambia de mayúsculas/minúsculas, y es único dentro del dataset.
2. El publisher solo publica ids que existen en `data/sismos.json`.
3. Los valores de `latitud` y `longitud` del mensaje son idénticos a los del registro correspondiente en el dataset.
4. La respuesta `200` es el registro del dataset con los opcionales ausentes completados con `null`. No hay campos renombrados, derivados ni duplicados.
5. Todas las coordenadas usan grados decimales con signo según el hemisferio. Todas las profundidades y distancias se expresan en km.
6. El umbral de distancia (estrictamente menor que 500 km) se aplica solo en el suscriptor y no forma parte de ningún contrato.

## 5. Decisiones pendientes

Las opciones marcadas como **propuesta** son las que asume este documento mientras el equipo no confirme otra cosa.

1. **Formato del `id`.**
   - **Propuesta:** `csn-<número del informe CSN>` para datos reales y `sim-NNN` para datos ficticios.
   - Alternativa: usar solo el número del CSN (`"385887"`). Con esta opción no se pueden distinguir los datos ficticios.
2. **Respuesta ante un id con formato inválido.**
   - **Propuesta:** `422`, el comportamiento por defecto de FastAPI.
   - Alternativa: devolver `404` también en este caso. Es más simple para el suscriptor.
3. **Estructura raíz del archivo.**
   - **Propuesta:** un arreglo JSON.
   - Alternativa: `{"sismos": [...]}`, si se quiere agregar metadatos más adelante.
4. **Idioma.** La ruta está en inglés (`/earthquakes`, viene del enunciado) y los campos están en español. Falta confirmar si esta mezcla es aceptable.
5. **Infraestructura.** Faltan por definir:
   - el nombre del exchange `fanout`,
   - los nombres de las cinco colas,
   - la URL base del servicio. **Propuesta:** `http://localhost:8000`, el puerto por defecto de uvicorn.
6. **Coordenadas de las ciudades.** Faltan las coordenadas de referencia de Arica, Coquimbo, Valparaíso, Concepción y Punta Arenas. Se fijan en los suscriptores y no forman parte de estos contratos.
