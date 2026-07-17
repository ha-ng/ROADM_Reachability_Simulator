import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from roadm_reachability_simulator import load_routes, simulate_reachability


class RoadmReachabilitySimulatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dco_spec = {
            "modules": [
                {
                    "name": "DCO-1",
                    "supported_profiles": [
                        {
                            "speed_gbps": 100,
                            "modulation": "QPSK",
                            "max_path_loss_db": 30.0,
                            "max_distance_km": 1000.0,
                        },
                        {
                            "speed_gbps": 400,
                            "modulation": "16QAM",
                            "max_path_loss_db": 20.0,
                            "max_distance_km": 150.0,
                        },
                    ],
                }
            ]
        }
        self.roadm_spec = {"vendor": "VendorA", "model": "R-1000", "node_loss_db": 1.0}

    def test_simulate_reachability_applies_distance_and_edfa_gain(self) -> None:
        routes = [
            {
                "start_site": "A",
                "end_site": "B",
                "fiber_type": "SMF",
                "distance_km": 100,
                "loss_db": 10,
                "bypass_sites": "",
            },
            {
                "start_site": "B",
                "end_site": "C",
                "fiber_type": "SMF",
                "distance_km": 100,
                "loss_db": 10,
                "bypass_sites": "",
            },
        ]
        normalized_routes = load_routes(self._write_csv(routes))
        wavelengths = [
            {
                "id": "ch-1",
                "source_site": "A",
                "destination_site": "C",
                "speed_gbps": 100,
                "modulation": "QPSK",
            },
            {
                "id": "ch-2",
                "source_site": "A",
                "destination_site": "C",
                "speed_gbps": 400,
                "modulation": "16QAM",
            },
        ]

        result = simulate_reachability(
            dco_spec=self.dco_spec,
            roadm_spec=self.roadm_spec,
            routes=normalized_routes,
            wavelengths=wavelengths,
            amplifier_config={"boost_gain_db": 2.0, "preamp_gain_db": 2.0},
        )
        self.assertTrue(result[0]["reachable"])
        self.assertFalse(result[1]["reachable"])

    def test_load_routes_supports_xlsx(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workbook = Workbook()
            sheet = workbook.active
            sheet.append(["fiber_type", "start_site", "end_site", "distance_km", "loss_db", "bypass_sites"])
            sheet.append(["SMF", "A", "B", 80, 8, "X;Y"])
            file_path = Path(tmp_dir) / "routes.xlsx"
            workbook.save(file_path)

            routes = load_routes(str(file_path))
            self.assertEqual(len(routes), 1)
            self.assertEqual(routes[0].bypass_sites, ("X", "Y"))

    def test_load_routes_supports_japanese_section_sheet_with_bypass_penalty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workbook = Workbook()
            sheet = workbook.active
            sheet.append(["起点", None, None, "終点", None, None, "区間", None])
            sheet.append(["ビル名", None, "ビル種別", "ビル名", None, "ビル種別", "距離", "損失"])
            # A(ROADM) -> z1(通過) -> z2(通過) -> B(ROADM)
            sheet.append(["A", None, "コア", "z1", None, "通過", 1.0, 0.5])
            sheet.append(["z1", None, "通過", "z2", None, "通過", 2.0, 1.0])
            sheet.append(["z2", None, "通過", "B", None, "OLT設置", 3.0, 1.5])
            # B(ROADM) -> C(ROADM)
            sheet.append(["B", None, "OLT設置", "C", None, "コア", 4.0, 2.0])

            file_path = Path(tmp_dir) / "sections.xlsx"
            workbook.save(file_path)

            routes = load_routes(str(file_path))
            self.assertEqual(len(routes), 2)

            self.assertEqual(routes[0].start_site, "A")
            self.assertEqual(routes[0].end_site, "B")
            self.assertEqual(routes[0].distance_km, 6.0)
            # Base loss 0.5+1.0+1.5 = 3.0 plus 2 bypass sites * 3 dB = 6.0 => 9.0
            self.assertEqual(routes[0].loss_db, 9.0)
            self.assertEqual(routes[0].bypass_sites, ("z1", "z2"))

            self.assertEqual(routes[1].start_site, "B")
            self.assertEqual(routes[1].end_site, "C")
            self.assertEqual(routes[1].distance_km, 4.0)
            self.assertEqual(routes[1].loss_db, 2.0)

    def _write_csv(self, rows):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as handle:
            handle.write("fiber_type,start_site,end_site,distance_km,loss_db,bypass_sites\n")
            for row in rows:
                handle.write(
                    f'{row["fiber_type"]},{row["start_site"]},{row["end_site"]},{row["distance_km"]},{row["loss_db"]},{row["bypass_sites"]}\n'
                )
            return handle.name


if __name__ == "__main__":
    unittest.main()
