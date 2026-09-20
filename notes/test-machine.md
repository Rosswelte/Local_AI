# Mesures de validation

Mesures relevées le 20 septembre 2026 avant installation d'un modèle Ollama:

| Mesure | CPU | GPU |
|---|---:|---:|
| RAM libre avant/après chargement | 298 MiB / non mesurée | 298 MiB / non mesurée |
| Tokens par seconde | non mesuré | non mesuré |
| Temps de chargement à froid | non mesuré | non mesuré |
| `size_vram` dans `/api/ps` | - | non mesuré |

Machine: Intel Core i5-5200U, 4 threads, 7.7 GiB de RAM; GeForce 840M,
4096 MiB de VRAM totale et 4034 MiB libres lors de la mesure. Ollama hôte est
inactif. Les mesures de débit et de `size_vram` restent à faire avec
`qwen3:1.7b`; le catalogue conserve donc des estimations prudentes.
