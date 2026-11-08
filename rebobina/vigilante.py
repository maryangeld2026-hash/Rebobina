"""Servicio en segundo plano: vigila las carpetas y guarda versiones.

Espera a que un archivo deje de cambiar unos segundos antes de guardarlo (así no guarda
cada pulsación de "Guardar" a medias) y detecta cambios masivos, típicos de un virus que cifra archivos.
"""

import os
import socket
import threading
import time
from collections import deque

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .motor import Motor

ESPERA = 3.0          # segundos sin cambios antes de guardar
VENTANA_MASIVO = 60   # segundos
LIMPIEZA_CADA = 3600  # segundos
PUERTO_UNICO = 47219  # evita que haya dos servicios a la vez


class _Manejador(FileSystemEventHandler):
    def __init__(self, servicio):
        self.s = servicio

    def on_created(self, e):
        if not e.is_directory:
            self.s.pendiente(e.src_path, "modificado")

    def on_modified(self, e):
        if not e.is_directory:
            self.s.pendiente(e.src_path, "modificado")

    def on_deleted(self, e):
        self.s.pendiente(e.src_path, "borrado")

    def on_moved(self, e):
        self.s.pendiente(e.src_path, "borrado")
        if not e.is_directory:
            self.s.pendiente(e.dest_path, "modificado")
        else:
            self.s.pendiente_carpeta(e.dest_path)


class Servicio:
    def __init__(self, motor: Motor = None):
        self.motor = motor or Motor()
        self._pend = {}
        self._carpetas_pend = set()
        self._lock = threading.Lock()
        self._cambios = deque()
        self._detener = threading.Event()
        self._observador = None
        self._vigiladas = []
        self._umbral = 40
        self.alerta_desde = None

    # -------------------------------------------------------------- eventos

    def pendiente(self, ruta, tipo):
        if self.motor.ignorado(ruta):
            return
        with self._lock:
            self._pend[ruta] = (tipo, time.time())
        self._contar_cambio(ruta)

    def pendiente_carpeta(self, ruta):
        with self._lock:
            self._carpetas_pend.add(ruta)

    def _contar_cambio(self, ruta):
        """Cuenta archivos distintos cambiados en el último minuto (un mismo archivo genera varios avisos al guardarse)."""
        ahora = time.time()
        self._cambios.append((ahora, ruta))
        while self._cambios and self._cambios[0][0] < ahora - VENTANA_MASIVO:
            self._cambios.popleft()
        if self.alerta_desde is not None:
            return
        distintos = len({r for _, r in self._cambios})
        if distintos >= int(self._umbral):
            if self.motor.restaurando_hace_poco():
                return  # los cambios los hizo la propia Rebobina al restaurar
            self.alerta_desde = self._cambios[0][0]
            self.motor.evento("masivo", {"desde": self.alerta_desde, "archivos": distintos}, activo=True)

    def _procesar(self):
        ahora = time.time()
        with self._lock:
            listos = [(r, t) for r, (t, cuando) in self._pend.items() if ahora - cuando >= ESPERA]
            for r, _ in listos:
                del self._pend[r]
            carpetas = list(self._carpetas_pend)
            self._carpetas_pend.clear()
        for ruta, tipo in listos:
            try:
                if os.path.isdir(ruta):
                    continue
                if os.path.exists(ruta):
                    self.motor.capturar(ruta, "modificado")
                else:
                    if not self.motor.marcar_borrado(ruta):
                        self.motor.marcar_borrados_en(ruta)  # era una carpeta
            except Exception as e:  # un archivo problemático no detiene el servicio
                print("Rebobina: no se pudo procesar", ruta, e)
        for c in carpetas:
            try:
                self.motor.escanear(c, "modificado", self._detener)
            except Exception as e:
                print("Rebobina: no se pudo escanear", c, e)
        if self.alerta_desde and ahora - self.alerta_desde > 10 * VENTANA_MASIVO and not self.motor.alerta_activa():
            self.alerta_desde = None  # el usuario ya la descartó

    # -------------------------------------------------------------- ciclo

    def _vigilar(self):
        ajustes = self.motor.ajustes()
        self._umbral = ajustes.get("umbral_masivo", 40)
        carpetas = [c for c in ajustes["carpetas"] if os.path.isdir(c)]
        if carpetas == self._vigiladas and self._observador:
            return
        if self._observador:
            self._observador.stop()
            self._observador.join(5)
        self._observador = Observer()
        for c in carpetas:
            self._observador.schedule(_Manejador(self), c, recursive=True)
        self._observador.start()
        nuevas = [c for c in carpetas if c not in self._vigiladas]
        self._vigiladas = carpetas
        for c in nuevas:  # recupera lo que cambió mientras no se vigilaba
            threading.Thread(target=self.motor.escanear, args=(c, "inicial", self._detener), daemon=True).start()

    def ejecutar(self):
        self._vigilar()
        ultima_limpieza = ultima_revision = 0
        while not self._detener.is_set():
            self._procesar()
            ahora = time.time()
            if ahora - ultima_revision > 30:  # aplica cambios de carpetas hechos desde la ventana
                self._vigilar()
                ultima_revision = ahora
            if ahora - ultima_limpieza > LIMPIEZA_CADA:
                try:
                    self.motor.limpiar()
                except Exception as e:
                    print("Rebobina: limpieza fallida", e)
                ultima_limpieza = ahora
            self._detener.wait(1.0)
        if self._observador:
            self._observador.stop()
            self._observador.join(5)

    def detener(self):
        self._detener.set()


def servicio_activo() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", PUERTO_UNICO)) == 0


def ejecutar_servicio():
    """Arranca el servicio si no hay otro. El puerto abierto sirve de marca de 'ya estoy funcionando'."""
    marca = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        marca.bind(("127.0.0.1", PUERTO_UNICO))
    except OSError:
        print("Rebobina ya está funcionando.")
        return
    marca.listen(1)
    threading.Thread(target=lambda: [c.close() for c in iter(lambda: marca.accept()[0], None)], daemon=True).start()
    Servicio().ejecutar()
