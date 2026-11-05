from tvguide.categories import category, derived_category, label


def test_derived_category():
    assert derived_category("movie", "Drama") == "film"
    assert derived_category("tvMovie", None) == "film"
    assert derived_category("tvSeries", "Documentary") == "format"
    assert derived_category("tvSeries", "News,Talk-Show") == "format"
    assert derived_category("tvSeries", "Game-Show") == "format"
    assert derived_category("tvSeries", "Drama,Family,Musical") == "serie"
    assert derived_category("tvSeries", None) == "serie"


def test_marks_override():
    assert category("tvSeries", "Documentary", "keep") == "serie"
    assert category("tvMovie", "Documentary", "keep") == "film"
    assert category("tvSeries", "Drama", "format") == "format"
    assert category("tvSeries", "Drama", None) == "serie"


def test_label():
    assert label("film") == "Film"
    assert label("serie") == "Serie"
    assert label("format") == "Format"
    assert label(None) == "Serie"
