import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const data=JSON.parse(document.getElementById('scene-data').textContent);
const scene=new THREE.Scene(); scene.background=new THREE.Color('#101820');
const camera=new THREE.PerspectiveCamera(40,innerWidth/innerHeight,.01,5000); camera.up.set(0,0,1);
const renderer=new THREE.WebGLRenderer({antialias:true,preserveDrawingBuffer:true});
renderer.setPixelRatio(Math.min(devicePixelRatio,2)); renderer.setSize(innerWidth,innerHeight); document.body.appendChild(renderer.domElement);
const controls=new OrbitControls(camera,renderer.domElement); controls.enableDamping=false;
scene.add(new THREE.HemisphereLight(0xe0f1ff,0x445044,2.8));
const sun=new THREE.DirectionalLight(0xffffff,3.5);sun.position.set(30,0,90);scene.add(sun);
const o=data.outline,W=o.width,H=o.height,T=data.thickness_mm||1.6;
controls.target.set(W/2,-H/2,0);
const groups={}; const pickable=[];
const mat=(color,metalness=0,roughness=.55)=>new THREE.MeshStandardMaterial({color,metalness,roughness,side:THREE.DoubleSide});
function group(name){if(!groups[name]){groups[name]=new THREE.Group();scene.add(groups[name]);}return groups[name];}
function mesh(geo,material,parent,user={}){const m=new THREE.Mesh(geo,material);m.userData=user;parent.add(m);if(user.net||user.refdes)pickable.push(m);return m;}
function shape(points){const s=new THREE.Shape();points.forEach(([x,y],i)=>i?s.lineTo(x,-y):s.moveTo(x,-y));s.closePath();return s;}
const outline=shape(o.vertices_mm||[[0,0],[W,0],[W,H],[0,H]]);
for(const h of [...data.holes,...data.vias.map(v=>({...v,drill_mm:v.drill}))]){const p=new THREE.Path();if(h.polygon){h.polygon.forEach(([x,y],i)=>i?p.lineTo(x,-y):p.moveTo(x,-y));p.closePath();}else p.absarc(h.x,-h.y,h.drill_mm/2,0,Math.PI*2,true);outline.holes.push(p);}
const maskMaterials=[mat('#194c35')];
const plate=mesh(new THREE.ExtrudeGeometry(outline,{depth:T,bevelEnabled:false,curveSegments:12}),[maskMaterials[0],mat('#9b864b')],group('基板'));plate.position.z=-T;
function padmesh(p,z,layer){const m=mesh(new THREE.ShapeGeometry(shape(p.polygon)),mat('#d8b760',.75,.27),group(layer),{net:p.net,refdes:p.refdes,pad:p.num});m.position.z=z;}
for(const p of data.pads){if(p.layers.includes('F.Cu')||p.layers.includes('*.Cu'))padmesh(p,.045,'F.Cu');if(p.layers.includes('B.Cu')||p.layers.includes('*.Cu'))padmesh(p,-T-.045,'B.Cu');}
const zmap={'F.Cu':.025,'In1.Cu':-.245,'In2.Cu':-1.328,'B.Cu':-T-.025};
for(const z of data.zones||[]){const material=mat(z.layer==='In1.Cu'?'#cf9a48':z.layer==='In2.Cu'?'#bf7049':'#286243',.3);if(!z.layer.startsWith('In'))maskMaterials.push(material);material.transparent=true;material.opacity=z.layer.startsWith('In')?.7:.65;material.depthWrite=false;const m=mesh(new THREE.ShapeGeometry(shape(z.polygon)),material,group(z.layer),{net:z.net});m.position.z=(zmap[z.layer]||0)-.01;}
for(const t of data.tracks){const [x,y]=t.start,[ex,ey]=t.end,dx=ex-x,dy=ey-y;const length=Math.hypot(dx,dy);if(!length)continue;const color=t.layer==='F.Cu'?'#3d9261':t.layer==='B.Cu'?'#318dad':'#b5854f';const m=mesh(new THREE.BoxGeometry(length,t.width,.018),mat(color,.3),group(t.layer),{net:t.net});m.position.set((x+ex)/2,-(y+ey)/2,zmap[t.layer]||0);m.rotation.z=Math.atan2(-dy,dx);}
for(const v of data.vias){const ring=mesh(new THREE.RingGeometry(v.drill/2,v.size/2,16),mat('#caab61',.8),group('过孔'),{net:v.net});ring.position.set(v.x,-v.y,.05);const barrel=mesh(new THREE.CylinderGeometry(v.drill/2,v.drill/2,T,12,1,true),mat('#b99557',.8),group('过孔'),{net:v.net});barrel.rotation.x=Math.PI/2;barrel.position.set(v.x,-v.y,-T/2);}
for(const c of data.components){const local=new THREE.Group();group('元器件').add(local);const [x,y,a]=c.position;local.position.set(x,-y,c.base_z_mm??(c.side==='bottom'?-T-.07:.07));local.rotation.z=THREE.MathUtils.degToRad(a);if(c.side==='bottom')local.scale.z=-1;const [x0,y0,x1,y1]=c.bbox_mm,bw=x1-x0,bh=y1-y0,ch=c.height_mm;
const metal=c.family==='usb';const bodyHeight=c.family==='pinheader'?Math.min(ch,2.5):ch;const body=mesh(new THREE.BoxGeometry(Math.max(bw,.1),Math.max(bh,.1),bodyHeight),mat(metal?'#abb5ba':c.family==='capacitor'?'#aa7944':c.family==='resistor'?'#26333a':'#222932',metal?.8:.08),local,{refdes:c.refdes,value:c.value,family:c.family});body.position.set((x0+x1)/2,-(y0+y1)/2,bodyHeight/2);
if(c.family==='electrolytic'){body.visible=false;const can=mesh(new THREE.CylinderGeometry(Math.min(bw,bh)/2,Math.min(bw,bh)/2,ch,24),mat('#41494d',.6),local,{refdes:c.refdes});can.rotation.x=Math.PI/2;can.position.copy(body.position);}
if(c.family==='usb'){const opening=mesh(new THREE.BoxGeometry(bw*.82,.12,ch*.6),mat('#15191d'),local,{refdes:c.refdes});opening.position.set((x0+x1)/2,-y0-.07,ch*.48);}
if(['qfn','lqfp','tqfp','soic','sot'].includes(c.family)){const mark=mesh(new THREE.CylinderGeometry(Math.min(bw,bh)*.055,Math.min(bw,bh)*.055,.025,12),mat('#8b9292'),local,{refdes:c.refdes});mark.rotation.x=Math.PI/2;mark.position.set(x0+bw*.17,-y0-bh*.17,ch+.02);}
// Metal terminations are actual raised meshes, with electrical pad identity.
for(const p of data.pads.filter(p=>p.refdes===c.refdes)){
 const xs=p.polygon.map(v=>v[0]),ys=p.polygon.map(v=>v[1]);const px=(Math.min(...xs)+Math.max(...xs))/2,py=(Math.min(...ys)+Math.max(...ys))/2;
 const pw=Math.max(...xs)-Math.min(...xs),ph=Math.max(...ys)-Math.min(...ys),pin=c.family==='pinheader';
 const lead=mesh(new THREE.BoxGeometry(pin?.55:Math.max(.15,pw*.9),pin?.55:Math.max(.15,ph*.9),pin?8:.13),mat(pin?'#d4b162':'#aeb5b8',.8,.3),group('元器件'),{refdes:c.refdes,pad:p.num,net:p.net});lead.position.set(px,-py,c.side==='bottom'?-T-(pin?2:.13):(pin?2:.13));
}
}
for(const name of Object.keys(groups)){const l=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.checked=!name.startsWith('In');groups[name].visible=input.checked;input.addEventListener('change',()=>{groups[name].visible=input.checked;render();});l.append(input,document.createTextNode(name));document.getElementById('layers').append(l);}
const scale=Math.max(W,H);function view(mode){if(mode==='top')camera.position.set(W/2,-H/2,scale*1.7);else if(mode==='side')camera.position.set(W/2,-H/2-scale*1.6,5);else camera.position.set(W/2+scale*.85,-H/2-scale,scale*.95);controls.update();}view('oblique');
document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>view(b.dataset.view));
const ray=new THREE.Raycaster(),pointer=new THREE.Vector2();let lit=[];
renderer.domElement.addEventListener('pointermove',e=>{pointer.set(e.clientX/innerWidth*2-1,-e.clientY/innerHeight*2+1);ray.setFromCamera(pointer,camera);for(const m of lit)m.material.emissive.set(0);lit=[];const hit=ray.intersectObjects(pickable).find(x=>{for(let p=x.object;p;p=p.parent)if(!p.visible)return false;return true;});if(!hit){render();return;}const u=hit.object.userData;document.getElementById('hover').textContent=[u.refdes,u.value,u.pad&&'焊盘 '+u.pad,u.net&&'网络 '+u.net].filter(Boolean).join(' · ');lit=u.net?pickable.filter(m=>m.userData.net===u.net):[hit.object];for(const m of lit)m.material.emissive.set('#665100');render();});
addEventListener('resize',()=>{camera.aspect=innerWidth/innerHeight;camera.updateProjectionMatrix();renderer.setSize(innerWidth,innerHeight);render();});
document.getElementById('status').textContent=`${data.components.length} 个立体器件 · ${data.mechanical.collision_count} 处包络碰撞 · ${data.mechanical.unverified_envelopes.length} 个高度/包络待核实`;
window.pcbViewer={ready:true,componentCount:data.components.length,trackCount:data.tracks.length,scene,view,renderer,groups,maskColor:'green'};
document.getElementById('mask-color').addEventListener('change',e=>{window.pcbViewer.maskColor=e.target.value;for(const material of maskMaterials)material.color.set(e.target.value==='black'?'#171e22':'#194c35');render();});
function render(){renderer.render(scene,camera);}controls.addEventListener('change',render);render();
