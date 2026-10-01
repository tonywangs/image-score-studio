'use strict';
const $ = id => document.getElementById(id);
const data = JSON.parse(new TextDecoder().decode(Uint8Array.from(atob($('payload').textContent), c=>c.charCodeAt(0))));
const source = data.score;
const LIMIT = 40 * 1024, HISTORY = 50;
const clone = x => JSON.parse(JSON.stringify(x));
const own = (o,k) => Object.prototype.hasOwnProperty.call(o,k);
function requireValue(ok, message) { if (!ok) throw new Error(message); }
function integer(n, lo, hi, name) { requireValue(Number.isSafeInteger(n) && n>=lo && n<=hi, `${name} must be an integer from ${lo} to ${hi}`); return n; }
function object(x) { return x !== null && typeof x==='object' && !Array.isArray(x); }
function fields(x, required, allowed=required) {
 requireValue(object(x) && required.every(k=>own(x,k)) && Object.keys(x).every(k=>allowed.includes(k)), 'Invalid fields');
}
// Integer quotient rounding with ties to even, matching Python at exact sample boundaries.
function roundEven(n,d) { const q=Math.floor(n/d), r=n-q*d; return q + (r*2>d || (r*2===d && q%2===1) ? 1 : 0); }
function normalize(spec, base=source) {
 fields(spec,['format','version'],['format','version','tempo','transpose','velocity','mute']);
 requireValue(spec.format==='image-score-edit' && spec.version===1, 'Unsupported edit format or version');
 const result={format:'image-score-edit',version:1,transpose:integer(spec.transpose??0,-119,119,'Transpose'),velocity:{},mute:{}};
 // Null is not an omitted field.
 if(own(spec,'transpose')) integer(spec.transpose,-119,119,'Transpose');
 if(own(spec,'tempo')) result.tempo=integer(spec.tempo,30,240,'Tempo');
 const ids=new Set(base.events.map((e,i)=>'n'+String(i).padStart(3,'0')));
 for(const field of ['velocity','mute']) {
  const map=own(spec,field)?spec[field]:{};
  requireValue(object(map) && Object.keys(map).length<=256, 'Invalid '+field+' map');
  for(const key of Object.keys(map).sort()) {
   requireValue(ids.has(key),'Unknown note identifier: '+key);
   if(field==='velocity') integer(map[key],0,127,'Velocity');
   else requireValue(typeof map[key]==='boolean','Mute must be boolean');
   result[field][key]=map[key];
  }
 }
 return result;
}
function transform(base, spec) {
 const edits=normalize(spec,base), result=clone(base);
 const count=result.events.length;
 integer(count,1,256,'Note count');
 result.version=2;
 result.provenance={parent_bundle_sha256:data.parent,edits};
 result.settings.tempo=edits.tempo??base.settings.tempo;
 result.tempo_us=roundEven(60000000,result.settings.tempo);
 requireValue(count*result.tempo_us<=120000000,'Edited score exceeds 60 seconds');
 result.events.forEach((e,i)=>{
  e.id='n'+String(i).padStart(3,'0');
  e.pitch=integer(e.pitch+edits.transpose,0,119,'Transposed pitch');
  e.velocity=own(edits.velocity,e.id)?edits.velocity[e.id]:e.velocity;
  e.muted=own(edits.mute,e.id)?edits.mute[e.id]:(e.muted??false);
  e.start_sample=roundEven(i*result.tempo_us*16000,2000000);
  e.end_sample=roundEven((i+1)*result.tempo_us*16000,2000000);
 });
 result.sample_count=result.events[count-1].end_sample;
 return result;
}
function stable(x) {
 if(Array.isArray(x)) return x.map(stable);
 if(object(x)) return Object.fromEntries(Object.keys(x).sort().map(k=>[k,stable(x[k])]));
 return x;
}
function canonical(x) { return JSON.stringify(stable(x),null,2)+'\n'; }
// Bounded JSON parser rejects duplicate keys and non-integer numeric syntax before
// JS loses their lexical type (Python rejects e.g. 1.0 and 1e0 for integer fields).
function parseStrict(text) {
 requireValue(new TextEncoder().encode(text).length<=LIMIT,'Import exceeds 40 KiB');
 let p=0;
 const space=()=>{while(/[\x20\t\r\n]/.test(text[p]??'x'))p++;};
 function string() {
  const begin=p++;
  while(p<text.length) {
   const c=text[p++];
   if(c==='"') return JSON.parse(text.slice(begin,p));
   if(c==='\\') p++;
  }
  throw new Error('Unterminated JSON string');
 }
 function value(depth) {
  requireValue(depth<=8,'JSON nesting exceeds limit');space();
  if(text[p]==='"') return string();
  if(text[p]==='{') {
   p++;const obj=Object.create(null);space();if(text[p]==='}') {p++;return obj;}
   while(p<text.length) {
    space();requireValue(text[p]==='"','Expected JSON key');const key=string();
    requireValue(!own(obj,key),'Duplicate JSON key');space();requireValue(text[p++]===':','Expected colon');
    obj[key]=value(depth+1);space();const end=text[p++];if(end==='}')return obj;requireValue(end===',','Expected comma');
   }
  } else {
   const match=/^(true|false|null|-?(?:0|[1-9][0-9]*))/.exec(text.slice(p));
   if(match){p+=match[0].length;return JSON.parse(match[0]);}
  }
  throw new Error('Invalid JSON; numeric values must use integer syntax');
 }
 const result=value(0);space();requireValue(p===text.length,'Invalid trailing JSON');return result;
}
function imported(text) {
 const value=parseStrict(text);
 if(value?.format==='image-score-session') {
  fields(value,['format','version','source_bundle_sha256','edits']);
  requireValue(value.version===1,'Unsupported session version');
  requireValue(value.source_bundle_sha256===data.parent,'Session belongs to a different source bundle');
  return normalize(value.edits);
 }
 requireValue(new TextEncoder().encode(text).length<=32768,'Edit specification exceeds 32 KiB');
 return normalize(value);
}
const identity=()=>({format:'image-score-edit',version:1,transpose:0,velocity:{},mute:{}});
let edits=identity(), score=transform(source,edits), undo=[], redo=[], selected=0;
let context=null, node=null, buffer=null, position=0, startTime=0, playing=false, generation=0, revision=0;
function duration(){return score.sample_count/16000;}
function currentTime(){return playing?Math.min(duration(),position+context.currentTime-startTime):position;}
function stop(){generation++;position=currentTime();playing=false;if(node){node.onended=null;node.stop();node.disconnect();node=null;}$('play').textContent='Play';}
function reportError(error){$('error').textContent=error.message;}
function attempt(fn){try{fn();$('error').textContent='';}catch(error){render();reportError(error);}}
function commit(candidate) {
 const normalized=normalize(candidate), next=transform(source,normalized);
 if(canonical(normalized)===canonical(edits))return;
 stop();undo.push(edits);if(undo.length>HISTORY)undo.shift();redo=[];
 edits=normalized;score=next;buffer=null;position=0;revision++;
 $('status').textContent='Edits applied. Playback stopped; press Play to preview.';render();
}
function travel(from,to){if(!from.length)return;stop();to.push(edits);if(to.length>HISTORY)to.shift();edits=from.pop();score=transform(source,edits);buffer=null;position=0;revision++;$('status').textContent='History restored. Playback stopped.';render();}
function updateNote() {
 const e=score.events[selected], [x0,y0,x1,y1]=e.region;
 buttons.forEach((b,i)=>{b.setAttribute('aria-current',String(i===selected));b.tabIndex=i===selected?0:-1;});
 Object.assign($('region').style,{left:100*x0/source.width+'%',top:100*y0/source.height+'%',width:100*(x1-x0)/source.width+'%',height:100*(y1-y0)/source.height+'%'});
 $('note-title').textContent='Selected note '+e.id;
 $('detail').textContent=`MIDI ${e.pitch} · velocity ${e.velocity}${e.muted?' · muted':''} · pixels [${e.region.join(', ')}).`;
 $('velocity').value=e.velocity;$('mute').checked=e.muted;
}
function clockUpdate(){const t=currentTime();$('seek').value=t;$('time').textContent=t.toFixed(2)+' s';const i=score.events.findIndex(e=>t*16000<e.end_sample), next=i<0?score.events.length-1:i;if(next!==selected){selected=next;updateNote();}}
function seek(t){stop();position=Math.min(duration(),Math.max(0,t));clockUpdate();$('status').textContent='Paused at '+position.toFixed(2)+' seconds.';}
const buttons=source.events.map((e,i)=>{
 const b=document.createElement('button');
 b.onclick=()=>{seek(score.events[i].start_sample/16000);selected=i;updateNote();};
 b.onkeydown=event=>{let j=i;if(['ArrowRight','ArrowDown'].includes(event.key))j=Math.min(i+1,buttons.length-1);else if(['ArrowLeft','ArrowUp'].includes(event.key))j=Math.max(0,i-1);else if(event.key==='Home')j=0;else if(event.key==='End')j=buttons.length-1;else return;event.preventDefault();buttons[j].click();buttons[j].focus();};
 $('notes').append(b);return b;
});
function render(){
 $('tempo').value=score.settings.tempo;$('transpose').value=edits.transpose;
 $('undo').disabled=!undo.length;$('redo').disabled=!redo.length;
 $('summary').textContent=`${score.events.length} regions · ${score.settings.tempo} BPM · ${duration().toFixed(2)} seconds`;
 $('seek').max=duration();$('seek').value=position;$('time').textContent=position.toFixed(2)+' s';
 $('history').textContent=`Undo: ${undo.length}/${HISTORY} · Redo: ${redo.length}/${HISTORY}`;
 buttons.forEach((b,i)=>{const e=score.events[i];b.textContent=`${e.id} · ${e.muted?'muted':e.velocity?'MIDI '+e.pitch:'rest'}`;});updateNote();
}
// One bounded mono Float32 buffer, at most 960,000 samples (~3.84 MB). It is
// synthesized only on Play and discarded after score changes, not on every edit.
function synthesize(ctx) {
 const result=ctx.createBuffer(1,score.sample_count,16000), samples=result.getChannelData(0);
 for(const e of score.events) {
  if(e.muted || !e.velocity)continue;
  const length=e.end_sample-e.start_sample, frequency=440*2**((e.pitch-69)/12);
  const ramp=Math.min(160,Math.floor(length/2));
  for(let i=0;i<length;i++)samples[e.start_sample+i]=Math.sin(2*Math.PI*frequency*i/16000)*.25*e.velocity/127*Math.min(1,i/ramp,(length-1-i)/ramp);
 }
 return result;
}
async function play() {
 if(playing){stop();clockUpdate();$('status').textContent='Paused';return;}
 const token=++generation;
 try {
  const Audio=window.AudioContext || window.webkitAudioContext;
  requireValue(Boolean(Audio),'Browser audio is unavailable. You can still edit and export.');
  if(!context)context=new Audio();
  await context.resume();
  if(token!==generation)return;
  requireValue(context.state==='running','Browser audio could not start. You can still edit and export.');
  if(position>=duration())position=0;
  if(!buffer)buffer=synthesize(context);
  node=context.createBufferSource();node.buffer=buffer;node.connect(context.destination);
  startTime=context.currentTime;playing=true;node.start(0,position);
  node.onended=()=>{if(token!==generation)return;position=duration();playing=false;node.disconnect();node=null;$('play').textContent='Play';$('status').textContent='Finished';clockUpdate();};
  $('play').textContent='Pause';$('status').textContent='Playing browser synthesis';$('error').textContent='';
 }catch(error){if(token!==generation)return;stop();reportError(error);}
}
function numberField(id,field){$(id).onchange=()=>attempt(()=>{const raw=$(id).value;requireValue(/^-?[0-9]+$/.test(raw),'Enter a whole number');commit({...edits,[field]:Number(raw)});});}
numberField('tempo','tempo');numberField('transpose','transpose');
$('velocity').onchange=()=>attempt(()=>{const raw=$('velocity').value;requireValue(/^[0-9]+$/.test(raw),'Enter a whole-number velocity');commit({...edits,velocity:{...edits.velocity,[score.events[selected].id]:Number(raw)}});});
$('mute').onchange=()=>attempt(()=>commit({...edits,mute:{...edits.mute,[score.events[selected].id]:$('mute').checked}}));
for(const id of ['tempo','transpose','velocity']) $(id).onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();$(id).dispatchEvent(new Event('change'));}};
// Pin the selected note while the user types instead of advancing its controls.
for(const id of ['tempo','transpose','velocity','mute']) $(id).onfocus=()=>{if(playing){stop();$('status').textContent='Paused for editing.';}};
$('reset').onclick=()=>attempt(()=>commit(identity()));
$('undo').onclick=()=>attempt(()=>travel(undo,redo));$('redo').onclick=()=>attempt(()=>travel(redo,undo));
$('play').onclick=play;$('restart').onclick=()=>seek(0);$('seek').oninput=()=>seek(Number($('seek').value));
let downloadURL=null;
function download(name,value){if(downloadURL)URL.revokeObjectURL(downloadURL);const text=canonical(value);requireValue(new TextEncoder().encode(text).length<=LIMIT,'Export too large');downloadURL=URL.createObjectURL(new Blob([text],{type:'application/json'}));const a=document.createElement('a');a.href=downloadURL;a.download=name;document.body.append(a);a.click();a.remove();$('status').textContent='Downloaded '+name;}
$('export').onclick=()=>attempt(()=>download('edits.json',edits));
$('save').onclick=()=>attempt(()=>download('session.json',{format:'image-score-session',version:1,source_bundle_sha256:data.parent,edits}));
let importRequest=0;
$('import').onchange=async()=>{
 const file=$('import').files[0], token=++importRequest, originalRevision=revision;
 if(!file)return;
 try {
  requireValue(file.size<=LIMIT,'Import exceeds 40 KiB');
  const text=new TextDecoder('utf-8',{fatal:true}).decode(await file.arrayBuffer());
  requireValue(token===importRequest && originalRevision===revision,'Score changed while reading; import again');
  commit(imported(text));$('error').textContent='';$('status').textContent='Imported successfully. Undo restores previous edits.';
 }catch(error){reportError(error);}finally{$('import').value='';}
};
window.addEventListener('pagehide',()=>{stop();if(context)context.close();if(downloadURL)URL.revokeObjectURL(downloadURL);});
$('name').textContent=source.input_name;$('image').src='data:image/png;base64,'+data.image;
$('identity').textContent='Source bundle SHA-256: '+data.parent;
render();function frame(){if(playing)clockUpdate();requestAnimationFrame(frame);}requestAnimationFrame(frame);
