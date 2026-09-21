# Orchestrateur IA

Orchestrateur local FastAPI pour Ollama et ComfyUI, avec SQLite, catalogue de
modeles, jobs, chat SSE, generations image, services distants chiffres et
suivi des ressources.

## Fonctionnalites V2.6

- Conversations et streaming SSE avec Ollama.
- Scheduler de jobs avec retries et annulation.
- Catalogue de modeles avec estimation de compatibilite.
- Eviction LRU et epinglage des modeles charges.
- Benchmark par modele et garde-fou RAM.
- Services OpenAI-compatible avec cles API chiffrees par Fernet.
- Migrations SQLite versionnees.

Le modele de validation principal est `qwen3:1.7b`. Sur la machine de
reference, il atteint 6.64 tok/s en CPU et 8.14 tok/s avec la GeForce 840M.
Les details sont dans `notes/test-machine.md`.

## Lancement Docker

```bash
cp .env.example .env
docker compose up --build
```

Ollama utilise l'image `ollama/ollama:0.34.2` et ecoute uniquement sur
`127.0.0.1:11434`. Le backend est disponible sur `http://127.0.0.1:8000`.

Pour utiliser le GPU NVIDIA :

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build
```

Le volume Docker `ollama` conserve les modeles entre les redemarrages.

## Lancement local

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r backend/requirements.txt
DATA_DIR=./data OLLAMA_URL=http://127.0.0.1:11434 \
  uvicorn app.main:app --app-dir backend --reload --port 8000
```

Documentation interactive : `http://127.0.0.1:8000/docs`.

## Configuration

- `DATA_DIR` : repertoire des donnees SQLite et des secrets.
- `OLLAMA_URL` : URL du serveur Ollama.
- `EXPOSE_HOST` et `PORT` : adresse et port du backend.
- `ADMIN_PASSWORD` : mot de passe d'administration optionnel.
- `ORCHESTRATOR_SECRET_KEY` : cle Fernet optionnelle; sinon elle est generee
  dans `DATA_DIR/secrets/master.key` avec les permissions `0600`.
- `LOG_LEVEL` : niveau de journalisation.

Les cles API des services distants sont chiffrees et ne sont jamais renvoyees
par l'API.

## API principale

- `GET /api/v1/health` : disponibilite du processus.
- `GET /api/v1/ready` : disponibilite de la base et de la detection materielle.
- `GET /api/v1/models` : catalogue et etat des modeles.
- Le service interne de benchmark mesure le debit d'un provider.
- `GET /api/v1/conversations` : liste des conversations.
- `POST /api/v1/conversations/{id}/messages` : creation d'un job de chat.
- `GET /api/v1/jobs/{id}/stream` : flux SSE du job.
- `POST /api/v1/services` : configuration d'un service distant.

## Tests

```bash
PYTHONPATH=backend .venv/bin/python -m pytest -q backend/tests
```

La suite actuelle contient 36 tests.

## V3.1 en cours

La branche `v3` ajoute la generation image via un provider ComfyUI HTTP. Le
workflow `reference` est versionne dans `backend/catalog/workflows/` et les
sorties sont conservees sous `DATA_DIR/outputs/{job_id}`.

Service ComfyUI (tache longue, ~10 Go a telecharger, a lancer manuellement) :

```bash
docker compose pull comfyui
docker compose up -d comfyui
```

L'image est epinglee a `yanwk/comfyui-boot:cu126-slim` (CUDA 12.6) : les
images `cu130` ne supportent pas Maxwell, donc pas la GeForce 840M de la
machine de reference. GPU via le profil existant :

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d comfyui
```

Checkpoints a deposer dans `./comfyui/models/checkpoints/` (jamais dans
l'image). Le workflow `reference` cite `sd_xl_base_1.0.safetensors`, mais
SDXL en 1024x1024 ne passe pas sur 7,7 Go de RAM + 4 Go de VRAM : viser un
modele leger (type SD 1.5) et des petites dimensions pour le premier test.
Avec moins de 6 Go de VRAM, demarrer avec `COMFYUI_CLI_ARGS=--lowvram`.

Configuration :

```bash
COMFYUI_URL=http://127.0.0.1:8188
```

Validation :

```bash
curl -sS http://127.0.0.1:8188/system_stats
```

Routes image :

- `GET /api/v1/image/workflows` : workflows controles disponibles.
- `POST /api/v1/image/jobs` : creation d'un job image.
- `GET /api/v1/jobs/{id}/stream` : progression et sorties SSE.
- `GET /api/v1/image/jobs/{id}/outputs` : metadonnees des sorties.
- `GET /api/v1/image/jobs/{id}/outputs/{output_id}` : fichier image controle.

Les checkpoints ne sont jamais telecharges par le projet : ils se deposent
dans `./comfyui/models/` et la generation reelle reste une etape
d'integration separee.
