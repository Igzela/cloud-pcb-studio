from __future__ import annotations
import json,math
from pathlib import Path
from shapely.geometry import LineString
from probe import Pad, ROOT, fixture, execute


def main():
    rr=Pad(.72,.42,.105); oval=Pad(.72,.42,.21)
    positive=((8.,-8.),(8.742,-7.558)); remote=((3.,-8.),(14.,-7.558))
    tilt=Pad(1.8,.35,.175,30.)
    distance=.53
    centers=((8.,-8.),(8.-.5*distance,-8.+distance*math.sin(math.pi/3)))
    far=tuple((x+sign*4*(-.5),y+sign*4*math.sin(math.pi/3)) for sign,(x,y) in zip((-1,1),centers))
    slot=Pad(6.0,2.0,1.0,29.)
    slot_with_margin=Pad(7.0,3.0,1.5,29.)  # identical capsule core plus 0.50 mm
    names={
        'axis_clear_box':fixture('axis_clear_box',rr,oval,positive,remote,False),
        'axis_clear_polygon':fixture('axis_clear_polygon',rr,oval,positive,remote),
        'tilted_clear_45':fixture('tilted_clear_45',tilt,tilt,centers,far),
        'tilted_clear_unrestricted_probe':fixture('tilted_clear_unrestricted_probe',tilt,tilt,centers,far),
        'slot_manufacturing_margin':fixture('slot_manufacturing_margin',rr,oval,((2,-8),(2,-14)),((20,-8),(20,-14)),slot=(slot_with_margin,(11,-8))),
    }
    unrestricted=ROOT/'tilted_clear_unrestricted_probe.dsn'
    unrestricted.write_text(unrestricted.read_text().replace('(rule (width 160)', '(snap_angle none)\n    (rule (width 160)',1))
    for name,row in names.items():
        row['effective_output_rule_mm']=.150
        for engine in ('fast','jvm'):
            row[engine],route=execute(name,engine)
            if name=='slot_manufacturing_margin':
                drill=slot.actual((11,-8))
                spaces=[]
                for s in route['segments']:
                    l=LineString(((s['x1'],-s['y1']),(s['x2'],-s['y2'])))
                    spaces.append(l.buffer(s['width_mm']/2).distance(drill))
                assert min(spaces)>=.50, (name,engine,min(spaces))
                row[engine]['min_routed_trace_copper_to_milled_slot_mm']=min(spaces)
    (ROOT/'followup-results.json').write_text(json.dumps(names,indent=2)+'\n')

if __name__=='__main__': main()
