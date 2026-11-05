from tvguide.cleaning import candidates, clean_title
from tvguide.normalize import normalize


def test_folgenmarker_und_tags():
    assert clean_title("Barbara (Folge 1 von 10)")[0] == "Barbara"
    assert clean_title("Hubert und Staller (94)")[0] == "Hubert und Staller"
    assert clean_title("Tatort (S03/E12)")[0] == "Tatort"
    assert clean_title("Film (HD)")[0] == "Film"
    assert clean_title("Sendung - Teil 2")[0] == "Sendung"
    assert clean_title("Serie Folge 3")[0] == "Serie"


def test_jahr_in_klammern():
    titel, jahr, changed = clean_title("Zurück in die Zukunft (1985)")
    assert (titel, jahr) == ("Zurück in die Zukunft", 1985)


def test_candidates_zerlegt_praefix_und_suffix():
    c = candidates("Pfarrer Braun: Adel vernichtet")
    kinds = [k for _t, _n, k in c]
    assert kinds[0] == "full"
    assert "prefix1" in kinds
    assert "suffix" in kinds
    assert c[0][1] == normalize("Pfarrer Braun: Adel vernichtet")


def test_candidates_ohne_trenner_nur_full():
    assert [k for _t, _n, k in candidates("MacGyver")] == ["full"]
