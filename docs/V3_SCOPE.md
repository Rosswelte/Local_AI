# Perimetre V3.1

## Objectif

Ajouter la generation d'images locale via ComfyUI sans modifier le chemin
existant des jobs texte Ollama.

## Inclus

- Ajouter un provider/adaptateur ComfyUI configurable par `COMFYUI_URL`.
- Soumettre un workflow ComfyUI valide avec ses parametres utilisateur.
- Executer la generation comme job de type `image` via le scheduler existant.
- Relayer l'etat du job par le flux SSE existant.
- Recuperer les sorties image generees par ComfyUI.
- Copier les sorties dans `DATA_DIR/outputs/{job_id}` avec des noms surs.
- Ajouter une migration pour les metadonnees de sortie si necessaire.
- Ajouter une interface minimale : prompt, workflow, statut et galerie de
  resultats.
- Ajouter des tests pour le provider, le job image, les erreurs et la securite
  des chemins de sortie.

## API cible

- `GET /api/v1/image/workflows` : workflows disponibles.
- `POST /api/v1/image/jobs` : creation d'un job image.
- `GET /api/v1/jobs/{id}/stream` : progression du job.
- `GET /api/v1/image/jobs/{id}/outputs` : sorties disponibles.

Les routes devront reutiliser l'authentification, les erreurs et les limites
du backend existant.

## Contraintes

- Le provider ComfyUI reste local par defaut.
- Aucun acces arbitraire au systeme de fichiers depuis un workflow ou une
  requete utilisateur.
- Les sorties sont limitees par taille, nombre et extension.
- Le job image doit respecter les timeouts et retries du scheduler.
- Les jobs texte Ollama et les endpoints V2 doivent rester compatibles.

## Hors perimetre

- Generation video.
- STT et TTS.
- Agent autonome et sandbox d'execution.
- Recherche web.
- Marketplace ou telechargement automatique de checkpoints ComfyUI.
- Galerie avancee, edition ou historique multi-projets.

## Criteres d'acceptation

- Un workflow ComfyUI de reference peut etre soumis depuis l'API.
- Le job passe par `queued`, `running`, puis `completed` ou `failed`.
- La progression est visible via SSE.
- Une image valide est conservee dans `DATA_DIR/outputs` et servie par une
  route controlee.
- Les erreurs ComfyUI et les timeouts sont convertis en erreurs applicatives.
- Les tests V2 existants passent sans modification de comportement.
- Le lancement de ComfyUI et le telechargement de modeles sont documentes
  comme operations potentiellement longues, mais ne sont pas executes dans
  cette phase de cadrage.
