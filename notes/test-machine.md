# Mesures de validation

Mesures relevées le 21 septembre 2026 avec Ollama 0.34.2 et `qwen3:1.7b`:

| Mesure | CPU | GPU |
|---|---:|---:|
| RAM libre avant/après chargement | 209 MiB / 131 MiB | non mesuré |
| Tokens par seconde | 6.64 tok/s en génération | non mesuré |
| Temps de chargement à froid | 6.70 s | non mesuré |
| `size_vram` dans `/api/ps` | 0 B | non mesuré |

Machine: Intel Core i5-5200U, 4 threads, 7.7 GiB de RAM; GeForce 840M,
4096 MiB de VRAM totale et 4035 MiB libres lors de la mesure. Le modèle occupe
1882 MiB dans `/api/ps` en CPU. Le test GPU a été interrompu par un arrêt de la
machine; ses valeurs restent donc à mesurer. Le catalogue conserve des
estimations GPU prudentes.
