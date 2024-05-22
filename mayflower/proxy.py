import asyncio
import json
import sys

from mayflower.aio import run_from_sync


class JavaScriptError(Exception):
    pass


# This is the Executor, something that sits in the middle of the Bridge and is the interface for
# Python to JavaScript. This is also used by the bridge to call Python from Node.js.
class Executor:
    def __init__(self, bridge):
        self.bridge = bridge
        self.i = 0

    def queue(self, *args, **kwargs):
        asyncio.create_task(self.bridge.queue_request(*args, **kwargs))

    def ipc(self, action, ffid, attr, args=None):
        self.i += 1
        r = self.i  # unique request ts, acts as ID for response
        if action == "get":  # return obj[prop]
            self.queue(r, {"r": r, "action": "get", "ffid": ffid, "key": attr})
        if action == "init":  # return new obj[prop]
            self.queue(
                r, {"r": r, "action": "init", "ffid": ffid, "key": attr, "args": args}
            )
        if action == "inspect":  # return require('util').inspect(obj[prop])
            self.queue(r, {"r": r, "action": "inspect", "ffid": ffid, "key": attr})
        if action == "serialize":  # return JSON.stringify(obj[prop])
            self.queue(r, {"r": r, "action": "serialize", "ffid": ffid})
        if action == "keys":
            self.queue(r, {"r": r, "action": "keys", "ffid": ffid})
        if action == "raw":
            # (not really a FFID, but request ID)
            r = ffid
            asyncio.create_task(self.bridge.queue_request_raw(ffid, args))

        # Listen for a response
        while True:
            j = run_from_sync(self.bridge.read())
            if j["r"] == r:  # if this is a message for us, OK, return to Python calls
                break
            else:  # The JS API we called wants to call a Python API... so let the bridge handle it.
                asyncio.create_task(
                    self.bridge.onMessage(
                        j["r"], j["action"], j["ffid"], j["key"], j["val"]
                    )
                )

        if "error" in j:
            raise JavaScriptError(f"Access to '{attr}' failed:\n{j['error']}\n")

        return j

    def pcall(self, ffid, action, attr, args, timeout: int | None = 10):
        """
        This function does a one-pass call to JavaScript. Since we assign the FFIDs, we do not
        need to send any preliminary call to JS. We can assign them ourselves.

        We simply iterate over the arguments, and for each of the non-primitive values, we
        create new FFIDs for them, then use them as a replacement for the non-primitive arg
        objects. We can then send the request to JS and expect one response back.
        """
        self.ctr = 0
        self.i += 1
        requestId = self.i
        packet = {
            "r": self.i,
            "c": "jsi",
            "p": 1,
            "action": action,
            "ffid": ffid,
            "key": attr,
            "args": args,
        }

        def ser(arg):
            if hasattr(arg, "ffid"):
                return {"ffid": arg.ffid}
            else:
                # Anything we don't know how to serialize -- exotic or not -- treat it as an object
                return {"ffid": self.new_ffid(arg)}

        payload = json.dumps(packet, default=ser)

        res = self.ipc("raw", requestId, attr, payload)

        return res["key"], res["val"]

    def getProp(self, ffid, method):
        resp = self.ipc("get", ffid, method)
        return resp["key"], resp["val"]

    def setProp(self, ffid, method, val):
        self.pcall(ffid, "set", method, [val])
        return True

    def callProp(self, ffid, method, args, timeout=None):
        resp = self.pcall(ffid, "call", method, args, timeout)
        return resp

    def initProp(self, ffid, method, args):
        resp = self.pcall(ffid, "init", method, args)
        return resp

    def inspect(self, ffid, mode):
        resp = self.ipc("inspect", ffid, mode)
        return resp["val"]

    def keys(self, ffid):
        return (self.ipc("keys", ffid, ""))["keys"]

    def free(self, ffid):
        self.i += 1
        try:
            l = self.queue(self.i, {"r": self.i, "action": "free", "args": [ffid]})
        except ValueError:  # Event loop is dead, no need for GC
            pass

    def new_ffid(self, for_object):
        self.bridge.cur_ffid += 1
        self.bridge.m[self.bridge.cur_ffid] = for_object
        return self.bridge.cur_ffid

    def get(self, ffid):
        return self.bridge.m[ffid]


INTERNAL_VARS = ["ffid", "_ix", "_exe", "_pffid", "_pname", "_es6", "~class", "_Keys"]


# "Proxy" classes get individually instantiated for every thread and JS object
# that exists. It interacts with an Executor to communicate.
class Proxy(object):
    def __init__(self, exe, ffid, prop_ffid=None, prop_name="", es6=False):
        self.ffid = ffid
        self._exe = exe
        self._ix = 0
        #
        self._pffid = prop_ffid if (prop_ffid != None) else ffid
        self._pname = prop_name
        self._es6 = es6
        self._Keys = None

    def _call(self, method, methodType, val):
        this = self

        # debug("MT", method, methodType, val)
        if methodType == "fn":
            return Proxy(self._exe, val, self.ffid, method)
        if methodType == "class":
            return Proxy(self._exe, val, es6=True)
        if methodType == "obj":
            return Proxy(self._exe, val)
        if methodType == "inst":
            return Proxy(self._exe, val)
        if methodType == "void":
            return None
        if methodType == "py":
            return self._exe.get(val)
        else:
            return val

    def __call__(self, *args, timeout=10):
        mT, v = (
            self._exe.initProp(self._pffid, self._pname, args)
            if self._es6
            else self._exe.callProp(self._pffid, self._pname, args, timeout)
        )
        if mT == "fn":
            return Proxy(self._exe, v)
        return self._call(self._pname, mT, v)

    def __getattr__(self, attr):
        # Special handling for new keyword for ES5 classes
        if attr == "new":
            return self._call(
                self._pname if self._pffid == self.ffid else "",
                "class",
                self._pffid,
            )
        methodType, val = self._exe.getProp(self._pffid, attr)
        return self._call(attr, methodType, val)

    def __getitem__(self, attr):
        methodType, val = self._exe.getProp(self.ffid, attr)
        return self._call(attr, methodType, val)

    def __iter__(self):
        self._ix = 0
        if self.length == None:
            self._Keys = self._exe.keys(self.ffid)
        return self

    def __next__(self):
        if self._Keys:
            if self._ix < len(self._Keys):
                result = self._Keys[self._ix]
                self._ix += 1
                return result
            else:
                raise StopIteration
        elif self._ix < self.length:
            result = self[self._ix]
            self._ix += 1
            return result
        else:
            raise StopIteration

    def __setattr__(self, name, value):
        if name in INTERNAL_VARS:
            object.__setattr__(self, name, value)
        else:
            return self._exe.setProp(self.ffid, name, value)

    def __setitem__(self, name, value):
        return self._exe.setProp(self.ffid, name, value)

    def __contains__(self, key):
        return True if self[key] is not None else False

    def valueOf(self):
        ser = self._exe.ipc("serialize", self.ffid, "")
        return ser["val"]

    def __str__(self):
        return self._exe.inspect(self.ffid, "str")

    def __repr__(self):
        return self._exe.inspect(self.ffid, "repr")

    def __json__(self):
        return {"ffid": self.ffid}

    def __del__(self):
        if not sys.is_finalizing():
            self._exe.free(self.ffid)
