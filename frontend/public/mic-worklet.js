// Microphone capture for ARC: resample to 16 kHz mono PCM16 and emit 512-sample frames
// (32 ms, Silero VAD's frame size). Runs on the audio rendering thread.
//
// Resampling averages the input samples that fall into each output sample (a box
// filter), which suppresses most aliasing that plain decimation would add.

const TARGET_RATE = 16000;
const FRAME_SAMPLES = 512;

class MicCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.step = sampleRate / TARGET_RATE; // input samples per output sample
    this.phase = 0;
    this.acc = 0;
    this.count = 0;
    this.frame = new Int16Array(FRAME_SAMPLES);
    this.filled = 0;
    this.sumSquares = 0;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;
    for (let i = 0; i < channel.length; i++) {
      this.acc += channel[i];
      this.count += 1;
      this.phase += 1;
      if (this.phase >= this.step) {
        this.phase -= this.step;
        const sample = Math.max(-1, Math.min(1, this.acc / this.count));
        this.acc = 0;
        this.count = 0;
        this.sumSquares += sample * sample;
        this.frame[this.filled++] = sample * 32767;
        if (this.filled === FRAME_SAMPLES) this.emit();
      }
    }
    return true;
  }

  emit() {
    const buffer = this.frame.buffer;
    const rms = Math.sqrt(this.sumSquares / FRAME_SAMPLES);
    this.port.postMessage({ buffer, rms }, [buffer]);
    this.frame = new Int16Array(FRAME_SAMPLES);
    this.filled = 0;
    this.sumSquares = 0;
  }
}

registerProcessor("mic-capture", MicCapture);
