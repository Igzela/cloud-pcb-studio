"""Exercise the recovery search on independent synthetic geometry."""

import pytest
from shapely.affinity import rotate, translate
from shapely.geometry import Point, box

from cloud_pcb.general_solver.placement_recovery import PackingExhausted, pack_movable


CLEARANCE = 0.28


def _state(board, sizes, *, fixed=(), committed_margin=None):
    """Synthetic callbacks deliberately use independent Shapely transforms."""
    committed_margin = committed_margin or {}
    inside = board.buffer(-1)
    fixed = tuple(fixed)
    placed = {}
    solids = {}

    def outline(ref, x, y, angle, margin):
        poly = translate(rotate(box(*sizes[ref]), -angle, origin=(0, 0)), xoff=x, yoff=y)
        return box(*poly.bounds).buffer(margin, join_style="mitre")

    def valid(ref, x, y, angle):
        candidate = outline(ref, x, y, angle, CLEARANCE)
        return inside.contains(candidate) and all(
            not candidate.intersects(g) for g in (*fixed, *solids.values())
        )

    def commit(ref, x, y, angle):
        assert valid(ref, x, y, angle)
        assert all(abs(v * 1000 - round(v * 1000)) < 1e-8 for v in (x, y))
        placed[ref] = (x, y, angle)
        solids[ref] = outline(ref, x, y, angle, committed_margin.get(ref, CLEARANCE))

    def support(_):
        return [g.bounds for g in (*fixed, *solids.values())]

    return placed, solids, valid, commit, support


@pytest.mark.parametrize("mode", ("area", "long-side"))
def test_fragmentation_recovery_places_long_footprint_first(mode):
    # Either edge can accommodate the header; the late header cannot fit after
    # the ordinary centre-oriented placement consumes both strips.
    bboxes = {"small": (-2, -2, 2, 2), "header": (-7, -1, 7, 1)}
    targets = {"small": (9, 5.4), "header": (9, 2.4)}
    board = box(0, 0, 18, 12)
    previous = _state(board, bboxes, committed_margin={"small": 1.1, "header": .55})
    _, _, valid, commit, support = previous
    commit("small", 9, 5.4, 0)
    with pytest.raises(PackingExhausted, match="header"):
        pack_movable(["header"], bboxes, targets, valid, commit, (18, 12), support_bounds=support)

    for refs in (["small", "header"], ["header", "small"]):
        recovered, _, valid, commit, support = _state(
            board, bboxes, committed_margin={"small": 1.1, "header": .55}
        )
        pack_movable(refs, bboxes, targets, valid, commit, (18, 12), mode=mode, support_bounds=support)
        assert list(recovered) == ["header", "small"]
        if refs[0] == "small":
            expected = recovered
        else:
            assert recovered == expected


def test_recovers_a_legal_pose_beyond_the_old_search_radius():
    bboxes = {"module": (-2, -2, 2, 2)}
    board = box(0, 0, 128, 10)
    placed, _, valid, commit, support = _state(board, bboxes, fixed=(box(0, 0, 100, 10),))
    pack_movable(
        ["module"], bboxes, {"module": (3.3, 5)}, valid, commit,
        (128, 10), support_bounds=support,
    )
    assert 102.28 < placed["module"][0] < 102.29
    assert placed["module"][0] - 3.3 > 45


def test_impossible_fixed_keepout_is_neither_moved_nor_committed():
    bboxes = {"module": (-2, -2, 2, 2)}
    board = box(0, 0, 18, 12)
    fixed = box(0, 0, 18, 12)
    placed, _, valid, commit, support = _state(board, bboxes, fixed=(fixed,))
    with pytest.raises(PackingExhausted) as caught:
        pack_movable(
            ["module"], bboxes, {"module": (9, 6)}, valid, commit,
            (18, 12), support_bounds=support, probe_budget=61,
        )
    assert caught.value.ref == "module"
    assert caught.value.probes == 61
    assert placed == {}
    assert fixed.equals(box(0, 0, 18, 12))


def test_candidate_order_obeys_actual_outline_and_round_keepouts():
    board = box(0, 0, 18, 12).difference(box(0, 0, 8, 5))
    hole = Point(11, 8).buffer(1.2)
    bboxes = {"module": (-2, -.5, 2, .5)}
    placed, solids, valid, commit, support = _state(board, bboxes, fixed=(hole,))
    pack_movable(
        ["module"], bboxes, {"module": (8, 5)}, valid, commit,
        (18, 12), support_bounds=support,
    )
    assert (placed["module"][0], placed["module"][1]) != (8, 5)
    assert board.buffer(-1).contains(solids["module"])
    assert not hole.intersects(solids["module"])


def test_single_probe_budget_allows_a_legal_target_and_live_target_update():
    bboxes = {"first": (-1, -1, 1, 1), "second": (-1, -1, 1, 1)}
    board = box(0, 0, 18, 12)
    placed, _, valid, commit, support = _state(board, bboxes)

    def target(ref):
        if ref == "first":
            return 4.234, 4.567
        x, y, _ = placed["first"]
        return x + 3.5, y

    pack_movable(
        list(bboxes), bboxes, target, valid, commit, (18, 12),
        support_bounds=support, probe_budget=1,
    )
    assert placed == {"first": (4.234, 4.567, 0), "second": (7.734, 4.567, 0)}


def _fragmented_placement_spec():
    def pad(number, x, net, kind="smd"):
        return {
            "num": str(number), "net": net, "x_rel_mm": x, "y_rel_mm": 0,
            "w_mm": .6, "h_mm": .6, "type": kind, "shape": "rect",
            "layers": ["*.Cu"] if kind == "thru_hole" else ["F.Cu"],
        }

    return {
        "project_name": "synthetic_fragmentation",
        "board_outline_mm": {"width": 18, "height": 12},
        "components": [
            {"refdes": "U1", "value": "Logic", "footprint_name": "Package",
             "body_bbox_mm": [-2, -2, 2, 2],
             "pads": [pad(1, -1, "SIG"), pad(2, 0, "A"), pad(3, 1, "B")]},
            {"refdes": "J1", "value": "Header", "footprint_name": "Connector_PinHeader",
             "body_bbox_mm": [-7, -1, 7, 1],
             "pads": [pad(i, x, "SIG", "thru_hole") for i, x in enumerate((-6, 0, 6), 1)]},
        ],
        "nets": {"SIG": [["U1", "1"], ["J1", "1"], ["J1", "2"], ["J1", "3"]],
                 "A": [["U1", "2"]], "B": [["U1", "3"]]},
        "design_requirements": {},
    }


def test_full_placer_recovers_the_compact_synthetic_layout_without_overlap():
    from cloud_pcb.general_solver.run_blind import PlacementExhausted, _classify_components_and_place

    with pytest.raises(PlacementExhausted):
        _classify_components_and_place(_fragmented_placement_spec())
    for compact in (False, True):
        spec = _fragmented_placement_spec()
        placed, metadata = _classify_components_and_place(spec, packing_order="area", packing_compact=compact)
        assert metadata["total_placed"] == 2
        assert metadata["placement_recovery"]["compact"] == compact
        footprints = []
        for component in spec["components"]:
            x, y, angle = placed[component["refdes"]]
            outline = rotate(box(*component["body_bbox_mm"]), -angle, origin=(0, 0))
            footprints.append(translate(outline, xoff=x, yoff=y))
        assert not footprints[0].intersects(footprints[1])
        edge = .5 if compact else 1.
        assert all(box(edge, edge, 18 - edge, 12 - edge).contains(outline) for outline in footprints)


def test_dense_obstacles_near_target_do_not_starve_full_board_coverage():
    """30+ obstacles clustered near target must not starve probe budget before reaching far side."""
    from cloud_pcb.general_solver.placement_recovery import ANGLES, _angle_candidates
    import heapq
    from itertools import islice

    board_w, board_h = 100.0, 60.0
    board = box(0, 0, board_w, board_h)
    # 36 distinct obstacles filling x in [0, 74] across the full height so no slot fits there.
    obstacles = []
    for row in range(6):
        for col in range(6):
            x0 = 1.0 + col * 12.13
            y0 = 0.5 + row * 9.87
            obstacles.append(box(x0, y0, x0 + 11.9, y0 + 9.7))
    # Also wall off [0, 74] solid while keeping all 36 distinct obstacle boxes in support_bounds
    # so the generator receives 72 X and 72 Y obstacle edges clustered near target (5, 30).
    wall = box(0, 0, 74.0, board_h)
    bboxes = {"part": (-2.0, -2.0, 2.0, 2.0)}
    placed, solids, valid, commit, _ = _state(board, bboxes, fixed=(wall, *obstacles))

    def support(_):
        return [g.bounds for g in obstacles]

    # Verify the candidate stream itself sweeps across >90% of board width & height within 40k probes.
    occupied = tuple(g.bounds for g in obstacles)
    ranked = heapq.merge(*(
        _angle_candidates(bboxes["part"], (5.0, 30.0), (board_w, board_h), angle, occupied, CLEARANCE, 1.0)
        for angle in ANGLES
    ))
    sampled = list(islice(ranked, 40000))
    xs = [x / 1000.0 for _, x, _, _ in sampled]
    ys = [y / 1000.0 for _, _, y, _ in sampled]
    assert max(xs) > 90.0
    assert min(ys) < 5.0 and max(ys) > 55.0

    # Verify pack_movable actually finds the legal open region on the far right (x > 76).
    pack_movable(
        ["part"], bboxes, {"part": (5.0, 30.0)}, valid, commit,
        (board_w, board_h), support_bounds=support, probe_budget=40000,
    )
    assert placed["part"][0] > 76.0
    assert board.buffer(-1.0).contains(solids["part"])
    assert not wall.intersects(solids["part"])


def test_tiered_angle_candidates_are_monotonic_and_deduplicated():
    from cloud_pcb.general_solver.placement_recovery import _angle_candidates

    occupied = tuple(
        (2.0 + i * 1.13, 2.0 + (i % 5) * 2.17, 3.0 + i * 1.13, 3.0 + (i % 5) * 2.17)
        for i in range(12)
    )
    stream = list(_angle_candidates(
        (-0.5, -0.5, 0.5, 0.5), (10.0, 10.0), (24.0, 16.0), 0, occupied, 0.28, 1.0,
    ))
    ranks = [entry[0] for entry in stream]
    coords = [(entry[1], entry[2], entry[3]) for entry in stream]
    assert ranks == sorted(ranks)
    assert len(coords) == len(set(coords))
    assert any(tier == 0 for tier, _ in ranks)

