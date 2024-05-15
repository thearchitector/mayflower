export class WebsocketCom {
  constructor() {
    this.handlers = {};
    this.sendQ = [];
    this.start();
  }

  async start() {
    this.sock = new WebSocket("ws://localhost:8768");
    this.sock.onmessage = (message) => {
      const msg = message.data;
      const j = JSON.parse(msg);
      if (j.c === "stderr") console.log("PyE", msg.val);
      else if (j.c === "stdout") console.log("PyO", msg.val);
      else this.receive(j);
    };
    this.sock.onopen = () => {
      // flush any messages queued during initialization
      for (const q of this.sendQ) {
        this.sock.send(q);
      }
    };
    this.sock.onerror = console.error;
  }

  receive(j) {
    console.debug("[py -> js]", j);
    if (this.handlers[j.c]) {
      return this.handlers[j.c](j);
    }
    if (this.handlers[j.r]) {
      if (this.handlers[j.r](j)) {
        return;
      }
      delete this.handlers[j.r];
    }
  }

  register(eventId, cb) {
    this.handlers[eventId] = cb;
  }

  write(what, cb) {
    const fb = JSON.stringify(what);
    this.writeRaw(fb, what.r, cb);
  }

  writeRaw(what, r, cb) {
    console.debug("[js -> py]", what);
    if (!this.sock || this.sock.readyState != 1) this.sendQ.push(what);
    else this.sock.send(what);
    this.register(r, cb);
  }

  end() {
    this.sock?.close();
  }
}
