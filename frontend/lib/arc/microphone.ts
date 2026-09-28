// Browser microphone → 16 kHz PCM16 frames (see public/mic-worklet.js).

export type MicState = "off" | "starting" | "live" | "denied" | "unavailable" | "error";

type OnFrame = (frame: ArrayBuffer, rms: number) => void;

export class Microphone {
  private ctx: AudioContext | null = null;
  private stream: MediaStream | null = null;

  async start(onFrame: OnFrame): Promise<void> {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw Object.assign(new Error("getUserMedia unavailable (needs https or localhost)"), {
        name: "NotSupportedError",
      });
    }
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        // Echo cancellation keeps the assistant's own voice from triggering barge-in.
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
    this.ctx = new AudioContext();
    await this.ctx.audioWorklet.addModule("/mic-worklet.js");
    const source = this.ctx.createMediaStreamSource(this.stream);
    const node = new AudioWorkletNode(this.ctx, "mic-capture");
    node.port.onmessage = (e: MessageEvent<{ buffer: ArrayBuffer; rms: number }>) =>
      onFrame(e.data.buffer, e.data.rms);
    source.connect(node);
    // Not connected to the destination: the mic is never played back.
  }

  async stop(): Promise<void> {
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
    await this.ctx?.close();
    this.ctx = null;
  }
}

export function micErrorState(error: unknown): MicState {
  const name = error instanceof Error ? error.name : "";
  if (name === "NotAllowedError" || name === "SecurityError") return "denied";
  if (name === "NotFoundError" || name === "NotSupportedError") return "unavailable";
  return "error";
}
