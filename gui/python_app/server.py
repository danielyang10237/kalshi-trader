"""FastAPI Server - Main Application"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .settings import settings
from .routes import market_data, config, websockets, trading

app = FastAPI(title="Kalshi Trading API")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(market_data.router)
app.include_router(config.router)
app.include_router(websockets.router)
app.include_router(trading.router)


@app.get("/health")
def health():
    return {"ok": True}


@app.on_event("startup")
async def startup():
    pass


@app.on_event("shutdown")
async def shutdown():
    pass
