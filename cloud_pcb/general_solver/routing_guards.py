"""Conservative DSN geometry for already placed ground vias.

A circumscribed octagon removes a square's unused corners while retaining its
full protected circular radius. No copper, clearance rule or layer is removed.
"""
from __future__ import annotations

import math
from shapely.geometry import Polygon


def conservative_via_guard(x: float, y: float, radius: float, layers: list[str]) -> dict:
    values = (x, y, radius)
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
           for v in values) or radius <= 0:
        raise ValueError('finite via center and positive protection radius required')
    grid = .001
    aligned = all(abs(v / grid - round(v / grid)) < 1e-7 for v in values)
    cx, cy = round(x, 3), round(y, 3)
    # Current fanout centers/radii already lie on the DSN micrometre lattice.
    # For a non-lattice caller, add a conservative quantization allowance.
    r = radius if aligned else math.ceil(radius / grid - 1e-9) * grid + 2 * grid
    diagonal = math.ceil((math.sqrt(2) - 1) * r / grid - 1e-9) * grid
    points = [(r, diagonal), (diagonal, r), (-diagonal, r), (-r, diagonal),
              (-r, -diagonal), (-diagonal, -r), (diagonal, -r), (r, -diagonal)]
    geometry = Polygon([(round(cx + dx, 3), round(cy + dy, 3)) for dx, dy in points]).convex_hull
    x0, y0, x1, y1 = geometry.bounds
    return dict(kind='fixed_ground_via', dsn_kind='keepout',
                x_min=x0, y_min=y0, x_max=x1, y_max=y1, layers=list(layers),
                polygon_mm=list(geometry.exterior.coords), protected_radius_mm=radius,
                guard_geometry='circumscribed_octagon')
