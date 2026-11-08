"""Integración con Windows: menú del clic derecho e inicio automático.

Todo se escribe en HKEY_CURRENT_USER, así que no hace falta ser administrador.
En Windows 11 el menú aparece en «Mostrar más opciones» (el menú clásico).
"""

import os
import sys

ES_WINDOWS = sys.platform == "win32"
CLAVES_MENU = [
    (r"Software\Classes\*\shell\Rebobina", "%1"),
    (r"Software\Classes\Directory\shell\Rebobina", "%1"),
    (r"Software\Classes\Directory\Background\shell\Rebobina", "%V"),
]
CLAVE_INICIO = r"Software\Microsoft\Windows\CurrentVersion\Run"
NOMBRE = "Rebobina"


def comando_base() -> str:
    """Cómo lanzar Rebobina: el .exe empaquetado, o pythonw + app.py durante el desarrollo."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    py = sys.executable
    pyw = os.path.join(os.path.dirname(py), "pythonw.exe")
    app = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")
    return f'"{pyw if os.path.exists(pyw) else py}" "{app}"'


def icono() -> str:
    return sys.executable if getattr(sys, "frozen", False) else ""


def instalar_menu():
    if not ES_WINDOWS:
        return False
    import winreg
    for ruta, marcador in CLAVES_MENU:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, ruta) as k:
            winreg.SetValueEx(k, "MUIVerb", 0, winreg.REG_SZ, "Ver en el tiempo")
            if icono():
                winreg.SetValueEx(k, "Icon", 0, winreg.REG_SZ, icono())
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, ruta + r"\command") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, f'{comando_base()} --ver "{marcador}"')
    return True


def quitar_menu():
    if not ES_WINDOWS:
        return False
    import winreg
    for ruta, _ in CLAVES_MENU:
        for sub in (ruta + r"\command", ruta):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
            except FileNotFoundError:
                pass
    return True


def inicio_automatico(activar: bool):
    if not ES_WINDOWS:
        return False
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CLAVE_INICIO, 0, winreg.KEY_SET_VALUE) as k:
        if activar:
            winreg.SetValueEx(k, NOMBRE, 0, winreg.REG_SZ, f"{comando_base()} --servicio")
        else:
            try:
                winreg.DeleteValue(k, NOMBRE)
            except FileNotFoundError:
                pass
    return True


def aplicar(ajustes: dict):
    """Deja el registro como dicen los ajustes."""
    if not ES_WINDOWS:
        return
    (instalar_menu if ajustes.get("menu_contextual", True) else quitar_menu)()
    inicio_automatico(bool(ajustes.get("inicio_automatico", True)))


def lanzar_servicio():
    """Arranca el servicio en segundo plano, sin ventana."""
    import subprocess
    import shlex
    if getattr(sys, "frozen", False):
        args = [sys.executable, "--servicio"]
    else:
        args = shlex.split(comando_base(), posix=not ES_WINDOWS) + ["--servicio"]
        args = [a.strip('"') for a in args]
    flags = 0
    if ES_WINDOWS:
        flags = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED_PROCESS | NEW_PROCESS_GROUP | NO_WINDOW
    subprocess.Popen(args, creationflags=flags, close_fds=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
