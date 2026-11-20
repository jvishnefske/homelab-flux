"""Level-3 ACL cases for zenohd.json5 (run by check.sh --acl).

Sessions, each with its own cert: two devices (kd240-t1, node ids a1 and
a2), a tool (tool-t1), a rogue (rogue-t1: valid CA, in no CN list) and a
browser (ws, no cert). The devices are either clients of the mesh router
(--devices client) or clients of a second zenohd that links to it
router-to-router with kd240-t1's cert (--devices router, the KD240's way).

Each device declares, like middleware's responder, both the bare key and
the per-node key `@mw/node/<id>/<key>`. A query's outcome is the set of
successful replies, error replies and whether only Zenoh's own timeout
came back.
"""

import argparse
import json
import sys
import time

import zenoh

A1 = "00000000000000a1"
A2 = "00000000000000a2"
TIMEOUT = 2.0


def session(endpoint, certs=None, cn=None, ca=None):
    conf = {
        "mode": "client",
        "connect": {"endpoints": [endpoint]},
        "scouting": {"multicast": {"enabled": False}, "gossip": {"enabled": False}},
    }
    if cn:
        conf["transport"] = {"link": {"tls": {
            "root_ca_certificate": f"{certs}/{ca or 'zenoh-ca'}.crt",
            "connect_certificate": f"{certs}/{cn}.crt",
            "connect_private_key": f"{certs}/{cn}.key",
            "enable_mtls": True,
        }}}
    return zenoh.open(zenoh.Config.from_json5(json.dumps(conf)))


# Device keys: (key, reply). "err:<text>" replies with an error.
DEVICE_KEYS = [
    ("sys/info", "info"),
    ("sys/manifest", "manifest"),
    ("sys/loader/begin", "begin"),
    ("sys/fw/begin", "fw"),
    ("sys/payload/status", "status"),
    ("sys/trust/claim", "claimed"),
    ("sys/payload/start", "err:Denied"),  # case 8: a device's error reply
    ("sys/log", "log"),
]


def device(sess, node):
    qs = []
    for key, reply in DEVICE_KEYS:
        for k in (key, f"@mw/node/{node}/{key}"):
            def cb(q, k=k, reply=reply):
                if reply.startswith("err:"):
                    q.reply_err(f"{node}:{reply[4:]}")
                else:
                    q.reply(k, f"{node}:{reply}")
            qs.append(sess.declare_queryable(k, cb))
    return qs


def ask(sess, sel):
    """(sorted ok payloads, sorted error payloads) of one get."""
    oks, errs = [], []
    for r in sess.get(sel, target=zenoh.QueryTarget.ALL,
                      consolidation=zenoh.ConsolidationMode.NONE, timeout=TIMEOUT):
        if r.ok is not None:
            oks.append(r.ok.payload.to_string())
        else:
            errs.append(r.err.payload.to_string())
    return sorted(oks), sorted(errs)


class Sink:
    def __init__(self, sess, key):
        self.got = []
        self.sub = sess.declare_subscriber(key, lambda s: self.got.append(
            (str(s.key_expr), s.payload.to_string())))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--certs", required=True)
    p.add_argument("--tls", required=True)
    p.add_argument("--ws", required=True)
    p.add_argument("--edge", required=True)
    p.add_argument("--devices", choices=["client", "router"], default="client")
    a = p.parse_args()

    if a.devices == "client":
        d1 = session(a.tls, a.certs, "kd240-t1")
        d2 = session(a.tls, a.certs, "kd240-t1")
    else:
        d1 = session(a.edge)
        d2 = session(a.edge)
    tool = session(a.tls, a.certs, "tool-t1")
    rogue = session(a.tls, a.certs, "rogue-t1")
    ws = session(a.ws)
    keep = device(d1, A1) + device(d2, A2)

    tool_state = Sink(tool, "app/proc/x/state")
    tool_cmd = Sink(tool, "app/proc/x/cmd")
    tool_unit = Sink(tool, "app/unit/x")
    rogue_state = Sink(rogue, "app/proc/x/state")
    dev_unit = Sink(d1, "app/unit/x")
    dev_cmd = Sink(d1, "app/proc/x/cmd")
    ws_state = Sink(ws, "app/proc/x/state")
    time.sleep(1.5)  # declarations propagate

    results = []

    def check(name, got, want):
        good = got == want
        results.append(good)
        print(f"{'ok  ' if good else 'BAD '} {name}: got {got}" + ("" if good else f", want {want}"))

    none = ([], [])
    both_ok = lambda v: ([f"{A1}:{v}", f"{A2}:{v}"], [])
    one_ok = lambda v: ([f"{A1}:{v}"], [])

    # 1. per-node query from the tool
    check("1 tool @mw/node/a1/sys/info", ask(tool, f"@mw/node/{A1}/sys/info"), one_ok("info"))
    check("1 tool bare sys/info (blueprint: denied)", ask(tool, "sys/info"), none)
    # 2. claim stays local
    check("2 tool sys/trust/claim", ask(tool, f"@mw/node/{A1}/sys/trust/claim"), none)
    # 3. wildcard over nodes
    check("3 tool @mw/node/*/sys/info", ask(tool, "@mw/node/*/sys/info"), both_ok("info"))
    check("3 tool @mw/node/** (wide, not covered)", ask(tool, "@mw/node/**"), none)
    check("3 tool sys/log (not listed)", ask(tool, f"@mw/node/{A1}/sys/log"), none)
    check("  tool fw/begin", ask(tool, f"@mw/node/{A1}/sys/fw/begin"), one_ok("fw"))
    # 8. a device's error reply
    check("8 tool payload/start error reply", ask(tool, f"@mw/node/{A1}/sys/payload/start"),
          ([], [f"{A1}:Denied"]))
    check("8 tool */payload/start error replies", ask(tool, "@mw/node/*/sys/payload/start"),
          ([], [f"{A1}:Denied", f"{A2}:Denied"]))
    if a.devices == "client":  # behind a router, the edge answers locally
        check("  device a1 queries device a2", ask(d1, f"@mw/node/{A2}/sys/info"), none)
    # 7. rogue: CA-valid, unlisted CN
    check("7 rogue sys/info", ask(rogue, f"@mw/node/{A1}/sys/info"), none)
    rogue_q = rogue.declare_queryable(f"@mw/node/{'f' * 16}/sys/info",
                                      lambda q: q.reply(q.key_expr, "rogue"))
    time.sleep(0.5)
    check("7 rogue queryable invisible to tool", ask(tool, "@mw/node/*/sys/info"), both_ok("info"))
    # 6. browser
    check("6 ws sys/info", ask(ws, f"@mw/node/{A1}/sys/info"), one_ok("info"))
    check("6 ws loader/begin", ask(ws, f"@mw/node/{A1}/sys/loader/begin"), one_ok("begin"))
    check("6 ws fw/begin", ask(ws, f"@mw/node/{A1}/sys/fw/begin"), none)
    check("6 ws payload/status", ask(ws, f"@mw/node/{A1}/sys/payload/status"), none)
    check("6 ws payload/start error reply", ask(ws, f"@mw/node/{A1}/sys/payload/start"),
          ([], [f"{A1}:Denied"]))
    check("6 ws bare sys/info", ask(ws, "sys/info"), none)

    # A prefix queryable, as the RT1050 firmware declares (@mw/node/<id>/sys/**):
    # visible, and still only the listed keys reach it.
    A3 = "00000000000000a3"
    pre = d2.declare_queryable(f"@mw/node/{A3}/sys/**",
                               lambda q: q.reply(q.key_expr, f"{A3}:{q.key_expr}"))
    time.sleep(0.5)
    check("  tool query on a prefix queryable", ask(tool, f"@mw/node/{A3}/sys/info"),
          ([f"{A3}:@mw/node/{A3}/sys/info"], []))
    check("  ws query on a prefix queryable", ask(ws, f"@mw/node/{A3}/sys/manifest"),
          ([f"{A3}:@mw/node/{A3}/sys/manifest"], []))
    check("  tool claim on a prefix queryable", ask(tool, f"@mw/node/{A3}/sys/trust/claim"), none)
    check("  ws fw/begin on a prefix queryable", ask(ws, f"@mw/node/{A3}/sys/fw/begin"), none)
    del pre

    # 4/5. publications
    d1.put("app/proc/x/state", "s1")
    d1.put("app/proc/x/cmd", "c1")
    d1.put("app/unit/x", "u1")
    tool.put("app/unit/x", "u2")
    tool.put("app/proc/x/cmd", "c2")
    rogue.put("app/proc/x/cmd", "c3")
    ws.put("app/proc/x/cmd", "c4")
    ws.put("app/proc/x/state", "s4")
    time.sleep(1.0)
    # A session's own puts reach its own subscribers inside the session, so
    # only the other sessions' payloads count.
    foreign = lambda sink, own: [g for g in sink.got if g[1] not in own]
    check("5 tool receives device state", foreign(tool_state, {"s4"}), [("app/proc/x/state", "s1")])
    check("5 tool does not receive device cmd", foreign(tool_cmd, {"c2"}), [])
    check("4 tool does not receive device unit put", foreign(tool_unit, {"u2"}), [])
    check("4 device does not receive tool unit put", foreign(dev_unit, {"u1"}), [])
    check("  device does not receive cmd (tool, rogue, ws)", foreign(dev_cmd, {"c1"}), [])
    check("7 rogue does not receive state", rogue_state.got, [])
    check("6 ws does not receive device state", foreign(ws_state, {"s4"}), [])
    check("6 ws put on state does not reach the tool", [g for g in tool_state.got if g[1] == "s4"], [])

    del keep, rogue_q
    for s in (d1, d2, tool, rogue, ws):
        s.close()
    print(f"{results.count(True)}/{len(results)} cases as expected")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
