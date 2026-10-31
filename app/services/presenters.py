"""Rows → contract models. The one place that shapes users and players for the wire."""

from app.db.models import Player, User
from app.domain.time import to_ms, to_ms_or_none
from app.repositories.players import PlayerView
from app.schemas.auth import UserOut
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


def _player_fields(player: Player) -> dict[str, object]:
    return {
        "id": player.id,
        "display_name": player.display_name,
        "first_name": player.first_name,
        "last_name": player.last_name,
        "emoji": player.emoji,
        "color": player.color,
        "created_by": player.created_by,
        "merged_into": player.merged_into,
        "created_at": to_ms(player.created_at),
        "updated_at": to_ms(player.updated_at),
    }


def player_out(view: PlayerView) -> PlayerOut:
    """The contract's Player."""
    return PlayerOut.model_validate(
        {**_player_fields(view.player), "linked_user_id": view.linked_user_id}
    )


def player_summary_out(view: PlayerView) -> PlayerSummaryOut:
    """The contract's PlayerSummary."""
    return PlayerSummaryOut.model_validate(
        {
            **_player_fields(view.player),
            "linked_user_id": view.linked_user_id,
            "games": view.player.games_count,
            "last_played_at": to_ms_or_none(view.player.last_played_at),
            "created_by_name": view.created_by_name,
        }
    )
