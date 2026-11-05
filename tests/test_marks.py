import pytest

from tvguide.db import connect
from tvguide.marks import delete_mark, get_marks, recent, set_mark, undo_last


def test_set_get_undo(cfg):
    con = connect(cfg)
    assert get_marks(con) == {}
    set_mark(con, "tt1", "format")
    set_mark(con, "tt2", "keep")
    assert get_marks(con) == {"tt1": "format", "tt2": "keep"}
    assert undo_last(con) == "tt2"
    assert get_marks(con) == {"tt1": "format"}
    delete_mark(con, "tt1")
    assert get_marks(con) == {}
    assert undo_last(con) is None
    con.close()


def test_invalid_mark(cfg):
    con = connect(cfg)
    with pytest.raises(ValueError):
        set_mark(con, "tt1", "nope")
    con.close()


def test_recent(cfg):
    con = connect(cfg)
    set_mark(con, "tt0086659", "format")
    rows = recent(con)
    assert rows and rows[0]["tconst"] == "tt0086659" and rows[0]["mark"] == "format"
    con.close()
