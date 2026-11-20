"""Compatibility projection for comparing a frozen reference with kiutils."""

from collections import Counter
from decimal import Decimal

from kiutils.utils.sexpr import parse_sexp

from scripts.seal_holdout import children


def _serialize(item) -> str:
    if isinstance(item, list):
        return "(" + " ".join(map(_serialize, item)) + ")"
    if isinstance(item, str):
        escaped = item.replace('"', r'\"')
        return '"' + escaped + '"'
    if isinstance(item, float):
        return format(Decimal(str(item)), "f")
    return str(item)


def normalize_named_reference(raw: bytes) -> tuple[bytes, dict[str, int]]:
    """Return numeric-net KiCad syntax, preserving copper topology and geometry."""
    tree = parse_sexp(raw.decode("utf-8"))
    if not tree or tree[0] != "kicad_pcb":
        raise ValueError("Expected a KiCad PCB")
    net_rows = children(tree, "net")
    instances = children(tree, "footprint")
    pads = [pad for footprint in instances for pad in children(footprint, "pad")]
    zones = children(tree, "zone") + [zone for footprint in instances for zone in children(footprint, "zone")]
    conductors = pads + zones + [
        item for kind in ("segment", "arc", "via") for item in children(tree, kind)
    ]
    bindings = []
    for item in conductors:
        rows = children(item, "net")
        if len(rows) > 1:
            raise ValueError(f"Multiple net assignments on a reference {item[0]}")
        if rows:
            bindings.append((item, rows[0]))
    all_rows = net_rows + [net for _, net in bindings]
    if not any(len(row) >= 2 and isinstance(row[1], str) for row in all_rows):
        return raw, {}

    names_by_id = {0: ""}
    ids_by_name = {"": 0}
    explicit_names = set()
    counters: Counter[str] = Counter()

    for row in net_rows:
        if len(row) == 2 and isinstance(row[1], str):
            explicit_names.add(row[1])
            continue
        if len(row) != 3 or type(row[1]) is not int or row[1] < 0 or not isinstance(row[2], str):
            raise ValueError("Malformed reference net table")
        number, name = row[1:]
        if names_by_id.get(number, name) != name or ids_by_name.get(name, number) != number:
            raise ValueError("Conflicting reference net table")
        names_by_id[number] = name
        ids_by_name[name] = number

    for item, row in bindings:
        if len(row) == 2 and isinstance(row[1], str):
            explicit_names.add(row[1])
        elif not (
            len(row) in (2, 3) and type(row[1]) is int and row[1] in names_by_id
            and (len(row) == 2 or (item[0] == "pad" and row[2] == names_by_id[row[1]]))
        ):
            raise ValueError(f"Invalid or ambiguous reference {item[0]} net binding")

    next_id = max(names_by_id) + 1
    for name in sorted(explicit_names - ids_by_name.keys()):
        ids_by_name[name] = next_id
        names_by_id[next_id] = name
        next_id += 1
    for row in net_rows:
        if isinstance(row[1], str):
            tree.remove(row)
    existing_numbers = {row[1] for row in children(tree, "net")}
    root_insert = next((i for i, field in enumerate(tree) if isinstance(field, list) and field
                        and field[0] in ("footprint", "gr_line", "segment", "zone")), len(tree))
    tree[root_insert:root_insert] = [
        ["net", number, name] for number, name in sorted(names_by_id.items())
        if number != 0 and number not in existing_numbers
    ]

    for item, row in bindings:
        kind = item[0]
        named = isinstance(row[1], str)
        number = ids_by_name[row[1]] if named else row[1]
        name = names_by_id[number]
        target = ["net", number, name] if kind == "pad" else ["net", number]
        if row != target:
            row[:] = target
            counters[kind] += 1
        if kind == "zone":
            names = children(item, "net_name")
            if names and names != [["net_name", name]]:
                raise ValueError("Zone name contradicts reference net table")
            if named and not names:
                item.insert(item.index(row) + 1, ["net_name", name])
    serialized = _serialize(tree) + "\n"
    if parse_sexp(serialized) != tree:
        raise ValueError("Reference conversion changed an unrelated KiCad field")
    return serialized.encode("utf-8"), dict(counters)
