"""FastAPI Server - Main Application"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from .settings import settings
from .routes import market_data, config, websockets, trading
from .nba.router import router as nba_router

app = FastAPI(title="Kalshi Trading API")

# Serve the Kalshi Client Web app at /client
_CLIENT_DIR = Path(__file__).parent.parent / "Kalshi-Client-Web" / "out"
if _CLIENT_DIR.exists():
    # Serve Next.js static assets
    app.mount("/client/_next", StaticFiles(directory=_CLIENT_DIR / "_next"), name="client-next-assets")

    @app.get("/client")
    @app.get("/client/")
    async def client_index():
        return FileResponse(_CLIENT_DIR / "index.html")

    @app.get("/client/{path:path}")
    async def client_catchall(path: str):
        # Try exact file first, then fall back to index.html (SPA routing)
        file = _CLIENT_DIR / path
        if file.is_file():
            return FileResponse(file)
        return FileResponse(_CLIENT_DIR / "index.html")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(market_data.router)
app.include_router(config.router)
app.include_router(websockets.router)
app.include_router(trading.router)
app.include_router(nba_router)


@app.get("/health")
def health():
    return {"ok": True}


@app.on_event("startup")
async def startup():
    pass


@app.on_event("shutdown")
async def shutdown():
    pass
