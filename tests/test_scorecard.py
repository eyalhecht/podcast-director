from backend.director import scorecard


def rows(**stats):
    return {r["label"]: r for r in scorecard({"stats": stats})["scorecard"]}


def test_measures_are_judged_against_the_good_shows():
    card = rows(melody=4.5, switches=0.5, transitions=["announced", "natural", "bridge"],
                talk={1: 0.8, 2: 0.2}, marks=3, open_marks=1, tips={"followup": 4})
    assert card["Voice melody"]["verdict"] == "good"
    assert card["Back-and-forth"]["verdict"] == "work"
    assert card["Topic changes"]["value"] == "33% just announced"
    assert card["Talk balance"] == {**card["Talk balance"], "value": "80% / 20%", "verdict": "ok"}
    assert card["Loose ends"]["value"] == "1 still open of 3"
    assert card["Missed follow-ups"]["verdict"] == "work"
    assert card["Cut-offs"]["verdict"] == "good"


def test_measures_without_data_are_left_out():
    assert set(rows(melody=None)) == {"Loose ends", "Missed follow-ups", "Cut-offs", "Flat-voice nudges"}
