import json
import os
import sys
import unittest
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import labmap  # noqa: E402

TODAY = date(2026, 11, 10)


def load(name):
    with open(os.path.join(HERE, "..", "examples", name), encoding="utf-8") as fh:
        return json.load(fh)


def codes(inv):
    return {f["code"] for f in labmap.lint(inv, TODAY)}


class CleanInventory(unittest.TestCase):
    def test_clean_is_true(self):
        self.assertEqual(labmap.lint(load("clean.json"), TODAY), [])
        self.assertEqual(labmap.verdict([]), "TRUE")


class Rules(unittest.TestCase):
    def setUp(self):
        self.inv = load("clean.json")

    def test_duplicate_ip(self):
        self.inv["devices"][4]["ip"][0]["addr"] = "192.168.10.2"
        self.assertIn("L01", codes(self.inv))

    def test_static_in_pool(self):
        self.inv["devices"][3]["ip"][0]["addr"] = "192.168.10.120"
        self.assertIn("L02", codes(self.inv))

    def test_dhcp_reservation_in_pool_is_fine(self):
        self.inv["devices"][3]["ip"][0].update(addr="192.168.10.120", mode="reserved")
        self.assertNotIn("L02", codes(self.inv))

    def test_ip_outside_subnet(self):
        self.inv["devices"][3]["ip"][0]["addr"] = "10.0.0.5"
        self.assertIn("L03", codes(self.inv))

    def test_undeclared_vlan_on_device(self):
        self.inv["devices"][3]["ip"].append({"vlan": 99, "addr": "10.9.9.9"})
        self.assertIn("L03", codes(self.inv))

    def test_vlan_mismatch(self):
        self.inv["links"][2]["b_vlans"] = {"untagged": 10, "tagged": []}
        self.assertIn("L04", codes(self.inv))

    def test_undeclared_vlan_on_link(self):
        self.inv["links"][2]["a_vlans"]["tagged"] = [30, 77]
        self.inv["links"][2]["b_vlans"]["tagged"] = [30, 77]
        self.assertIn("L04", codes(self.inv))

    def test_speed_above_port(self):
        self.inv["links"][3]["speed_mbps"] = 2500
        self.assertIn("L05", codes(self.inv))

    def test_speed_below_expected(self):
        self.inv["links"][2]["speed_mbps"] = 1000
        self.inv["links"][2]["expected_mbps"] = 2500
        self.assertIn("L05", codes(self.inv))

    def test_poe_over_budget_is_error(self):
        self.inv["links"][3]["poe_w"] = 80
        f = labmap.lint(self.inv, TODAY)
        self.assertTrue(any(x["code"] == "L06" and x["level"] == "ERROR" for x in f))

    def test_poe_near_budget_is_warning(self):
        self.inv["links"][3]["poe_w"] = 60
        f = labmap.lint(self.inv, TODAY)
        self.assertTrue(any(x["code"] == "L06" and x["level"] == "WARN" for x in f))

    def test_poe_without_budget(self):
        del self.inv["devices"][2]["poe_budget_w"]
        self.assertIn("L06", codes(self.inv))

    def test_forward_not_on_exposed_list(self):
        self.inv["forwards"].append({"wan_port": 22, "to": "nas", "to_port": 22})
        self.assertIn("L07", codes(self.inv))

    def test_exposed_without_reason(self):
        self.inv["exposed"][0]["reason"] = ""
        self.assertIn("L07", codes(self.inv))

    def test_exposed_via_tunnel_needs_no_forward(self):
        self.inv["forwards"] = []
        self.inv["exposed"][0]["via"] = "outbound tunnel"
        self.assertNotIn("L07", codes(self.inv))

    def test_guess_is_flagged(self):
        self.inv["devices"][4]["evidence"]["grade"] = "GUESS"
        self.assertIn("L08", codes(self.inv))

    def test_seen_without_source(self):
        del self.inv["devices"][4]["evidence"]["source"]
        self.assertIn("L08", codes(self.inv))

    def test_orphan_device(self):
        self.inv["devices"].append({"name": "pi", "kind": "server",
                                    "evidence": {"grade": "SEEN", "source": "x", "date": "2026-11-01"}})
        self.assertIn("L09", codes(self.inv))

    def test_link_to_unknown_device(self):
        self.inv["links"].append({"a": "core-sw:4", "b": "ghost:eth0"})
        self.assertIn("L09", codes(self.inv))

    def test_stale_evidence(self):
        self.inv["devices"][3]["evidence"]["date"] = "2026-01-01"
        self.assertIn("L10", codes(self.inv))


class Verdicts(unittest.TestCase):
    def test_messy_is_conflict(self):
        self.assertEqual(labmap.verdict(labmap.lint(load("messy.json"), TODAY)), "CONFLICT")

    def test_warning_only_is_drift(self):
        inv = load("clean.json")
        inv["devices"][4]["evidence"]["grade"] = "TOLD"
        inv["devices"][4]["evidence"]["date"] = "2025-01-01"
        self.assertEqual(labmap.verdict(labmap.lint(inv, TODAY)), "DRIFT")


class Diagram(unittest.TestCase):
    def test_redacted_by_default(self):
        out = labmap.mermaid(load("clean.json"))
        self.assertTrue(out.startswith("graph LR"))
        self.assertNotIn("192.168.10.10", out)
        self.assertIn("192.168.10.x", out)

    def test_show_private(self):
        self.assertIn("192.168.10.10", labmap.mermaid(load("clean.json"), show_private=True))

    def test_trunk_and_poe_labels(self):
        out = labmap.mermaid(load("clean.json"))
        self.assertIn("===", out)
        self.assertIn("PoE 12W", out)
        self.assertIn("internet((internet))", out)

    def test_guess_marked_unconfirmed(self):
        self.assertIn("(unconfirmed)", labmap.mermaid(load("messy.json")))


class Questions(unittest.TestCase):
    def test_open_questions(self):
        qs = labmap.open_questions(load("messy.json"))
        self.assertTrue(any("ap-hall" in q for q in qs))
        self.assertTrue(any("printer" in q for q in qs))

    def test_cli_exit_codes(self):
        ex = os.path.join(HERE, "..", "examples")
        self.assertEqual(labmap.main(["check", os.path.join(ex, "clean.json")]), 0)
        self.assertEqual(labmap.main(["check", os.path.join(ex, "messy.json")]), 2)
        self.assertEqual(labmap.main(["check", "/nonexistent.json"]), 3)


if __name__ == "__main__":
    unittest.main()
