import { WebsocketCom } from "./interface.js";

import { Bridge } from "./bridge.js";

const com = new WebsocketCom();
const bridge = new Bridge(com);
globalThis.__pythonBridge = bridge;
const root = bridge.makePyObject(0);

export async function py(tokens, ...replacements) {
  const vars = {}; // List of locals
  let nstr = "";
  for (let i = 0; i < tokens.length; i++) {
    const token = tokens[i];
    const repl = await replacements[i];
    if (repl != null) {
      const v = "__" + i;
      vars[v] = repl.ffid ? { ffid: repl.ffid } : repl;
      nstr += token + v;
    } else {
      nstr += token;
    }
  }
  return root.eval(nstr, null, vars);
}

// same as above but with eval instead -- todo: auto fix indent
async function pyExec(tokens, ...replacements) {
  const vars = {}; // List of locals
  let nstr = "";
  for (let i = 0; i < tokens.length; i++) {
    const token = tokens[i];
    const repl = await replacements[i];
    if (repl != null) {
      const v = "__" + i;
      vars[v] = repl.ffid ? { ffid: repl.ffid } : repl;
      nstr += token + v;
    } else {
      nstr += token;
    }
  }
  return root.exec(nstr, null, vars);
}

py.enumerate = (what) => root.enumerate(what);
py.tuple = (...items) => root.tuple(items);
py.set = (...items) => root.set(items);
py.exec = pyExec;
py.with = async (using, fn) => {
  const handle = await (await using).__enter__();
  await fn(handle);
  await py`${using}.__exit__(*sys.exc_info())`;
};
py.awith = async (using, fn) => {
  const handle = await (await using).__aenter__();
  await fn(handle);
  await py`${using}.__aexit__(*sys.exc_info())`;
};

export function python(file) {
  return root.python(file);
}

python.exit = () => {
  bridge.end();
  com.end();
};

python.setFastMode = (val) => {
  root.sendInspect(!val);
};

console._log = console.log;
console.log = (...args) => {
  const nargs = [];
  for (const arg of args) {
    if (arg.ffid) nargs.push(arg.toString());
    else nargs.push(arg);
  }
  console._log(...nargs);
};
