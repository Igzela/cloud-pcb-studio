import {chromium} from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';

const directory=path.resolve(process.argv[2]);
const errors=[],requests=[];
const browser=await chromium.launch({headless:true,args:['--no-sandbox','--disable-dev-shm-usage','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
const page=await browser.newPage({viewport:{width:1440,height:1000},deviceScaleFactor:1});
page.on('pageerror',e=>errors.push(String(e)));
page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
await page.route(/^https?:/,route=>{requests.push(route.request().url());route.abort();});
let result;
try{
  await page.goto(pathToFileURL(path.join(directory,'assembly.html')).href,{waitUntil:'load'});
  await page.waitForFunction(()=>window.pcbViewer?.ready,{timeout:20000});
  const metrics=await page.evaluate(()=>{
    const v=window.pcbViewer;let meshes=0,nonFlat=0;
    v.groups['元器件'].traverse(m=>{if(m.isMesh){meshes++;m.geometry.computeBoundingBox();if(m.geometry.boundingBox.max.z-m.geometry.boundingBox.min.z>.1)nonFlat++;}});
    return {componentCount:v.componentCount,trackCount:v.trackCount,componentMeshes:meshes,nonFlatMeshes:nonFlat,webgl:!!v.renderer.getContext(),canvasWidth:v.renderer.domElement.width};
  });
  for(const view of ['oblique','top','side']){
    await page.evaluate(mode=>window.pcbViewer.view(mode),view);
    await page.waitForTimeout(200);
    await page.screenshot({path:path.join(directory,`assembly_${view}.png`)});
  }
  await page.locator('#mask-color').selectOption('black');
  const maskSwitchWorked=await page.evaluate(()=>window.pcbViewer.maskColor==='black');
  await page.evaluate(()=>window.pcbViewer.view('oblique'));
  await page.screenshot({path:path.join(directory,'assembly_black.png')});
  await page.locator('#mask-color').selectOption('green');
  const before=await page.locator('#layers input').first().isChecked();
  await page.locator('#layers input').first().click();
  const toggled=await page.locator('#layers input').first().isChecked()!==before;
  await page.locator('#layers input').first().click();
  await page.mouse.move(1000,500);await page.mouse.down();await page.mouse.move(1050,550);await page.mouse.up();await page.mouse.wheel(0,-100);
  result={...metrics,layerToggleWorked:toggled,maskSwitchWorked,console_errors:errors,external_requests:requests,passed:metrics.webgl&&metrics.componentCount>0&&metrics.nonFlatMeshes>=metrics.componentCount&&toggled&&maskSwitchWorked&&errors.length===0&&requests.length===0};
}catch(e){result={passed:false,error:String(e),console_errors:errors,external_requests:requests};}
finally{await browser.close();}
fs.writeFileSync(path.join(directory,'assembly_browser_report.json'),JSON.stringify(result,null,2)+'\n');
console.log(JSON.stringify(result));if(!result.passed)process.exitCode=1;
