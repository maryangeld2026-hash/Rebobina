# Rebobina

**Versiones de cada archivo para Windows.** Rebobina guarda en segundo plano cada versión de los archivos de tus carpetas. Con clic derecho › **Ver en el tiempo**, mueves una barra y recuperas cómo estaba un archivo o una carpeta hace una hora, ayer o hace un mes.

- Recupera **solo lo que eliges**, sin tocar el resto del PC.
- **Sin disco externo ni internet:** todo se guarda en tu propio disco, sin duplicados y comprimido.
- **Restaurar es reversible:** antes de restaurar, la versión actual se guarda.
- **Archivos borrados:** aunque vacíes la Papelera, sigue la última versión.
- **Alerta de cambios masivos:** si muchos archivos cambian de golpe (como hace un virus que los cifra), avisa y deja de borrar versiones antiguas.

## Instalar

1. Descarga `Rebobina-windows.zip` desde [la última versión](../../releases/latest).
2. Clic derecho › **Extraer todo**, en una carpeta fija (por ejemplo `Documentos\Programas`), y abre `Rebobina\Rebobina.exe`.
3. La primera vez:
   - empieza a vigilar **Documentos** y **Escritorio**;
   - añade **Ver en el tiempo** al clic derecho (en Windows 11, dentro de «Mostrar más opciones»);
   - se configura para vigilar al iniciar Windows.

SmartScreen puede avisar porque el programa no está firmado: «Más información › Ejecutar de todas formas».

Si Microsoft Defender lo borra: **Seguridad de Windows › Protección contra virus y amenazas › Historial de protección**, elige la entrada y **Restaurar**; para que no vuelva a pasar, añade la carpeta de Rebobina en **Exclusiones**. Es un falso positivo habitual en programas sin firma digital. Para reducirlo, Rebobina se distribuye en carpeta (no como un único .exe) y con un cargador de PyInstaller compilado expresamente.

Para quitar el menú y el inicio automático: `Rebobina.exe --desinstalar`. Las versiones guardadas están en `%LOCALAPPDATA%\Rebobina` y se pueden borrar a mano.

## Qué guarda

| Antigüedad | Qué se conserva |
|---|---|
| Últimas 24 horas | Todas las versiones |
| De 1 a 7 días | Una por hora |
| De 7 a 30 días | Una por día |
| Más de 30 días | Solo la última de cada archivo |

- Límite de espacio configurable (10 GB por defecto). Al llegar, se borran primero las versiones más antiguas; nunca la última de un archivo.
- No guarda temporales (`~$`, `.tmp`…), carpetas como `.git` o `node_modules`, ni archivos de más de 200 MB (configurable).
- Espera 3 segundos a que un archivo deje de cambiar antes de guardarlo.

## Límites

- **No es una copia de seguridad:** las versiones están en el mismo disco. Si el disco falla, se pierden. Mantén también una copia externa o en la nube.
- Un archivo bloqueado por otro programa se guarda en su siguiente cambio.
- En archivos grandes, cada cambio guarda el archivo entero (comprimido).

## Para desarrolladores

```
pip install -r requirements.txt
python app.py                 # ventana
python app.py --navegador     # en el navegador
python app.py --servicio      # vigilancia en segundo plano
python pruebas/prueba_motor.py
```

| Archivo | Qué hace |
|---|---|
| `rebobina/motor.py` | Almacén sin duplicados, índice SQLite, restaurar, reglas de conservación |
| `rebobina/vigilante.py` | Vigila carpetas, espera a que terminen los cambios, detecta cambios masivos |
| `rebobina/integracion.py` | Menú del clic derecho e inicio automático (registro del usuario, sin administrador) |
| `app.py` | Ventana (pywebview) y modos de arranque |
| `ui/index.html` | Interfaz |
| `.github/workflows/exe.yml` | Prueba en Windows y crea `Rebobina.exe` |
