"""Configuración compartida de RabbitMQ (CONTRATOS.md, secciones 2.1 a 2.3).

El publisher y los suscriptores importan este módulo para declarar la
topología siempre con los mismos nombres y parámetros. Los componentes se
ejecutan desde la raíz del repositorio con ``python -m``.

``python -m mensajeria`` declara el exchange y las cinco colas con sus
bindings, para preparar o verificar la topología.

También contiene las reglas de validación del mensaje (CONTRATOS.md 1.3 y
2.2), compartidas por el publisher y los suscriptores.
"""

import math
import re
import sys

import pika
from pika.exchange_type import ExchangeType

HOST = "localhost"
PUERTO = 5672
USUARIO = "guest"
CLAVE = "guest"

EXCHANGE = "sismos"
ROUTING_KEY = ""

# Identificador de cada ciudad -> nombre de su cola ("sismos.<ciudad>").
CIUDADES = ("arica", "coquimbo", "valparaiso", "concepcion", "punta_arenas")
COLAS = {ciudad: f"{EXCHANGE}.{ciudad}" for ciudad in CIUDADES}

# Propiedades con las que el publisher envía cada mensaje (CONTRATOS.md 2.2).
PROPIEDADES_MENSAJE = pika.BasicProperties(
    content_type="application/json",
    delivery_mode=pika.DeliveryMode.Persistent,
)

# Formato del identificador de un sismo (CONTRATOS.md 1.3).
PATRON_ID = re.compile(r"(csn|sim)-[0-9]{1,9}")


def validar_id(valor):
    """Lanza ValueError si el id no es un string con el formato del contrato."""
    if not isinstance(valor, str):
        raise ValueError(f"id debe ser un string, no {type(valor).__name__}")
    # fullmatch: no acepta caracteres sobrantes ni un salto de línea final.
    if not PATRON_ID.fullmatch(valor):
        raise ValueError(f"id con formato inválido: {valor!r}")


def _validar_valor(nombre, valor, limite):
    # bool es subclase de int, pero no es una coordenada válida.
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        raise ValueError(f"{nombre} debe ser un número, no {type(valor).__name__}")
    if not math.isfinite(valor):
        raise ValueError(f"{nombre} debe ser finita: {valor}")
    if not -limite <= valor <= limite:
        raise ValueError(f"{nombre} fuera del rango [-{limite}, {limite}]: {valor}")


def validar_coordenadas(latitud, longitud):
    """Lanza ValueError si las coordenadas no son números finitos dentro de rango."""
    _validar_valor("latitud", latitud, 90)
    _validar_valor("longitud", longitud, 180)


def conectar():
    """Abre una conexión con el broker; termina el proceso si no está disponible."""
    parametros = pika.ConnectionParameters(
        host=HOST,
        port=PUERTO,
        credentials=pika.PlainCredentials(USUARIO, CLAVE),
    )
    try:
        return pika.BlockingConnection(parametros)
    except pika.exceptions.AMQPConnectionError as error:
        sys.exit(
            f"Error: no se pudo conectar a RabbitMQ en {HOST}:{PUERTO} "
            f"({type(error).__name__}). ¿Está iniciado el broker?"
        )


def declarar_exchange(canal):
    """Declara el exchange fanout durable compartido."""
    canal.exchange_declare(
        exchange=EXCHANGE,
        exchange_type=ExchangeType.fanout,
        durable=True,
    )


def declarar_cola(canal, ciudad):
    """Declara el exchange, la cola de la ciudad y su binding; devuelve el nombre de la cola."""
    cola = COLAS[ciudad]
    declarar_exchange(canal)
    canal.queue_declare(
        queue=cola,
        durable=True,
        exclusive=False,
        auto_delete=False,
    )
    canal.queue_bind(queue=cola, exchange=EXCHANGE, routing_key=ROUTING_KEY)
    return cola


def main():
    with conectar() as conexion, conexion.channel() as canal:
        declarar_exchange(canal)
        print(f"Exchange '{EXCHANGE}' (fanout, durable) declarado")
        for ciudad in CIUDADES:
            cola = declarar_cola(canal, ciudad)
            print(f"Cola '{cola}' declarada y enlazada a '{EXCHANGE}'")


if __name__ == "__main__":
    main()
