# Azimuth MUD - Uvicorn Migration Guide

## Overview

This application has been migrated from Flask + Flask-SocketIO to FastAPI + python-socketio with uvicorn server.

## Dependencies

Install the required dependencies:

```bash
pip install -r requirements.txt
```

Required packages:
- `fastapi>=0.104.0` - Modern web framework
- `uvicorn[standard]>=0.24.0` - ASGI server
- `python-socketio>=5.10.0` - SocketIO implementation for FastAPI
- `jinja2>=3.1.0` - Template engine
- `python-dotenv>=1.0.0` - Environment variable management
- `python-multipart>=0.0.6` - Form data parsing

## Running the Server

### Option 1: Direct execution
```bash
cd azimuth
python azimuth/main.py
```

### Option 2: Using the startup script
```bash
cd azimuth
python run.py
```

### Option 3: Using uvicorn directly
```bash
cd azimuth
uvicorn azimuth.main:socket_app --host 0.0.0.0 --port 5001 --reload
```

## Changes Made

### Main Application (`main.py`)
- Replaced Flask with FastAPI
- Replaced Flask-SocketIO with python-socketio AsyncServer
- Updated routing decorators (`@app.route` → `@app.get`)
- Updated template rendering for FastAPI
- Created SocketIO wrapper for backward compatibility with existing world code

### World Module (`world.py`)
- Removed Flask-SocketIO imports
- Added socketio instance injection from main.py
- Updated emit/join_room/leave_room methods to use injected socketio

### Template System
- Templates remain unchanged (still using Jinja2)
- Template directory structure unchanged

## Environment Variables

The application uses the same environment variables as before:
- `AZIMUTH_WORLD_ID` - World identifier (default: "WORLD1")
- `AZIMUTH_DB_TYPE` - Database type: "file" or "marklogic" (default: "file")
- `AZIMUTH_ML_URL` - MarkLogic URL (if using MarkLogic)
- `AZIMUTH_ML_USER` - MarkLogic username
- `AZIMUTH_ML_PASSWORD` - MarkLogic password
- `AZIMUTH_ML_DB` - MarkLogic database name

## API Endpoints

The REST API endpoints remain the same:
- `GET /` - Web client interface
- `GET /data/{identifier}` - Fetch object data
- `GET /search/{name}` - Search objects by name

## SocketIO Events

SocketIO events remain unchanged:
- `connect` - Client connection
- `disconnect` - Client disconnection
- `command` - Player commands

## Performance Benefits

- **Better async support**: Native async/await support throughout the stack
- **Improved performance**: uvicorn is significantly faster than Flask's development server
- **Production ready**: uvicorn is production-grade ASGI server
- **Better WebSocket handling**: More efficient SocketIO implementation
- **Hot reload**: Built-in code reloading during development

## Troubleshooting

### Common Issues

1. **Import errors**: Make sure you're running from the correct directory
2. **Port conflicts**: Check if port 5001 is already in use
3. **Template not found**: Ensure templates directory is in the correct location
4. **SocketIO connection issues**: Check browser console for WebSocket errors

### Logs

Uvicorn provides detailed logging. Set log level with `--log-level debug` for more verbose output.