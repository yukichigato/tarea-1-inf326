"""Pruebas de http_service/main.py contra CONTRATOS.md (sección 3).

No levantan uvicorn: usan TestClient de FastAPI sobre la aplicación.

Ejecutar desde la raíz del repositorio:
    .venv/bin/python -m unittest discover -s tests -v
"""

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from http_service import main

CAMPOS = {
    "id",
    "latitud",
    "longitud",
    "fecha_utc",
    "fecha_local",
    "profundidad_km",
    "magnitud",
    "tipo_magnitud",
    "referencia",
    "fuente_url",
}

# Respuestas de ejemplo de CONTRATOS.md, sección 3.
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
SIM_001 = {
    "id": "sim-001",
    "latitud": -33.05,
    "longitud": -71.62,
    "fecha_utc": "2026-10-01T12:00:00Z",
    "fecha_local": None,
    "profundidad_km": 30.0,
    "magnitud": 5.1,
    "tipo_magnitud": "Mw",
    "referencia": "Evento ficticio de prueba cerca de Valparaíso",
    "fuente_url": None,
}


class TestEarthquakes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cliente = TestClient(main.app)

    def test_sismo_real(self):
        respuesta = self.cliente.get("/earthquakes/csn-385887")
        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(respuesta.headers["content-type"].startswith("application/json"))
        self.assertEqual(respuesta.json(), CSN_385887)

    def test_sismo_ficticio_con_opcionales_en_null(self):
        respuesta = self.cliente.get("/earthquakes/sim-001")
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json(), SIM_001)

    def test_todos_los_sismos_del_dataset(self):
        with open(main.RUTA_DATASET, encoding="utf-8") as archivo:
            registros = json.load(archivo)
        for registro in registros:
            with self.subTest(id=registro["id"]):
                respuesta = self.cliente.get(f"/earthquakes/{registro['id']}")
                self.assertEqual(respuesta.status_code, 200)
                cuerpo = respuesta.json()
                self.assertEqual(set(cuerpo), CAMPOS)
                # La respuesta es el registro con los opcionales omitidos en null.
                esperado = {campo: registro.get(campo) for campo in CAMPOS}
                self.assertEqual(cuerpo, esperado)

    def test_id_inexistente(self):
        for id_sismo in ("csn-999999", "sim-999", "csn-1"):
            with self.subTest(id=id_sismo):
                respuesta = self.cliente.get(f"/earthquakes/{id_sismo}")
                self.assertEqual(respuesta.status_code, 404)
                self.assertEqual(respuesta.json(), {"detail": "Sismo no encontrado"})

    def test_formato_invalido(self):
        invalidos = (
            "abc",
            "CSN-385887",
            "csn-",
            "csn-1234567890",
            "csn-38a",
            "xyz-1",
            "csn385887",
            " csn-385887",
        )
        for id_sismo in invalidos:
            with self.subTest(id=id_sismo):
                respuesta = self.cliente.get(f"/earthquakes/{id_sismo}")
                self.assertEqual(respuesta.status_code, 422)
                self.assertIsInstance(respuesta.json()["detail"], list)

    def test_rutas_sin_id(self):
        for ruta in ("/earthquakes/", "/earthquakes/csn-1/extra"):
            with self.subTest(ruta=ruta):
                self.assertEqual(self.cliente.get(ruta).status_code, 404)

    def test_solo_get(self):
        respuesta = self.cliente.post("/earthquakes/csn-385887")
        self.assertEqual(respuesta.status_code, 405)


class TestCargarSismos(unittest.TestCase):
    def escribir(self, registros):
        directorio = tempfile.TemporaryDirectory()
        self.addCleanup(directorio.cleanup)
        ruta = Path(directorio.name) / "sismos.json"
        ruta.write_text(json.dumps(registros), encoding="utf-8")
        return ruta

    def test_indexa_por_id(self):
        sismos = main.cargar_sismos(self.escribir([CSN_385887]))
        self.assertEqual(list(sismos), ["csn-385887"])

    def test_id_repetido(self):
        with self.assertRaises(ValueError):
            main.cargar_sismos(self.escribir([CSN_385887, CSN_385887]))

    def test_falta_campo_obligatorio(self):
        incompleto = {k: v for k, v in CSN_385887.items() if k != "magnitud"}
        with self.assertRaises(ValueError):
            main.cargar_sismos(self.escribir([incompleto]))


if __name__ == "__main__":
    unittest.main()
