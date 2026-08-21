"""Aggressive M5 scalper — a sibling to the SLP swing bot.

The thesis: a single 1H candle is really 12 five-minute candles. If we learn how
those 12 tend to behave (where the hour's high/low forms, whether the first M5s
predict the hour, how opening-range breaks resolve), we can scalp that behavior
with tight stops and high frequency. `research.py` measures it; `strategy.py`
trades whatever the data says has an edge.
"""
