/** Curated orientation landmarks, not a complete lunar gazetteer.
 * Named feature centers: IAU/USGS Gazetteer; research points match moonmesh/data.py.
 */
export interface Landmark { id:string; name:string; lat:number; lon:number; kind:string; description:string; source?:string; minor?:boolean }
const usgs=(id:number)=>`https://planetarynames.wr.usgs.gov/Feature/${id}`;
export const LANDMARKS:Landmark[]=[
 {id:'tranquillitatis',name:'Mare Tranquillitatis',lat:8.35,lon:30.83,kind:'Lunar sea',description:'Sea of Tranquility. A named mare on the near side of the Moon.',source:usgs(3691)},
 {id:'imbrium',name:'Mare Imbrium',lat:34.72,lon:-14.91,kind:'Lunar sea',description:'Sea of Showers. One of the large named maria on the lunar near side.',source:usgs(3678)},
 {id:'serenitatis',name:'Mare Serenitatis',lat:27.29,lon:18.36,kind:'Lunar sea',description:'Sea of Serenity. A named mare north of Mare Tranquillitatis.',source:usgs(3686)},
 {id:'crisium',name:'Mare Crisium',lat:16.18,lon:59.10,kind:'Lunar sea',description:'Sea of Crises. A named mare toward the eastern limb of the near side.',source:usgs(3671)},
 {id:'nubium',name:'Mare Nubium',lat:-20.59,lon:-17.29,kind:'Lunar sea',description:'Sea of Clouds. A named mare in the southern part of the near side.',source:usgs(3684)},
 {id:'moscoviense',name:'Mare Moscoviense',lat:27.28,lon:148.12,kind:'Far-side lunar sea',description:'A named mare on the far side. Rotate the Moon to explore this hemisphere.',source:usgs(3682)},
 {id:'tycho',name:'Tycho',lat:-43.30,lon:-11.22,kind:'Impact crater',description:'A named impact crater in the southern lunar highlands.',source:usgs(6163),minor:true},
 {id:'copernicus',name:'Copernicus',lat:9.62,lon:-20.08,kind:'Impact crater',description:'A named impact crater on the lunar near side.',source:usgs(1296),minor:true},
 {id:'north-pole',name:'North pole',lat:90,lon:0,kind:'Geographic pole',description:'90° north latitude. All lines of longitude meet at the pole.'},
 {id:'south-pole',name:'South pole',lat:-90,lon:0,kind:'Geographic pole',description:'90° south latitude. MOONMESH’s supported research regions are near this pole.'},
 {id:'far-side',name:'Far side',lat:0,lon:180,kind:'Hemisphere reference',description:'Orientation reference at 0° latitude, 180° longitude, at the center of the far-side hemisphere.'},
 {id:'haworth',name:'Haworth region',lat:-86.8,lon:337.2,kind:'Research point',description:'Approximate MOONMESH research point. Configure this region in Step 02 to calculate a network.',minor:true},
 {id:'nobile',name:'Nobile Rim 1',lat:-85.4,lon:37.3,kind:'Research point',description:'Approximate MOONMESH research point. This marker is not a tower placement or an official landing coordinate.',minor:true},
 {id:'malapert',name:'Malapert Massif',lat:-85.964,lon:357.681,kind:'Research point',description:'Approximate MOONMESH research point. Select it in mission setup to evaluate its terrain and communication.',minor:true},
];
export const coordinate=(lat:number,lon:number)=>`${Math.abs(lat).toFixed(2)}° ${lat<0?'S':'N'} · ${Math.abs(((lon+540)%360)-180).toFixed(2)}° ${((lon+540)%360)-180<0?'W':'E'}`;
