import argparse
import csv
import heapq
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from openpyxl import load_workbook


@dataclass(frozen=True)
class RouteHop:
    start_site: str
    end_site: str
    fiber_type: str
    distance_km: float
    loss_db: float
    bypass_sites: Tuple[str, ...]


def _to_float(value: object, field_name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid numeric value for '{field_name}': {value!r}") from exc


def _normalize_row(row: Dict[str, object]) -> RouteHop:
    bypass_raw = str(row.get("bypass_sites", "") or "")
    bypass_sites = tuple(site.strip() for site in bypass_raw.split(";") if site.strip())
    return RouteHop(
        start_site=str(row["start_site"]).strip(),
        end_site=str(row["end_site"]).strip(),
        fiber_type=str(row["fiber_type"]).strip().upper(),
        distance_km=_to_float(row["distance_km"], "distance_km"),
        loss_db=_to_float(row["loss_db"], "loss_db"),
        bypass_sites=bypass_sites,
    )


def load_routes(path: str) -> List[RouteHop]:
    source = Path(path)
    if source.suffix.lower() == ".csv":
        with source.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        return [_normalize_row(row) for row in rows]
    if source.suffix.lower() in {".xlsx", ".xlsm"}:
        workbook = load_workbook(source, read_only=True, data_only=True)
        sheet = workbook.active
        raw_rows = list(sheet.iter_rows(values_only=True))
        if not raw_rows:
            return []
        headers = [str(column).strip() for column in raw_rows[0]]
        rows = [dict(zip(headers, row)) for row in raw_rows[1:] if any(cell is not None for cell in row)]
        return [_normalize_row(row) for row in rows]
    raise ValueError("Unsupported route file format. Use .csv or .xlsx/.xlsm")


def _load_json(path: str) -> Dict[str, object]:
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def _build_graph(routes: Iterable[RouteHop]) -> Dict[str, List[Tuple[str, float, float]]]:
    graph: Dict[str, List[Tuple[str, float, float]]] = {}
    for hop in routes:
        graph.setdefault(hop.start_site, []).append((hop.end_site, hop.loss_db, hop.distance_km))
        graph.setdefault(hop.end_site, []).append((hop.start_site, hop.loss_db, hop.distance_km))
    return graph


def _shortest_path_loss(
    graph: Dict[str, List[Tuple[str, float, float]]], source: str, destination: str
) -> Optional[Tuple[float, float, List[str]]]:
    if source not in graph or destination not in graph:
        return None
    queue: List[Tuple[float, float, str, List[str]]] = [(0.0, 0.0, source, [source])]
    visited: Dict[str, float] = {}
    while queue:
        loss, distance, node, path = heapq.heappop(queue)
        if node in visited and visited[node] <= loss:
            continue
        visited[node] = loss
        if node == destination:
            return loss, distance, path
        for neighbor, edge_loss, edge_distance in graph[node]:
            heapq.heappush(queue, (loss + edge_loss, distance + edge_distance, neighbor, [*path, neighbor]))
    return None


def _find_profile(
    dco_spec: Dict[str, object], speed_gbps: int, modulation: str, module_name: Optional[str]
) -> Optional[Dict[str, object]]:
    for module in dco_spec.get("modules", []):
        if module_name and module.get("name") != module_name:
            continue
        for profile in module.get("supported_profiles", []):
            if int(profile.get("speed_gbps", -1)) == speed_gbps and str(profile.get("modulation", "")).upper() == modulation:
                return profile
    return None


def simulate_reachability(
    dco_spec: Dict[str, object],
    roadm_spec: Dict[str, object],
    routes: List[RouteHop],
    wavelengths: List[Dict[str, object]],
    amplifier_config: Optional[Dict[str, float]] = None,
) -> List[Dict[str, object]]:
    graph = _build_graph(routes)
    amplifier_config = amplifier_config or {}
    boost_gain = float(amplifier_config.get("boost_gain_db", 0.0))
    preamp_gain = float(amplifier_config.get("preamp_gain_db", 0.0))
    roadm_node_loss = float(roadm_spec.get("node_loss_db", 0.0))

    results: List[Dict[str, object]] = []
    for wavelength in wavelengths:
        channel_id = wavelength.get("id")
        source = str(wavelength["source_site"])
        destination = str(wavelength["destination_site"])
        speed = int(wavelength["speed_gbps"])
        modulation = str(wavelength["modulation"]).upper()
        module_name = wavelength.get("dco_module")

        path_details = _shortest_path_loss(graph, source, destination)
        profile = _find_profile(dco_spec, speed, modulation, module_name)
        if path_details is None:
            results.append(
                {"id": channel_id, "reachable": False, "reason": "No route found", "path": [], "net_loss_db": None}
            )
            continue
        if profile is None:
            results.append(
                {
                    "id": channel_id,
                    "reachable": False,
                    "reason": "No DCO profile matched speed/modulation",
                    "path": path_details[2],
                    "net_loss_db": None,
                }
            )
            continue

        path_loss, distance, path = path_details
        transit_nodes = max(len(path) - 2, 0)
        net_loss = path_loss + (transit_nodes * roadm_node_loss) - (boost_gain + preamp_gain)
        max_path_loss = float(profile["max_path_loss_db"])
        max_distance = float(profile["max_distance_km"])
        reachable = net_loss <= max_path_loss and distance <= max_distance
        reason = "OK" if reachable else "Exceeded distance or loss limits"
        results.append(
            {
                "id": channel_id,
                "reachable": reachable,
                "reason": reason,
                "path": path,
                "distance_km": round(distance, 3),
                "net_loss_db": round(net_loss, 3),
                "max_distance_km": max_distance,
                "max_path_loss_db": max_path_loss,
            }
        )
    return results


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ROADM Reachability Simulator")
    parser.add_argument("--dco-spec", required=True, help="Path to DCO module spec JSON")
    parser.add_argument("--roadm-spec", required=True, help="Path to ROADM spec JSON")
    parser.add_argument("--routes", required=True, help="Path to route file (.csv/.xlsx)")
    parser.add_argument("--wavelength-plan", required=True, help="Path to wavelength plan JSON")
    parser.add_argument("--output", help="Optional output report file path")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    dco_spec = _load_json(args.dco_spec)
    roadm_spec = _load_json(args.roadm_spec)
    wavelength_plan = _load_json(args.wavelength_plan)
    routes = load_routes(args.routes)
    results = simulate_reachability(
        dco_spec=dco_spec,
        roadm_spec=roadm_spec,
        routes=routes,
        wavelengths=wavelength_plan.get("wavelengths", []),
        amplifier_config=wavelength_plan.get("amplifiers"),
    )
    output = {"summary": {"total": len(results), "reachable": sum(1 for row in results if row["reachable"])}, "results": results}
    if args.output:
        with Path(args.output).open("w", encoding="utf-8") as handle:
            json.dump(output, handle, indent=2)
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
