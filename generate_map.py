#!/usr/bin/env python3
"""
generate_map.py — fetch Tempe South street network from OpenStreetMap
and output an inline SVG showing the LATM works area.

Usage:
    python3 generate_map.py > tempe_map_fragment.svg
    python3 generate_map.py --file  (writes tempe_map_fragment.svg directly)

The SVG is designed to be pasted inline into dashboard.html.
"""

import json
import math
import sys
import urllib.request
import urllib.parse

# ── Bounding box: Tempe South (lat_min, lon_min, lat_max, lon_max) ──────────
BBOX = (-33.928, 151.158, -33.907, 151.183)

# ── SVG viewport ─────────────────────────────────────────────────────────────
SVG_W = 700
SVG_H = 460
PAD = 28   # padding inside viewport

# ── Colour scheme (matches dashboard.html CSS variables) ─────────────────────
COLOURS = {
    "closure":  "#dc2626",   # red   — full road closure
    "stopgo":   "#c2410c",   # amber — stop/slow
    "marking":  "#1a56db",   # blue  — line marking
    "zone":     "#7c3aed",   # purple — speed zone
    "context":  "#c8c8c8",   # light grey — background streets
    "arterial": "#a0a0a0",   # mid grey — major roads (Princes Hwy, etc.)
    "bg":       "#f5f5f4",   # map background
    "border":   "#e4e4e4",
}

# ── Street classification ─────────────────────────────────────────────────────
# Each entry: canonical OSM name → (colour key, toggle id, display label)
AFFECTED = {
    "Edwin Street":          ("closure", "edwin",       "Edwin St"),
    "Tramway Street":        ("closure", "tramway",     "Tramway St"),
    "Wentworth Street":      ("closure", "wentworth-n", "Wentworth St"),
    "Holbeach Avenue":       ("stopgo",  "holbeach",    "Holbeach Ave"),
    "Barden Street":         ("marking", "barden",      "Barden St"),
    "Fanning Street":        ("marking", "fanning",     "Fanning St"),
    "Hart Street":           ("marking", "hart",        "Hart St"),
    "Station Street":        ("marking", "station",     "Station St"),
    "Union Street":          ("zone",    "union",       "Union St"),
}

CONTEXT_ARTERIAL = {
    "Princes Highway",
    "Unwins Bridge Road",
    "Sydenham Road",
}

CONTEXT_LOCAL = {
    "South Street",
    "Stanley Street",
    "School Avenue",
    "Burrows Road",
    "Edward Street",
    "Chapel Street",
    "Cheltenham Street",
    "Railway Parade",
}

ALL_WANTED = (set(AFFECTED.keys()) | CONTEXT_ARTERIAL | CONTEXT_LOCAL)


def overpass_query(bbox):
    lat_min, lon_min, lat_max, lon_max = bbox
    bbox_str = f"{lat_min},{lon_min},{lat_max},{lon_max}"
    query = f"""
[out:json][timeout:30];
(
  way["highway"]["name"]({bbox_str});
);
out body;
>;
out skel qt;
"""
    url = "https://overpass-api.de/api/interpreter"
    data = urllib.parse.urlencode({"data": query}).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("User-Agent", "tempe-latm-dashboard/1.0")
    with urllib.request.urlopen(req, timeout=40) as resp:
        return json.loads(resp.read())


def build_node_map(elements):
    return {e["id"]: (e["lat"], e["lon"])
            for e in elements if e["type"] == "node"}


def project(lat, lon, lat_min, lat_max, lon_min, lon_max):
    """Map lat/lon to SVG coordinates. Y flipped (north = top)."""
    x = PAD + (lon - lon_min) / (lon_max - lon_min) * (SVG_W - 2 * PAD)
    y = PAD + (lat_max - lat) / (lat_max - lat_min) * (SVG_H - 2 * PAD)
    return x, y


def midpoint(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def polyline_str(pts):
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)


def label_angle(pts):
    """Angle in degrees for a label along a polyline (first→last point)."""
    if len(pts) < 2:
        return 0
    dx = pts[-1][0] - pts[0][0]
    dy = pts[-1][1] - pts[0][1]
    angle = math.degrees(math.atan2(dy, dx))
    # Keep text readable (not upside-down)
    if angle > 90 or angle < -90:
        angle += 180
    return angle


def render_svg(ways_by_name, nodes, bbox):
    lat_min, lon_min, lat_max, lon_max = bbox

    def proj(lat, lon):
        return project(lat, lon, lat_min, lat_max, lon_min, lon_max)

    lines = []
    lines.append(
        f'<svg class="tempe-map" viewBox="0 0 {SVG_W} {SVG_H}" '
        f'xmlns="http://www.w3.org/2000/svg" '
        f'role="img" aria-label="Map of Tempe South works area">'
    )

    # Background
    lines.append(
        f'  <rect width="{SVG_W}" height="{SVG_H}" rx="8" '
        f'fill="{COLOURS["bg"]}" stroke="{COLOURS["border"]}" stroke-width="1"/>'
    )

    # ── Draw context arterial roads (thicker, mid-grey) ──────────────────────
    for name in CONTEXT_ARTERIAL:
        if name not in ways_by_name:
            continue
        for nids in ways_by_name[name]:
            pts = [proj(*nodes[n]) for n in nids if n in nodes]
            if len(pts) < 2:
                continue
            lines.append(
                f'  <polyline points="{polyline_str(pts)}" '
                f'fill="none" stroke="{COLOURS["arterial"]}" stroke-width="4" '
                f'stroke-linecap="round" stroke-linejoin="round"/>'
            )

    # ── Draw context local roads (thin, light-grey) ───────────────────────────
    for name in CONTEXT_LOCAL:
        if name not in ways_by_name:
            continue
        for nids in ways_by_name[name]:
            pts = [proj(*nodes[n]) for n in nids if n in nodes]
            if len(pts) < 2:
                continue
            lines.append(
                f'  <polyline points="{polyline_str(pts)}" '
                f'fill="none" stroke="{COLOURS["context"]}" stroke-width="2" '
                f'stroke-linecap="round" stroke-linejoin="round"/>'
            )

    # ── Draw affected streets (coloured, thicker, clickable) ─────────────────
    label_positions = []   # (x, y, angle, label, colour)

    for name, (col_key, toggle_id, display) in AFFECTED.items():
        if name not in ways_by_name:
            continue
        colour = COLOURS[col_key]
        all_pts = []
        for nids in ways_by_name[name]:
            pts = [proj(*nodes[n]) for n in nids if n in nodes]
            if len(pts) < 2:
                continue
            all_pts.extend(pts)
            lines.append(
                f'  <polyline points="{polyline_str(pts)}" '
                f'fill="none" stroke="{colour}" stroke-width="5" '
                f'stroke-linecap="round" stroke-linejoin="round" '
                f'class="street-path" data-street="{toggle_id}" '
                f'onclick="toggle(\'{toggle_id}\')" '
                f'role="button" aria-label="{display}">'
                f'<title>{display}</title></polyline>'
            )

        if all_pts:
            mx, my = midpoint(all_pts)
            angle = label_angle(all_pts)
            label_positions.append((mx, my, angle, display, colour))

    # ── Street name labels ────────────────────────────────────────────────────
    for mx, my, angle, label, colour in label_positions:
        # White halo background for readability
        lines.append(
            f'  <text x="{mx:.1f}" y="{my:.1f}" '
            f'transform="rotate({angle:.1f},{mx:.1f},{my:.1f})" '
            f'text-anchor="middle" dominant-baseline="auto" '
            f'font-family="-apple-system,BlinkMacSystemFont,\'Segoe UI\',Helvetica,Arial,sans-serif" '
            f'font-size="9.5" font-weight="600" fill="white" '
            f'stroke="white" stroke-width="3" paint-order="stroke" '
            f'dy="-7" pointer-events="none">{label}</text>'
        )
        lines.append(
            f'  <text x="{mx:.1f}" y="{my:.1f}" '
            f'transform="rotate({angle:.1f},{mx:.1f},{my:.1f})" '
            f'text-anchor="middle" dominant-baseline="auto" '
            f'font-family="-apple-system,BlinkMacSystemFont,\'Segoe UI\',Helvetica,Arial,sans-serif" '
            f'font-size="9.5" font-weight="600" fill="{colour}" '
            f'dy="-7" pointer-events="none">{label}</text>'
        )

    # ── Legend ────────────────────────────────────────────────────────────────
    legend_items = [
        ("closure", "Full closure"),
        ("stopgo",  "Stop/slow"),
        ("marking", "Line marking"),
        ("zone",    "Speed zone"),
    ]
    lx, ly = SVG_W - PAD - 130, PAD + 6
    lines.append(
        f'  <rect x="{lx - 8}" y="{ly - 8}" width="142" height="{len(legend_items) * 18 + 12}" '
        f'rx="5" fill="white" fill-opacity="0.88" stroke="{COLOURS["border"]}" stroke-width="1"/>'
    )
    for i, (col_key, label) in enumerate(legend_items):
        colour = COLOURS[col_key]
        iy = ly + i * 18
        lines.append(
            f'  <rect x="{lx}" y="{iy}" width="14" height="6" rx="2" fill="{colour}"/>'
        )
        lines.append(
            f'  <text x="{lx + 19}" y="{iy + 6}" '
            f'font-family="-apple-system,BlinkMacSystemFont,\'Segoe UI\',Helvetica,Arial,sans-serif" '
            f'font-size="9" fill="#444">{label}</text>'
        )

    # ── Compass rose (top-right, inside legend area) ──────────────────────────
    cx, cy = PAD + 20, PAD + 20
    lines.append(
        f'  <text x="{cx}" y="{cy - 10}" text-anchor="middle" '
        f'font-size="10" font-weight="700" fill="#666" '
        f'font-family="-apple-system,BlinkMacSystemFont,\'Segoe UI\',Helvetica,Arial,sans-serif">N</text>'
    )
    lines.append(
        f'  <line x1="{cx}" y1="{cy - 8}" x2="{cx}" y2="{cy + 8}" '
        f'stroke="#888" stroke-width="1.5"/>'
    )
    lines.append(
        f'  <polygon points="{cx},{cy-8} {cx-4},{cy+4} {cx},{cy+1} {cx+4},{cy+4}" '
        f'fill="#555"/>'
    )

    lines.append('</svg>')
    return "\n".join(lines)


def main():
    write_file = "--file" in sys.argv

    print("Querying OpenStreetMap Overpass API...", file=sys.stderr)
    result = overpass_query(BBOX)

    elements = result.get("elements", [])
    nodes = build_node_map(elements)

    # Group ways by name, keeping only wanted street names
    ways_by_name = {}
    for e in elements:
        if e["type"] != "way":
            continue
        name = e.get("tags", {}).get("name", "")
        if name not in ALL_WANTED:
            continue
        ways_by_name.setdefault(name, []).append(e.get("nodes", []))

    found = sorted(ways_by_name.keys())
    print(f"Found {len(found)} matching street names:", file=sys.stderr)
    for n in found:
        count = len(ways_by_name[n])
        tag = "✓ AFFECTED" if n in AFFECTED else "  context"
        print(f"  {tag}  {n} ({count} way{'s' if count > 1 else ''})", file=sys.stderr)

    missing = [n for n in AFFECTED if n not in ways_by_name]
    if missing:
        print(f"\nWARNING — affected streets not found in OSM data:", file=sys.stderr)
        for n in missing:
            print(f"  ✗ {n}", file=sys.stderr)

    svg = render_svg(ways_by_name, nodes, BBOX)

    if write_file:
        out_path = "tempe_map_fragment.svg"
        with open(out_path, "w") as f:
            f.write(svg)
        print(f"\nWritten to {out_path}", file=sys.stderr)
    else:
        print(svg)


if __name__ == "__main__":
    main()
