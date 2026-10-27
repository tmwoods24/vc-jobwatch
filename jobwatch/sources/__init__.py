from .getro import fetch_getro
from .consider import fetch_consider
from .lowercarbon import fetch_lowercarbon
from .traction import fetch_traction

FETCHERS = {
    "getro": fetch_getro,
    "consider": fetch_consider,
    "lowercarbon": fetch_lowercarbon,
    "traction": fetch_traction,
}
