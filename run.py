import uvicorn

# Have to use import form below to get reload to work
if __name__ == "__main__":
    uvicorn.run("azimuth.main:socket_app", host="0.0.0.0", port=5001, log_level="info", reload=True)
