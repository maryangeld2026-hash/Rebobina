"""Comprueba en Windows el menú del clic derecho y el inicio automático (se ejecuta en GitHub Actions)."""
import sys
import winreg
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rebobina import integracion  # noqa: E402


def existe(ruta):
    try:
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CURRENT_USER, ruta))
        return True
    except FileNotFoundError:
        return False


def leer(ruta, nombre=""):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, ruta) as k:
        return winreg.QueryValueEx(k, nombre)[0]


integracion.instalar_menu()
for ruta, marcador in integracion.CLAVES_MENU:
    assert existe(ruta + r"\command"), ruta
    cmd = leer(ruta + r"\command")
    assert "--ver" in cmd and marcador in cmd, cmd
    assert leer(ruta, "MUIVerb") == "Ver en el tiempo"
print("  ✓ menú del clic derecho instalado para archivos, carpetas y fondo de carpeta")
print("    comando:", leer(integracion.CLAVES_MENU[0][0] + r"\command"))

integracion.inicio_automatico(True)
valor = leer(integracion.CLAVE_INICIO, integracion.NOMBRE)
assert "--servicio" in valor, valor
print("  ✓ inicio automático registrado:", valor)

integracion.quitar_menu()
integracion.inicio_automatico(False)
assert not any(existe(r) for r, _ in integracion.CLAVES_MENU)
try:
    leer(integracion.CLAVE_INICIO, integracion.NOMBRE)
    raise AssertionError("el inicio automático sigue registrado")
except FileNotFoundError:
    pass
print("  ✓ desinstalar deja el registro limpio")
