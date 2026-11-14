/* Optional remote PCM capture. Never plays remote audio through this browser. */
const voiceCapture = {enabled:false, generation:0, frames:[], tracks:new Map(), pending:new Set(), maxUsers:8};
function dropVoiceTrack(uid) {
  const entry=voiceCapture.tracks.get(String(uid));
  if(entry){try{entry.track.setAudioFrameCallback(null);}catch(_){} voiceCapture.tracks.delete(String(uid));}
  voiceCapture.frames=voiceCapture.frames.filter(frame=>frame.uid!==String(uid));
}
window.agoraResetVoiceCapture=()=>{
  voiceCapture.generation++;
  for(const uid of Array.from(voiceCapture.tracks.keys()))dropVoiceTrack(uid);
  voiceCapture.pending.clear();voiceCapture.frames=[];
};
async function captureRemoteAudio(user, mediaType) {
  if(mediaType!=='audio'||!voiceCapture.enabled||!client||voiceCapture.tracks.has(String(user.uid))||voiceCapture.pending.has(voiceCapture.generation+':'+String(user.uid)))return;
  if(voiceCapture.tracks.size+voiceCapture.pending.size>=voiceCapture.maxUsers)return;
  const joinedClient=client, generation=voiceCapture.generation, uid=String(user.uid);
  const pendingKey=generation+':'+uid;
  voiceCapture.pending.add(pendingKey);
  try{
    await joinedClient.subscribe(user,'audio');
    if(!voiceCapture.enabled||client!==joinedClient||generation!==voiceCapture.generation){
      try{await joinedClient.unsubscribe(user,'audio');}catch(_){}return;
    }
    const track=user.audioTrack;
    if(!track)return;
    const entry={track,phase:0,sum:0,count:0,buffer:new Uint8Array(3200),offset:0};
    voiceCapture.tracks.set(uid,entry);
    track.setAudioFrameCallback(audio=>{
      if(!voiceCapture.enabled||client!==joinedClient||generation!==voiceCapture.generation)return;
      const channels=Array.from({length:audio.numberOfChannels},(_,i)=>audio.getChannelData(i));
      for(let i=0;i<audio.length;i++){
        let value=0;for(const channel of channels)value+=channel[i]/channels.length;
        entry.sum+=value;entry.count++;entry.phase+=16000;
        if(entry.phase>=audio.sampleRate){
          entry.phase-=audio.sampleRate;
          const sample=Math.max(-1,Math.min(1,entry.sum/entry.count));
          const integer=Math.round(sample*(sample<0?32768:32767));
          entry.buffer[entry.offset++]=integer&255;entry.buffer[entry.offset++]=(integer>>8)&255;
          entry.sum=0;entry.count=0;
          if(entry.offset===entry.buffer.length){
            const frame={uid,at:Date.now(),generation,pcm:btoa(String.fromCharCode(...entry.buffer))};
            voiceCapture.frames.push(frame);
            if(voiceCapture.frames.length>120)voiceCapture.frames.shift();
            entry.buffer=new Uint8Array(3200);entry.offset=0;
          }
        }
      }
    },4096);
  }catch(_){if(client===joinedClient&&generation===voiceCapture.generation)dropVoiceTrack(uid);}
  finally{voiceCapture.pending.delete(pendingKey);}
}
window.agoraAttachVoiceCapture=joinedClient=>{
  joinedClient.on('user-published',(user,type)=>{if(client===joinedClient)captureRemoteAudio(user,type);});
  joinedClient.on('user-unpublished',(user,type)=>{if(client===joinedClient&&type==='audio')dropVoiceTrack(user.uid);});
  joinedClient.on('user-left',user=>{if(client===joinedClient)dropVoiceTrack(user.uid);});
};
window.agoraResumeVoiceCapture=async()=>{
  if(voiceCapture.enabled&&client)await Promise.all(client.remoteUsers.map(user=>user.hasAudio?captureRemoteAudio(user,'audio'):null));
};
window.agoraEnableVoiceCapture=async(enabled)=>{
  voiceCapture.enabled=!!enabled;
  window.agoraResetVoiceCapture();
  if(!enabled&&client){
    await Promise.all(client.remoteUsers.map(user=>client.unsubscribe(user,'audio').catch(()=>{})));
  }
  if(enabled)await window.agoraResumeVoiceCapture();
  return {ok:true,generation:voiceCapture.generation};
};
window.agoraDrainVoiceAudio=()=>({ok:true,generation:voiceCapture.generation,frames:voiceCapture.frames.splice(0),users:Array.from(voiceCapture.tracks.keys())});
window.agoraWakeTone=async()=>{
  if(!client||client.connectionState!=='CONNECTED')return {ok:false};
  const joinedClient=client;
  const context=new AudioContext();
  const destination=context.createMediaStreamDestination();
  const oscillator=context.createOscillator(),gain=context.createGain();
  oscillator.frequency.value=880;oscillator.connect(gain);gain.connect(destination);
  let tone;
  try{
    tone=AgoraRTC.createCustomAudioTrack({mediaStreamTrack:destination.stream.getAudioTracks()[0]});
    tone.setVolume(50);await context.resume();await joinedClient.publish([tone]);
    await sendCurrentVoiceState(2);
    const now=context.currentTime;
    gain.gain.setValueAtTime(0,now);gain.gain.linearRampToValueAtTime(.2,now+.015);
    gain.gain.exponentialRampToValueAtTime(.001,now+.22);
    oscillator.start(now);oscillator.stop(now+.23);
    await new Promise(resolve=>setTimeout(resolve,260));
    for(const entry of voiceCapture.tracks.values()){entry.buffer=new Uint8Array(3200);entry.offset=0;entry.phase=0;entry.sum=0;entry.count=0;}
    return {ok:true,after:Date.now(),generation:voiceCapture.generation};
  }finally{
    if(tone){try{await joinedClient.unpublish([tone]);}catch(_){}tone.close();}
    oscillator.disconnect();gain.disconnect();await context.close();
  }
};
