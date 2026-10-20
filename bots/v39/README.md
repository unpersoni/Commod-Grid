# Commod15min V39 — open favourite with inverse markets (Oct 20 2026)

V39 is V38b V3 plus one switch, `INVERSE_MARKETS`. It's the user's experiment, run in dry run only (no live runs
planned). `commod15min_v39.py` is the bot-code cell without the API key. `history_cell.py` and `switches_cell.py`
are the notebook's HISTORY and SWITCHES cells. Everything else (rules, switches, log, HISTORY cell, live path,
tests) is as in `bots/v38b_v3/README.md`.

## INVERSE_MARKETS (default `["WTI"]`)
In these markets the bot buys the **underdog**. When one side's ask is 56-95c (the side V38b would buy), it buys the
other side at its ask (100 minus the favourite's bid), with the same timing, spread and fill-or-kill rules. Inverse
buys show `INVERSE: the underdog` on the BUY line and `(inverse)` on the SETTLED line, and have their own
"bought the UNDERDOG" line in the status and the HISTORY cell.

Why WTI: the favourite lost in every source: V38's run, the Oct 9-16 replay (-6.9c per contract, won 54.9%) and
the live run on Oct 20. Buying WTI's underdog in the replay: won 45.1% at 41c, +2.2c per contract (halves -0.8 / +5.2).
See FINDINGS_log.md, "V39 (V38b): which markets and price bands cause the losses?".

## Time of day
The status (every 15 minutes) and the HISTORY cell split each class into blocks by the market's opening time,
US Eastern:
- day: 9am-6pm;
- evening: 6pm-12am;
- night: 12am-9am.

Each block shows markets, % won and net, for example
`time of day (ET): day 9am-6pm 40 mkts 63% won +$12.30 · evening 6pm-12am 28 mkts 54% won -$9.10`.

Why: crypto evenings were the weakest block in two checks, the Oct 1-10 time-of-day check and
`analysis/v39_conditions.py` (Oct 9-14: crypto evening -5.6c per contract, day -0.8c, night +2.2c). The dry runs
show whether that holds on current markets.
