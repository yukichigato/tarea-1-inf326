"""Suscriptor de sismos de una ciudad (CONTRATOS.md, secciones 2.3, 2.4 y 3).

Consume la cola de su ciudad, valida cada evento {id, latitud, longitud},
calcula la distancia geodésica al epicentro y, solo si es menor que 500 km,
consulta los detalles en ``GET {SISMOS_API_URL}/earthquakes/{id}``.

Uso, desde la raíz del repositorio:
    python -m subscriber.subscriber {arica,coquimbo,valparaiso,concepcion,punta_arenas}

La URL base del servicio HTTP se toma de la variable de entorno
``SISMOS_API_URL`` (por defecto, http://localhost:8000).

Confirmaciones (CONTRATOS.md 2.4): ``basic_ack`` si el procesamiento termina
bien y ``basic_nack(requeue=False)`` ante cualquier error del mensaje; nunca se
reencola. Un error de un mensaje no detiene el consumo; un error de RabbitMQ
sí termina el proceso (código 1), sin reconexión.
"""

import argparse
import functools
import http.client
import json
import os
import sys
import traceback
import urllib.error
import urllib.request
from typing import NamedTuple
from urllib.parse import urlsplit

import pika

import mensajeria
from subscriber import ciudades

URL_POR_DEFECTO = "http://localhost:8000"
# urllib aplica el timeout a cada operación de red (conectar, cada lectura),
# no a la duración total de la petición.
TIMEOUT_HTTP = 5
CAMPOS_EVENTO = frozenset({"id", "latitud", "longitud"})


class MensajeInvalido(ValueError):
    """El cuerpo recibido no cumple el formato del mensaje (CONTRATOS.md 2.2)."""


class ErrorConsulta(Exception):
    """La consulta HTTP no produjo una respuesta 200 utilizable."""


class Resultado(NamedTuple):
    exito: bool
    descripcion: str


def decodificar_evento(cuerpo):
    """Decodifica y valida el evento; lanza MensajeInvalido si no es válido."""
    try:
        evento = json.loads(cuerpo.decode("utf-8"))
    except UnicodeDecodeError as error:
        raise MensajeInvalido("el cuerpo no es UTF-8 válido") from error
    except json.JSONDecodeError as error:
        raise MensajeInvalido(f"JSON inválido ({error})") from error

    if not isinstance(evento, dict):
        raise MensajeInvalido(f"debe ser un objeto JSON, no {type(evento).__name__}")
    faltantes = CAMPOS_EVENTO - evento.keys()
    if faltantes:
        raise MensajeInvalido(f"faltan los campos {', '.join(sorted(faltantes))}")
    sobrantes = evento.keys() - CAMPOS_EVENTO
    if sobrantes:
        raise MensajeInvalido(f"campos no permitidos: {', '.join(sorted(sobrantes))}")

    try:
        mensajeria.validar_id(evento["id"])
        mensajeria.validar_coordenadas(evento["latitud"], evento["longitud"])
    except ValueError as error:
        raise MensajeInvalido(str(error)) from error
    return evento


def _es_timeout(error):
    # El timeout llega directo (al esperar o leer la respuesta) o como
    # `reason` de un URLError (al conectar).
    return isinstance(error, TimeoutError) or isinstance(
        getattr(error, "reason", None), TimeoutError
    )


def consultar_sismo(url_base, id_sismo):
    """GET {url_base}/earthquakes/{id}; devuelve el detalle o lanza ErrorConsulta."""
    url = f"{url_base.rstrip('/')}/earthquakes/{id_sismo}"
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_HTTP) as respuesta:
            estado = respuesta.status
            contenido = respuesta.read()
    # HTTPError es subclase de URLError (y de OSError): debe ir primero.
    except urllib.error.HTTPError as error:
        error.close()
        raise ErrorConsulta(f"HTTP {error.code}") from error
    except (OSError, http.client.HTTPException) as error:
        if _es_timeout(error):
            raise ErrorConsulta(f"timeout ({TIMEOUT_HTTP} s por operación de red)") from error
        motivo = getattr(error, "reason", error)
        raise ErrorConsulta(
            f"error de conexión o protocolo ({type(motivo).__name__}: {motivo})"
        ) from error

    # urlopen no lanza excepción con otros 2xx (por ejemplo, 204).
    if estado != 200:
        raise ErrorConsulta(f"HTTP {estado} (se esperaba 200)")
    try:
        detalle = json.loads(contenido.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ErrorConsulta("la respuesta 200 no es JSON UTF-8 válido") from error
    if not isinstance(detalle, dict):
        raise ErrorConsulta(f"la respuesta 200 no es un objeto JSON ({type(detalle).__name__})")
    if detalle.get("id") != id_sismo:
        raise ErrorConsulta(
            f"la respuesta 200 tiene id {detalle.get('id')!r}, se esperaba {id_sismo!r}"
        )
    return detalle


def _resumir(detalle):
    # El servicio debe enviar los 10 campos (CONTRATOS.md 3); aquí no se exigen.
    return (
        f"M{detalle.get('magnitud', '?')} {detalle.get('tipo_magnitud', '')}".rstrip()
        + f", prof. {detalle.get('profundidad_km', '?')} km"
        + f", {detalle.get('fecha_utc', '?')}"
        + f", {detalle.get('referencia', '?')}"
    )


def procesar_mensaje(ciudad, cuerpo, url_base):
    """Procesa una entrega sin confirmarla; devuelve si terminó bien y qué ocurrió."""
    try:
        evento = decodificar_evento(cuerpo)
    except MensajeInvalido as error:
        return Resultado(False, f"mensaje inválido: {error}")

    id_sismo = evento["id"]
    distancia = ciudades.distancia_km(ciudad, evento["latitud"], evento["longitud"])
    if not ciudades.es_de_interes(distancia):
        return Resultado(
            True,
            f"{id_sismo} a {distancia:.1f} km (≥ {ciudades.UMBRAL_KM:.0f} km): sin consulta HTTP",
        )

    try:
        detalle = consultar_sismo(url_base, id_sismo)
    except ErrorConsulta as error:
        return Resultado(False, f"{id_sismo} a {distancia:.1f} km: consulta HTTP falló: {error}")
    return Resultado(
        True, f"{id_sismo} a {distancia:.1f} km: consulta HTTP 200 → {_resumir(detalle)}"
    )


def al_recibir(canal, metodo, propiedades, cuerpo, *, ciudad, url_base):
    """Callback de basic_consume: procesa y luego confirma exactamente una vez."""
    nombre = ciudades.UBICACIONES[ciudad].nombre
    # Solo el procesamiento va dentro del try: un error de basic_ack/basic_nack
    # es de RabbitMQ, se propaga y termina el proceso sin otra confirmación.
    try:
        resultado = procesar_mensaje(ciudad, cuerpo, url_base)
    except Exception:
        traceback.print_exc()
        resultado = Resultado(False, "error inesperado al procesar el mensaje (ver traza)")

    if resultado.exito:
        canal.basic_ack(delivery_tag=metodo.delivery_tag)
        print(f"[{nombre}] {resultado.descripcion} → ack", flush=True)
    else:
        canal.basic_nack(delivery_tag=metodo.delivery_tag, requeue=False)
        print(f"[{nombre}] {resultado.descripcion} → nack", file=sys.stderr, flush=True)


def validar_url_base(url):
    """Devuelve la URL sin barras finales; lanza ValueError si no es http(s) válida."""
    if any(caracter.isspace() for caracter in url):
        raise ValueError("no debe contener espacios")
    partes = urlsplit(url)
    if partes.scheme not in ("http", "https"):
        raise ValueError("debe comenzar con http:// o https://")
    if not partes.hostname:
        raise ValueError("falta el host")
    try:
        partes.port
    except ValueError as error:
        raise ValueError("puerto inválido") from error
    return url.rstrip("/")


def crear_parser():
    parser = argparse.ArgumentParser(
        prog="python -m subscriber.subscriber",
        description=(
            "Suscriptor de sismos de una ciudad. Consulta el servicio HTTP solo si "
            "el epicentro está a menos de 500 km. URL base del servicio: variable "
            f"de entorno SISMOS_API_URL (por defecto, {URL_POR_DEFECTO})."
        ),
    )
    parser.add_argument("ciudad", choices=mensajeria.CIUDADES, help="ciudad del suscriptor")
    return parser


def main(argv=None):
    args = crear_parser().parse_args(argv)
    ciudad = args.ciudad
    nombre = ciudades.UBICACIONES[ciudad].nombre
    cola = mensajeria.COLAS[ciudad]

    url = os.environ.get("SISMOS_API_URL", URL_POR_DEFECTO)
    try:
        url_base = validar_url_base(url)
    except ValueError as error:
        print(f"Error: SISMOS_API_URL inválida ({url!r}): {error}.", file=sys.stderr)
        return 2

    try:
        with mensajeria.conectar() as conexion, conexion.channel() as canal:
            mensajeria.declarar_cola(canal, ciudad)
            canal.basic_qos(prefetch_count=1)
            canal.basic_consume(
                queue=cola,
                on_message_callback=functools.partial(
                    al_recibir, ciudad=ciudad, url_base=url_base
                ),
                auto_ack=False,
            )
            print(
                f"[{nombre}] Esperando sismos en '{cola}' (HTTP: {url_base}). "
                "Ctrl+C para salir.",
                flush=True,
            )
            canal.start_consuming()
    except KeyboardInterrupt:
        print(f"[{nombre}] Detenido por el usuario.", flush=True)
        return 0
    except pika.exceptions.AMQPError as error:
        print(
            f"Error de RabbitMQ en el suscriptor de {nombre} "
            f"({type(error).__name__}: {error}).",
            file=sys.stderr,
        )
        return 1

    # start_consuming solo retorna si el broker canceló el consumidor
    # (por ejemplo, porque se eliminó la cola).
    print(
        f"Error: el consumo de '{cola}' terminó inesperadamente "
        "(el broker canceló el consumidor).",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
