"""Abre Rebobina en modo navegador con datos reales de prueba y recorre todas las pantallas."""
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
from rebobina.motor import Motor  # noqa: E402

SALIDA = sys.argv[1] if len(sys.argv) > 1 else "/tmp/rebobina"
tmp = Path(tempfile.mkdtemp())
datos, docs = tmp / "datos", tmp / "Documentos"
(docs / "Fotos").mkdir(parents=True)
os.environ["REBOBINA_DATOS"] = str(datos)
m = Motor(datos)
m.guardar_ajuste("carpetas", [str(docs)])

# Historia de un archivo de texto a lo largo de varios días
inf = docs / "Informe trimestral.txt"
ahora = time.time()
textos = [
    "Informe del tercer trimestre\n\nVentas: 120 unidades\nGastos: 3.400\n",
    "Informe del tercer trimestre\n\nVentas: 120 unidades\nGastos: 3.400\nConclusión: estable\n",
    "Informe del tercer trimestre\n\nVentas: 135 unidades\nGastos: 3.400\nConclusión: crecimiento leve\nPendiente: revisar con Ana\n",
    "Informe del tercer trimestre\n\nVentas: 135 unidades\nGastos: 3.950\nConclusión: crecimiento leve\n",
]
for i, t in enumerate(textos):
    inf.write_text(t, encoding="utf-8")
    m.capturar(str(inf), fecha=ahora - (len(textos) - i) * 9 * 3600)
# Una imagen y otros archivos
png = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000010000000100802000000909168360000001c4944415478da63fcffff3f03"
                    "3e20c0a68c8a1a3070d4c071c05200006f52f8f06b8f6e0e0000000049454e44ae426082")
(docs / "Fotos" / "logo.png").write_bytes(png)
m.capturar(str(docs / "Fotos" / "logo.png"), fecha=ahora - 30 * 3600)
for n in ("presupuesto.csv", "notas.md", "contrato.pdf"):
    (docs / n).write_text(f"contenido de {n}\n", encoding="utf-8")
    m.capturar(str(docs / n), fecha=ahora - 20 * 3600)
(docs / "notas.md").write_text("contenido de notas.md\n- idea nueva\n", encoding="utf-8")
m.capturar(str(docs / "notas.md"), fecha=ahora - 2 * 3600)
(docs / "contrato.pdf").unlink()
m.marcar_borrado(str(docs / "contrato.pdf"))

proc = subprocess.Popen([sys.executable, "-u", str(RAIZ / "app.py"), "--navegador", "--sin-abrir", "--sin-servicio"],
                        cwd=RAIZ, stdout=subprocess.PIPE, text=True, env=os.environ.copy())
url = None
for _ in range(50):
    linea = proc.stdout.readline()
    g = re.search(r"(http://\S+)", linea)
    if g:
        url = g.group(1)
        break
errores = []
try:
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/usr/bin/google-chrome")
        pg = b.new_page(viewport={"width": 1200, "height": 860})
        pg.on("pageerror", lambda e: errores.append(str(e)))
        pg.on("console", lambda c: errores.append(c.text) if c.type == "error" else None)
        pg.goto(url)
        pg.wait_for_selector("text=Recupera cualquier versión")
        pg.screenshot(path=f"{SALIDA}_1_inicio.png", full_page=True)
        pg.fill("#q", "inform")
        pg.wait_for_timeout(600)
        print("búsqueda:", pg.locator("#res .item").count(), "resultado(s)")
        pg.locator("#res .item").first.click()
        pg.wait_for_selector("text=Vista previa")
        print("versiones del informe:", pg.locator(".lista .item").count())
        pg.locator("[data-paso='-1']").click()
        pg.wait_for_timeout(400)
        pg.screenshot(path=f"{SALIDA}_2_archivo.png", full_page=True)
        pg.click("[data-act=restaurar]")
        pg.click("#msi")
        pg.wait_for_timeout(600)
        print("restaurado:", "Pendiente: revisar con Ana" in inf.read_text(encoding="utf-8"))
        print("toast:", pg.locator(".toast").inner_text())
        pg.goto(url + "#/carpeta?r=" + str(docs).replace("/", "%2F"))
        pg.wait_for_selector("text=Carpeta en el tiempo")
        pg.evaluate("() => { const r = document.getElementById('barra-c'); r.value = 0; r.dispatchEvent(new Event('change', {bubbles:true})); }")
        pg.wait_for_timeout(700)
        pg.screenshot(path=f"{SALIDA}_3_carpeta.png", full_page=True)
        pg.click("text=Borrados")
        pg.wait_for_selector("text=Lo que ya no está")
        pg.screenshot(path=f"{SALIDA}_4_borrados.png", full_page=True)
        pg.click("[data-act=restaurar]")
        pg.click("#msi")
        pg.wait_for_timeout(500)
        print("contrato recuperado:", (docs / "contrato.pdf").exists())
        m.evento("masivo", {"desde": time.time() - 120, "archivos": 57}, activo=True)
        pg.click("[data-sec=inicio]")
        pg.wait_for_selector("text=Cambios masivos")
        pg.screenshot(path=f"{SALIDA}_5_alerta.png", full_page=True)
        pg.click("text=Fui yo, descartar")
        pg.wait_for_timeout(400)
        print("alerta descartada:", m.alerta_activa() is None)
        pg.click("text=Ajustes")
        pg.wait_for_selector("text=Carpetas vigiladas")
        pg.fill("#nueva", str(tmp / "no-existe"))
        pg.click("[data-act=anadir]")
        pg.wait_for_timeout(400)
        print("carpeta inexistente rechazada:", pg.locator(".toast").inner_text())
        pg.screenshot(path=f"{SALIDA}_6_ajustes.png", full_page=True)
        pg.set_viewport_size({"width": 400, "height": 860})
        pg.goto(url + "#/ver?r=" + str(inf).replace("/", "%2F"))
        pg.wait_for_selector("text=Vista previa")
        pg.screenshot(path=f"{SALIDA}_7_movil.png", full_page=True)
        b.close()
finally:
    proc.terminate()
print("ERRORES:", errores)
