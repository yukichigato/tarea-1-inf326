"""Ciudades suscriptoras y filtro por distancia (CONTRATOS.md, sección 2.3).

Las claves de ``UBICACIONES`` son los identificadores de ``mensajeria.CIUDADES``;
el nombre de la cola de cada ciudad se obtiene con ``mensajeria.COLAS``.
"""

from typing import NamedTuple

from geopy.distance import geodesic

from mensajeria import validar_coordenadas

UMBRAL_KM = 500.0


class Ciudad(NamedTuple):
    nombre: str
    latitud: float
    longitud: float


# Coordenadas fijadas por el enunciado; no se redondean.
UBICACIONES = {
    "arica": Ciudad("Arica", -18.4746, -70.29792),
    "coquimbo": Ciudad("Coquimbo", -29.95332, -71.33947),
    "valparaiso": Ciudad("Valparaíso", -33.036, -71.62963),
    "concepcion": Ciudad("Concepción", -36.82699, -73.04977),
    "punta_arenas": Ciudad("Punta Arenas", -53.16282, -70.90922),
}


def distancia_km(ciudad, latitud, longitud):
    """Distancia geodésica WGS84, en km, entre la ciudad y el epicentro.

    Lanza KeyError si la ciudad no existe y ValueError si el epicentro no es válido.
    """
    ubicacion = UBICACIONES[ciudad]
    validar_coordenadas(latitud, longitud)
    return geodesic(
        (ubicacion.latitud, ubicacion.longitud),
        (latitud, longitud),
        ellipsoid="WGS-84",
    ).km


def es_de_interes(distancia):
    """True solo si la distancia es estrictamente menor que UMBRAL_KM."""
    return distancia < UMBRAL_KM
