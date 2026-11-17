from copy import deepcopy

import pytest

from app.errors import AppError
from app.weights import distribute


def test_exact_totals_for_all_supported_integer_splits():
    for count in range(1, 101):
        offers = [
            {"offer_id": i, "state": "active", "share": 0, "pinned": False} for i in range(count)
        ]
        distribute(offers)
        assert sum(o["share"] for o in offers) == 100
        assert max(o["share"] for o in offers) - min(o["share"] for o in offers) <= 1
        first = deepcopy(offers)
        distribute(offers)
        assert offers == first


def test_pin_stays_fixed_and_removed_receives_zero():
    offers = [
        {"state": "active", "share": 25, "pinned": True},
        {"state": "active", "share": 0, "pinned": False},
        {"state": "active", "share": 0, "pinned": False},
        {"state": "removed", "share": 50, "pinned": True},
    ]
    distribute(offers)
    assert [o["share"] for o in offers] == [25, 38, 37, 0]


@pytest.mark.parametrize(
    "offers",
    [
        [{"state": "active", "share": 50, "pinned": True}],
        [
            {"state": "active", "share": 100, "pinned": True},
            {"state": "active", "share": 0, "pinned": False},
        ],
        [
            {"state": "active", "share": 60, "pinned": True},
            {"state": "active", "share": 60, "pinned": True},
        ],
    ],
)
def test_impossible_pins_are_rejected(offers):
    with pytest.raises(AppError):
        distribute(offers)
