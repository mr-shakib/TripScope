"""A tiny taxi-zone shapefile in the same projection as TLC's (NY State Plane Long Island, US survey feet)."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import shapefile

PRJ = (
    'PROJCS["NAD_1983_StatePlane_New_York_Long_Island_FIPS_3104_Feet",GEOGCS["GCS_North_American_1983",'
    'DATUM["D_North_American_1983",SPHEROID["GRS_1980",6378137.0,298.257222101]],PRIMEM["Greenwich",0.0],'
    'UNIT["Degree",0.0174532925199433]],PROJECTION["Lambert_Conformal_Conic"],'
    'PARAMETER["False_Easting",984250.0],PARAMETER["False_Northing",0.0],PARAMETER["Central_Meridian",-74.0],'
    'PARAMETER["Standard_Parallel_1",41.0333333333333],PARAMETER["Standard_Parallel_2",40.6666666666667],'
    'PARAMETER["Latitude_Of_Origin",40.1666666666667],UNIT["US survey foot",0.304800609601219]]'
)


def _square(x: float, y: float, size: float = 4000) -> list[list[tuple[float, float]]]:
    return [[(x, y), (x, y + size), (x + size, y + size), (x + size, y), (x, y)]]


# (LocationID, zone, borough, lower-left corners in state-plane feet). Zone 161 has two parts.
ZONES = [
    (132, "JFK Airport", "Queens", [(1030700, 165300)]),
    (161, "Midtown Center", "Manhattan", [(988000, 214000), (996000, 214000)]),
    (236, "Upper East Side North", "Manhattan", [(995000, 222000)]),
]


def write_zone_zip(path: Path, *, prj: str = PRJ, far_away: bool = False) -> Path:
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    with shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=shapefile.POLYGON) as writer:
        writer.field("LocationID", "N", size=9)
        writer.field("zone", "C", size=80)
        writer.field("borough", "C", size=80)
        for location_id, zone, borough, corners in ZONES:
            for x, y in corners:
                shift = 3_000_000 if far_away else 0  # hundreds of miles east: must be rejected
                writer.poly(_square(x + shift, y))
                writer.record(location_id, zone, borough)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("taxi_zones/taxi_zones.shp", shp.getvalue())
        archive.writestr("taxi_zones/taxi_zones.shx", shx.getvalue())
        archive.writestr("taxi_zones/taxi_zones.dbf", dbf.getvalue())
        archive.writestr("taxi_zones/taxi_zones.prj", prj)
    return path
