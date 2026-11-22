"""Incomplete or uninterpretable sessions cannot become comparison evidence."""

import pytest

from cloud_pcb.general_solver.freerouting_comparison import completed_session


SCORE = "Auto-routing stage completed: final score: 950.0 (3 unrouted and 2 violations)"
COMPLETE = "Job '1234' finished with state: COMPLETED (elapsed: 1 second)"
SESSION = """
(session "board" (routes
  (resolution um 10)
  (network_out
    (net logic (wire (path F.Cu 2000 20000 -20000 30000 -20000)))
  )))
"""


@pytest.mark.parametrize("separator", [" and ", ", "])
def test_completed_session_reports_engine_counts_and_parses_copper(tmp_path, separator):
    path = tmp_path / "actual.ses"
    path.write_text(SESSION)
    parsed, counts = completed_session(path, 0, SCORE.replace(" and ", separator) + "\n" + COMPLETE, {"logic"})
    assert counts == {"engine_unrouted_connections": 3, "engine_clearance_violations": 2}
    assert parsed["segments"][0]["width_mm"] == .2
    assert parsed["total_wirelength_mm"] == 1


@pytest.mark.parametrize(
    ("rc", "log", "message"),
    [
        (-1, SCORE + "\n" + COMPLETE, "process did not finish"),
        (0, SCORE, "missing Freerouting COMPLETED"),
        (0, COMPLETE, "missing Freerouting final routing score"),
        (0, SCORE + "\n" + COMPLETE + "\nNetwork.insert_component: component package not found", "discarded"),
    ],
)
def test_a_file_alone_does_not_establish_valid_comparison(tmp_path, rc, log, message):
    path = tmp_path / "stale.ses"
    path.write_text(SESSION)
    with pytest.raises(ValueError, match=message):
        completed_session(path, rc, log, {"logic"})


def test_session_rejects_unknown_net_and_layer(tmp_path):
    path = tmp_path / "actual.ses"
    path.write_text(SESSION)
    with pytest.raises(ValueError, match="foreign nets"):
        completed_session(path, 0, SCORE + "\n" + COMPLETE, {"other"})
    path.write_text(SESSION.replace("F.Cu", "Not.Cu"))
    with pytest.raises(ValueError, match="unknown copper layer"):
        completed_session(path, 0, SCORE + "\n" + COMPLETE, {"logic"})
