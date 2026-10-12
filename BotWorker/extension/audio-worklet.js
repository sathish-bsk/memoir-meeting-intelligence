// Memoir BotWorker audio capture — AudioWorklet processor
//
// AudioWorklet renders in 128-sample quanta (~2.67 ms at 48 kHz). Posting
// one message per quantum produces 375 IPC events per second across
// worklet.port + WebSocket, which is more than offscreen contexts handle
// cleanly. We accumulate 8 quanta = 1024 samples per channel (~21 ms)
// before posting. That gives ~47 messages/sec end-to-end, an 8× drop in
// IPC pressure with negligible latency cost for chunked recording.

const BATCH_QUANTA = 8;                    // 8 * 128 samples per channel
const BATCH_SAMPLES = BATCH_QUANTA * 128;  // 1024 samples per channel = ~21 ms

class PCMExtractor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._left = new Float32Array(BATCH_SAMPLES);
    this._right = new Float32Array(BATCH_SAMPLES);
    this._offset = 0;
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input.length) return true;
    const l = input[0];
    const r = input.length > 1 ? input[1] : input[0]; // mono → duplicate

    // If a render quantum spans a batch boundary, copy what fits and start
    // the next batch with the remainder. This handles the rare case where
    // input.length !== 128 (start/end of stream, glitches).
    let written = 0;
    while (written < l.length) {
      const space = BATCH_SAMPLES - this._offset;
      const take = Math.min(space, l.length - written);
      this._left.set(l.subarray(written, written + take), this._offset);
      this._right.set(r.subarray(written, written + take), this._offset);
      this._offset += take;
      written += take;

      if (this._offset >= BATCH_SAMPLES) {
        // Transfer ownership of the underlying ArrayBuffers; allocate
        // fresh batch buffers for the next round.
        const left = this._left;
        const right = this._right;
        this.port.postMessage({ left, right }, [left.buffer, right.buffer]);
        this._left = new Float32Array(BATCH_SAMPLES);
        this._right = new Float32Array(BATCH_SAMPLES);
        this._offset = 0;
      }
    }
    return true;
  }
}

registerProcessor("pcm-extractor", PCMExtractor);
