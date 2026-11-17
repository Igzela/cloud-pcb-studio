"""Shared immutable contracts between Cloud PCB Studio cluster workers."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal


@dataclass(frozen=True)
class RailSpec:
    name: str
    vin_min: float
    vin_nom: float
    vin_max: float
    vout_nom: float
    iout_max_a: float
    ambient_temp_c: float = 40.0
    low_noise_required: bool = False


@dataclass(frozen=True)
class StackupProfile:
    id: str
    layers: int
    board_thickness_mm: float
    outer_cu_oz: float
    inner_cu_oz: float
    prepreg_type: str
    prepreg_thickness_mm: float
    prepreg_er: float
    core_thickness_mm: float
    surface_finish: Literal["HASL", "LeadFree-HASL", "ENIG", "OSP"]
    substrate: Literal["FR4-Std", "FR4-MidTg", "FR4-HighTg", "Al-MCPCB", "Polyimide-Flex"]
    w_50ohm_mil: float
    w_90diff_mil: float
    s_90diff_mil: float
    min_trace_mil: float
    min_space_mil: float
    min_drill_mm: float
    min_annular_ring_mm: float
    copper_to_edge_mm: float


@dataclass(frozen=True)
class SelectedPart:
    refdes: str
    role: str
    lcsc_code: str
    mpn: str
    package: str
    value: str
    jlcpcb_tier: Literal["Basic", "Preferred", "Extended"]
    stock: int
    unit_price_usd: float
    rated_voltage_v: float | None = None
    dielectric: str | None = None
    effective_cap_ratio: float | None = None
    solder_joints: int = 2
    source: str = "cdfer-csv"


@dataclass(frozen=True)
class NetClassRule:
    name: str
    trace_width_mm: float
    clearance_mm: float
    via_drill_mm: float
    via_pad_mm: float
    max_current_a: float
    diff_pair_gap_mm: float | None = None
    impedance_ohm: float | None = None
    zero_via_allowed: bool = True


@dataclass(frozen=True)
class RoutingBenchmark:
    engine: str
    board_name: str
    success: bool
    runtime_ms: float
    total_nets: int
    routed_nets: int
    unrouted_nets: int
    via_count: int
    total_wirelength_mm: float
    notes: str = ""


@dataclass(frozen=True)
class VerifyTicket:
    ticket_id: str
    severity: Literal["CRITICAL", "HIGH", "MEDIUM", "INFO"]
    owner_worker: Literal["parts_dfm", "special_ip", "layout_route", "verify_gate"]
    board: str
    gate: str
    message: str
    measured_value: str
    required_value: str
    artifact_path: str = ""


@dataclass(frozen=True)
class GateReport:
    board: str
    passed: bool
    checks_run: int
    checks_passed: int
    critical_count: int
    high_count: int
    metrics: dict[str, Any] = field(default_factory=dict)
    tickets: list[VerifyTicket] = field(default_factory=list)


def save_json(path: str | Path, obj: Any) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(obj, "__dataclass_fields__"):
        payload = asdict(obj)
    else:
        payload = obj
    out.write_text(json.dumps(payload, indent=2, default=_json_default), encoding="utf-8")
    return out


def _json_default(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
