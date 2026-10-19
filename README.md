# VoltSpot - EV Charging Station App

| Part     | Tech                                   | Folder      |
|----------|----------------------------------------|-------------|
| Frontend | Node.js + Express (EJS pages, HTML forms) | `frontend/` |
| Backend  | Python Flask REST API                  | `backend/`  |
| Database | MongoDB (container, data kept in a volume) | -        |

The browser submits HTML forms to the Express server, which forwards each request to the Flask API (`/api/...`).
The Flask API stores stations and charging sessions in MongoDB. Compose starts the API only after MongoDB is healthy, and starts the frontend only after the API can reach MongoDB.

## Run
```bash
cp .env.example .env     # optional: set your own DB user/password
docker compose up --build
```
- App: http://localhost:8000
- API health check: http://localhost:5001/api/health

To start from a clean database: `docker compose down -v`.

## Run without Docker
```bash
# backend (needs a MongoDB running; set MONGO_URI / MONGO_USER / MONGO_PASSWORD if needed)
cd backend && pip install -r requirements.txt && python app.py      # http://localhost:5000

# frontend
cd frontend && npm install && BACKEND_URL=http://localhost:5000 npm start   # http://localhost:3000
```
