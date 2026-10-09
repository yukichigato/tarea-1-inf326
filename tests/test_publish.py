"""Pruebas de publisher/publish.py y de la validación compartida de mensajeria.py.

No usan RabbitMQ: el canal y la conexión se reemplazan por mocks.

Ejecutar desde la raíz del repositorio:
    .venv/bin/python -m unittest discover -s tests -v
"""

import contextlib
import io
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pika

import mensajeria
import subscriber.ciudades
from publisher import publish

# Registros de ejemplo de CONTRATOS.md, sección 1.4.
REAL = {
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
FICTICIO = {
    "id": "sim-001",
    "latitud": -33.05,
    "longitud": -71.62,
    "fecha_utc": "2026-10-01T12:00:00Z",
    "profundidad_km": 30.0,
    "magnitud": 5.1,
    "tipo_magnitud": "Mw",
    "referencia": "Evento ficticio de prueba cerca de Valparaíso",
}


def registro(**cambios):
    """Copia del registro ficticio con los campos indicados reemplazados."""
    return {**FICTICIO, **cambios}


class ConArchivoTemporal(unittest.TestCase):
    def setUp(self):
        directorio = tempfile.TemporaryDirectory()
        self.addCleanup(directorio.cleanup)
        self.directorio = Path(directorio.name)

    def escribir(self, contenido, nombre="sismos.json"):
        ruta = self.directorio / nombre
        if not isinstance(contenido, str):
            contenido = json.dumps(contenido, ensure_ascii=False)
        ruta.write_text(contenido, encoding="utf-8")
        return ruta


class TestCargarRegistros(ConArchivoTemporal):
    def test_lee_archivo_valido(self):
        ruta = self.escribir([REAL, FICTICIO])
        self.assertEqual(publish.cargar_registros(ruta), [REAL, FICTICIO])

    def test_dataset_vacio(self):
        self.assertEqual(publish.cargar_registros(self.escribir([])), [])

    def test_archivo_inexistente(self):
        with self.assertRaisesRegex(ValueError, "no se pudo leer"):
            publish.cargar_registros(self.directorio / "no_existe.json")

    def test_json_invalido(self):
        for contenido in ['[{"id": "sim-001",', "", "no es json"]:
            with self.subTest(contenido=contenido):
                with self.assertRaisesRegex(ValueError, "JSON válido"):
                    publish.cargar_registros(self.escribir(contenido))

    def test_raiz_que_no_es_arreglo(self):
        for contenido in [{"sismos": [FICTICIO]}, FICTICIO, '"texto"', 3, None]:
            with self.subTest(contenido=contenido):
                with self.assertRaisesRegex(ValueError, "arreglo JSON"):
                    publish.cargar_registros(self.escribir(contenido))

    def test_ruta_por_defecto_relativa_al_modulo(self):
        raiz = Path(publish.__file__).resolve().parents[1]
        self.assertEqual(publish.RUTA_DATASET, raiz / "data" / "sismos.json")


class TestValidarRegistros(unittest.TestCase):
    def assertInvalido(self, registros, fragmento):
        errores = publish.validar_registros(registros)
        self.assertTrue(errores, f"se esperaba un error para {registros!r}")
        self.assertTrue(
            any(fragmento in error for error in errores),
            f"ningún error contiene {fragmento!r}: {errores}",
        )

    def test_ejemplos_del_contrato_son_validos(self):
        self.assertEqual(publish.validar_registros([REAL, FICTICIO]), [])
        self.assertEqual(publish.validar_registros([]), [])

    def test_no_valida_campos_que_no_usa(self):
        minimo = {"id": "sim-002", "latitud": 0, "longitud": 0}
        self.assertEqual(publish.validar_registros([minimo]), [])

    def test_registro_que_no_es_objeto(self):
        for valor in ["sim-001", 1, None, [FICTICIO]]:
            with self.subTest(valor=valor):
                self.assertInvalido([valor], "debe ser un objeto JSON")

    def test_campos_obligatorios_ausentes(self):
        for campo in publish.CAMPOS_EVENTO:
            with self.subTest(campo=campo):
                incompleto = {k: v for k, v in FICTICIO.items() if k != campo}
                self.assertInvalido([incompleto], f"faltan los campos {campo}")

    def test_ids_invalidos(self):
        for id_sismo in ["CSN-385887", "csn-", "csn-1234567890", "385887", "csn-1\n",
                         " csn-1", "csn-1 ", "abc-1", "csn_1", ""]:
            with self.subTest(id=id_sismo):
                self.assertInvalido([registro(id=id_sismo)], "formato inválido")

    def test_ids_de_tipo_incorrecto(self):
        for id_sismo in [385887, None, True, ["sim-001"]]:
            with self.subTest(id=id_sismo):
                self.assertInvalido([registro(id=id_sismo)], "id debe ser un string")

    def test_ids_duplicados(self):
        self.assertInvalido([FICTICIO, REAL, registro(latitud=-30.0)], "id repetido")

    def test_coordenadas_invalidas(self):
        for campo in ("latitud", "longitud"):
            for valor in [math.nan, math.inf, -math.inf, True, False, "1.0", None]:
                with self.subTest(campo=campo, valor=valor):
                    self.assertInvalido([registro(**{campo: valor})], campo)

    def test_coordenadas_fuera_de_rango(self):
        for cambios in [{"latitud": 90.0001}, {"latitud": -90.0001},
                        {"longitud": 180.0001}, {"longitud": -180.0001}]:
            with self.subTest(**cambios):
                self.assertInvalido([registro(**cambios)], "fuera del rango")

    def test_limites_geograficos_validos(self):
        for latitud, longitud in [(90, 180), (-90, -180), (90.0, -180.0), (0, 0)]:
            with self.subTest(latitud=latitud, longitud=longitud):
                self.assertEqual(
                    publish.validar_registros([registro(latitud=latitud, longitud=longitud)]),
                    [],
                )

    def test_informa_todos_los_errores_con_indice_e_id(self):
        errores = publish.validar_registros(
            [FICTICIO, registro(id="sim-002", latitud=99), "x", registro(id="mal")]
        )
        self.assertEqual(len(errores), 3)
        self.assertIn("registro 1 ('sim-002')", errores[0])
        self.assertIn("registro 2", errores[1])
        self.assertIn("registro 3 ('mal')", errores[2])


class TestSeleccionar(unittest.TestCase):
    REGISTROS = [registro(id="sim-001"), registro(id="sim-002"), registro(id="sim-003")]

    def ids(self, seleccion):
        return [r["id"] for r in seleccion]

    def test_sin_ids_devuelve_todos(self):
        self.assertEqual(self.ids(publish.seleccionar(self.REGISTROS, [])),
                         ["sim-001", "sim-002", "sim-003"])

    def test_conserva_el_orden_del_dataset(self):
        seleccion = publish.seleccionar(self.REGISTROS, ["sim-003", "sim-001"])
        self.assertEqual(self.ids(seleccion), ["sim-001", "sim-003"])

    def test_ids_repetidos_se_publican_una_vez(self):
        seleccion = publish.seleccionar(self.REGISTROS, ["sim-002", "sim-002"])
        self.assertEqual(self.ids(seleccion), ["sim-002"])

    def test_id_inexistente(self):
        with self.assertRaisesRegex(ValueError, "sim-999"):
            publish.seleccionar(self.REGISTROS, ["sim-001", "sim-999"])


class TestConstruirEvento(unittest.TestCase):
    def test_exactamente_tres_campos(self):
        evento = publish.construir_evento(REAL)
        self.assertEqual(set(evento), {"id", "latitud", "longitud"})
        self.assertEqual(evento, {"id": "csn-385887", "latitud": -36.1, "longitud": -71.84})

    def test_valores_sin_transformar(self):
        original = registro(latitud=-33, longitud=-71.62963)
        evento = publish.construir_evento(original)
        for campo in publish.CAMPOS_EVENTO:
            self.assertIs(evento[campo], original[campo])

    def test_json_serializado(self):
        cuerpo = json.dumps(publish.construir_evento(FICTICIO)).encode("utf-8")
        self.assertEqual(
            json.loads(cuerpo.decode("utf-8")),
            {"id": "sim-001", "latitud": -33.05, "longitud": -71.62},
        )


class TestPublicar(unittest.TestCase):
    EVENTOS = [publish.construir_evento(REAL), publish.construir_evento(FICTICIO)]

    def test_publica_en_el_exchange_con_propiedades_persistentes(self):
        canal = mock.MagicMock()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(publish.publicar(canal, self.EVENTOS), 2)

        nombres = [llamada[0] for llamada in canal.method_calls]
        self.assertEqual(nombres, ["exchange_declare", "basic_publish", "basic_publish"])
        canal.exchange_declare.assert_called_once_with(
            exchange="sismos", exchange_type="fanout", durable=True
        )
        canal.queue_declare.assert_not_called()
        canal.queue_bind.assert_not_called()

        for llamada, evento in zip(canal.basic_publish.call_args_list, self.EVENTOS):
            self.assertEqual(llamada.kwargs["exchange"], "sismos")
            self.assertEqual(llamada.kwargs["routing_key"], "")
            propiedades = llamada.kwargs["properties"]
            self.assertIs(propiedades, mensajeria.PROPIEDADES_MENSAJE)
            self.assertEqual(propiedades.content_type, "application/json")
            self.assertEqual(propiedades.delivery_mode, 2)
            self.assertEqual(json.loads(llamada.kwargs["body"].decode("utf-8")), evento)

    def test_error_amqp_informa_publicados_e_id(self):
        canal = mock.MagicMock()
        canal.basic_publish.side_effect = [None, pika.exceptions.AMQPConnectionError()]
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(publish.ErrorPublicacion) as contexto:
                publish.publicar(canal, self.EVENTOS)
        self.assertEqual(contexto.exception.publicados, 1)
        self.assertEqual(contexto.exception.id_sismo, "sim-001")

    def test_error_amqp_al_declarar_exchange(self):
        canal = mock.MagicMock()
        canal.exchange_declare.side_effect = pika.exceptions.AMQPChannelError()
        with self.assertRaises(publish.ErrorPublicacion) as contexto:
            publish.publicar(canal, self.EVENTOS)
        self.assertEqual(contexto.exception.publicados, 0)
        self.assertIsNone(contexto.exception.id_sismo)
        canal.basic_publish.assert_not_called()


class TestMain(ConArchivoTemporal):
    def ejecutar(self, argv, canal=None):
        """Ejecuta main con mensajeria.conectar reemplazado; devuelve código, salidas y mock."""
        canal = canal or mock.MagicMock()
        conectar = mock.MagicMock()
        conexion = conectar.return_value.__enter__.return_value
        conexion.channel.return_value.__enter__.return_value = canal
        salida, errores = io.StringIO(), io.StringIO()
        with mock.patch("mensajeria.conectar", conectar), \
                contextlib.redirect_stdout(salida), contextlib.redirect_stderr(errores):
            codigo = publish.main(argv)
        return codigo, salida.getvalue(), errores.getvalue(), conectar, canal

    def publicados(self, canal):
        return [json.loads(llamada.kwargs["body"])["id"]
                for llamada in canal.basic_publish.call_args_list]

    def test_publica_todo_por_defecto(self):
        ruta = self.escribir([REAL, FICTICIO])
        codigo, salida, _, conectar, canal = self.ejecutar(["--dataset", str(ruta)])
        self.assertEqual(codigo, 0)
        conectar.assert_called_once()
        self.assertEqual(self.publicados(canal), ["csn-385887", "sim-001"])
        self.assertIn("2 evento(s) publicados", salida)

    def test_publica_solo_ids_pedidos_en_orden_del_dataset(self):
        ruta = self.escribir([REAL, FICTICIO, registro(id="sim-002")])
        codigo, _, _, _, canal = self.ejecutar(
            ["--dataset", str(ruta), "sim-002", "csn-385887", "sim-002"]
        )
        self.assertEqual(codigo, 0)
        self.assertEqual(self.publicados(canal), ["csn-385887", "sim-002"])

    def test_id_inexistente_no_publica_ni_conecta(self):
        ruta = self.escribir([REAL, FICTICIO])
        codigo, _, errores, conectar, _ = self.ejecutar(["--dataset", str(ruta), "sim-001", "sim-404"])
        self.assertEqual(codigo, 1)
        self.assertIn("sim-404", errores)
        conectar.assert_not_called()

    def test_dataset_invalido_no_publica_ni_conecta(self):
        ruta = self.escribir([REAL, registro(latitud=math.nan), FICTICIO])
        codigo, _, errores, conectar, canal = self.ejecutar(["--dataset", str(ruta)])
        self.assertEqual(codigo, 1)
        self.assertIn("no se publicó nada", errores)
        self.assertIn("registro 1", errores)
        conectar.assert_not_called()
        canal.basic_publish.assert_not_called()

    def test_archivo_inexistente_o_json_invalido_no_conecta(self):
        for ruta in [self.directorio / "no_existe.json", self.escribir("{mal json")]:
            with self.subTest(ruta=ruta):
                codigo, _, errores, conectar, _ = self.ejecutar(["--dataset", str(ruta)])
                self.assertEqual(codigo, 1)
                self.assertTrue(errores.startswith("Error:"))
                conectar.assert_not_called()

    def test_dataset_vacio_no_conecta(self):
        codigo, salida, _, conectar, _ = self.ejecutar(["--dataset", str(self.escribir([]))])
        self.assertEqual(codigo, 0)
        self.assertIn("No hay sismos que publicar", salida)
        conectar.assert_not_called()

    def test_error_amqp_durante_publicacion(self):
        canal = mock.MagicMock()
        canal.basic_publish.side_effect = [None, pika.exceptions.AMQPConnectionError()]
        ruta = self.escribir([REAL, FICTICIO])
        codigo, _, errores, _, _ = self.ejecutar(["--dataset", str(ruta)], canal)
        self.assertEqual(codigo, 1)
        self.assertIn("al publicar sim-001", errores)
        self.assertIn("publicados antes del fallo: 1 de 2", errores)


class TestValidacionCompartida(unittest.TestCase):
    def test_ciudades_usa_la_validacion_de_mensajeria(self):
        self.assertIs(subscriber.ciudades.validar_coordenadas, mensajeria.validar_coordenadas)
        with self.assertRaises(ValueError):
            subscriber.ciudades.distancia_km("arica", 0, 190)

    def test_validar_id(self):
        for id_sismo in ["csn-385887", "sim-001", "csn-0", "sim-123456789"]:
            with self.subTest(id=id_sismo):
                mensajeria.validar_id(id_sismo)
        for id_sismo in ["csn-1\n", "CSN-1", "sim-1234567890", 1, None]:
            with self.subTest(id=id_sismo):
                with self.assertRaises(ValueError):
                    mensajeria.validar_id(id_sismo)


if __name__ == "__main__":
    unittest.main()
