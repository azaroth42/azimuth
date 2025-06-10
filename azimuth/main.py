from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi_mcp import FastApiMCP
import socketio
from .persistence import SimpleFileStorage, MlStorage
from .world import setup_world
import dotenv
import os
import uvicorn

dotenv.load_dotenv()

# --- FastAPI & SocketIO Setup ---
app = FastAPI()


# Create Socket.IO server
sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")
socket_app = socketio.ASGIApp(sio, app)
templates = Jinja2Templates(directory="azimuth/templates")


world_id = os.getenv("AZIMUTH_WORLD_ID", "WORLD1")
db_type = os.getenv("AZIMUTH_DB_TYPE", "file")

if db_type == "file":
    db = SimpleFileStorage()
elif db_type == "marklogic":
    url = os.getenv("AZIMUTH_ML_URL", "http://localhost:8000")
    user = os.getenv("AZIMUTH_ML_USER", "admin")
    password = os.getenv("AZIMUTH_ML_PASSWORD")
    dbname = os.getenv("AZIMUTH_ML_DB", "azimuth")
    db = MlStorage(url, user, password, dbname)

world = setup_world(db, world_id)
if not world:
    print("FATAL: Could not initialize world.")
    exit(1)

# Inject SocketIO wrapper into world for compatibility
world.socketio = sio


# --- Web Client ---
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


# Inspect the database
@app.get("/data/{identifier}", operation_id="get_record")
async def fetch_data(identifier: str):
    """Given the UUID identifier of an object, or sufficiently many characters to make it unique, return the object."""
    if len(identifier) != 36:
        return db.get_object_by_id(identifier)
    else:
        return JSONResponse(db.load(identifier))


@app.get("/search/{name}", operation_id="search_record")
async def search(name: str):
    """Given a name of an object, search for it and return the object."""
    return JSONResponse(db.get_object_by_name(name))


# --- SocketIO Event Handlers ---
@sio.event
async def connect(sid, environ):
    """Handles a new client connection. Does not log them in yet."""
    print(f"Client connected: {sid}")
    # Ask the client to either login or register
    await sio.emit("message", world.motd, to=sid)
    await sio.emit(
        "message",
        "Please 'login <username> <password>' or 'register <username> <password>'.",
        to=sid,
    )


@sio.event
async def disconnect(sid):
    """Handles a player disconnection"""
    print(f"Client disconnected: {sid}")
    world.on_disconnect(sid)


@sio.event
async def command(sid, data):
    """Handles commands received from a player."""
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
        await sio.emit("message", msg, to=sid)
    else:
        # Process command synchronously for now
        world.process_player_command(player_id, command_text)


@app.on_event("shutdown")
def on_shutdown():
    world.dump_database()


mcp = FastApiMCP(app, name="Azimuth MCP Server", describe_all_responses=True, describe_full_response_schema=True)
mcp.mount()

# --- Main Execution ---
if __name__ == "__main__":
    print("Starting uvicorn server...")
    uvicorn.run(socket_app, host="0.0.0.0", port=5001, log_level="info")
