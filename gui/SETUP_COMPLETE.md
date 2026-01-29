# Kalshi Trading Bot - Setup Complete ✅

## What We Built

A full-stack web application for viewing Kalshi prediction markets:

**Backend (Python/FastAPI)**
- REST API endpoints for fetching series and markets
- Authenticated Kalshi API client with RSA-PSS signing
- WebSocket client (ready for live data streaming)

**Frontend (TypeScript/Next.js)**
- Series browser with dropdown selection
- Market viewer filtered by series
- Market details display (prices, volume, status)
- Modern UI with Tailwind CSS and dark mode

## Quick Start

### 1. Install Backend Dependencies
```bash
cd /Users/danielyang/Desktop/Extra_Curriculars/kalshi-bot
pip install -r requirements.txt
```

### 2. Install Frontend Dependencies
```bash
cd gui/frontend
npm install
```

### 3. Start Backend (Terminal 1)
```bash
cd /Users/danielyang/Desktop/Extra_Curriculars/kalshi-bot
uvicorn gui.python_app:app --reload --port 8000
```

### 4. Start Frontend (Terminal 2)
```bash
cd /Users/danielyang/Desktop/Extra_Curriculars/kalshi-bot/gui/frontend
npm run dev
```

### 5. Open Browser
Navigate to: http://localhost:3000

## Project Structure

```
kalshi-bot/
├── .env                    # API credentials (you need to create this)
├── kalshi_key.pem         # Private key (you need to provide this)
├── requirements.txt       # Python dependencies
└── gui/
    ├── python_app/        # FastAPI backend
    │   ├── __init__.py   # Exports app
    │   ├── main.py       # API routes
    │   ├── settings.py   # Configuration
    │   ├── kalshi_rest.py   # REST client
    │   └── kalshi_ws.py     # WebSocket client
    └── frontend/          # Next.js frontend
        ├── app/          # Pages and layouts
        │   ├── page.tsx  # Main UI component
        │   ├── layout.tsx
        │   └── globals.css
        ├── lib/
        │   └── api.ts    # API client functions
        ├── package.json
        └── tsconfig.json
```

## API Endpoints

**Backend (http://localhost:8000)**
- `GET /health` - Health check
- `GET /api/series?limit=100` - Get all series
- `GET /api/markets?series_ticker=XXX&status=open` - Get markets
- `GET /api/markets/{ticker}` - Get specific market

**Frontend (http://localhost:3000)**
- Home page with series/market selection

## Environment Variables

### .env (project root)
```bash
KALSHI_API_KEY=your_api_key_here
KALSHI_PRIVATE_KEY_PATH=kalshi_key.pem
KALSHI_WS_URL=wss://api.elections.kalshi.com/trade-api/ws/v2
FRONTEND_ORIGIN=http://localhost:3000
```

### frontend/.env.local (auto-created)
```bash
NEXT_PUBLIC_API_URL=http://localhost:8000
```

## Features

### ✅ Implemented
1. Series browsing - view all available Kalshi series
2. Market filtering - filter markets by selected series
3. Market details - display prices, volume, status, close times
4. REST API integration - authenticated requests to Kalshi
5. Responsive UI - works on desktop and mobile
6. Dark mode support

### 🔜 Coming Next
1. WebSocket integration for live data
2. Real-time price updates
3. Market charts/graphs
4. Trade execution interface

## Authentication Fix

We fixed the authentication issues:
- ✅ Changed from 401 errors to working auth
- ✅ Fixed DNS issue by using production URL
- ✅ Proper RSA-PSS signature generation
- ✅ Correct header formatting

The signature format: `timestamp + METHOD + /path`

## How It Works

1. **User opens frontend** → Next.js loads the UI
2. **Frontend fetches series** → Calls `/api/series`
3. **Backend authenticates** → Signs request with RSA key
4. **Kalshi returns data** → Backend forwards to frontend
5. **User selects series** → Frontend fetches markets
6. **Markets display** → User can browse and select markets

## Next Steps

To enable live data streaming:
1. User selects a market
2. Frontend connects to `/ws/market` WebSocket
3. Backend subscribes to Kalshi WebSocket for that market
4. Live updates flow: Kalshi → Backend → Frontend

## Troubleshooting

**Backend won't start:**
- Check `.env` file exists with `KALSHI_API_KEY`
- Verify `kalshi_key.pem` is in project root
- Run `pip install -r requirements.txt`

**Frontend errors:**
- Run `npm install` in `gui/frontend/`
- Check backend is running on port 8000
- Verify `.env.local` has correct `NEXT_PUBLIC_API_URL`

**API returns errors:**
- Check your API key is valid
- Ensure private key matches your API key
- Try the production URL instead of demo

## Files Created/Modified

### New Files
- `gui/python_app/kalshi_rest.py` - REST API client
- `gui/frontend/` - Complete Next.js app
  - `app/page.tsx` - Main UI
  - `lib/api.ts` - API client
  - `package.json`, `tsconfig.json`, etc.
- `gui/README.md` - Documentation

### Modified Files
- `gui/python_app/main.py` - Added REST endpoints
- `gui/python_app/settings.py` - Updated URL to production
- `gui/python_app/__init__.py` - Export app
- `requirements.txt` - Added requests

Ready to use! 🚀

