"""Integration smoke test. Set TEST_REDIS_URL to an isolated Redis database."""
import os
from io import BytesIO
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient
from PIL import Image


def check():
    with TemporaryDirectory() as directory:
        os.environ["LUDOTECA_DATA"] = directory
        os.environ["REDIS_URL"] = os.getenv("TEST_REDIS_URL", "redis://127.0.0.1:6379/15")
        from backend.main import app

        with TestClient(app) as client:
            def send(method, path, status=200, **kwargs):
                result = client.request(method, path, **kwargs)
                assert result.status_code == status, (method, path, result.text)
                return result

            assert send("GET", "/api/games").json()["total"] == 12
            send("GET", "/api/schema/game")
            send("POST", "/api/validate/game", 422, json={"app_id": 0, "name": "Prueba"})
            genre = send("POST", "/api/genres", 201, json={"name": "Prueba"}).json()
            genre_path = f"/api/genres/{genre['id']}"
            send("GET", "/api/genres/records")
            send("GET", genre_path)
            send("PUT", genre_path, json={"name": "Música"})
            game = {"app_id": 999001, "name": "Juego de prueba", "genres": ["Música"], "positive": 20}
            game_path = "/api/games/999001"
            send("POST", "/api/games", 201, json=game)
            send("POST", "/api/games", 409, json=game)
            assert send("GET", game_path).json()["genres"] == ["Música"]
            assert send("GET", "/api/games", params={"genre": "Música"}).json()["total"] == 1
            send("DELETE", genre_path, 409)
            ranking = "/api/ranking"
            filters = {"genre": "Música"}
            first = send("GET", ranking, params=filters)
            if os.getenv("TEST_REDIS_URL"):
                assert first.headers["X-Cache"] == "MISS"
                assert send("GET", ranking, params=filters).headers["X-Cache"] == "HIT"
            send("PUT", game_path, json={**game, "positive": 30})
            updated = send("GET", ranking, params=filters)
            assert updated.json()[0]["positive"] == 30
            if os.getenv("TEST_REDIS_URL"):
                assert updated.headers["X-Cache"] == "MISS"
            send("POST", game_path + "/cover", 415, files={"file": ("fake.png", b"invalid")})
            image = BytesIO()
            Image.new("RGB", (16, 16)).save(image, format="PNG")
            cover = send("POST", game_path + "/cover", files={"file": ("cover.png", image.getvalue())}).json()
            assert send("GET", cover["url"]).headers["content-type"] == "image/jpeg"
            assert send("GET", game_path).json()["header_image"] == cover["url"]
            send("DELETE", game_path, 204)
            send("GET", game_path, 404)
            send("DELETE", genre_path, 204)
            if os.path.isdir("dist"):
                assert "assets/" in send("GET", "/").text

        with TestClient(app) as client:
            assert client.get("/api/games").json()["total"] == 12
            assert client.get(game_path).status_code == 404

    print("OK: CRUD, SQLite, validación, portadas, ranking y frontend.")


if __name__ == "__main__":
    check()
