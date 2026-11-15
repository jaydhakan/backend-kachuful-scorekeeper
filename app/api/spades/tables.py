"""Spades online tables: the server runs the game."""

from fastapi import APIRouter

spades_tables_router = APIRouter(prefix="/tables")
