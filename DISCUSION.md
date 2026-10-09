# Discusión — Tarea 1 INF326

## 1. Trade-offs de la arquitectura

Cada decisión se describe con:

- la alternativa descartada,
- lo que se gana,
- lo que se acepta perder,
- si ese costo es de consideración en el dominio: avisos de sismos a cinco ciudades.

Las garantías descritas son las de la implementación actual (`CONTRATOS.md`, secciones 2.4 y 2.5). Lo marcado como **mejora posible** no está implementado.

### 1.1 Evento mínimo y detalle por HTTP (pull)

- **Decisión:** el publisher envía solo `{id, latitud, longitud}`. Cada suscriptor interesado pide el detalle con `GET /earthquakes/{id}`.
- **Alternativa:** publicar el registro completo del sismo en el mensaje.
- **Ventajas:**
  - El mensaje lleva solo lo que todos los suscriptores usan: identificar el sismo y calcular la distancia.
  - Las ciudades lejanas, que son la mayoría para cada evento, no reciben datos que no usarían.
  - El detalle tiene una sola fuente, el servicio HTTP.
- **Costos:**
  - Cada ciudad interesada hace una petición HTTP adicional.
  - Esa ciudad depende de que el servicio esté disponible. Si no lo está, descarta el aviso (ver 1.4).
- **En este dominio:**
  - El tráfico extra es acotado: un evento interesa como mucho a 3 de las 5 ciudades (sección 2.4), así que hay a lo más 3 peticiones por sismo.
  - La dependencia del servicio sí importa, porque las ciudades que necesitan el detalle son las cercanas al epicentro.

### 1.2 Exchange fanout y filtrado en cada suscriptor

- **Decisión:** el exchange `sismos` (fanout) copia cada evento a las 5 colas. Cada suscriptor decide localmente si la distancia geodésica es menor que 500 km.
- **Alternativa:** un exchange `topic` o `direct` con enrutamiento por región, o que el publisher calcule qué ciudades están interesadas.
- **Ventajas:**
  - El publisher no conoce a los suscriptores ni la regla de interés.
  - Agregar una ciudad solo requiere una cola y un suscriptor nuevos, sin modificar el publisher.
- **Costo:** cada evento llega a las 5 colas aunque la mayoría lo descarte, y cada suscriptor calcula la distancia.
- **En este dominio:** no es de consideración. Con 5 ciudades y mensajes de tres campos, las copias extra son despreciables. Empezaría a importar con muchas más ciudades (sección 2.6).

### 1.3 Colas durables por ciudad y mensajes persistentes

- **Decisión:** cada ciudad tiene su propia cola durable, y los mensajes se publican como persistentes (`delivery_mode=2`).
- **Alternativa:** colas temporales o exclusivas, que solo existen mientras el suscriptor está conectado.
- **Ventajas:**
  - Si un suscriptor se detiene, su cola conserva los avisos y los entrega cuando vuelve.
  - Un suscriptor lento o detenido no afecta a los demás.
- **Costos:**
  - Los avisos acumulados se entregan atrasados y no vencen (no hay TTL).
  - El mensaje no incluye la fecha del sismo, así que el suscriptor no puede saber, sin consultar HTTP, si un aviso es antiguo. Solo las ciudades interesadas ven la fecha en el detalle.
  - Los mensajes publicados antes de que exista una cola se pierden; por eso el orden de arranque exige crear primero las colas.
- **En este dominio:** es de consideración.
  - Un aviso atrasado puede tener menos valor, o confundir si se presenta como reciente.
  - Que convenga recibirlo tarde o descartarlo depende de cuánto tiempo haya pasado.
  - **Mejora posible:** un TTL en las colas, o incluir la fecha en el evento para descartar avisos antiguos.

### 1.4 Confirmación manual y descarte de mensajes fallidos

- **Decisión:** el suscriptor confirma manualmente:
  - `basic_ack` cuando el procesamiento termina bien;
  - `basic_nack(requeue=False)` ante cualquier error: mensaje inválido, HTTP 404, servicio caído o timeout.

  No hay reintentos ni dead-letter queue, así que el mensaje rechazado se descarta.
- **Alternativa:** reencolar el mensaje, reintentar con espera, o derivarlo a una dead-letter queue.
- **Ventajas:**
  - Un mensaje inválido nunca queda reintentándose indefinidamente ni bloquea la cola.
  - La política es simple y predecible.
- **Costo:** un fallo transitorio (servicio HTTP caído o lento) hace que esa ciudad pierda definitivamente el detalle de ese sismo. Solo queda el registro en el log del suscriptor.
- **En este dominio:** es uno de los costos más relevantes de la solución, porque afecta a la ciudad cercana justo cuando falla el servicio.
- **Mejora posible:** una dead-letter queue, o un número acotado de reintentos solo para errores transitorios.

### 1.5 Garantías de entrega: sin publisher confirms y con posibles duplicados

- **Decisión:** el sistema no garantiza entrega de extremo a extremo:
  - **Publicación:** no se usan *publisher confirms*. El publisher valida todo el dataset antes de conectarse, pero si la conexión con el broker falla a mitad del envío puede quedar una publicación parcial, sin que el publisher sepa con certeza qué mensajes aceptó el broker.
  - **Consumo:** si un suscriptor se cae después de recibir un mensaje y antes de confirmarlo, RabbitMQ entrega de nuevo ese mensaje no confirmado. Esto puede producir **duplicados**: una segunda línea de log para el mismo sismo y, si la ciudad está a menos de 500 km, una segunda consulta HTTP. No hay deduplicación.
- **Alternativa:** *publisher confirms* para saber qué mensajes aceptó el broker, y deduplicación por `id` en el suscriptor.
- **Ventajas:**
  - El código queda simple.
  - Validar antes de conectar evita publicaciones parciales causadas por datos inválidos.
- **Costos:**
  - Puede perderse un aviso si el broker falla durante el envío.
  - Pueden repetirse consultas y registros tras una caída del suscriptor.
- **En este dominio:**
  - Los duplicados tienen un efecto acotado: cada uno es una consulta HTTP de lectura más un aviso repetido en el log.
  - Cuál pesa más depende del uso del aviso:
    - un duplicado repite una consulta de lectura y un registro, pero quien consuma los avisos podría tomarlo como un segundo sismo;
    - una pérdida deja a una ciudad sin el aviso, pero solo ocurre si el broker falla durante la publicación.
- **Mejora posible:** *publisher confirms* y deduplicación por `id`.

### 1.6 Timeout por operación y procesamiento secuencial

- **Decisión:** cada suscriptor procesa un mensaje a la vez (`prefetch_count=1`). La petición HTTP usa `urlopen(..., timeout=5)`, que limita a 5 s **cada operación de red** (conectar o leer).
- **Alternativa:** procesar varios mensajes en paralelo dentro del suscriptor, o no usar timeout.
- **Ventajas:**
  - El código es simple.
  - Si el suscriptor se cae, se vuelve a entregar como máximo un mensaje: el que estaba sin confirmar.
  - Un servicio que no responde no bloquea al suscriptor para siempre.
- **Costos:**
  - Mientras espera una respuesta, la ciudad no procesa los avisos siguientes.
  - El timeout no es un límite para la duración total de la petición: si el servicio no responde, la espera ronda los 5 s, pero una respuesta que llega muy lentamente puede tardar más.
- **En este dominio:** es de consideración en ráfagas de réplicas, cuando llegan muchos eventos de la misma zona. La sección 2.4 muestra cómo estimar cuándo empieza a crecer la cola (`λ_pico > 1/t_proc`).

### 1.7 Simplificación y costo del desacoplamiento

- **Simplificación:** el publisher toma los eventos de `data/sismos.json`, el mismo archivo que sirve el servicio HTTP.
  - Así se puede demostrar el flujo completo con datos coherentes, como permite el enunciado al asumir que la información ya está en el servicio.
  - El costo es que acopla el publisher al almacén del servicio, cuando la Figura 1 los muestra como componentes independientes.
  - En un sistema real, la fuente de detección publicaría el evento y registraría el detalle por separado.
- **Desacoplamiento:** separar publisher, broker, cinco suscriptores y servicio HTTP permite que cada parte falle o se reinicie por separado.
  - A cambio, hay que operar más procesos y hay más puntos de falla.
  - Para cinco ciudades es más infraestructura de la estrictamente necesaria, pero es la arquitectura que pide el enunciado, y las colas durables evitan que un suscriptor detenido afecte al resto.

### 1.8 RabbitMQ como punto único de falla

- **Decisión:** una sola instancia de RabbitMQ, sin clúster ni réplicas.
- **Alternativa:** un clúster de RabbitMQ con colas replicadas (*quorum queues*), o que el publisher avise directamente a cada suscriptor.
- **Ventajas:**
  - Es la configuración más simple de levantar y operar: un contenedor.
  - El broker es lo que desacopla al publisher de los suscriptores (ver 1.2 y 1.3).
- **Costos:**
  - Si el broker cae, **ningún** aviso llega a ninguna ciudad. El publisher no puede conectarse y termina con error.
  - Los suscriptores terminan con código 1 ante un error de RabbitMQ y no se reconectan solos; con `levantar`, eso detiene todo el sistema.
  - Al reiniciar el broker se conservan las colas, los bindings y los mensajes persistentes ya escritos en disco (ver 1.3). Lo publicado mientras estaba caído no se recupera.
- **En este dominio:** es de consideración. Un fallo del broker es el único que deja a las cinco ciudades sin avisos a la vez.
- **Mejora posible:** reconexión automática en los suscriptores y un clúster con *quorum queues*.

### 1.9 Datos en memoria cargados desde un archivo

- **Decisión:** el servicio HTTP carga `data/sismos.json` en memoria al iniciar. No usa base de datos ni ofrece `POST`, porque el enunciado asume que la información ya está en el servicio.
- **Alternativa:** una base de datos y un endpoint para registrar sismos nuevos.
- **Ventajas:**
  - No hay infraestructura adicional y las consultas son lecturas en memoria.
  - Reiniciar el servicio **no pierde datos**, porque el archivo es la fuente y se vuelve a cargar al arrancar.
- **Costos:**
  - No se pueden agregar ni corregir sismos con el servicio en ejecución. Cualquier cambio en el archivo exige reiniciarlo.
  - Si el CSN revisa los valores de un informe, el dataset no se actualiza solo.
  - Mientras el servicio se reinicia, las ciudades interesadas descartan sus avisos (ver 1.4).
- **En este dominio:** no es de consideración para la tarea, cuyo dataset es fijo. En un sistema real sí lo sería, porque los sismos se registran continuamente y el servicio no puede detenerse para cargar cada uno.

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
| `t_proc` | Tiempo de procesamiento de un mensaje en un suscriptor | Se mide en los logs: cálculo de distancia más, si corresponde, el tiempo de la petición HTTP (el timeout de 5 s se aplica a cada operación de red, conectar o leer, y no limita la duración total) |

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
- Si el servicio HTTP no responde, `t_proc` ronda los 5 s del timeout. No es un tope estricto, porque el timeout se aplica a cada operación de red.

**Acumulación durante fallas.**

- Si el suscriptor de la ciudad `c` está detenido durante `T` segundos, su cola durable acumula `λ · T` mensajes, que ocupan `λ · T · S_msg` bytes.
- Esto permite estimar el espacio en disco del broker para tolerar caídas de una duración dada.

### 2.5 Promedio vs. alta actividad sísmica

El promedio no basta para dimensionar el sistema, porque los sismos llegan agrupados en el tiempo y en el espacio:

- **Temporal.** Después de un sismo grande, la tasa de réplicas puede superar en órdenes de magnitud a la de un día normal y decae en horas o días (Omori). El sistema debe dimensionarse con `λ_pico` y no con `λ`. Las colas durables absorben ráfagas cortas aunque los suscriptores sean más lentos por un rato.
- **Espacial.** Las réplicas se concentran cerca del epicentro principal. Durante una secuencia, `p_c` se acerca a 1 en las ciudades cercanas y a 0 en las lejanas. Por eso la carga HTTP del peor caso es `λ_pico` multiplicado por la cantidad de ciudades cercanas a esa zona, que es como mucho 3 y no 5.
- **Correlación con el interés.** Los momentos de mayor carga coinciden con los momentos en que la información más importa. Por eso la estimación del pico, y no la del promedio, debe guiar el timeout, la decisión de incorporar reintentos (hoy no existen; ver 1.4) y la capacidad del servicio HTTP.

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
