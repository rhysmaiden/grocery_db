"""Quantity/unit parsing and normalisation.

Ported from hotprices-au (github.com/Javex/hotprices-au, MIT) with light
changes: parse failures return (None, None) instead of raising, because a
price observation is still worth keeping when the pack size is unparseable.
"""

import re

GLOBAL_UNITS = {
    "ea": {"unit": "ea", "factor": 1},
    "each": {"unit": "ea", "factor": 1},
    "pack": {"unit": "ea", "factor": 1},
    "pk": {"unit": "ea", "factor": 1},
    "pac": {"unit": "ea", "factor": 1},
    "bunch": {"unit": "ea", "factor": 1},
    "sheets": {"unit": "ea", "factor": 1},
    "sachets": {"unit": "ea", "factor": 1},
    "capsules": {"unit": "ea", "factor": 1},
    "ss": {"unit": "ea", "factor": 1},
    "set": {"unit": "ea", "factor": 1},
    "pair": {"unit": "ea", "factor": 1},
    "pairs": {"unit": "ea", "factor": 1},
    "piece": {"unit": "ea", "factor": 1},
    "tablets": {"unit": "ea", "factor": 1},
    "rolls": {"unit": "ea", "factor": 1},
    "dozen": {"unit": "ea", "factor": 12},
    "mg": {"unit": "g", "factor": 0.001},
    "g": {"unit": "g", "factor": 1},
    "kg": {"unit": "g", "factor": 1000},
    "ml": {"unit": "ml", "factor": 1},
    "l": {"unit": "ml", "factor": 1000},
    "m": {"unit": "cm", "factor": 100},
    "metre": {"unit": "cm", "factor": 100},
    "cm": {"unit": "cm", "factor": 1},
}

# 30 x 375ml (and 4x4x375mL)
_RE_MULTIPLE_FIRST = (
    r"^(?P<count>[0-9x]+)? ?x ?(?P<quantity>[0-9]+) ?(?P<unit>[a-z]+) ?(case|carton|pack)?$"
)
# 375ml x 30
_RE_MULTIPLE_LATER = (
    r"^(?P<quantity>[0-9]+)(?P<unit>[a-z]+) ?x ?(?P<count>[0-9]+)? ?(case|carton|pack)?$"
)
# 100g Pack
_RE_REGULAR_PACK = r"^(?P<quantity>[0-9\.]+)? ?(?P<unit>[a-z]+) ?(punnet|pack|each|set)?$"


def parse_str_unit(unit_str: str | None) -> tuple[float | None, str | None]:
    """Parse a size string like '500g', '30 x 375ml' into (quantity, unit).

    Returns (None, None) when unparseable.
    """
    if not unit_str:
        return None, None
    unit_str = unit_str.lower().strip()
    match unit_str:
        case "whole each":
            return 1, "ea"
        case "half each":
            return 0.5, "ea"
        case "each":
            return 1, "ea"
        case "355ml xcase":
            return 8520, "ml"

    if unit_str.startswith("per "):
        unit_str = unit_str[4:]

    for regex in (_RE_MULTIPLE_FIRST, _RE_MULTIPLE_LATER, _RE_REGULAR_PACK):
        matched = re.match(regex, unit_str)
        if not matched:
            continue
        try:
            count_group = matched.group("count")
        except IndexError:
            count_group = None
        count = 1.0
        if count_group:
            for count_elem in count_group.split("x"):
                count *= float(count_elem)
        try:
            quantity = float(matched.group("quantity"))
        except TypeError:
            quantity = 1.0
        unit = matched.group("unit")
        if unit not in GLOBAL_UNITS:
            continue
        return quantity * count, unit
    return None, None


def normalise(quantity: float | None, unit: str | None) -> tuple[float | None, str | None]:
    """Convert to base units: g, ml, cm or ea."""
    if quantity is None or unit is None:
        return None, None
    conv = GLOBAL_UNITS.get(unit.lower())
    if conv is None:
        return None, None
    return conv["factor"] * quantity, conv["unit"]
