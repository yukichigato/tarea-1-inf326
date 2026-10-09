# Discusión — Tarea 1 INF326

## 1. Trade-offs de la arquitectura

_(Sección a cargo de la persona B.)_

## 2. Back of the Envelope

El enunciado pide **no calcular**, sino explicar cómo se podría estimar el uso del sistema con lo que se sabe del fenómeno (los sismos en Chile) y cómo esa estimación justifica la arquitectura. Por eso esta sección define qué cantidades hay que estimar, de dónde salen los datos y cómo se combinan. No entrega cifras.

### 2.1 Qué se quiere estimar y para qué

| Pregunta | Componente que dimensiona |
|---|---|
| ¿Cuántos eventos por segundo publica el sistema, en promedio y en el peor caso? | Publisher y RabbitMQ |
| ¿Cuántos bytes ocupa cada mensaje y cuántas copias se mueven? | RabbitMQ y la red |
| ¿Qué fracción de los eventos interesa a cada ciudad? | Suscriptores |
| ¿Cuántas solicitudes HTTP llegan al servicio y de qué tamaño? | Servicio HTTP y la red |
| ¿Cuánto se acumula en las colas si un componente falla o se satura? | Durabilidad de RabbitMQ |

### 2.2 Datos de entrada sobre el fenómeno

- **Catálogo sísmico del CSN** (https://www.sismologia.cl). Lista cada evento con fecha, coordenadas, profundidad y magnitud, que son los mismos campos de nuestro dataset. Con él se puede medir:
  - la frecuencia de eventos por día, mes o año;
  - su distribución geográfica;
  - su distribución por magnitud.
- **Ley de Gutenberg-Richter** (`log10 N(≥M) = a − b·M`). Relaciona la cantidad de sismos con su magnitud. Si el sistema notificara solo desde una magnitud mínima, esta ley permite estimar cuántos eventos quedan, ajustando `a` y `b` con el catálogo.
- **Ley de Omori** (`n(t) = K / (c + t)^p`). Describe cómo decae la tasa de réplicas después de un sismo grande. Sirve para estimar el **pico** de actividad, que es muy superior al promedio. Se puede calibrar con secuencias históricas del catálogo, por ejemplo las réplicas de Maule 2010, Iquique 2014 o Illapel 2015.
- **Datos fijos del sistema:** las 5 ciudades con sus coordenadas, el umbral de 500 km y el contrato de los mensajes (`CONTRATOS.md`).

### 2.3 Variables y cómo se estiman

| Símbolo | Significado | Cómo se obtiene |
|---|---|---|
| `λ` | Eventos publicados por segundo (promedio) | Eventos del catálogo en un período ÷ duración del período |
| `λ_pico` | Eventos por segundo en alta actividad | Máximo de eventos por minuto u hora en secuencias de réplicas (Omori o catálogo) |
| `S_msg` | Tamaño de un mensaje AMQP | Bytes del JSON `{id, latitud, longitud}` más encabezados y propiedades AMQP; se mide con `len(json.dumps(evento).encode())` y en el panel de RabbitMQ |
| `N_s` | Número de suscriptores (colas) | 5, fijo |
| `p_c` | Fracción de eventos a menos de 500 km de la ciudad `c` | Se recorre el catálogo histórico y se cuenta, con la misma distancia geodésica del suscriptor, cuántos eventos quedan bajo el umbral |
| `S_req`, `S_resp` | Tamaño de la solicitud y de la respuesta HTTP | Se miden en una petición real a `GET /earthquakes/{id}` (cuerpo de 10 campos más encabezados HTTP y TCP) |
| `t_proc` | Tiempo de procesamiento de un mensaje en un suscriptor | Se mide en los logs: cálculo de distancia más, si corresponde, el tiempo de la petición HTTP (con un tope de 5 s por timeout) |

### 2.4 Cómo se combinan

**Mensajería (RabbitMQ).**

- El publisher envía `λ` mensajes por segundo.
- El exchange fanout los copia a cada cola, así que el broker entrega `N_s · λ` mensajes por segundo.
- Tráfico AMQP aproximado: `(1 + N_s) · λ · S_msg`.

**Solicitudes HTTP.** Cada suscriptor consulta solo una fracción de los eventos:

```
R = λ · Σ_c p_c        (solicitudes HTTP por segundo)
B_http = R · (S_req + S_resp)
```

- Como cada `p_c` es menor o igual que 1, siempre se cumple `R ≤ N_s · λ`.
- La geografía acota aún más el peor caso por evento, y se puede verificar solo con las coordenadas de las ciudades:
  - Dos ciudades solo pueden compartir un evento si están a menos de 1000 km entre sí (dos radios de 500 km), por desigualdad triangular.
  - Arica y Punta Arenas están a más de 1000 km de todas las demás, así que nunca comparten un evento.
  - Por lo tanto, un evento interesa como mucho a 3 ciudades (Coquimbo, Valparaíso y Concepción), como ocurre con `sim-001`.

**Capacidad de los suscriptores.**

- Con `prefetch_count=1`, cada suscriptor procesa un mensaje a la vez, así que su capacidad es de unos `1 / t_proc` mensajes por segundo.
- Mientras `λ_pico < 1 / t_proc`, las colas no crecen.
- Si se supera ese valor, la cola crece aproximadamente a razón de `λ_pico − 1/t_proc`.
- El peor caso de `t_proc` es el timeout HTTP de 5 s.

**Acumulación durante fallas.**

- Si el suscriptor de la ciudad `c` está detenido durante `T` segundos, su cola durable acumula `λ · T` mensajes, que ocupan `λ · T · S_msg` bytes.
- Esto permite estimar el espacio en disco del broker para tolerar caídas de una duración dada.

### 2.5 Promedio vs. alta actividad sísmica

El promedio no basta para dimensionar el sistema, porque los sismos llegan agrupados en el tiempo y en el espacio:

- **Temporal.** Después de un sismo grande, la tasa de réplicas puede superar en órdenes de magnitud a la de un día normal y decae en horas o días (Omori). El sistema debe dimensionarse con `λ_pico` y no con `λ`. Las colas durables absorben ráfagas cortas aunque los suscriptores sean más lentos por un rato.
- **Espacial.** Las réplicas se concentran cerca del epicentro principal. Durante una secuencia, `p_c` se acerca a 1 en las ciudades cercanas y a 0 en las lejanas. Por eso la carga HTTP del peor caso es `λ_pico` multiplicado por la cantidad de ciudades cercanas a esa zona, que es como mucho 3 y no 5.
- **Correlación con el interés.** Los momentos de mayor carga coinciden con los momentos en que la información más importa. Por eso la estimación del pico, y no la del promedio, debe guiar el timeout, la política de reintentos y la capacidad del servicio HTTP.

### 2.6 Cómo esta estimación justifica la arquitectura

- **Evento mínimo + pull por HTTP.** Si cada mensaje llevara el detalle completo (tamaño `S_full`), el broker movería `N_s · λ · S_full`. Con el evento mínimo mueve `N_s · λ · S_msg + R · (S_req + S_resp)`. El diseño conviene cuando `Σ_c p_c` es bajo, es decir, cuando la mayoría de las ciudades descarta la mayoría de los eventos. Eso se comprueba con el `p_c` medido en el catálogo. Su costo es una petición HTTP adicional, con su latencia, para las ciudades interesadas.
- **Filtrado local en el suscriptor.** Con `p_c` se estima cuántas peticiones HTTP se evitan, frente a que todos los suscriptores consultaran siempre: `N_s · λ − R`.
- **Un broker y un servicio HTTP únicos.** Si `λ_pico` y `R_pico` resultan pequeños frente a la capacidad de una instancia de RabbitMQ y de uvicorn, que se puede medir con una prueba de carga, no se justifica replicar ni particionar. Si la estimación diera lo contrario, el mismo cálculo indicaría qué escalar primero. Si domina `N_s · λ`, conviene repartir las colas. Si domina `R`, conviene replicar o poner caché en el servicio HTTP, ya que los datos de un sismo no cambian una vez publicados.
- **Más ciudades.** `N_s` aparece multiplicando el tráfico del broker. Si el sistema creciera a muchas más ciudades, convendría pasar de fanout a un exchange `topic` con routing por región, para que cada suscriptor reciba solo los eventos de su zona.

### 2.7 Cómo obtener las mediciones en este sistema

- **`S_msg` y `S_resp`:** medir con `len(...)` sobre el JSON publicado y con `curl -s -o /dev/null -w '%{size_download}' http://localhost:8000/earthquakes/<id>`.
- **`λ` y las tasas por cola:** se ven en el panel de RabbitMQ (http://localhost:15672). También se pueden simular publicando el dataset repetidas veces.
- **`R` real:** contar las líneas `GET /earthquakes/...` del log de uvicorn y compararlas con `λ · Σ p_c`.
- **`p_c`:** descargar el catálogo del CSN de un período y aplicar `subscriber.ciudades.distancia_km` y `es_de_interes` a cada evento.
