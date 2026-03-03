/**
 * PCM Player AudioWorklet Processor
 *
 * Plays incoming Int16 PCM chunks from the main thread through a ring buffer.
 *
 * Key improvements over naive approach:
 * 1. Split buffering: larger startup pre-buffer + smaller re-buffer threshold
 *    for smoother response starts without long recovery gaps.
 * 2. Silence on underflow: outputs 0.0 when buffer is empty instead of
 *    repeating the last sample (which causes clicks/DC offset).
 * 3. Smooth clear: on endOfAudio (interruption), applies a brief fade-out
 *    ramp instead of hard-cutting to zero.
 *
 * Pattern references:
 * - google-gemini/live-api-web-console (schedule-ahead buffering)
 * - google/adk-samples bidi-demo (ring buffer base)
 */
class PCMPlayerProcessor extends AudioWorkletProcessor {
  constructor(options = {}) {
    super();

    // Ring buffer: 24kHz x 180 seconds max capacity.
    this.bufferSize = 24000 * 180;
    this.buffer = new Float32Array(this.bufferSize);
    this.writeIndex = 0;
    this.readIndex = 0;

    // Split buffering strategy:
    // - startup buffer is larger (smoother first response)
    // - rebuffer is smaller (faster recovery after a brief jitter underflow)
    this.startupPrebufferSamples = Math.floor(24000 * 0.12); // ~120ms
    this.rebufferSamples = Math.floor(24000 * 0.045); // ~45ms
    this.currentRebufferSamples = this.rebufferSamples;
    this.maxRebufferSamples = Math.floor(24000 * 0.12); // cap ~120ms
    this.isBuffering = true;
    this.hasPlayedAudio = false;
    this.underrunCount = 0;
    this.blocksSinceLastUnderrun = 0;
    this.statsEveryBlocks = 16; // ~85ms at 128-frame render quanta
    this.statsBlockCounter = 0;
    this.emitPlaybackStats = Boolean(
      options &&
      options.processorOptions &&
      options.processorOptions.emitPlaybackStats
    );

    // Fade-out state for smooth interruption (avoids clicks on clear).
    this.fadeOutRemaining = 0;
    this.fadeOutLength = Math.floor(24000 * 0.01); // 10ms ramp
    this.endOfAudioWriteIndex = null;

    this.port.onmessage = (event) => {
      if (event.data && event.data.command === 'enable_playback_stats') {
        this.emitPlaybackStats = Boolean(event.data.enabled);
        return;
      }

      if (event.data && event.data.command === 'endOfAudio') {
        this.endOfAudioWriteIndex = this.writeIndex;
        // Start a brief fade-out instead of hard-cutting.
        if (this._availableSamples() > 0) {
          this.fadeOutRemaining = Math.min(this.fadeOutLength, this._availableSamples());
        } else {
          // Buffer already empty — just reset.
          this.readIndex = this.endOfAudioWriteIndex;
          this.endOfAudioWriteIndex = null;
        }
        // Return to buffering state so next response pre-buffers again.
        this.isBuffering = true;
        this.hasPlayedAudio = false;
        this.currentRebufferSamples = this.rebufferSamples;
        return;
      }

      // Incoming audio data: Int16 PCM.
      if (!(event.data instanceof ArrayBuffer)) return;
      const int16Samples = new Int16Array(event.data);
      this._enqueue(int16Samples);
    };
  }

  _availableSamples() {
    if (this.writeIndex >= this.readIndex) {
      return this.writeIndex - this.readIndex;
    }
    return this.bufferSize - this.readIndex + this.writeIndex;
  }

  _enqueue(int16Samples) {
    for (let i = 0; i < int16Samples.length; i++) {
      const floatVal = int16Samples[i] / 32768;
      this.buffer[this.writeIndex] = floatVal;
      this.writeIndex = (this.writeIndex + 1) % this.bufferSize;

      // Overflow: overwrite oldest samples.
      if (this.writeIndex === this.readIndex) {
        this.readIndex = (this.readIndex + 1) % this.bufferSize;
      }
    }
  }

  _emitStats() {
    if (!this.emitPlaybackStats) {
      return;
    }
    this.port.postMessage({
      type: 'playback_stats',
      availableSamples: this._availableSamples(),
      isBuffering: this.isBuffering,
      startupPrebufferSamples: this.startupPrebufferSamples,
      currentRebufferSamples: this.currentRebufferSamples,
      underrunCount: this.underrunCount,
    });
  }

  process(inputs, outputs, parameters) {
    const output = outputs[0];
    const framesPerBlock = output[0].length;
    this.statsBlockCounter++;

    // Fade-out phase: drain remaining samples with decreasing gain.
    if (this.fadeOutRemaining > 0) {
      for (let frame = 0; frame < framesPerBlock; frame++) {
        if (this.fadeOutRemaining > 0 && this.readIndex !== this.writeIndex) {
          const gain = this.fadeOutRemaining / this.fadeOutLength;
          const sample = this.buffer[this.readIndex] * gain;
          output[0][frame] = sample;
          if (output.length > 1) output[1][frame] = sample;
          this.readIndex = (this.readIndex + 1) % this.bufferSize;
          this.fadeOutRemaining--;
        } else {
          output[0][frame] = 0;
          if (output.length > 1) output[1][frame] = 0;
        }
      }
      if (this.fadeOutRemaining <= 0) {
        // Fade complete — discard only audio queued before endOfAudio.
        if (typeof this.endOfAudioWriteIndex === 'number') {
          this.readIndex = this.endOfAudioWriteIndex;
          this.endOfAudioWriteIndex = null;
        } else {
          this.readIndex = this.writeIndex;
        }
      }
      return true;
    }

    // Pre-buffering phase: output silence until enough data accumulated.
    if (this.isBuffering) {
      const targetBuffer = this.hasPlayedAudio
        ? this.currentRebufferSamples
        : this.startupPrebufferSamples;
      if (this._availableSamples() >= targetBuffer) {
        this.isBuffering = false;
        // Fall through to normal playback.
      } else {
        // Output silence while buffering.
        for (let ch = 0; ch < output.length; ch++) {
          output[ch].fill(0);
        }
        return true;
      }
    }

    // Normal playback: read from ring buffer, silence on underflow.
    let sawUnderrun = false;
    for (let frame = 0; frame < framesPerBlock; frame++) {
      if (this.readIndex !== this.writeIndex) {
        const sample = this.buffer[this.readIndex];
        output[0][frame] = sample;
        if (output.length > 1) output[1][frame] = sample;
        this.readIndex = (this.readIndex + 1) % this.bufferSize;
        this.hasPlayedAudio = true;
      } else {
        // Underflow: output silence (not last sample — that causes clicks).
        output[0][frame] = 0;
        if (output.length > 1) output[1][frame] = 0;
        // Re-enter buffering to absorb the next batch of chunks, but with a
        // smaller threshold so short jitter doesn't create a long audible gap.
        this.isBuffering = true;
        this.hasPlayedAudio = true;
        sawUnderrun = true;
      }
    }

    if (sawUnderrun) {
      this.underrunCount += 1;
      this.blocksSinceLastUnderrun = 0;
      this.currentRebufferSamples = Math.min(
        this.maxRebufferSamples,
        Math.max(this.currentRebufferSamples, this.rebufferSamples) + Math.floor(24000 * 0.01)
      );
    } else {
      this.blocksSinceLastUnderrun += 1;
      if (this.blocksSinceLastUnderrun >= 80 && this.currentRebufferSamples > this.rebufferSamples) {
        this.currentRebufferSamples = Math.max(
          this.rebufferSamples,
          this.currentRebufferSamples - Math.floor(24000 * 0.005)
        );
        this.blocksSinceLastUnderrun = 0;
      }
    }

    if (this.statsBlockCounter >= this.statsEveryBlocks) {
      this.statsBlockCounter = 0;
      this._emitStats();
    }

    return true;
  }
}

registerProcessor('pcm-player-processor', PCMPlayerProcessor);
