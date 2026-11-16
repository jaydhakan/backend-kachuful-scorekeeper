from fastapi import APIRouter

from app.api.spades.games import spades_games_router
from app.api.spades.tables import spades_tables_router

# Table-mode sync and boards (`/games`, `/bootstrap`, `/leaderboard`, `/players`) and online tables.
spades_router = APIRouter(prefix="/spades", tags=["Spades"])
spades_router.include_router(spades_games_router)
spades_router.include_router(spades_tables_router)

__all__ = ["spades_router"]
