"""Prepare a native FastRoute warm replay with selected copper fixed in DSN."""

import argparse
import json
import re
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

from cloud_pcb.layout_route.dsn_ses_bridge import COPPER_LAYERS, _find_child, _parse_sexpr, _quote


SESSION_HEADER = '    (network_out \n'
SESSION_END = '    )\n  )\n)'
DSN_END = '  )\n)\n'


def _microns(token):
    if not re.fullmatch(r'-?\d+', token):
        raise ValueError(f'Non-integer SES coordinate {token!r}')
    return format(Decimal(token) / 10, 'f')


def _section(tree, key):
    matches = [node for node in tree if isinstance(node, list) and node and node[0] == key]
    if len(matches) != 1:
        raise ValueError(f'Expected exactly one {key} section')
    return matches[0]


def _round_land_sizes(stack, scale):
    layers = {}
    for shape in (entry[1] for entry in stack[2:] if entry[0] == 'shape'):
        if (len(shape) not in (3, 5) or shape[0] != 'circle' or shape[1] in layers
                or any(Decimal(offset) != 0 for offset in shape[3:])):
            raise ValueError('Expected centered circular via lands')
        layers[shape[1]] = Decimal(shape[2]) / scale
    return layers


def _check_via(name, stacks, session_stacks):
    size = re.fullmatch(r'Via\[0-3\]_(\d+):(\d+)_um', name)
    if not size or name not in stacks or name not in session_stacks:
        raise ValueError(f'Unsupported or undefined through-via padstack {name!r}')
    diameter, drill = map(int, size.groups())
    expected = {layer: diameter for layer in COPPER_LAYERS}
    if not 0 < drill < diameter or _round_land_sizes(stacks[name], 1) != expected or _round_land_sizes(session_stacks[name], 10) != expected:
        raise ValueError(f'Via lands disagree with the native padstack identifier {name!r}')


def prepare_protected_seed(design, session, protected_nets):
    """Return a fixed design and two lossless partitions of a native 0.1 µm session."""
    protected = set(protected_nets)
    if not protected or any(not net or '\\' in net or any(ord(char) < 32 for char in net) for net in protected):
        raise ValueError('At least one supported protected net name is required')
    dsn, = _parse_sexpr(design)
    ses, = _parse_sexpr(session)
    routes = _section(ses, 'routes')
    resolution = ['resolution', 'um', '10']
    if (_section(dsn, 'unit') != ['unit', 'um'] or _section(dsn, 'resolution') != resolution
            or _section(routes, 'resolution') != resolution):
        raise ValueError('Expected a matching native 0.1 µm DSN and SES resolution')
    wiring = _section(dsn, 'wiring')
    existing_names = {_section(item, 'net')[1] for item in wiring[1:]}
    if existing_names & protected:
        raise ValueError('Protected networks overlap existing DSN fixed copper')
    net_names = {node[1] for node in _section(dsn, 'network')[1:] if node[0] == 'net'}
    if not protected <= net_names:
        raise ValueError('A protected net is absent from the DSN')
    stacks = {node[1]: node for node in _section(dsn, 'library')[1:] if node[0] == 'padstack'}
    session_stacks = {node[1]: node for node in (_find_child(routes, 'library_out') or [])[1:] if node[0] == 'padstack'}
    header, body = session.split(SESSION_HEADER)
    network, tail = body.rsplit(SESSION_END, 1)
    blocks = re.findall(r'^      \(net [^\n]+\n.*?^      \)\n', network, re.M | re.S)
    if ''.join(blocks) != network or tail.strip():
        raise ValueError('Unsupported native SES network formatting')

    fixed, remaining, held, seen = [], [], [], set()
    for block in blocks:
        (_, net, *items), = _parse_sexpr(block)
        if net in seen:
            raise ValueError(f'Duplicate SES net block {net!r}')
        seen.add(net)
        if net not in protected:
            remaining.append(block)
            continue
        if not items:
            raise ValueError(f'Protected net has no routed copper: {net!r}')
        held.append(block)
        suffix = f' (net {_quote(net)}) (type fix))\n'
        for item in items:
            if item[0] == 'wire' and len(item) == 2 and item[1][0] == 'path':
                _, layer, width, *coordinates = item[1]
                if (layer not in COPPER_LAYERS or len(coordinates) < 4 or len(coordinates) % 2
                        or int(width) <= 0 or int(width) % 2):
                    raise ValueError('Unsupported protected wire geometry')
                path = ' '.join(_microns(token) for token in [width, *coordinates])
                fixed.append(f'    (wire (path {layer} {path})' + suffix)
            elif item[0] == 'via' and len(item) == 4:
                _, stack, x, y = item
                _check_via(stack, stacks, session_stacks)
                fixed.append(f'    (via {_quote(stack)} {_microns(x)} {_microns(y)}' + suffix)
            else:
                raise ValueError(f'Unsupported protected copper record: {item[0]!r}')
    if not protected <= seen or not design.endswith(DSN_END):
        raise ValueError('A protected net is missing or the DSN wiring format is unsupported')
    locked = design[:-len(DSN_END)] + ''.join(fixed) + DSN_END
    amended, = _parse_sexpr(locked)
    amended_wiring = _section(amended, 'wiring')
    if (amended_wiring[:-len(fixed)] != wiring
            or [node for node in amended if node is not amended_wiring] != [node for node in dsn if node is not wiring]):
        raise ValueError('The change is not confined to new DSN fixed wiring')
    prefix, suffix = header + SESSION_HEADER, SESSION_END + tail
    return locked, prefix + ''.join(held) + suffix, prefix + ''.join(remaining) + suffix, len(fixed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('design', type=Path)
    parser.add_argument('session', type=Path)
    parser.add_argument('--spec', required=True, type=Path)
    parser.add_argument('--outdir', required=True, type=Path)
    args = parser.parse_args()
    try:
        design, session, spec = (path.read_bytes() for path in (args.design, args.session, args.spec))
        requirements = json.loads(spec)['design_requirements']
        protected = set(requirements.get('high_speed_or_clock_nets', []))
        protected.update(pair[side] for pair in requirements.get('differential_pairs', []) for side in ('pos_net', 'neg_net'))
        locked, fixed, remaining, count = prepare_protected_seed(design.decode(), session.decode(), protected)
        meta = {'protected_nets': sorted(protected), 'fixed_dsn_objects': count,
                'input_sha256': {str(path): sha256(content).hexdigest()
                                 for path, content in zip((args.design, args.session, args.spec), (design, session, spec))}}
        args.outdir.mkdir()
        for name, content in (('system-fixed.dsn', locked), ('system-fixed-source.ses', fixed), ('unfixed-seed.ses', remaining)):
            (args.outdir / name).write_text(content, encoding='utf-8')
        (args.outdir / 'fixture-evidence.json').write_text(json.dumps(meta, indent=2) + '\n', encoding='utf-8')
    except (OSError, ValueError, TypeError, KeyError, IndexError, ArithmeticError) as exc:
        print(json.dumps({'prepared': False, 'error': str(exc)}))
        return 2
    print(json.dumps(meta))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
