class PCMProcessor extends AudioWorkletProcessor {
  constructor(options = {}) {
    super();
    const processorOptions = (options && options.processorOptions) || {};
    // process() runs every render quantum (128 samples @ 16kHz ≈ 8ms).
    // chunkSize below is 512 samples (≈32ms) for PCM uplink batching.
    this.chunkSize = 512;
    this.pcmBytes = new ArrayBuffer(this.chunkSize * 2);
    this.pcmView = new DataView(this.pcmBytes);
    this.writeIndex = 0;

    // Lightweight denoiser pipeline:
    // 1) one-pole high-pass filter for HVAC/traffic rumble
    // 2) adaptive noise-gate that attenuates low-level background during non-speech
    this.denoiseEnabled = processorOptions.denoiseEnabled !== false;
    this.noiseGateFloor = Number.isFinite(processorOptions.noiseGateFloor)
      ? Math.max(0.0005, processorOptions.noiseGateFloor)
      : 0.0022;
    this.noiseGateMultiplier = Number.isFinite(processorOptions.noiseGateMultiplier)
      ? Math.max(1, processorOptions.noiseGateMultiplier)
      : 1.8;
    this.noiseGateResidualGain = Number.isFinite(processorOptions.noiseGateResidualGain)
      ? Math.min(Math.max(processorOptions.noiseGateResidualGain, 0), 1)
      : 0.16;
    this.noiseFloorEma = this.noiseGateFloor;

    const highPassCutoffHz = Number.isFinite(processorOptions.highPassCutoffHz)
      ? Math.min(Math.max(processorOptions.highPassCutoffHz, 20), 300)
      : 85;
    this.highPassEnabled = this.denoiseEnabled && highPassCutoffHz > 0;
    const dt = 1 / sampleRate;
    const rc = 1 / (2 * Math.PI * highPassCutoffHz);
    this.highPassAlpha = rc / (rc + dt);
    this.highPassPrevInput = 0;
    this.highPassPrevOutput = 0;

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
      // Noise gate threshold uses the previous frame's noise floor estimate.
      // The one-frame lag (~8ms) is inaudible and avoids a costly two-pass loop.
      const likelySpeech = this.isSpeaking || this.rmsEma >= this.vadStartThreshold * 0.75;
      const adaptiveGateThreshold = Math.max(
        this.noiseGateFloor,
        this.noiseFloorEma * this.noiseGateMultiplier
      );

      let filteredEnergy = 0;
      let energy = 0;
      for (let i = 0; i < inputChannel.length; i++) {
        const sample = Math.max(-1, Math.min(1, inputChannel[i]));

        let processed = sample;
        if (this.highPassEnabled) {
          // High-pass filter to suppress low-frequency environmental rumble.
          processed = this.highPassAlpha * (
            this.highPassPrevOutput + sample - this.highPassPrevInput
          );
          this.highPassPrevInput = sample;
          this.highPassPrevOutput = processed;
        }
        // Accumulate energy after high-pass for accurate noise floor estimation.
        filteredEnergy += processed * processed;
        if (this.denoiseEnabled && !likelySpeech) {
          const amplitude = Math.abs(processed);
          if (amplitude < adaptiveGateThreshold) {
            const normalized = adaptiveGateThreshold > 0 ? amplitude / adaptiveGateThreshold : 1;
            const gain =
              this.noiseGateResidualGain +
              (1 - this.noiseGateResidualGain) * normalized * normalized;
            processed *= gain;
          }
        }

        energy += processed * processed;
        const intSample = processed < 0
          ? processed * 0x8000
          : processed * 0x7fff;
        const clampedIntSample = Math.max(-32768, Math.min(32767, intSample));
        const encoded = Math.trunc(clampedIntSample);
        // Write explicitly as 16-bit little-endian PCM.
        this.pcmView.setInt16(this.writeIndex * 2, encoded, true);
        this.writeIndex += 1;

        if (this.writeIndex === this.chunkSize) {
          // Transfer ownership of the chunk buffer for lower postMessage overhead.
          this.port.postMessage(this.pcmBytes, [this.pcmBytes]);
          this.pcmBytes = new ArrayBuffer(this.chunkSize * 2);
          this.pcmView = new DataView(this.pcmBytes);
          this.writeIndex = 0;
        }
      }

      // Update noise floor from high-pass filtered signal (not gated output).
      const filteredRms = Math.sqrt(filteredEnergy / inputChannel.length);
      if (this.denoiseEnabled && !likelySpeech) {
        this.noiseFloorEma = this.noiseFloorEma * 0.985 + filteredRms * 0.015;
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
