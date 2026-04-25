export async function mergeAudioWithMusic(
  speechWavBlob: Blob,
  introFile: File | null,
  outroFile: File | null
): Promise<Blob> {
  // Use a standard sample rate for the final output
  const sampleRate = 24000;
  const ctx = new window.AudioContext({ sampleRate });

  try {
    // Decode speech
    const speechArrayBuffer = await speechWavBlob.arrayBuffer();
    const speechBuffer = await ctx.decodeAudioData(speechArrayBuffer);

    let introBuffer: AudioBuffer | null = null;
    let outroBuffer: AudioBuffer | null = null;

    if (introFile) {
      const introArrayBuffer = await introFile.arrayBuffer();
      introBuffer = await ctx.decodeAudioData(introArrayBuffer);
    }

    if (outroFile) {
      const outroArrayBuffer = await outroFile.arrayBuffer();
      outroBuffer = await ctx.decodeAudioData(outroArrayBuffer);
    }

    // Calculate total duration
    const introDuration = introBuffer ? introBuffer.duration : 0;
    const speechDuration = speechBuffer.duration;
    const outroDuration = outroBuffer ? outroBuffer.duration : 0;
    
    // Slight overlap? Or straight append? Let's do straight append with a small 0.5s crossfade or just tight append.
    // Tight append is safest for now.
    const totalDuration = introDuration + speechDuration + outroDuration;
    
    // Create an offline context to render the final audio
    const numberOfChannels = Math.max(
      speechBuffer.numberOfChannels,
      introBuffer ? introBuffer.numberOfChannels : 1,
      outroBuffer ? outroBuffer.numberOfChannels : 1
    );

    const offlineCtx = new window.OfflineAudioContext(
      numberOfChannels,
      Math.ceil(totalDuration * sampleRate),
      sampleRate
    );

    let currentTime = 0;

    // Draw Intro
    if (introBuffer) {
      const source = offlineCtx.createBufferSource();
      source.buffer = introBuffer;
      source.connect(offlineCtx.destination);
      source.start(currentTime);
      currentTime += introBuffer.duration;
    }

    // Draw Speech
    const speechSource = offlineCtx.createBufferSource();
    speechSource.buffer = speechBuffer;
    speechSource.connect(offlineCtx.destination);
    speechSource.start(currentTime);
    currentTime += speechBuffer.duration;

    // Draw Outro
    if (outroBuffer) {
      const source = offlineCtx.createBufferSource();
      source.buffer = outroBuffer;
      source.connect(offlineCtx.destination);
      source.start(currentTime);
    }

    // Render
    const renderedBuffer = await offlineCtx.startRendering();

    // Convert to WAV Blob
    return audioBufferToWavBlob(renderedBuffer);
  } finally {
    if (ctx.state !== 'closed') {
      await ctx.close();
    }
  }
}

function audioBufferToWavBlob(buffer: AudioBuffer): Blob {
  const numberOfChannels = buffer.numberOfChannels;
  const sampleRate = buffer.sampleRate;
  
  let interleaved;
  if (numberOfChannels === 2) {
    interleaved = interleave(buffer.getChannelData(0), buffer.getChannelData(1));
  } else {
    interleaved = buffer.getChannelData(0);
  }
  
  return createWavBlobFromFloat32(interleaved, numberOfChannels, sampleRate);
}

function interleave(inputL: Float32Array, inputR: Float32Array): Float32Array {
  const length = inputL.length + inputR.length;
  const result = new Float32Array(length);
  let index = 0, inputIndex = 0;
  while (index < length) {
    result[index++] = inputL[inputIndex];
    result[index++] = inputR[inputIndex];
    inputIndex++;
  }
  return result;
}

function createWavBlobFromFloat32(samples: Float32Array, channels: number, sampleRate: number): Blob {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  
  const writeString = (view: DataView, offset: number, string: string) => {
    for (let i = 0; i < string.length; i++) {
      view.setUint8(offset + i, string.charCodeAt(i));
    }
  };

  writeString(view, 0, 'RIFF');
  view.setUint32(4, 36 + samples.length * 2, true);
  writeString(view, 8, 'WAVE');
  writeString(view, 12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, channels, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * channels * 2, true);
  view.setUint16(32, channels * 2, true);
  view.setUint16(34, 16, true);
  writeString(view, 36, 'data');
  view.setUint32(40, samples.length * 2, true);
  
  let offset = 44;
  for (let i = 0; i < samples.length; i++, offset += 2) {
    let s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
  }
  
  return new Blob([buffer], { type: 'audio/wav' });
}
