"""Levanta el servicio HTTP y los cinco suscriptores desde una sola terminal.

Uso, desde la raíz del repositorio y con RabbitMQ ya iniciado:
    python -m levantar

Pasos:
  1. Declara la topología con ``python -m mensajeria``; si RabbitMQ no está
     disponible, termina con error.
  2. Inicia el servicio HTTP y espera a que responda.
  3. Inicia los cinco suscriptores y espera a que cada uno consuma su cola.
  4. Solo entonces muestra "Sistema listo".

Ctrl+C (o SIGTERM) detiene todos los procesos que inició y solo esos. La
publicación sigue siendo un comando aparte, en otra terminal:
    python -m publisher.publish sim-001

No cambia el comportamiento de los componentes: ejecuta los mismos comandos que
la ejecución manual (``uvicorn http_service.main:app`` y
``python -m subscriber.subscriber <ciudad>``), cada uno en su propio proceso.
"""

import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import mensajeria

RAIZ = Path(__file__).resolve().parent
URL_HTTP = "http://127.0.0.1:8000"
TIMEOUT_ARRANQUE = 30  # segundos para que cada proceso quede listo
TIMEOUT_PARADA = 10  # segundos de espera tras SIGINT antes de enviar SIGTERM


class ErrorArranque(Exception):
    """Un proceso terminó o no quedó listo durante el arranque."""


class Interrumpido(Exception):
    """Se pidió detener el sistema durante el arranque."""


class Proceso:
    """Comando de un componente y cómo saber que quedó listo."""

    def __init__(self, nombre, comando, marca, comprobar=None):
        self.nombre = nombre
        self.comando = comando
        self.marca = marca  # texto que el proceso imprime cuando está listo
        self.comprobar = comprobar  # verificación adicional opcional (callable -> bool)


def http_disponible(url=URL_HTTP):
    """True si el servicio HTTP responde 200 en /openapi.json."""
    try:
        with urllib.request.urlopen(f"{url}/openapi.json", timeout=2) as respuesta:
            return respuesta.status == 200
    except (OSError, ValueError):
        return False


def proceso_http():
    # "Uvicorn running on" se imprime solo después de abrir el puerto; si el
    # puerto está ocupado, uvicorn termina y el arranque falla.
    return Proceso(
        "http",
        [sys.executable, "-u", "-m", "uvicorn", "http_service.main:app",
         "--host", "127.0.0.1", "--port", "8000"],
        marca="Uvicorn running on",
        comprobar=http_disponible,
    )


def procesos_suscriptores():
    # El suscriptor imprime "Esperando sismos" después de declarar su cola y
    # registrar el consumidor (basic_consume).
    return [
        Proceso(ciudad, [sys.executable, "-u", "-m", "subscriber.subscriber", ciudad],
                marca="Esperando sismos")
        for ciudad in mensajeria.CIUDADES
    ]


class Supervisor:
    """Inicia, vigila y detiene procesos hijos; solo actúa sobre los que creó."""

    def __init__(self, timeout_arranque=TIMEOUT_ARRANQUE, timeout_parada=TIMEOUT_PARADA,
                 salida=None):
        self.timeout_arranque = timeout_arranque
        self.timeout_parada = timeout_parada
        self.salida = salida or sys.stdout
        self.detener = threading.Event()
        self.senal = None  # nombre de la señal que pidió detener, si hubo una
        self._hijos = []  # (Proceso, Popen, hilo lector, evento "listo")
        self._lock = threading.Lock()

    def emitir(self, texto):
        # Si la salida ya no existe (por ejemplo, se cerró la terminal), se
        # descarta el texto: un error de escritura no debe impedir la parada.
        with self._lock:
            try:
                print(texto, file=self.salida, flush=True)
            except (OSError, ValueError):
                pass

    def pids(self):
        return {proceso.nombre: popen.pid for proceso, popen, _, _ in self._hijos}

    def instalar_senales(self):
        """Ctrl+C, SIGTERM y SIGHUP piden detener el sistema.

        Debe llamarse antes de iniciar hijos: así, aunque este proceso se haya
        lanzado con SIGINT ignorado, los hijos reciben SIGINT con su acción por
        defecto (exec restablece las señales capturadas, no las ignoradas).
        """
        for senal in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(senal, self._al_recibir_senal)

    def _al_recibir_senal(self, numero, _marco):
        # No escribe ni toma el lock de emitir(): si la señal llega mientras el
        # hilo principal está dentro de emitir(), se bloquearía a sí mismo.
        if self.senal is None:
            self.senal = signal.Signals(numero).name
        self.detener.set()

    def _leer(self, proceso, popen, listo):
        # Lee hasta EOF para que el pipe nunca se llene y bloquee al hijo.
        for linea in popen.stdout:
            linea = linea.rstrip("\n")
            if proceso.marca in linea:
                listo.set()
            self.emitir(linea if linea.startswith("[") else f"[{proceso.nombre}] {linea}")

    def _iniciar(self, proceso):
        entorno = dict(os.environ, PYTHONUNBUFFERED="1")
        popen = subprocess.Popen(
            proceso.comando, cwd=RAIZ, env=entorno, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            # Sesión propia: el Ctrl+C de la terminal llega solo al supervisor,
            # que reenvía SIGINT explícitamente a cada hijo.
            start_new_session=True,
        )
        listo = threading.Event()
        hilo = threading.Thread(target=self._leer, args=(proceso, popen, listo), daemon=True)
        hilo.start()
        self._hijos.append((proceso, popen, hilo, listo))
        return popen, listo

    def arrancar(self, procesos):
        """Inicia los procesos y espera a que todos queden listos.

        Lanza ErrorArranque si alguno termina o no queda listo a tiempo, e
        Interrumpido si se pide detener. No detiene nada: eso lo hace parar().
        """
        iniciados = [(proceso, *self._iniciar(proceso)) for proceso in procesos]
        limite = time.monotonic() + self.timeout_arranque
        for proceso, popen, listo in iniciados:
            while True:
                if self.detener.is_set():
                    raise Interrumpido()
                if listo.is_set() and (proceso.comprobar is None or proceso.comprobar()):
                    break
                codigo = popen.poll()
                if codigo is not None:
                    raise ErrorArranque(
                        f"'{proceso.nombre}' terminó durante el arranque (código {codigo})")
                if time.monotonic() > limite:
                    raise ErrorArranque(
                        f"'{proceso.nombre}' no quedó listo en {self.timeout_arranque} s")
                time.sleep(0.1)

    def supervisar(self):
        """Espera hasta que se pida detener (0) o termine un hijo inesperadamente (1)."""
        while not self.detener.wait(0.5):
            for proceso, popen, _, _ in self._hijos:
                codigo = popen.poll()
                if codigo is not None:
                    self.emitir(f"[levantar] Error: '{proceso.nombre}' terminó "
                                f"inesperadamente (código {codigo}).")
                    return 1
        self.emitir(f"[levantar] {self.senal or 'Detención'} recibida: deteniendo…")
        return 0

    def parar(self):
        """SIGINT a cada hijo vivo; si no termina a tiempo, SIGTERM y luego SIGKILL."""
        for senal, espera in ((signal.SIGINT, self.timeout_parada),
                              (signal.SIGTERM, 5), (signal.SIGKILL, 5)):
            vivos = [(p, h) for p, h, _, _ in self._hijos if h.poll() is None]
            if not vivos:
                break
            # Primero la señal y después el mensaje: un error de salida no debe
            # interrumpir la limpieza.
            for _, popen in vivos:
                try:
                    popen.send_signal(senal)
                except ProcessLookupError:
                    pass
            if senal is not signal.SIGINT:
                self.emitir(f"[levantar] Enviado {senal.name} a: "
                            f"{', '.join(p.nombre for p, _ in vivos)}")
            limite = time.monotonic() + espera
            for _, popen in vivos:
                try:
                    popen.wait(timeout=max(0, limite - time.monotonic()))
                except subprocess.TimeoutExpired:
                    pass
        for _, popen, hilo, _ in self._hijos:
            hilo.join(timeout=5)
            if popen.stdout:
                popen.stdout.close()


def main():
    supervisor = Supervisor()
    supervisor.instalar_senales()
    try:
        topologia = subprocess.run(
            [sys.executable, "-m", "mensajeria"], cwd=RAIZ,
            capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        print("Error: python -m mensajeria no respondió en 60 s.", file=sys.stderr)
        return 1
    if topologia.returncode != 0:
        print((topologia.stderr or topologia.stdout).strip(), file=sys.stderr)
        return 1
    supervisor.emitir("[levantar] RabbitMQ disponible; exchange 'sismos' y 5 colas declaradas.")

    # parar() es idempotente; el finally garantiza que no queden hijos vivos.
    try:
        try:
            supervisor.arrancar([proceso_http()])
            supervisor.arrancar(procesos_suscriptores())
        except ErrorArranque as error:
            supervisor.emitir(f"[levantar] Error de arranque: {error}. Deteniendo lo iniciado…")
            return 1
        except Interrumpido:
            supervisor.emitir(f"[levantar] {supervisor.senal or 'Detención'} recibida "
                              "durante el arranque.")
            return 1

        pids = ", ".join(f"{nombre}={pid}" for nombre, pid in supervisor.pids().items())
        supervisor.emitir(
            f"[levantar] Sistema listo (PID: {pids}).\n"
            f"[levantar] Los suscriptores consultan "
            f"{os.environ.get('SISMOS_API_URL', 'http://localhost:8000')}.\n"
            "[levantar] En otra terminal: python -m publisher.publish sim-001 · "
            "Ctrl+C aquí detiene todo."
        )
        return supervisor.supervisar()
    finally:
        supervisor.parar()
        supervisor.emitir("[levantar] Procesos iniciados por levantar detenidos.")


if __name__ == "__main__":
    sys.exit(main())
