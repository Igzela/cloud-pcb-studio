"""JLCPCB parts catalog loader, Board 1 BOM selector, PCBA cost estimator, and BOM/CPL exporter."""

from __future__ import annotations

import csv
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Sequence

from contracts.schemas import SelectedPart, save_json

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent.parent
DEFAULT_CSV_PATH = PROJECT_ROOT / "data" / "jlcpcb-basic-preferred.csv"
DEFAULT_REGULATORS_JSON = PROJECT_ROOT / "data" / "jlcsearch-regulators.json"
DEFAULT_5K1_JSON = PROJECT_ROOT / "data" / "jlcsearch-5k1.json"
DEFAULT_CACHE_JSON = PACKAGE_DIR / "jlcsearch_cache.json"

JLCPCB_SETUP_FEE_USD = 8.00
JLCPCB_STENCIL_FEE_USD = 1.50
JLCPCB_JOINT_FEE_USD = 0.0017
JLCPCB_EXTENDED_FEEDER_FEE_USD = 3.00


@dataclass(frozen=True)
class CatalogPart:
    """Normalized JLCPCB/LCSC component record."""

    lcsc_code: str
    mfr: str
    package: str
    joints: int
    tier: Literal["Basic", "Preferred", "Extended"]
    stock: int
    unit_price_usd: float
    price_tiers: tuple[tuple[int, float], ...] = ()
    category: str = ""
    subcategory: str = ""
    description: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    rated_voltage_v: float | None = None
    dielectric: str | None = None
    source: str = "cdfer-csv"


def normalize_lcsc_code(raw: str | int) -> str:
    """Normalize numeric LCSC id (e.g., 18199188) to 'C18199188'."""
    text = str(raw).strip().upper()
    if not text:
        raise ValueError("Empty LCSC code")
    if text.startswith("C"):
        digits = text[1:]
    else:
        digits = text
    if not digits.isdigit():
        raise ValueError(f"Invalid LCSC code: {raw!r}")
    return f"C{int(digits)}"


def parse_price_tiers(price_str: str | float | int | None) -> tuple[tuple[int, float], ...]:
    """Parse JLCPCB price string like '1-49:0.0887,50-149:0.0707' into sorted (min_qty, usd) pairs."""
    if price_str is None:
        return ()
    if isinstance(price_str, (int, float)):
        return ((1, float(price_str)),)
    text = str(price_str).strip()
    if not text:
        return ()
    tiers: list[tuple[int, float]] = []
    for chunk in text.split(","):
        chunk = chunk.strip()
        if ":" not in chunk:
            continue
        qty_part, price_part = chunk.split(":", 1)
        qty_m = re.match(r"(\d+)", qty_part.strip())
        if not qty_m:
            continue
        try:
            min_qty = int(qty_m.group(1))
            price = float(price_part.strip())
        except ValueError:
            continue
        tiers.append((min_qty, price))
    tiers.sort(key=lambda t: t[0])
    return tuple(tiers)


def price_for_quantity(tiers: Sequence[tuple[int, float]], qty: int = 1, fallback: float = 0.0) -> float:
    if not tiers:
        return fallback
    selected = tiers[0][1]
    for min_qty, price in tiers:
        if qty >= min_qty:
            selected = price
    return selected


def _parse_attributes(raw_attrs: Any) -> dict[str, Any]:
    if isinstance(raw_attrs, dict):
        return raw_attrs
    if isinstance(raw_attrs, str) and raw_attrs.strip():
        try:
            parsed = json.loads(raw_attrs)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {}
    return {}


def _extract_voltage_rating(description: str, attributes: dict[str, Any]) -> float | None:
    for key in ("Voltage Rating", "Voltage - Rated", "Voltage - Supply", "Voltage - Input (Max)"):
        val = attributes.get(key)
        if isinstance(val, str):
            m = re.search(r"(\d+(?:\.\d+)?)\s*(k?V)\b", val, re.IGNORECASE)
            if m:
                v = float(m.group(1))
                return v * 1000.0 if m.group(2).lower() == "kv" else v
    # Parse from description tokens (avoid temperature/resistance tokens)
    kv_m = re.search(r"\b(\d+(?:\.\d+)?)\s*kV\b", description)
    if kv_m:
        return float(kv_m.group(1)) * 1000.0
    v_matches = re.findall(r"(?<![~\w])(\d+(?:\.\d+)?)\s*V(?![\w/])", description)
    if v_matches:
        # Take highest voltage token when ranges like 4.5V~28V or 50V appear
        vals = [float(x) for x in v_matches]
        return max(vals)
    # Handle range syntax like 4.5V~36V
    range_m = re.findall(r"(\d+(?:\.\d+)?)V~(\d+(?:\.\d+)?)V", description)
    if range_m:
        return max(float(hi) for _, hi in range_m)
    return None


def _extract_dielectric(description: str, attributes: dict[str, Any]) -> str | None:
    for key in ("Temperature Coefficient", "Dielectric", "Type"):
        val = attributes.get(key)
        if isinstance(val, str):
            for tok in ("C0G", "NP0", "X7R", "X5R", "X7S", "X6S", "Y5V"):
                if tok in val.upper():
                    return "C0G" if tok == "NP0" else tok
    upper = description.upper()
    for tok in ("C0G", "NP0", "X7R", "X5R", "X7S", "X6S", "Y5V"):
        if re.search(rf"\b{tok}\b", upper):
            return "C0G" if tok == "NP0" else tok
    return None


def _infer_joints(package: str, raw_joints: Any = None) -> int:
    if raw_joints not in (None, "", 0, "0"):
        try:
            j = int(raw_joints)
            if j > 0:
                return j
        except (TypeError, ValueError):
            pass
    pkg = package.upper()
    if any(p in pkg for p in ("0402", "0603", "0805", "1206", "SOD-", "SMA", "SMB", "SMC")):
        return 2
    if "SOT-23-6" in pkg or "TSOT-23-6" in pkg or "SOT-26" in pkg:
        return 6
    if "SOT-23-5" in pkg or "SOT-25" in pkg:
        return 5
    if "SOT-23" in pkg:
        return 3
    m = re.search(r"(\d+)\s*P\b", pkg)
    if m:
        return int(m.group(1))
    m2 = re.search(r"(?:QFN|LQFP|UFQFPN|SOIC|SOP|ESOP|TSSOP|WSON|WQFN)-(\d+)", pkg)
    if m2:
        pins = int(m2.group(1))
        return pins + 1 if "EP" in pkg or "QFN" in pkg or "ESOP" in pkg else pins
    if "16P" in pkg or "TYPE-C" in pkg:
        return 16
    return 2


def _determine_tier(
    basic_flag: Any = None,
    preferred_flag: Any = None,
    library_type: str | None = None,
) -> Literal["Basic", "Preferred", "Extended"]:
    if str(basic_flag).strip().lower() in ("1", "true", "yes") or str(library_type).strip().lower() == "base":
        return "Basic"
    if str(preferred_flag).strip().lower() in ("1", "true", "yes"):
        return "Preferred"
    return "Extended"


def estimate_effective_cap_ratio(
    dielectric: str | None,
    vdc: float,
    vrated: float | None,
    package: str = "0603",
    capacitance_uf: float = 0.1,
) -> float:
    """Estimate effective capacitance retention C_eff / C_nom under DC bias.

    Class 1 (C0G/NP0) shows negligible DC-bias drop (~1.00).
    Class 2 (X7R/X5R/Y5V) exhibits strong ferroelectric permittivity drop when
    Vdc / Vrated is high or when high capacitance is packed into tiny packages (0402).
    """
    d = (dielectric or "").upper()
    if d in ("C0G", "NP0"):
        return 1.0
    if not vrated or vrated <= 0:
        return 0.5
    ratio = max(0.0, vdc / vrated)

    # Package size factor (smaller case sizes have thinner dielectric layers -> higher E-field)
    pkg = package.upper()
    if "0402" in pkg:
        pkg_penalty = 1.35 if capacitance_uf >= 1.0 else 1.15
    elif "0603" in pkg:
        pkg_penalty = 1.15 if capacitance_uf >= 4.7 else 1.0
    elif "0805" in pkg or "1206" in pkg:
        pkg_penalty = 0.85
    else:
        pkg_penalty = 1.0

    # Dielectric sensitivity (Y5V >> X5R > X7R)
    if "Y5V" in d:
        k = 2.6
    elif "X5R" in d:
        k = 1.25
    else:  # X7R / generic Class 2
        k = 0.95

    retention = 1.0 / (1.0 + (k * pkg_penalty * ratio) ** 1.65)
    return round(max(0.10, min(0.99, retention)), 3)


class PartCatalog:
    """Offline-resilient JLCPCB catalog backed by CSV + cached/optional live jlcsearch JSON."""

    def __init__(self, parts: dict[str, CatalogPart] | None = None) -> None:
        self._parts: dict[str, CatalogPart] = parts or {}

    def __len__(self) -> int:
        return len(self._parts)

    def all_parts(self) -> list[CatalogPart]:
        return list(self._parts.values())

    def by_lcsc(self, lcsc: str | int) -> CatalogPart | None:
        try:
            code = normalize_lcsc_code(lcsc)
        except ValueError:
            return None
        return self._parts.get(code)

    def upsert(self, part: CatalogPart, prefer_existing_tier: bool = True) -> None:
        existing = self._parts.get(part.lcsc_code)
        if existing and prefer_existing_tier:
            tier_rank = {"Basic": 0, "Preferred": 1, "Extended": 2}
            if tier_rank[existing.tier] < tier_rank[part.tier] and existing.stock > 0:
                return
        self._parts[part.lcsc_code] = part

    @classmethod
    def load(
        cls,
        csv_path: str | Path = DEFAULT_CSV_PATH,
        regulators_json_path: str | Path | None = DEFAULT_REGULATORS_JSON,
        resistors_json_path: str | Path | None = DEFAULT_5K1_JSON,
        cache_json_path: str | Path | None = DEFAULT_CACHE_JSON,
        enrich_live: bool = False,
        live_queries: Sequence[str] = (),
    ) -> "PartCatalog":
        catalog = cls()
        csv_p = Path(csv_path)
        if csv_p.exists():
            catalog._load_csv(csv_p)

        for json_p, src_name in (
            (regulators_json_path, "jlcsearch-regulators-json"),
            (resistors_json_path, "jlcsearch-5k1-json"),
            (cache_json_path, "jlcsearch-cache-json"),
        ):
            if json_p is not None and Path(json_p).exists():
                catalog._load_jlcsearch_json(Path(json_p), source_name=src_name)

        if enrich_live and live_queries:
            for q in live_queries:
                catalog.enrich_from_jlcsearch(q)

        return catalog

    def _load_csv(self, path: Path) -> None:
        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                raw_lcsc = (row.get("lcsc") or "").strip()
                if not raw_lcsc:
                    continue
                try:
                    lcsc_code = normalize_lcsc_code(raw_lcsc)
                except ValueError:
                    continue
                mfr = (row.get("mfr") or "").strip()
                package = (row.get("package") or "").strip()
                desc = (row.get("description") or "").strip()
                attrs = _parse_attributes(row.get("attributes"))
                tiers = parse_price_tiers(row.get("price"))
                unit_price = price_for_quantity(tiers, qty=1, fallback=0.0)
                try:
                    stock = int(float(row.get("stock") or 0))
                except ValueError:
                    stock = 0
                tier = _determine_tier(
                    basic_flag=row.get("basic"),
                    preferred_flag=row.get("preferred"),
                    library_type=row.get("library_type"),
                )
                part = CatalogPart(
                    lcsc_code=lcsc_code,
                    mfr=mfr,
                    package=package,
                    joints=_infer_joints(package, row.get("joints")),
                    tier=tier,
                    stock=stock,
                    unit_price_usd=unit_price,
                    price_tiers=tiers,
                    category=(row.get("category") or "").strip(),
                    subcategory=(row.get("subcategory") or "").strip(),
                    description=desc,
                    attributes=attrs,
                    rated_voltage_v=_extract_voltage_rating(desc, attrs),
                    dielectric=_extract_dielectric(desc, attrs),
                    source="cdfer-csv",
                )
                self.upsert(part, prefer_existing_tier=False)

    def _load_jlcsearch_json(self, path: Path, source_name: str = "jlcsearch-json") -> int:
        payload = json.loads(path.read_text(encoding="utf-8"))
        items: list[dict[str, Any]] = []
        if isinstance(payload, dict):
            for v in payload.values():
                if isinstance(v, list):
                    items.extend(x for x in v if isinstance(x, dict))
        elif isinstance(payload, list):
            items.extend(x for x in payload if isinstance(x, dict))

        loaded = 0
        for item in items:
            raw_lcsc = item.get("lcsc")
            if raw_lcsc is None:
                continue
            try:
                lcsc_code = normalize_lcsc_code(raw_lcsc)
            except ValueError:
                continue
            desc = str(item.get("description") or "").strip()
            attrs = _parse_attributes(item.get("attributes"))
            pkg = str(item.get("package") or "").strip()
            tiers = parse_price_tiers(item.get("price"))
            if not tiers and item.get("price1") is not None:
                try:
                    tiers = ((1, float(item["price1"])),)
                except (TypeError, ValueError):
                    tiers = ()
            unit_price = price_for_quantity(tiers, qty=1, fallback=0.0)
            try:
                stock = int(item.get("stock") or 0)
            except (TypeError, ValueError):
                stock = 0
            tier = _determine_tier(
                basic_flag=item.get("is_basic"),
                preferred_flag=item.get("is_preferred"),
            )
            part = CatalogPart(
                lcsc_code=lcsc_code,
                mfr=str(item.get("mfr") or "").strip(),
                package=pkg,
                joints=_infer_joints(pkg, item.get("joints")),
                tier=tier,
                stock=stock,
                unit_price_usd=unit_price,
                price_tiers=tiers,
                category=str(item.get("category") or "").strip(),
                subcategory=str(item.get("subcategory") or "").strip(),
                description=desc,
                attributes=attrs,
                rated_voltage_v=_extract_voltage_rating(desc, attrs),
                dielectric=_extract_dielectric(desc, attrs),
                source=source_name,
            )
            self.upsert(part, prefer_existing_tier=True)
            loaded += 1
        return loaded

    def enrich_from_jlcsearch(
        self,
        query: str,
        timeout: float = 5.0,
        endpoint: str = "components",
    ) -> list[CatalogPart]:
        """Query jlcsearch.tscircuit.com with offline-resilient fallback to cached parts."""
        encoded = urllib.parse.quote(query)
        url = f"https://jlcsearch.tscircuit.com/{endpoint}/list.json?search={encoded}"
        req = urllib.request.Request(url, headers={"User-Agent": "cloud-pcb-studio/0.1"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, OSError):
            return self.search(query=query, min_stock=1)

        items: list[dict[str, Any]] = []
        if isinstance(data, dict):
            for val in data.values():
                if isinstance(val, list):
                    items.extend(x for x in val if isinstance(x, dict))

        added: list[CatalogPart] = []
        for item in items:
            raw_lcsc = item.get("lcsc")
            if raw_lcsc is None:
                continue
            try:
                lcsc_code = normalize_lcsc_code(raw_lcsc)
            except ValueError:
                continue
            desc = str(item.get("description") or "").strip()
            attrs = _parse_attributes(item.get("attributes"))
            pkg = str(item.get("package") or "").strip()
            tiers = parse_price_tiers(item.get("price"))
            if not tiers and item.get("price1") is not None:
                try:
                    tiers = ((1, float(item["price1"])),)
                except (TypeError, ValueError):
                    tiers = ()
            part = CatalogPart(
                lcsc_code=lcsc_code,
                mfr=str(item.get("mfr") or "").strip(),
                package=pkg,
                joints=_infer_joints(pkg, item.get("joints")),
                tier=_determine_tier(item.get("is_basic"), item.get("is_preferred")),
                stock=int(item.get("stock") or 0),
                unit_price_usd=price_for_quantity(tiers, qty=1, fallback=0.0),
                price_tiers=tiers,
                category=str(item.get("category") or "").strip(),
                subcategory=str(item.get("subcategory") or "").strip(),
                description=desc,
                attributes=attrs,
                rated_voltage_v=_extract_voltage_rating(desc, attrs),
                dielectric=_extract_dielectric(desc, attrs),
                source="jlcsearch-live",
            )
            self.upsert(part, prefer_existing_tier=True)
            added.append(part)
        return added

    def search(
        self,
        query: str | None = None,
        package: str | None = None,
        subcategory: str | None = None,
        min_stock: int = 101,
        dielectric: str | None = None,
        min_voltage: float | None = None,
        tokens: Sequence[str] = (),
        prefer_basic: bool = True,
    ) -> list[CatalogPart]:
        """Search loaded catalog parts, ranking Basic > Preferred > Extended and higher stock."""
        tier_rank = {"Basic": 0, "Preferred": 1, "Extended": 2}
        results: list[CatalogPart] = []
        q_low = query.lower() if query else None
        pkg_low = package.lower() if package else None
        sub_low = subcategory.lower() if subcategory else None
        diel_up = dielectric.upper() if dielectric else None

        for p in self._parts.values():
            if p.stock < min_stock:
                continue
            haystack = f"{p.lcsc_code} {p.mfr} {p.package} {p.category} {p.subcategory} {p.description}".lower()
            if q_low and q_low not in haystack:
                continue
            if pkg_low and pkg_low not in p.package.lower():
                continue
            if sub_low and sub_low not in p.subcategory.lower():
                continue
            if diel_up and (p.dielectric or "").upper() != diel_up:
                continue
            if min_voltage is not None and (p.rated_voltage_v is None or p.rated_voltage_v < min_voltage):
                continue
            if tokens and not all(tok.lower() in haystack for tok in tokens):
                continue
            results.append(p)

        if prefer_basic:
            results.sort(key=lambda p: (tier_rank[p.tier], -p.stock, p.unit_price_usd))
        else:
            results.sort(key=lambda p: (-p.stock, tier_rank[p.tier], p.unit_price_usd))
        return results


def _pick_part(
    catalog: PartCatalog,
    *,
    preferred_lcsc: str | None = None,
    min_stock: int = 101,
    **search_kwargs: Any,
) -> CatalogPart:
    """Select best matching in-stock part, preferring Basic/Preferred and verifying stock > 100."""
    candidates = catalog.search(min_stock=min_stock, **search_kwargs)
    if preferred_lcsc:
        norm = normalize_lcsc_code(preferred_lcsc)
        for c in candidates:
            if c.lcsc_code == norm:
                return c
        direct = catalog.by_lcsc(norm)
        if direct and direct.stock >= min_stock:
            return direct
    if not candidates:
        raise LookupError(f"No in-stock part (stock >= {min_stock}) found for {search_kwargs}")
    return candidates[0]


def _make_selected(
    part: CatalogPart,
    refdes: str,
    role: str,
    value: str,
    vdc_applied: float | None = None,
    cap_uf: float = 0.1,
    joints_override: int | None = None,
) -> SelectedPart:
    eff_ratio: float | None = None
    if part.dielectric is not None and vdc_applied is not None:
        eff_ratio = estimate_effective_cap_ratio(
            dielectric=part.dielectric,
            vdc=vdc_applied,
            vrated=part.rated_voltage_v,
            package=part.package,
            capacitance_uf=cap_uf,
        )
    return SelectedPart(
        refdes=refdes,
        role=role,
        lcsc_code=part.lcsc_code,
        mpn=part.mfr,
        package=part.package,
        value=value,
        jlcpcb_tier=part.tier,
        stock=part.stock,
        unit_price_usd=part.unit_price_usd,
        rated_voltage_v=part.rated_voltage_v,
        dielectric=part.dielectric,
        effective_cap_ratio=eff_ratio,
        solder_joints=joints_override if joints_override is not None else part.joints,
        source=part.source,
    )


def select_board1_bom(catalog: PartCatalog, min_stock: int = 101) -> list[SelectedPart]:
    """Select verified in-stock BOM for Board 1 (USB-C PD Telemetry & Smart Actuator Node).

    Maximizes JLCPCB Basic & Preferred passives/semiconductors while satisfying strict
    derating, ESD, USB-C PD dual-5.1k, and Buck + low-noise post-LDO requirements.
    """
    # Query catalog dynamically (Basic/Preferred first, stock > 100)
    usbc_conn = _pick_part(catalog, preferred_lcsc="C709357", query="KH-TYPE-C-16P", min_stock=min_stock)
    res_5k1 = _pick_part(catalog, preferred_lcsc="C23186", package="0603", subcategory="Chip Resistor", tokens=("5.1kΩ", "±1%"), min_stock=min_stock)
    res_1k = _pick_part(catalog, preferred_lcsc="C21190", package="0603", subcategory="Chip Resistor", tokens=("1kΩ", "±1%"), min_stock=min_stock)
    res_100k = _pick_part(catalog, preferred_lcsc="C25803", package="0603", subcategory="Chip Resistor", tokens=("100kΩ", "±1%"), min_stock=min_stock)
    res_22k = _pick_part(catalog, preferred_lcsc="C31850", package="0603", subcategory="Chip Resistor", tokens=("22kΩ", "±1%"), min_stock=min_stock)
    res_10k = _pick_part(catalog, preferred_lcsc="C25804", package="0603", subcategory="Chip Resistor", tokens=("10kΩ", "±1%"), min_stock=min_stock)
    res_22 = _pick_part(catalog, preferred_lcsc="C23345", package="0603", subcategory="Chip Resistor", tokens=("22Ω",), min_stock=min_stock)
    res_shunt = _pick_part(catalog, preferred_lcsc="C25334", package="1206", subcategory="Chip Resistor", tokens=("100mΩ", "±1%"), min_stock=min_stock)

    cap_100n_50v = _pick_part(catalog, preferred_lcsc="C14663", package="0603", dielectric="X7R", min_voltage=50.0, tokens=("100nF",), min_stock=min_stock)
    cap_10u_50v = _pick_part(catalog, preferred_lcsc="C440198", package="0805", min_voltage=50.0, tokens=("10uF",), min_stock=min_stock)
    cap_10u_25v = _pick_part(catalog, preferred_lcsc="C15850", package="0805", min_voltage=25.0, tokens=("10uF",), min_stock=min_stock)
    cap_22p_c0g = _pick_part(catalog, preferred_lcsc="C1653", package="0603", dielectric="C0G", min_voltage=50.0, tokens=("22pF",), min_stock=min_stock)
    cap_100p_c0g = _pick_part(catalog, preferred_lcsc="C14858", package="0603", dielectric="C0G", min_voltage=50.0, tokens=("100pF",), min_stock=min_stock)

    esd_usb = _pick_part(catalog, preferred_lcsc="C5261088", query="USBLC6-2SC6", min_stock=min_stock)
    esd_io = _pick_part(catalog, preferred_lcsc="C85364", query="SRV05-4", min_stock=min_stock)
    buck_ic = _pick_part(catalog, preferred_lcsc="C527684", query="TPS54202", min_stock=min_stock)
    buck_ind = _pick_part(catalog, preferred_lcsc="C305174", query="SWPA5040S4R7NT", min_stock=min_stock)
    ldo_2v5 = _pick_part(catalog, preferred_lcsc="C2827655", query="TPRT9193-25GB", min_stock=min_stock)
    ferrite_bead = _pick_part(catalog, preferred_lcsc="C14709", package="0603", subcategory="Ferrite Beads", tokens=("120Ω@100MHz", "2A"), min_stock=min_stock)
    mcu_qfn = _pick_part(catalog, preferred_lcsc="C2829190", query="STM32G0B1CEU6", min_stock=min_stock)
    opamp_ic = _pick_part(catalog, preferred_lcsc="C7377", query="MCP6002", min_stock=min_stock)
    xtal_12m = _pick_part(catalog, preferred_lcsc="C9002", package="SMD3225-4P", subcategory="Crystals", tokens=("12MHz",), min_stock=min_stock)
    led_pwr = _pick_part(catalog, preferred_lcsc="C2286", package="0603", subcategory="LED", tokens=("Red",), min_stock=min_stock)
    led_stat = _pick_part(catalog, preferred_lcsc="C89811", package="0603", subcategory="LED", tokens=("Yellow",), min_stock=min_stock)
    mosfet_coil = _pick_part(catalog, preferred_lcsc="C20917", package="SOT-23", subcategory="MOSFETs", tokens=("AO3400A",), min_stock=min_stock)
    flyback_diode = _pick_part(catalog, preferred_lcsc="C28646294", package="SMA", subcategory="Schottky", tokens=("SS33",), min_stock=min_stock)
    sw_boot = _pick_part(catalog, preferred_lcsc="C318884", subcategory="Tactile Switches", tokens=("TS-1187A",), min_stock=min_stock)

    parts: list[SelectedPart] = [
        _make_selected(usbc_conn, "J1", "usbc_receptacle_16p", "USB-C-16P", vdc_applied=20.0, joints_override=16),
        # Dual independent 5.1k CC1 & CC2 pulldowns
        _make_selected(res_5k1, "R1", "usbc_cc1_pulldown", "5.1k 1%"),
        _make_selected(res_5k1, "R2", "usbc_cc2_pulldown", "5.1k 1%"),
        # 1k ESD current-limiting series resistors (CC1/CC2 + analog/telemetry IO)
        _make_selected(res_1k, "R3", "esd_series_limiter_cc1", "1k 1%"),
        _make_selected(res_1k, "R4", "esd_series_limiter_cc2", "1k 1%"),
        _make_selected(res_1k, "R5", "esd_series_limiter_adc_telem", "1k 1%"),
        _make_selected(res_1k, "R6", "esd_series_limiter_gpio_act", "1k 1%"),
        # LED current-limiting resistors (reuses 1k Basic feeder!)
        _make_selected(res_1k, "R7", "led_pwr_limiter", "1k 1%"),
        _make_selected(res_1k, "R8", "led_stat_limiter", "1k 1%"),
        # Synchronous Buck 3.3V feedback divider (0.596V * (1 + 100k/22k) = 3.305V)
        _make_selected(res_100k, "R9", "buck_fb_top", "100k 1%"),
        _make_selected(res_22k, "R10", "buck_fb_bot", "22k 1%"),
        # Coil driver gate & pull-down & current sense shunt
        _make_selected(res_22, "R11", "coil_mosfet_gate_series", "22R 1%"),
        _make_selected(res_10k, "R12", "coil_mosfet_gate_pulldown", "10k 1%"),
        _make_selected(res_10k, "R14", "mcu_nrst_pullup", "10k 1%"),
        _make_selected(res_shunt, "R13", "coil_current_sense_shunt", "0.1R 1% 250mW"),
        # VBUS 20V MLCCs (50V rated -> Vrated >= 2.5 * 20V)
        _make_selected(cap_10u_50v, "C1", "vbus_bulk_decoupling_1", "10uF 50V X5R", vdc_applied=20.0, cap_uf=10.0),
        _make_selected(cap_10u_50v, "C2", "vbus_bulk_decoupling_2", "10uF 50V X5R", vdc_applied=20.0, cap_uf=10.0),
        # Buck 3.3V & LDO 2.5V bulk caps (25V 0805 X5R -> >7.5x margin, 3x10uF = 28.9uF eff)
        _make_selected(cap_10u_25v, "C3", "buck_3v3_out_bulk_1", "10uF 25V X5R", vdc_applied=3.3, cap_uf=10.0),
        _make_selected(cap_10u_25v, "C4", "buck_3v3_out_bulk_2", "10uF 25V X5R", vdc_applied=3.3, cap_uf=10.0),
        _make_selected(cap_10u_25v, "C18", "buck_3v3_out_bulk_3", "10uF 25V X5R", vdc_applied=3.3, cap_uf=10.0),
        _make_selected(cap_10u_25v, "C17", "ldo_2v5_out_bulk", "10uF 25V X5R", vdc_applied=2.5, cap_uf=10.0),
        # 100nF 50V X7R 0603 decoupling & bootstrap caps (safe on 20V, 3.3V, 2.5V)
        _make_selected(cap_100n_50v, "C5", "vbus_hf_bypass", "100nF 50V X7R", vdc_applied=20.0, cap_uf=0.1),
        _make_selected(cap_100n_50v, "C6", "buck_boot_cap", "100nF 50V X7R", vdc_applied=5.0, cap_uf=0.1),
        _make_selected(cap_100n_50v, "C7", "mcu_vdd_decoupling_1", "100nF 50V X7R", vdc_applied=3.3, cap_uf=0.1),
        _make_selected(cap_100n_50v, "C8", "mcu_vdd_decoupling_2", "100nF 50V X7R", vdc_applied=3.3, cap_uf=0.1),
        _make_selected(cap_100n_50v, "C9", "mcu_vdd_decoupling_3", "100nF 50V X7R", vdc_applied=3.3, cap_uf=0.1),
        _make_selected(cap_100n_50v, "C10", "ldo_2v5_in_bypass", "100nF 50V X7R", vdc_applied=3.3, cap_uf=0.1),
        _make_selected(cap_100n_50v, "C11", "opamp_2v5_bypass", "100nF 50V X7R", vdc_applied=2.5, cap_uf=0.1),
        _make_selected(cap_100n_50v, "C12", "telem_rc_filter_cap", "100nF 50V X7R", vdc_applied=2.5, cap_uf=0.1),
        # C0G/NP0 timing & precision ADC caps
        _make_selected(cap_22p_c0g, "C13", "crystal_load_cap_1", "22pF 50V C0G", vdc_applied=1.8, cap_uf=22e-6),
        _make_selected(cap_22p_c0g, "C14", "crystal_load_cap_2", "22pF 50V C0G", vdc_applied=1.8, cap_uf=22e-6),
        _make_selected(cap_100p_c0g, "C15", "adc_antialias_c0g_1", "100pF 50V C0G", vdc_applied=2.5, cap_uf=100e-6),
        _make_selected(cap_100p_c0g, "C16", "adc_antialias_c0g_2", "100pF 50V C0G", vdc_applied=2.5, cap_uf=100e-6),
        # ESD protection, power conversion, MCU, analog, actuator
        _make_selected(esd_usb, "D1", "usb_esd_tvs", "USBLC6-2SC6", vdc_applied=5.0),
        _make_selected(esd_io, "D2", "io_esd_tvs_array", "SRV05-4-P-T7", vdc_applied=3.3),
        _make_selected(buck_ic, "U1", "buck_3v3_sync_reg", "TPS54202HDDCR", vdc_applied=20.0),
        _make_selected(buck_ind, "L1", "buck_power_inductor", "4.7uH 3.9A", joints_override=2),
        _make_selected(ldo_2v5, "U2", "ldo_2v5_low_noise", "TPRT9193-25GB", vdc_applied=3.3),
        _make_selected(ferrite_bead, "FB1", "analog_rail_ferrite_bead", "120R@100MHz 2A"),
        _make_selected(mcu_qfn, "U3", "mcu_qfn_usb_pd", "STM32G0B1CEU6", vdc_applied=3.3, joints_override=49),
        _make_selected(opamp_ic, "U4", "telem_sense_opamp", "MCP6002T-I/SN", vdc_applied=2.5),
        _make_selected(xtal_12m, "Y1", "hse_crystal_12mhz", "12MHz 20pF", joints_override=4),
        _make_selected(led_pwr, "LED1", "status_led_pwr_red", "Red 0603"),
        _make_selected(led_stat, "LED2", "status_led_pd_yellow", "Yellow 0603"),
        _make_selected(mosfet_coil, "Q1", "coil_driver_nmos", "AO3400A 30V 5.7A"),
        _make_selected(flyback_diode, "D3", "coil_flyback_schottky", "SS33 30V 3A"),
        _make_selected(sw_boot, "SW1", "boot_reset_tactile", "TS-1187A", joints_override=4),
    ]
    return parts


def estimate_pcba_cost(parts: Sequence[SelectedPart], board_qty: int = 5) -> dict[str, Any]:
    """Calculate JLCPCB SMT PCBA assembly + BOM cost breakdown.

    Pricing rules:
      - Setup fee: $8.00 per order
      - Stencil fee: $1.50 per order
      - Solder joint fee: $0.0017 per joint across all boards
      - Extended feeder fee: $3.00 per unique Extended LCSC part ($0 for Basic & Preferred)
    """
    if board_qty < 1:
        raise ValueError("board_qty must be >= 1")

    unique_by_lcsc: dict[str, SelectedPart] = {}
    for p in parts:
        unique_by_lcsc.setdefault(p.lcsc_code, p)

    unique_basic = [p.lcsc_code for p in unique_by_lcsc.values() if p.jlcpcb_tier == "Basic"]
    unique_pref = [p.lcsc_code for p in unique_by_lcsc.values() if p.jlcpcb_tier == "Preferred"]
    unique_ext = [p.lcsc_code for p in unique_by_lcsc.values() if p.jlcpcb_tier == "Extended"]

    total_joints_per_board = sum(p.solder_joints for p in parts)
    unit_bom_sum = sum(p.unit_price_usd for p in parts)

    setup_fee = JLCPCB_SETUP_FEE_USD
    stencil_fee = JLCPCB_STENCIL_FEE_USD
    joint_fee = round(total_joints_per_board * JLCPCB_JOINT_FEE_USD * board_qty, 4)
    extended_feeder_fee = round(len(unique_ext) * JLCPCB_EXTENDED_FEEDER_FEE_USD, 2)
    components_cost = round(unit_bom_sum * board_qty, 4)
    total_cost = round(setup_fee + stencil_fee + joint_fee + extended_feeder_fee + components_cost, 4)

    basic_pref_placements = sum(1 for p in parts if p.jlcpcb_tier in ("Basic", "Preferred"))
    return {
        "board_qty": board_qty,
        "total_placements_per_board": len(parts),
        "total_solder_joints_per_board": total_joints_per_board,
        "unique_parts": len(unique_by_lcsc),
        "unique_basic_parts": len(unique_basic),
        "unique_preferred_parts": len(unique_pref),
        "unique_extended_parts": len(unique_ext),
        "unique_extended_lcsc": sorted(unique_ext),
        "basic_preferred_placement_ratio": round(basic_pref_placements / max(1, len(parts)), 4),
        "setup_fee_usd": setup_fee,
        "stencil_fee_usd": stencil_fee,
        "joint_fee_usd": joint_fee,
        "extended_feeder_fee_usd": extended_feeder_fee,
        "components_cost_usd": components_cost,
        "unit_bom_cost_per_board_usd": round(unit_bom_sum, 4),
        "total_pcba_cost_usd": total_cost,
        "per_board_cost_usd": round(total_cost / board_qty, 4),
    }


def generate_board1_placements(parts: Sequence[SelectedPart]) -> list[dict[str, Any]]:
    """Generate clean, non-overlapping Board 1 (70mm x 50mm) CPL placements.

    Enforces critical physical layout rules:
      - J1 USB-C connector at left board edge (x=6.5mm, y=25.0mm)
      - D1 USBLC6-2SC6 placed <= 3.5mm from J1 DP/DM pins with 0-via intent
      - R1, R2 independent 5.1k CC1/CC2 pulldowns right next to J1 CC pins
      - Buck power stage (U1, L1, C1..C6, R9, R10) compactly grouped at top-left
      - Quiet 2.5V analog section (FB1, U2, C10, C11, C17, U4, C12, C15, C16, R5) at bottom-left
      - U3 QFN MCU centered with Y1 crystal & C13/C14 close by
      - Coil actuator MOSFET Q1, D3, R11, R12, R13 at right power-drive zone
    """
    coords: dict[str, tuple[float, float, str, float]] = {
        "J1": (6.5, 25.0, "Top", 90.0),
        "D1": (10.0, 25.0, "Top", 0.0),   # 3.5 mm from J1 center (< 5 mm 0-via ESD rule!)
        "R1": (11.5, 20.5, "Top", 90.0),  # CC1 5.1k
        "R2": (11.5, 29.5, "Top", 90.0),  # CC2 5.1k
        "R3": (14.0, 20.5, "Top", 0.0),   # CC1 1k ESD limiter
        "R4": (14.0, 29.5, "Top", 0.0),   # CC2 1k ESD limiter
        "C1": (13.0, 36.0, "Top", 0.0),
        "C2": (16.5, 36.0, "Top", 0.0),
        "C5": (10.0, 36.0, "Top", 0.0),
        "U1": (20.5, 38.0, "Top", 0.0),
        "C6": (20.5, 42.5, "Top", 0.0),
        "L1": (26.5, 38.0, "Top", 0.0),
        "R9": (24.0, 43.0, "Top", 0.0),
        "R10": (27.0, 43.0, "Top", 0.0),
        "C3": (32.0, 38.0, "Top", 0.0),
        "C4": (35.5, 38.0, "Top", 0.0),
        "C18": (39.0, 38.0, "Top", 0.0),
        "FB1": (16.0, 12.0, "Top", 0.0),
        "U2": (20.5, 12.0, "Top", 0.0),
        "C10": (18.0, 15.5, "Top", 0.0),
        "C17": (24.0, 12.0, "Top", 0.0),
        "U4": (29.5, 11.5, "Top", 0.0),
        "C11": (29.5, 16.0, "Top", 0.0),
        "R5": (25.0, 7.5, "Top", 0.0),
        "C12": (28.0, 7.5, "Top", 0.0),
        "C15": (31.5, 7.5, "Top", 0.0),
        "C16": (34.5, 7.5, "Top", 0.0),
        "U3": (37.0, 25.0, "Top", 0.0),
        "C7": (31.5, 29.5, "Top", 90.0),
        "C8": (42.5, 29.5, "Top", 90.0),
        "C9": (42.5, 20.5, "Top", 90.0),
        "R14": (31.5, 20.5, "Top", 90.0),
        "Y1": (37.0, 15.5, "Top", 0.0),
        "C13": (34.5, 12.0, "Top", 0.0),
        "C14": (39.5, 12.0, "Top", 0.0),
        "D2": (48.0, 12.0, "Top", 0.0),
        "R6": (48.0, 16.5, "Top", 0.0),
        "Q1": (56.0, 24.0, "Top", 0.0),
        "R11": (51.0, 24.0, "Top", 0.0),
        "R12": (51.0, 27.0, "Top", 0.0),
        "D3": (61.5, 24.0, "Top", 90.0),
        "R13": (56.0, 17.5, "Top", 0.0),
        "LED1": (58.0, 42.0, "Top", 0.0),
        "R7": (54.5, 42.0, "Top", 0.0),
        "LED2": (58.0, 38.0, "Top", 0.0),
        "R8": (54.5, 38.0, "Top", 0.0),
        "SW1": (58.0, 9.0, "Top", 0.0),
    }
    cpl: list[dict[str, Any]] = []
    for idx, p in enumerate(parts):
        if p.refdes in coords:
            x, y, layer, rot = coords[p.refdes]
        else:
            x = 12.0 + (idx % 8) * 6.0
            y = 46.0 - (idx // 8) * 4.0
            layer, rot = "Top", 0.0
        cpl.append(
            {
                "Designator": p.refdes,
                "Mid X": f"{x:.2f}mm",
                "Mid Y": f"{y:.2f}mm",
                "Layer": layer,
                "Rotation": int(rot) if float(rot).is_integer() else rot,
            }
        )
    return cpl


def export_bom_and_cpl(
    parts: Sequence[SelectedPart],
    placements: Sequence[dict[str, Any]],
    cost_summary: dict[str, Any],
    out_dir: str | Path = PROJECT_ROOT / "artifacts" / "spec",
) -> dict[str, Path]:
    """Export artifacts/spec/bom_verified.json, bom_jlcpcb.csv, and cpl_jlcpcb.csv."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # 1. bom_verified.json
    bom_json_path = out / "bom_verified.json"
    all_in_stock = all(p.stock > 100 for p in parts)
    cc_parts = [p for p in parts if p.role in ("usbc_cc1_pulldown", "usbc_cc2_pulldown")]
    bom_payload = {
        "board": "Board 1: USB-C PD Telemetry & Smart Actuator Node",
        "stock_threshold_min": 100,
        "all_parts_in_stock": all_in_stock,
        "independent_cc_resistors_verified": len(cc_parts) == 2 and cc_parts[0].refdes != cc_parts[1].refdes,
        "cost_estimate": cost_summary,
        "parts": parts,
    }
    save_json(bom_json_path, bom_payload)

    # 2. bom_jlcpcb.csv (Designator, Footprint, Comment, LCSC Part #)
    grouped: dict[tuple[str, str, str], list[str]] = {}
    for p in parts:
        key = (p.package, p.value, p.lcsc_code)
        grouped.setdefault(key, []).append(p.refdes)

    bom_csv_path = out / "bom_jlcpcb.csv"
    with bom_csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["Designator", "Footprint", "Comment", "LCSC Part #"])
        for (footprint, comment, lcsc_code), refdes_list in grouped.items():
            writer.writerow([",".join(refdes_list), footprint, comment, lcsc_code])

    # 3. cpl_jlcpcb.csv (Designator, Mid X, Mid Y, Layer, Rotation)
    cpl_csv_path = out / "cpl_jlcpcb.csv"
    with cpl_csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["Designator", "Mid X", "Mid Y", "Layer", "Rotation"],
            lineterminator="\n",
        )
        writer.writeheader()
        for row in placements:
            writer.writerow(row)

    return {
        "bom_verified_json": bom_json_path,
        "bom_jlcpcb_csv": bom_csv_path,
        "cpl_jlcpcb_csv": cpl_csv_path,
    }
