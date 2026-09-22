import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve, dirname, basename } from 'node:path';
import { createServer, request } from 'node:http';
import { spawn } from 'node:child_process';

async function start(handler) {
  const server = createServer(handler);
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  return server;
}
test('gateway serves planner while preserving score files and other service APIs', async()=>{
  const directory = await mkdtemp(join(tmpdir(),'practice-gateway-test-'));
  const upstream = await start((request,response)=>{response.setHeader('Content-Type','application/json');response.end(JSON.stringify({planner:true,path:request.url}));});
  const legacy = await start((request,response)=>{response.setHeader('Content-Type','application/json');response.end(JSON.stringify({legacy:true,path:request.url}));});
  const reservation = await start((q,r)=>r.end());
  const port=reservation.address().port;
  await new Promise(r=>reservation.close(r));
  await mkdir(join(directory,'planner'));
  await writeFile(join(directory,'index.html'),'previous score app');
  await writeFile(join(directory,'home.html'),'preserved score home');
  await writeFile(join(directory,'planner','index.html'),'academic planner');
  const child=spawn(process.execPath,[resolve('scripts/music-home-server.mjs')],{windowsHide:true,stdio:'ignore',env:{...process.env,MUSIC_PRACTICE_PORT:String(port),MUSIC_PRACTICE_SITE_ROOT:directory,PRACTICE_PORT:String(upstream.address().port),MUSIC_LEGACY_API_PORT:String(legacy.address().port),PRACTICE_SERVER_PATH:'',PRACTICE_PYTHON:''}});
  try {
    let ready=false;
    for(let i=0;i<50;i++){try{if((await fetch(`http://127.0.0.1:${port}/healthz`)).ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,30));}
    assert.ok(ready);
    const base=`http://127.0.0.1:${port}`;
    assert.equal(await (await fetch(base+'/')).text(),'academic planner');
    assert.equal(await (await fetch(base+'/home')).text(),'preserved score home');
    assert.deepEqual(await (await fetch(base+'/api/meta?t=7')).json(),{planner:true,path:'/api/meta?t=7'});
    assert.deepEqual(await (await fetch(base+'/api/chess/state')).json(),{legacy:true,path:'/api/chess/state'});
    assert.equal((await fetch(base+'/',{method:'POST'})).status,405);
    const invalidHost = await new Promise((done,reject)=>{
      const req=request(base+'/',{headers:{Host:'unexpected.test'}},response=>{response.resume();done(response.statusCode);});
      req.on('error',reject);req.end();
    });
    assert.equal(invalidHost,403);
  } finally {
    child.kill();
    await new Promise(r=>child.once('exit',r));
    await Promise.all([new Promise(r=>upstream.close(r)),new Promise(r=>legacy.close(r))]);
    assert.equal(dirname(resolve(directory)),resolve(tmpdir()));
    assert.ok(basename(directory).startsWith('practice-gateway-test-'));
    await rm(directory,{recursive:true,force:true});
  }
});
