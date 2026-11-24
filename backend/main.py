"""
Generator zielonych tras spacerowych / biegowych (Wersja OFFLINE).
Korzysta z pliku opolskie.osm.pbf i biblioteki pyrosm, całkowicie eliminując Overpass API.
"""
from __future__ import annotations

import logging
import math
from contextlib import asynccontextmanager
from pathlib import Path

import geopandas as gpd
import networkx as nx
import osmnx as ox
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pyproj import Transformer
from pyrosm import OSM


# KONFIGURACJA
BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR.parent / "frontend"
PBF_FILE = BASE_DIR / "opolskie.osm.pbf"

# Opole (środek) i promień w metrach
CENTER = (50.6751, 17.9213)   # 
DIST = 5000                   # metry

WALK_SPEED_KMH = 5.0
MAX_SNAP_DISTANCE_M = 300

GREEN_BUFFER_M = 25
NOISE_BUFFERS_M = {
    "motorway": 60, "motorway_link": 40,
    "trunk": 55, "trunk_link": 35,
    "primary": 45, "primary_link": 30,
    "secondary": 35, "secondary_link": 25,
    "tertiary": 20,
}

ROAD_CLASS_PENALTY = {
    "motorway": 2.0, "motorway_link": 1.5,
    "trunk": 1.5, "trunk_link": 1.0,
    "primary": 1.0, "primary_link": 0.7,
    "secondary": 0.8, "secondary_link": 0.5,
    "tertiary": 0.4,
}

NOISE_PENALTY = 2.0
GREEN_BONUS = 0.6

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("green-route-offline")

STATE: dict = {}


# ANALIZA PRZESTRZENNA
def get_bounding_box(lat, lon, dist_m):
    # Przelicza środek i promień na Bounding Box w WGS84 do przycięcia mapy
    earth_r = 6371000
    lat_rad = math.radians(lat)
    lon_rad = math.radians(lon)
    d_lat = dist_m / earth_r
    d_lon = dist_m / (earth_r * math.cos(lat_rad))
    
    min_lat = math.degrees(lat_rad - d_lat)
    max_lat = math.degrees(lat_rad + d_lat)
    min_lon = math.degrees(lon_rad - d_lon)
    max_lon = math.degrees(lon_rad + d_lon)
    return [min_lon, min_lat, max_lon, max_lat]

def zone_fraction(edges: gpd.GeoDataFrame, zones: gpd.GeoDataFrame) -> pd.Series:
    result = pd.Series(0.0, index=edges.index)
    if zones.empty:
        return result

    hits = gpd.sjoin(edges[["geometry"]], zones[["geometry"]], how="inner", predicate="intersects")
    idx = hits.index.unique()
    if len(idx) == 0:
        return result

    union = zones.geometry.union_all()
    sub = edges.loc[idx]
    inter_len = sub.geometry.intersection(union).length
    frac = (inter_len / sub.geometry.length).replace([math.inf, -math.inf], 0).fillna(0).clip(0, 1)
    result.loc[frac.index] = frac
    return result

def road_penalty(highway) -> float:
    values = highway if isinstance(highway, list) else [highway]
    return max((ROAD_CLASS_PENALTY.get(v, 0.0) for v in values), default=0.0)

def build_state() -> None:
    if not PBF_FILE.exists():
        raise FileNotFoundError(f"Brak pliku mapy: {PBF_FILE}. Pobierz .osm.pbf i umieść w folderze backend.")

    bbox = get_bounding_box(CENTER[0], CENTER[1], DIST)
    log.info(f"Inicjalizacja Pyrosm dla pliku: {PBF_FILE.name} (obszar przycięty: {bbox})...")
    osm = OSM(str(PBF_FILE), bounding_box=bbox)

    log.info("Ekstrakcja siatki drogowej dla pieszych...")
    nodes, edges = osm.get_network(network_type="walking", nodes=True)
    G = osm.to_graph(nodes, edges, graph_type="networkx", direction="oneway") # Piesi chodzą w obie strony po krawędziach oneway wg OSMnx
    
    # Rzutowanie grafu na układ metryczny, aby bufory i długości były w metrach
    G = ox.project_graph(G)
    crs = G.graph["crs"]

    log.info("Ekstrakcja terenów zielonych...")
    green_filter = {
        "leisure": ["park", "garden", "nature_reserve", "recreation_ground"],
        "landuse": ["forest", "grass", "meadow", "village_green", "recreation_ground", "orchard"],
        "natural": ["wood", "scrub", "grassland", "water", "wetland"]
    }
    green_gdf = osm.get_data_by_custom_criteria(custom_filter=green_filter)
    if green_gdf is not None and not green_gdf.empty:
        green_gdf = green_gdf[green_gdf.geom_type.isin(["Polygon", "MultiPolygon"])].to_crs(crs)
        green_geoms = green_gdf.geometry.make_valid().buffer(GREEN_BUFFER_M).reset_index(drop=True)
        green = gpd.GeoDataFrame(geometry=green_geoms, crs=crs)
    else:
        green = gpd.GeoDataFrame(geometry=[], crs=crs)

    log.info("Ekstrakcja ruchliwych ulic (hałas)...")
    noise_filter = {"highway": list(NOISE_BUFFERS_M.keys())}
    noise_gdf = osm.get_data_by_custom_criteria(custom_filter=noise_filter)
    if noise_gdf is not None and not noise_gdf.empty:
        noise_gdf = noise_gdf[noise_gdf.geom_type.isin(["LineString", "MultiLineString"])].to_crs(crs)
        radii = noise_gdf["highway"].map(NOISE_BUFFERS_M)
        mask = radii.notna()
        noise_geoms = [geom.buffer(r) for geom, r in zip(noise_gdf.geometry[mask], radii[mask])]
        noise = gpd.GeoDataFrame(geometry=noise_geoms, crs=crs)
    else:
        noise = gpd.GeoDataFrame(geometry=[], crs=crs)

    log.info("Spatial join: integracja grafu ze strefami przestrzennymi...")
    # Wyodrębnienie krawędzi do GeoDataFrame w celu operacji wektorowych
    gdf_nodes, gdf_edges = ox.graph_to_gdfs(G)
    gdf_edges = gdf_edges.reset_index()
    
    gdf_edges["green_frac"] = zone_fraction(gdf_edges, green)
    gdf_edges["noise_frac"] = zone_fraction(gdf_edges, noise)
    gdf_edges["road_pen"] = gdf_edges["highway"].apply(road_penalty)

    # Aktualizacja wag bezpośrednio w krawędziach grafu NetworkX
    for row in gdf_edges.itertuples(index=False):
        data = G[row.u][row.v][row.key]
        data["green_frac"] = float(row.green_frac)
        data["noise_frac"] = float(row.noise_frac)
        data["road_pen"] = float(row.road_pen)

    # Zrzucenie uproszczonych stref do GeoJSON (WGS84) 
    zones_gdf = gpd.GeoDataFrame(
        {"kind": ["green", "noise"]},
        geometry=[
            green.geometry.union_all().simplify(2) if not green.empty else None,
            noise.geometry.union_all().simplify(2) if not noise.empty else None,
        ],
        crs=crs,
    ).dropna(subset=["geometry"]).to_crs(4326)

    nodes_wgs = gdf_nodes.to_crs(4326)
    minx, miny, maxx, maxy = nodes_wgs.total_bounds

    STATE.update(
        G=G,
        zones_json=zones_gdf.to_json(),
        bounds=[[miny, minx], [maxy, maxx]],
        to_proj=Transformer.from_crs(4326, crs, always_xy=True),
        to_wgs=Transformer.from_crs(crs, 4326, always_xy=True),
    )
    log.info("Inicjalizacja zakończona sukcesem. Graf zoptymalizowany pod algorytmy pathfindingu.")


# MODEL WAG + ALGORYTM
def edge_cost(d: dict, g: float) -> float:
    penalty = NOISE_PENALTY * d.get("noise_frac", 0.0) + d.get("road_pen", 0.0)
    bonus = GREEN_BONUS * d.get("green_frac", 0.0)
    return d["length"] * (1.0 + g * penalty) * (1.0 - g * bonus)

def make_weight(g: float):
    def weight(u, v, keydict):
        return min(edge_cost(d, g) for d in keydict.values())
    return weight

def make_heuristic(G: nx.MultiDiGraph, target: int, g: float):
    tx, ty = G.nodes[target]["x"], G.nodes[target]["y"]
    factor = max(1.0 - g * GREEN_BONUS, 0.05) * 0.98

    def h(u, _target):
        n = G.nodes[u]
        return factor * math.hypot(n["x"] - tx, n["y"] - ty)

    return h

def describe_path(G: nx.MultiDiGraph, path: list[int], g: float):
    pts: list[tuple[float, float]] = []
    total = green_len = noise_len = 0.0

    for u, v in zip(path[:-1], path[1:]):
        keydict = G[u][v]
        key = min(keydict, key=lambda k: edge_cost(keydict[k], g))
        d = keydict[key]

        ux, uy = G.nodes[u]["x"], G.nodes[u]["y"]
        geom = d.get("geometry")
        if geom is None:
            coords = [(ux, uy), (G.nodes[v]["x"], G.nodes[v]["y"])]
        else:
            coords = list(geom.coords)
            if math.hypot(coords[0][0] - ux, coords[0][1] - uy) > math.hypot(coords[-1][0] - ux, coords[-1][1] - uy):
                coords.reverse()
        pts.extend(coords if not pts else coords[1:])

        total += d["length"]
        green_len += d["length"] * d.get("green_frac", 0.0)
        noise_len += d["length"] * d.get("noise_frac", 0.0)

    xs, ys = zip(*pts)
    lons, lats = STATE["to_wgs"].transform(list(xs), list(ys))
    coordinates = [[round(lo, 6), round(la, 6)] for lo, la in zip(lons, lats)]

    stats = {
        "length_m": round(total, 1),
        "minutes": round(total / (WALK_SPEED_KMH / 3.6) / 60, 1),
        "green_share": round(green_len / total, 3) if total else 0.0,
        "noise_share": round(noise_len / total, 3) if total else 0.0,
    }
    return coordinates, stats

def snap(lat: float, lon: float, label: str) -> int:
    G = STATE["G"]
    x, y = STATE["to_proj"].transform(lon, lat)
    node, dist = ox.distance.nearest_nodes(G, X=x, Y=y, return_dist=True)
    if dist > MAX_SNAP_DISTANCE_M:
        raise HTTPException(
            status_code=400,
            detail=f"Punkt „{label}” leży poza obszarem. (Najbliższy węzeł: {dist:.0f} m)",
        )
    return int(node)


# API
@asynccontextmanager
async def lifespan(_: FastAPI):
    build_state()
    yield

app = FastAPI(title="Green Route API Offline", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.get("/info")
def info():
    return {"center": CENTER, "bounds": STATE["bounds"]}

@app.get("/zones")
def zones():
    return Response(content=STATE["zones_json"], media_type="application/geo+json")

@app.get("/route")
def get_route(
    start_lat: float = Query(...),
    start_lon: float = Query(...),
    end_lat: float = Query(...),
    end_lon: float = Query(...),
    green: float = Query(0.8, ge=0.0, le=1.0),
):
    G = STATE["G"]
    s = snap(start_lat, start_lon, "start")
    t = snap(end_lat, end_lon, "koniec")
    if s == t:
        raise HTTPException(status_code=400, detail="Start i koniec są w tym samym miejscu.")

    try:
        green_path = nx.astar_path(G, s, t, heuristic=make_heuristic(G, t, green), weight=make_weight(green))
        short_path = nx.shortest_path(G, s, t, weight="length")
    except nx.NetworkXNoPath:
        raise HTTPException(status_code=404, detail="Trasa niemożliwa do znalezienia w tym grafie.")

    features = []
    for kind, path, g in (("green", green_path, green), ("shortest", short_path, 0.0)):
        coords, stats = describe_path(G, path, g)
        features.append({
            "type": "Feature",
            "properties": {"kind": kind, **stats},
            "geometry": {"type": "LineString", "coordinates": coords},
        })
    return {"type": "FeatureCollection", "features": features}

app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")