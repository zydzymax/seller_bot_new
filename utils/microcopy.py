import random, yaml, os

_MC = None

def t(path: str, **kw) -> str:
    global _MC
    if _MC is None:
        with open(os.path.join(os.path.dirname(__file__), "..", "dialog", "microcopy_ru.yaml"), "r", encoding="utf-8") as f:
            _MC = yaml.safe_load(f)
    cur = _MC
    for p in path.split("."): 
        cur = cur[p]
    s = random.choice(cur) if isinstance(cur, list) else cur
    return s.format(**kw) if kw else s