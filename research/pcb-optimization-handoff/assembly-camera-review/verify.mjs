import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {chromium} from 'playwright';
const folder=path.dirname(new URL(import.meta.url).pathname);
const browser=await chromium.launch({headless:true,args:['--no-sandbox','--disable-dev-shm-usage','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
const measurements=[];
const errors=[];
try {
  const page=await browser.newPage({viewport:{width:1440,height:1000},deviceScaleFactor:1});
  page.on('pageerror',error=>errors.push(String(error)));
  page.on('console',record=>{if(record.type()==='error')errors.push(record.text());});
  async function load(file) {
    await page.goto(pathToFileURL(path.join(folder,file)).href);
    await page.waitForFunction(()=>window.pcbViewer?.ready,{timeout:15000});
    await page.evaluate(()=>{
      const viewer=window.pcbViewer;
      const render=viewer.renderer.render.bind(viewer.renderer);
      viewer.renderer.render=(scene,camera)=>{window.testCamera=camera;return render(scene,camera);};
      viewer.view('side');
    });
  }
  async function geometry() {
    return page.evaluate(()=>{
      const v=window.pcbViewer, camera=window.testCamera;
      v.scene.updateMatrixWorld(true);
      camera.updateMatrixWorld(true);
      const minimum=[Infinity,Infinity,Infinity],maximum=[-Infinity,-Infinity,-Infinity];
      const worldZ=[Infinity,-Infinity];
      let meshes=0;
      for(const group of Object.values(v.groups)) group.traverseVisible(mesh=>{
        if(!mesh.isMesh)return;
        meshes++;
        mesh.geometry.computeBoundingBox();
        const {min,max}=mesh.geometry.boundingBox;
        for(const x of [min.x,max.x])for(const y of [min.y,max.y])for(const z of [min.z,max.z]){
          const p=min.clone().set(x,y,z).applyMatrix4(mesh.matrixWorld);
          worldZ[0]=Math.min(worldZ[0],p.z);worldZ[1]=Math.max(worldZ[1],p.z);
          const ndc=p.project(camera).toArray();
          for(let i=0;i<3;i++){
            minimum[i]=Math.min(minimum[i],ndc[i]);maximum[i]=Math.max(maximum[i],ndc[i]);
          }
        }
      });
      const fit=meshes>0&&[...minimum,...maximum].every(Number.isFinite)&&minimum.every(x=>x>=-1)&&maximum.every(x=>x<=1);
      const rectangle={
        left:(minimum[0]+1)*innerWidth/2,right:(maximum[0]+1)*innerWidth/2,
        top:(1-maximum[1])*innerHeight/2,bottom:(1-minimum[1])*innerHeight/2,
      };
      const overlayCollision=['panel','hover'].some(id=>{
        const hit=document.getElementById(id).getBoundingClientRect();
        return rectangle.left<hit.right&&rectangle.right>hit.left&&rectangle.top<hit.bottom&&rectangle.bottom>hit.top;
      });
      return {min:minimum,max:maximum,fit,meshes,worldZ,rectangle,overlayCollision,
        cameraPosition:camera.position.toArray(),viewport:[innerWidth,innerHeight],aspect:camera.aspect};
    });
  }
  await load('old-synthetic.html');
  const original=await geometry();
  measurements.push({fixture:'old-synthetic',mode:'side',...original});
  assert.equal(original.fit,false);
  await page.screenshot({path:path.join(folder,'old-synthetic-side.png')});
  await load('new-synthetic.html');
  for(const [width,height] of [[1440,1000],[900,1000],[1600,600]]) {
    await page.setViewportSize({width,height});
    await page.waitForFunction(ratio=>Math.abs(window.testCamera?.aspect-ratio)<1e-6,width/height);
    for(const mode of ['side','top','oblique']){
      await page.locator(`[data-view="${mode}"]`).click();
      const result=await geometry();
      measurements.push({fixture:'new-synthetic',mode,...result});
      assert(result.fit,`synthetic ${mode} at ${width}x${height}`);
      assert(!result.overlayCollision);
      assert(Math.abs(result.worldZ[0]+9.8)<1e-6&&Math.abs(result.worldZ[1]-10.5)<1e-6);
      if(width===1440)await page.screenshot({path:path.join(folder,`new-synthetic-${mode}.png`)});
    }
  }
  // Keep the current preset through a resize while no orbital interaction has occurred.
  await page.setViewportSize({width:700,height:950});
  await page.waitForFunction(()=>Math.abs(window.testCamera?.aspect-700/950)<1e-6);
  const resized=await geometry();
  measurements.push({fixture:'new-synthetic-resize',mode:'oblique',...resized});
  assert(resized.fit&&!resized.overlayCollision);
  // A preset is still framed when changing visible assembly layers.
  await page.locator('#layers label',{hasText:'元器件'}).locator('input').uncheck();
  const withoutComponents=await geometry();
  assert(withoutComponents.fit&&Math.abs(withoutComponents.worldZ[1])<1e-6);
  await page.locator('#layers label',{hasText:'元器件'}).locator('input').check();
  assert((await geometry()).fit);
  // Manual scroll and subsequent resize preserve the user's zoom.
  const beforeZoom=(await geometry()).cameraPosition;
  await page.mouse.move(550,700);
  await page.mouse.wheel(0,-120);
  await page.waitForFunction(before=>window.testCamera.position.toArray().some((v,i)=>Math.abs(v-before[i])>1e-5),beforeZoom);
  const zoomed=(await geometry()).cameraPosition;
  await page.setViewportSize({width:800,height:950});
  await page.waitForFunction(()=>Math.abs(window.testCamera?.aspect-800/950)<1e-6);
  assert.deepEqual((await geometry()).cameraPosition,zoomed);
  await page.setViewportSize({width:1440,height:1000});
  await load('new-existing.html');
  const existing=await geometry();
  measurements.push({fixture:'existing-small-board',mode:'side',...existing});
  assert(existing.fit&&!existing.overlayCollision);
  await page.screenshot({path:path.join(folder,'new-existing-side.png')});
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(folder,'measurements.json'),JSON.stringify({measurements,errors,passed:true},null,2)+'\n');
  console.log(JSON.stringify({passes:measurements.filter(m=>m.fit).length,expectedOriginalClip:!original.fit,oldSideMin:original.min.slice(0,2),oldSideMax:original.max.slice(0,2),existingSideMin:existing.min.slice(0,2),existingSideMax:existing.max.slice(0,2),errors}));
} finally {await browser.close();}
