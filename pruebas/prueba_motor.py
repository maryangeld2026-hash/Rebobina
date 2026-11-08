"""Pruebas del motor y del servicio con archivos reales en una carpeta temporal."""
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rebobina.motor import Motor, DIA, HORA  # noqa: E402
from rebobina.vigilante import Servicio  # noqa: E402

ok = 0


def comprobar(cond, texto):
    global ok
    if not cond:
        raise AssertionError(texto)
    ok += 1
    print("  ✓", texto)


tmp = Path(tempfile.mkdtemp())
datos, docs = tmp / "datos", tmp / "Documentos"
docs.mkdir()
m = Motor(datos)
m.guardar_ajuste("carpetas", [str(docs)])

print("Versiones de un archivo")
a = docs / "informe.txt"
a.write_text("versión 1\n")
comprobar(m.capturar(str(a)), "guarda la primera versión")
comprobar(not m.capturar(str(a)), "no duplica si el contenido no cambió")
a.write_text("versión 2\nlínea nueva\n")
comprobar(m.capturar(str(a)), "guarda la segunda versión")
vs = m.versiones(str(a))
comprobar(len(vs) == 2 and vs[0]["es_actual"] and vs[0]["evento"] == "modificado" and vs[1]["evento"] == "creado", "lista dos versiones, la reciente es la actual")
comprobar(m.contenido(vs[1]["hash"]) == "versión 1\n".encode(), "recupera el contenido antiguo exacto")

print("Sin duplicados entre archivos")
b = docs / "copia.txt"
b.write_text("versión 1\n")
m.capturar(str(b))
comprobar(m.espacio()["objetos"] == 2, "dos archivos con el mismo contenido comparten un solo objeto")

print("Restaurar")
r = m.restaurar(vs[1]["id"])
comprobar(a.read_text() == "versión 1\n", "el archivo vuelve a la versión 1")
eventos = [v["evento"] for v in m.versiones(str(a))]
comprobar(eventos[0] == "restaurado" or "antes de restaurar" in eventos, "la versión que había antes de restaurar quedó guardada")
comprobar(any(m.contenido(v["hash"]) == "versión 2\nlínea nueva\n".encode() for v in m.versiones(str(a)) if v["hash"]), "la versión 2 sigue disponible tras restaurar")
cp = m.guardar_copia(vs[0]["id"])
comprobar(Path(cp["ruta"]).exists() and "(versión " in cp["ruta"], "guarda una copia con la fecha en el nombre")
os.remove(cp["ruta"])

print("Borrados y carpetas")
sub = docs / "fotos"
sub.mkdir()
(sub / "a.jpg").write_bytes(os.urandom(5000))
(sub / "b.jpg").write_bytes(os.urandom(5000))
m.escanear(str(docs), "inicial")
t_antes = time.time()
time.sleep(0.05)
(sub / "a.jpg").write_bytes(os.urandom(5000))
m.capturar(str(sub / "a.jpg"))
(sub / "b.jpg").unlink()
m.marcar_borrado(str(sub / "b.jpg"))
comprobar(any(Path(x["ruta"]).name == "b.jpg" for x in m.borrados()), "b.jpg aparece en archivos borrados")
estado = {Path(x["ruta"]).name: x["estado"] for x in m.carpeta_en(str(docs), t_antes)}
comprobar(estado.get("a.jpg") == "cambió después" and estado.get("b.jpg") == "borrado después" and estado.get("informe.txt") == "igual",
          "la vista de la carpeta en el pasado compara bien con ahora")
res = m.restaurar_carpeta(str(docs), t_antes)
comprobar((sub / "b.jpg").exists() and res["recuperados"] == 1 and res["cambiados"] == 1, "restaurar la carpeta recupera el borrado y revierte el cambiado")
comprobar(m.restaurando_hace_poco(), "deja constancia de que Rebobina está restaurando")

print("Ignorados y límites")
(docs / "~$borrador.docx").write_text("x")
comprobar(not m.capturar(str(docs / "~$borrador.docx")), "ignora archivos temporales de Office")
m.guardar_ajuste("max_mb", 0.001)
(docs / "grande.bin").write_bytes(os.urandom(5000))
comprobar(not m.capturar(str(docs / "grande.bin")), "respeta el tamaño máximo")
m.guardar_ajuste("max_mb", 200)

print("Limpieza por antigüedad")
c = docs / "diario.txt"
ahora = time.time()
for i in range(48):  # una versión cada 30 minutos durante 24 horas, hace 3 días
    c.write_text(f"día 3, versión {i}")
    m.capturar(str(c), fecha=ahora - 3 * DIA - i * 1800)
for i in range(5):  # cinco versiones hace 10 días el mismo día
    c.write_text(f"día 10, versión {i}")
    m.capturar(str(c), fecha=ahora - 10 * DIA - i * 600)
c.write_text("hoy")
m.capturar(str(c), fecha=ahora)
antes = len(m.versiones(str(c)))
m.limpiar(ahora)
despues = m.versiones(str(c))
viejas3 = [v for v in despues if 2 * DIA < ahora - v["fecha"] < 5 * DIA]
viejas10 = [v for v in despues if ahora - v["fecha"] > 9 * DIA]
horas_distintas = len({int((ahora - 3 * DIA - i * 1800) // HORA) for i in range(48)})
comprobar(antes == 54 and len(viejas3) == horas_distintas, f"de 1 a 7 días deja una por hora ({len(viejas3)} de 48, una por cada hora distinta)")
comprobar(len(viejas10) == 1, "de 7 a 30 días deja una por día")
comprobar(despues[0]["evento"] != "borrado" and c.read_text() == m.contenido(despues[0]["hash"]).decode(), "la última versión se conserva")

print("Límite de espacio")
m.guardar_ajuste("limite_gb", 0.00001)  # unos 10 KB
r = m.limpiar(ahora)
comprobar(m.espacio()["ocupado"] <= 0.00001 * 1024 ** 3 or r["versiones_borradas"] > 0, "al pasar el límite borra versiones antiguas")
comprobar(all(m.ultima(str(p)) is not None for p in [a, c]), "nunca borra la última versión de un archivo")
m.guardar_ajuste("limite_gb", 10)

print("Servicio con vigilancia real")
m.guardar_ajuste("umbral_masivo", 15)
s = Servicio(m)
hilo = threading.Thread(target=s.ejecutar, daemon=True)
hilo.start()
time.sleep(1.5)
n0 = len(m.versiones(str(a)))
a.write_text("cambio vigilado")
time.sleep(5)
comprobar(len(m.versiones(str(a))) == n0 + 1, "detecta y guarda un cambio sin intervención")
(docs / "nuevo.txt").write_text("hola")
os.rename(docs / "nuevo.txt", docs / "renombrado.txt")
time.sleep(5)
comprobar(m.ultima(str(docs / "renombrado.txt")) is not None, "un archivo renombrado se guarda con su nuevo nombre")
m.db.execute("DELETE FROM eventos WHERE tipo='restaurando'")
m.db.commit()
masa = docs / "masa"
masa.mkdir()
for i in range(20):
    (masa / f"f{i}.txt").write_text("cifrado" * i)
time.sleep(5)
alerta = m.alerta_activa()
comprobar(alerta is not None and alerta["detalle"]["archivos"] >= 15, "20 archivos cambiados en segundos activan la alerta de cambios masivos")
comprobar(m.limpiar().get("omitida"), "con la alerta activa no se borra ninguna versión")
m.descartar_alertas()
comprobar(m.alerta_activa() is None, "la alerta se puede descartar")
s.detener()
hilo.join(5)
print(f"\nTodo bien: {ok} comprobaciones superadas.")
