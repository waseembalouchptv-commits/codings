"""Build the bundled map data (docedit/data/geo_region.json) from Natural Earth.

Natural Earth is public domain (https://www.naturalearthdata.com/about/terms-of-use/).
Country outlines use Natural Earth's Pakistan point-of-view layer
(ne_10m_admin_0_countries_pak). Always verify disputed-boundary depictions against
official Survey of Pakistan maps before publishing.

Usage:
    python tools/build_geodata.py <dir containing the downloaded .geojson files>
"""
import json
import sys
from pathlib import Path

from shapely.geometry import box, mapping, shape
from shapely.ops import unary_union

BBOX = box(44.0, 12.0, 90.0, 44.0)  # Gulf/Arabian Sea to Tibet: everything the maps can pan to
OUT = Path(__file__).resolve().parent.parent / "docedit" / "data" / "geo_region.json"

PROVINCE_NAMES = {
    "Baluchistan": "Balochistan",
    "Sind": "Sindh",
    "Punjab": "Punjab",
    "K.P.": "Khyber Pakhtunkhwa",
    "F.A.T.A.": "Khyber Pakhtunkhwa",  # merged into KP in 2018
    "Northern Areas": "Gilgit-Baltistan",
    "Azad Kashmir": "Azad Jammu and Kashmir",
    "F.C.T.": "Islamabad Capital Territory",
}


def rings(geom, tol):
    """Simplify and flatten a (Multi)Polygon into a list of exterior rings."""
    geom = geom.simplify(tol, preserve_topology=True)
    polys = [geom] if geom.geom_type == "Polygon" else list(getattr(geom, "geoms", []))
    out = []
    for p in polys:
        if p.geom_type != "Polygon" or p.area < tol * tol * 4:
            continue
        out.append([[round(x, 3), round(y, 3)] for x, y in p.exterior.coords])
    return out


def lines(geom, tol):
    geom = geom.simplify(tol)
    parts = [geom] if geom.geom_type == "LineString" else list(getattr(geom, "geoms", []))
    return [[[round(x, 3), round(y, 3)] for x, y in ln.coords] for ln in parts if ln.geom_type == "LineString"]


def main(src: Path):
    load = lambda name: json.load(open(src / f"{name}.geojson"))
    data = {"source": "Natural Earth (public domain); Pakistan POV admin-0 boundaries",
            "countries": [], "provinces": [], "rivers": [], "regions": [], "places": []}

    for f in load("ne_10m_admin_0_countries_pak")["features"]:
        g = shape(f["geometry"])
        if g.intersects(BBOX):
            data["countries"].append({"name": f["properties"]["NAME"], "iso": f["properties"]["ADM0_A3"],
                                      "rings": rings(g.intersection(BBOX.buffer(4)), 0.02)})

    groups = {}
    for f in load("ne_10m_admin_1_states_provinces")["features"]:
        p = f["properties"]
        if p["adm0_a3"] == "PAK":
            groups.setdefault(PROVINCE_NAMES.get(p["name"], p["name"]), []).append(shape(f["geometry"]))
    for name, geoms in groups.items():
        data["provinces"].append({"name": name, "country": "Pakistan", "rings": rings(unary_union(geoms), 0.01)})

    for f in load("ne_50m_rivers_lake_centerlines")["features"]:
        g = shape(f["geometry"])
        if g.intersects(BBOX) and f["properties"].get("name"):
            data["rivers"].append({"name": f["properties"]["name"], "lines": lines(g.intersection(BBOX), 0.02)})

    for f in load("ne_10m_geography_regions_polys")["features"]:
        p = f["properties"]
        g = shape(f["geometry"])
        if g.intersects(BBOX) and p["FEATURECLA"] in ("Range/mtn", "Desert", "Delta", "Valley", "Plateau", "Geoarea"):
            c = g.representative_point()
            data["regions"].append({"name": p["NAME_EN"] or p["NAME"], "kind": p["FEATURECLA"],
                                    "lon": round(c.x, 3), "lat": round(c.y, 3),
                                    "rings": rings(g, 0.03)})

    for f in load("ne_10m_populated_places_simple")["features"]:
        p = f["properties"]
        g = shape(f["geometry"])
        if g.within(BBOX) and (p["adm0name"] == "Pakistan" or p["scalerank"] <= 3):
            data["places"].append({"name": p["name"], "country": p["adm0name"], "kind": "city",
                                   "lon": round(g.x, 4), "lat": round(g.y, 4),
                                   "rank": p["scalerank"], "capital": bool(p.get("adm0cap"))})

    OUT.write_text(json.dumps(data, separators=(",", ":")))
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "."))
