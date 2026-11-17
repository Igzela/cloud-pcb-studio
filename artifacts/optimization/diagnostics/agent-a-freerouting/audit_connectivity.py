import re, math, sys
from collections import defaultdict

DSN = "/tmp/freerouting-exp/board_freerouting.dsn"
SES = "/tmp/freerouting-exp/board_freerouting.ses"

dsn = open(DSN).read()

images = {}
for m in re.finditer(r"\(image\s+(\S+)(.*?)\n    \)", dsn, re.S):
    name, body = m.group(1), m.group(2)
    pins = {}
    for pm in re.finditer(r"\(pin\s+(\S+)\s+(\S+)\s+(-?[\d.]+)\s+(-?[\d.]+)\)", body):
        pins[pm.group(2)] = (float(pm.group(3)), float(pm.group(4)))
    images[name] = pins

places = {}
for m in re.finditer(r"\(component\s+(\S+)\s*\(place\s+(\S+)\s+(-?[\d.]+)\s+(-?[\d.]+)", dsn):
    img, ref, x, y = m.group(1), m.group(2), float(m.group(3)), float(m.group(4))
    places[ref] = (img, x, y)

net_pins = {}
for m in re.finditer(r"\(net\s+(\"[^\"]+\"|\S+)\s*\(pins\s+([^)]*)\)", dsn):
    net = m.group(1).strip("\"")
    net_pins[net] = m.group(2).split()

def pin_abs(refpin):
    ref, pnum = refpin.rsplit("-", 1)
    img, x, y = places[ref]
    dx, dy = images[img][pnum]
    return ((x+dx)/1000.0, -(y+dy)/1000.0)

from cloud_pcb.layout_route.dsn_ses_bridge import parse_ses
from pathlib import Path
info = parse_ses(Path(SES))
segs_by_net = defaultdict(list)
for s in info["segments"]:
    segs_by_net[s["net"]].append(s)
vias_by_net = defaultdict(list)
for v in info["vias"]:
    vias_by_net[v["net"]].append(v)

def components(net):
    parent = {}
    def find(a):
        parent.setdefault(a,a)
        while parent[a]!=a: parent[a]=parent[parent[a]]; a=parent[a]
        return a
    def union(a,b): parent[find(a)]=find(b)
    def key(x,y): return (round(x*50), round(y*50))
    for s in segs_by_net[net]:
        union(key(s["x1"],s["y1"]), key(s["x2"],s["y2"]))
    for v in vias_by_net[net]:
        k = key(v["x"],v["y"]); parent.setdefault(k,k)
    return parent, find

incomplete = []
for net, pins in net_pins.items():
    if len(pins) < 2: continue
    parent, find = components(net)
    hits = []
    for p in pins:
        x,y = pin_abs(p)
        k = (round(x*50), round(y*50))
        hit = None
        for dx in (-1,0,1):
            for dy in (-1,0,1):
                kk=(k[0]+dx,k[1]+dy)
                if kk in parent: hit=find(kk); break
            if hit is not None: break
        hits.append(hit)
    touched = set(hits)
    if None in touched or len(touched - {None}) > 1:
        incomplete.append((net, pins, touched))
        print(" -", net, "untouched pins:", [p for p, h in zip(pins, hits) if h is None],
              "multi-component:", len(touched - {None}) > 1)

print("DSN totals: components(placed):", len(places), "images:", len(images))
pad_count = sum(len(p) for p in images.values())
print("DSN totals: pins in library:", pad_count, "nets:", len(net_pins), "net-pin refs:", sum(len(p) for p in net_pins.values()))
print("nets checked (>=2 pins):", sum(1 for n,p in net_pins.items() if len(p)>=2))
print("incomplete nets:", len(incomplete))
for net,pins,touched in incomplete:
    print(" -", net, pins, "components:", len(touched - {None}), "untouched:", None in touched)
