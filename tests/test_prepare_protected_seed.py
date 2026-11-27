"""Fixed replay preserves the native wire and via precision."""

import pytest

from cloud_pcb.layout_route.dsn_ses_bridge import COPPER_LAYERS, parse_ses
from scripts.prepare_protected_seed import prepare_protected_seed


STACK = 'Via[0-3]_420:200_um'


def fixture():
    dsn_shapes = ' '.join(f'(shape (circle {layer} 420))' for layer in COPPER_LAYERS)
    ses_shapes = ' '.join(f'(shape (circle {layer} 4200 0 0))' for layer in COPPER_LAYERS)
    dsn = (f'(pcb test\n  (resolution um 10)\n  (unit um)\n'
           f'  (library (padstack {STACK} {dsn_shapes}))\n'
           '  (network (net "fast signal") (net slow))\n  (wiring\n  )\n)\n')
    ses = (f'(session test\n  (routes\n    (resolution um 10)\n'
           f'    (library_out (padstack {STACK} {ses_shapes}))\n'
           '    (network_out \n'
           '      (net "fast signal"\n'
           '        (wire (path F.Cu 1998 1 -2 5001 -3002))\n'
           f'        (via {STACK} 5001 -3002)\n'
           '      )\n'
           '      (net slow\n        (wire (path B.Cu 2000 0 0 3000 4000))\n      )\n'
           '    )\n  )\n)')
    return dsn, ses


def test_fixed_wire_and_via_keep_decimal_microns_and_partition_seed(tmp_path):
    dsn, ses = fixture()
    fixed, held, remaining, count = prepare_protected_seed(dsn, ses, {'fast signal'})
    assert count == 2
    assert '(path F.Cu 199.8 0.1 -0.2 500.1 -300.2) (net "fast signal") (type fix)' in fixed
    assert f'(via {STACK} 500.1 -300.2 (net "fast signal") (type fix)' in fixed
    for name, text in (('source', ses), ('held', held), ('remaining', remaining)):
        (tmp_path / name).write_text(text)
    for kind in ('segments', 'vias'):
        assert parse_ses(tmp_path/'source')[kind] == parse_ses(tmp_path/'held')[kind] + parse_ses(tmp_path/'remaining')[kind]


@pytest.mark.parametrize(('old', 'replacement'), [
    ('resolution um 10', 'resolution um 1'),
    ('1998 1', '1999 1'),
    ('circle B.Cu 4200', 'circle B.Cu 4100'),
    ('(net "fast signal"', '(net unexpected'),
])
def test_inexact_or_unmeasured_copper_is_rejected(old, replacement):
    dsn, ses = fixture()
    with pytest.raises(ValueError):
        prepare_protected_seed(dsn, ses.replace(old, replacement), {'fast signal'})
