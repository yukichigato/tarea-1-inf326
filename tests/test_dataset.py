"""Pruebas de data/sismos.json contra CONTRATOS.md (secciones 1 y 2.3).

Ejecutar desde la raíz del repositorio:
    .venv/bin/python -m unittest discover -s tests -v
"""

import json
import re
import unittest
from pathlib import Path

from publisher import publish
from subscriber.ciudades import UBICACIONES, UMBRAL_KM, distancia_km

RUTA_DATASET = Path(__file__).resolve().parents[1] / "data" / "sismos.json"

OBLIGATORIOS = {
    "id": str,
    "latitud": float,
    "longitud": float,
    "fecha_utc": str,
    "profundidad_km": float,
    "magnitud": float,
    "tipo_magnitud": str,
    "referencia": str,
}
OPCIONALES = {"fecha_local": str, "fuente_url": str}

PATRON_FECHA_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
PATRON_FECHA_LOCAL = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}")
PATRON_FUENTE = re.compile(
    r"https://www\.sismologia\.cl/sismicidad/informes/\d{4}/\d{2}/(\d+)\.html"
)

# Ciudades a menos de 500 km de cada sismo (las que deben consultar HTTP).
INTERESADAS = {
    "csn-385887": {"valparaiso", "concepcion"},
    "csn-385885": {"arica"},
    "csn-385851": {"coquimbo", "valparaiso"},
    "csn-385813": set(),
    "sim-001": {"valparaiso", "coquimbo", "concepcion"},
    "sim-002": set(),
    "sim-003": {"punta_arenas"},
    "sim-004": {"arica"},
    "sim-005": set(),
}

# Margen mínimo al umbral para los casos ficticios (CONTRATOS.md 2.3).
MARGEN_KM = 5.0

# Registro real de CONTRATOS.md 1.4, que debe estar sin cambios.
CSN_385887 = {
    "id": "csn-385887",
    "latitud": -36.1,
    "longitud": -71.84,
    "fecha_utc": "2026-10-08T22:25:46Z",
    "fecha_local": "2026-10-08T19:25:46-03:00",
    "profundidad_km": 74.0,
    "magnitud": 2.5,
    "tipo_magnitud": "MLv",
    "referencia": "36 km al SO de Linares",
    "fuente_url": "https://www.sismologia.cl/sismicidad/informes/2026/10/385887.html",
}


def es_tipo(valor, tipo):
    # Los números se tratan como float (CONTRATOS.md 0); bool no es un número.
    if tipo is float:
        return isinstance(valor, (int, float)) and not isinstance(valor, bool)
    return isinstance(valor, tipo)


class TestDataset(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registros = publish.cargar_registros(RUTA_DATASET)

    def test_valido_para_el_publisher(self):
        self.assertEqual(publish.validar_registros(self.registros), [])

    def test_campos_y_tipos(self):
        for registro in self.registros:
            with self.subTest(id=registro.get("id")):
                for campo, tipo in OBLIGATORIOS.items():
                    self.assertIn(campo, registro)
                    self.assertTrue(es_tipo(registro[campo], tipo), campo)
                sobrantes = registro.keys() - OBLIGATORIOS.keys() - OPCIONALES.keys()
                self.assertEqual(sobrantes, set())
                for campo, tipo in OPCIONALES.items():
                    if campo in registro:
                        # Un opcional sin valor se omite, nunca se escribe null.
                        self.assertTrue(es_tipo(registro[campo], tipo), campo)

    def test_formatos(self):
        for registro in self.registros:
            with self.subTest(id=registro["id"]):
                self.assertRegex(registro["fecha_utc"], PATRON_FECHA_UTC)
                if "fecha_local" in registro:
                    self.assertRegex(registro["fecha_local"], PATRON_FECHA_LOCAL)
                self.assertGreaterEqual(registro["profundidad_km"], 0)
                self.assertTrue(registro["referencia"].strip())
                self.assertTrue(registro["tipo_magnitud"].strip())

    def test_reales_enlazan_su_informe_csn(self):
        reales = [r for r in self.registros if r["id"].startswith("csn-")]
        self.assertGreater(len(reales), 0)
        for registro in reales:
            with self.subTest(id=registro["id"]):
                coincidencia = PATRON_FUENTE.fullmatch(registro.get("fuente_url", ""))
                self.assertIsNotNone(coincidencia)
                self.assertEqual(f"csn-{coincidencia.group(1)}", registro["id"])

    def test_ficticios_identificados(self):
        ficticios = [r for r in self.registros if r["id"].startswith("sim-")]
        self.assertGreater(len(ficticios), 0)
        for registro in ficticios:
            with self.subTest(id=registro["id"]):
                self.assertNotIn("fuente_url", registro)
                self.assertIn("ficticio", registro["referencia"].lower())

    def test_ejemplo_del_contrato(self):
        por_id = {r["id"]: r for r in self.registros}
        self.assertEqual(por_id["csn-385887"], CSN_385887)

    def test_utf8_sin_bom(self):
        contenido = RUTA_DATASET.read_bytes()
        self.assertFalse(contenido.startswith(b"\xef\xbb\xbf"))
        json.loads(contenido.decode("utf-8"))


class TestEscenarios(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registros = publish.cargar_registros(RUTA_DATASET)

    def test_todos_los_sismos_tienen_escenario(self):
        self.assertEqual({r["id"] for r in self.registros}, set(INTERESADAS))

    def test_ciudades_interesadas(self):
        for registro in self.registros:
            with self.subTest(id=registro["id"]):
                interesadas = {
                    ciudad
                    for ciudad in UBICACIONES
                    if distancia_km(ciudad, registro["latitud"], registro["longitud"])
                    < UMBRAL_KM
                }
                self.assertEqual(interesadas, INTERESADAS[registro["id"]])

    def test_ficticios_lejos_del_umbral(self):
        for registro in self.registros:
            if not registro["id"].startswith("sim-"):
                continue
            for ciudad in UBICACIONES:
                with self.subTest(id=registro["id"], ciudad=ciudad):
                    distancia = distancia_km(
                        ciudad, registro["latitud"], registro["longitud"]
                    )
                    self.assertGreaterEqual(abs(distancia - UMBRAL_KM), MARGEN_KM)

    def test_hay_casos_a_ambos_lados_del_umbral(self):
        distancias = {
            r["id"]: distancia_km("arica", r["latitud"], r["longitud"])
            for r in self.registros
            if r["id"] in ("sim-004", "sim-005")
        }
        self.assertLess(distancias["sim-004"], UMBRAL_KM)
        self.assertLess(UMBRAL_KM - distancias["sim-004"], 15)
        self.assertGreaterEqual(distancias["sim-005"], UMBRAL_KM)
        self.assertLess(distancias["sim-005"] - UMBRAL_KM, 15)


if __name__ == "__main__":
    unittest.main()
