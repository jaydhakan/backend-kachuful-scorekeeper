"""The Wardrobe and the Bazaar (rewards finish §3-§4): the catalogue, prices, level gifts, looks and
the weekly stock.

Mirrors the app's `src/wardrobe/catalog.ts`; parity is the `wardrobe-catalog` golden fixture. Pure:
ownership facts come in, decisions come out.
"""

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

from app.domain.time import from_ms, to_ms


class Slot(StrEnum):
    """Where an item is worn; a look has at most one item per slot."""

    OUTFIT = "outfit"
    HEADWEAR = "headwear"
    NECKWEAR = "neckwear"
    FRAME = "frame"
    AURA = "aura"
    BADGE = "badge"
    TITLE = "title"


class Rarity(StrEnum):
    """How rare an item is; `honour` items are earned, never sold."""

    COMMON = "common"
    RARE = "rare"
    EPIC = "epic"
    LEGENDARY = "legendary"
    HONOUR = "honour"


class Source(StrEnum):
    """How a player came to own an item."""

    BOUGHT = "bought"
    GIFT = "gift"
    LEVEL = "level"
    HONOUR = "honour"


@dataclass(frozen=True)
class Item:
    """One catalogue item."""

    id: str
    slot: Slot
    rarity: Rarity
    variants: int
    sets: tuple[str, ...] = ()

    @property
    def sellable(self) -> bool:
        """Honour items are never sold or given."""
        return self.rarity != Rarity.HONOUR


def _i(item_id: str, slot: Slot, rarity: Rarity, variants: int, *sets: str) -> Item:
    return Item(item_id, slot, rarity, variants, sets)


_C, _R, _E, _L, _H = Rarity.COMMON, Rarity.RARE, Rarity.EPIC, Rarity.LEGENDARY, Rarity.HONOUR
_O, _HW, _N, _F, _A, _B, _T = (
    Slot.OUTFIT,
    Slot.HEADWEAR,
    Slot.NECKWEAR,
    Slot.FRAME,
    Slot.AURA,
    Slot.BADGE,
    Slot.TITLE,
)

CATALOGUE: tuple[Item, ...] = (
    _i("kurta", _O, _C, 6),
    _i("hoodie", _O, _C, 4),
    _i("office_shirt", _O, _C, 3, "office"),
    _i("cricket_whites", _O, _C, 1, "cricket"),
    _i("jersey", _O, _R, 4),
    _i("nehru_jacket", _O, _R, 4),
    _i("knit_sweater", _O, _R, 4, "sardi"),
    _i("raincoat", _O, _R, 3, "monsoon"),
    _i("shawl", _O, _E, 4),
    _i("dupatta", _O, _E, 4),
    _i("sherwani", _O, _E, 3, "shaadi"),
    _i("lungi_banian", _O, _L, 3),
    _i("champions_shaal", _O, _H, 1),
    _i("safa", _HW, _E, 3, "shaadi"),
    _i("cricket_cap", _HW, _C, 3, "cricket"),
    _i("umbrella_hat", _HW, _R, 3, "monsoon"),
    _i("monkey_cap", _HW, _R, 3, "sardi"),
    _i("shades", _HW, _R, 2),
    _i("headphones", _HW, _C, 3),
    _i("gold_chain", _N, _E, 1, "shaadi"),
    _i("whistle", _N, _C, 1, "cricket"),
    _i("muffler", _N, _R, 4, "monsoon", "sardi"),
    _i("id_lanyard", _N, _C, 3, "office"),
    _i("marigold_mala", _N, _R, 1),
    _i("bow_tie", _N, _C, 4),
    _i("marigold_frame", _F, _R, 1, "shaadi"),
    _i("stumps_frame", _F, _C, 1, "cricket"),
    _i("raindrop_frame", _F, _R, 1, "monsoon"),
    _i("grid_frame", _F, _C, 1, "office"),
    _i("frost_frame", _F, _R, 1, "sardi"),
    _i("gold_ring", _F, _E, 1),
    _i("neon_ring", _F, _R, 3),
    _i("smoke", _A, _C, 2),
    _i("sunburst", _A, _R, 2),
    _i("diya_glow", _A, _R, 1),
    _i("starfield", _A, _E, 1),
    _i("pehle_waale", _B, _H, 1),
    _i("chai_cup", _B, _C, 1),
    _i("spade_pin", _B, _C, 1),
    _i("lucky_seven", _B, _R, 1),
    _i("last_bench", _T, _C, 1),
    _i("chai_pe_charcha", _T, _C, 1),
    _i("pakka_player", _T, _R, 1),
    _i("card_shark", _T, _R, 1),
    _i("taash_ustaad", _T, _E, 1),
)
ITEMS: dict[str, Item] = {item.id: item for item in CATALOGUE}

# The fixture's numbers, never re-derived.
PRICES: dict[Rarity, int] = {_C: 100, _R: 300, _E: 750, _L: 1800}
VARIANT_PRICE = 50
"""Each colour variant beyond the one that comes with the item."""

LEVEL_GIFTS: dict[int, tuple[str, int]] = {
    2: ("kurta", 0),
    3: ("cricket_cap", 0),
    4: ("bow_tie", 0),
    5: ("neon_ring", 0),
    6: ("starfield", 0),
}
"""What reaching each level gives (derived on read, never stored)."""

CHAMPIONS_SHAAL = "champions_shaal"
"""Owned by anyone with a `season_champion` row in any game."""
PEHLE_WAALE = "pehle_waale"
"""Owned by any player with a finished game before `PEHLE_WAALE_BEFORE`."""
PEHLE_WAALE_BEFORE = date(2026, 11, 25)
"""Midnight APP_TZ at the start of this day."""

JERSEY = "jersey"
JERSEY_NUMBER_MAX = 99

WEEKLY_SIZE = 6
WEEK_START_DAY_OFFSET = timedelta(hours=6)
"""The Bazaar's week starts Monday 06:00 APP_TZ."""
LEGENDARY_EVERY_WEEKS = 3
"""A legendary is in stock when the ISO week number is a multiple of this."""

Pair = tuple[str, int]


@dataclass(frozen=True)
class Owned:
    """An owned (item, variant) pair and how it came; `at` is null for derived ones."""

    item_id: str
    variant: int
    source: Source
    at: int | None


@dataclass(frozen=True)
class OwnershipFacts:
    """What a player's ownership is derived from."""

    bought: tuple[tuple[str, int, int], ...]
    """(item, variant, epoch ms) of each purchase."""
    gifts: tuple[tuple[str, int, int], ...]
    """(item, variant, epoch ms sent) of each gift received."""
    lifetime: int
    champion: bool
    early: bool


def pehle_waale_cutoff_ms(tz: ZoneInfo) -> int:
    """Epoch ms before which a finished game makes its players `pehle_waale`."""
    return to_ms(datetime.combine(PEHLE_WAALE_BEFORE, time.min, tz))


def level_gifts(level: int) -> list[Pair]:
    """Every level gift a level has reached, lowest level first."""
    return [LEVEL_GIFTS[n] for n in sorted(LEVEL_GIFTS) if n <= level]


def owned_items(facts: OwnershipFacts, level: int) -> list[Owned]:
    """Every owned pair once, with its earliest source: bought and gifts first (oldest first), then
    level gifts and honours. Variant 0 comes with the item, so any variant brings it too."""
    stored = [Owned(i, v, Source.BOUGHT, at) for i, v, at in facts.bought]
    stored += [Owned(i, v, Source.GIFT, at) for i, v, at in facts.gifts]
    stored += [Owned(o.item_id, 0, o.source, o.at) for o in list(stored) if o.variant > 0]
    stored.sort(key=lambda o: (o.at or 0, o.item_id, o.variant))
    derived = [Owned(i, v, Source.LEVEL, None) for i, v in level_gifts(level)]
    if facts.champion:
        derived.append(Owned(CHAMPIONS_SHAAL, 0, Source.HONOUR, None))
    if facts.early:
        derived.append(Owned(PEHLE_WAALE, 0, Source.HONOUR, None))
    out: dict[Pair, Owned] = {}
    for owned in stored + derived:
        out.setdefault((owned.item_id, owned.variant), owned)
    return list(out.values())


def owned_pairs(owned: Iterable[Owned]) -> set[Pair]:
    """The (item, variant) pairs of some owned items."""
    return {(o.item_id, o.variant) for o in owned}


def variants_owned(pairs: Iterable[Pair], item_id: str) -> list[int]:
    """The variants of one item among some pairs, ascending."""
    return sorted(v for i, v in pairs if i == item_id)


def base_price(item: Item) -> int:
    """An item's price with the variant it comes with; honour items have none."""
    if not item.sellable:
        raise ValueError(f"{item.id} is not for sale")
    return PRICES[item.rarity]


def price_of(item: Item, variant: int, *, owns_item: bool) -> int:
    """What a pair costs a player (bought, or given to them): a new item costs its price, plus
    `VARIANT_PRICE` for a variant other than 0 (which comes with it); a further variant of an owned
    item costs `VARIANT_PRICE`. The app's `priceOf`."""
    if owns_item:
        return VARIANT_PRICE
    return base_price(item) + (VARIANT_PRICE if variant > 0 else 0)


def valid_variant(item: Item, variant: int) -> bool:
    """Whether an item comes in this variant."""
    return 0 <= variant < item.variants


# --- Looks -------------------------------------------------------------------------------------


def look_problem(look: Mapping[str, Mapping[str, Any]], owned: set[Pair] | None) -> str | None:
    """The first slot of a look that's wrong (unknown item, other slot, bad variant or number, not
    owned when `owned` is given), or None."""
    for slot in Slot:
        worn = look.get(slot.value)
        if worn is not None and not _worn_ok(slot, worn, owned):
            return slot.value
    return None


def _worn_ok(slot: Slot, worn: Mapping[str, Any], owned: set[Pair] | None) -> bool:
    item = ITEMS.get(str(worn.get("id")))
    variant = worn.get("v")
    if item is None or item.slot != slot or not isinstance(variant, int):
        return False
    if not valid_variant(item, variant):
        return False
    number = worn.get("number")
    if number is not None and not _jersey_number(item, number):
        return False
    return owned is None or (item.id, variant) in owned


def _jersey_number(item: Item, number: object) -> bool:
    return item.id == JERSEY and isinstance(number, int) and 0 <= number <= JERSEY_NUMBER_MAX


def owned_look(look: Mapping[str, Mapping[str, Any]], owned: set[Pair]) -> dict[str, Any]:
    """A look with every slot that isn't a valid, owned pair dropped (a saved look being worn, or
    an item that stopped being owned)."""
    return {
        slot.value: dict(look[slot.value])
        for slot in Slot
        if look.get(slot.value) is not None and _worn_ok(slot, look[slot.value], owned)
    }


# --- The weekly stock ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Week:
    """A Bazaar week: its key (the ISO week of its Monday) and when it starts and ends."""

    key: str
    number: int
    starts_at: int
    refreshes_at: int


def week_of(now_ms: int, tz: ZoneInfo) -> Week:
    """The Bazaar week an instant falls in (weeks start Monday 06:00 in `tz`)."""
    local = from_ms(now_ms).astimezone(tz) - WEEK_START_DAY_OFFSET
    monday = local.date() - timedelta(days=local.weekday())
    year, number, _ = monday.isocalendar()
    start = datetime.combine(monday, time.min, tz) + WEEK_START_DAY_OFFSET
    end = datetime.combine(monday + timedelta(days=7), time.min, tz) + WEEK_START_DAY_OFFSET
    return Week(f"{year}-W{number:02d}", number, to_ms(start), to_ms(end))


def _draw(key: str, n: int) -> int:
    """The n-th number of a week's stream: sha256 of the key and a counter, as an integer."""
    return int.from_bytes(hashlib.sha256(f"{key}:{n}".encode()).digest(), "big")


def _sellable(rarity: Rarity) -> list[Item]:
    return [item for item in CATALOGUE if item.rarity == rarity]


def weekly_stock(week: Week) -> list[Item]:
    """The week's six items, the same for everyone: a legendary every third ISO week, one epic,
    rares for the rest; no duplicates. Each pick takes the next draw modulo what's left."""
    plan = [Rarity.LEGENDARY] if week.number % LEGENDARY_EVERY_WEEKS == 0 else []
    plan.append(Rarity.EPIC)
    plan += [Rarity.RARE] * (WEEKLY_SIZE - len(plan))
    pools = {rarity: _sellable(rarity) for rarity in set(plan)}
    stock: list[Item] = []
    for n, rarity in enumerate(plan):
        pool = pools[rarity]
        stock.append(pool.pop(_draw(week.key, n) % len(pool)))
    return stock


def always_stock() -> list[Item]:
    """Hamesha: every sellable common item, catalogue order."""
    return _sellable(Rarity.COMMON)


def in_stock(item: Item, week: Week) -> bool:
    """Whether an item is for sale this week to someone who doesn't own it yet."""
    return item.rarity == Rarity.COMMON or item in weekly_stock(week)
