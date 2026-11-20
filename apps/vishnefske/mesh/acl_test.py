"""Level-3 ACL cases for zenohd.json5 (run by check.sh --acl).

Sessions, each with its own cert: two devices (kd240-t1, node ids a1 and
a2), a tool (tool-t1), a rogue (rogue-t1: valid CA, in no CN list) and a
browser (ws, no cert), plus `observer-t1`: a subject check.sh adds for
the test alone, which may subscribe to and receive app/** and nothing
else. A put that the ACL wrongly lets in therefore reaches the observer;
without it, a denied put would have nobody permitted to receive it, and
the case could not fail. The devices are either clients of the mesh router
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


# What a tool and the browser may query, written out here (not read from
# the config) so that a typo in a config key fails its case. The keys are
# middleware's (mw-stack src/loader.rs, posix-node, mwctl).
TOOL_KEYS = [
    "sys/info", "sys/board", "sys/manifest", "sys/manifest/cm", "sys/peers", "sys/trust/info",
    "sys/payload/status", "sys/payload/binding",
    "sys/loader/begin", "sys/loader/chunk", "sys/loader/commit",
    "sys/payload/start", "sys/payload/stop", "sys/payload/bind",
    "sys/fw/begin", "sys/fw/chunk", "sys/fw/commit", "sys/fw/reboot", "sys/fw/confirm", "sys/fw/abort",
]
WS_KEYS = [
    "sys/info", "sys/manifest", "sys/manifest/cm",
    "sys/loader/begin", "sys/loader/chunk", "sys/loader/commit",
    "sys/payload/start", "sys/payload/stop", "sys/payload/bind",
]
NEVER_KEYS = ["sys/trust/claim", "sys/log"]
ERR_KEY = "sys/payload/start"  # case 8: this one replies with an error


def device(sess, node):
    """Queryables on every key, bare and per-node, replying `<node>:<key>`."""
    qs = []
    for key in TOOL_KEYS + NEVER_KEYS:
        for k in (key, f"@mw/node/{node}/{key}"):
            def cb(q, k=k, key=key):
                if key == ERR_KEY:
                    q.reply_err(f"{node}:Denied")
                else:
                    q.reply(k, f"{node}:{key}")
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
    obs = session(a.tls, a.certs, "observer-t1")
    keep = device(d1, A1) + device(d2, A2)

    tool_state = Sink(tool, "app/proc/x/state")
    rogue_state = Sink(rogue, "app/proc/x/state")
    ws_state = Sink(ws, "app/proc/x/state")
    # Exact keys, not app/**: behind a router, only subscriptions the ACL
    # lets out to the KD240 (kd-subscribers) make it forward puts at all.
    obs_keys = ["app/proc/x/state", "app/proc/x/cmd", "app/proc/x/program", "app/unit/x"]
    obs_sinks = [Sink(obs, k) for k in obs_keys]
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
    check("1 tool @mw/node/a1/sys/info", ask(tool, f"@mw/node/{A1}/sys/info"), one_ok("sys/info"))
    check("1 tool bare sys/info (blueprint: denied)", ask(tool, "sys/info"), none)
    # 2. claim stays local
    check("2 tool sys/trust/claim", ask(tool, f"@mw/node/{A1}/sys/trust/claim"), none)
    # 3. wildcard over nodes
    check("3 tool @mw/node/*/sys/info", ask(tool, "@mw/node/*/sys/info"), both_ok("sys/info"))
    check("3 tool @mw/node/** (wide, not covered)", ask(tool, "@mw/node/**"), none)
    check("3 tool sys/log (not listed)", ask(tool, f"@mw/node/{A1}/sys/log"), none)
    # every listed key, so a typo in the config fails here
    for k in TOOL_KEYS:
        want = ([], [f"{A1}:Denied"]) if k == ERR_KEY else one_ok(k)
        check(f"  tool {k}", ask(tool, f"@mw/node/{A1}/{k}"), want)
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
    check("7 rogue queryable invisible to tool", ask(tool, "@mw/node/*/sys/info"), both_ok("sys/info"))
    # 6. browser
    check("6 ws sys/info", ask(ws, f"@mw/node/{A1}/sys/info"), one_ok("sys/info"))
    for k in WS_KEYS:
        want = ([], [f"{A1}:Denied"]) if k == ERR_KEY else one_ok(k)
        check(f"6 ws {k}", ask(ws, f"@mw/node/{A1}/{k}"), want)
    for k in TOOL_KEYS + NEVER_KEYS:
        if k not in WS_KEYS:
            check(f"6 ws {k} (not listed)", ask(ws, f"@mw/node/{A1}/{k}"), none)
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

    # 4/5. publications: (session, key, payload, allowed in?). Each denied
    # put would reach the observer if the ACL let it in.
    puts = [
        (d1, "app/proc/x/state", "d-state", True),
        (d1, "app/proc/x/cmd", "d-cmd", False),
        (d1, "app/proc/x/program", "d-program", False),
        (d1, "app/unit/x", "d-unit", False),
        (tool, "app/proc/x/state", "t-state", False),
        (tool, "app/proc/x/cmd", "t-cmd", False),
        (tool, "app/proc/x/program", "t-program", False),
        (tool, "app/unit/x", "t-unit", False),
        (rogue, "app/proc/x/cmd", "r-cmd", False),
        (rogue, "app/proc/x/state", "r-state", False),
        (ws, "app/proc/x/cmd", "w-cmd", False),
        (ws, "app/proc/x/state", "w-state", False),
        (ws, "app/unit/x", "w-unit", False),
    ]
    for sess, key, payload, _ in puts:
        sess.put(key, payload)
    time.sleep(1.0)
    seen = {g for sink in obs_sinks for g in sink.got}
    who = {id(d1): "device", id(tool): "tool", id(rogue): "rogue", id(ws): "ws"}
    for sess, key, payload, allowed in puts:
        check(f"{'5' if allowed else '4'} {who[id(sess)]} put {key} {'reaches' if allowed else 'stopped'}",
              (key, payload) in seen, allowed)
    # A session's own puts reach its own subscribers inside the session, so
    # only the other sessions' payloads count.
    foreign = lambda sink, own: [g for g in sink.got if g[1] not in own]
    check("5 tool receives device state", foreign(tool_state, {"t-state"}), [("app/proc/x/state", "d-state")])
    check("7 rogue does not receive state", foreign(rogue_state, {"r-state"}), [])
    check("6 ws does not receive state", foreign(ws_state, {"w-state"}), [])

    del keep, rogue_q
    for s in (d1, d2, tool, rogue, ws, obs):
        s.close()
    print(f"{results.count(True)}/{len(results)} cases as expected")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
