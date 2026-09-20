# Conception V1

Cette copie décrit l'implémentation livrée dans ce workspace. La conception
v11 référencée par le guide n'était pas présente dans le répertoire initial.

## V2.1 livrée

La migration `002_v2_foundation.sql` ajoute les services locaux, les
comportements, les réglages avec mode automatique (`value = NULL`) et les
colonnes de modèle/job nécessaires à la suite. `ProviderManager` et
`ServiceManager` exposent déjà les points d'extension; les providers distants,
`ResourceManager` suit maintenant les modèles chargés, les réservations RAM/VRAM,
l'épinglage et l'éviction LRU hors verrou. Les providers distants, le scheduler
avancé et le chiffrement des secrets restent les prochains jalons.

La V1 utilise un seul worker Uvicorn, une connexion SQLite d'écriture confinée
à un thread, un catalogue YAML synchronisé sans écraser l'état local, un
provider Ollama asynchrone et un tampon SSE en mémoire limité à 200 événements
par job. Les jobs `text` interrompus sont marqués `failed/interrupted` au
redémarrage; les jobs non textuels sont remis en file.
