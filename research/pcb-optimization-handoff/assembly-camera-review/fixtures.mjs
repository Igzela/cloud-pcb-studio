import fs from 'node:fs';
import path from 'node:path';
const dir=path.dirname(new URL(import.meta.url).pathname);
const original=fs.readFileSync('.run/legacy-compat-integration/current-genuine-exhausted/assembly.html','utf8');
const pattern=/(<script id="scene-data" type="application\/json">)(.*?)<\/script><script>(.*?)<\/script><\/html>/s;
const match=original.match(pattern);
if(!match)throw new Error('could not find the assembly template payload');
const base=JSON.parse(match[2]);
const update=fs.readFileSync(path.join(dir,'viewer.js'),'utf8').replaceAll('</script','<\\/script');
const make=(filename,scene,js)=>fs.writeFileSync(path.join(dir,filename), original.replace(pattern,(_all,tag)=>`${tag}${JSON.stringify(scene).replaceAll('<','\\u003c')}</script><script>${js}</script></html>`));
const component=(refdes,position,side,bbox_mm,height_mm,base_z_mm)=>({refdes,value:refdes,position,side,bbox_mm,height_mm,base_z_mm,family:'generic'});
const synthetic={outline:{width:6,height:4},thickness_mm:1.6,mechanical:{collision_count:0,unverified_envelopes:[]},pads:[],vias:[],tracks:[],holes:[],zones:[],components:[
  component('tall top',[2,1.2,12],'top',[-1.2,-.8,1.2,.8],8,2.5),
  component('underneath',[3.3,2.8,28],'bottom',[-1.4,-.7,1.4,.7],7,-2.8),
  component('edge shell',[5.7,1.8,15],'top',[-1,-1.2,3.1,.4],1.2,0),
]};
make('old-synthetic.html',synthetic,match[3]);
make('new-synthetic.html',synthetic,update);
make('new-existing.html',base,update);
