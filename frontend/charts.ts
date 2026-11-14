import type {Result, Candidate} from "./types.js";

export function powerTimeline(candidate:Candidate, capacity:number, index:number) {
  const {ctx,w,h}=canvas("power-timeline");
  const left=69, step=(w-left-10)/candidate.powered.length;
  ctx.font="12px monospace";
  const tracks:[string,boolean[],string][]=[["Sunlight",candidate.sunlight,"#f4ba72"],["Powered",candidate.powered,"#73d8ae"]];
  tracks.forEach(([name,values,color],row)=>{
    const y=10+row*25;
    ctx.fillStyle="#a9bacd";ctx.fillText(name,7,y+10);
    ctx.fillStyle="#172637";ctx.fillRect(left,y,w-left-10,13);
    ctx.fillStyle=color;
    values.forEach((value,i)=>{if(value)ctx.fillRect(left+i*step,y,Math.max(.8,step),13);});
  });
  ctx.fillStyle="#a9bacd";ctx.fillText("Reserve",7,75);
  ctx.strokeStyle="#84deff";ctx.beginPath();
  candidate.reserve.forEach((value,i)=>{
    const x=left+i*step,y=h-8-value/Math.max(capacity,1)*31;
    if(i)ctx.lineTo(x,y);else ctx.moveTo(x,y);
  });
  ctx.stroke();
  ctx.fillStyle="#edf5ff";ctx.fillRect(left+index*step,5,1,h-10);
}

function canvas(id:string, ratio=1) {
  const element=document.querySelector<HTMLCanvasElement>("#"+id)!;
  const bounds=element.getBoundingClientRect();
  const w=bounds.width, h=bounds.height, dpi=devicePixelRatio*ratio;
  element.width=Math.round(w*dpi); element.height=Math.round(h*dpi);
  const ctx=element.getContext("2d")!;
  ctx.scale(dpi,dpi);
  ctx.fillStyle="#0c1520"; ctx.fillRect(0,0,w,h);
  return {element,ctx,w,h};
}

export function map(result:Result, layer:string, disabled:Set<string>, index:number, select:(c:Candidate)=>void, selected:string, zoom=1) {
  const {element,ctx,w,h}=canvas("map");
  const side=Math.min(w,h);
  const x0=(w-side)/2, y0=(h-side)/2;
  const raster=layer==="slope"?result.map.slope:result.map.elevation;
  const size=raster.length, cell=side/size*zoom;
  const offset=(side-side*zoom)/2;
  ctx.save();
  ctx.beginPath();ctx.rect(x0,y0,side,side);ctx.clip();
  ctx.strokeStyle="#84deff0d";ctx.lineWidth=1;
  for(let x=20;x<w;x+=40){ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,h);ctx.stroke();}
  for(let y=20;y<h;y+=40){ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(w,y);ctx.stroke();}
  const samples=raster.flat().filter((a):a is number=>a!==null);
  const min=Math.min(...samples), max=Math.max(...samples);
  raster.forEach((row,y)=>row.forEach((value,x)=>{
    if(value===null) ctx.fillStyle=(x+y)%3===0?"#a97943":"#473725";
    else if(layer==="confidence") ctx.fillStyle="#264d56";
    else{
      const t=(value-min)/Math.max(1,max-min);
      const neighbor=raster[Math.max(0,y-1)][Math.max(0,x-1)];
      const light=layer==="slope"?0:Math.max(-35,Math.min(35,((neighbor??value)-value)*.36));
      const c=Math.round(45+t*130+light);
      ctx.fillStyle=layer==="slope"?`rgb(${45+t*185},${45+t*100},${75-t*32})`:`rgb(${c},${c+8},${c+19})`;
    }
    ctx.fillRect(x0+offset+x*cell,y0+offset+y*cell,Math.ceil(cell),Math.ceil(cell));
  }));
  const cx=w/2, cy=h/2;
  const scale=side*zoom/(result.map.extent*2);
  const points=result.candidates.map(c=>({c,x:cx+(c.x-result.map.x)*scale,y:cy-(c.y-result.map.y)*scale}));
  const ray=(az:number,length:number,color:string,label:string)=>{
    const theta=(az+result.site.lon)*Math.PI/180;
    ctx.strokeStyle=color;ctx.setLineDash([5,5]);ctx.beginPath();ctx.moveTo(cx,cy);ctx.lineTo(cx+Math.sin(theta)*length,cy-Math.cos(theta)*length);ctx.stroke();ctx.setLineDash([]);ctx.fillStyle=color;ctx.font="12px monospace";ctx.fillText(label,cx+Math.sin(theta)*length+5,cy-Math.cos(theta)*length-5);
  };
  const blocked=result.samples.obstruction_m[index];
  ray(result.samples.earth_az[index],Math.min(side*.47,(blocked??5000)*scale),result.samples.direct[index]?"#73d8ae":"#f2848c","Earth");
  ray(result.samples.sun_az[index],side*.31,result.samples.solar[index]?"#ffd07f":"#a77d54","Sun");
  points.forEach(({c,x,y})=>{
    const active=result.recommended.includes(c.id);
    const color=disabled.has(c.id)?"#f2848c":active?"#73d8ae":"#b3c2d5";
    if(active){ctx.strokeStyle=color;ctx.lineWidth=1.7;ctx.beginPath();ctx.moveTo(cx,cy);ctx.lineTo(x,y);ctx.stroke();}
    ctx.fillStyle=color;ctx.beginPath();ctx.arc(x,y,c.id===selected?6:active?5:2.7,0,2*Math.PI);ctx.fill();
    if(c.id===selected){ctx.strokeStyle="#84deff";ctx.beginPath();ctx.arc(x,y,10,0,Math.PI*2);ctx.stroke();}
    if(disabled.has(c.id)){ctx.strokeStyle="#f2848c";ctx.beginPath();ctx.moveTo(x-5,y-5);ctx.lineTo(x+5,y+5);ctx.moveTo(x-5,y+5);ctx.lineTo(x+5,y-5);ctx.stroke();}
    if(active||c.id===selected){ctx.font="12px monospace";ctx.fillStyle="#edf5ff";ctx.fillText(c.id,x+7,y-4);}
  });
  ctx.fillStyle="#84deff";ctx.fillRect(cx-4,cy-4,8,8);
  ctx.restore();
  ctx.fillStyle="#edf5ff";ctx.font="12px monospace";ctx.fillText("GRID N ↑",x0+9,17);
  const bar=Math.min(5,Math.floor(side*.22/scale/1000));
  if(bar>0){ctx.fillRect(x0+9,y0+side-16,bar*1000*scale,2);ctx.fillText(bar+" km",x0+9,y0+side-21);}
  element.onclick=(event)=>{
    const bounds=element.getBoundingClientRect();
    let distance=13, found:Candidate|undefined;
    points.forEach(({c,x,y})=>{
      const d=Math.hypot(event.clientX-bounds.left-x,event.clientY-bounds.top-y);
      if(d<distance){distance=d;found=c;}
    });
    if(found) select(found);
  };
}

export function timeline(result:Result, network:boolean[], assisted:boolean[], index:number, zoom:number, move:(i:number)=>void) {
  const {element,ctx,w,h}=canvas("timeline");
  const tracks:[string,boolean[],string][]=[
    ["Flat horizon",result.samples.flat,"#779ebd"],["Terrain visible",result.samples.terrain,"#84deff"],
    ["Direct RF",result.samples.direct,"#73d8ae"],["Sunlight",result.samples.solar,"#f4ba72"],
    ["Relay path",assisted,"#bba9ff"],["Combined",network,"#73d8ae"],["Direct blackout",result.samples.direct.map(v=>!v),"#f2848c"],
  ];
  const start=Math.max(0,Math.min(result.times.length-zoom,index-Math.floor(zoom/2)));
  const left=Math.min(120,w*.4), span=(w-left-10)/zoom;
  ctx.font="12px monospace";
  tracks.forEach(([label,flags,color],row)=>{
    const y=5+row*(h-23)/tracks.length;
    ctx.fillStyle="#a9bacd";ctx.fillText(label,8,y+12);
    ctx.fillStyle="#172637";ctx.fillRect(left,y+2,w-left-10,12);
    let first=start;
    while(first<start+zoom){
      const flag=flags[first];
      const uncertain=row===3?!result.samples.solar_known[first]:[1,2,6].includes(row)&&result.samples.status[first]==="uncertain";
      let next=first+1;
      while(next<start+zoom&&flags[next]===flag&&(row===3?!result.samples.solar_known[next]:[1,2,6].includes(row)&&result.samples.status[next]==="uncertain")===uncertain) next++;
      if(flag||uncertain){ctx.fillStyle=uncertain?"#bd864d":color;ctx.fillRect(left+(first-start)*span,y+2,(next-first)*span,12);
        if(uncertain){const x=left+(first-start)*span,width=(next-first)*span;ctx.save();ctx.beginPath();ctx.rect(x,y+2,width,12);ctx.clip();ctx.strokeStyle="#302219";ctx.lineWidth=2;for(let t=x-12;t<x+width;t+=6){ctx.beginPath();ctx.moveTo(t,y+14);ctx.lineTo(t+12,y+2);ctx.stroke();}ctx.restore();}}
      first=next;
    }
  });
  ctx.fillStyle="#fff";ctx.fillRect(left+(index-start)*span,3,1,h-23);
  ctx.fillStyle="#a9bacd";ctx.fillText(result.times[start].slice(0,10),left,h-5);
  ctx.textAlign="right";ctx.fillText(result.times[start+zoom-1].slice(0,10),w-10,h-5);
  const timestamp=(event:MouseEvent)=>Math.max(start,Math.min(start+zoom-1,start+Math.floor((event.offsetX-left)/span)));
  element.onmousemove=e=>{element.title=result.times[timestamp(e)].replace("T"," ").replace("+00:00"," UTC");};
  element.onclick=e=>move(timestamp(e));
}

export function horizon(result:Result, index:number) {
  const {ctx,w,h}=canvas("horizon");
  const padding=34, width=w-padding-17, height=h-40;
  const values=result.horizon.elevation.filter((v):v is number=>v!==null);
  const earth=result.samples.earth_el[index], sun=result.samples.sun_el[index];
  const lo=Math.floor(Math.min(-2,earth,sun,...values)-1), hi=Math.ceil(Math.max(2,earth,sun,...values)+1);
  const px=(az:number)=>padding+az/360*width;
  const py=(el:number)=>10+(hi-el)/(hi-lo)*height;
  ctx.font="12px monospace";ctx.strokeStyle="#29435a";
  for(let line=0;line<=4;line++){
    const el=lo+(hi-lo)*line/4,y=py(el);
    ctx.beginPath();ctx.moveTo(padding,y);ctx.lineTo(w-17,y);ctx.stroke();
    ctx.fillStyle="#a9bacd";ctx.fillText(el.toFixed(1)+"°",1,y+3);
  }
  ["N 0°","E 90°","S 180°","W 270°","360°"].forEach((label,i)=>{ctx.textAlign=i===4?"right":"left";ctx.fillText(label,px(i*90)-3,h-9);});ctx.textAlign="left";
  ctx.strokeStyle="#84deff";ctx.lineWidth=1.6;ctx.beginPath();
  let open=false;
  [...result.horizon.elevation,result.horizon.elevation[0]].forEach((el,i)=>{
    if(el===null){open=false;return;}
    const x=px(i*360/result.horizon.azimuth.length);
    if(open)ctx.lineTo(x,py(el));else ctx.moveTo(x,py(el));
    open=true;
  });
  ctx.stroke();
  [[result.samples.earth_az[index],earth,"#73d8ae"],[result.samples.sun_az[index],sun,"#f4ba72"]].forEach(row=>{
    const [az,el,color]=row as [number,number,string];
    ctx.fillStyle=color;ctx.beginPath();ctx.arc(px(az),py(el),5,0,Math.PI*2);ctx.fill();
  });
}
