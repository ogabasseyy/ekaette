class PCMProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    // process() runs every render quantum (128 samples @ 16kHz ≈ 8ms).
    // chunkSize below is 512 samples (≈32ms) for PCM uplink batching.
    this.chunkSize = 512;
    this.pcmBytes = new ArrayBuffer(this.chunkSize * 2);
    this.pcmView = new DataView(this.pcmBytes);
    this.writeIndex = 0;

    // Lightweight VAD for faster local barge-in signal.
    this.isSpeaking = false;
    this.rmsEma = 0;
    this.speechFrames = 0;
    this.speakingFrames = 0;
    this.silenceFrames = 0;
    this.cooldownFrames = 0;
    // Hysteresis + debounce to prevent VAD chatter on keyboard clicks / mic noise.
    this.vadStartThreshold = 0.014;
    this.vadEndThreshold = 0.0085;
    this.speechFramesForStart = 8; // ~64ms confirmation before speech_start
    this.silenceFramesForEnd = 48; // ~384ms hangover to avoid word-gap flicker
    this.minSpeakingFramesBeforeEnd = 16; // ~128ms minimum speech hold
    this.cooldownFramesAfterEnd = 12; // ~96ms guard against immediate retrigger
  }

  process(inputs, outputs, parameters) {
    if (inputs.length > 0 && inputs[0].length > 0) {
      // Use first channel (mono). Convert Float32 -> Int16 PCM little-endian.
      const inputChannel = inputs[0][0];
      let energy = 0;
      for (let i = 0; i < inputChannel.length; i++) {
        const sample = Math.max(-1, Math.min(1, inputChannel[i]));
        energy += sample * sample;
        const intSample = sample < 0
          ? sample * 0x8000
          : sample * 0x7fff;
        // Write explicitly as 16-bit little-endian PCM.
        this.pcmView.setInt16(this.writeIndex * 2, intSample, true);
        this.writeIndex += 1;

        if (this.writeIndex === this.chunkSize) {
          // Transfer ownership of the chunk buffer for lower postMessage overhead.
          this.port.postMessage(this.pcmBytes, [this.pcmBytes]);
          this.pcmBytes = new ArrayBuffer(this.chunkSize * 2);
          this.pcmView = new DataView(this.pcmBytes);
          this.writeIndex = 0;
        }
      }

      // Speech activity events for manual activity signaling.
      const rms = Math.sqrt(energy / inputChannel.length);
      this.rmsEma = this.rmsEma === 0 ? rms : this.rmsEma * 0.8 + rms * 0.2;
      const vadLevel = this.rmsEma;

      if (this.cooldownFrames > 0) {
        this.cooldownFrames -= 1;
      }

      if (!this.isSpeaking) {
        if (vadLevel >= this.vadStartThreshold && this.cooldownFrames === 0) {
          this.speechFrames += 1;
          if (this.speechFrames >= this.speechFramesForStart) {
            this.isSpeaking = true;
            this.speakingFrames = 0;
            this.silenceFrames = 0;
            this.port.postMessage({
              type: 'vad',
              state: 'speech_start',
              rms,
              vadLevel,
            });
          }
        } else {
          this.speechFrames = 0;
        }
      } else {
        this.speakingFrames += 1;
        if (vadLevel <= this.vadEndThreshold) {
          this.silenceFrames += 1;
        } else {
          this.silenceFrames = 0;
        }

        if (
          this.speakingFrames >= this.minSpeakingFramesBeforeEnd &&
          this.silenceFrames >= this.silenceFramesForEnd
        ) {
          this.isSpeaking = false;
          this.speechFrames = 0;
          this.speakingFrames = 0;
          this.silenceFrames = 0;
          this.cooldownFrames = this.cooldownFramesAfterEnd;
          this.port.postMessage({
            type: 'vad',
            state: 'speech_end',
            rms,
            vadLevel,
          });
        }
      }
    }
    return true;
  }
}

registerProcessor("pcm-recorder-processor", PCMProcessor);
