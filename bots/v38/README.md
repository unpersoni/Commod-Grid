# V38 "early favs" — builder sources (no API key in here)
`python build_v38.py` joins the parts into `v38_src.py`; the notebook delivered to the user is that file with the
key inserted (cell 2), plus a Drive-mount cell and a keep-alive cell. `test_v38.py` runs it against simulated
Kalshi data (needs the websocket-client and cryptography packages).
Rules: crypto 88-95c in the first 3 minutes, commodities 85-95c in the first 8 minutes, hold to settlement,
10 contracts, one trade per market, spread <= 5c. See FINDINGS_log.md ("V38 plan").
