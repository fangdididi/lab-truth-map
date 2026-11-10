#!/usr/bin/env python3
"""labmap.py - check a home/small-office network inventory and draw it.

Commands:
  check   INVENTORY            lint the inventory, print findings and a verdict
  mermaid INVENTORY [--show-private] [--unredacted]
                               print a Mermaid diagram (redacted by default)
  ask     INVENTORY            list the questions still open (GUESS / missing facts)

INVENTORY is JSON, or YAML when PyYAML is installed.
Exit codes for `check`: 0 = TRUE, 1 = DRIFT, 2 = CONFLICT, 3 = unreadable input.
"""
import ipaddress
import json
import sys
from datetime import date, datetime

STALE_DAYS = 90
POE_WARN = 0.8
ERR, WARN = "ERROR", "WARN"


def load(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if path.endswith((".yaml", ".yml")):
        try:
            import yaml  # optional
        except ImportError:
            raise SystemExit("PyYAML is not installed: save the inventory as JSON or `pip install pyyaml`.")
        return yaml.safe_load(text)
    return json.loads(text)


def _ends(link):
    a_dev, _, a_port = str(link["a"]).partition(":")
    b_dev, _, b_port = str(link["b"]).partition(":")
    return (a_dev, a_port), (b_dev, b_port)


def _vlanset(v):
    v = v or {}
    return v.get("untagged"), tuple(sorted(v.get("tagged", []) or []))


def lint(inv, today=None):
    today = today or date.today()
    out = []

    def add(level, code, msg):
        out.append({"level": level, "code": code, "msg": msg})

    vlans = {v["id"]: v for v in inv.get("vlans", [])}
    nets = {}
    for vid, v in vlans.items():
        try:
            nets[vid] = ipaddress.ip_network(v["subnet"], strict=False)
        except (KeyError, ValueError):
            add(ERR, "L00", f"VLAN {vid} has no valid subnet")
    devices = {d["name"]: d for d in inv.get("devices", [])}

    # L01 duplicate IPs, L02 static inside DHCP pool, L03 IP outside its VLAN
    seen_ip = {}
    for d in devices.values():
        for entry in d.get("ip", []) or []:
            addr, vid = entry.get("addr"), entry.get("vlan")
            try:
                ip = ipaddress.ip_address(addr)
            except (TypeError, ValueError):
                add(ERR, "L03", f"{d['name']}: '{addr}' is not an IP address")
                continue
            key = (vid, str(ip))
            if key in seen_ip:
                add(ERR, "L01", f"{ip} on VLAN {vid} is claimed by both {seen_ip[key]} and {d['name']}")
            seen_ip[key] = d["name"]
            net = nets.get(vid)
            if vid not in vlans:
                add(ERR, "L03", f"{d['name']}: VLAN {vid} is not declared")
            elif net and ip not in net:
                add(ERR, "L03", f"{d['name']}: {ip} is outside VLAN {vid} ({net})")
            pool = vlans.get(vid, {}).get("dhcp")
            if pool and entry.get("mode", "static") == "static":
                lo, hi = (ipaddress.ip_address(x) for x in pool)
                if lo <= ip <= hi:
                    add(ERR, "L02", f"{d['name']}: static {ip} sits inside the DHCP pool {pool[0]}-{pool[1]}")

    # L04 VLAN mismatch across a cable, L05 speed, L09 orphans
    linked = set()
    port_max = {}
    for d in devices.values():
        for p in d.get("ports", []) or []:
            port_max[(d["name"], str(p["id"]))] = p.get("max_mbps")
    poe_draw = {}
    for link in inv.get("links", []):
        (ad, ap), (bd, bp) = _ends(link)
        tag = f"{ad}:{ap} <-> {bd}:{bp}"
        for dev in (ad, bd):
            if dev not in devices:
                add(ERR, "L09", f"{tag}: device '{dev}' is not in the inventory")
            linked.add(dev)
        if "a_vlans" in link and "b_vlans" in link and _vlanset(link["a_vlans"]) != _vlanset(link["b_vlans"]):
            add(ERR, "L04", f"{tag}: the two ends disagree on VLANs {link['a_vlans']} vs {link['b_vlans']}")
        for vset in (link.get("a_vlans"), link.get("b_vlans")):
            u, t = _vlanset(vset)
            for vid in ([u] if u is not None else []) + list(t):
                if vid not in vlans:
                    add(ERR, "L04", f"{tag}: uses undeclared VLAN {vid}")
        speed = link.get("speed_mbps")
        for end in ((ad, ap), (bd, bp)):
            mx = port_max.get(end)
            if speed and mx and speed > mx:
                add(WARN, "L05", f"{tag}: {speed} Mb/s is faster than {end[0]}:{end[1]} can do ({mx})")
        expected = link.get("expected_mbps")
        if speed and expected and speed < expected:
            add(WARN, "L05", f"{tag}: negotiated {speed} Mb/s, expected {expected} (cable, port or setting?)")
        if link.get("poe_w"):
            poe_draw[ad] = poe_draw.get(ad, 0) + float(link["poe_w"])
    for name, d in devices.items():
        if name not in linked and d.get("kind") not in ("wan", "cloud"):
            add(WARN, "L09", f"{name}: no cable or wireless link is recorded")

    # L06 PoE budget
    for name, draw in poe_draw.items():
        budget = devices.get(name, {}).get("poe_budget_w")
        if not budget:
            add(WARN, "L06", f"{name}: powers {draw:g} W over PoE but has no poe_budget_w")
        elif draw > budget:
            add(ERR, "L06", f"{name}: PoE draw {draw:g} W is over its {budget:g} W budget")
        elif draw > POE_WARN * budget:
            add(WARN, "L06", f"{name}: PoE draw {draw:g} W is above {int(POE_WARN*100)}% of {budget:g} W")

    # L07 exposure
    exposed = {(e.get("device"), e.get("port")): e for e in inv.get("exposed", [])}
    for fw in inv.get("forwards", []):
        key = (fw.get("to"), fw.get("to_port"))
        e = exposed.get(key)
        if not e:
            add(ERR, "L07", f"WAN port {fw.get('wan_port')} forwards to {key[0]}:{key[1]}, which is not on the exposed list")
        elif not e.get("reason"):
            add(WARN, "L07", f"{key[0]}:{key[1]} is exposed without a written reason")
    for (dev, port), e in exposed.items():
        if not any((f.get("to"), f.get("to_port")) == (dev, port) for f in inv.get("forwards", [])) and not e.get("via"):
            add(WARN, "L07", f"{dev}:{port} is on the exposed list but no forward or tunnel ('via') reaches it")

    # L08 evidence, L10 stale facts
    for name, d in devices.items():
        ev = d.get("evidence") or {}
        grade = str(ev.get("grade", "")).upper()
        if grade not in ("SEEN", "TOLD", "GUESS"):
            add(WARN, "L08", f"{name}: no evidence grade (SEEN / TOLD / GUESS)")
        elif grade == "GUESS":
            add(WARN, "L08", f"{name}: facts are a GUESS - confirm before drawing them as true")
        elif grade == "SEEN" and not ev.get("source"):
            add(WARN, "L08", f"{name}: graded SEEN but names no source")
        when = ev.get("date")
        if when:
            try:
                age = (today - datetime.strptime(str(when), "%Y-%m-%d").date()).days
                if age > STALE_DAYS:
                    add(WARN, "L10", f"{name}: last checked {age} days ago")
            except ValueError:
                add(WARN, "L10", f"{name}: evidence date '{when}' is not YYYY-MM-DD")
    return out


def verdict(findings):
    if any(f["level"] == ERR for f in findings):
        return "CONFLICT"
    if findings:
        return "DRIFT"
    return "TRUE"


def _mask_ip(addr, show_private):
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return "x.x.x.x"
    if ip.is_private and show_private:
        return str(ip)
    if ip.is_private:
        parts = str(ip).split(".")
        return ".".join(parts[:3] + ["x"]) if len(parts) == 4 else "private"
    return "public-ip"


def _nid(name):
    return "n_" + "".join(c if c.isalnum() else "_" for c in name)


def mermaid(inv, show_private=False, unredacted=False):
    domain = inv.get("domain")
    lines = ["graph LR"]
    for d in inv.get("devices", []):
        label = d["name"]
        if domain and not unredacted:
            label = label.replace(domain, "example.lan")
        bits = [label, d.get("kind", "device")]
        for e in d.get("ip", []) or []:
            addr = e.get("addr", "")
            bits.append(f"v{e.get('vlan')} {addr if unredacted else _mask_ip(addr, show_private)}")
        grade = (d.get("evidence") or {}).get("grade", "?")
        if str(grade).upper() == "GUESS":
            bits.append("(unconfirmed)")
        text = "<br/>".join(str(b).replace('"', "'") for b in bits)
        shape = ('(["', '"])') if d.get("kind") in ("wan", "cloud") else ('["', '"]')
        lines.append(f"  {_nid(d['name'])}{shape[0]}{text}{shape[1]}")
    for link in inv.get("links", []):
        (ad, _), (bd, _) = _ends(link)
        u, t = _vlanset(link.get("a_vlans"))
        tags = []
        if link.get("speed_mbps"):
            s = link["speed_mbps"]
            tags.append(f"{s/1000:g}G" if s >= 1000 else f"{s}M")
        if u is not None:
            tags.append(f"U{u}")
        if t:
            tags.append("T" + ",".join(map(str, t)))
        if link.get("poe_w"):
            tags.append(f"PoE {link['poe_w']}W")
        arrow = "-.-" if link.get("wireless") else ("===" if t else "---")
        lab = f'|"{" ".join(tags)}"|' if tags else ""
        lines.append(f"  {_nid(ad)} {arrow}{lab} {_nid(bd)}")
    for e in inv.get("exposed", []):
        lines.append(f"  internet((internet)) -->|\"{e.get('proto','tcp')}/{e.get('port')}\"| {_nid(e['device'])}")
    return "\n".join(lines)


def open_questions(inv):
    qs = []
    for d in inv.get("devices", []):
        ev = d.get("evidence") or {}
        if str(ev.get("grade", "")).upper() != "SEEN":
            qs.append(f"{d['name']}: how can we see this for ourselves (UI page, CLI output, label photo)?")
        if not d.get("ip") and d.get("kind") not in ("wan", "cloud", "unmanaged-switch", "patch-panel"):
            qs.append(f"{d['name']}: which VLAN and address does it use?")
    for link in inv.get("links", []):
        if not link.get("speed_mbps") and not link.get("wireless"):
            qs.append(f"{link['a']} <-> {link['b']}: what speed did it negotiate?")
    return qs


def main(argv):
    if len(argv) < 2 or argv[0] not in ("check", "mermaid", "ask"):
        print(__doc__)
        return 3
    try:
        inv = load(argv[1])
    except (OSError, ValueError) as exc:
        print(f"cannot read {argv[1]}: {exc}")
        return 3
    if argv[0] == "mermaid":
        print(mermaid(inv, "--show-private" in argv, "--unredacted" in argv))
        return 0
    if argv[0] == "ask":
        for q in open_questions(inv):
            print("- " + q)
        return 0
    findings = lint(inv)
    for f in findings:
        print(f"{f['level']:5} {f['code']}  {f['msg']}")
    v = verdict(findings)
    print(f"VERDICT: {v}  ({sum(f['level']==ERR for f in findings)} errors, {sum(f['level']==WARN for f in findings)} warnings)")
    return {"TRUE": 0, "DRIFT": 1, "CONFLICT": 2}[v]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
