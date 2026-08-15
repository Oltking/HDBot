"""Hand-crafted SLP scenarios — validates the engine logic on clean structure,
where random synthetic data can't. Run: python -m pytest tests/ -q  (or run directly).
"""
from slp.model import Candle, Dir
from slp.strategy import SLPDetector
from slp.structure import StructureTracker, swing_at


def _c(t, o, h, l, cl):
    return Candle(time=t * 900, open=o, high=h, low=l, close=cl)


def test_swing_high_and_low():
    # index 2 is a clear swing high; index 5 a clear swing low.
    cs = [_c(0, 10, 11, 9, 10), _c(1, 10, 12, 9, 11), _c(2, 11, 15, 10, 12),
          _c(3, 12, 13, 10, 11), _c(4, 11, 12, 8, 9),  _c(5, 9, 10, 5, 6),
          _c(6, 6, 8, 5, 7),     _c(7, 7, 9, 6, 8)]
    hi = swing_at(cs, 2)
    lo = swing_at(cs, 5)
    assert hi is not None and hi.is_high and hi.price == 15
    assert lo is not None and not lo.is_high and lo.price == 5


def test_bullish_bos_detected():
    # Downtrend then a close above the last swing high => bullish BOS.
    st = StructureTracker()
    seq = [_c(0, 20, 21, 19, 20), _c(1, 20, 22, 19, 21), _c(2, 21, 23, 20, 22),  # swing high @23
           _c(3, 22, 22, 18, 19), _c(4, 19, 20, 15, 16), _c(5, 16, 17, 14, 15),  # swing low @14
           _c(6, 15, 19, 15, 18), _c(7, 18, 25, 17, 24)]  # closes above 23 -> BOS
    events = [st.update(c) for c in seq]
    fired = [e for e in events if e is not None]
    assert fired, "expected a bullish BOS"
    direction, swing = fired[-1]
    assert direction is Dir.LONG


def test_full_long_setup_fires():
    # Construct: swing low (origin) -> impulse up -> BOS -> pullback >=50% into OB.
    d = SLPDetector()
    seq = [
        _c(0, 30, 31, 29, 30), _c(1, 30, 32, 29, 31), _c(2, 31, 33, 30, 32),  # swing high ~33
        _c(3, 32, 32, 27, 28), _c(4, 28, 29, 24, 25), _c(5, 25, 26, 22, 23),  # swing low origin ~22
        _c(6, 23, 24, 22, 22),  # last bearish candle before impulse = OB (low22/high24)
        _c(7, 22, 30, 22, 29), _c(8, 29, 36, 28, 35),  # impulse up, closes above 33 -> BOS
        _c(9, 35, 36, 33, 34),
        _c(10, 34, 34, 23, 24),  # deep pullback taps OB (<=24) and >50% retrace
    ]
    setup = None
    for c in seq:
        s = d.update(c)
        if s is not None:
            setup = s
    assert setup is not None, "expected a full SLP long setup to fire"
    assert setup.direction is Dir.LONG
    assert setup.stop < setup.entry < setup.target
    assert setup.rr > 0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("all passed")
