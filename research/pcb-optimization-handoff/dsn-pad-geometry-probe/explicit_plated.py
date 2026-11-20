from __future__ import annotations
import json,sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent/'draft'))
from overlay import activate
activate()

from kiutils.board import Board
from kiutils.footprint import DrillDefinition, Footprint, Pad as PcbPad
from kiutils.items.common import Net, Position
from cloud_pcb.layout_route.dsn_ses_bridge import board_to_dsn
from contracts.schemas import NetClassRule
from probe import Pad, ROOT, execute


def emit(name,surplus,precise):
    board=Board.create_new()
    w,h=.82,.30
    a,b=Net(1,'A_NET'),Net(2,'B_NET');board.nets.extend([a,b])
    probe = [(8, 8, 'A', 'roundrect', a), (8+w+surplus, 8-h-surplus, 'B', 'oval', b),
             (3, 8, 'U', 'rect', a), (14, 7, 'V', 'rect', b)]
    for x,y,ref,shape,net in probe:
        plated=shape!='rect'
        drill=DrillDefinition(oval=True,diameter=.52,width=.09) if plated else None
        pad=PcbPad(number='1',type='thru_hole' if plated else 'smd',shape=shape,
                   roundrectRatio=.1 if shape=='roundrect' else None,net=net,
                   size=Position(X=w if plated else .4,Y=h if plated else .4),
                   layers=['*.Cu','*.Mask'] if plated else ['F.Cu'],drill=drill)
        board.footprints.append(Footprint(position=Position(X=x,Y=y),properties={'Reference':ref},pads=[pad]))
    path=board_to_dsn(board,ROOT/f'{name}.dsn',{'DEFAULT':NetClassRule('DEFAULT',.16,.130,.30,.55,.5)}, {},board_bounds_mm=(0,0,24,20),accurate_pad_geometry=precise)
    # The synthetic board deliberately specifies the .130 mm drill-item rule
    # at every shared place where a default-class value could compete with it.
    path.write_text(path.read_text().replace('(clearance 150)', '(clearance 130)').replace('(clearance 150 (type default_smd))','(clearance 130 (type default_smd))'))
    r=Pad(w,h,.03); o=Pad(w,h,.15)
    return r.actual((8,-8)).distance(o.actual((8+w+surplus,-8+h+surplus)))


def main():
    summary={}
    for name,clear,precise in [('plated_130_box',.052,False),('plated_130_polygon',.052,True),('plated_130_real_violation',.030,True)]:
        physical_gap=emit(name,clear,precise)
        row={'physical_gap_mm':physical_gap,'explicit_rule_mm':.13}
        for engine in ('fast','jvm'):
            row[engine],_=execute(name,engine)
            assert row[engine]['unrouted']==0
            expected=4 if not precise or physical_gap<.13 else 0
            assert row[engine]['raw_violations']==expected
            if engine=='fast':
                assert all(v['clearance_mm']==.13 for v in row[engine]['pin_pairs'])
        summary[name]=row
    (ROOT/'explicit-130-plated-results.json').write_text(json.dumps(summary,indent=2)+'\n')

if __name__=='__main__': main()
