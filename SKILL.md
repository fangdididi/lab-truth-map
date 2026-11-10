---
name: lab-truth-map
description: Map a homelab or small-office network as it really is. Gather facts read-only and grade each one SEEN / TOLD / GUESS, write them into one inventory file, lint it for address clashes, VLAN mismatches across cables, slow links, PoE overload and unexplained internet exposure, then draw a redacted diagram and return TRUE / DRIFT / CONFLICT. Use when someone asks for a network diagram, wants to document or tidy their home network, plans a VLAN or IP change, or suspects their notes no longer match the hardware.
---

# Lab Truth Map

[中文版](SKILL.zh-CN.md)

## What this is

Most home network diagrams are drawn from memory. They look great and are quietly wrong: the NAS that "has a static IP" is actually inside the DHCP pool, the trunk to the access point carries one VLAN less on the far end, the 2.5G link negotiated 1G months ago, and a port forward nobody remembers still points at an old box.

This skill treats the network like a site survey. You collect facts without changing anything, record where each fact came from, put them into one inventory file, check that file for contradictions, and only then draw it. The diagram is the last step, not the first, and anything you could not confirm is drawn as unconfirmed.

## The problem it solves

- Diagrams and notes drift away from the real hardware.
- Contradictions hide in plain sight: duplicate addresses, static addresses inside DHCP ranges, a port tagged one way on the switch and another way on the device.
- Internet exposure grows by accident; forwards outlive the reason they were added.
- Diagrams get posted publicly with real domains, public IPs and SSIDs in them.

## When to use it

- "Draw my homelab / home network."
- "Document my network before I change it" (new VLAN, new firewall, moving house, re-addressing).
- "Something is slow / something can't reach something" and you want the physical and VLAN picture first.
- "Is anything open to the internet that shouldn't be?"
- A yearly check that the notes still match the boxes.

Do **not** use it to scan networks the user doesn't own or administer, and do not change device settings as part of the survey. Fixes come after the verdict, one at a time, with the user's go-ahead.

## Ground rules

1. **Read-only first.** Status pages, port tables, `ip addr`, DHCP lease lists, the controller's device list, a photo of a label. No config writes, no reboots, no firmware updates during the survey.
2. **Every fact gets a grade.**
   - `SEEN` - you or the agent looked at the device or its own UI/CLI output. Name the source.
   - `TOLD` - the user said so, or an old note says so.
   - `GUESS` - inferred (same vendor default, "probably 1G"). Never draw a GUESS as fact.
3. **One file is the truth.** The inventory is the only place facts live. The diagram is generated from it, never edited by hand.
4. **Private by default.** Real domain, public IPs, SSIDs, serial numbers and MAC addresses stay out of anything shareable.
5. **Ask, don't fill.** When a field is unknown, leave it empty and add it to the open-question list.

## Step by step

### 1. Scope the survey
Agree on the boundary: which site, which devices are in, which are out (a work laptop, a neighbour's mesh node). Write down what the diagram is for (sharing on a forum, planning a change, troubleshooting), because that decides how much detail and how much redaction.

### 2. Walk the path from the internet inward
Collect facts in this order so nothing is skipped: ISP line and modem/router, firewall or main router, core switch, other switches and access points, servers and hypervisors, end devices that matter (NAS, printer, cameras, smart-home hub). For each device note kind, model, ports with their maximum speed, addresses per VLAN, and for switches the PoE budget. Grade every device's evidence and date it.

Good read-only sources: the switch's port/VLAN table, the firewall's interface and NAT pages, the DHCP lease and reservation list, `ip -br addr` / `ipconfig`, the Wi-Fi controller's client list, LLDP neighbour tables, the link LEDs or the negotiated-speed column.

### 3. Record links, both ends
For every cable record both ends (`device:port`), the negotiated speed, the speed you expected, PoE watts if powered, and the VLANs **as configured on each end separately** (`untagged` + `tagged`). Recording the two ends separately is what catches mismatches. Mark wireless links with `wireless: true`.

### 4. Record exposure
List every port forward from the router, and separately an `exposed` list of what is *meant* to be reachable from the internet, each with a written reason. Services reached through an outbound tunnel go on the exposed list with `via` instead of a forward.

### 5. Lint
Run `python3 scripts/labmap.py check inventory.json` (YAML works if PyYAML is installed). It reports:

| Code | Level | What it catches |
|---|---|---|
| L01 | error | the same address claimed twice on one VLAN |
| L02 | error | a static address inside the DHCP pool (`mode: reserved` is allowed) |
| L03 | error | an address outside its VLAN's subnet, or an undeclared VLAN |
| L04 | error | the two ends of a cable disagree on VLANs, or a link uses an undeclared VLAN |
| L05 | warn | a link faster than a port can do, or slower than expected |
| L06 | error / warn | PoE over budget / above 80% / no budget recorded |
| L07 | error / warn | a forward to something not on the exposed list / exposure without reason / listed but unreachable |
| L08 | warn | missing grade, GUESS facts, SEEN without a source |
| L09 | warn | device with no recorded link, or a link to an unknown device |
| L10 | warn | evidence older than 90 days |

Verdict: **TRUE** (no findings), **DRIFT** (warnings only: the map is usable but not fully trustworthy), **CONFLICT** (at least one error: the inventory contradicts itself or the network does something nobody declared).

`python3 scripts/labmap.py ask inventory.json` lists the open questions to take back to the user.

### 6. Resolve before you draw
For each error decide with the user: is the inventory wrong (go look again) or is the network wrong (plan a fix)? Fix the file, re-run, repeat until the verdict is TRUE or the remaining DRIFT items are written down as accepted.

### 7. Draw
`python3 scripts/labmap.py mermaid inventory.json` prints a Mermaid diagram: private addresses masked to `/24`, domain replaced with `example.lan`, trunks drawn as thick lines, wireless as dotted, PoE and speed on each cable, internet exposure drawn as arrows from `internet`, and GUESS devices labelled "(unconfirmed)". Use `--show-private` for a private copy with full LAN addresses and `--unredacted` only for a copy that never leaves the user's machine.

For a richer poster-style picture, hand the same inventory to any renderer; keep the rule that the picture is generated from the file.

### 8. Hand over
Deliver: the inventory file, the diagram, the lint output with the verdict, the accepted-drift list, and the open questions. Suggest a re-survey date.

## Example

User: "Make a diagram of my network so I can post it on r/homelab."

1. Scope: whole flat, public post, so redacted.
2. Agent asks for (or reads) the firewall interface page, the switch port table and the DHCP list. Facts go into `inventory.json`, graded SEEN with sources.
3. `check` returns CONFLICT: the NAS's "static" `.150` is inside the pool `.100-.199` (L02) and the switch port to the NAS tags VLAN 30 while the NAS side has none (L04).
4. User confirms the NAS should be `.10` and the DMZ VLAN should reach it. The user changes the NAS and the switch; the agent re-reads both, updates the file. `check` now says TRUE.
5. `mermaid` produces the redacted diagram; the agent also reminds the user the screenshot of the controller still shows SSIDs.

See `examples/clean.json` (TRUE) and `examples/messy.json` (CONFLICT, five errors, five warnings).

## Tips

- Negotiated speed beats nameplate speed. A 2.5G port showing 1G is usually a cable or a forced setting.
- Record VLANs from each device's own view, not from what the switch "should" have.
- Reservations made in DHCP belong in the pool; mark them `mode: reserved`.
- Keep the exposed list short and give each line a reason a stranger would accept.
- A diagram for sharing and a diagram for fixing are different files; generate both from the same inventory.
- Re-run `check` after every change; it is cheap.
