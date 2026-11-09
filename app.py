"""Rebobina — versiones de cada archivo para Windows.

Uso:
    Rebobina.exe                 abre la ventana
    Rebobina.exe --ver RUTA      abre «Ver en el tiempo» de un archivo o carpeta (lo usa el clic derecho)
    Rebobina.exe --servicio      vigila las carpetas en segundo plano (arranca solo con Windows)
    Rebobina.exe --desinstalar   quita el menú del clic derecho y el inicio automático
    Rebobina.exe --navegador     abre la ventana en el navegador (si la ventana normal falla)
"""

import base64
import difflib
import json
import os
import secrets
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from rebobina import integracion
from rebobina.motor import Motor, carpeta_datos, carpetas_por_defecto, tam_legible
from rebobina.vigilante import ejecutar_servicio, servicio_activo

# El .exe no tiene consola: lo que se imprime va a un registro en la carpeta de datos.
if sys.stdout is None or sys.stderr is None:
    _registro = open(carpeta_datos() / "rebobina.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or _registro
    sys.stderr = sys.stderr or _registro

IMAGENES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
            ".webp": "image/webp", ".bmp": "image/bmp"}
TEXTO = {".txt", ".md", ".csv", ".json", ".xml", ".html", ".htm", ".css", ".js", ".ts", ".py", ".java", ".c", ".cpp",
         ".h", ".cs", ".ini", ".cfg", ".log", ".yml", ".yaml", ".sql", ".bat", ".ps1", ".sh", ".tex", ".srt", ".rtf"}


def recurso(rel: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / rel


def fecha_txt(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else ""


class Api:
    """Funciones que la ventana puede llamar."""

    def __init__(self, ruta_inicial=None):
        self._m = Motor()
        self._ruta = ruta_inicial
        self._ventana = None

    # ---------------------------------------------------------------- general

    def inicio(self):
        return {"ruta": self._ruta, "es_windows": integracion.ES_WINDOWS}

    def estado(self):
        a = self._m.ajustes()
        e = self._m.espacio()
        return {
            "carpetas": [{"ruta": c, "existe": os.path.isdir(c), "nombre": Path(c).name or c} for c in a["carpetas"]],
            "espacio": e | {"ocupado_txt": tam_legible(e["ocupado"]), "original_txt": tam_legible(e["original"]),
                            "limite_txt": f"{a['limite_gb']} GB", "porcentaje": round(e["ocupado"] / (a["limite_gb"] * 1024 ** 3) * 100, 1)},
            "alerta": self._m.alerta_activa(),
            "servicio": servicio_activo(),
            "recientes": [self._fila(v) for v in self._m.recientes(30)],
            "borrados": len(self._m.borrados()),
        }

    def _fila(self, v):
        v = dict(v)
        p = Path(v["ruta"])
        return {"id": v["id"], "ruta": v["ruta"], "nombre": p.name, "carpeta": str(p.parent), "fecha": v["fecha"],
                "fecha_txt": fecha_txt(v["fecha"]), "evento": v["evento"], "tamano": v["tamano"],
                "tamano_txt": tam_legible(v["tamano"]) if v["hash"] else "", "hash": bool(v["hash"]),
                "es_actual": v.get("es_actual", False), "borrado_en": v.get("borrado_en")}

    def iniciar_servicio(self):
        if not servicio_activo():
            integracion.lanzar_servicio()
            for _ in range(20):
                time.sleep(0.25)
                if servicio_activo():
                    break
        return servicio_activo()

    # ---------------------------------------------------------------- archivos

    def buscar(self, texto):
        return [self._fila(v) for v in self._m.buscar(texto)] if texto and len(texto) >= 2 else []

    def versiones(self, ruta):
        es_carpeta = os.path.isdir(ruta) or (not os.path.exists(ruta) and bool(self._m.momentos_carpeta(ruta)) and not self._m.ultima(ruta))
        return {"ruta": ruta, "nombre": Path(ruta).name or ruta, "existe": os.path.exists(ruta), "es_carpeta": es_carpeta,
                "vigilada": self._m.carpeta_de(ruta) is not None,
                "versiones": [] if es_carpeta else [self._fila(v) for v in self._m.versiones(ruta)]}

    def vista(self, vid):
        v = self._m.version(vid)
        if not v["hash"]:
            return {"tipo": "borrado"}
        ext = Path(v["ruta"]).suffix.lower()
        datos = self._m.contenido(v["hash"])
        if ext in IMAGENES and len(datos) < 8 * 1024 * 1024:
            return {"tipo": "imagen", "data": f"data:{IMAGENES[ext]};base64," + base64.b64encode(datos).decode()}
        texto = None
        if (ext in TEXTO or not ext) and len(datos) < 400 * 1024:
            for cod in ("utf-8", "utf-16", "latin-1"):
                try:
                    texto = datos.decode(cod)
                    break
                except UnicodeDecodeError:
                    continue
        if texto is None:
            return {"tipo": "otro", "tamano": tam_legible(len(datos))}
        actual = None
        if os.path.isfile(v["ruta"]):
            try:
                actual = Path(v["ruta"]).read_bytes().decode("utf-8", "replace")
            except OSError:
                actual = None
        cambios = None
        if actual is not None and actual != texto:
            cambios = []
            for linea in difflib.ndiff(texto.splitlines(), actual.splitlines()):
                if linea.startswith("? "):
                    continue
                cambios.append({"t": linea[0], "l": linea[2:]})
                if len(cambios) > 1500:
                    break
        return {"tipo": "texto", "texto": texto[:200000], "cambios": cambios, "igual": actual == texto}

    def restaurar(self, vid):
        try:
            r = self._m.restaurar(vid)
            return {"ok": True, "mensaje": f"Restaurado: {Path(r['ruta']).name}. La versión que había antes quedó guardada."}
        except Exception as e:
            return {"ok": False, "mensaje": f"No se pudo restaurar: {e}"}

    def copia(self, vid):
        try:
            r = self._m.guardar_copia(vid)
            return {"ok": True, "mensaje": f"Copia guardada como «{Path(r['ruta']).name}» junto al original.", "ruta": r["ruta"]}
        except Exception as e:
            return {"ok": False, "mensaje": f"No se pudo guardar la copia: {e}"}

    def borrados(self):
        return [self._fila(v) for v in self._m.borrados()]

    # ---------------------------------------------------------------- carpetas

    def momentos(self, carpeta):
        return self._m.momentos_carpeta(carpeta)

    def carpeta_en(self, carpeta, momento):
        filas = self._m.carpeta_en(carpeta, float(momento))
        return [self._fila(f) | {"estado": f["estado"], "relativa": f["relativa"]} for f in filas]

    def restaurar_carpeta(self, carpeta, momento):
        try:
            r = self._m.restaurar_carpeta(carpeta, float(momento))
            return {"ok": True, "mensaje": f"Carpeta restaurada: {r['cambiados']} archivos revertidos y {r['recuperados']} recuperados. "
                                           "Las versiones que había antes quedaron guardadas."}
        except Exception as e:
            return {"ok": False, "mensaje": f"No se pudo restaurar la carpeta: {e}"}

    def descartar_alerta(self):
        self._m.descartar_alertas()
        return True

    # ---------------------------------------------------------------- ajustes

    def ajustes(self):
        return self._m.ajustes()

    def guardar_ajustes(self, nuevos):
        permitidos = {"carpetas", "limite_gb", "max_mb", "menu_contextual", "inicio_automatico", "umbral_masivo"}
        for k, v in (nuevos or {}).items():
            if k in permitidos:
                if k == "carpetas":
                    v = [os.path.abspath(c) for c in v if c and os.path.isdir(c)]
                self._m.guardar_ajuste(k, v)
        try:
            integracion.aplicar(self._m.ajustes())
        except Exception as e:
            return {"ok": False, "mensaje": f"Ajustes guardados, pero no se pudo cambiar el registro de Windows: {e}"}
        return {"ok": True, "mensaje": "Ajustes guardados."}

    def elegir_carpeta(self):
        if self._ventana is None:
            return None
        import webview
        r = self._ventana.create_file_dialog(webview.FOLDER_DIALOG)
        return r[0] if r else None

    def abrir_ubicacion(self, ruta):
        try:
            if integracion.ES_WINDOWS:
                if os.path.exists(ruta) and not os.path.isdir(ruta):
                    subprocess.Popen(["explorer", "/select,", os.path.normpath(ruta)])
                else:
                    os.startfile(ruta if os.path.isdir(ruta) else os.path.dirname(ruta))
            return True
        except Exception:
            return False


METODOS = {n for n in dir(Api) if not n.startswith("_")}


def preparar():
    """La primera vez elige las carpetas. Siempre vuelve a registrar el clic derecho y el inicio
    automático con la ruta de ESTE Rebobina.exe, por si se movió, se reinstaló o el anterior se borró."""
    m = Motor()
    if m.primera_vez():
        m.guardar_ajuste("carpetas", carpetas_por_defecto())
    try:
        integracion.aplicar(m.ajustes())
    except Exception as e:
        print("No se pudo configurar el registro:", e)


def modo_navegador(api: Api, abrir=True, puerto=0):
    token = secrets.token_urlsafe(16)
    html = recurso("ui/index.html").read_bytes()

    class Manejador(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _enviar(self, codigo, cuerpo, tipo="application/json; charset=utf-8"):
            self.send_response(codigo)
            self.send_header("Content-Type", tipo)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(cuerpo)

        def do_GET(self):
            if self.path.split("?")[0] in ("/", "/index.html"):
                self._enviar(200, html, "text/html; charset=utf-8")
            else:
                self._enviar(404, b"{}")

        def do_POST(self):
            metodo = self.path.removeprefix("/api/")
            if self.headers.get("X-Token") != token or metodo not in METODOS:
                return self._enviar(403, b'{"ok": false}')
            largo = int(self.headers.get("Content-Length") or 0)
            args = json.loads(self.rfile.read(largo) or b"[]")
            try:
                res = getattr(api, metodo)(*args)
            except Exception as e:
                res = {"error": str(e)}
            self._enviar(200, json.dumps(res, ensure_ascii=False, default=str).encode("utf-8"))

    srv = ThreadingHTTPServer(("127.0.0.1", puerto), Manejador)
    url = f"http://127.0.0.1:{srv.server_address[1]}/?t={token}"
    print(f"Rebobina abierta en {url}  (Ctrl+C para cerrar)", flush=True)
    if abrir:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


def main():
    args = sys.argv[1:]
    if "--servicio" in args:
        return ejecutar_servicio()
    if "--desinstalar" in args:
        integracion.quitar_menu()
        integracion.inicio_automatico(False)
        print("Se quitaron el menú del clic derecho y el inicio automático. Tus versiones siguen en", Motor().datos)
        return
    ruta = args[args.index("--ver") + 1] if "--ver" in args and args.index("--ver") + 1 < len(args) else None
    preparar()
    api = Api(os.path.abspath(ruta) if ruta else None)
    if "--sin-servicio" not in args:
        threading.Thread(target=api.iniciar_servicio, daemon=True).start()
    if "--navegador" in args:
        puerto = next((int(a.split("=")[1]) for a in args if a.startswith("--puerto=")), 0)
        return modo_navegador(api, abrir="--sin-abrir" not in args, puerto=puerto)
    try:
        import webview
    except ImportError:
        return modo_navegador(api)
    titulo = f"Rebobina · {Path(ruta).name}" if ruta else "Rebobina"
    api._ventana = webview.create_window(titulo, url=str(recurso("ui/index.html")), js_api=api,
                                        width=1180, height=820, min_size=(420, 560), background_color="#0A0D14")
    try:
        webview.start()
    except Exception as e:
        print("No se pudo abrir la ventana, se usará el navegador:", e)
        modo_navegador(api)


if __name__ == "__main__":
    main()
