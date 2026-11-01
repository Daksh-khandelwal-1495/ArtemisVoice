# Docker Development

Docker Compose runs the FastAPI evaluator and React dashboard together.

```bash
docker compose up --build
```

Open `http://localhost:5173`, then select **Run demo investigation**. The
dashboard proxies `/api` requests to the internal backend service. SQLite data
persists in the `artemis-data` named volume.

Useful commands:

```bash
docker compose ps
docker compose logs -f backend
docker compose down
```

Remove persistent local evaluation data only when desired:

```bash
docker compose down -v
```
