// Synthetic playback checks: run with Node; no browser, dataset, or model calls.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(path.join(__dirname,'../swarmgraph/features_full.html'),'utf8');
const script=html.split('<script>')[1].split('</script>')[0];
const nodes=new Map();let now=0,frames=0,posts=0;
const paint=new Proxy({measureText:s=>({width:s.length*6})},{get:(o,k)=>k in o?o[k]:()=>{}});
function node(id){if(!nodes.has(id))nodes.set(id,{style:{},textContent:'',innerHTML:'',value:id==='speed'?'1':'0',checked:true,
  classList:{toggle(){}},setAttribute(){},addEventListener(){},getContext:()=>paint,
  getBoundingClientRect:()=>({width:760,height:480,left:0,top:0})});return nodes.get(id);}
const context=vm.createContext({assert,document:{getElementById:node,documentElement:{}},window:{devicePixelRatio:1,addEventListener(){}},
  getComputedStyle:()=>({getPropertyValue:()=> '#999'}),matchMedia:()=>({matches:false}),performance:{now:()=>now},
  requestAnimationFrame:()=>++frames,cancelAnimationFrame(){},setTimeout:()=>1,clearTimeout(){},
  fetch:async(url,options)=>{if(options?.method==='POST')posts++;return {ok:true,json:async()=>({status:'building'})};}});
vm.runInContext(script.slice(0,script.lastIndexOf('(async () => {')),context);
vm.runInContext(`
D={unit:'day',bins:['2030-01-01','2030-01-02','2030-01-03'],n_bin:[1,0,1],themes:['Reports','Requests'],coverage:'Synthetic fixture',
 features:[{id:'REPORT',text:'Reports progress',theme:'Reports',source:'judged',units:2,actors:1,density:1},
           {id:'ASK',text:'Requests updates',theme:'Requests',source:'judged',units:1,actors:1,density:.5}],
 profiles:{REPORT:{rate:[1,0,1],units:[1,0,1],actors:[1,0,1]},ASK:{rate:[0,0,1],units:[0,0,1],actors:[0,0,1]}},
 fpos:{REPORT:[.1,.2],ASK:[.8,.7]},units:[
 {id:1,start:'2030-01-01 08:00:00',end:'2030-01-01 08:00:00',x:.1,y:.2,f:{REPORT:1},ids:['a'],who:'Actor A'},
 {id:2,start:'2030-01-03 08:00:00',end:'2030-01-03 08:00:00',x:.8,y:.7,f:{REPORT:1,ASK:1},ids:['b'],who:'Actor A'}]};
F=Object.fromEntries(D.features.map(f=>[f.id,f]));binOf={1:0,2:2};prepareFlow();setMapMode('flow');setBin(-1);
acceptStoryResponse({status:'ready',stories:[{title:'Synthetic reports',scenes:[
 {title:'A change in reviewed work',caption:'Reports appear before and after the missing sample.',start:D.bins[0],end:D.bins[2],features:['REPORT','ASK'],events:['a','b'],caveat:'Synthetic data.'},
 {title:'Updates requested',caption:'An update request appears in the reviewed work.',start:D.bins[2],end:D.bins[2],features:['ASK'],events:['b'],caveat:''}]}]});
chooseStory(0);toggleStoryPlay();tick(0);tick(600);
assert.equal(motion.t,.5);assert.equal(cur,0);assert.equal(tour.scene,0);
`,context);
now=600;vm.runInContext('pauseStory();',context);
now=3000;vm.runInContext(`resumeStory();switchMap('umap');
assert.equal(cur,0);assert.equal(motion.t,.5);assert.equal(tour.scene,0);assert.equal(tour.playing,true);
assert.ok(sceneMeasuredNote(currentScene()).includes('Fixed positions'));
switchMap('flow');assert.ok(sceneMeasuredNote(currentScene()).includes('gaps are not connected'));
`,context);
now=3600;vm.runInContext('tick(3600);assert.equal(cur,1);assert.equal(flowPose("REPORT").observed,false);',context);
now=4800;vm.runInContext('tick(4800);assert.equal(cur,2);assert.equal(motion.sceneEnd,true);assert.equal(motion.duration,1600);',context);
now=6400;vm.runInContext('tick(6400);assert.equal(tour.scene,1);assert.equal(motion.sceneEnd,true);',context);
now=10400;vm.runInContext(`tick(10400);assert.equal(tour.complete,true);assert.equal(tour.playing,false);
replayScene();assert.equal(tour.scene,1);assert.equal(tour.complete,false);
document.getElementById('slider').oninput({target:{value:'0'}});assert.equal(tour.active,false);
chooseStory(0);toggleStoryPlay();pick('REPORT');assert.equal(tour.active,false);assert.equal(timer,null);
D.bins=['2030-01-01','2030-01-05','2030-01-06'];flowDays.REPORT[1]={x:.4,y:.4,count:1};cur=0;motion={to:1,t:.75};
assert.equal(consecutiveBins(0,1),false);assert.equal(flowPose('REPORT').x,.4);
assert.equal(consecutiveBins(1,2),true);
`,context);
// Incident actions can be outside the reviewed atlas, and use source order rather than centroid motion.
now=20000;vm.runInContext(`
D.bins=['2030-01-01','2030-01-02','2030-01-03'];
acceptStoryResponse({status:'ready',stories:[{title:'A connected exchange',question:'How did the request get an answer?',links:[
 {from_event:'unmapped',to_event:'b',basis:'reported',caption:'A later worker reports using the shared answer.'}],scenes:[
 {title:'A request and a report',caption:'A request is followed by a reported result.',start:D.bins[0],end:D.bins[2],features:[],events:['unmapped','b'],caveat:'The result is a report.',steps:[
 {event:'unmapped',when:'2030-01-01 08:01:00',role:'Requester',action:'Requests a missing value.',status:'observed',map_units:[]},
 {event:'b',when:'2030-01-03 08:00:00',role:'Responding worker',action:'Reports using the shared value.',status:'reported',map_units:[2]}],map_units:[2]}]}]});
chooseStory(0);assert.equal(currentStep().event,'unmapped');assert.equal(cur,0);
assert.equal(visibleIncidentLinks().length,0);
assert.ok(!document.getElementById('story-tour').innerHTML.includes('A later worker reports using'));
assert.ok(sceneMeasuredNote(currentScene()).includes('no reviewed stretch'));
assert.ok(document.getElementById('story-tour').innerHTML.includes('Requester'));
assert.equal(normaliseStories(storyData).status,'ready');toggleStoryPlay();tick(20000);tick(21900);
assert.equal(tour.step,0);assert.equal(motion.t,0);
`,context);
now=21900;vm.runInContext('pauseStory();',context);
now=23000;vm.runInContext('resumeStory();switchMap("umap");assert.equal(tour.step,0);assert.equal(motion.duration,4000);',context);
now=25100;vm.runInContext(`tick(25100);assert.equal(tour.step,1);assert.equal(cur,2);
assert.ok(document.getElementById('story-tour').innerHTML.includes('Reported'));
assert.deepEqual(currentStep().map_units,[2]);
assert.equal(visibleIncidentLinks().length,1);
chooseIncidentStep(0);assert.equal(tour.playing,false);assert.equal(tour.step,0);assert.equal(cur,0);
assert.equal(incidentMapPoint('unmapped'),null);assert.ok(incidentMapPoint('b'));
// Equal timestamps do not reveal a connection before its second action.
currentScene().steps[1].when=currentScene().steps[0].when;
assert.equal(visibleIncidentLinks().length,0);
chooseIncidentStep(1);assert.equal(visibleIncidentLinks().length,1);
// A source action outside the atlas is retained without a substitute bin.
acceptStoryResponse({status:'ready',stories:[{title:'Earlier raw sources',links:[],scenes:[
 {title:'Before the atlas',caption:'A raw request remains available.',start:'2029-12-31',end:'2029-12-31',features:[],events:['early'],steps:[
 {event:'early',when:'2029-12-31 08:00:00',role:'Requester',action:'Requests a value.',status:'observed',map_units:[]}]}]}]});
chooseStory(0);assert.equal(cur,-1);assert.equal(normaliseStories(storyData).stories[0].scenes.length,1);
assert.ok(sceneMeasuredNote(currentScene()).includes('outside the atlas'));
`,context);
// Loading/status alone is read-only; only the explicit action sends POST.
vm.runInContext('acceptStoryResponse({status:"missing"});',context);assert.equal(posts,0);
vm.runInContext('generateStorylines();',context);assert.equal(posts,1);
console.log('Story playback: pause, view switch, missing observations, timing, replay, manual exit, calendar gaps, explicit generation passed.');
