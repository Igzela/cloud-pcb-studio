"""Netlist-only hierarchy and repeated-channel discovery, independent of references."""
from __future__ import annotations

import hashlib
import math
from collections import defaultdict

import networkx as nx


def component_signature(comp):
    pads=comp.get('pads',[])
    geometry=sorted((str(p['num']),round(float(p['x_rel_mm']),3),round(float(p['y_rel_mm']),3),round(float(p['w_mm']),3),round(float(p['h_mm']),3),float(p.get('angle_rel_deg',0)),p.get('pin_type','')) for p in pads)
    return hashlib.sha256(repr((comp.get('footprint_name',''),comp.get('value',''),geometry)).encode()).hexdigest()


def analyze_topology(spec):
    comps={c['refdes']:c for c in spec['components']}
    rails={r['net'] for r in spec.get('design_requirements',{}).get('power_rails',[])}
    rails.update(n for n in spec.get('nets',{}) if n.upper() in {'GND','AGND','DGND','PGND','VSS','VCC','VDD','VBUS'} or n.startswith('+'))
    graph=nx.Graph()
    for ref,c in comps.items(): graph.add_node(ref,signature=component_signature(c))
    for net,pins in spec.get('nets',{}).items():
        refs=sorted({r for r,_ in pins if r in comps})
        if net in rails or len(refs)<2: continue
        weight=1.0/(len(refs)-1)
        for i,a in enumerate(refs):
            for b in refs[i+1:]:
                graph.add_edge(a,b,weight=graph.get_edge_data(a,b,{}).get('weight',0)+weight)
    communities=nx.community.louvain_communities(graph,seed=0,weight='weight') if graph.number_of_edges() else [{r} for r in graph]
    communities=sorted((sorted(c) for c in communities),key=lambda c:(-len(c),tuple(graph.nodes[r]['signature'] for r in c)))
    anchors=[r for r,c in comps.items() if len(c.get('pads',[]))>=6 and not any(t in c.get('footprint_name','').lower() for t in ('connector','header','socket','terminal'))]
    # Radius-one induced subgraphs: shared controller/rails stay outside the channel.
    buckets=defaultdict(list)
    for r in anchors:
        members={r}|{n for n in graph.neighbors(r) if len(comps[n].get('pads',[]))<=3}
        sub=graph.subgraph(members).copy()
        for n in sub: sub.nodes[n]['root']=n==r
        h=nx.weisfeiler_lehman_graph_hash(sub,node_attr='signature',iterations=3)
        buckets[h].append((r,sub))
    repeated=[]
    for entries in buckets.values():
        while entries:
            root,template=entries.pop(0); group=[root]; mappings={root:{n:n for n in template}}
            rest=[]
            for other,g in entries:
                matcher=nx.algorithms.isomorphism.GraphMatcher(template,g,node_match=lambda a,b:a['signature']==b['signature'] and a['root']==b['root'])
                if matcher.is_isomorphic(): group.append(other); mappings[other]=matcher.mapping
                else: rest.append((other,g))
            if len(group)>1: repeated.append(dict(anchors=group,mappings=mappings))
            entries=rest
    return dict(communities=communities,repeated_channels=repeated,signal_edges=graph.number_of_edges(),algorithm='weighted Louvain + WL filter + exact attributed isomorphism',seed=0)


def macro_targets(spec, hierarchy):
    """Area-weighted shelf of communities; repeated anchors use evenly spaced lanes."""
    w=float(spec['board_outline_mm']['width']); h=float(spec['board_outline_mm']['height'])
    groups=hierarchy['communities']; cols=max(1,math.ceil(math.sqrt(len(groups)*w/max(h,1))))
    rows=max(1,math.ceil(len(groups)/cols)); targets={}
    for i,group in enumerate(groups):
        center=(w*(i%cols+0.5)/cols,h*(i//cols+0.5)/rows)
        for r in group: targets[r]=center
    for channel in hierarchy['repeated_channels']:
        anchors=channel['anchors']; n=len(anchors)
        for i,ref in enumerate(anchors):
            targets[ref]=(w*(i+1)/(n+1),h*0.55)
    return targets


def topology_metrics(spec, placed):
    hierarchy=analyze_topology(spec); spans=[]; alignment=[]
    for group in hierarchy['communities']:
        pts=[placed[r] for r in group if r in placed]
        if pts: spans.append((max(p[0] for p in pts)-min(p[0] for p in pts))+(max(p[1] for p in pts)-min(p[1] for p in pts)))
    for channel in hierarchy['repeated_channels']:
        pts=[placed[r] for r in channel['anchors'] if r in placed]
        if len(pts)>1: alignment.append(min(max(p[0] for p in pts)-min(p[0] for p in pts),max(p[1] for p in pts)-min(p[1] for p in pts)))
    return dict(community_count=len(hierarchy['communities']),repeated_channel_groups=len(hierarchy['repeated_channels']),community_hpwl_mm=spans,channel_alignment_error_mm=alignment)
