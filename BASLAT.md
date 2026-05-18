

## Terminal 1 — Postgres

> Postgres zaten çalışıyorsa bu adımı atla.

**Önce:** [Docker Desktop] aç; 

    
```bash
cd "/Users/0xanrelins/Documents/Terminal Sirius"
docker compose up -d
```


## Terminal 2 — Backend

```bash
cd "/Users/0xanrelins/Documents/Terminal Sirius/backend"
.venv/bin/uvicorn main:app --reload --port 8000 --host 127.0.0.1
```


## Terminal 3 — Frontend

```bash
cd "/Users/0xanrelins/Documents/Terminal Sirius/frontend"
npm run dev
```
