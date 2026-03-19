# Kalshi Trading Bot - GUI

A full-stack application for viewing Kalshi prediction markets with a TypeScript/Next.js frontend and Python/FastAPI backend.

## Project Structure

```
gui/
├── frontend/          # Next.js + TypeScript + Tailwind CSS
│   ├── app/          # Next.js app directory
│   ├── lib/          # API client and utilities
│   └── package.json
└── python_app/       # FastAPI backend
    ├── main.py       # FastAPI routes
    ├── settings.py   # Configuration
    ├── kalshi_rest.py   # REST API client
    └── kalshi_ws.py     # WebSocket client
```

## Setup

### 1. Backend Setup

The backend requires a `.env` file in the project root with your Kalshi API credentials:

```bash
# .env (in kalshi-bot/ root directory)
KALSHI_API_KEY=your_api_key_here
KALSHI_PRIVATE_KEY_PATH=kalshi_key.pem
KALSHI_WS_URL=wss://api.elections.kalshi.com/trade-api/ws/v2
FRONTEND_ORIGIN=http://localhost:3000
```

Install Python dependencies:
```bash
cd ../..  # Go to project root
pip install -r requirements.txt
```

### 2. Frontend Setup

Install Node.js dependencies:
```bash
cd gui/frontend
npm install
```

## Running the Application

### Start the Backend

From the project root:
```bash
uvicorn gui.python_app:app --reload --port 8000
```

The backend will be available at `http://localhost:8000`

API endpoints:
- `GET /health` - Health check
- `GET /api/series` - Get all series
- `GET /api/markets?series_ticker=XXX` - Get markets (optionally filtered by series)
- `GET /api/markets/{ticker}` - Get specific market details

### Start the Frontend

In a new terminal:
```bash
cd gui/frontend
npm run dev
```

The frontend will be available at `http://localhost:3000`

## Features

### Current Features
- ✅ Browse all Kalshi series
- ✅ View markets filtered by series
- ✅ See market details (prices, volume, status, etc.)
- ✅ Responsive UI with dark mode support
- ✅ REST API integration

### Coming Soon
- 🔜 Live WebSocket data streaming
- 🔜 Real-time price updates
- 🔜 Market charts and graphs
- 🔜 Trade execution

## Architecture

### Backend (FastAPI)
- **REST API**: Fetches series and market data from Kalshi's REST API
- **WebSocket**: Can stream live market data (to be integrated with frontend)
- **Authentication**: RSA-PSS signature-based authentication with Kalshi API

### Frontend (Next.js)
- **Server Components**: Fast initial page loads
- **Client Components**: Interactive UI with React hooks
- **Tailwind CSS**: Modern, responsive styling
- **TypeScript**: Type-safe API integration

## API Authentication

The backend handles authentication with Kalshi's API using:
1. API Key ID (from `.env`)
2. RSA Private Key (PEM file)
3. PSS signature with SHA-256

Each request is signed with a timestamp + method + path.

## Troubleshooting

### Backend won't start
- Ensure `.env` file exists in project root with valid `KALSHI_API_KEY`
- Check that `kalshi_key.pem` exists in project root
- Verify Python dependencies are installed

### Frontend can't connect to backend
- Ensure backend is running on port 8000
- Check CORS settings in `main.py`
- Verify `NEXT_PUBLIC_API_URL` in frontend `.env.local`

### API returns 401 errors
- Check that your API key is valid
- Ensure the private key matches your API key
- Verify the signature generation is working correctly

