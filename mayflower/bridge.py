import importlib
import importlib.util
import inspect
import traceback
from weakref import WeakValueDictionary

from .proxy import Executor, Proxy


def python(method):
    return importlib.import_module(method, package=None)


class Iterate:
    def __init__(self, v):
        self.what = v

        # If we have a normal iterator, we need to make it a generator
        if inspect.isgeneratorfunction(v):
            it = self.next_gen()
        elif hasattr(v, "__iter__"):
            it = self.next_iter()

        def next_iter():
            try:
                return next(it)
            except Exception:
                return "$$STOPITER"

        self.Next = next_iter

    def next_iter(self):
        for entry in self.what:
            yield entry
        return

    def next_gen(self):
        yield self.what()


def fix_key(key):
    return key.replace("~~", "") if isinstance(key, str) else key


class Bridge:
    m = {
        0: {
            "python": python,
            "open": open,
            "eval": eval,
            "exec": exec,
            "setattr": setattr,
            "getattr": getattr,
            "Iterate": Iterate,
            "tuple": tuple,
            "set": set,
            "enumerate": enumerate,
            "repr": repr,
        }
    }
    # Things added to this dict are auto GC'ed
    weakmap = WeakValueDictionary()
    cur_ffid = 0

    def __init__(self, ipc):
        self.ipc = ipc
        # This toggles if we want to send inspect data for console logging. It's auto
        # disabled when a for loop is active; use `repr` to request logging instead.
        self.m[0]["sendInspect"] = lambda x: setattr(self, "send_inspect", x)
        self.send_inspect = True
        self.q = lambda r, key, val, sig="": self.ipc.queue(
            {"r": r, "key": key, "val": val, "sig": sig}
        )
        self.executor = Executor(self)

        # os.JSPyBridge = Proxy(self.executor, 0)

    def assign_ffid(self, what):
        self.cur_ffid += 1
        self.m[self.cur_ffid] = what
        return self.cur_ffid

    def make_class(this, name, proxy, bases, overriden):
        def init(self):
            for base_ffid, baseArgs, baseKwargs in bases:
                base = this.m[base_ffid]
                base.__init__(self, *baseArgs, **baseKwargs)

        def getAttribute(self, attr):
            if attr.startswith("__"):
                return object.__getattribute__(self, attr)
            if attr.startswith("~~"):  # Bypass keyword for our __getattribute__ trap
                return super(clas, self).__getattribute__(attr[2:])
            if attr in overriden:
                return getattr(proxy, attr)
            return super(clas, self).__getattribute__(attr)

        def setAttr(self, attr, val):
            # Trippy stuff, but we need to set on both super and this
            # to avoid a mess
            super(clas, self).__setattr__(attr, val)
            object.__setattr__(self, attr, val)

        base_classes = []
        for base_ffid, _, _ in bases:
            base = this.m[base_ffid]
            base_classes.append(base)

        # claz = type(base_classes[0])
        clas = type(
            name,
            tuple(base_classes),
            {
                "__init__": init,
                "__getattribute__": getAttribute,
                "__setattr__": setAttr,
            },
        )
        inst = clas()
        setattr(proxy, "~class", inst)
        return inst

    # Here, we allocate two different refrences. The first is the Proxy to the JS
    # class, the send is a ref to our Python class. Both refs are GC tracked by JS.
    async def makeclass(self, r, ffid, key, params):
        self.cur_ffid += 1
        js_ffid = self.cur_ffid
        proxy = Proxy(self.executor, js_ffid)
        self.m[js_ffid] = proxy
        inst = self.make_class(
            params["name"], proxy, params["bases"], params["overriden"]
        )
        py_ffid = self.assign_ffid(inst)
        await self.q(r, "inst", [js_ffid, py_ffid])

    async def length(self, r, ffid, keys, args):
        v = self.m[ffid]
        for key in keys:
            if isinstance(v, (dict, tuple, list)):
                v = v[key]
            elif hasattr(v, str(key)):
                v = getattr(v, str(key))
            elif hasattr(v, "__getitem__"):
                try:
                    v = v[key]
                except Exception as e:
                    raise LookupError(
                        f"Property '{fix_key(key)}' does not exist on {repr(v)}"
                    ) from e
            else:
                raise LookupError(
                    f"Property '{fix_key(key)}' does not exist on {repr(v)}"
                )
        length = len(v)
        await self.q(r, "num", length)

    async def init(self, r, ffid, key, args):
        v = self.m[ffid](*args)
        ffid = self.assign_ffid(v)
        await self.q(r, "inst", ffid)

    async def call(self, r, ffid, keys, args, kwargs, invoke=True):
        v = self.m[ffid]
        # Subtle differences here depending on if we want to call or get a property.
        # Since in Python, items ([]) and attributes (.) function differently,
        # when calling first we want to try . then []
        # For example with the .append function we don't want ['append'] taking
        # precedence in a dict. However if we're only getting objects, we can
        # first try bracket for dicts, then attributes.
        if invoke:
            for key in keys:
                t = getattr(v, str(key), None)
                if t:
                    v = t
                elif hasattr(v, "__getitem__"):
                    try:
                        v = v[key]
                    except Exception as e:
                        raise LookupError(
                            f"Property '{fix_key(key)}' does not exist on {repr(v)}"
                        ) from e
                else:
                    raise LookupError(
                        f"Property '{fix_key(key)}' does not exist on {repr(v)}"
                    )
        else:
            for key in keys:
                if isinstance(v, (dict, tuple, list)):
                    v = v[key]
                elif hasattr(v, str(key)):
                    v = getattr(v, str(key))
                elif hasattr(v, "__getitem__"):
                    try:
                        v = v[key]
                    except Exception as e:
                        raise LookupError(
                            f"Property '{fix_key(key)}' does not exist on {repr(v)}"
                        ) from e
                else:
                    raise LookupError(
                        f"Property '{fix_key(key)}' does not exist on {repr(v)}"
                    )

        # Classes when called will return void, but we need to return
        # object to JS.
        was_class = False
        if invoke:
            if inspect.isclass(v):
                was_class = True

            v = v(*args, **kwargs)
            if inspect.isawaitable(v):
                v = await v

        if isinstance(v, str):
            await self.q(r, "string", v)
            return
        if isinstance(v, (int, float)) or (v is None) or (v is True) or (v is False):
            await self.q(r, "int", v)
            return
        if inspect.isclass(v) or isinstance(v, type):
            # We need to increment FFID
            await self.q(r, "class", self.assign_ffid(v), self.make_signature(v))
            return
        if callable(v):  # anything with __call__
            await self.q(r, "fn", self.assign_ffid(v), self.make_signature(v))
            return
        if (
            isinstance(v, dict) or (inspect.ismodule(v)) or was_class
        ):  # "object" in JS speak
            await self.q(r, "obj", self.assign_ffid(v), self.make_signature(v))
            return
        if isinstance(v, list):
            await self.q(r, "list", self.assign_ffid(v), self.make_signature(v))
            return
        if hasattr(v, "__class__"):  # numpy generator can't be picked up without this
            await self.q(r, "class", self.assign_ffid(v), self.make_signature(v))
            return
        await self.q(r, "void", self.cur_ffid)

    # Same as call just without invoking anything, and args
    # would be null
    async def get(self, r, ffid, keys, args):
        o = await self.call(r, ffid, keys, [], {}, invoke=False)
        return o

    async def Set(self, r, ffid, keys, args):
        v = self.m[ffid]
        on, val = args
        for key in keys:
            if isinstance(v, (dict, tuple, list)):
                v = v[key]
            elif hasattr(v, str(key)):
                v = getattr(v, str(key))
            else:
                try:
                    v = v[key]
                except Exception as e:
                    raise LookupError(
                        f"Property '{fix_key(key)}' does not exist on {repr(v)}"
                    ) from e

        if isinstance(v, (dict, tuple, list, set)):
            v[on] = val
        else:
            setattr(v, on, val)
        await self.q(r, "void", self.cur_ffid)

    async def inspect(self, r, ffid, keys, args):
        v = self.m[ffid]
        for key in keys:
            v = getattr(v, key, None) or v[key]
        s = repr(v)
        await self.q(r, "", s)

    # no ACK needed
    def free(self, r, ffid, key, args):
        for i in args:
            if i not in self.m:
                continue
            del self.m[i]

    async def make(self, r, ffid, key, args):
        self.cur_ffid += 1
        p = Proxy(self.executor, self.cur_ffid)
        # We need to put into both WeakMap and map to prevent immedate GC
        self.weakmap[self.cur_ffid] = p
        self.m[self.cur_ffid] = p
        await self.ipc.queue({"r": r, "val": self.cur_ffid})

    async def queue_request(self, request_id, payload, timeout=None):
        payload["c"] = "jsi"
        await self.ipc.queue(payload)

    async def queue_request_raw(self, request_id, payload, timeout=None):
        await self.ipc.queue(payload)

    def make_signature(self, what):
        if self.send_inspect:
            return repr(what)
        return ""

    async def read(self):
        data = await self.ipc.websocket.recv()
        if not data:
            pass  # exit()

        return self.ipc.json_loads(data)

    async def pcall(self, r, ffid, key, args, set_attr=False):
        created = {}

        # Convert special JSON objects to Python methods
        def process(json_input, lookup_key):
            if isinstance(json_input, dict):
                for k, v in json_input.items():
                    if isinstance(v, dict) and (lookup_key in v):
                        lookup = v[lookup_key]
                        if lookup == "":
                            self.cur_ffid += 1
                            proxy = (
                                self.m[v["extend"]]
                                if "extend" in v
                                else Proxy(self.executor, self.cur_ffid)
                            )
                            self.weakmap[self.cur_ffid] = proxy
                            json_input[k] = proxy
                            created[v["r"]] = self.cur_ffid
                        else:
                            json_input[k] = self.m[lookup]
                    else:
                        process(v, lookup_key)
            elif isinstance(json_input, list):
                for k, v in enumerate(json_input):
                    if isinstance(v, dict) and (lookup_key in v):
                        lookup = v[lookup_key]
                        if lookup == "":
                            self.cur_ffid += 1
                            proxy = (
                                self.m[v["extend"]]
                                if "extend" in v
                                else Proxy(self.executor, self.cur_ffid)
                            )
                            self.weakmap[self.cur_ffid] = proxy
                            json_input[k] = proxy
                            created[v["r"]] = self.cur_ffid
                        else:
                            json_input[k] = self.m[lookup]
                    else:
                        process(v, lookup_key)

        process(args, "ffid")
        pargs, kwargs = args
        if len(created):
            await self.q(r, "pre", created)
        if set_attr:
            await self.Set(r, ffid, key, pargs)
        else:
            await self.call(r, ffid, key, pargs, kwargs or {})

    async def setval(self, r, ffid, key, args):
        return await self.pcall(r, ffid, key, args, set_attr=True)

    # This returns a primitive version (JSON-serialized) of the object
    # including arrays and dictionary/object maps, unlike what the .get
    # and .call methods do where they only return numeric/strings as
    # primitive values and everything else is an object refrence.
    async def value(self, r, ffid, keys, args):
        v = self.m[ffid]

        for key in keys:
            t = getattr(v, str(key), None)
            if t is None:
                v = v[
                    key
                ]  # 🚨 If you get an error here, you called an undefined property
            else:
                v = t

        # TODO: do we realy want to worry about functions/classes here?
        # we're only supposed to send primitives, probably best to ignore
        # everything else.
        # payload = json.dumps(v, default=lambda arg: None)
        await self.q(r, "ser", v)

    async def onMessage(self, r, action, ffid, key, args):
        try:
            res_or_coro = getattr(self, action)(r, ffid, key, args)

            if inspect.isawaitable(res_or_coro):
                return await res_or_coro

            return res_or_coro
        except Exception:
            traceback.print_exc()
            await self.q(r, "error", "", traceback.format_exc())
