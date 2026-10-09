"""Publisher de sismos (CONTRATOS.md, secciones 2.2 y 4).

Lee el dataset, valida todos los registros y, solo si no hay errores, publica
por cada sismo un evento mínimo {id, latitud, longitud} en el exchange
fanout ``sismos``.

Uso, desde la raíz del repositorio:
    python -m publisher.publish [--dataset RUTA] [ID ...]

Limitación aceptada (CONTRATOS.md 2.5): no se usan publisher confirms, así que
si la conexión con RabbitMQ falla durante el envío pueden quedar publicados
solo los primeros eventos. No se reintenta, para no duplicar los ya enviados.
"""

import argparse
import json
import sys
from pathlib import Path

import pika

import mensajeria

RUTA_DATASET = Path(__file__).resolve().parents[1] / "data" / "sismos.json"
CAMPOS_EVENTO = ("id", "latitud", "longitud")


class ErrorPublicacion(Exception):
    """Fallo de AMQP a mitad de la publicación."""

    def __init__(self, publicados, id_sismo, causa):
        super().__init__(causa)
        self.publicados = publicados
        self.id_sismo = id_sismo
        self.causa = causa


def cargar_registros(ruta):
    """Lee y parsea el dataset; lanza ValueError si no es un arreglo JSON legible."""
    try:
        with open(ruta, encoding="utf-8") as archivo:
            datos = json.load(archivo)
    except OSError as error:
        raise ValueError(f"no se pudo leer {ruta}: {error.strerror or error}") from error
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError(f"{ruta} no contiene JSON válido: {error}") from error
    if not isinstance(datos, list):
        raise ValueError(
            f"la raíz de {ruta} debe ser un arreglo JSON, no {type(datos).__name__}"
        )
    return datos


def validar_registros(registros):
    """Valida todos los registros y devuelve la lista completa de errores."""
    errores = []
    vistos = set()
    for indice, registro in enumerate(registros):
        etiqueta = f"registro {indice}"
        if not isinstance(registro, dict):
            errores.append(
                f"{etiqueta}: debe ser un objeto JSON, no {type(registro).__name__}"
            )
            continue
        if isinstance(registro.get("id"), str):
            etiqueta += f" ({registro['id']!r})"

        faltantes = [campo for campo in CAMPOS_EVENTO if campo not in registro]
        if faltantes:
            errores.append(f"{etiqueta}: faltan los campos {', '.join(faltantes)}")
            continue

        try:
            mensajeria.validar_id(registro["id"])
        except ValueError as error:
            errores.append(f"{etiqueta}: {error}")
        else:
            if registro["id"] in vistos:
                errores.append(f"{etiqueta}: id repetido en el dataset")
            vistos.add(registro["id"])

        try:
            mensajeria.validar_coordenadas(registro["latitud"], registro["longitud"])
        except ValueError as error:
            errores.append(f"{etiqueta}: {error}")
    return errores


def seleccionar(registros, ids):
    """Registros con los ids pedidos, en el orden del dataset y sin repetir.

    Sin ids, devuelve todos. Lanza ValueError si algún id no existe.
    """
    if not ids:
        return list(registros)
    pedidos = set(ids)
    existentes = {registro["id"] for registro in registros}
    faltantes = [id_sismo for id_sismo in dict.fromkeys(ids) if id_sismo not in existentes]
    if faltantes:
        raise ValueError(f"ids que no existen en el dataset: {', '.join(faltantes)}")
    return [registro for registro in registros if registro["id"] in pedidos]


def construir_evento(registro):
    """Evento mínimo del contrato: solo id, latitud y longitud, sin transformar."""
    return {campo: registro[campo] for campo in CAMPOS_EVENTO}


def publicar(canal, eventos):
    """Declara el exchange y publica cada evento; devuelve cuántos se publicaron."""
    publicados = 0
    id_sismo = None
    try:
        mensajeria.declarar_exchange(canal)
        for evento in eventos:
            id_sismo = evento["id"]
            canal.basic_publish(
                exchange=mensajeria.EXCHANGE,
                routing_key=mensajeria.ROUTING_KEY,
                body=json.dumps(evento).encode("utf-8"),
                properties=mensajeria.PROPIEDADES_MENSAJE,
            )
            publicados += 1
            print(f"Publicado {id_sismo}")
    except pika.exceptions.AMQPError as error:
        raise ErrorPublicacion(publicados, id_sismo, error) from error
    return publicados


def crear_parser():
    parser = argparse.ArgumentParser(
        prog="python -m publisher.publish",
        description=(
            "Publica en RabbitMQ un evento {id, latitud, longitud} por cada sismo "
            "del dataset. Valida el dataset completo antes de conectarse."
        ),
    )
    parser.add_argument(
        "ids",
        nargs="*",
        metavar="ID",
        help="ids de los sismos a publicar (por defecto, todos los del dataset)",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=RUTA_DATASET,
        help=f"ruta del dataset JSON (por defecto, {RUTA_DATASET})",
    )
    return parser


def main(argv=None):
    args = crear_parser().parse_args(argv)

    try:
        registros = cargar_registros(args.dataset)
    except ValueError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    errores = validar_registros(registros)
    if errores:
        print(
            f"Error: {args.dataset} tiene registros inválidos; no se publicó nada.",
            *(f"  - {error}" for error in errores),
            sep="\n",
            file=sys.stderr,
        )
        return 1

    try:
        seleccion = seleccionar(registros, args.ids)
    except ValueError as error:
        print(f"Error: {error}; no se publicó nada.", file=sys.stderr)
        return 1

    eventos = [construir_evento(registro) for registro in seleccion]
    if not eventos:
        print("No hay sismos que publicar.")
        return 0

    with mensajeria.conectar() as conexion, conexion.channel() as canal:
        try:
            publicados = publicar(canal, eventos)
        except ErrorPublicacion as error:
            detalle = f" al publicar {error.id_sismo}" if error.id_sismo else ""
            print(
                f"Error de RabbitMQ{detalle} ({type(error.causa).__name__}). "
                f"Eventos publicados antes del fallo: {error.publicados} de {len(eventos)}.",
                file=sys.stderr,
            )
            return 1

    print(f"{publicados} evento(s) publicados en el exchange '{mensajeria.EXCHANGE}'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
