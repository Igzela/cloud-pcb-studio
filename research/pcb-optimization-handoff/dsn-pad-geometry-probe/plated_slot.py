import json
from kiutils.board import Board
from kiutils.footprint import DrillDefinition, Footprint, Pad as PcbPad
from kiutils.items.common import Net, Position
from shapely.geometry import LineString
from shapely.ops import unary_union
from cloud_pcb.general_solver.geometry import drill_geometry
from cloud_pcb.layout_route.dsn_ses_bridge import merge_ses_into_board
from probe import Pad, ROOT, dsn_header, execute


def main():
    name='plated_slot'
    outer=Pad(4,1.2,.6,27)
    layers=('F.Cu','In1.Cu','In2.Cu','B.Cu')
    shapes=' '.join(f'(shape {outer.polygon_expr(layer)})' for layer in layers)
    (ROOT/f'{name}.dsn').write_text(dsn_header(name)+f'''
    (rule (width 160) (clearance 150)))
  (placement (component IMG_SLOT (place SLOT 4000 -8000 front 0))
             (component IMG_BACK (place BACKPAD 15000 -8000 front 0)))
  (library
    (image IMG_SLOT (pin SLOTCU 1 0 0))
    (image IMG_BACK (pin BACKCU 1 0 0))
    (padstack SLOTCU {shapes} (attach off))
    (padstack BACKCU (shape (rect B.Cu -500 -500 500 500)) (attach off))
    (padstack Via[0-3]_550:300_um
      (shape (circle F.Cu 550)) (shape (circle In1.Cu 550))
      (shape (circle In2.Cu 550)) (shape (circle B.Cu 550)) (attach off)))
  (network (net SLOTTED_NET (pins SLOT-1 BACKPAD-1)))
  (wiring))
''')
    output={}
    for engine in ('fast','jvm'):
        info,route=execute(name,engine)
        assert [s['layer'] for s in route['segments']]==['B.Cu']
        assert not route['vias'] and info['unrouted']==info['raw_violations']==0
        net=Net(1,'SLOTTED_NET')
        slot=Footprint(position=Position(X=4,Y=8),properties={'Reference':'SLOT'},pads=[
            PcbPad(number='1',shape='oval',type='thru_hole',net=net,size=Position(X=4,Y=1.2),
                   position=Position(X=0,Y=0,angle=27),layers=['*.Cu','*.Mask'],
                   drill=DrillDefinition(oval=True,diameter=2.3,width=.4,offset=Position(X=.15,Y=0)))] )
        back=Footprint(position=Position(X=15,Y=8),properties={'Reference':'BACKPAD'},pads=[
            PcbPad(number='1',shape='rect',type='smd',net=net,size=Position(X=1,Y=1),layers=['B.Cu'])])
        board=Board.create_new(); board.nets.append(net); board.footprints.extend((slot,back))
        before=slot.to_sexpr(); drill=drill_geometry(slot,slot.pads[0])
        merged,report=merge_ses_into_board(board,ROOT/f'{name}-{engine}.ses')
        assert merged.footprints[0].to_sexpr()==before
        from shapely.affinity import scale
        from shapely.geometry import box
        true_outer=scale(outer.actual((4,-8)),xfact=1,yfact=-1,origin=(0,0))
        assert true_outer.covers(drill.buffer(.1))
        plated_copper=true_outer.difference(drill)
        traces=[LineString(((s['x1'],s['y1']),(s['x2'],s['y2']))).buffer(s['width_mm']/2) for s in route['segments']]
        assert unary_union([plated_copper,box(14.5,7.5,15.5,8.5),*traces]).geom_type=='Polygon'
        output[engine]=dict(**info,checked_backside_plated_contact=True,physical_drill_unchanged=True,annular_mm=round(drill.distance(true_outer.boundary),5))
    (ROOT/'plated-slot-results.json').write_text(json.dumps(output,indent=2)+'\n')

if __name__=='__main__':main()
