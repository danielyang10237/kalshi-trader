"""Entry point for the simulated exchange server."""

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "backend.server:app",
        host="0.0.0.0",
        port=9000,
        reload=True,
    )
