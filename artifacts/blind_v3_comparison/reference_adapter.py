"""Post-unseal syntax adapter: KiCad name-only nets -> kiutils numeric nets.
No placement, copper geometry or electrical net identity is changed.
"""
import hashlib,json,re
from pathlib import Path
from kiutils.utils.sexpr import parse_sexp,term_regex

def adapt_reference(source,destination):
    raw=Path(source).read_text();original=parse_sexp(raw)
    def root_nets(ast):return {int(n[1]):str(n[2]) for n in ast if isinstance(n,list) and n and n[0]=='net' and len(n)>2}
    names=root_nets(original);names.setdefault(0,'');ids={n:i for i,n in names.items()}
    explicit_names=set()
    def discover(node,parent=None):
        if not isinstance(node,list):return
        if node and node[0]=='net' and parent!='kicad_pcb' and len(node)>1 and isinstance(node[1],str):explicit_names.add(node[1])
        for child in node:discover(child,node[0] if node else None)
    discover(original)
    additions={}
    for name in sorted(explicit_names):
        if name not in ids:
            number=max(names)+1;names[number]=name;ids[name]=number;additions[number]=name
    stack=[];edits=[];named_count=0;id_count=0
    for token in re.finditer(term_regex,raw):
        kind,value=next((k,v) for k,v in token.groupdict().items() if v)
        if kind=='brackl':stack.append([token.start(),None])
        elif kind=='brackr':
            start,key=stack.pop()
            if key=='net' and stack and stack[-1][1]!='kicad_pcb':
                item=parse_sexp(raw[start:token.end()]);value=item[1]
                if isinstance(value,str):
                    number=ids[value];suffix=' '+json.dumps(value,ensure_ascii=False) if stack[-1][1]=='pad' else ''
                    edits.append((start,token.end(),f'(net {number}{suffix})'));named_count+=1
                elif stack[-1][1]=='pad' and len(item)==2:
                    number=int(value);assert number in names
                    edits.append((start,token.end(),f'(net {number} {json.dumps(names[number],ensure_ascii=False)})'));id_count+=1
        elif stack and stack[-1][1] is None:stack[-1][1]=value
    converted=raw
    for start,end,value in reversed(edits):converted=converted[:start]+value+converted[end:]
    if additions:
        close=converted.rfind(')');table='\n'+''.join(f'  (net {i} {json.dumps(name,ensure_ascii=False)})\n' for i,name in additions.items())
        converted=converted[:close]+table+converted[close:]
    check=parse_sexp(converted)
    def semantic(ast):
        lookup=root_nets(ast);lookup.setdefault(0,'')
        def visit(node,parent=None):
            if not isinstance(node,list):return node
            if node and node[0]=='net':
                if parent=='kicad_pcb':return None
                return ['net',node[1] if isinstance(node[1],str) else lookup[int(node[1])]]
            return [visit(child,node[0] if node else None) for child in node if not (parent is None and isinstance(child,list) and child and child[0]=='net')]
        return visit(ast)
    assert semantic(original)==semantic(check),'adapter changed non-net semantics'
    destination=Path(destination);destination.write_text(converted)
    return dict(original_path=str(source),original_sha256=hashlib.sha256(raw.encode()).hexdigest(),analysis_copy=str(destination),analysis_sha256=hashlib.sha256(converted.encode()).hexdigest(),name_only_net_references_converted=named_count,pad_id_names_expanded=id_count,synthesized_net_entries=len(additions),ast_semantics_verified=True,rule='preserve explicit net identity; add numeric table and legacy pad name for kiutils only')
