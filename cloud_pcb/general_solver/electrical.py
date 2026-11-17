"""Conservative supply evidence from blind pin attributes and voltage labels.

Names identify nets, never particular projects. Inferred intent has provenance,
does not invent current ratings, and never mutates the caller's specification.
"""
from __future__ import annotations

import copy
import re

GROUND_NAMES = frozenset({'GND', 'AGND', 'DGND', 'PGND', 'VSS'})
_VOLTAGE = re.compile(r'^([+-]?)(\d+)(?:V(\d+)|(\.\d+)?V)(?:$|_)', re.I)
_SUPPLY_PIN = re.compile(r'^(?:(?:A|D|P|U\d*)?VDD|VCC|VBAT|VBUS|VIN|VS|VM)(?:\d|_|$)', re.I)
_INTERFACE_WORDS = ('connector', 'pinheader', 'pinsocket', 'terminal', 'usb', 'rj45', 'jst')


def voltage_label(name):
    """Recognize lexical voltage labels, including hierarchy and rail suffixes."""
    leaf = str(name).rsplit('/', 1)[-1]
    match = _VOLTAGE.match(leaf)
    if not match:
        return None
    sign, whole, fraction, decimal = match.groups()
    value = float(whole + ('.' + fraction if fraction else decimal or ''))
    return -value if sign == '-' else value


def _passive_kind(comp):
    """Recognize two-terminal R/L/C by package identity, not reference ID."""
    pads = [p for p in comp.get('pads', []) if p.get('type') != 'np_thru_hole']
    if len(pads) != 2:
        return None
    footprint = comp.get('footprint_name', '').lower()
    leaf = footprint.rsplit(':', 1)[-1]
    if any(word in footprint for word in _INTERFACE_WORDS):
        return None
    for kind, words, prefixes in (
        ('capacitor', ('capacitor',), ('c_', 'cp_')),
        ('resistor', ('resistor',), ('r_',)),
        ('inductor', ('inductor', 'ferrite'), ('l_', 'fb_')),
    ):
        if any(word in footprint for word in words) or leaf.startswith(prefixes):
            return kind
    # Custom libraries may use an opaque package name. A declared engineering
    # unit in the value is evidence; a bare number or pin count is not.
    number = r'(?:\d+(?:\.\d*)?|\.\d+)'
    value = str(comp.get('value', '')).strip().lower().replace('µ', 'u').replace('μ', 'u')
    for kind, unit in (
        ('capacitor', r'[pnumk]?f'),
        ('inductor', r'[pnum]?h'),
        ('resistor', r'(?:[kmr]\d*(?:ohm|Ω)?|ohm|Ω)'),
    ):
        if re.match(r'^' + number + r'\s*' + unit + r'(?:$|\s|/)', value):
            return kind
    return None


def _typed_supply_pad(pad):
    function = str(pad.get('pin_function', '')).upper()
    return (pad.get('pin_type') in ('power_in', 'power_out')
            and function not in GROUND_NAMES
            and not any(word in function for word in ('GND', 'VSS', 'REF', 'BIAS', 'SW', 'LX'))
            and (_SUPPLY_PIN.match(function) is not None or voltage_label(function) is not None))


def supply_routing_scope(spec, net):
    """Prove a single-IC supply domain from explicit pins and shunt capacitors.

    Unknown endpoints, external interfaces and additional active devices make
    this a distribution network. R/L filters are allowed within a local domain;
    connectivity alone is never used to infer load current or physical height.
    """
    by_ref = {c['refdes']: c for c in spec.get('components', [])}
    endpoints = spec.get('nets', {}).get(net, [])
    members = {ref for ref, _ in endpoints}
    fallback = dict(scope='board_distribution', reason='local supply domain not proven')
    if not members or not members.issubset(by_ref):
        return fallback
    for ref, pin in endpoints:
        if not any(str(p['num']) == str(pin) and p.get('net') == net for p in by_ref[ref].get('pads', [])):
            return dict(**fallback, incomplete_endpoints=True)
    # The two representations of the netlist must agree before narrowing scope.
    pad_endpoints = {(c['refdes'], str(p['num'])) for c in by_ref.values() for p in c.get('pads', []) if p.get('net') == net}
    if pad_endpoints != {(ref, str(pin)) for ref, pin in endpoints}:
        return dict(**fallback, incomplete_endpoints=True)
    edge_refs = {c['refdes'] for c in spec.get('edge_connectors', [])}
    if members & edge_refs:
        return dict(**fallback, external_interface=True)
    passives = {ref: _passive_kind(by_ref[ref]) for ref in members}
    owners = members - {ref for ref, kind in passives.items() if kind}
    if len(owners) != 1:
        return fallback
    owner = by_ref[next(iter(owners))]
    if len(owner.get('pads', [])) < 3 or any(word in owner.get('footprint_name', '').lower() for word in _INTERFACE_WORDS):
        return fallback
    supply_pins = [p for p in owner['pads'] if p.get('net') == net and _typed_supply_pad(p)]
    if not supply_pins:
        return fallback
    ground = {n for n in spec.get('nets', {}) if n.upper() in GROUND_NAMES}
    ground.update(p['net'] for c in by_ref.values() for p in c.get('pads', []) if p.get('net') and str(p.get('pin_function', '')).upper() in GROUND_NAMES and p.get('pin_type') in ('power_in', 'power_out'))
    caps = [ref for ref, kind in passives.items() if kind == 'capacitor' and any(p.get('net') in ground for p in by_ref[ref]['pads'])]
    if not caps:
        return fallback
    return dict(scope='single_ic_domain', owner_ref=owner['refdes'], shunt_caps=sorted(caps), passive_members=sorted(ref for ref, kind in passives.items() if kind), supply_pins=sorted(str(p['num']) for p in supply_pins), current_verified=False, reason='one typed IC supply owner, shunt return capacitors and only passive R/L/C endpoints')


def derive_electrical_requirements(spec, *, scoped=False):
    derived = copy.deepcopy(spec)
    requirements = derived.setdefault('design_requirements', {})
    rails = requirements.setdefault('power_rails', [])
    declared = {r['net'] for r in rails}
    nets = set(spec.get('nets', {}))
    protected = set(requirements.get('high_speed_or_clock_nets', []))
    protected.update(n for pair in requirements.get('differential_pairs', []) for n in (pair['pos_net'], pair['neg_net']))
    evidence = {}
    for net in nets - declared - protected:
        voltage = voltage_label(net)
        if voltage is not None:
            evidence[net] = [dict(kind='voltage_label', nominal_v=voltage)]
    for comp in spec.get('components', []):
        for pad in comp.get('pads', []):
            net = pad.get('net', '')
            function = str(pad.get('pin_function', '')).upper()
            if net not in nets or net in declared or net in protected or pad.get('pin_type') not in ('power_in', 'power_out'):
                continue
            # Ground, thermal, reference and switch-node pins must not silently
            # become supply trunks merely because a symbol calls them power_in.
            if _typed_supply_pad(pad):
                evidence.setdefault(net, []).append(dict(kind='typed_supply_pin', refdes=comp['refdes'], pin=str(pad['num']), function=function, pin_type=pad['pin_type']))
    decisions = []
    for net, reasons in sorted(evidence.items()):
        # A labelled ground net wins over contradictory/incorrect symbol data.
        if net.upper() in GROUND_NAMES:
            continue
        voltage = voltage_label(net)
        rail = dict(net=net, nominal_v=voltage, requirements_source='derived from blind electrical evidence', evidence=reasons, current_verified=False)
        if scoped:
            scope = supply_routing_scope(spec, net)
            rail['routing_scope'] = scope['scope']
            rail['routing_scope_evidence'] = scope
        rails.append(rail)
        decisions.append(rail)
    return derived, decisions
