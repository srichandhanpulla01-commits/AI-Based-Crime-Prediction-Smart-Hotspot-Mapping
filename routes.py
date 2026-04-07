import math


def distance(point_a, point_b):
    """Return Euclidean distance between two latitude/longitude points."""
    return math.sqrt((point_a[0] - point_b[0]) ** 2 + (point_a[1] - point_b[1]) ** 2)


def generate_patrol_route(zones, start_point=None, close_loop=True):
    """
    Build a simple nearest-neighbor patrol route across hotspot centers.
    """
    if not zones:
        return []

    remaining = [tuple(point) for point in zones]
    route = []

    if start_point is not None:
        current = tuple(start_point)
        route.append(current)
    else:
        current = remaining.pop(0)
        route.append(current)

    while remaining:
        nearest = min(remaining, key=lambda point: distance(current, point))
        route.append(nearest)
        remaining.remove(nearest)
        current = nearest

    if close_loop and len(route) > 1:
        route.append(route[0])

    return route


def get_high_risk_zones(data, top_n=3):
    """
    Return the centroid of the densest clusters in descending risk order.
    """
    if data.empty or "cluster" not in data.columns:
        return []

    cluster_counts = data["cluster"].value_counts().head(top_n)
    zones = []

    for cluster_id in cluster_counts.index:
        cluster_rows = data[data["cluster"] == cluster_id]
        zones.append(
            (
                round(cluster_rows["latitude"].mean(), 6),
                round(cluster_rows["longitude"].mean(), 6),
            )
        )

    return zones
