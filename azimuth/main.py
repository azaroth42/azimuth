from flask import Flask, request, render_template, jsonify
from flask_socketio import SocketIO, emit
import persistence
from world import setup_world
import atexit
import dotenv
import os

dotenv.load_dotenv()

# --- Flask & SocketIO Setup ---
app = Flask(__name__)
socketio = SocketIO(app)

world_id = os.getenv("AZIMUTH_WORLD_ID", "WORLD1")
db_type = os.getenv("AZIMUTH_DB_TYPE", "file")

if db_type == "file":
    db = persistence.SimpleFileStorage()
elif db_type == "marklogic":
    url = os.getenv("AZIMUTH_ML_URL", "http://localhost:8000")
    user = os.getenv("AZIMUTH_ML_USER", "admin")
    password = os.getenv("AZIMUTH_ML_PASSWORD")
    dbname = os.getenv("AZIMUTH_ML_DB", "azimuth")
    db = persistence.MlStorage(url, user, password, dbname)


world = setup_world(db, world_id)
if not world:
    print("FATAL: Could not initialize world.")
    exit(1)


# --- Web Handler ---
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/data/<identifier>")
def fetch_data(identifier):
    if len(identifier) != 36:
        return db.get_object_by_id(identifier)
    else:
        return jsonify(db.load(identifier))


@app.route("/search/<name>")
def search_data(name):
    return jsonify(db.get_object_by_name(name))


# --- SocketIO Event Handlers ---
@socketio.on("connect")
def handle_connect():
    """Handles a new client connection. Does not log them in yet."""
    sid = request.sid
    print(f"Client connected: {sid}")
    # Ask the client to either login or register
    emit("message", world.motd)
    emit(
        "message",
        "Please 'login <username> <password>' or 'register <username> <password>'.",
        to=sid,
    )


@socketio.on("disconnect")
def handle_disconnect():
    """Handles a player disconnection"""
    sid = request.sid
    print(f"Client disconnected: {sid}")
    world.on_disconnect(sid)


@socketio.on("command")
def handle_command(data):
    """Handles commands received from a logged-in player."""
    sid = request.sid
    command_text = data.get("command") if isinstance(data, dict) else data  # Allow plain string command
    if not command_text or not isinstance(command_text, str):
        return

    command_text = command_text.strip()
    player_id = world.active_sids.get(sid, None)  # Get player ID from active session map

    # --- Handle pre-login commands: login, register ---
    if not player_id:
        parts = command_text.split()
        if not parts:
            return
        command_verb = parts[0].lower()
        args = parts[1:]

        if command_verb == "login" and len(args) == 2:
            msg = world.handle_login(sid, {"username": args[0], "password": args[1]})
        elif command_verb == "register" and len(args) == 3:
            msg = world.handle_register(sid, {"username": args[0], "password": args[1], "email": args[2]})
        elif command_verb in ["login", "register"]:
            msg = f"Usage: {command_verb} <username> <password> [email]"
        else:
            msg = "You must 'login <user> <pass>' or 'register <user> <pass> <email>' first."
        emit("message", msg, to=sid)
    else:
        world.process_player_command(player_id, command_text)


def dump_database():
    print("Exiting, dumping database")
    world.dump_database()


# --- Main Execution ---
if __name__ == "__main__":
    print("Starting SocketIO server...")
    atexit.register(dump_database)
    socketio.run(app, debug=True, host="0.0.0.0", port=5001)
