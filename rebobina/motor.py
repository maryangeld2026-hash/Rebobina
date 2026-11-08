"""Motor de Rebobina: guarda versiones de archivos y las recupera.

- Cada contenido distinto se guarda una sola vez (por su huella SHA-256), comprimido si conviene.
- El índice vive en SQLite (modo WAL), así la ventana y el servicio pueden usarlo a la vez.
- Nada se borra del disco del usuario: restaurar guarda antes la versión actual.
"""

import hashlib
import json
import os
import sqlite3
import sys
import threading
import time
import zlib
from datetime import datetime
from pathlib import Path

ES_WINDOWS = sys.platform == "win32"

# Extensiones que ya vienen comprimidas: comprimirlas otra vez no ahorra espacio.
YA_COMPRIMIDOS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".mp3", ".mp4", ".mov", ".mkv", ".avi", ".m4a",
    ".zip", ".rar", ".7z", ".gz", ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".pdf", ".epub", ".apk",
}
NOMBRES_IGNORADOS = {"thumbs.db", "desktop.ini", ".ds_store"}
PREFIJOS_IGNORADOS = ("~$", ".~lock", "~wrl")
SUFIJOS_IGNORADOS = (".tmp", ".temp", ".crdownload", ".part", ".partial", ".swp", ".lnk")
CARPETAS_IGNORADAS = {".git", "node_modules", "__pycache__", "$recycle.bin", ".venv", "venv"}

AJUSTES_INICIALES = {
    "carpetas": [],
    "limite_gb": 10,
    "max_mb": 200,
    "umbral_masivo": 40,
    "menu_contextual": True,
    "inicio_automatico": True,
}

HORA, DIA = 3600, 86400


def carpeta_datos() -> Path:
    if os.environ.get("REBOBINA_DATOS"):
        p = Path(os.environ["REBOBINA_DATOS"])
    else:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
        p = Path(base) / "Rebobina"
    p.mkdir(parents=True, exist_ok=True)
    return p


def carpetas_por_defecto():
    casa = Path.home()
    candidatas = [casa / "Documents", casa / "Documentos", casa / "Desktop", casa / "Escritorio"]
    if ES_WINDOWS and os.environ.get("OneDrive"):
        od = Path(os.environ["OneDrive"])
        candidatas += [od / "Documents", od / "Documentos", od / "Desktop", od / "Escritorio"]
    vistas, salida = set(), []
    for c in candidatas:
        if c.is_dir():
            real = os.path.normcase(str(c.resolve()))
            if real not in vistas:
                vistas.add(real)
                salida.append(str(c))
    return salida


def clave_de(ruta: str) -> str:
    """Forma única de una ruta para buscarla (en Windows no distingue mayúsculas)."""
    return os.path.normcase(os.path.abspath(ruta))


def tam_legible(b) -> str:
    b = b or 0
    for unidad, f in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if b >= f:
            return f"{b / f:.1f}".replace(".", ",") + " " + unidad
    return f"{b} bytes"


class Motor:
    def __init__(self, datos: Path = None):
        self.datos = Path(datos) if datos else carpeta_datos()
        self.objetos = self.datos / "objetos"
        self.objetos.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._bloqueo = threading.Lock()
        self._crear_tablas()

    # ------------------------------------------------------------ base de datos

    @property
    def db(self) -> sqlite3.Connection:
        c = getattr(self._local, "db", None)
        if c is None:
            c = sqlite3.connect(str(self.datos / "rebobina.db"), timeout=30)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA busy_timeout=30000")
            self._local.db = c
        return c

    def _crear_tablas(self):
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS ajustes (clave TEXT PRIMARY KEY, valor TEXT);
            CREATE TABLE IF NOT EXISTS versiones (
                id INTEGER PRIMARY KEY, clave TEXT, ruta TEXT, hash TEXT, tamano INTEGER,
                mtime REAL, fecha REAL, evento TEXT);
            CREATE INDEX IF NOT EXISTS v_clave ON versiones(clave, fecha);
            CREATE INDEX IF NOT EXISTS v_fecha ON versiones(fecha);
            CREATE TABLE IF NOT EXISTS objetos (hash TEXT PRIMARY KEY, tamano INTEGER, guardado INTEGER, comprimido INTEGER);
            CREATE TABLE IF NOT EXISTS eventos (id INTEGER PRIMARY KEY, fecha REAL, tipo TEXT, detalle TEXT, activo INTEGER DEFAULT 0);
        """)
        self.db.commit()

    # ------------------------------------------------------------ ajustes

    def ajustes(self) -> dict:
        a = dict(AJUSTES_INICIALES)
        for fila in self.db.execute("SELECT clave, valor FROM ajustes"):
            a[fila["clave"]] = json.loads(fila["valor"])
        return a

    def guardar_ajuste(self, clave, valor):
        self.db.execute("INSERT OR REPLACE INTO ajustes VALUES (?, ?)", (clave, json.dumps(valor)))
        self.db.commit()

    def primera_vez(self) -> bool:
        return self.db.execute("SELECT 1 FROM ajustes WHERE clave='carpetas'").fetchone() is None

    # ------------------------------------------------------------ filtros

    def ignorado(self, ruta: str) -> bool:
        p = Path(ruta)
        nombre = p.name.lower()
        if nombre in NOMBRES_IGNORADOS or nombre.startswith(PREFIJOS_IGNORADOS) or nombre.endswith(SUFIJOS_IGNORADOS):
            return True
        partes = {x.lower() for x in p.parts}
        if partes & CARPETAS_IGNORADAS:
            return True
        try:
            if clave_de(ruta).startswith(clave_de(str(self.datos))):
                return True
        except ValueError:
            pass
        return False

    def carpeta_de(self, ruta: str):
        """Carpeta vigilada que contiene la ruta, o None."""
        k = clave_de(ruta)
        for c in self.ajustes()["carpetas"]:
            kc = clave_de(c)
            if k == kc or k.startswith(kc.rstrip(os.sep) + os.sep):
                return c
        return None

    # ------------------------------------------------------------ objetos

    def _ruta_objeto(self, h: str) -> Path:
        return self.objetos / h[:2] / h

    def _guardar_objeto(self, h: str, datos: bytes, ext: str):
        if self.db.execute("SELECT 1 FROM objetos WHERE hash=?", (h,)).fetchone():
            return
        comprimir = ext.lower() not in YA_COMPRIMIDOS and len(datos) > 256
        cuerpo = zlib.compress(datos, 6) if comprimir else datos
        if comprimir and len(cuerpo) >= len(datos):
            cuerpo, comprimir = datos, False
        destino = self._ruta_objeto(h)
        destino.parent.mkdir(parents=True, exist_ok=True)
        tmp = destino.with_suffix(".tmp")
        tmp.write_bytes(cuerpo)
        os.replace(tmp, destino)
        self.db.execute("INSERT OR IGNORE INTO objetos VALUES (?, ?, ?, ?)", (h, len(datos), len(cuerpo), int(comprimir)))

    def contenido(self, h: str) -> bytes:
        fila = self.db.execute("SELECT comprimido FROM objetos WHERE hash=?", (h,)).fetchone()
        if not fila:
            raise FileNotFoundError("Esta versión ya no está guardada.")
        datos = self._ruta_objeto(h).read_bytes()
        return zlib.decompress(datos) if fila["comprimido"] else datos

    # ------------------------------------------------------------ capturar

    def ultima(self, ruta: str):
        return self.db.execute("SELECT * FROM versiones WHERE clave=? ORDER BY fecha DESC, id DESC LIMIT 1",
                               (clave_de(ruta),)).fetchone()

    def _leer(self, ruta: str):
        for intento in range(4):
            try:
                with open(ruta, "rb") as f:
                    return f.read()
            except PermissionError:
                time.sleep(0.5 * (intento + 1))
            except (FileNotFoundError, IsADirectoryError):
                return None
        return None

    def capturar(self, ruta: str, evento: str = "modificado", fecha: float = None) -> bool:
        """Guarda una versión si el contenido es nuevo. Devuelve True si guardó algo."""
        if self.ignorado(ruta) or not os.path.isfile(ruta):
            return False
        try:
            st = os.stat(ruta)
        except OSError:
            return False
        if st.st_size > self.ajustes()["max_mb"] * 1024 * 1024:
            return False
        datos = self._leer(ruta)
        if datos is None:
            return False
        h = hashlib.sha256(datos).hexdigest()
        with self._bloqueo:
            ult = self.ultima(ruta)
            if ult is not None and ult["hash"] == h and ult["evento"] != "borrado":
                return False
            if ult is None and evento == "modificado":
                evento = "creado"
            if ult is not None and ult["evento"] == "borrado" and evento == "modificado":
                evento = "creado"
            self._guardar_objeto(h, datos, Path(ruta).suffix)
            self.db.execute("INSERT INTO versiones (clave, ruta, hash, tamano, mtime, fecha, evento) VALUES (?,?,?,?,?,?,?)",
                            (clave_de(ruta), os.path.abspath(ruta), h, len(datos), st.st_mtime, fecha or time.time(), evento))
            self.db.commit()
        return True

    def marcar_borrado(self, ruta: str) -> bool:
        if self.ignorado(ruta):
            return False
        with self._bloqueo:
            ult = self.ultima(ruta)
            if ult is None or ult["evento"] == "borrado" or os.path.exists(ruta):
                return False
            self.db.execute("INSERT INTO versiones (clave, ruta, hash, tamano, mtime, fecha, evento) VALUES (?,?,?,?,?,?,?)",
                            (clave_de(ruta), ult["ruta"], None, 0, None, time.time(), "borrado"))
            self.db.commit()
        return True

    def marcar_borrados_en(self, carpeta: str) -> int:
        """Marca como borrados los archivos de una carpeta (o subcarpetas) que ya no existen."""
        prefijo = clave_de(carpeta).rstrip(os.sep) + os.sep
        n = 0
        for fila in self._ultimas_bajo(prefijo):
            if fila["evento"] != "borrado" and not os.path.exists(fila["ruta"]):
                n += self.marcar_borrado(fila["ruta"])
        return n

    def escanear(self, carpeta: str, evento="inicial", detener=None) -> int:
        """Guarda lo que haya cambiado mientras Rebobina no estaba vigilando."""
        n = 0
        for raiz, dirs, archivos in os.walk(carpeta):
            dirs[:] = [d for d in dirs if d.lower() not in CARPETAS_IGNORADAS]
            for a in archivos:
                if detener and detener.is_set():
                    return n
                n += self.capturar(os.path.join(raiz, a), evento)
        self.marcar_borrados_en(carpeta)
        return n

    # ------------------------------------------------------------ consultar

    def _ultimas_bajo(self, prefijo: str, hasta: float = None):
        """Última versión (hasta un momento) de cada archivo cuya clave empieza por prefijo."""
        hasta = hasta if hasta is not None else time.time() + 1
        return self.db.execute("""
            SELECT v.* FROM versiones v JOIN (
                SELECT clave, MAX(id) AS id FROM versiones
                WHERE clave >= ? AND clave < ? AND fecha <= ? GROUP BY clave
            ) u ON v.id = u.id ORDER BY v.ruta
        """, (prefijo, prefijo + "￿", hasta)).fetchall()

    def versiones(self, ruta: str):
        filas = self.db.execute("SELECT * FROM versiones WHERE clave=? ORDER BY fecha DESC, id DESC", (clave_de(ruta),)).fetchall()
        actual_hash = None
        if os.path.isfile(ruta):
            datos = self._leer(ruta)
            actual_hash = hashlib.sha256(datos).hexdigest() if datos is not None else None
        return [dict(f) | {"es_actual": f["hash"] is not None and f["hash"] == actual_hash} for f in filas]

    def recientes(self, n=40):
        return [dict(f) for f in self.db.execute("SELECT * FROM versiones WHERE evento != 'inicial' ORDER BY fecha DESC, id DESC LIMIT ?", (n,))]

    def buscar(self, texto: str, n=60):
        t = f"%{texto.lower()}%"
        return [dict(f) for f in self.db.execute("""
            SELECT v.* FROM versiones v JOIN (SELECT clave, MAX(id) id FROM versiones WHERE lower(ruta) LIKE ? GROUP BY clave) u
            ON v.id = u.id ORDER BY v.fecha DESC LIMIT ?""", (t, n))]

    def borrados(self, n=200):
        filas = self.db.execute("""
            SELECT v.* FROM versiones v JOIN (SELECT clave, MAX(id) id FROM versiones GROUP BY clave) u
            ON v.id = u.id WHERE v.evento = 'borrado' ORDER BY v.fecha DESC LIMIT ?""", (n,)).fetchall()
        salida = []
        for f in filas:
            previa = self.db.execute("SELECT * FROM versiones WHERE clave=? AND hash IS NOT NULL ORDER BY fecha DESC, id DESC LIMIT 1",
                                     (f["clave"],)).fetchone()
            if previa and not os.path.exists(f["ruta"]):
                salida.append(dict(previa) | {"borrado_en": f["fecha"]})
        return salida

    def version(self, vid: int):
        f = self.db.execute("SELECT * FROM versiones WHERE id=?", (vid,)).fetchone()
        if not f:
            raise KeyError("Versión no encontrada.")
        return f

    def momentos_carpeta(self, carpeta: str, n=400):
        prefijo = clave_de(carpeta).rstrip(os.sep) + os.sep
        filas = self.db.execute("SELECT DISTINCT fecha FROM versiones WHERE clave >= ? AND clave < ? ORDER BY fecha DESC LIMIT ?",
                                (prefijo, prefijo + "￿", n)).fetchall()
        return sorted(f["fecha"] for f in filas)

    def carpeta_en(self, carpeta: str, momento: float):
        """Cómo estaba la carpeta en un momento, comparado con ahora."""
        prefijo = clave_de(carpeta).rstrip(os.sep) + os.sep
        salida = []
        for f in self._ultimas_bajo(prefijo, momento):
            if f["evento"] == "borrado":
                continue
            ahora = self.ultima(f["ruta"])
            if not os.path.exists(f["ruta"]):
                estado = "borrado después"
            elif ahora is not None and ahora["hash"] == f["hash"]:
                estado = "igual"
            else:
                estado = "cambió después"
            salida.append(dict(f) | {"estado": estado, "relativa": os.path.relpath(f["ruta"], carpeta)})
        return salida

    # ------------------------------------------------------------ restaurar

    def _escribir(self, ruta: str, datos: bytes):
        Path(ruta).parent.mkdir(parents=True, exist_ok=True)
        tmp = ruta + ".rebobina-tmp"
        with open(tmp, "wb") as f:
            f.write(datos)
        os.replace(tmp, ruta)

    def restaurar(self, vid: int) -> dict:
        v = self.version(vid)
        if v["hash"] is None:
            raise ValueError("Esa entrada solo marca que el archivo se borró.")
        datos = self.contenido(v["hash"])
        ruta = v["ruta"]
        if os.path.isfile(ruta):
            self.capturar(ruta, "antes de restaurar")
        self._escribir(ruta, datos)
        self.capturar(ruta, "restaurado")
        self.evento("restaurado", {"ruta": ruta, "version": vid})
        return {"ruta": ruta}

    def guardar_copia(self, vid: int) -> dict:
        v = self.version(vid)
        if v["hash"] is None:
            raise ValueError("Esa entrada solo marca que el archivo se borró.")
        p = Path(v["ruta"])
        sello = datetime.fromtimestamp(v["fecha"]).strftime("%Y-%m-%d %H.%M")
        destino = p.with_name(f"{p.stem} (versión {sello}){p.suffix}")
        i = 2
        while destino.exists():
            destino = p.with_name(f"{p.stem} (versión {sello}) {i}{p.suffix}")
            i += 1
        self._escribir(str(destino), self.contenido(v["hash"]))
        return {"ruta": str(destino)}

    def restaurando_hace_poco(self, segundos=180) -> bool:
        return self.db.execute("SELECT 1 FROM eventos WHERE tipo='restaurando' AND fecha > ?",
                               (time.time() - segundos,)).fetchone() is not None

    def restaurar_carpeta(self, carpeta: str, momento: float) -> dict:
        """Devuelve cada archivo a como estaba en el momento. No borra los archivos creados después."""
        self.evento("restaurando", {"carpeta": carpeta})
        cambiados = recuperados = 0
        for f in self.carpeta_en(carpeta, momento):
            if f["estado"] == "igual":
                continue
            ruta = f["ruta"]
            if os.path.isfile(ruta):
                self.capturar(ruta, "antes de restaurar")
                cambiados += 1
            else:
                recuperados += 1
            self._escribir(ruta, self.contenido(f["hash"]))
            self.capturar(ruta, "restaurado")
        self.evento("carpeta restaurada", {"carpeta": carpeta, "momento": momento, "cambiados": cambiados, "recuperados": recuperados})
        return {"cambiados": cambiados, "recuperados": recuperados}

    # ------------------------------------------------------------ eventos y alertas

    def evento(self, tipo: str, detalle: dict, activo=False):
        self.db.execute("INSERT INTO eventos (fecha, tipo, detalle, activo) VALUES (?,?,?,?)",
                        (time.time(), tipo, json.dumps(detalle, ensure_ascii=False), int(activo)))
        self.db.commit()

    def alerta_activa(self):
        f = self.db.execute("SELECT * FROM eventos WHERE tipo='masivo' AND activo=1 ORDER BY fecha DESC LIMIT 1").fetchone()
        return dict(f) | {"detalle": json.loads(f["detalle"])} if f else None

    def descartar_alertas(self):
        self.db.execute("UPDATE eventos SET activo=0 WHERE tipo='masivo'")
        self.db.commit()

    # ------------------------------------------------------------ limpieza

    def espacio(self) -> dict:
        f = self.db.execute("SELECT COUNT(*) n, COALESCE(SUM(guardado),0) g, COALESCE(SUM(tamano),0) t FROM objetos").fetchone()
        v = self.db.execute("SELECT COUNT(*) n, COUNT(DISTINCT clave) a FROM versiones WHERE hash IS NOT NULL").fetchone()
        return {"objetos": f["n"], "ocupado": f["g"], "original": f["t"], "versiones": v["n"], "archivos": v["a"]}

    def limpiar(self, ahora: float = None) -> dict:
        """Aplica las reglas de conservación y el límite de espacio. No hace nada con una alerta activa."""
        if self.alerta_activa():
            return {"omitida": True}
        ahora = ahora or time.time()
        borrar = []
        claves = [f["clave"] for f in self.db.execute("SELECT DISTINCT clave FROM versiones")]
        for clave in claves:
            filas = self.db.execute("SELECT id, hash, fecha, evento FROM versiones WHERE clave=? ORDER BY fecha DESC, id DESC", (clave,)).fetchall()
            ultima_con_contenido = next((f["id"] for f in filas if f["hash"]), None)
            vistos = set()
            for i, f in enumerate(filas):
                if i == 0 or f["id"] == ultima_con_contenido:
                    continue  # la última entrada y el último contenido de cada archivo siempre se conservan
                edad = ahora - f["fecha"]
                if edad < DIA:
                    continue
                if f["hash"] is None:
                    borrar.append(f["id"])  # marcas de borrado antiguas que no son la última
                    continue
                if edad < 7 * DIA:
                    cubo = ("h", int(f["fecha"] // HORA))
                elif edad < 30 * DIA:
                    cubo = ("d", int(f["fecha"] // DIA))
                else:
                    borrar.append(f["id"])
                    continue
                if cubo in vistos:
                    borrar.append(f["id"])
                else:
                    vistos.add(cubo)  # se queda la más reciente de cada hora o día
        self._borrar_versiones(borrar)

        # Límite de espacio: se quitan las versiones más antiguas que no sean la última de su archivo.
        limite = float(self.ajustes()["limite_gb"]) * 1024 ** 3
        por_espacio = 0
        while self.espacio()["ocupado"] > limite:
            antes = self.espacio()["ocupado"]
            candidatas = self.db.execute("""
                SELECT id FROM versiones WHERE hash IS NOT NULL AND id NOT IN (
                    SELECT MAX(id) FROM versiones WHERE hash IS NOT NULL GROUP BY clave)
                ORDER BY fecha ASC LIMIT 200""").fetchall()
            if not candidatas:
                break
            self._borrar_versiones([c["id"] for c in candidatas])
            por_espacio += len(candidatas)
            self._borrar_huerfanos()
            if self.espacio()["ocupado"] >= antes:
                break  # lo que queda son las últimas versiones: no se tocan
        return {"versiones_borradas": len(borrar) + por_espacio, "objetos_borrados": self._borrar_huerfanos()}

    def _borrar_versiones(self, ids):
        for i in range(0, len(ids), 500):
            parte = ids[i:i + 500]
            self.db.execute(f"DELETE FROM versiones WHERE id IN ({','.join('?' * len(parte))})", parte)
        self.db.commit()

    def _borrar_huerfanos(self) -> int:
        huerfanos = [f["hash"] for f in self.db.execute(
            "SELECT hash FROM objetos WHERE hash NOT IN (SELECT hash FROM versiones WHERE hash IS NOT NULL)")]
        for h in huerfanos:
            try:
                self._ruta_objeto(h).unlink()
            except FileNotFoundError:
                pass
            self.db.execute("DELETE FROM objetos WHERE hash=?", (h,))
        self.db.commit()
        return len(huerfanos)
