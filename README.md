# Orchestrateur IA

V1 minimale d'un orchestrateur FastAPI pour Ollama, avec SQLite, catalogue de
modèles, jobs et chat SSE.

## Lancement local

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r backend/requirements.txt
DATA_DIR=./data OLLAMA_URL=http://127.0.0.1:11434 \
  uvicorn app.main:app --app-dir backend --reload --port 8000
```

`GET /api/v1/health` est disponible immédiatement. `GET /api/v1/ready` devient
disponible après l'ouverture de la base et la détection matérielle.

Les services distants se configurent via `POST /api/v1/services`. Le champ
`api_key` est chiffré avec la clé maître et n'est jamais renvoyé par l'API;
`ORCHESTRATOR_SECRET_KEY` permet de fournir cette clé, sinon elle est générée
dans `DATA_DIR/secrets/master.key` avec les permissions 0600.

## Docker

```bash
cp .env.example .env
docker compose up --build
```

Le GPU est optionnel: `docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build`.

## Tests

```bash
pytest -q backend/tests
```
