"""Pruebas de subscriber/ciudades.py.

Ejecutar desde la raíz del repositorio:
    .venv/bin/python -m unittest discover -s tests -v
"""

import math
import unittest

from geopy.distance import geodesic

import mensajeria
from subscriber.ciudades import (
    UBICACIONES,
    UMBRAL_KM,
    distancia_km,
    es_de_interes,
    validar_coordenadas,
)

# Error máximo aceptado al comparar distancias calculadas (1 mm).
TOLERANCIA_KM = 1e-6
RUMBOS = (0, 90, 180, 270)


def punto_a(ciudad, km, rumbo):
    """Punto a `km` de la ciudad en el rumbo dado (problema directo sobre WGS84)."""
    ubicacion = UBICACIONES[ciudad]
    destino = geodesic(kilometers=km, ellipsoid="WGS-84").destination(
        (ubicacion.latitud, ubicacion.longitud), rumbo
    )
    return destino.latitude, destino.longitude


class TestCiudades(unittest.TestCase):
    def test_coordenadas_exactas(self):
        esperadas = {
            "arica": ("Arica", -18.4746, -70.29792),
            "coquimbo": ("Coquimbo", -29.95332, -71.33947),
            "valparaiso": ("Valparaíso", -33.036, -71.62963),
            "concepcion": ("Concepción", -36.82699, -73.04977),
            "punta_arenas": ("Punta Arenas", -53.16282, -70.90922),
        }
        self.assertEqual({k: tuple(v) for k, v in UBICACIONES.items()}, esperadas)

    def test_claves_coinciden_con_mensajeria(self):
        self.assertEqual(set(UBICACIONES), set(mensajeria.CIUDADES))
        self.assertEqual(len(UBICACIONES), 5)


class TestDistancia(unittest.TestCase):
    def test_distancia_cero_a_si_misma(self):
        for ciudad, ubicacion in UBICACIONES.items():
            with self.subTest(ciudad=ciudad):
                self.assertEqual(
                    distancia_km(ciudad, ubicacion.latitud, ubicacion.longitud), 0.0
                )

    def test_bajo_el_umbral(self):
        for ciudad in UBICACIONES:
            for rumbo in RUMBOS:
                with self.subTest(ciudad=ciudad, rumbo=rumbo):
                    d = distancia_km(ciudad, *punto_a(ciudad, 495, rumbo))
                    self.assertAlmostEqual(d, 495, delta=TOLERANCIA_KM)
                    self.assertTrue(es_de_interes(d))

    def test_sobre_el_umbral(self):
        for ciudad in UBICACIONES:
            for rumbo in RUMBOS:
                with self.subTest(ciudad=ciudad, rumbo=rumbo):
                    d = distancia_km(ciudad, *punto_a(ciudad, 505, rumbo))
                    self.assertAlmostEqual(d, 505, delta=TOLERANCIA_KM)
                    self.assertFalse(es_de_interes(d))

    def test_consistente_con_geodesic_wgs84(self):
        epicentro = (-36.1, -71.84)  # evento csn-385887 de CONTRATOS.md
        for ciudad, ubicacion in UBICACIONES.items():
            with self.subTest(ciudad=ciudad):
                esperada = geodesic(
                    (ubicacion.latitud, ubicacion.longitud),
                    epicentro,
                    ellipsoid="WGS-84",
                ).km
                self.assertEqual(distancia_km(ciudad, *epicentro), esperada)

    def test_ciudad_inexistente(self):
        with self.assertRaises(KeyError):
            distancia_km("santiago", -33.45, -70.66)
        with self.assertRaises(KeyError):
            distancia_km("Arica", -18.0, -70.0)


class TestUmbral(unittest.TestCase):
    def test_limite_estricto(self):
        self.assertEqual(UMBRAL_KM, 500.0)
        self.assertTrue(es_de_interes(math.nextafter(500.0, 0.0)))
        self.assertTrue(es_de_interes(499.999))
        self.assertTrue(es_de_interes(0.0))
        self.assertFalse(es_de_interes(500.0))
        self.assertFalse(es_de_interes(500))
        self.assertFalse(es_de_interes(math.nextafter(500.0, math.inf)))
        self.assertFalse(es_de_interes(500.001))


class TestValidacion(unittest.TestCase):
    def test_extremos_validos(self):
        for latitud, longitud in [(90, 0), (-90, 0), (0, 180), (0, -180),
                                  (90.0, 180.0), (-90.0, -180.0), (0, 0)]:
            with self.subTest(latitud=latitud, longitud=longitud):
                validar_coordenadas(latitud, longitud)
                distancia_km("arica", latitud, longitud)

    def test_rechaza_no_numericos(self):
        for valor in ["1.0", None, [1.0], True, False]:
            with self.subTest(valor=valor):
                with self.assertRaises(ValueError):
                    validar_coordenadas(valor, 0)
                with self.assertRaises(ValueError):
                    validar_coordenadas(0, valor)

    def test_rechaza_no_finitos(self):
        for valor in [math.nan, math.inf, -math.inf]:
            with self.subTest(valor=valor):
                with self.assertRaises(ValueError):
                    validar_coordenadas(valor, 0)
                with self.assertRaises(ValueError):
                    validar_coordenadas(0, valor)

    def test_rechaza_fuera_de_rango(self):
        for latitud, longitud in [(90.0001, 0), (-90.0001, 0), (0, 180.0001),
                                  (0, -180.0001), (91, 0), (0, 190)]:
            with self.subTest(latitud=latitud, longitud=longitud):
                with self.assertRaises(ValueError):
                    validar_coordenadas(latitud, longitud)

    def test_distancia_valida_el_epicentro(self):
        # geopy normalizaría la longitud 190 sin error; distancia_km debe rechazarla.
        with self.assertRaises(ValueError):
            distancia_km("arica", 0, 190)
        with self.assertRaises(ValueError):
            distancia_km("arica", math.nan, 0)


if __name__ == "__main__":
    unittest.main()
