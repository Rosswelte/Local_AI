# Mesures de validation

Mesures relevées le 21 septembre 2026 avec Ollama 0.34.2 et `qwen3:1.7b`:

| Mesure | CPU | GPU |
|---|---:|---:|
| RAM libre avant/après chargement | 209 MiB / 131 MiB | 1995 MiB / 144 MiB |
| Tokens par seconde | 6.64 tok/s en génération | 8.14 tok/s en génération |
| Temps de chargement à froid | 6.70 s | 29.07 s |
| `size_vram` dans `/api/ps` | 0 B | 1811645725 B (~1729 MiB) |

Machine: Intel Core i5-5200U, 4 threads, 7.7 GiB de RAM; GeForce 840M,
4096 MiB de VRAM totale et 4035 MiB libres lors de la mesure. Le modèle occupe
1812 MiB dans `/api/ps` avec CUDA. Ollama signale CUDA avec compute capability
5.0; la variante CUDA 13 est ignorée, mais la variante CUDA 12 est utilisée.
