# Despliegue en Render

## Opción rápida para la presentación (gratis)

1. Sube esta carpeta a un repositorio de GitHub.
2. En Render crea un **Web Service** y conecta ese repositorio.
3. Render detectará `render.yaml`. Si configuras el servicio manualmente usa:
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn -w 1 --threads 100 --bind 0.0.0.0:$PORT app:app`
4. Espera a que el deploy termine y abre la URL `https://...onrender.com`.
5. Inicia sesión y comparte esa URL con los demás dispositivos.

## Datos y persistencia

La aplicación sigue usando JSON. Sin `DATA_DIR`, guarda los datos en `./data`, igual que antes.
En un servicio con sistema de archivos efímero, los cambios pueden perderse al reiniciar o redesplegar.
Para conservarlos con un disco persistente, configura la variable:

`DATA_DIR=/opt/render/project/src/data`

y monta el disco en:

`/opt/render/project/src/data`

No es necesario para probar el chat durante una presentación corta, pero sí para usar la aplicación a largo plazo.

## Ejecución local

Instala dependencias:

`pip install -r requirements.txt`

Ejecuta:

`python app.py`

Luego abre `http://localhost:5000`. Para permitir conexiones LAN, la aplicación sigue escuchando en `0.0.0.0`.

## Variables admitidas

- `PORT`: puerto del servidor (Render lo asigna automáticamente).
- `SECRET_KEY`: clave de sesión; en Render se genera automáticamente con `render.yaml`.
- `DATA_DIR`: carpeta donde se guardan los JSON y la clave local.
- `FLASK_DEBUG=1`: activa debug solamente cuando ejecutas `python app.py`; no usar en producción.
