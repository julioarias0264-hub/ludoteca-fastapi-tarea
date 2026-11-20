import json
import os
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, Query, Response, UploadFile
from fastapi.staticfiles import StaticFiles
from jsonschema import Draft202012Validator
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field
from redis import Redis, RedisError
from redis.retry import Retry
from redis.backoff import NoBackoff
from sqlalchemy import event, func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Field as Column, Session, SQLModel, create_engine, select

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.getenv("LUDOTECA_DATA", str(ROOT / "data")))
DATA.mkdir(parents=True, exist_ok=True)
UPLOADS = DATA / "covers"
UPLOADS.mkdir(exist_ok=True)
engine = create_engine(f"sqlite:///{DATA / 'ludoteca.db'}", connect_args={"check_same_thread": False})
cache = Redis.from_url(os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
                       decode_responses=True, socket_timeout=1, socket_connect_timeout=1, retry=Retry(NoBackoff(), 0))
GENERATION = "ludoteca-fastapi:ranking:revision"


@event.listens_for(engine, "connect")
def enable_foreign_keys(connection, _record):
    connection.execute("PRAGMA foreign_keys=ON")


class Genre(SQLModel, table=True):
    id: int | None = Column(default=None, primary_key=True)
    name: str = Column(unique=True)


class Game(SQLModel, table=True):
    app_id: int = Column(primary_key=True)
    name: str
    price: float | None = None
    positive: int | None = None
    details: str = "{}"


class GameGenre(SQLModel, table=True):
    app_id: int = Column(foreign_key="game.app_id", primary_key=True)
    genre_id: int = Column(foreign_key="genre.id", primary_key=True)


class GenreInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)


class GameInput(BaseModel):
    app_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=500)
    release_date: str = Field(default='', max_length=100)
    price: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    short_description: str = Field(default='', max_length=10000)
    detailed_description: str = Field(default='', max_length=100000)
    header_image: str = Field(default='', max_length=2000)
    genres: list[str] = Field(default_factory=list, max_length=50)
    developers: list[str] = Field(default_factory=list, max_length=50)
    windows: bool = False
    mac: bool = False
    linux: bool = False
    website: str = Field(default='', max_length=2000)
    positive: int | None = Field(default=None, ge=0)
    negative: int | None = Field(default=None, ge=0)
    peak_ccu: int | None = Field(default=None, ge=0)
    metacritic_score: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)

def require(session, model, key):
    record = session.get(model, key)
    if record is None:
        raise HTTPException(404, "Registro no encontrado.")
    return record


def validate(model, data):
    errors = list(Draft202012Validator(model.model_json_schema()).iter_errors(data))
    if errors:
        raise HTTPException(422, errors[0].message)


def commit(session):
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "El registro ya existe.")
    try:
        cache.set(GENERATION, uuid4().hex)
    except RedisError:
        pass


def genre_query(app_id):
    return select(Genre.name).join(GameGenre).where(GameGenre.app_id == app_id).order_by(Genre.name)


def serialize(session, game):
    details = json.loads(game.details)
    price = game.price
    return {**details, "app_id": str(game.app_id), "name": game.name, "price": price,
            "positive": game.positive, "genres": list(session.exec(genre_query(game.app_id))),
            "price_status": "unknown" if price is None else "free" if price == 0 else "paid"}


def write_game(session, data, game):
    name = data.name.strip()
    if not name:
        raise HTTPException(422, "Escribe el nombre del juego.")
    genre_names = list(dict.fromkeys(GenreInput(name=name).name for name in data.genres
                                    if name.strip()))
    game.name = name
    game.price = data.price
    game.positive = data.positive
    game.details = data.model_dump_json(exclude={"app_id", "name", "price", "positive", "genres"})
    session.add(game)
    try:
        session.flush()
        for link in session.exec(select(GameGenre).where(GameGenre.app_id == game.app_id)):
            session.delete(link)
        session.flush()
        for genre_name in genre_names:
            genre = session.exec(select(Genre).where(Genre.name == genre_name)).first()
            if genre is None:
                genre = Genre(name=genre_name)
                session.add(genre)
                session.flush()
            session.add(GameGenre(app_id=game.app_id, genre_id=genre.id))
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "El juego o género ya existe.")


@asynccontextmanager
async def lifespan(_app):
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        if not (DATA / ".seeded").exists():
            sample = json.loads((ROOT / "src" / "games.json").read_text())
            for item in sample[:12]:
                if item.get("price") == "":
                    item["price"] = None
                data = GameInput.model_validate(item)
                if session.get(Game, data.app_id) is None:
                    write_game(session, data, Game(app_id=data.app_id))
            commit(session)
            (DATA / ".seeded").touch()
    yield


app = FastAPI(title="Ludoteca · Programación para Internet", lifespan=lifespan)
app.mount("/covers", StaticFiles(directory=UPLOADS), name="covers")


@app.get("/api/health")
def health():
    with Session(engine) as session:
        session.exec(select(1)).one()
    return {"status": "ok"}


@app.get("/api/schema/game")
def game_schema():
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", **GameInput.model_json_schema()}


@app.get("/api/genres")
def genre_names():
    with Session(engine) as session:
        return session.exec(select(Genre.name).order_by(Genre.name)).all()


@app.get("/api/genres/records", response_model=list[Genre])
def list_genres():
    with Session(engine) as session:
        return session.exec(select(Genre).order_by(Genre.name)).all()


@app.get("/api/genres/{genre_id}", response_model=Genre)
def get_genre(genre_id: int):
    with Session(engine) as session:
        return require(session, Genre, genre_id)


@app.post("/api/genres", status_code=201, response_model=Genre)
def create_genre(data: GenreInput):
    with Session(engine) as session:
        genre = Genre(name=data.name)
        session.add(genre)
        commit(session)
        session.refresh(genre)
        return genre


@app.put("/api/genres/{genre_id}", response_model=Genre)
def update_genre(genre_id: int, data: GenreInput):
    with Session(engine) as session:
        genre = require(session, Genre, genre_id)
        genre.name = data.name
        commit(session)
        session.refresh(genre)
        return genre


@app.delete("/api/genres/{genre_id}", status_code=204)
def delete_genre(genre_id: int):
    with Session(engine) as session:
        genre = require(session, Genre, genre_id)
        if session.exec(select(GameGenre).where(GameGenre.genre_id == genre_id)).first():
            raise HTTPException(409, "Quita el género de sus juegos antes de eliminarlo.")
        session.delete(genre)
        commit(session)


@app.get("/api/games")
def games(q: str = Query("", max_length=200), genre: str = "", kind: str = "",
          page: int = Query(1, ge=1), limit: int = Query(24, ge=1, le=100)):
    if kind not in ("", "free", "paid"):
        raise HTTPException(422, "Filtro de precio inválido.")
    query = select(Game)
    if q:
        query = query.where(Game.name.icontains(q, autoescape=True))
    if genre:
        query = query.join(GameGenre).join(Genre).where(Genre.name == genre)
    if kind:
        query = query.where(Game.price == 0 if kind == "free" else Game.price > 0)
    with Session(engine) as session:
        total = session.exec(select(func.count()).select_from(query.subquery())).one()
        rows = session.exec(query.order_by(Game.name, Game.app_id)
                            .offset((page - 1) * limit).limit(limit))
        return {"items": [serialize(session, row) for row in rows],
                "total": total, "page": page, "limit": limit}


@app.get("/api/games/{app_id}")
def detail(app_id: int):
    with Session(engine) as session:
        return serialize(session, require(session, Game, app_id))


def save_game(data, app_id, creating):
    if data.app_id != app_id:
        raise HTTPException(422, "El App ID no coincide.")
    if any(len(name.strip()) > 80 for name in data.genres):
        raise HTTPException(422, "Cada género admite hasta 80 caracteres.")
    with Session(engine) as session:
        game = session.get(Game, app_id)
        if creating:
            if game is not None:
                raise HTTPException(409, "Ya existe un juego con ese App ID.")
            game = Game(app_id=app_id)
        elif game is None:
            raise HTTPException(404, "Juego no encontrado.")
        write_game(session, data, game)
        commit(session)
        return serialize(session, game)


@app.post("/api/games", status_code=201)
def create_game(data: GameInput):
    return save_game(data, data.app_id, True)


@app.put("/api/games/{app_id}")
def update_game(app_id: int, data: GameInput):
    return save_game(data, app_id, False)


@app.delete("/api/games/{app_id}", status_code=204)
def delete_game(app_id: int):
    with Session(engine) as session:
        game = require(session, Game, app_id)
        for link in session.exec(select(GameGenre).where(GameGenre.app_id == app_id)):
            session.delete(link)
        session.flush()
        session.delete(game)
        commit(session)


@app.post("/api/validate/game")
def validate_game(payload: dict):
    validate(GameInput, payload)
    return {"valid": True}


@app.get("/api/ranking")
def ranking(response: Response, genre: str = Query("", max_length=80),
            limit: int = Query(10, ge=1, le=50)):
    key = None
    response.headers["X-Cache"] = "BYPASS"
    try:
        revision = cache.get(GENERATION) or "initial"
        key = f"ludoteca-fastapi:ranking:{revision}:{limit}:{genre}"
        cached = cache.get(key)
        if cached is not None:
            response.headers["X-Cache"] = "HIT"
            return json.loads(cached)
    except RedisError:
        key = None
    query = select(Game).where(Game.positive.is_not(None))
    if genre:
        query = query.join(GameGenre).join(Genre).where(Genre.name == genre)
    with Session(engine) as session:
        rows = session.exec(query.order_by(Game.positive.desc(), Game.app_id).limit(limit))
        result = [serialize(session, row) for row in rows]
    if key is not None:
        try:
            cache.setex(key, 60, json.dumps(result))
            response.headers["X-Cache"] = "MISS"
        except RedisError:
            pass
    return result


@app.post("/api/games/{app_id}/cover")
def upload_cover(app_id: int, file: UploadFile = File(...)):
    max_bytes = 5 * 1024 * 1024
    with Session(engine) as session:
        game = require(session, Game, app_id)
        content = file.file.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise HTTPException(413, "La portada no puede superar 5 MB.")
        try:
            with Image.open(BytesIO(content), formats=["PNG", "JPEG", "WEBP"]) as image:
                if image.width * image.height > 16_000_000:
                    raise HTTPException(413, "La imagen supera 16 megapíxeles.")
                converted = image.convert("RGB")
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
            raise HTTPException(415, "Sube una imagen PNG, JPEG o WebP válida.")
        filename = f"{uuid4().hex}.jpg"
        path = UPLOADS / filename
        converted.save(path, format="JPEG")
        details = json.loads(game.details)
        details["header_image"] = f"/covers/{filename}"
        game.details = json.dumps(details)
        try:
            commit(session)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return {"url": details["header_image"]}


if (ROOT / "dist").is_dir():
    app.mount("/", StaticFiles(directory=ROOT / "dist", html=True), name="frontend")
