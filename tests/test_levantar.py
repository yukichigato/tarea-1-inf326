"""Pruebas de levantar.py con procesos hijos ficticios.

No usan RabbitMQ ni el servicio HTTP: los hijos son pequeños programas Python
que imitan las señales de arranque y de parada.

Ejecutar desde la raíz del repositorio:
    .venv/bin/python -m unittest discover -s tests -v
"""

import io
import os
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

import levantar

RAIZ = Path(levantar.__file__).resolve().parent

# Hijo ficticio: el primer argumento elige el comportamiento.
HIJO = r"""
import signal, sys, time
modo = sys.argv[1]
if modo == "falla":
    print("arrancando", flush=True)
    sys.exit(3)
if modo == "ignora_sigint":
    signal.signal(signal.SIGINT, signal.SIG_IGN)
if modo == "verboso":
    for i in range(20000):
        print(f"línea {i}", flush=True)
if modo != "mudo":
    print("hijo LISTO", flush=True)
if modo == "muere":
    time.sleep(0.3)
    sys.exit(5)
try:
    while True:
        time.sleep(0.1)
except KeyboardInterrupt:
    print("SIGINT recibido", flush=True)
    sys.exit(0)
"""


def hijo(nombre, modo):
    return levantar.Proceso(nombre, [sys.executable, "-c", HIJO, modo], marca="LISTO")


def vivo(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class ConSupervisor(unittest.TestCase):
    def setUp(self):
        self.salida = io.StringIO()
        self.sup = levantar.Supervisor(timeout_arranque=5, timeout_parada=1, salida=self.salida)
        # Ninguna prueba debe dejar hijos vivos.
        self.addCleanup(self.sup.parar)

    def popens(self):
        return [popen for _, popen, _, _ in self.sup._hijos]


class TestArranque(ConSupervisor):
    def test_espera_la_marca_de_listo(self):
        self.sup.arrancar([hijo("a", "normal"), hijo("b", "normal")])
        self.assertEqual(set(self.sup.pids()), {"a", "b"})
        self.assertTrue(all(p.poll() is None for p in self.popens()))
        self.assertIn("[a] hijo LISTO", self.salida.getvalue())

    def test_salida_abundante_no_bloquea(self):
        self.sup.arrancar([hijo("v", "verboso")])
        self.assertIn("[v] línea 19999", self.salida.getvalue())

    def test_hijo_que_falla_durante_el_arranque(self):
        with self.assertRaisesRegex(levantar.ErrorArranque, r"'f' terminó durante el arranque \(código 3\)"):
            self.sup.arrancar([hijo("ok", "normal"), hijo("f", "falla")])
        self.sup.parar()
        self.assertTrue(all(p.poll() is not None for p in self.popens()))

    def test_hijo_que_no_queda_listo_a_tiempo(self):
        self.sup.timeout_arranque = 1
        with self.assertRaisesRegex(levantar.ErrorArranque, "'m' no quedó listo en 1 s"):
            self.sup.arrancar([hijo("m", "mudo")])

    def test_comprobacion_adicional_debe_cumplirse(self):
        self.sup.timeout_arranque = 1
        proceso = hijo("h", "normal")
        proceso.comprobar = lambda: False
        with self.assertRaises(levantar.ErrorArranque):
            self.sup.arrancar([proceso])

    def test_detener_durante_el_arranque(self):
        self.sup.detener.set()
        with self.assertRaises(levantar.Interrumpido):
            self.sup.arrancar([hijo("m", "mudo")])


class TestParada(ConSupervisor):
    def test_sigint_a_cada_hijo(self):
        self.sup.arrancar([hijo("a", "normal"), hijo("b", "normal")])
        self.sup.parar()
        self.assertEqual([p.returncode for p in self.popens()], [0, 0])
        self.assertEqual(self.salida.getvalue().count("SIGINT recibido"), 2)

    def test_escala_a_sigterm_si_se_ignora_sigint(self):
        self.sup.arrancar([hijo("t", "ignora_sigint")])
        self.sup.parar()
        (popen,) = self.popens()
        self.assertEqual(popen.returncode, -signal.SIGTERM)
        self.assertIn("Enviado SIGTERM a: t", self.salida.getvalue())

    def test_escalada_con_salida_que_falla(self):
        # Simula una terminal cerrada: toda escritura falla con OSError.
        class SalidaRota:
            def write(self, _texto):
                raise OSError(5, "Input/output error")

            def flush(self):
                raise OSError(5, "Input/output error")

        sup = levantar.Supervisor(timeout_arranque=5, timeout_parada=1, salida=SalidaRota())
        self.addCleanup(sup.parar)
        sup.arrancar([hijo("t", "ignora_sigint")])
        sup.parar()
        (popen,) = [h for _, h, _, _ in sup._hijos]
        self.assertEqual(popen.returncode, -signal.SIGTERM)

    def test_manejador_de_senal_no_escribe(self):
        with mock.patch.object(self.sup, "emitir") as emitir:
            self.sup._al_recibir_senal(signal.SIGHUP, None)
        emitir.assert_not_called()
        self.assertTrue(self.sup.detener.is_set())
        self.assertEqual(self.sup.senal, "SIGHUP")
        self.assertEqual(self.sup.supervisar(), 0)
        self.assertIn("SIGHUP recibida: deteniendo", self.salida.getvalue())

    def test_hijo_que_termina_inesperadamente(self):
        self.sup.arrancar([hijo("ok", "normal"), hijo("x", "muere")])
        self.assertEqual(self.sup.supervisar(), 1)
        self.assertIn("'x' terminó inesperadamente (código 5)", self.salida.getvalue())
        self.sup.parar()
        self.assertTrue(all(p.poll() is not None for p in self.popens()))

    def test_supervisar_devuelve_0_al_pedir_detener(self):
        self.sup.arrancar([hijo("a", "normal")])
        self.sup.detener.set()
        self.assertEqual(self.sup.supervisar(), 0)


# Supervisor real en un proceso aparte, iniciado con SIGINT ignorado (como un
# proceso en segundo plano), para probar el manejo explícito de señales.
ARNES = r"""
import signal, sys
signal.signal(signal.SIGINT, signal.SIG_IGN)
import levantar
HIJO = sys.argv[1]
sup = levantar.Supervisor(timeout_arranque=10, timeout_parada=3)
sup.instalar_senales()
sup.arrancar([levantar.Proceso(n, [sys.executable, "-c", HIJO, "normal"], "LISTO") for n in ("a", "b")])
print("PIDS", *sup.pids().values(), flush=True)
codigo = sup.supervisar()
sup.parar()
sys.exit(codigo)
"""


class TestSenalesAlSupervisor(unittest.TestCase):
    def ejecutar_con_senal(self, senal):
        arnes = subprocess.Popen(
            [sys.executable, "-c", ARNES, HIJO], cwd=RAIZ, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True,
        )
        self.addCleanup(lambda: arnes.poll() is None and arnes.kill())
        pids = []
        limite = time.monotonic() + 15
        while time.monotonic() < limite:
            linea = arnes.stdout.readline()
            if linea.startswith("PIDS"):
                pids = [int(p) for p in linea.split()[1:]]
                break
        self.assertEqual(len(pids), 2, "el arnés no llegó a iniciar los hijos")
        arnes.send_signal(senal)
        resto, _ = arnes.communicate(timeout=15)
        return arnes.returncode, resto, pids

    def test_sigint_detiene_los_hijos_ordenadamente(self):
        codigo, salida, pids = self.ejecutar_con_senal(signal.SIGINT)
        self.assertEqual(codigo, 0)
        self.assertIn("SIGINT recibida: deteniendo", salida)
        # Los hijos reciben SIGINT aunque el supervisor partió con SIGINT ignorado.
        self.assertEqual(salida.count("SIGINT recibido"), 2)
        self.assertFalse(any(vivo(pid) for pid in pids))

    def test_sigterm_detiene_los_hijos_ordenadamente(self):
        codigo, salida, pids = self.ejecutar_con_senal(signal.SIGTERM)
        self.assertEqual(codigo, 0)
        self.assertIn("SIGTERM recibida: deteniendo", salida)
        self.assertEqual(salida.count("SIGINT recibido"), 2)
        self.assertFalse(any(vivo(pid) for pid in pids))

    def test_sighup_detiene_los_hijos_ordenadamente(self):
        # Equivale a cerrar la terminal: los hijos están en otras sesiones y
        # solo se detienen si el supervisor se lo indica.
        codigo, salida, pids = self.ejecutar_con_senal(signal.SIGHUP)
        self.assertEqual(codigo, 0)
        self.assertIn("SIGHUP recibida: deteniendo", salida)
        self.assertEqual(salida.count("SIGINT recibido"), 2)
        self.assertFalse(any(vivo(pid) for pid in pids))


class TestMain(unittest.TestCase):
    def test_broker_no_disponible(self):
        fallo = subprocess.CompletedProcess([], 1, stdout="", stderr="Error: no se pudo conectar a RabbitMQ")
        with mock.patch("subprocess.run", return_value=fallo), \
                mock.patch.object(levantar.Supervisor, "instalar_senales"), \
                mock.patch.object(levantar.Supervisor, "arrancar") as arrancar, \
                mock.patch("sys.stderr", new_callable=io.StringIO) as errores:
            self.assertEqual(levantar.main(), 1)
        arrancar.assert_not_called()
        self.assertIn("no se pudo conectar a RabbitMQ", errores.getvalue())

    def test_procesos_definidos(self):
        http = levantar.proceso_http()
        self.assertEqual(http.comando[1:], ["-u", "-m", "uvicorn", "http_service.main:app",
                                            "--host", "127.0.0.1", "--port", "8000"])
        self.assertIs(http.comprobar, levantar.http_disponible)
        suscriptores = levantar.procesos_suscriptores()
        self.assertEqual([p.nombre for p in suscriptores], list(levantar.mensajeria.CIUDADES))
        for p in suscriptores:
            self.assertEqual(p.comando[1:], ["-u", "-m", "subscriber.subscriber", p.nombre])
            self.assertEqual(p.marca, "Esperando sismos")


if __name__ == "__main__":
    unittest.main()
