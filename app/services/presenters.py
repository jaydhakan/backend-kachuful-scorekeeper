"""Rows → contract models. The one place that shapes users and players for the wire."""

from app.db.models import Player, User
from app.domain.appearance import known_theme
from app.domain.time import to_ms, to_ms_or_none
from app.repositories.games import SummaryRow
from app.repositories.players import PlayerView
from app.schemas.auth import PreferencesOut, UserOut
from app.schemas.games import GameSummaryOut
from app.schemas.players import PlayerOut, PlayerSummaryOut


def user_out(user: User) -> UserOut:
    """The contract's User."""
    return UserOut(
        id=user.id,
        email=user.email,
        role="admin" if user.role == "admin" else "member",
        player_id=user.player_id,
        created_at=to_ms(user.created_at),
    )


def preferences_out(user: User) -> PreferencesOut:
    """A user's own preferences: the stored `{theme?, setup?}` and when they last changed."""
    return PreferencesOut.model_validate(
        {
            "setup": None,
            **user.preferences,
            # An id this build no longer ships follows the admin's theme instead of failing.
            "theme": known_theme(user.preferences.get("theme")),
            "updated_at": to_ms_or_none(user.preferences_updated_at),
        }
    )


def photo_url(player_id: object, photo_rev: int) -> str:
    """Where a player's photo is served; the rev makes each version a new, cacheable URL."""
    return f"/api/players/{player_id}/photo?v={photo_rev}"


def can_see_email(viewer: User | None, player: Player) -> bool:
    """Only the admin, the player's creator and the player's linked user see an email."""
    if viewer is None:
        return False
    return viewer.role == "admin" or viewer.id == player.created_by or viewer.player_id == player.id


def _player_fields(view: PlayerView) -> dict[str, object]:
    player = view.player
    return {
        "id": player.id,
        "display_name": player.display_name,
        "photo_url": photo_url(player.id, player.photo_rev) if view.has_photo else None,
        "first_name": player.first_name,
        "last_name": player.last_name,
        "emoji": player.emoji,
        "color": player.color,
        "aliases": [
            {"display_name": a.display_name, "first_name": a.first_name, "last_name": a.last_name}
            for a in view.aliases
        ],
        "created_by": player.created_by,
        "merged_into": player.merged_into,
        "created_at": to_ms(player.created_at),
        "updated_at": to_ms(player.updated_at),
    }


def player_out(view: PlayerView, viewer: User | None) -> PlayerOut:
    """The contract's Player, with the email only for those allowed to see it."""
    email = view.player.email if can_see_email(viewer, view.player) else None
    return PlayerOut.model_validate(
        {**_player_fields(view), "email": email, "linked_user_id": view.linked_user_id}
    )


def player_summary_out(view: PlayerView) -> PlayerSummaryOut:
    """The contract's PlayerSummary: never an email."""
    return PlayerSummaryOut.model_validate(
        {
            **_player_fields(view),
            "email": None,
            "linked_user_id": view.linked_user_id,
            "games": view.player.games_count,
            "last_played_at": to_ms_or_none(view.player.last_played_at),
            "created_by_name": view.created_by_name,
        }
    )


def summary_out(row: SummaryRow, *, public: bool = False) -> GameSummaryOut:
    """The contract's GameSummary; `public` hides the owner's user id."""
    stored = row.summary
    players = [
        {**seat, "photoUrl": _seat_photo(row.photos, seat.get("playerId"))}
        for seat in stored["players"]
    ]
    return GameSummaryOut.model_validate(
        {
            **stored,
            "players": players,
            "id": row.game_id,
            "owner": {"id": None if public else row.owner_id, "name": row.owner_name},
            "updatedAt": to_ms(row.updated_at),
            "live": row.live,
        }
    )


def _seat_photo(photos: dict[str, int], player_id: str | None) -> str | None:
    if player_id is None or player_id not in photos:
        return None
    return photo_url(player_id, photos[player_id])
