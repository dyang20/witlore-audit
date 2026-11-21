"""Map rendering with matplotlib.

The DA training's map standards drive this: every map is labelled, shows the
search ring, and uses parcel letters that match the parcel table. Excluded
parcels are shaded with a legend explaining why, and every parcel in the ring is
accounted for rather than quietly omitted.

The analysis maps (parcel, zoning, flood, wetlands, ASR) are pure vector,
drawn from geometry already fetched, so they cannot fail on a tile server. The
two overview maps (topo, street/aerial) sit on USGS National Map tiles; when
those cannot be fetched the overview is skipped and the report says so, rather
than printing an empty frame.
"""

from __future__ import annotations

import os
from typing import Any, Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

from ..models import Disposition, Parcel

FILL = {
    Disposition.PURSUE: "#C8E6C9",
    Disposition.DO_NOT_PURSUE: "#FFCDD2",
    Disposition.UNRESOLVED: "#FFE0B2",
}
EDGE = {
    Disposition.PURSUE: "#1B5E20",
    Disposition.DO_NOT_PURSUE: "#B71C1C",
    Disposition.UNRESOLVED: "#E65100",
}
DPI = 160


def _poly(ax, geom, **kw) -> None:
    """Draw a polygon or multipolygon."""
    if geom is None or geom.is_empty:
        return
    geoms = geom.geoms if geom.geom_type.startswith("Multi") else [geom]
    for g in geoms:
        if not hasattr(g, "exterior"):
            continue
        ax.fill(*g.exterior.xy, **kw)


def _outline(ax, geom, **kw) -> None:
    if geom is None or geom.is_empty:
        return
    geoms = geom.geoms if geom.geom_type.startswith("Multi") else [geom]
    for g in geoms:
        if hasattr(g, "exterior"):
            ax.plot(*g.exterior.xy, **kw)
        elif hasattr(g, "xy"):
            ax.plot(*g.xy, **kw)


def _frame(ax, ring, title: str, pad: float = 1.25) -> None:
    ax.set_aspect("equal")
    r = ring.radius_ft * pad
    ax.set_xlim(ring.center_x - r, ring.center_x + r)
    ax.set_ylim(ring.center_y - r, ring.center_y + r)
    ax.set_title(title, fontsize=11, fontweight="bold")
    ax.set_xticks([])
    ax.set_yticks([])
    _scale_bar(ax, ring)
    ax.annotate("N", xy=(0.95, 0.93), xycoords="axes fraction", ha="center",
                fontsize=12, fontweight="bold")
    ax.annotate("", xy=(0.95, 0.97), xytext=(0.95, 0.90), xycoords="axes fraction",
                arrowprops=dict(arrowstyle="-|>", color="black", lw=1.2))


def _scale_bar(ax, ring) -> None:
    span = ring.radius_ft * 2.5
    for candidate in (5280, 2640, 1000, 500, 250):
        if candidate < span * 0.35:
            length = candidate
            break
    else:
        length = 250
    x0 = ring.center_x - ring.radius_ft * 1.15
    y0 = ring.center_y - ring.radius_ft * 1.15
    ax.plot([x0, x0 + length], [y0, y0], color="black", lw=2.5,
            solid_capstyle="butt")
    label = f"{length:,} ft" + (" (1 mi)" if length == 5280 else "")
    ax.text(x0 + length / 2, y0 + ring.radius_ft * 0.04, label,
            ha="center", fontsize=7)


def _save(fig, out_dir: str, name: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{name}.png")
    fig.savefig(path, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return path


def _ring(ax, ring) -> None:
    _outline(ax, ring.polygon, color="#1565C0", lw=2.0, zorder=5)
    ax.plot([ring.center_x], [ring.center_y], marker="+", color="#1565C0",
            markersize=12, markeredgewidth=2, zorder=6)


def _label_style(n_parcels: int) -> tuple[float, bool]:
    """Font size and whether to box the letter.

    An urban ring can hold several hundred parcels. At that density a boxed
    10 pt letter on every lot is a solid smear, so labels shrink and lose their
    boxes as the count rises.
    """
    if n_parcels <= 40:
        return 10.0, True
    if n_parcels <= 120:
        return 6.5, False
    return 4.5, False


def parcel_map(ring, parcels: list[Parcel], out_dir: str,
               title: str = "Parcel Map and Dispositions") -> str:
    fig, ax = plt.subplots(figsize=(8, 8))
    size, boxed = _label_style(len(parcels))
    for p in parcels:
        d = p.disposition or Disposition.UNRESOLVED
        _poly(ax, p.geometry, facecolor=FILL[d], edgecolor=EDGE[d],
              lw=0.9 if boxed else 0.4, alpha=0.85, zorder=2)
        if not p.letter:
            continue
        c = p.geometry.representative_point()
        # Candidates always keep a box so they stand out of a dense map.
        box = boxed or d is Disposition.PURSUE
        ax.text(c.x, c.y, p.letter, ha="center", va="center",
                fontsize=max(size, 7.0) if d is Disposition.PURSUE else size,
                fontweight="bold", zorder=5 if d is Disposition.PURSUE else 4,
                bbox=dict(boxstyle="circle,pad=0.22", fc="white",
                          ec=EDGE[d], lw=1.0) if box else None)
    _ring(ax, ring)
    _frame(ax, ring, title)

    handles = [mpatches.Patch(facecolor=FILL[d], edgecolor=EDGE[d],
                              label=d.value.replace("_", " ").title())
               for d in (Disposition.PURSUE, Disposition.DO_NOT_PURSUE,
                         Disposition.UNRESOLVED)]
    handles.append(Line2D([0], [0], color="#1565C0", lw=2, label="Search ring"))
    ax.legend(handles=handles, loc="upper left", fontsize=8, framealpha=0.95)
    return _save(fig, out_dir, "parcel")


_JURISDICTION_SHORT = {"Pottawattamie County": "County", "Council Bluffs": "CB",
                       "City of Oakland": "Oakland"}


def zoning_map(ring, parcels: list[Parcel], districts: list[dict], out_dir: str,
               boundaries: Optional[list[dict]] = None) -> str:
    """Districts coloured per jurisdiction-and-code, with city limits drawn.

    Two jurisdictions reuse the same district names: the county's R-1 and
    Council Bluffs' R-1 are different districts under different codes. Keying
    the legend on the code alone merged them into one colour, which on a ring
    that straddles the city limit hides exactly the thing the map is for.
    """
    fig, ax = plt.subplots(figsize=(8, 8))
    keys = sorted({(d.get("jurisdiction") or "", d["code"]) for d in districts})
    cmap = plt.get_cmap("tab20")
    colors = {k: cmap(i % 20) for i, k in enumerate(keys)}

    for d in districts:
        k = (d.get("jurisdiction") or "", d["code"])
        _poly(ax, d["geometry"], facecolor=colors[k], edgecolor="#555555",
              lw=0.6, alpha=0.55, zorder=1)
    size, boxed = _label_style(len(parcels))
    for p in parcels:
        _outline(ax, p.geometry, color="#333333", lw=0.7 if boxed else 0.35, zorder=3)
        if p.letter:
            c = p.geometry.representative_point()
            ax.text(c.x, c.y, p.letter, ha="center", va="center",
                    fontsize=min(size, 9.0), fontweight="bold", zorder=4,
                    bbox=dict(boxstyle="circle,pad=0.2", fc="white", ec="#333333",
                              lw=0.8) if boxed else None)
    cities = []
    for b in boundaries or []:
        if b.get("geometry") is None or not b.get("city"):
            continue
        _outline(ax, b["geometry"], color="#000000", lw=2.2, ls=(0, (6, 3)), zorder=6)
        cities.append(str(b["city"]).title())
    _ring(ax, ring)
    _frame(ax, ring, "Zoning Districts and Jurisdictions")

    handles = [mpatches.Patch(
        facecolor=colors[k], alpha=0.55,
        label=f"{_JURISDICTION_SHORT.get(k[0], k[0])} {k[1]}".strip()) for k in keys]
    if cities:
        handles.append(Line2D([0], [0], color="#000000", lw=2.2, ls=(0, (6, 3)),
                              label="City limit: " + ", ".join(sorted(set(cities)))))
    handles.append(Line2D([0], [0], color="#1565C0", lw=2, label="Search ring"))
    ax.legend(handles=handles, loc="upper left", fontsize=7.5,
              title="Jurisdiction and district", framealpha=0.95)
    if not keys:
        ax.text(0.5, 0.02, "No zoning district polygons were returned for this ring",
                transform=ax.transAxes, ha="center", fontsize=9, color="#B71C1C")
    return _save(fig, out_dir, "zoning")


def _overlay_map(ring, parcels: list[Parcel], out_dir: str, name: str, title: str,
                 layers: list[tuple[list[Any], str, str, str]], empty: str) -> str:
    """One constraint layer over lettered parcel outlines."""
    fig, ax = plt.subplots(figsize=(8, 8))
    handles = []
    for z, (geoms, face, edge, label) in enumerate(layers, start=1):
        drawn = False
        for g in geoms:
            _poly(ax, g, facecolor=face, edgecolor=edge, lw=0.5, alpha=0.6, zorder=z)
            drawn = True
        if drawn:
            handles.append(mpatches.Patch(facecolor=face, edgecolor=edge, label=label))
    size, boxed = _label_style(len(parcels))
    for p in parcels:
        _outline(ax, p.geometry, color="#444444", lw=0.7 if boxed else 0.35, zorder=5)
        if p.letter and len(parcels) <= 120:
            c = p.geometry.representative_point()
            ax.text(c.x, c.y, p.letter, ha="center", va="center",
                    fontsize=min(size, 8.0), fontweight="bold", zorder=6,
                    color="#222222")
    _ring(ax, ring)
    _frame(ax, ring, title)
    handles.append(Line2D([0], [0], color="#1565C0", lw=2, label="Search ring"))
    ax.legend(handles=handles, loc="upper left", fontsize=8, framealpha=0.95)
    if len(handles) == 1:
        ax.text(0.5, 0.02, empty, transform=ax.transAxes, ha="center", fontsize=9)
    return _save(fig, out_dir, name)


def flood_map(ring, parcels: list[Parcel], out_dir: str,
              flood: Optional[list[dict]] = None) -> str:
    zones = sorted({str(z.get("zone")) for z in flood or [] if z.get("zone")})
    hazard = [z.get("geometry") for z in flood or []
              if str(z.get("zone") or "").upper() not in
              ("X", "AREA OF MINIMAL FLOOD HAZARD")]
    minimal = [z.get("geometry") for z in flood or []
               if str(z.get("zone") or "").upper() in
               ("X", "AREA OF MINIMAL FLOOD HAZARD")]
    special = [z for z in zones if z.upper() not in ("X",)]
    return _overlay_map(
        ring, parcels, out_dir, "flood", "Flood Plain Map (FEMA NFHL)",
        [(minimal, "#E3F2FD", "#90CAF9", "Zone X (minimal hazard)"),
         (hazard, "#64B5F6", "#0D47A1",
          f"Flood hazard zone {', '.join(special) or 'mapped'} (risk, not disqualifying)")],
        "No FEMA flood hazard polygons were returned for this ring")


def wetlands_map(ring, parcels: list[Parcel], out_dir: str,
                 wetlands: Optional[list[Any]] = None) -> str:
    return _overlay_map(
        ring, parcels, out_dir, "wetlands", "Wetlands Map (USFWS NWI)",
        [(list(wetlands or []), "#A5D6A7", "#2E7D32",
          "Wetland (excluded from buildable area)")],
        "No mapped wetlands in this ring")


def constraints_map(ring, parcels: list[Parcel], out_dir: str, *,
                    wetlands: Optional[list[Any]] = None,
                    flood: Optional[list[dict]] = None) -> str:
    """Wetlands and flood on one sheet. Kept for callers that want both."""
    return _overlay_map(
        ring, parcels, out_dir, "constraints", "Wetlands and Flood Hazard",
        [([z.get("geometry") for z in flood or []], "#90CAF9", "#1565C0",
          "FEMA flood zone (risk, not disqualifying)"),
         (list(wetlands or []), "#A5D6A7", "#2E7D32",
          "Wetland (excluded from buildable area)")],
        "No wetlands or mapped flood hazard in this ring")


_ASR_MARK = {
    "constructed": ("^", "#4E342E", "Constructed"),
    "granted": ("D", "#EF6C00", "Granted, not yet built"),
    "inactive": ("x", "#9E9E9E", "Cancelled, dismantled or terminated"),
    "unknown": ("o", "#616161", "Status not recorded"),
}


def asr_map(ring, towers: list[dict], out_dir: str) -> str:
    """Ring plus the 2-mile ASR search area, marked by registration status."""
    from ..geo.crs import point_to_working

    fig, ax = plt.subplots(figsize=(8, 8))
    two_mile = ring.radius_buffer(2.0)
    _outline(ax, two_mile, color="#6A1B9A", lw=1.4, ls="--", zorder=4)
    half_mile = ring.radius_buffer(0.5)
    _outline(ax, half_mile, color="#AD1457", lw=1.0, ls=":", zorder=4)
    _ring(ax, ring)

    seen = set()
    for t in towers:
        try:
            x, y = point_to_working(t["longitude"], t["latitude"])
        except Exception:  # noqa: BLE001
            continue
        cat = t.get("category") or ("constructed" if t.get("is_constructed")
                                    else "unknown")
        marker, color, _label = _ASR_MARK.get(cat, _ASR_MARK["unknown"])
        seen.add(cat)
        ax.plot([x], [y], marker=marker, color=color, markersize=7, zorder=6, lw=0)
        h = t.get("height_agl_ft")
        if h and cat != "inactive":
            ax.text(x, y + 180, f"{h:.0f} ft", ha="center", fontsize=6.5, zorder=6)

    ax.set_aspect("equal")
    span = 2.2 * 5280
    ax.set_xlim(ring.center_x - span, ring.center_x + span)
    ax.set_ylim(ring.center_y - span, ring.center_y + span)
    ax.set_xticks([]); ax.set_yticks([])
    ax.set_title("Search Ring and 2-Mile Radius with FCC ASR Registrations",
                 fontsize=11, fontweight="bold")
    handles = [
        Line2D([0], [0], color="#1565C0", lw=2, label="Search ring"),
        Line2D([0], [0], color="#AD1457", lw=1, ls=":", label="0.5 mile"),
        Line2D([0], [0], color="#6A1B9A", lw=1.4, ls="--", label="2 miles"),
    ]
    for cat in ("constructed", "granted", "inactive", "unknown"):
        if cat in seen:
            marker, color, label = _ASR_MARK[cat]
            handles.append(Line2D([0], [0], marker=marker, color=color, lw=0,
                                  label=label))
    if not towers:
        ax.text(0.5, 0.02, "No FCC ASR registrations within 2 miles",
                transform=ax.transAxes, ha="center", fontsize=9)
    ax.legend(handles=handles, loc="upper left", fontsize=8, framealpha=0.95)
    return _save(fig, out_dir, "asr")


def overview_map(ring, parcels: list[Parcel], mosaic, out_dir: str, name: str,
                 title: str, towers: Optional[list[dict]] = None) -> str:
    """Search ring and parcel lines over a USGS basemap.

    Drawn in Web Mercator, the tiles' own projection, so the imagery is shown
    as served rather than resampled. This map is orientation only; every
    measurement in the report is made in State Plane feet.
    """
    from ..geo.crs import point_to_wgs84, to_web_mercator
    from ..sources.basemap import ATTRIBUTION, lonlat_to_mercator

    fig, ax = plt.subplots(figsize=(8, 8))
    ax.imshow(mosaic.image, extent=mosaic.extent, zorder=0, interpolation="bilinear")

    on_dark = name == "street"
    line = "#FFEB3B" if on_dark else "#B71C1C"
    for p in parcels:
        try:
            _outline(ax, to_web_mercator(p.geometry), color=line,
                     lw=0.6 if len(parcels) <= 120 else 0.3, alpha=0.9, zorder=2)
        except Exception:  # noqa: BLE001 - one odd geometry is not the map
            continue
    ring_m = to_web_mercator(ring.polygon)
    _outline(ax, ring_m, color="#00E5FF" if on_dark else "#1565C0", lw=2.4, zorder=4)
    lon, lat = point_to_wgs84(ring.center_x, ring.center_y)
    cx, cy = lonlat_to_mercator(lon, lat)
    ax.plot([cx], [cy], marker="+", color="#00E5FF" if on_dark else "#1565C0",
            markersize=14, markeredgewidth=2.2, zorder=5)
    for t in towers or []:
        if not t.get("is_constructed"):
            continue
        tx, ty = lonlat_to_mercator(t["longitude"], t["latitude"])
        ax.plot([tx], [ty], marker="^", color="#FF1744", markersize=8,
                markeredgecolor="white", zorder=6, lw=0)

    minx, miny, maxx, maxy = ring_m.bounds
    half = max(maxx - minx, maxy - miny) / 2.0 * 1.45
    ax.set_xlim(cx - half, cx + half)
    ax.set_ylim(cy - half, cy + half)
    ax.set_aspect("equal")
    ax.set_xticks([]); ax.set_yticks([])
    ax.set_title(title, fontsize=11, fontweight="bold")
    ax.annotate("N", xy=(0.95, 0.93), xycoords="axes fraction", ha="center",
                fontsize=12, fontweight="bold", color="white" if on_dark else "black")
    ax.annotate("", xy=(0.95, 0.97), xytext=(0.95, 0.90), xycoords="axes fraction",
                arrowprops=dict(arrowstyle="-|>", lw=1.2,
                                color="white" if on_dark else "black"))
    ax.text(0.995, 0.005, ATTRIBUTION, transform=ax.transAxes, ha="right",
            va="bottom", fontsize=6.5, color="white" if on_dark else "#333333",
            bbox=dict(fc="black" if on_dark else "white", ec="none", alpha=0.55,
                      pad=1.5))
    handles = [
        Line2D([0], [0], color="#00E5FF" if on_dark else "#1565C0", lw=2.4,
               label=f"Search ring ({ring.radius_ft:,.0f} ft)"),
        Line2D([0], [0], color=line, lw=0.8, label="Parcel boundary"),
    ]
    if any(t.get("is_constructed") for t in towers or []):
        handles.append(Line2D([0], [0], marker="^", color="#FF1744", lw=0,
                              markeredgecolor="white", label="Constructed ASR tower"))
    ax.legend(handles=handles, loc="upper left", fontsize=8, framealpha=0.9)
    return _save(fig, out_dir, name)
