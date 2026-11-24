from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.domain.time import to_ms
from app.domain.wardrobe import (
    CATALOGUE,
    ITEMS,
    LEVEL_GIFTS,
    PRICES,
    VARIANT_PRICE,
    OwnershipFacts,
    Rarity,
    Source,
    always_stock,
    in_stock,
    look_problem,
    owned_items,
    owned_look,
    owned_pairs,
    pehle_waale_cutoff_ms,
    price_of,
    week_of,
    weekly_stock,
)
from tests.domain.golden import load

TZ = ZoneInfo("Asia/Kolkata")


def _at(text: str) -> int:
    return to_ms(datetime.fromisoformat(text).replace(tzinfo=TZ))


def _facts(**overrides: object) -> OwnershipFacts:
    base: dict[str, object] = {
        "bought": (),
        "gifts": (),
        "lifetime": 0,
        "champion": False,
        "early": False,
    }
    return OwnershipFacts(**(base | overrides))  # type: ignore[arg-type]


# --- Catalogue parity -----------------------------------------------------------------------------


def test_catalogue_matches_the_app_fixture() -> None:
    fixture = load("wardrobe-catalog")

    expected = [
        (
            i["id"],
            i["slot"],
            i["rarity"],
            i["variants"],
            tuple(i["set"].split("+")) if i["set"] else (),
            i["sellable"],
        )
        for i in fixture["items"]
    ]
    ours = [(i.id, i.slot.value, i.rarity.value, i.variants, i.sets, i.sellable) for i in CATALOGUE]
    assert ours == expected


def test_prices_and_level_gifts_match_the_app_fixture() -> None:
    fixture = load("wardrobe-catalog")

    assert {r.value: p for r, p in PRICES.items()} == fixture["prices"]
    assert fixture["variantPrice"] == VARIANT_PRICE
    assert {str(n): {"id": i, "v": v} for n, (i, v) in LEVEL_GIFTS.items()} == fixture["levelGifts"]


def test_honour_items_are_not_sellable() -> None:
    honours = {i.id for i in CATALOGUE if not i.sellable}

    assert honours == {"champions_shaal", "pehle_waale"}


# --- Prices ---------------------------------------------------------------------------------------


def test_a_new_item_costs_its_rarity_price_and_a_further_variant_adds_fifty() -> None:
    sherwani = ITEMS["sherwani"]

    assert price_of(sherwani, 0, owns_item=False) == 750
    assert price_of(sherwani, 2, owns_item=False) == 800
    assert price_of(sherwani, 2, owns_item=True) == 50


def test_a_pair_for_someone_without_the_item_costs_its_price_with_the_variant() -> None:
    assert price_of(ITEMS["kurta"], 0, owns_item=False) == 100
    assert price_of(ITEMS["lungi_banian"], 1, owns_item=False) == 1850


def test_an_honour_item_has_no_price() -> None:
    with pytest.raises(ValueError, match="not for sale"):
        price_of(ITEMS["champions_shaal"], 0, owns_item=False)


# --- Ownership ------------------------------------------------------------------------------------


def test_owned_items_combine_purchases_gifts_level_gifts_and_honours() -> None:
    facts = _facts(
        bought=(("sherwani", 1, 2_000),),
        gifts=(("shades", 0, 1_000),),
        lifetime=4_000,
        champion=True,
        early=True,
    )

    owned = owned_items(facts, level=4)

    assert [(o.item_id, o.variant, o.source, o.at) for o in owned] == [
        ("shades", 0, Source.GIFT, 1_000),
        ("sherwani", 0, Source.BOUGHT, 2_000),
        ("sherwani", 1, Source.BOUGHT, 2_000),
        ("kurta", 0, Source.LEVEL, None),
        ("cricket_cap", 0, Source.LEVEL, None),
        ("bow_tie", 0, Source.LEVEL, None),
        ("champions_shaal", 0, Source.HONOUR, None),
        ("pehle_waale", 0, Source.HONOUR, None),
    ]


def test_a_pair_owned_twice_is_listed_once_with_its_first_source() -> None:
    facts = _facts(bought=(("kurta", 0, 5_000),))

    owned = owned_items(facts, level=2)

    assert [(o.item_id, o.source) for o in owned] == [("kurta", Source.BOUGHT)]


def test_a_variant_brings_variant_zero_with_it() -> None:
    facts = _facts(bought=(("sherwani", 2, 3_000),), gifts=(("shades", 1, 4_000),))

    owned = owned_items(facts, level=1)

    assert [(o.item_id, o.variant, o.source, o.at) for o in owned] == [
        ("sherwani", 0, Source.BOUGHT, 3_000),
        ("sherwani", 2, Source.BOUGHT, 3_000),
        ("shades", 0, Source.GIFT, 4_000),
        ("shades", 1, Source.GIFT, 4_000),
    ]


def test_level_one_owns_nothing_derived() -> None:
    assert owned_items(_facts(), level=1) == []


def test_level_six_owns_every_level_gift() -> None:
    pairs = owned_pairs(owned_items(_facts(), level=6))

    assert pairs == set(LEVEL_GIFTS.values())


def test_pehle_waale_cutoff_is_midnight_in_the_app_timezone() -> None:
    assert pehle_waale_cutoff_ms(TZ) == _at("2026-11-25T00:00")


# --- Looks ----------------------------------------------------------------------------------------

OWNED = {("jersey", 2), ("safa", 0), ("kurta", 0)}


def test_a_look_of_owned_items_has_no_problem() -> None:
    look = {"outfit": {"id": "jersey", "v": 2, "number": 7}, "headwear": {"id": "safa", "v": 0}}

    assert look_problem(look, OWNED) is None


@pytest.mark.parametrize(
    ("look", "slot"),
    [
        ({"outfit": {"id": "nope", "v": 0}}, "outfit"),
        ({"headwear": {"id": "kurta", "v": 0}}, "headwear"),
        ({"outfit": {"id": "kurta", "v": 6}}, "outfit"),
        ({"outfit": {"id": "kurta", "v": 0, "number": 3}}, "outfit"),
        ({"outfit": {"id": "jersey", "v": 2, "number": 100}}, "outfit"),
        ({"outfit": {"id": "jersey", "v": 1}}, "outfit"),
        ({"headwear": {"id": "safa", "v": 0}, "frame": {"id": "gold_ring", "v": 0}}, "frame"),
    ],
)
def test_a_wrong_slot_is_named(look: dict[str, dict[str, object]], slot: str) -> None:
    assert look_problem(look, OWNED) == slot


def test_a_saved_look_needs_valid_items_but_not_owned_ones() -> None:
    assert look_problem({"frame": {"id": "gold_ring", "v": 0}}, None) is None
    assert look_problem({"frame": {"id": "gold_ring", "v": 1}}, None) == "frame"


def test_wearing_a_look_drops_what_is_not_owned() -> None:
    look = {"outfit": {"id": "jersey", "v": 2, "number": 9}, "frame": {"id": "gold_ring", "v": 0}}

    assert owned_look(look, OWNED) == {"outfit": {"id": "jersey", "v": 2, "number": 9}}


# --- The weekly stock -----------------------------------------------------------------------------


def test_the_week_starts_monday_at_six_in_the_app_timezone() -> None:
    before = week_of(_at("2026-11-30T05:59"), TZ)
    after = week_of(_at("2026-11-30T06:00"), TZ)

    assert before.key == "2026-W48"
    assert before.starts_at == _at("2026-11-23T06:00")
    assert before.refreshes_at == _at("2026-11-30T06:00")
    assert after.key == "2026-W49"


def test_the_week_key_is_the_iso_week_of_its_monday() -> None:
    assert week_of(_at("2026-12-28T10:00"), TZ).key == "2026-W53"
    assert week_of(_at("2027-01-04T06:00"), TZ).key == "2027-W01"
    # Before Monday 06:00 is still the previous year's last week.
    assert week_of(_at("2027-01-04T05:00"), TZ).key == "2026-W53"


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (
            "2026-11-24T12:00",
            [
                "lungi_banian",
                "sherwani",
                "pakka_player",
                "shades",
                "marigold_mala",
                "marigold_frame",
            ],
        ),
        (
            "2026-11-30T06:00",
            ["shawl", "muffler", "raindrop_frame", "marigold_mala", "card_shark", "lucky_seven"],
        ),
        (
            "2027-01-04T06:00",
            ["starfield", "sunburst", "marigold_frame", "frost_frame", "muffler", "umbrella_hat"],
        ),
    ],
)
def test_the_weekly_stock_is_fixed_per_week_key(moment: str, expected: list[str]) -> None:
    assert [i.id for i in weekly_stock(week_of(_at(moment), TZ))] == expected


@pytest.mark.parametrize("week", range(1, 54))
def test_every_weekly_stock_follows_the_rarity_rules(week: int) -> None:
    moment = datetime.fromisocalendar(2026, week, 2).replace(hour=12, tzinfo=TZ)
    stock = weekly_stock(week_of(to_ms(moment), TZ))
    rarities = [i.rarity for i in stock]

    assert len(stock) == 6
    assert len({i.id for i in stock}) == 6
    assert rarities.count(Rarity.LEGENDARY) == (1 if week % 3 == 0 else 0)
    assert rarities.count(Rarity.EPIC) >= 1
    assert set(rarities) <= {Rarity.RARE, Rarity.EPIC, Rarity.LEGENDARY}


def test_always_stock_is_every_common_item() -> None:
    assert {i.id for i in always_stock()} == {i.id for i in CATALOGUE if i.rarity == Rarity.COMMON}


def test_a_common_item_is_always_in_stock_and_a_rare_only_in_its_week() -> None:
    week = week_of(_at("2026-11-24T12:00"), TZ)

    assert in_stock(ITEMS["bow_tie"], week)
    assert in_stock(ITEMS["shades"], week)
    assert not in_stock(ITEMS["jersey"], week)
