const fs=require('fs'), vm=require('vm'), assert=require('assert'), path=require('path');
const window={};let callback=null, subscribed=0, unsubscribed=0;
const remote={uid:123,hasAudio:true,audioTrack:{setAudioFrameCallback:(cb)=>{callback=cb;}}};
const handlers={};const client={connectionState:'CONNECTED',remoteUsers:[remote],on:(name,fn)=>{handlers[name]=fn;},subscribe:async()=>{subscribed++;},unsubscribe:async()=>{unsubscribed++;}};
let published=false,closed=false;const musicTrack={music:true};client.localTracks=[musicTrack];
client.publish=async tracks=>{published=true;client.localTracks.push(...tracks);};
client.unpublish=async tracks=>{client.localTracks=client.localTracks.filter(track=>!tracks.includes(track));};
const sound=fs.readFileSync(path.join(__dirname,'../src/oopz_sdk/assets/voice/wake_sound.mp3'));
const soundBase64=sound.toString('base64');
class AudioContext {constructor(){this.currentTime=0;}createMediaStreamDestination(){return{stream:{getAudioTracks:()=>[{}]}};}createBufferSource(){return{buffer:null,connect(){},disconnect(){},start(){assert(published);},stop(){}};}async decodeAudioData(bytes){assert(Buffer.from(bytes).equals(sound));return{duration:.679184};}async resume(){}async close(){}}

let expectedVolume=150;
const tone={setVolume(value){assert.equal(value,expectedVolume);},close(){closed=true;}};
const scope={window,client,console,Date,Map,Set,Uint8Array,Array,Promise,Math,String,AudioContext,setTimeout:fn=>fn(),AgoraRTC:{createCustomAudioTrack:()=>tone},sendCurrentVoiceState:async()=>{},atob:s=>Buffer.from(s,'base64').toString('binary'),btoa:s=>Buffer.from(s,'binary').toString('base64')};
vm.createContext(scope);vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/oopz_sdk/assets/voice/voice_control.js'),'utf8'),scope);
(async()=>{
 window.agoraAttachVoiceCapture(client);
 await handlers['user-published'](remote,'audio');assert.equal(subscribed,0);
 await window.agoraEnableVoiceCapture(true);assert.equal(subscribed,1);assert(callback);
 const samples=new Float32Array(4800).fill(.5);
 callback({numberOfChannels:1,length:samples.length,sampleRate:48000,getChannelData:()=>samples});
 let result=window.agoraDrainVoiceAudio();assert.equal(result.frames.length,1);assert.equal(result.frames[0].uid,'123');assert.equal(Buffer.from(result.frames[0].pcm,'base64').length,3200);assert.equal(Buffer.from(result.frames[0].pcm,'base64').readInt16LE(0),16384);
 for(let i=0;i<150;i++)callback({numberOfChannels:1,length:samples.length,sampleRate:48000,getChannelData:()=>samples});
 assert.equal(window.agoraDrainVoiceAudio().frames.length,120);
 handlers['user-left'](remote);assert.equal(callback,null);
 await window.agoraEnableVoiceCapture(false);assert.equal(unsubscribed,1);assert.equal(window.agoraDrainVoiceAudio().frames.length,0);
 const beep=await window.agoraWakeTone(150,soundBase64);assert(beep.ok);assert(closed);assert.deepEqual(client.localTracks,[musicTrack]);
 expectedVolume=75;await window.agoraWakeTone(75,soundBase64);assert.deepEqual(client.localTracks,[musicTrack]);
 expectedVolume=0;await window.agoraWakeTone(0,soundBase64);assert.deepEqual(client.localTracks,[musicTrack]);
 console.log('Receiver JS: opt-in subscription, 48k to 16k PCM, user isolation, bounded queue, detach and disable passed.');
})().catch(error=>{console.error(error);process.exit(1);});
