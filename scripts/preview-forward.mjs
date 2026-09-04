// Read-only local preview of the existing Vercel static + edge API layout.
// Public files only; no broker routes or execution endpoints are exposed.
import http from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import paper from '../api/paper.js';
import progress from '../api/progress.js';
import { forwardFixture } from '../tests/fixtures/forward-ui.mjs';
const root=path.resolve('public');
const fixtures=process.argv.includes('--fixtures');
const port=fixtures?3002:3001;
const mime={'.html':'text/html','.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml','.json':'application/json','.webmanifest':'application/manifest+json'};
http.createServer(async(req,res)=>{
  try {
    const url=new URL(req.url,`http://127.0.0.1:${port}`);
    if(req.method!=='GET'){res.writeHead(405);res.end();return;}
    if(url.pathname.startsWith('/api/')){
      if(fixtures&&url.pathname==='/api/paper'){
        const book=url.searchParams.get('book');
        if(!['v6','v10'].includes(book)){res.writeHead(404);res.end('{}');return;}
        res.writeHead(200,{'Content-Type':'application/json','Cache-Control':'no-store'});res.end(JSON.stringify(forwardFixture(book)));return;
      }
      const handler={'/api/paper':paper,'/api/progress':progress}[url.pathname];
      if(!handler){res.writeHead(404);res.end();return;}
      const response=await handler(new Request(url,{method:'GET'}));
      res.writeHead(response.status,Object.fromEntries(response.headers));res.end(await response.text());return;
    }
    let pathname=decodeURIComponent(url.pathname==='/'?'/engine-book.html':url.pathname);
    if(!path.extname(pathname))pathname+='.html';
    const file=path.resolve(root,'.'+pathname);
    if(!file.startsWith(root+path.sep)){res.writeHead(403);res.end();return;}
    let body=await fs.readFile(file);
    if(fixtures&&path.extname(file)==='.html')body=body.toString().replace('<body>','<body><div style="padding:10px;background:#5a4015;color:white;text-align:center">LOCAL UI TEST FIXTURE — synthetic balances and trades; not a live paper book</div>');
    res.writeHead(200,{'Content-Type':mime[path.extname(file)]||'application/octet-stream','Cache-Control':'no-store'});res.end(body);
  }catch{res.writeHead(404);res.end('Not found');}
}).listen(port,'127.0.0.1',()=>console.log(`Read-only ${fixtures?'synthetic-fixture':'forward workspace'} preview: http://127.0.0.1:${port}`));
