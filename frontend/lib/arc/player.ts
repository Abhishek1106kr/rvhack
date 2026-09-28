import type { AudioMessage } from "./protocol";

// Plays assistant audio and reports which sentences finished playing.
//
// A sentence is ACKed only when the audio scheduled before its terminal (is_last)
// chunk has actually ended. A flush stops everything immediately; stopped audio
// is never ACKed. This is the client half of ARC's sentence-level commit rule.

type OnSentencePlayed = (turnId: string, sentenceId: number) => void;

function decodePcm16(base64: string): Float32Array<ArrayBuffer> {
  const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
  const view = new DataView(bytes.buffer);
  const samples = new Float32Array(bytes.byteLength / 2);
  for (let i = 0; i < samples.length; i++) {
    samples[i] = view.getInt16(i * 2, true) / 32768;
  }
  return samples;
}

export class SentencePlayer {
  private ctx: AudioContext | null = null;
  private playhead = 0;
  private live = new Set<AudioBufferSourceNode>();
  private stopped = new WeakSet<AudioBufferSourceNode>();
  private tail: AudioBufferSourceNode | null = null;
  private lastPlayed = new Map<string, number>();

  constructor(private readonly onSentencePlayed: OnSentencePlayed) {}

  /** Must be called from a user gesture: browsers keep audio suspended until then. */
  async unlock(): Promise<void> {
    this.ctx ??= new AudioContext();
    if (this.ctx.state !== "running") await this.ctx.resume();
  }

  get unlocked(): boolean {
    return this.ctx?.state === "running";
  }

  get playing(): boolean {
    return this.live.size > 0;
  }

  enqueue(chunk: AudioMessage): void {
    const ctx = this.ctx;
    if (chunk.data) {
      if (!ctx) return; // not unlocked: nothing plays, nothing is ACKed
      const samples = decodePcm16(chunk.data);
      const buffer = ctx.createBuffer(1, samples.length, chunk.sample_rate);
      buffer.copyToChannel(samples, 0);
      const source = ctx.createBufferSource();
      source.buffer = buffer;
      source.connect(ctx.destination);
      this.playhead = Math.max(this.playhead, ctx.currentTime);
      source.start(this.playhead);
      this.playhead += buffer.duration;
      this.live.add(source);
      source.addEventListener("ended", () => this.live.delete(source));
      this.tail = source;
    }
    if (chunk.is_last) this.ackWhenTailEnds(chunk.turn_id, chunk.sentence_id);
  }

  /** Stop and drop all audio. Returns the last sentence of `turnId` that fully played. */
  flush(turnId: string): number | null {
    for (const source of this.live) {
      this.stopped.add(source);
      source.stop();
    }
    this.live.clear();
    this.tail = null;
    this.playhead = this.ctx?.currentTime ?? 0;
    return this.lastPlayed.get(turnId) ?? null;
  }

  private ackWhenTailEnds(turnId: string, sentenceId: number): void {
    const source = this.tail;
    const ack = () => {
      this.lastPlayed.set(turnId, Math.max(sentenceId, this.lastPlayed.get(turnId) ?? 0));
      this.onSentencePlayed(turnId, sentenceId);
    };
    if (!source || !this.live.has(source)) {
      // Everything scheduled for this sentence has already ended.
      if (this.ctx) ack();
      return;
    }
    source.addEventListener("ended", () => {
      if (!this.stopped.has(source)) ack();
    });
  }
}
