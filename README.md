# Ludoteca · Proyecto FastAPI

**Alumno:** Julio César Arias Gaytán
**Materia:** Programación para Internet

Catálogo de videojuegos con React y FastAPI. Juegos y géneros se relacionan de muchos a muchos y se guardan en SQLite con SQLModel. La primera ejecución carga 12 registros de muestra de `src/games.json` sin restaurar una base externa.

## Instalación

Necesitas Python 3.11+, Node.js 22+ y Redis. Para levantar Redis de forma local con Docker:

```sh
docker run --rm --name ludoteca-redis -p 127.0.0.1:6379:6379 redis:7-alpine
```

También puedes iniciar tu Redis instalado con `redis-server --bind 127.0.0.1`. Si usas otro servidor, configura `REDIS_URL`.

En otra terminal, desde la raíz del repositorio:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
npm ci
npm run build
python -m uvicorn backend.main:app
```

Abre <http://127.0.0.1:8000/> para ver el catálogo y <http://127.0.0.1:8000/docs> para ejecutar todos los endpoints. Para editar el frontend, puedes ejecutar `npm run dev` en otra terminal y abrir el enlace que muestra Vite.

La base y las portadas se guardan en `data/`, excluido de Git. La API está pensada para probarse localmente. Sus rutas de escritura no requieren autenticación.

## Ejemplos con curl

```sh
API=http://127.0.0.1:8000/api

# Géneros: crea uno y sustituye 99 por el ID que devuelva.
curl -X POST "$API/genres" -H 'Content-Type: application/json' -d '{"name":"Ritmo"}'
curl "$API/genres/records"
curl "$API/genres/99"
curl -X PUT "$API/genres/99" -H 'Content-Type: application/json' -d '{"name":"Música"}'

# Juegos: crear, listar, consultar, editar y borrar.
curl -X POST "$API/games" -H 'Content-Type: application/json' \
  -d '{"app_id":999001,"name":"Juego de muestra","genres":["Música"],"positive":20}'
curl "$API/games?genre=M%C3%BAsica"
curl "$API/games/999001"
curl -X PUT "$API/games/999001" -H 'Content-Type: application/json' \
  -d '{"app_id":999001,"name":"Juego actualizado","genres":["Música"],"positive":35}'

# Portada: sustituye portada.png por un archivo real.
curl -F 'file=@portada.png' "$API/games/999001/cover"

# Ranking: la primera consulta devuelve MISS y la segunda HIT con Redis activo.
curl -i "$API/ranking?genre=M%C3%BAsica"
curl -i "$API/ranking?genre=M%C3%BAsica"

# JSON Schema y ejemplo rechazado con 422 porque app_id debe ser positivo.
curl "$API/schema/game"
curl -i -X POST "$API/validate/game" -H 'Content-Type: application/json' \
  -d '{"app_id":0,"name":"Nombre"}'

curl -i -X DELETE "$API/games/999001"
curl -i -X DELETE "$API/genres/99"
```

Las portadas PNG, JPEG y WebP se validan, se convierten a JPEG y se sirven en `/covers/`. El ranking ordena por reseñas positivas, guarda resultados durante 60 segundos e invalida la caché después de cambios. Si Redis no está disponible, devuelve los datos directamente desde SQLite con `X-Cache: BYPASS`. El catálogo y el ranking usan datos de muestra, no métricas en vivo.

Fuente de los datos de muestra: [Steam Games Dataset](https://www.kaggle.com/datasets/hubertsidorowicz/steam-games-dataset-daily-updates).

## Pruebas

Con Redis local activo, ejecuta la prueba de integración. Usa una SQLite
temporal y una base Redis de prueba independiente de la del catálogo.

```sh
pip install httpx
TEST_REDIS_URL=redis://127.0.0.1:6379/15 python -m tests.check_api
```
