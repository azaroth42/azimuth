
import inspect

def get_my_info():
    code = inspect.currentframe().f_back.f_code
    qn = code.co_qualname
    # FIXME: now would need to import it as a function
    # not sure this is actually useful?
    fn = None
    if hasattr(fn, '_commands'):
        return fn._commands
    else:
        return {}

class Commands:
    def __init__(self):
        self._commands = {}

    def register_commands(self):
        for (k,v) in self._commands.items():
            (mod, cln) = k
            clss = getattr(mod, cln)
            clss.default_commands = {}
            for fn in v:
                verbs = fn._commands
                for info in verbs:
                    info['func'] = fn
                    for vb in info['verb']:
                        try:
                            if not info in clss.default_commands[vb]:
                                clss.default_commands[vb].append(info)
                        except:
                            clss.default_commands[vb] = [info]

commands = Commands()

# Decorator for commands
def make_command(verb, dobj=None, prep=None, iobj=None):
    if type(verb) is str:
        verb = [verb]
    if type(prep) is str:
        prep = [prep]

    def wrapper(func):
        clsname = func.__qualname__.split('.')[0]
        mod = inspect.getmodule(func)
        try:
            commands._commands[(mod,clsname)].append(func)
        except:
            commands._commands[(mod,clsname)] = [func]
        info = {'verb': verb, 'dobj':dobj, 'prep':prep, 'iobj':iobj}
        if not hasattr(func, '_commands'):
            setattr(func, "_commands", [info])
        else:
            func._commands.append(info)
        return func
    return wrapper
