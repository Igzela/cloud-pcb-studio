"""Special PCB Engineering IP: 4-Layer Planar Coils, Chevron CapTouch & Curved/Meander Routing."""

from .planar_coil import (
    CoilLayerGeometry,
    PlanarCoilDesign,
    PlanarCoilSpec,
    build_planar_coil,
    run_coil_spice_simulation,
)
from .captouch import (
    CapTouchDesign,
    CapTouchSpec,
    ChevronElectrode,
    build_captouch_slider,
)
from .curved_routing import (
    ArcSegment,
    MeanderResult,
    StraightSegment,
    TracePath,
    chaikin_smooth,
    fillet_polyline_with_arcs,
    generate_serpentine_meander,
)

__all__ = [
    "ArcSegment",
    "CapTouchDesign",
    "CapTouchSpec",
    "ChevronElectrode",
    "CoilLayerGeometry",
    "MeanderResult",
    "PlanarCoilDesign",
    "PlanarCoilSpec",
    "StraightSegment",
    "TracePath",
    "build_captouch_slider",
    "build_planar_coil",
    "chaikin_smooth",
    "fillet_polyline_with_arcs",
    "generate_serpentine_meander",
    "run_coil_spice_simulation",
]
