"""Pruebas de subscriber/subscriber.py.

No usan RabbitMQ, el servicio HTTP ni data/sismos.json: el canal, la conexión
y urlopen se reemplazan por mocks.

Ejecutar desde la raíz del repositorio:
    .venv/bin/python -m unittest discover -s tests -v
"""

import contextlib
import http.client
import io
import json
import os
import unittest
import urllib.error
from unittest import mock

import pika

import mensajeria
from subscriber import ciudades
from subscriber import subscriber as sub

URL = "http://localhost:8000"
# Evento csn-385887 de CONTRATOS.md: a 135 km de Concepción y 1959 km de Arica.
EVENTO = {"id": "csn-385887", "latitud": -36.1, "longitud": -71.84}
DETALLE = {
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


def cuerpo(datos):
    return json.dumps(datos).encode("utf-8")


def respuesta_http(estado=200, contenido=None, error_lectura=None):
    """Objeto que imita la respuesta de urlopen usada como gestor de contexto."""
    respuesta = mock.MagicMock()
    respuesta.__enter__.return_value = respuesta
    respuesta.status = estado
    if error_lectura:
        respuesta.read.side_effect = error_lectura
    else:
        respuesta.read.return_value = cuerpo(DETALLE) if contenido is None else contenido
    return respuesta


def error_http(codigo):
    return urllib.error.HTTPError(
        f"{URL}/earthquakes/x", codigo, "error", {}, io.BytesIO(b'{"detail": "x"}')
    )


def silenciar():
    """Descarta stdout y stderr durante la prueba."""
    pila = contextlib.ExitStack()
    pila.enter_context(contextlib.redirect_stdout(io.StringIO()))
    pila.enter_context(contextlib.redirect_stderr(io.StringIO()))
    return pila


class TestDecodificarEvento(unittest.TestCase):
    def test_evento_valido_con_coordenadas_decimales_y_enteras(self):
        self.assertEqual(sub.decodificar_evento(cuerpo(EVENTO)), EVENTO)
        enteras = {"id": "sim-001", "latitud": -33, "longitud": -71}
        self.assertEqual(sub.decodificar_evento(cuerpo(enteras)), enteras)

    def assertInvalido(self, datos_crudos, fragmento):
        with self.assertRaises(sub.MensajeInvalido) as contexto:
            sub.decodificar_evento(datos_crudos)
        self.assertIn(fragmento, str(contexto.exception))

    def test_cuerpo_no_utf8(self):
        self.assertInvalido(b"\xff\xfe{}", "UTF-8")
        # json.loads(bytes) aceptaría UTF-16; aquí se exige UTF-8 estricto.
        self.assertInvalido(json.dumps(EVENTO).encode("utf-16"), "UTF-8")

    def test_json_invalido(self):
        for crudo in [b"{", b"", b"no es json", b'{"id": "sim-001",}']:
            with self.subTest(crudo=crudo):
                self.assertInvalido(crudo, "JSON inválido")

    def test_json_que_no_es_objeto(self):
        for datos in [[EVENTO], "csn-385887", 1, None]:
            with self.subTest(datos=datos):
                self.assertInvalido(cuerpo(datos), "objeto JSON")

    def test_campos_faltantes(self):
        for campo in EVENTO:
            with self.subTest(campo=campo):
                incompleto = {k: v for k, v in EVENTO.items() if k != campo}
                self.assertInvalido(cuerpo(incompleto), f"faltan los campos {campo}")

    def test_campos_adicionales(self):
        self.assertInvalido(cuerpo({**EVENTO, "magnitud": 2.5}), "campos no permitidos: magnitud")

    def test_id_invalido_por_formato_y_por_tipo(self):
        self.assertInvalido(cuerpo({**EVENTO, "id": "CSN-385887"}), "formato inválido")
        self.assertInvalido(cuerpo({**EVENTO, "id": "csn-1\n"}), "formato inválido")
        self.assertInvalido(cuerpo({**EVENTO, "id": 385887}), "id debe ser un string")

    def test_coordenadas_invalidas(self):
        casos = [
            ('{"id": "sim-001", "latitud": true, "longitud": 0}', "latitud debe ser un número"),
            ('{"id": "sim-001", "latitud": NaN, "longitud": 0}', "latitud debe ser finita"),
            ('{"id": "sim-001", "latitud": 0, "longitud": Infinity}', "longitud debe ser finita"),
            ('{"id": "sim-001", "latitud": 0, "longitud": 1e400}', "longitud debe ser finita"),
            ('{"id": "sim-001", "latitud": 91, "longitud": 0}', "latitud fuera del rango"),
            ('{"id": "sim-001", "latitud": 0, "longitud": -180.5}', "longitud fuera del rango"),
            ('{"id": "sim-001", "latitud": "1.0", "longitud": 0}', "latitud debe ser un número"),
        ]
        for crudo, fragmento in casos:
            with self.subTest(crudo=crudo):
                self.assertInvalido(crudo.encode("utf-8"), fragmento)

    def test_reutiliza_las_validaciones_compartidas(self):
        with mock.patch("mensajeria.validar_id", wraps=mensajeria.validar_id) as validar_id, \
                mock.patch("mensajeria.validar_coordenadas",
                           wraps=mensajeria.validar_coordenadas) as validar_coordenadas:
            sub.decodificar_evento(cuerpo(EVENTO))
        validar_id.assert_called_once_with("csn-385887")
        validar_coordenadas.assert_called_once_with(-36.1, -71.84)


class TestProcesarMensaje(unittest.TestCase):
    def test_menos_de_500_km_consulta_http(self):
        with mock.patch.object(sub, "consultar_sismo", return_value=DETALLE) as consultar:
            resultado = sub.procesar_mensaje("concepcion", cuerpo(EVENTO), URL)
        self.assertTrue(resultado.exito)
        consultar.assert_called_once_with(URL, "csn-385887")
        self.assertIn("135.2 km", resultado.descripcion)
        self.assertIn("consulta HTTP 200", resultado.descripcion)
        self.assertIn("M2.5 MLv", resultado.descripcion)

    def test_exactamente_500_km_no_consulta(self):
        with mock.patch("subscriber.ciudades.distancia_km", return_value=500.0), \
                mock.patch.object(sub, "consultar_sismo") as consultar:
            resultado = sub.procesar_mensaje("concepcion", cuerpo(EVENTO), URL)
        self.assertTrue(resultado.exito)
        consultar.assert_not_called()
        self.assertIn("sin consulta HTTP", resultado.descripcion)

    def test_mas_de_500_km_no_consulta(self):
        with mock.patch.object(sub, "consultar_sismo") as consultar:
            resultado = sub.procesar_mensaje("arica", cuerpo(EVENTO), URL)
        self.assertTrue(resultado.exito)
        consultar.assert_not_called()
        self.assertIn("1959.0 km (≥ 500 km)", resultado.descripcion)

    def test_error_de_consulta_es_fallo(self):
        with mock.patch.object(sub, "consultar_sismo", side_effect=sub.ErrorConsulta("HTTP 404")):
            resultado = sub.procesar_mensaje("concepcion", cuerpo(EVENTO), URL)
        self.assertFalse(resultado.exito)
        self.assertIn("HTTP 404", resultado.descripcion)

    def test_mensaje_invalido_es_fallo_sin_consulta(self):
        with mock.patch.object(sub, "consultar_sismo") as consultar:
            resultado = sub.procesar_mensaje("concepcion", b"{", URL)
        self.assertFalse(resultado.exito)
        consultar.assert_not_called()
        self.assertIn("mensaje inválido", resultado.descripcion)


class TestConsultarSismo(unittest.TestCase):
    def consultar(self, url_base=URL, **urlopen):
        with mock.patch("urllib.request.urlopen", **urlopen) as abrir:
            return sub.consultar_sismo(url_base, "csn-385887"), abrir

    def assertErrorConsulta(self, fragmento, **urlopen):
        with self.assertRaises(sub.ErrorConsulta) as contexto:
            self.consultar(**urlopen)
        self.assertIn(fragmento, str(contexto.exception))

    def test_200_valido_url_y_timeout(self):
        for url_base in [URL, URL + "/", URL + "//"]:
            with self.subTest(url_base=url_base):
                detalle, abrir = self.consultar(url_base, return_value=respuesta_http())
                self.assertEqual(detalle, DETALLE)
                abrir.assert_called_once_with(f"{URL}/earthquakes/csn-385887", timeout=5)

    def test_tolera_campos_ausentes_en_la_respuesta(self):
        detalle, _ = self.consultar(return_value=respuesta_http(contenido=b'{"id": "csn-385887"}'))
        self.assertEqual(detalle, {"id": "csn-385887"})
        self.assertEqual(sub._resumir(detalle), "M?, prof. ? km, ?, ?")

    def test_http_404_422_500(self):
        for codigo in (404, 422, 500):
            with self.subTest(codigo=codigo):
                self.assertErrorConsulta(f"HTTP {codigo}", side_effect=error_http(codigo))

    def test_http_204_no_es_exito(self):
        self.assertErrorConsulta("HTTP 204 (se esperaba 200)",
                                 return_value=respuesta_http(204, b""))

    def test_respuestas_200_inutilizables(self):
        casos = [
            (b"", "no es JSON"),
            (b"{mal", "no es JSON"),
            (b"\xff", "no es JSON"),
            (b"[]", "no es un objeto JSON"),
            (b'{"magnitud": 2.5}', "tiene id None"),
            (b'{"id": "sim-001"}', "tiene id 'sim-001'"),
        ]
        for contenido, fragmento in casos:
            with self.subTest(contenido=contenido):
                self.assertErrorConsulta(fragmento, return_value=respuesta_http(contenido=contenido))

    def test_timeouts(self):
        casos = {
            "directo": {"side_effect": TimeoutError("timed out")},
            "dentro de URLError": {"side_effect": urllib.error.URLError(TimeoutError("timed out"))},
            "durante la lectura": {
                "return_value": respuesta_http(error_lectura=TimeoutError("timed out"))
            },
        }
        for nombre, urlopen in casos.items():
            with self.subTest(caso=nombre):
                self.assertErrorConsulta("timeout (5 s por operación de red)", **urlopen)

    def test_conexion_rechazada(self):
        self.assertErrorConsulta(
            "ConnectionRefusedError",
            side_effect=urllib.error.URLError(ConnectionRefusedError(111, "Connection refused")),
        )

    def test_errores_de_protocolo(self):
        self.assertErrorConsulta(
            "IncompleteRead",
            return_value=respuesta_http(error_lectura=http.client.IncompleteRead(b"{")),
        )
        self.assertErrorConsulta(
            "RemoteDisconnected",
            side_effect=http.client.RemoteDisconnected("cerrado sin respuesta"),
        )


class TestAlRecibir(unittest.TestCase):
    def recibir(self, canal, cuerpo_mensaje, delivery_tag=7, ciudad="concepcion"):
        metodo = mock.Mock(delivery_tag=delivery_tag)
        with silenciar():
            sub.al_recibir(canal, metodo, mock.Mock(), cuerpo_mensaje, ciudad=ciudad, url_base=URL)

    def test_mensaje_mal_formado_hace_nack_sin_consultar(self):
        canal = mock.MagicMock()
        with mock.patch("urllib.request.urlopen") as abrir:
            self.recibir(canal, b'{"id": "sim-001"}')
        canal.basic_nack.assert_called_once_with(delivery_tag=7, requeue=False)
        canal.basic_ack.assert_not_called()
        abrir.assert_not_called()

    def test_mensaje_valido_lejano_hace_ack_sin_consultar(self):
        canal = mock.MagicMock()
        with mock.patch("urllib.request.urlopen") as abrir:
            self.recibir(canal, cuerpo(EVENTO), ciudad="arica")
        canal.basic_ack.assert_called_once_with(delivery_tag=7)
        canal.basic_nack.assert_not_called()
        abrir.assert_not_called()

    def test_error_http_hace_nack(self):
        canal = mock.MagicMock()
        with mock.patch("urllib.request.urlopen", side_effect=error_http(404)):
            self.recibir(canal, cuerpo(EVENTO))
        canal.basic_nack.assert_called_once_with(delivery_tag=7, requeue=False)
        canal.basic_ack.assert_not_called()

    def test_excepcion_inesperada_no_se_propaga_y_hace_nack(self):
        canal = mock.MagicMock()
        errores = io.StringIO()
        with mock.patch.object(sub, "procesar_mensaje", side_effect=RuntimeError("falla")), \
                contextlib.redirect_stderr(errores):
            sub.al_recibir(canal, mock.Mock(delivery_tag=3), mock.Mock(), b"x",
                           ciudad="arica", url_base=URL)
        canal.basic_nack.assert_called_once_with(delivery_tag=3, requeue=False)
        canal.basic_ack.assert_not_called()
        self.assertIn("RuntimeError: falla", errores.getvalue())

    def test_confirma_despues_de_procesar(self):
        registro = mock.Mock()
        registro.procesar.return_value = sub.Resultado(True, "ok")
        with mock.patch.object(sub, "procesar_mensaje", registro.procesar):
            self.recibir(registro.canal, cuerpo(EVENTO))
        self.assertEqual(
            [llamada[0] for llamada in registro.mock_calls],
            ["procesar", "canal.basic_ack"],
        )

    def test_fallo_de_basic_ack_se_propaga_sin_nack(self):
        canal = mock.MagicMock()
        canal.basic_ack.side_effect = pika.exceptions.StreamLostError("conexión perdida")
        with mock.patch.object(sub, "procesar_mensaje", return_value=sub.Resultado(True, "ok")):
            with self.assertRaises(pika.exceptions.StreamLostError):
                self.recibir(canal, cuerpo(EVENTO))
        canal.basic_ack.assert_called_once()
        canal.basic_nack.assert_not_called()


class TestValidarUrlBase(unittest.TestCase):
    def test_urls_validas(self):
        casos = {
            "http://localhost:8000": "http://localhost:8000",
            "http://localhost:8000/": "http://localhost:8000",
            "https://api.example.cl": "https://api.example.cl",
            "http://127.0.0.1:9000/base/": "http://127.0.0.1:9000/base",
        }
        for url, esperada in casos.items():
            with self.subTest(url=url):
                self.assertEqual(sub.validar_url_base(url), esperada)

    def test_urls_invalidas(self):
        for url in ["localhost:8000", "ftp://localhost", "http://", "http://:8000",
                    "http://localhost:abc", "http://localhost:99999", "", "http://exa mple"]:
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    sub.validar_url_base(url)


class TestMain(unittest.TestCase):
    def setUp(self):
        entorno = mock.patch.dict(os.environ)
        entorno.start()
        self.addCleanup(entorno.stop)
        os.environ.pop("SISMOS_API_URL", None)

    def ejecutar(self, argv, canal=None, conectar=None):
        """Ejecuta main con mensajeria.conectar reemplazado."""
        canal = canal or mock.MagicMock()
        if conectar is None:
            conectar = mock.MagicMock()
            conexion = conectar.return_value.__enter__.return_value
            conexion.channel.return_value.__enter__.return_value = canal
        salida, errores = io.StringIO(), io.StringIO()
        with mock.patch("mensajeria.conectar", conectar), \
                contextlib.redirect_stdout(salida), contextlib.redirect_stderr(errores):
            codigo = sub.main(argv)
        return codigo, salida.getvalue(), errores.getvalue(), conectar, canal

    def test_arranque_configura_qos_y_consumo_manual(self):
        canal = mock.MagicMock()
        canal.start_consuming.side_effect = KeyboardInterrupt
        codigo, salida, _, _, canal = self.ejecutar(["arica"], canal)
        self.assertEqual(codigo, 0)
        nombres = [llamada[0] for llamada in canal.method_calls]
        # declarar_cola (exchange, cola y binding) → qos → consume → start_consuming.
        self.assertEqual(
            nombres,
            ["exchange_declare", "queue_declare", "queue_bind",
             "basic_qos", "basic_consume", "start_consuming"],
        )
        canal.queue_declare.assert_called_once_with(
            queue="sismos.arica", durable=True, exclusive=False, auto_delete=False
        )
        canal.basic_qos.assert_called_once_with(prefetch_count=1)
        argumentos = canal.basic_consume.call_args.kwargs
        self.assertEqual(argumentos["queue"], "sismos.arica")
        self.assertIs(argumentos["auto_ack"], False)
        self.assertIn("http://localhost:8000", salida)

    def test_ciclo_de_consumo_continua_tras_mensajes_defectuosos(self):
        """Simula start_consuming: invoca el callback sin protegerlo, como Pika.

        No es una prueba de integración: el canal es un mock.
        """
        canal = mock.MagicMock()
        entregas = [
            (1, b"{"),                                                     # mal formado
            (2, cuerpo({"id": "sim-002", "latitud": -18.0, "longitud": -70.0})),  # falla inesperada
            (3, cuerpo(EVENTO)),                                           # válido, 1959 km
        ]

        def start_consuming():
            callback = canal.basic_consume.call_args.kwargs["on_message_callback"]
            for tag, contenido in entregas:
                callback(canal, mock.Mock(delivery_tag=tag), mock.Mock(), contenido)
            raise KeyboardInterrupt

        canal.start_consuming.side_effect = start_consuming
        distancia_real = ciudades.distancia_km

        def distancia(ciudad, latitud, longitud):
            if latitud == -18.0:
                raise RuntimeError("falla inesperada simulada")
            return distancia_real(ciudad, latitud, longitud)

        with mock.patch("subscriber.ciudades.distancia_km", side_effect=distancia), \
                mock.patch("urllib.request.urlopen") as abrir:
            codigo, _, errores, _, _ = self.ejecutar(["arica"], canal)

        self.assertEqual(codigo, 0)
        confirmaciones = [
            (llamada[0], llamada.kwargs) for llamada in canal.method_calls
            if llamada[0] in ("basic_ack", "basic_nack")
        ]
        self.assertEqual(confirmaciones, [
            ("basic_nack", {"delivery_tag": 1, "requeue": False}),
            ("basic_nack", {"delivery_tag": 2, "requeue": False}),
            ("basic_ack", {"delivery_tag": 3}),
        ])
        abrir.assert_not_called()
        self.assertIn("RuntimeError: falla inesperada simulada", errores)

    def test_ciudad_desconocida(self):
        conectar = mock.MagicMock()
        with mock.patch("mensajeria.conectar", conectar), silenciar():
            with self.assertRaises(SystemExit) as contexto:
                sub.main(["santiago"])
        self.assertEqual(contexto.exception.code, 2)
        conectar.assert_not_called()

    def test_ciudades_validas_vienen_de_mensajeria(self):
        accion = next(a for a in sub.crear_parser()._actions if a.dest == "ciudad")
        self.assertIs(accion.choices, mensajeria.CIUDADES)

    def test_url_invalida(self):
        for url in ["localhost:8000", "ftp://x", "http://", "http://x:abc"]:
            with self.subTest(url=url):
                os.environ["SISMOS_API_URL"] = url
                codigo, _, errores, conectar, _ = self.ejecutar(["arica"])
                self.assertEqual(codigo, 2)
                self.assertIn("SISMOS_API_URL inválida", errores)
                conectar.assert_not_called()

    def test_url_desde_variable_de_entorno(self):
        os.environ["SISMOS_API_URL"] = "http://127.0.0.1:9000/"
        canal = mock.MagicMock()
        canal.start_consuming.side_effect = KeyboardInterrupt
        _, salida, _, _, canal = self.ejecutar(["arica"], canal)
        callback = canal.basic_consume.call_args.kwargs["on_message_callback"]
        self.assertEqual(callback.keywords, {"ciudad": "arica", "url_base": "http://127.0.0.1:9000"})
        self.assertIn("HTTP: http://127.0.0.1:9000)", salida)

    def test_broker_no_disponible_al_arrancar(self):
        # mensajeria.conectar() termina con sys.exit(mensaje): código de salida 1.
        conectar = mock.MagicMock(side_effect=SystemExit("Error: no se pudo conectar"))
        with self.assertRaises(SystemExit) as contexto:
            self.ejecutar(["arica"], conectar=conectar)
        self.assertIsInstance(contexto.exception.code, str)

    def test_error_amqp_al_declarar_la_cola(self):
        canal = mock.MagicMock()
        canal.queue_declare.side_effect = pika.exceptions.ChannelClosedByBroker(
            406, "PRECONDITION_FAILED"
        )
        codigo, _, errores, _, canal = self.ejecutar(["arica"], canal)
        self.assertEqual(codigo, 1)
        self.assertIn("Error de RabbitMQ", errores)
        self.assertIn("PRECONDITION_FAILED", errores)
        canal.basic_consume.assert_not_called()

    def test_error_amqp_durante_el_consumo(self):
        canal = mock.MagicMock()
        canal.start_consuming.side_effect = pika.exceptions.StreamLostError("conexión perdida")
        codigo, _, errores, _, _ = self.ejecutar(["arica"], canal)
        self.assertEqual(codigo, 1)
        self.assertIn("StreamLostError", errores)

    def test_error_amqp_al_confirmar_termina_el_proceso(self):
        canal = mock.MagicMock()
        canal.basic_ack.side_effect = pika.exceptions.StreamLostError("conexión perdida")

        def start_consuming():
            callback = canal.basic_consume.call_args.kwargs["on_message_callback"]
            callback(canal, mock.Mock(delivery_tag=1), mock.Mock(), cuerpo(EVENTO))

        canal.start_consuming.side_effect = start_consuming
        codigo, _, errores, _, canal = self.ejecutar(["arica"], canal)
        self.assertEqual(codigo, 1)
        self.assertIn("StreamLostError", errores)
        canal.basic_nack.assert_not_called()

    def test_start_consuming_retorna_normalmente(self):
        codigo, _, errores, _, _ = self.ejecutar(["arica"])
        self.assertEqual(codigo, 1)
        self.assertIn("terminó inesperadamente", errores)

    def test_ctrl_c_cierra_recursos(self):
        canal = mock.MagicMock()
        canal.start_consuming.side_effect = KeyboardInterrupt
        conectar = mock.MagicMock()
        conexion = conectar.return_value.__enter__.return_value
        gestor_canal = conexion.channel.return_value
        gestor_canal.__enter__.return_value = canal
        codigo, salida, _, _, _ = self.ejecutar(["arica"], canal, conectar)
        self.assertEqual(codigo, 0)
        self.assertIn("Detenido por el usuario", salida)
        gestor_canal.__exit__.assert_called_once()
        conectar.return_value.__exit__.assert_called_once()
        self.assertIs(gestor_canal.__exit__.call_args.args[0], KeyboardInterrupt)


if __name__ == "__main__":
    unittest.main()
