"""Panel de eventos: servidor Flask con login y roles.

Todo se guarda en archivos JSON dentro de data/:
    events.json   eventos del calendario
    users.json    cuentas de usuario (contraseñas cifradas, nunca en texto plano)
    secret.key    clave para firmar las sesiones (se crea sola la primera vez)

Regla principal: los permisos los decide SIEMPRE el servidor. Lo que el
navegador muestre u oculte es solo comodidad para el usuario.
"""

import json
import os
import re
import secrets
import tempfile
import threading
import uuid
from datetime import datetime
from functools import wraps

from flask import (
    Flask, g, jsonify, redirect, render_template, request, session, url_for,
)
from flask_socketio import SocketIO, emit, join_room, leave_room
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
EVENTS_FILE = os.path.join(DATA_DIR, "events.json")
USERS_FILE = os.path.join(DATA_DIR, "users.json")
MESSAGES_FILE = os.path.join(DATA_DIR, "messages.json")
KEY_FILE = os.path.join(DATA_DIR, "secret.key")

VALID_TYPES = {"evento", "reunion", "proyecto"}
ROLES = {"jefe": "Jefe", "supervisor": "Supervisor", "colaborador": "Colaborador"}

# Qué tipos de evento puede crear (y, por tanto, editar) cada rol.
CREATE_PERMISSIONS = {
    "jefe": ["evento", "reunion", "proyecto"],
    "supervisor": ["evento", "reunion"],
    "colaborador": [],
}

# Cuentas de ejemplo que se crean la primera vez que arranca la app.
# (id fijo, usuario, nombre, rol, contraseña)
DEMO_USERS = [
    ("u-jefe", "jefe", "Carlos Jiménez", "jefe", "jefe123"),
    ("u-supervisor", "supervisor", "María Rojas", "supervisor", "super123"),
    ("u-colaborador", "colaborador", "Luis Vargas", "colaborador", "colab123"),
]

# Un candado por archivo: evita que dos peticiones lo escriban a la vez.
events_lock = threading.Lock()
users_lock = threading.Lock()
messages_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Lectura y escritura de archivos JSON
# ---------------------------------------------------------------------------

def read_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    """Escribe en un archivo temporal y lo reemplaza al final.

    Así, si algo falla a mitad de la escritura, el archivo original no
    queda corrupto.
    """
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=folder, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def load_events():
    return read_json(EVENTS_FILE, [])


def load_users():
    with users_lock:
        return read_json(USERS_FILE, [])


def load_messages():
    with messages_lock:
        return read_json(MESSAGES_FILE, [])


# ---------------------------------------------------------------------------
# Arranque: clave secreta y cuentas de ejemplo
# ---------------------------------------------------------------------------

def load_secret_key():
    """Clave con la que Flask firma la cookie de sesión.

    Se toma de la variable de entorno SECRET_KEY o, si no existe, se genera
    una al azar y se guarda en data/secret.key.
    """
    key = os.environ.get("SECRET_KEY")
    if key:
        return key
    if not os.path.exists(KEY_FILE):
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(KEY_FILE, "w", encoding="utf-8") as f:
            f.write(secrets.token_hex(32))
    with open(KEY_FILE, "r", encoding="utf-8") as f:
        return f.read().strip()


def create_demo_users_if_needed():
    with users_lock:
        if os.path.exists(USERS_FILE):
            return
        users = [
            {
                "id": user_id,
                "usuario": username,
                "nombre": name,
                "rol": role,
                "password_hash": generate_password_hash(password),
            }
            for user_id, username, name, role, password in DEMO_USERS
        ]
        write_json(USERS_FILE, users)

    print("\nSe crearon las cuentas de ejemplo (usuario / contraseña):")
    for _, username, _, role, password in DEMO_USERS:
        print(f"  {role:<12} {username} / {password}")
    print()


app = Flask(__name__)
app.secret_key = load_secret_key()
socketio = SocketIO(app, async_mode="threading")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,   # JavaScript no puede leer la cookie
    SESSION_COOKIE_SAMESITE="Lax",  # no se envía desde páginas de otros sitios
)
create_demo_users_if_needed()
if not os.path.exists(MESSAGES_FILE):
    write_json(MESSAGES_FILE, [])


# ---------------------------------------------------------------------------
# Sesión y permisos
# ---------------------------------------------------------------------------

def current_user():
    """Devuelve el usuario de la sesión, leído del archivo en cada petición.

    Como el rol se lee del archivo (no de la cookie), si el jefe cambia el
    rol de alguien o elimina su cuenta, el cambio se aplica al instante.
    """
    user_id = session.get("uid")
    if not user_id:
        return None
    for user in load_users():
        if user["id"] == user_id:
            return user
    return None


def wants_json():
    return request.path.startswith("/api/")


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        user = current_user()
        if user is None:
            session.clear()
            if wants_json():
                return jsonify(error="Tu sesión terminó. Inicia sesión de nuevo."), 401
            return redirect(url_for("login"))
        g.user = user
        return view(*args, **kwargs)
    return wrapper


def boss_required(view):
    @wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        if g.user["rol"] != "jefe":
            if wants_json():
                return jsonify(error="Solo el jefe puede hacer esto."), 403
            return redirect(url_for("index"))
        return view(*args, **kwargs)
    return wrapper


def can_modify(user, event):
    """¿Puede este usuario editar o eliminar este evento?"""
    if user["rol"] == "jefe":
        return True
    if user["rol"] == "supervisor":
        return (
            event.get("creado_por") == user["id"]
            and event["tipo"] in CREATE_PERMISSIONS["supervisor"]
        )
    return False


def user_names():
    return {u["id"]: u["nombre"] for u in load_users()}


def present_event(event, user, names):
    """Evento tal como se envía al navegador, con datos extra para la vista."""
    data = dict(event)
    creator_id = event.get("creado_por")
    if creator_id:
        data["creador"] = names.get(creator_id, "un usuario eliminado")
    else:
        data["creador"] = ""
    data["puedeEditar"] = can_modify(user, event)
    return data


# ---------------------------------------------------------------------------
# Validación de eventos
# ---------------------------------------------------------------------------

def parse_iso(value):
    """Convierte '2026-09-29' o '2026-09-29T09:00' en datetime (o None)."""
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def clean_event(data):
    """Valida los datos recibidos del navegador.

    Devuelve (evento, None) si todo está bien, o (None, mensaje_de_error).
    """
    if not isinstance(data, dict):
        return None, "Los datos enviados no son válidos."

    title = str(data.get("title") or "").strip()
    if not title:
        return None, "El título es obligatorio."
    if len(title) > 120:
        return None, "El título no puede pasar de 120 caracteres."

    tipo = data.get("tipo")
    if tipo not in VALID_TYPES:
        return None, "El tipo de evento no es válido."

    all_day = bool(data.get("allDay", False))
    start_raw = str(data.get("start") or "")
    end_raw = data.get("end") or None

    if all_day:
        start_raw = start_raw[:10]
        end_raw = None
    elif "T" not in start_raw:
        return None, "Falta la hora de inicio."

    start = parse_iso(start_raw)
    if start is None:
        return None, "La fecha de inicio no es válida."

    if end_raw:
        end = parse_iso(end_raw)
        if end is None:
            return None, "La hora de fin no es válida."
        if end < start:
            return None, "La hora de fin no puede ser anterior a la de inicio."

    event = {
        "title": title,
        "start": start_raw,
        "end": end_raw,
        "allDay": all_day,
        "tipo": tipo,
        "descripcion": str(data.get("descripcion") or "").strip()[:1000],
    }
    return event, None


# ---------------------------------------------------------------------------
# Chat por evento
# ---------------------------------------------------------------------------

def public_message(message, users=None):
    """Datos de un mensaje que se pueden enviar al navegador."""
    if users is None:
        users = load_users()
    names = {u["id"]: u["nombre"] for u in users}
    return {
        "id": message["id"],
        "evento_id": message["evento_id"],
        "usuario_id": message["usuario_id"],
        "usuario": names.get(message["usuario_id"], "Usuario eliminado"),
        "mensaje": message["mensaje"],
        "fecha": message["fecha"],
    }


def event_exists(event_id):
    with events_lock:
        return any(e.get("id") == event_id for e in load_events())


def clean_message(data):
    if not isinstance(data, dict):
        return None, "Los datos enviados no son válidos."

    message = str(data.get("mensaje") or "").strip()
    if not message:
        return None, "El mensaje no puede estar vacío."
    if len(message) > 1000:
        return None, "El mensaje no puede pasar de 1000 caracteres."
    return message, None


@app.get("/api/eventos/<event_id>/mensajes")
@login_required
def list_messages(event_id):
    if not event_exists(event_id):
        return jsonify(error="No se encontró el evento."), 404
    with messages_lock:
        messages = [m for m in read_json(MESSAGES_FILE, []) if m.get("evento_id") == event_id]
    return jsonify([public_message(m) for m in messages])


def save_message(event_id, user_id, message_text):
    message = {
        "id": uuid.uuid4().hex[:12],
        "evento_id": event_id,
        "usuario_id": user_id,
        "mensaje": message_text,
        "fecha": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    with messages_lock:
        messages = read_json(MESSAGES_FILE, [])
        messages.append(message)
        write_json(MESSAGES_FILE, messages)
    return message


@socketio.on("connect")
def socket_connect(auth=None):
    user = current_user()
    if user is None:
        return False
    return True


@socketio.on("join_event")
def socket_join_event(data):
    user = current_user()
    if user is None:
        emit("chat_error", {"error": "Tu sesión terminó. Inicia sesión de nuevo."})
        return

    event_id = str((data or {}).get("evento_id") or "").strip()
    if not event_id or not event_exists(event_id):
        emit("chat_error", {"error": "No se encontró el evento."})
        return

    room = "evento:" + event_id
    join_room(room)

    with messages_lock:
        messages = [m for m in read_json(MESSAGES_FILE, []) if m.get("evento_id") == event_id]
    emit("chat_history", {
        "evento_id": event_id,
        "mensajes": [public_message(m) for m in messages],
    })


@socketio.on("leave_event")
def socket_leave_event(data):
    event_id = str((data or {}).get("evento_id") or "").strip()
    if event_id:
        leave_room("evento:" + event_id)


@socketio.on("send_message")
def socket_send_message(data):
    user = current_user()
    if user is None:
        emit("chat_error", {"error": "Tu sesión terminó. Inicia sesión de nuevo."})
        return

    data = data or {}
    event_id = str(data.get("evento_id") or "").strip()
    message_text, error = clean_message(data)
    if error:
        emit("chat_error", {"error": error})
        return
    if not event_id or not event_exists(event_id):
        emit("chat_error", {"error": "No se encontró el evento."})
        return

    message = save_message(event_id, user["id"], message_text)
    emit("new_message", public_message(message, [user] + [u for u in load_users() if u["id"] != user["id"]]), room="evento:" + event_id)


# ---------------------------------------------------------------------------
# Páginas: login, salida y calendario
# ---------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user() is not None:
        return redirect(url_for("index"))

    error = None
    if request.method == "POST":
        username = request.form.get("usuario", "").strip().lower()
        password = request.form.get("password", "")
        user = next((u for u in load_users() if u["usuario"] == username), None)
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["uid"] = user["id"]
            return redirect(url_for("index"))
        error = "Usuario o contraseña incorrectos."

    return render_template("login.html", error=error), (401 if error else 200)


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    yo = {
        "id": g.user["id"],
        "nombre": g.user["nombre"],
        "rol": g.user["rol"],
        "puedeCrear": CREATE_PERMISSIONS[g.user["rol"]],
    }
    return render_template("index.html", yo=yo, rol_etiqueta=ROLES[g.user["rol"]])


# ---------------------------------------------------------------------------
# API de eventos
# ---------------------------------------------------------------------------

@app.get("/api/eventos")
@login_required
def list_events():
    names = user_names()
    with events_lock:
        events = load_events()
    return jsonify([present_event(e, g.user, names) for e in events])


@app.post("/api/eventos")
@login_required
def create_event():
    event, error = clean_event(request.get_json(silent=True))
    if error:
        return jsonify(error=error), 400
    if event["tipo"] not in CREATE_PERMISSIONS[g.user["rol"]]:
        return jsonify(error="Tu rol no puede crear este tipo de evento."), 403

    event["id"] = uuid.uuid4().hex[:12]
    event["creado_por"] = g.user["id"]
    with events_lock:
        events = load_events()
        events.append(event)
        write_json(EVENTS_FILE, events)
    return jsonify(present_event(event, g.user, user_names())), 201


@app.put("/api/eventos/<event_id>")
@login_required
def update_event(event_id):
    changes, error = clean_event(request.get_json(silent=True))
    if error:
        return jsonify(error=error), 400

    with events_lock:
        events = load_events()
        for i, current in enumerate(events):
            if current["id"] != event_id:
                continue
            if not can_modify(g.user, current):
                return jsonify(error="No tienes permiso para editar este evento."), 403
            if changes["tipo"] not in CREATE_PERMISSIONS[g.user["rol"]]:
                return jsonify(error="Tu rol no puede usar este tipo de evento."), 403
            changes["id"] = event_id
            changes["creado_por"] = current.get("creado_por")
            events[i] = changes
            write_json(EVENTS_FILE, events)
            return jsonify(present_event(changes, g.user, user_names()))
    return jsonify(error="No se encontró el evento."), 404


@app.delete("/api/eventos/<event_id>")
@login_required
def delete_event(event_id):
    with events_lock:
        events = load_events()
        target = next((e for e in events if e["id"] == event_id), None)
        if target is None:
            return jsonify(error="No se encontró el evento."), 404
        if not can_modify(g.user, target):
            return jsonify(error="No tienes permiso para eliminar este evento."), 403
        write_json(EVENTS_FILE, [e for e in events if e["id"] != event_id])

    # El chat pertenece al evento: al eliminar el evento, también se elimina su historial.
    with messages_lock:
        messages = read_json(MESSAGES_FILE, [])
        write_json(MESSAGES_FILE, [m for m in messages if m.get("evento_id") != event_id])

    socketio.emit("event_deleted", {"evento_id": event_id}, room="evento:" + event_id)
    return "", 204


# ---------------------------------------------------------------------------
# Administración de usuarios (solo el jefe)
# ---------------------------------------------------------------------------

def public_user(user):
    """Datos de un usuario que se pueden enviar al navegador (sin la contraseña)."""
    return {k: user[k] for k in ("id", "usuario", "nombre", "rol")}


def clean_new_user(data, existing_users):
    if not isinstance(data, dict):
        return None, "Los datos enviados no son válidos."

    name = str(data.get("nombre") or "").strip()
    username = str(data.get("usuario") or "").strip().lower()
    password = str(data.get("password") or "")
    role = data.get("rol")

    if not name:
        return None, "El nombre es obligatorio."
    if len(name) > 60:
        return None, "El nombre no puede pasar de 60 caracteres."
    if not re.fullmatch(r"[a-z0-9._-]{3,30}", username):
        return None, ("El usuario debe tener de 3 a 30 caracteres: "
                      "letras, números, punto, guion o guion bajo.")
    if any(u["usuario"] == username for u in existing_users):
        return None, "Ese usuario ya existe."
    if len(password) < 6:
        return None, "La contraseña debe tener al menos 6 caracteres."
    if role not in ROLES:
        return None, "El rol no es válido."

    user = {
        "id": uuid.uuid4().hex[:12],
        "usuario": username,
        "nombre": name,
        "rol": role,
        "password_hash": generate_password_hash(password),
    }
    return user, None


@app.get("/usuarios")
@boss_required
def users_page():
    ajustes = {"yo": g.user["id"], "roles": ROLES}
    return render_template("usuarios.html", ajustes=ajustes)


@app.get("/api/usuarios")
@boss_required
def list_users():
    return jsonify([public_user(u) for u in load_users()])


@app.post("/api/usuarios")
@boss_required
def create_user():
    with users_lock:
        users = read_json(USERS_FILE, [])
        user, error = clean_new_user(request.get_json(silent=True), users)
        if error:
            return jsonify(error=error), 400
        users.append(user)
        write_json(USERS_FILE, users)
    return jsonify(public_user(user)), 201


@app.put("/api/usuarios/<user_id>")
@boss_required
def update_user(user_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error="Los datos enviados no son válidos."), 400

    name = str(data.get("nombre") or "").strip()
    username = str(data.get("usuario") or "").strip().lower()
    password = str(data.get("password") or "")
    role = data.get("rol")

    if not name:
        return jsonify(error="El nombre es obligatorio."), 400
    if len(name) > 60:
        return jsonify(error="El nombre no puede pasar de 60 caracteres."), 400
    if not re.fullmatch(r"[a-z0-9._-]{3,30}", username):
        return jsonify(error=(
            "El usuario debe tener de 3 a 30 caracteres: "
            "letras, números, punto, guion o guion bajo."
        )), 400
    if role not in ROLES:
        return jsonify(error="El rol no es válido."), 400
    if password and len(password) < 6:
        return jsonify(error="La contraseña debe tener al menos 6 caracteres."), 400

    with users_lock:
        users = read_json(USERS_FILE, [])
        target = next((u for u in users if u["id"] == user_id), None)
        if target is None:
            return jsonify(error="No se encontró el usuario."), 404

        # La cuenta que está usando el jefe siempre conserva el rol de jefe.
        if user_id == g.user["id"] and role != "jefe":
            return jsonify(error="La cuenta del jefe no puede perder el rol de jefe."), 400

        if any(u["usuario"] == username and u["id"] != user_id for u in users):
            return jsonify(error="Ese usuario ya existe."), 400

        target["nombre"] = name
        target["usuario"] = username
        target["rol"] = role if user_id != g.user["id"] else "jefe"
        if password:
            target["password_hash"] = generate_password_hash(password)

        write_json(USERS_FILE, users)
        return jsonify(public_user(target))


@app.delete("/api/usuarios/<user_id>")
@boss_required
def delete_user(user_id):
    if user_id == g.user["id"]:
        return jsonify(error="No puedes eliminar tu propia cuenta."), 400

    with users_lock:
        users = read_json(USERS_FILE, [])
        remaining = [u for u in users if u["id"] != user_id]
        if len(remaining) == len(users):
            return jsonify(error="No se encontró el usuario."), 404
        write_json(USERS_FILE, remaining)
    return "", 204


if __name__ == "__main__":
    # 0.0.0.0 permite entrar desde otros dispositivos de la misma red Wi-Fi.
    # SocketIO se encarga de los WebSockets y del servidor de desarrollo.
    socketio.run(app, host="0.0.0.0", port=5000, debug=True)
