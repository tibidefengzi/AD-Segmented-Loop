// Presentation-only helpers: never serialize caches or pointer events into generation state.
export function frameBatch(callback, request = requestAnimationFrame, cancel = cancelAnimationFrame) {
    let frame = 0, latest, pending = false;
    const flush = () => {
        if (frame) cancel(frame);
        frame = 0;
        if (!pending) return;
        pending = false;
        const value = latest;
        latest = undefined;
        callback(value);
    };
    const schedule = value => {
        latest = value;
        pending = true;
        if (!frame) frame = request(flush);
    };
    schedule.flush = flush;
    schedule.cancel = () => {
        if (frame) cancel(frame);
        frame = 0; pending = false; latest = undefined;
    };
    return schedule;
}

export class PreviewCache {
    constructor(limitBytes, idleMs = 120000, now = () => performance.now()) {
        this.limitBytes = limitBytes; this.idleMs = idleMs; this.now = now;
        this.entries = new Map(); this.bytes = 0;
    }
    get(key) {
        const entry = this.entries.get(key);
        if (!entry) return undefined;
        entry.used = this.now();
        this.entries.delete(key); this.entries.set(key, entry);
        return entry.value;
    }
    set(key, value, bytes, dispose = item => item?.close?.()) {
        this.delete(key);
        this.entries.set(key, { value, bytes, dispose, used: this.now() });
        this.bytes += bytes;
    }
    resize(key, bytes) {
        const entry = this.entries.get(key);
        if (!entry) return;
        this.bytes += bytes - entry.bytes; entry.bytes = bytes;
    }
    delete(key) {
        const entry = this.entries.get(key);
        if (!entry) return;
        this.entries.delete(key); this.bytes -= entry.bytes;
        entry.dispose(entry.value);
    }
    sweep(protectedKeys = new Set()) {
        const now = this.now();
        for (const [key, entry] of this.entries) {
            if (protectedKeys.has(key)) continue;
            if (this.bytes > this.limitBytes || now - entry.used >= this.idleMs) this.delete(key);
        }
    }
    clear() { for (const key of this.entries.keys()) this.delete(key); }
}
