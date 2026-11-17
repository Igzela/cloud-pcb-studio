import {chromium} from 'playwright';
import {pathToFileURL} from 'node:url';
import fs from 'node:fs';
const browser=await chromium.launch({headless:true,args:['--no-sandbox','--disable-dev-shm-usage','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
const results=[];
for(const number of [1,5]){
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 const errors=[];page.on('pageerror',e=>errors.push(String(e)));
 await page.goto(pathToFileURL(`${process.cwd()}/artifacts/blind_v3_case${number}/assembly.html`).href);
 await page.waitForFunction(()=>window.pcbViewer?.ready);
 await page.evaluate(()=>window.pcbViewer.view('top'));
 let found;
 for(let y=220;y<900&&!found;y+=42){for(let x=420;x<1300&&!found;x+=42){
  await page.mouse.move(x,y);
  const state=await page.evaluate(()=>{let highlighted=0;window.pcbViewer.scene.traverse(m=>{if(m.isMesh&&m.material?.emissive?.getHex()===0x665100)highlighted++;});return{text:document.querySelector('#hover').textContent,highlighted};});
  if(state.text.includes('网络 ')&&state.highlighted>0)found={x,y,...state};
 }}
 results.push({case:number,networkHoverWorked:!!found,found,errors,passed:!!found&&!errors.length});
 await page.close();
}
await browser.close();
fs.writeFileSync('artifacts/v3_validation/network-hover-check.json',JSON.stringify(results,null,2)+'\n');
console.log(JSON.stringify(results));if(results.some(r=>!r.passed))process.exitCode=1;
