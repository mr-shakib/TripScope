"""TLC taxi-zone boundaries for the map: shapefile (NY State Plane, US feet) → simplified WGS84 GeoJSON.

The archive is treated as data: member sizes are capped, only the expected .shp/.shx/.dbf/.prj members are
read, the projection must be the documented State Plane Long Island system, and every resulting zone must lie
within New York City's bounding box.
"""

from __future__ import annotations

import io
import json
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any

import pyproj
import shapefile
import shapely
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

from tripscope.core.errors import SourceValidationError

EXPECTED_PROJECTION = "New_York_Long_Island"
NYC_BOUNDS = (-74.30, 40.45, -73.65, 40.95)  # lon/lat with a small margin around the five boroughs
MAX_MEMBER_BYTES = 25 * 1024 * 1024


def _member(archive: zipfile.ZipFile, suffix: str) -> bytes:
    matches = [i for i in archive.infolist() if i.filename.lower().endswith(suffix) and not i.is_dir()]
    if len(matches) != 1:
        raise SourceValidationError(f"zone archive must contain exactly one {suffix} file")
    info = matches[0]
    if info.file_size > MAX_MEMBER_BYTES:
        raise SourceValidationError(f"{info.filename} is unexpectedly large")
    return archive.read(info)


def _round(value: Any, digits: int = 5) -> Any:
    if isinstance(value, float):
        return round(value, digits)
    if isinstance(value, list | tuple):
        return [_round(v, digits) for v in value]
    return value


def build_zone_geojson(zip_path: Path, *, tolerance: float = 0.00012) -> dict[str, Any]:
    with zipfile.ZipFile(zip_path) as archive:
        prj = _member(archive, ".prj").decode("utf-8", errors="replace")
        if EXPECTED_PROJECTION not in prj:
            raise SourceValidationError(
                "zone shapefile is not in the expected NY State Plane (Long Island) system"
            )
        reader = shapefile.Reader(
            shp=io.BytesIO(_member(archive, ".shp")),
            shx=io.BytesIO(_member(archive, ".shx")),
            dbf=io.BytesIO(_member(archive, ".dbf")),
        )
    field_names = {f[0] for f in reader.fields[1:]}
    if not {"LocationID", "zone", "borough"} <= field_names:
        raise SourceValidationError(
            f"zone shapefile lacks LocationID/zone/borough fields: {sorted(field_names)}"
        )

    to_wgs84 = pyproj.Transformer.from_crs(pyproj.CRS.from_wkt(prj), "EPSG:4326", always_xy=True)
    parts: dict[int, list[Any]] = defaultdict(list)
    names: dict[int, dict[str, str]] = {}
    for item in reader.iterShapeRecords():
        record = item.record.as_dict()
        location_id = int(record["LocationID"])
        geometry = shape(item.shape.__geo_interface__)
        parts[location_id].append(shapely.transform(geometry, to_wgs84.transform, interleaved=False))
        names[location_id] = {"zone": str(record["zone"]).strip(), "borough": str(record["borough"]).strip()}

    features = []
    min_lon, min_lat, max_lon, max_lat = NYC_BOUNDS
    for location_id in sorted(parts):
        # Some zones are stored as several records (islands); merge them into one feature.
        geometry = unary_union(parts[location_id]).simplify(tolerance, preserve_topology=True)
        lon0, lat0, lon1, lat1 = geometry.bounds
        if lon0 < min_lon or lat0 < min_lat or lon1 > max_lon or lat1 > max_lat:
            raise SourceValidationError(f"zone {location_id} falls outside New York City after reprojection")
        geo = mapping(geometry)
        features.append(
            {
                "type": "Feature",
                "id": location_id,
                "properties": {"location_id": location_id, **names[location_id]},
                "geometry": {"type": geo["type"], "coordinates": _round(geo["coordinates"])},
            }
        )
    if not features:
        raise SourceValidationError("zone shapefile contains no zones")
    return {"type": "FeatureCollection", "features": features}


def geojson_bytes(collection: dict[str, Any]) -> bytes:
    return json.dumps(collection, separators=(",", ":")).encode()
