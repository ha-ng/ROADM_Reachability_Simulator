import argparse
import csv
import heapq
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from openpyxl import load_workbook

ROADM_ROLES = {"コア", "OLT設置"}
BYPASS_ROLE = "通過"


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
        expected_headers = {"start_site", "end_site", "fiber_type", "distance_km", "loss_db"}
        if expected_headers.issubset(set(headers)):
            rows = [dict(zip(headers, row)) for row in raw_rows[1:] if any(cell is not None for cell in row)]
            return [_normalize_row(row) for row in rows]
        return _parse_japanese_section_sheet(raw_rows)
    raise ValueError("Unsupported route file format. Use .csv or .xlsx/.xlsm")


def _parse_japanese_section_sheet(raw_rows: List[Tuple[object, ...]]) -> List[RouteHop]:
    """
    Parse section-based route sheets where:
      A=start site, C=start role, D=end site, F=end role, H=section loss
    and aggregate contiguous sections into ROADM-to-ROADM hops.

    Additional design rule:
      +3 dB for each bypass site (role="通過") between two ROADM endpoints.
    """
    sections: List[Dict[str, object]] = []
    for row in raw_rows:
        if len(row) < 8:
            continue
        start_site = row[0]
        start_role = row[2]
        end_site = row[3]
        end_role = row[5]
        distance_km = row[6]
        loss_db = row[7]

        if not start_site or not end_site:
            continue
        try:
            distance_val = _to_float(distance_km, "distance_km")
            loss_val = _to_float(loss_db, "loss_db")
        except ValueError:
            # Skip title/header rows or malformed rows.
            continue

        sections.append(
            {
                "start_site": str(start_site).strip(),
                "start_role": str(start_role).strip(),
                "end_site": str(end_site).strip(),
                "end_role": str(end_role).strip(),
                "distance_km": distance_val,
                "loss_db": loss_val,
            }
        )

    if not sections:
        raise ValueError("No valid section rows found in route workbook")

    aggregated: List[RouteHop] = []
    i = 0
    while i < len(sections):
        section = sections[i]
        if section["start_role"] not in ROADM_ROLES:
            i += 1
            continue

        agg_start = str(section["start_site"])
        agg_end = str(section["end_site"])
        total_distance = float(section["distance_km"])
        total_loss = float(section["loss_db"])
        bypass_sites: List[str] = []
        if section["end_role"] == BYPASS_ROLE:
            bypass_sites.append(agg_end)

        j = i
        while section["end_role"] not in ROADM_ROLES:
            j += 1
            if j >= len(sections):
                raise ValueError(f"Section chain from '{agg_start}' does not terminate at a ROADM site")

            next_section = sections[j]
            if next_section["start_site"] != section["end_site"]:
                raise ValueError(
                    "Section rows are not contiguous. "
                    f"Expected start '{section['end_site']}', got '{next_section['start_site']}'"
                )

            section = next_section
            agg_end = str(section["end_site"])
            total_distance += float(section["distance_km"])
            total_loss += float(section["loss_db"])
            if section["end_role"] == BYPASS_ROLE:
                bypass_sites.append(agg_end)

        bypass_penalty_db = 3.0 * len(bypass_sites)
        aggregated.append(
            RouteHop(
                start_site=agg_start,
                end_site=agg_end,
                fiber_type="SMF",
                distance_km=total_distance,
                loss_db=total_loss + bypass_penalty_db,
                bypass_sites=tuple(bypass_sites),
            )
        )
        i = j + 1

    if not aggregated:
        raise ValueError("No ROADM-to-ROADM segments could be built from section sheet")
    return aggregated


def _load_json(path: str) -> Dict[str, object]:
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def _build_graph(routes: Iterable[RouteHop]) -> Dict[str, List[Tuple[str, float, float]]]:
    graph: Dict[str, List[Tuple[str, float, float]]] = {}
    for hop in routes:
        graph.setdefault(hop.start_site, []).append((hop.end_site, hop.loss_db, hop.distance_km))
        graph.setdefault(hop.end_site, []).append((hop.start_site, hop.loss_db, hop.distance_km))
    return graph


def _build_route_adjacency(routes: Iterable[RouteHop]) -> Dict[str, List[Tuple[str, RouteHop]]]:
    adjacency: Dict[str, List[Tuple[str, RouteHop]]] = {}
    for hop in routes:
        adjacency.setdefault(hop.start_site, []).append((hop.end_site, hop))
        adjacency.setdefault(hop.end_site, []).append((hop.start_site, hop))
    return adjacency


def _find_input_path(routes: List[RouteHop], source: str, destination: str) -> Optional[List[Tuple[str, str, RouteHop]]]:
    """
    Find the first valid path by traversing input-defined routes in their original order.
    This intentionally avoids best-path optimization and simulates operator-provided pathing.
    """
    adjacency = _build_route_adjacency(routes)
    if source not in adjacency or destination not in adjacency:
        return None

    stack: List[Tuple[str, List[Tuple[str, str, RouteHop]], Set[str]]] = [(source, [], {source})]
    while stack:
        node, path, visited = stack.pop()
        if node == destination:
            return path

        neighbors = adjacency.get(node, [])
        # Reverse-push so traversal respects input order when popped from stack.
        for neighbor, hop in reversed(neighbors):
            if neighbor in visited:
                continue
            stack.append((neighbor, [*path, (node, neighbor, hop)], {*visited, neighbor}))
    return None


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


def _db_to_linear(value_db: float) -> float:
    return math.pow(10.0, value_db / 10.0)


def _linear_to_db(value_linear: float) -> float:
    if value_linear <= 0.0:
        return -999.0
    return 10.0 * math.log10(value_linear)


def _estimate_baud_rate_gbaud(speed_gbps: int, modulation: str) -> float:
    modulation_u = modulation.upper()
    if speed_gbps <= 100 and modulation_u == "QPSK":
        return 32.0
    if speed_gbps <= 200 and modulation_u in {"QPSK", "8QAM", "16QAM"}:
        return 64.0
    if speed_gbps >= 400 and modulation_u in {"16QAM", "32QAM", "64QAM"}:
        return 64.0
    return 64.0


def _modulation_sensitivity(modulation: str) -> float:
    return {
        "QPSK": 1.0,
        "8QAM": 1.2,
        "16QAM": 1.5,
        "32QAM": 1.8,
        "64QAM": 2.1,
    }.get(modulation.upper(), 1.4)


def _planning_scenarios() -> Dict[str, Dict[str, float]]:
    # Envelope-style assumptions for strategy assessment without field calibration.
    return {
        "optimistic": {
            "noise_figure_db": 4.8,
            "osnr_penalty_per_hop_db": 0.55,
            "wss_narrowing_per_hop_ghz": 0.15,
            "clipping_penalty_coef_db_per_ghz": 0.08,
            "ase_inv_osnr_per_hop": 0.00008,
            "gain_flatness_penalty_db": 0.15,
            "spm_penalty_coef": 0.001,
            "xpm_penalty_coef": 0.0006,
            "fwm_penalty_coef": 0.0002,
            "channel_count": 60,
        },
        "nominal": {},
        "conservative": {
            "noise_figure_db": 6.5,
            "osnr_penalty_per_hop_db": 0.9,
            "wss_narrowing_per_hop_ghz": 0.35,
            "clipping_penalty_coef_db_per_ghz": 0.14,
            "ase_inv_osnr_per_hop": 0.0003,
            "gain_flatness_penalty_db": 0.45,
            "spm_penalty_coef": 0.0036,
            "xpm_penalty_coef": 0.0018,
            "fwm_penalty_coef": 0.0008,
            "channel_count": 96,
        },
    }


def _merge_float_config(base: Optional[Dict[str, float]], override: Dict[str, float]) -> Dict[str, float]:
    merged = dict(base or {})
    merged.update(override)
    return merged


def _build_strategy_summary(scenario_runs: Dict[str, Dict[str, object]]) -> Dict[str, object]:
    by_channel: Dict[str, List[Dict[str, object]]] = defaultdict(list)
    for scenario_name, payload in scenario_runs.items():
        for row in payload.get("results", []):
            by_channel[str(row.get("id"))].append({"scenario": scenario_name, **row})

    channels: List[Dict[str, object]] = []
    for channel_id, rows in by_channel.items():
        total = len(rows)
        passed = sum(1 for row in rows if row.get("reachable"))
        osnr_margins = [row.get("osnr_margin_db") for row in rows if row.get("osnr_margin_db") is not None]
        worst_osnr_margin = min(osnr_margins) if osnr_margins else None

        if passed == total and (worst_osnr_margin is None or worst_osnr_margin >= 2.0):
            risk_band = "green"
        elif passed >= max(1, math.ceil(total / 2)):
            risk_band = "yellow"
        else:
            risk_band = "red"

        channels.append(
            {
                "id": channel_id,
                "pass_count": passed,
                "scenario_count": total,
                "pass_rate": round((passed / total) if total else 0.0, 3),
                "worst_osnr_margin_db": round(worst_osnr_margin, 3) if worst_osnr_margin is not None else None,
                "risk_band": risk_band,
            }
        )

    return {
        "channels": channels,
        "portfolio": {
            "total_channels": len(channels),
            "green": sum(1 for row in channels if row["risk_band"] == "green"),
            "yellow": sum(1 for row in channels if row["risk_band"] == "yellow"),
            "red": sum(1 for row in channels if row["risk_band"] == "red"),
        },
    }


def simulate_reachability(
    dco_spec: Dict[str, object],
    roadm_spec: Dict[str, object],
    routes: List[RouteHop],
    wavelengths: List[Dict[str, object]],
    amplifier_config: Optional[Dict[str, float]] = None,
) -> List[Dict[str, object]]:
    amplifier_config = amplifier_config or {}
    boost_gain = float(amplifier_config.get("boost_gain_db", 0.0))
    preamp_gain = float(amplifier_config.get("preamp_gain_db", 0.0))
    per_hop_gain_db = float(amplifier_config.get("per_hop_gain_db", boost_gain + preamp_gain))
    noise_figure_db = float(amplifier_config.get("noise_figure_db", 5.0))
    osnr_penalty_per_hop_db = float(amplifier_config.get("osnr_penalty_per_hop_db", 0.7))
    osnr_penalty_per_km_db = float(amplifier_config.get("osnr_penalty_per_km_db", 0.002))
    wss_narrowing_per_hop_ghz = float(amplifier_config.get("wss_narrowing_per_hop_ghz", 1.5))
    clipping_penalty_coef_db_per_ghz = float(amplifier_config.get("clipping_penalty_coef_db_per_ghz", 0.12))
    ase_inv_osnr_per_hop = float(amplifier_config.get("ase_inv_osnr_per_hop", 0.0001))
    gain_flatness_penalty_db = float(amplifier_config.get("gain_flatness_penalty_db", 0.2))
    spm_penalty_coef = float(amplifier_config.get("spm_penalty_coef", 0.0002))
    xpm_penalty_coef = float(amplifier_config.get("xpm_penalty_coef", 0.00012))
    fwm_penalty_coef = float(amplifier_config.get("fwm_penalty_coef", 0.00005))

    roadm_node_loss = float(roadm_spec.get("node_loss_db", 0.0))

    results: List[Dict[str, object]] = []
    for wavelength in wavelengths:
        channel_id = wavelength.get("id")
        source = str(wavelength["source_site"])
        destination = str(wavelength["destination_site"])
        speed = int(wavelength["speed_gbps"])
        modulation = str(wavelength["modulation"]).upper()
        module_name = wavelength.get("dco_module")
        profile = _find_profile(dco_spec, speed, modulation, module_name)
        baud_rate_gbaud = float(
            wavelength.get(
                "baud_rate_gbaud",
                profile.get("baud_rate_gbaud", _estimate_baud_rate_gbaud(speed, modulation)) if profile else _estimate_baud_rate_gbaud(speed, modulation),
            )
        )
        grid_spacing_ghz = float(wavelength.get("grid_spacing_ghz", profile.get("grid_spacing_ghz", 75.0)) if profile else wavelength.get("grid_spacing_ghz", 75.0))
        filter_shape_factor = float(
            wavelength.get("filter_shape_factor", profile.get("filter_shape_factor", 1.0)) if profile else wavelength.get("filter_shape_factor", 1.0)
        )
        channel_count = int(wavelength.get("channel_count", amplifier_config.get("channel_count", 1)))
        requested_path = _find_input_path(routes, source, destination)
        if requested_path is None:
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
                    "path": [source, destination],
                    "net_loss_db": None,
                }
            )
            continue

        # DCO and channel defaults for impairment simulation.
        launch_power_dbm = float(wavelength.get("launch_power_dbm", profile.get("launch_power_dbm", 0.0)))
        initial_osnr_db = float(wavelength.get("initial_osnr_db", profile.get("initial_osnr_db", 45.0)))
        q_offset_db = float(profile.get("q_offset_db", dco_spec.get("q_offset_db", 6.0)))
        min_osnr_db = profile.get("min_osnr_db")
        min_q_value = profile.get("min_q_value")

        total_distance = 0.0
        total_loss = 0.0
        current_power_dbm = launch_power_dbm
        current_osnr_db = initial_osnr_db
        hops_report: List[Dict[str, object]] = []
        path = [source]

        for idx, (hop_start, hop_end, hop) in enumerate(requested_path, start=1):
            path.append(hop_end)
            total_distance += hop.distance_km

            # Apply span loss and ROADM transit-node loss (except final destination).
            span_loss_db = hop.loss_db
            node_loss_db = roadm_node_loss if idx < len(requested_path) else 0.0
            hop_total_loss_db = span_loss_db + node_loss_db
            total_loss += hop_total_loss_db

            # Power propagation.
            power_in_dbm = current_power_dbm
            power_out_dbm = power_in_dbm - hop_total_loss_db + per_hop_gain_db

            # OSNR propagation using a simple penalty model.
            osnr_in_db = current_osnr_db
            distributed_penalty_db = (
                osnr_penalty_per_hop_db
                + (hop.distance_km * osnr_penalty_per_km_db)
                + max(per_hop_gain_db, 0.0) * 0.01
                + noise_figure_db * 0.05
            )

            # Filter narrowing and clipping across cascaded WSS traversals.
            transit_count = idx - 1
            effective_passband_ghz = max((grid_spacing_ghz * filter_shape_factor) - (transit_count * wss_narrowing_per_hop_ghz), 0.1)
            clipped_bw_ghz = max(baud_rate_gbaud - effective_passband_ghz, 0.0)
            clipping_penalty_db = clipped_bw_ghz * clipping_penalty_coef_db_per_ghz

            # ASE accumulation in linear OSNR domain with gain-flatness contribution.
            osnr_in_linear = _db_to_linear(osnr_in_db)
            ase_inverse_term = (
                ase_inv_osnr_per_hop
                * _db_to_linear(noise_figure_db)
                * _db_to_linear(max(per_hop_gain_db, 0.0))
                * _db_to_linear(gain_flatness_penalty_db)
            )
            osnr_after_ase_linear = 1.0 / ((1.0 / max(osnr_in_linear, 1e-12)) + ase_inverse_term)
            osnr_after_ase_db = _linear_to_db(osnr_after_ase_linear)

            # Nonlinear penalties (SPM/XPM/FWM) with spectral loading dependence.
            modulation_factor = _modulation_sensitivity(modulation)
            loading_ratio = max(channel_count, 1) * (baud_rate_gbaud / max(grid_spacing_ghz, 1e-6))
            power_mw = max(_db_to_linear(power_in_dbm), 1e-12)
            spm_penalty_db = spm_penalty_coef * power_mw * hop.distance_km * modulation_factor
            xpm_penalty_db = xpm_penalty_coef * power_mw * hop.distance_km * loading_ratio * modulation_factor
            fwm_penalty_db = fwm_penalty_coef * power_mw * hop.distance_km * (loading_ratio * loading_ratio)
            nonlinear_penalty_db = spm_penalty_db + xpm_penalty_db + fwm_penalty_db

            osnr_penalty_db = distributed_penalty_db + clipping_penalty_db + nonlinear_penalty_db
            osnr_out_db = osnr_after_ase_db - (distributed_penalty_db + clipping_penalty_db + nonlinear_penalty_db)

            # Q-value approximation from OSNR with configurable implementation offset.
            q_db = osnr_out_db - q_offset_db
            q_value = math.pow(10.0, q_db / 20.0)

            hops_report.append(
                {
                    "hop_index": idx,
                    "start_site": hop_start,
                    "end_site": hop_end,
                    "fiber_type": hop.fiber_type,
                    "distance_km": round(hop.distance_km, 3),
                    "span_loss_db": round(span_loss_db, 3),
                    "roadm_node_loss_db": round(node_loss_db, 3),
                    "hop_total_loss_db": round(hop_total_loss_db, 3),
                    "gain_db": round(per_hop_gain_db, 3),
                    "power_in_dbm": round(power_in_dbm, 3),
                    "power_out_dbm": round(power_out_dbm, 3),
                    "osnr_in_db": round(osnr_in_db, 3),
                    "osnr_after_ase_db": round(osnr_after_ase_db, 3),
                    "osnr_out_db": round(osnr_out_db, 3),
                    "effective_passband_ghz": round(effective_passband_ghz, 3),
                    "clipped_bw_ghz": round(clipped_bw_ghz, 3),
                    "clipping_penalty_db": round(clipping_penalty_db, 4),
                    "distributed_penalty_db": round(distributed_penalty_db, 4),
                    "spm_penalty_db": round(spm_penalty_db, 4),
                    "xpm_penalty_db": round(xpm_penalty_db, 4),
                    "fwm_penalty_db": round(fwm_penalty_db, 4),
                    "nonlinear_penalty_db": round(nonlinear_penalty_db, 4),
                    "osnr_penalty_db": round(osnr_penalty_db, 4),
                    "q_db": round(q_db, 3),
                    "q_value": round(q_value, 4),
                }
            )

            current_power_dbm = power_out_dbm
            current_osnr_db = osnr_out_db

        transit_nodes = max(len(path) - 2, 0)
        net_loss = total_loss - (per_hop_gain_db * len(requested_path))
        max_path_loss_per_hop = float(profile["max_path_loss_db"])
        max_path_loss_total = max_path_loss_per_hop * len(requested_path)
        max_distance = float(profile["max_distance_km"])

        limits_ok = net_loss <= max_path_loss_total and total_distance <= max_distance
        osnr_ok = True if min_osnr_db is None else current_osnr_db >= float(min_osnr_db)
        q_ok = True if min_q_value is None else hops_report[-1]["q_value"] >= float(min_q_value)
        reachable = limits_ok and osnr_ok and q_ok

        if reachable:
            reason = "OK"
        else:
            failed_checks = []
            if not limits_ok:
                failed_checks.append("distance/loss")
            if not osnr_ok:
                failed_checks.append("osnr")
            if not q_ok:
                failed_checks.append("q")
            reason = f"Exceeded {'/'.join(failed_checks)} limits"

        results.append(
            {
                "id": channel_id,
                "reachable": reachable,
                "reason": reason,
                "path": path,
                "distance_km": round(total_distance, 3),
                "net_loss_db": round(net_loss, 3),
                "max_distance_km": max_distance,
                "max_path_loss_db_per_hop": max_path_loss_per_hop,
                "max_path_loss_db_total": round(max_path_loss_total, 3),
                "final_power_dbm": round(current_power_dbm, 3),
                "final_osnr_db": round(current_osnr_db, 3),
                "final_q_value": hops_report[-1]["q_value"] if hops_report else None,
                "min_osnr_db": float(min_osnr_db) if min_osnr_db is not None else None,
                "min_q_value": float(min_q_value) if min_q_value is not None else None,
                "osnr_margin_db": round(current_osnr_db - float(min_osnr_db), 3) if min_osnr_db is not None else None,
                "q_margin": round(hops_report[-1]["q_value"] - float(min_q_value), 4)
                if (hops_report and min_q_value is not None)
                else None,
                "hops": hops_report,
            }
        )
    return results


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ROADM Reachability Simulator")
    parser.add_argument("--dco-spec", required=True, help="Path to DCO module spec JSON")
    parser.add_argument("--roadm-spec", required=True, help="Path to ROADM spec JSON")
    parser.add_argument("--routes", required=True, help="Path to route file (.csv/.xlsx)")
    parser.add_argument("--wavelength-plan", required=True, help="Path to wavelength plan JSON")
    parser.add_argument(
        "--scenario-set",
        choices=["none", "planning"],
        default="none",
        help="Optional scenario envelope run. Use 'planning' for optimistic/nominal/conservative sweeps.",
    )
    parser.add_argument("--output", help="Optional output report file path")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    dco_spec = _load_json(args.dco_spec)
    roadm_spec = _load_json(args.roadm_spec)
    wavelength_plan = _load_json(args.wavelength_plan)
    routes = load_routes(args.routes)

    if args.scenario_set == "planning":
        scenarios = _planning_scenarios()
        scenario_runs: Dict[str, Dict[str, object]] = {}
        for scenario_name, override in scenarios.items():
            amplifier_cfg = _merge_float_config(wavelength_plan.get("amplifiers"), override)
            scenario_results = simulate_reachability(
                dco_spec=dco_spec,
                roadm_spec=roadm_spec,
                routes=routes,
                wavelengths=wavelength_plan.get("wavelengths", []),
                amplifier_config=amplifier_cfg,
            )
            scenario_runs[scenario_name] = {
                "summary": {
                    "total": len(scenario_results),
                    "reachable": sum(1 for row in scenario_results if row["reachable"]),
                },
                "results": scenario_results,
            }

        nominal_payload = scenario_runs.get("nominal", {"summary": {"total": 0, "reachable": 0}, "results": []})
        output = {
            "summary": nominal_payload["summary"],
            "results": nominal_payload["results"],
            "scenario_set": "planning",
            "scenarios": scenario_runs,
            "strategy_summary": _build_strategy_summary(scenario_runs),
        }
    else:
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
