# mud_client.py
import socketio
import threading
import sys
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory

# --- Configuration ---
SERVER_URL = "http://localhost:5001"  # Make sure this matches your server address and port

# --- SocketIO Client Setup ---
sio = socketio.Client()
connected = threading.Event()  # Event to signal successful connection
session = PromptSession(
    history=FileHistory(".mud_history")
)  # Use prompt_toolkit for input history


# --- Event Handlers ---
@sio.event
def connect():
    """Called when the client successfully connects to the server."""
    print("Connection established!")
    print("Type your commands below. Type 'quit' to exit.")
    connected.set()  # Signal that connection is successful


@sio.event
def connect_error(data):
    """Called when the client fails to connect to the server."""
    print(f"Connection failed: {data}")
    connected.set()  # Signal to stop waiting, even on failure


@sio.event
def disconnect():
    """Called when the client is disconnected from the server."""
    print("Disconnected from server.")
    connected.set()  # Signal to stop the input loop


@sio.event
def message(data=""):
    """Handles 'message' events received from the server (game output)."""
    # Print the message received from the MUD server
    print(f"\n{data}")


@sio.event
def disconnect_request():
    """Handles a request from the server to disconnect (e.g., after typing 'quit')."""
    print("Server requested disconnect. Closing connection...")
    sio.disconnect()


# --- Input Handling ---
def send_commands():
    """Function to run in a separate thread for handling user input."""
    connected.wait()  # Wait until connection is established or failed

    try:
        while True:
            # Use prompt_toolkit for input
            command = session.prompt("> ")
            if command:
                sio.emit("command", {"command": command})
            # If the connection drops while waiting for input, exit loop
            if not sio.connected:
                break
    except EOFError:  # Handle Ctrl+D
        print("\nEOF received, disconnecting...")
        if sio.connected:
            sio.disconnect()
    except KeyboardInterrupt:  # Handle Ctrl+C
        print("\nKeyboard interrupt received, disconnecting...")
        if sio.connected:
            sio.disconnect()
    finally:
        print("Input loop finished.")


# --- Main Execution ---
if __name__ == "__main__":
    # Start the input loop in a separate thread
    input_thread = threading.Thread(target=send_commands, daemon=True)
    input_thread.start()

    # Attempt to connect to the server
    try:
        print(f"Attempting to connect to {SERVER_URL}...")
        sio.connect(SERVER_URL)
        # Wait for the connection to close
        sio.wait()
    except socketio.exceptions.ConnectionError as e:
        print(f"Failed to connect to server: {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
    finally:
        print("Client shutting down.")
        connected.set()  # Make sure the event is set so the thread doesn't block forever
