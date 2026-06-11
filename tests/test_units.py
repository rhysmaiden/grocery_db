from grocery_db import units


def test_simple_grams():
    assert units.parse_str_unit("500g") == (500, "g")


def test_multipack():
    assert units.parse_str_unit("30 x 375ml") == (30 * 375, "ml")


def test_multipack_later():
    assert units.parse_str_unit("375ml x 30") == (30 * 375, "ml")


def test_each():
    assert units.parse_str_unit("each") == (1, "ea")


def test_unparseable_returns_none():
    assert units.parse_str_unit("a dozen long stem roses!!") == (None, None)
    assert units.parse_str_unit(None) == (None, None)


def test_normalise_kg_to_g():
    assert units.normalise(1.5, "kg") == (1500, "g")


def test_normalise_litre_to_ml():
    assert units.normalise(2, "l") == (2000, "ml")


def test_normalise_none_passthrough():
    assert units.normalise(None, None) == (None, None)
