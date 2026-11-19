"""Synthetic tests for holdout input isolation and evidence integrity."""

import hashlib
import json
from pathlib import Path

import pytest

from scripts.seal_holdout import extract


def synthetic_board(
    *, x: int = 24, y: int = 12, rotation: int = 0, routed: bool = True
) -> bytes:
    copper = """
        (segment (start 21 10) (end 28 10) (width 0.20) (layer "F.Cu") (net 1))
        (via (at 30 10) (size 0.60) (drill 0.30) (layers "F.Cu" "B.Cu") (net 1))
        (zone (net 1) (net_name "sig") (layer "In2.Cu")
            (polygon (pts (xy 20 8) (xy 36 8) (xy 36 18) (xy 20 18))))
    """ if routed else ""
    return f"""(kicad_pcb
      (layers (0 "F.Cu" signal) (1 "In1.Cu" power) (2 "In2.Cu" power) (31 "B.Cu" signal))
      (net 1 "sig")
      (gr_rect (start 20 8) (end 36 18) (layer "Edge.Cuts"))
      (gr_text "source-only annotations" (at 33 15) (layer "F.SilkS"))
      (footprint "Package_QFN:QFN-4" (at {x} {y} {rotation})
        (property "Reference" "U1") (property "Value" "Amplifier")
        (fp_rect (start -1.20 -0.90) (end 1.20 0.90) (layer "F.Fab"))
        (pad "1" smd rect (at 0.5 -0.25 {rotation+15})
          (size 0.30 0.60) (layers "F.Cu" "F.Paste" "F.Mask")
          (net 1 "sig") (pinfunction "OUT") (pintype "output")))
      (footprint "Connector_USB:USB_C" (at 20.5 12 90)
        (property "Reference" "J1") (property "Value" "USB-C connector")
        (pad "1" smd rect (at 1 0 90) (size 0.30 0.60)
          (layers "F.Cu" "F.Paste" "F.Mask") (net 1 "sig")))
      (footprint "MountingHole:MountingHole_2mm" (at 34 16 0)
        (property "Reference" "H1")
        (pad "" np_thru_hole circle (at 0 0) (size 2 2) (drill 2) (layers "*.Cu" "*.Mask")))
      {copper}
    )""".encode()


def test_extract_discards_instance_pose_copper_and_global_annotations():
    original = extract(synthetic_board(), 42)
    moved = extract(synthetic_board(x=30, y=15, rotation=90, routed=False), 42)
    assert original == moved

    components = {component['refdes']: component for component in original['components']}
    assert set(components) == {'J1', 'U1'}
    assert components['U1']['pads'][0]['angle_rel_deg'] == 15
    assert components['U1']['pads'][0]['pin_type'] == 'output'
    assert components['U1']['body_bbox_mm'] == [-1.2, -.9, 1.2, .9]
    assert original['nets']['sig'] == [['U1', '1'], ['J1', '1']]
    assert all(key not in original for key in ('segment', 'via', 'zone', 'gr_text'))


def test_extract_retains_only_edge_interface_and_mounting_hole_coordinates():
    result = extract(synthetic_board(), 42)
    assert result['board_outline_mm']['width'] == 16
    assert result['board_outline_mm']['height'] == 10
    assert result['mounting_holes'] == [{'x_mm': 34., 'y_mm': 16., 'drill_mm': 2.}]
    assert len(result['edge_connectors']) == 1
    connector = result['edge_connectors'][0]
    assert (connector['refdes'], connector['x_mm'], connector['y_mm'], connector['angle_deg']) == (
        'J1', 20.5, 12., 90.
    )


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')


def test_intake_rebases_only_permitted_coordinates_and_hides_source_identity():
    from scripts.intake_holdout import prepare_spec

    result = prepare_spec(synthetic_board(), 6)
    assert not result.get('repo_url') and not result.get('commit_sha')
    assert result['board_outline_mm'].get('origin_x', 0) == 0
    assert result['board_outline_mm'].get('origin_y', 0) == 0
    hole, = result['mounting_holes']
    assert (hole['x_mm'], hole['y_mm']) == (14., 8.)
    connector, = result['edge_connectors']
    assert (connector['refdes'], connector['x_mm'], connector['y_mm'], connector['angle_deg']) == (
        'J1', .5, 4., 90.
    )


def test_intake_does_not_expose_pose_for_chip_with_edge_connector_keywords():
    from scripts.intake_holdout import prepare_spec

    board = synthetic_board(x=21).replace(b'"Amplifier"', b'"USB optical amplifier"')
    moved = synthetic_board(x=30, y=15, rotation=90, routed=False).replace(
        b'"Amplifier"', b'"USB optical amplifier"'
    )
    spec = prepare_spec(board, 6)
    assert spec == prepare_spec(moved, 6)
    assert [item['refdes'] for item in spec['edge_connectors']] == ['J1']


@pytest.fixture
def holdout_cohort(tmp_path, monkeypatch):
    from scripts import run_holdout
    from scripts.intake_holdout import prepare_spec

    monkeypatch.setattr(run_holdout, 'ROOT', tmp_path)
    benchmark = tmp_path / 'benchmark_case'
    benchmark.mkdir()
    cases = []
    for case, raw in (
        (6, synthetic_board()),
        (7, synthetic_board().replace(b'(end 36 18)', b'(end 38 19)')),
    ):
        identifier = f'case{case}'
        spec = benchmark / f'blind_spec_{identifier}.json'
        write_json(spec, prepare_spec(raw, case))
        cases.append(dict(
            id=identifier,
            spec=spec.relative_to(tmp_path).as_posix(),
            spec_sha256=sha256(spec.read_bytes()),
            reference_sha256=sha256(raw),
            theme='synthetic instrumentation fixture',
        ))
    path = benchmark / 'holdout-synthetic.json'
    value = dict(schema_version=1, cohort='synthetic', cases=cases)
    write_json(path, value)
    return run_holdout, path, value


def test_read_cohort_accepts_anonymous_registry_then_rejects_spec_tampering(holdout_cohort):
    runner, path, registry = holdout_cohort
    assert runner.read_cohort(path) == registry
    case = registry['cases'][0]
    spec = runner.ROOT / case['spec']
    spec.write_bytes(spec.read_bytes() + b'\n')
    with pytest.raises(ValueError, match='hash|digest|SHA'):
        runner.read_cohort(path)


@pytest.mark.parametrize('key', ['repo_url', 'source_path', 'reference_path'])
def test_read_cohort_rejects_private_metadata_in_public_registry(holdout_cohort, key):
    runner, path, registry = holdout_cohort
    registry['cases'][0][key] = 'synthetic-private-origin'
    write_json(path, registry)
    with pytest.raises(ValueError):
        runner.read_cohort(path)


def test_read_cohort_rejects_exposed_case_even_with_valid_spec_hash(holdout_cohort):
    runner, path, registry = holdout_cohort
    original, *_ = registry['cases']
    old_name = 'benchmark_case/blind_spec_case4.json'
    (runner.ROOT / old_name).write_bytes((runner.ROOT / original['spec']).read_bytes())
    original.update(id='case4', spec=old_name)
    write_json(path, registry)
    with pytest.raises(ValueError):
        runner.read_cohort(path)


def test_read_cohort_rejects_spec_symlink_even_inside_root(holdout_cohort):
    runner, path, registry = holdout_cohort
    spec = runner.ROOT / registry['cases'][0]['spec']
    actual = spec.with_name('alias-target.json')
    spec.rename(actual)
    spec.symlink_to(actual)
    with pytest.raises(ValueError):
        runner.read_cohort(path)


def test_read_cohort_rejects_traversal_even_when_target_is_inside_root(holdout_cohort):
    runner, path, registry = holdout_cohort
    case = registry['cases'][0]
    case['spec'] = 'benchmark_case/../' + case['spec']
    write_json(path, registry)
    with pytest.raises(ValueError):
        runner.read_cohort(path)


def test_compare_rejects_invalid_freeze_before_even_resolving_vault(holdout_cohort, monkeypatch):
    runner, registry, _ = holdout_cohort
    checked = []

    def invalid_freeze(*args, **kwargs):
        checked.append(True)
        raise ValueError('synthetic freeze integrity failure')

    class UnavailableVault:
        def __fspath__(self):
            pytest.fail('comparison touched the reference vault before freeze verification')

    monkeypatch.setattr(runner, 'verify_frozen', invalid_freeze)
    output = runner.ROOT / 'not-opened'
    with pytest.raises(ValueError, match='synthetic freeze integrity failure'):
        runner.compare_frozen(registry, runner.ROOT / 'run', UnavailableVault(), output)
    assert checked == [True]
    assert not output.exists()


@pytest.fixture
def frozen_synthetic_run(holdout_cohort, monkeypatch):
    runner, registry, _ = holdout_cohort
    root = runner.ROOT
    source = root / 'synthetic_solver.py'
    source.write_text('fixed mock solver source\n', encoding='utf-8')
    monkeypatch.setattr(runner, 'code_fingerprint', lambda: {
        source.name: sha256(source.read_bytes()),
    })

    def fake_process(command, log, timeout):
        if command[2] == 'pytest':
            log.write_text('synthetic preflight passed\n', encoding='utf-8')
        else:
            assert command[2] == 'cloud_pcb.general_solver.run_blind'
            spec_copy = command[command.index('--spec') + 1]
            assert spec_copy.parent.name == 'inputs'
            assert json.loads(spec_copy.read_bytes())['repo_url'] == ''
            outdir = command[command.index('--outdir') + 1]
            for name in runner.BOARD_FILES:
                artifact = outdir / name
                artifact.parent.mkdir(parents=True, exist_ok=True)
                if artifact.suffix == '.json':
                    write_json(artifact, {})
                else:
                    artifact.write_text('synthetic evidence\n', encoding='utf-8')
        return 0

    monkeypatch.setattr(runner, 'sourced_process', fake_process)
    monkeypatch.setattr(runner, 'inspect_browser', lambda _: {'passed': True})
    monkeypatch.setattr(runner, 'report_metrics', lambda *args: {
        'hard_pass': False, 'failed_gates': ['synthetic failure'],
        'values': {}, 'evidence_errors': [],
    })

    directory = root / 'artifacts/holdouts/synthetic'
    result = runner.run_frozen(registry, directory, variant='stable', case_timeout=1)
    assert result['status'] == 'frozen'
    assert all(not case['hard_pass'] for case in result['cases'])
    assert runner.verify_frozen(registry, directory) == result
    return runner, registry, directory, source


@pytest.mark.parametrize('change', ['source', 'copied_spec', 'board', 'added_file', 'deleted_file', 'symlink'])
def test_verify_frozen_rejects_drift_in_actual_recorded_run(frozen_synthetic_run, change):
    runner, registry, directory, source = frozen_synthetic_run
    board = directory / 'case6/routed.kicad_pcb'
    if change == 'source':
        source.write_bytes(b'tampered source')
    elif change == 'copied_spec':
        spec = directory / 'inputs/case6.json'
        spec.write_bytes(spec.read_bytes() + b'\n')
    elif change == 'board':
        board.write_bytes(b'tampered copper')
    elif change == 'added_file':
        (directory / 'unexpected.txt').write_bytes(b'unregistered evidence')
    elif change == 'deleted_file':
        board.unlink()
    else:
        outside = runner.ROOT / 'replacement.kicad_pcb'
        outside.write_bytes(board.read_bytes())
        board.unlink()
        board.symlink_to(outside)
    with pytest.raises(ValueError, match='source|digest|file set|hash|symlink'):
        runner.verify_frozen(registry, directory)


def test_completed_cohort_cannot_be_retried_even_with_new_source(frozen_synthetic_run):
    runner, registry, directory, source = frozen_synthetic_run
    parent_digest = sha256((directory / 'freeze.json').read_bytes())
    source.write_text('different solver source')
    retry = directory.with_name('synthetic-retry-01')
    with pytest.raises(ValueError, match='incomplete'):
        runner.run_frozen(registry, retry, variant='placement-recovery', case_timeout=1,
                          retry_of=parent_digest)
    assert not retry.exists()


def test_compare_verifies_entire_cohort_before_using_any_reference(frozen_synthetic_run, monkeypatch):
    from cloud_pcb.general_solver import compare_reference

    runner, registry, directory, _ = frozen_synthetic_run
    vault = runner.ROOT / '.run/private-test'
    initial = synthetic_board()
    other = synthetic_board().replace(b'(end 36 18)', b'(end 38 19)')
    for case, raw in [('case6', initial), ('case7', other)]:
        private = vault / case
        private.mkdir(parents=True)
        (private / 'reference.kicad_pcb').write_bytes(raw)
        write_json(private / 'provenance.json', {
            'reference_sha256': sha256(raw), 'source_commit': 'a' * 40,
            'repo_url': 'https://example.invalid/source', 'source_path': 'source.kicad_pcb',
            'license': 'synthetic fixture',
        })
    second = vault / 'case7/reference.kicad_pcb'
    second.write_bytes(second.read_bytes() + b'\n')

    def unexpected_comparison(*args, **kwargs):
        pytest.fail('comparison started before the last reference passed its hash check')

    monkeypatch.setattr(compare_reference, 'compare_boards', unexpected_comparison)
    output = runner.ROOT / 'artifacts/comparison'
    with pytest.raises(ValueError, match='[Rr]eference digest'):
        runner.compare_frozen(registry, directory, vault, output)
    assert not output.exists()
