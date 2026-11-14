import type {Result, Candidate} from "./types.js";

export function powerTimeline(candidate:Candidate, capacity:number, index:number, target:string|HTMLCanvasElement="power-timeline") {
  const {ctx,w,h}=canvas(target);
  const left=69, step=(w-left-10)/candidate.powered.length;
  ctx.font="11px monospace";
  const tracks:[string,boolean[],string][]=[["Sunlight",candidate.sunlight,"#ffca7e"],["Powered",candidate.powered,"#6ee2b4"]];
  tracks.forEach(([name,values,color],row)=>{
    const y=10+row*25;
    ctx.fillStyle="#a0b1c9";ctx.fillText(name,7,y+10);
    ctx.fillStyle="#172637";ctx.fillRect(left,y,w-left-10,13);
    ctx.fillStyle=color;
    values.forEach((value,i)=>{if(value)ctx.fillRect(left+i*step,y,Math.max(.8,step),13);});
  });
  ctx.fillStyle="#a0b1c9";ctx.fillText("Reserve",7,75);
  ctx.strokeStyle="#76d7f4";ctx.beginPath();
  candidate.reserve.forEach((value,i)=>{
    const x=left+i*step,y=h-8-value/Math.max(capacity,1)*31;
    if(i)ctx.lineTo(x,y);else ctx.moveTo(x,y);
  });
  ctx.stroke();
  ctx.fillStyle="#eaf2ff";ctx.fillRect(left+index*step,5,1,h-10);
}

// Detached canvases (used for printed reports) have no layout size, so they fall back to a fixed one.
function canvas(target:string|HTMLCanvasElement, fallback:[number,number]=[640,100]) {
  const element=typeof target==="string"?document.querySelector<HTMLCanvasElement>("#"+target)!:target;
  const bounds=element.getBoundingClientRect();
  const w=bounds.width||fallback[0], h=bounds.height||fallback[1], dpi=devicePixelRatio;
  element.width=Math.round(w*dpi); element.height=Math.round(h*dpi);
  const ctx=element.getContext("2d")!;
  ctx.scale(dpi,dpi);
  ctx.fillStyle="#081321"; ctx.fillRect(0,0,w,h);
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
  const samples=raster.flat().filter((a):a is number=>a!==null);
  const min=Math.min(...samples), max=Math.max(...samples);
  raster.forEach((row,y)=>row.forEach((value,x)=>{
    if(value===null) ctx.fillStyle="#473725";
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
  const ray=(az:number,length:number,color:string)=>{
    const theta=(az+result.site.lon)*Math.PI/180;
    ctx.strokeStyle=color;ctx.setLineDash([5,5]);ctx.beginPath();ctx.moveTo(cx,cy);ctx.lineTo(cx+Math.sin(theta)*length,cy-Math.cos(theta)*length);ctx.stroke();ctx.setLineDash([]);
  };
  const blocked=result.samples.obstruction_m[index];
  ray(result.samples.earth_az[index],Math.min(side*.47,(blocked??5000)*scale),result.samples.direct[index]?"#6ee2b4":"#f18d94");
  ray(result.samples.sun_az[index],side*.31,result.samples.solar[index]?"#ffd07f":"#a77d54");
  points.forEach(({c,x,y})=>{
    const active=result.recommended.includes(c.id);
    const color=disabled.has(c.id)?"#f18d94":active?"#6ee2b4":"#b3c2d5";
    if(active){ctx.strokeStyle=color;ctx.lineWidth=1.7;ctx.beginPath();ctx.moveTo(cx,cy);ctx.lineTo(x,y);ctx.stroke();}
    ctx.fillStyle=color;ctx.beginPath();ctx.arc(x,y,c.id===selected?6:active?5:2.7,0,2*Math.PI);ctx.fill();
    if(active||c.id===selected){ctx.font="11px monospace";ctx.fillStyle="#eaf2ff";ctx.fillText(c.id,x+7,y-4);}
  });
  ctx.fillStyle="#76d7f4";ctx.fillRect(cx-4,cy-4,8,8);
  ctx.restore();
  ctx.fillStyle="#eaf2ff";ctx.font="11px monospace";ctx.fillText("GRID N ↑",x0+9,17);
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
    ["Flat horizon",result.samples.flat,"#779ebd"],["Terrain visible",result.samples.terrain,"#76d7f4"],
    ["Direct RF",result.samples.direct,"#6ee2b4"],["Sunlight",result.samples.solar,"#ffca7e"],
    ["Relay path",assisted,"#bba9ff"],["Combined",network,"#6ee2b4"],["Direct blackout",result.samples.direct.map(v=>!v),"#f18d94"],
  ];
  const start=Math.max(0,Math.min(result.times.length-zoom,index-Math.floor(zoom/2)));
  const left=Math.min(120,w*.4), span=(w-left-10)/zoom;
  ctx.font="11px monospace";
  tracks.forEach(([label,flags,color],row)=>{
    const y=5+row*(h-23)/tracks.length;
    ctx.fillStyle="#a0b1c9";ctx.fillText(label,8,y+12);
    ctx.fillStyle="#172637";ctx.fillRect(left,y+2,w-left-10,12);
    let first=start;
    while(first<start+zoom){
      const flag=flags[first];
      const uncertain=row===3?!result.samples.solar_known[first]:[1,2,6].includes(row)&&result.samples.status[first]==="uncertain";
      let next=first+1;
      while(next<start+zoom&&flags[next]===flag&&(row===3?!result.samples.solar_known[next]:[1,2,6].includes(row)&&result.samples.status[next]==="uncertain")===uncertain) next++;
      if(flag||uncertain){ctx.fillStyle=uncertain?"#bd864d":color;ctx.fillRect(left+(first-start)*span,y+2,(next-first)*span,12);}
      first=next;
    }
  });
  ctx.fillStyle="#fff";ctx.fillRect(left+(index-start)*span,3,1,h-23);
  ctx.fillStyle="#a0b1c9";ctx.fillText(result.times[start].slice(0,10),left,h-5);
  ctx.textAlign="right";ctx.fillText(result.times[start+zoom-1].slice(0,10),w-10,h-5);
  const timestamp=(event:MouseEvent)=>Math.max(start,Math.min(start+zoom-1,start+Math.floor((event.offsetX-left)/span)));
  element.onmousemove=e=>{element.title=result.times[timestamp(e)].replace("T"," ").replace("+00:00"," UTC");};
  element.onclick=e=>move(timestamp(e));
}

export function horizon(result:Result, index:number) {
  const {ctx,w,h}=canvas("horizon");
  const s=result.samples, clearance=Number(result.config.clearance)||0, terrain=result.horizon.elevation;
  const left=40, right=w-14, top=26, bottom=h-24;
  const values=terrain.filter((v):v is number=>v!==null);
  const earth=s.earth_el[index], sun=s.sun_el[index], limit=s.earth_limit[index];
  const lo=Math.floor(Math.min(-2,earth,sun,...values)-1), hi=Math.ceil(Math.max(2,earth,sun,...values)+clearance+1);
  const ax=(az:number)=>left+az/360*(right-left);
  const px=(az:number)=>ax(((az%360)+360)%360);
  const py=(el:number)=>top+(hi-el)/(hi-lo)*(bottom-top);
  ctx.font="10px monospace";ctx.strokeStyle="#29435a";ctx.lineWidth=1;ctx.textAlign="right";
  for(let line=0;line<=4;line++){
    const el=lo+(hi-lo)*line/4,y=py(el);
    ctx.beginPath();ctx.moveTo(left,y);ctx.lineTo(right,y);ctx.stroke();
    ctx.fillStyle="#a0b1c9";ctx.fillText(el.toFixed(1)+"°",left-5,y+3);
  }
  ctx.textAlign="center";
  ["N","E","S","W","N"].forEach((label,i)=>ctx.fillText(label,ax(i*90),h-9));
  // Terrain profile, then the same profile raised by the clearance allowance.
  const profile=(offset:number)=>{
    ctx.beginPath();
    let open=false;
    [...terrain,terrain[0]].forEach((el,i)=>{
      if(el===null){open=false;return;}
      const x=ax(i*360/result.horizon.azimuth.length), y=py(el+offset);
      if(open)ctx.lineTo(x,y);else ctx.moveTo(x,y);
      open=true;
    });
    ctx.stroke();
  };
  if(clearance>0){ctx.strokeStyle="rgba(118,215,244,.5)";ctx.setLineDash([4,4]);profile(clearance);ctx.setLineDash([]);}
  ctx.strokeStyle="#76d7f4";ctx.lineWidth=1.6;profile(0);
  // Line from Earth to the elevation it has to clear in its direction.
  const ex=px(s.earth_az[index]), ey=py(earth);
  const blocked=limit!==null&&!s.terrain[index];
  const state=limit===null?"#bd864d":blocked?"#f18d94":"#6ee2b4";
  if(limit!==null){
    const need=py(limit+clearance);
    ctx.strokeStyle=state;ctx.lineWidth=2;
    ctx.beginPath();ctx.moveTo(ex,ey);ctx.lineTo(ex,need);ctx.moveTo(ex-6,need);ctx.lineTo(ex+6,need);ctx.stroke();
  }
  [[s.earth_az[index],earth,"#6ee2b4"],[s.sun_az[index],sun,"#ffca7e"]].forEach(row=>{
    const [az,el,color]=row as [number,number,string];
    ctx.fillStyle=color;ctx.beginPath();ctx.arc(px(az),py(el),5,0,Math.PI*2);ctx.fill();
  });
  const distance=s.obstruction_m[index];
  const note=limit===null?"terrain data incomplete"
    :!blocked?`clear by ${(s.earth_clearance[index]??0).toFixed(1)}°${s.direct[index]?"":" · RF margin too low"}`
    :s.reason[index]==="Earth below geometric horizon"?"below the horizon"
    :earth>limit?`inside the ${clearance}° clearance allowance`
    :`blocked by terrain${distance===null?"":` ${(distance/1000).toFixed(1)} km away`}`;
  // Keep the label inside the plot: prefer the side away from the nearest edge, then clamp.
  const label="Earth: "+note;
  ctx.font="11px monospace";
  if(ctx.measureText(label).width>right-left-8)ctx.font="10px monospace";
  ctx.fillStyle=state;ctx.textAlign="left";
  const textW=ctx.measureText(label).width;
  const preferred=ex>(left+right)/2?ex-10-textW:ex+10;
  ctx.fillText(label,Math.max(left+4,Math.min(right-textW,preferred)),Math.max(top+12,Math.min(bottom-6,ey-9)));
  // Legend
  ctx.textAlign="left";ctx.font="10px monospace";
  let x=left;
  ([["━ terrain","#76d7f4"],["┅ clearance allowance","rgba(118,215,244,.7)"],["● Earth","#6ee2b4"],["● Sun","#ffca7e"]] as [string,string][]).forEach(([label,color])=>{
    ctx.fillStyle=color;ctx.fillText(label,x,12);x+=ctx.measureText(label).width+14;
  });
}
