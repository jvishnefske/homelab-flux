# Mesh router (mTLS zenohd)

`mesh-zenohd` (in `mesh/`) is a Zenoh router for the lathes' KD240
boards, operator tools and, later, the browser. A default-deny ACL
maps each connection to a role.

| endpoint | who | auth |
|---|---|---|
| `tls/mesh.updates.<SITE_HOST>:443` | KD240 routers (`kd240-<id>`), tools (`tool-<id>`) | client cert from the **Zenoh CA**, CN in the ACL |
| `wss://updates.<SITE_HOST>/mesh` | browser (web client, W4c) | none: the ACL's `browser` subject is the only gate. **Not enabled** (`mesh/ws.yaml`) |

On the tls endpoint, Traefik passes the TLS stream through by SNI
(`IngressRouteTCP`, no termination). zenohd then verifies the client cert
against the Zenoh CA, TLS 1.3 only. On the ws endpoint, Traefik ends TLS
and forwards plain ws to port 8000. zenohd accepts any request path there,
so no prefix stripping is needed. `/zenoh` on the same host belongs to the
release channel's router (`updates.yaml`).

## Status: committed, not enabled

`mesh/` builds on its own (`kustomize build apps/vishnefske/mesh`), but
nothing references it yet. Enabling it takes one change, which deploys on
push. Make it in this order:

1. Land the **updates channel** work (`updates.yaml`, plus the
   `localhost/mw/zenohd` entry in this directory's `kustomization.yaml`
   `images:`). Until then:
   - main has no `images:` entry for `localhost/mw/zenohd`;
   - the image itself is preloaded on the Wyse only by the NixOS config's
     `middleware` input (`mwImages` in nixos-x86-squashfs), which is also
     part of that work;
   - `ws.yaml` uses the cert Secret `updates-tls` of its Ingress `updates`.

   If the updates work lands first, drop the `images:` hunk from the
   enable patch, because the entry is already there.
2. On the Wyse, create the Zenoh CA and `zenohd-tls` (see
   [Certificates](#certificates)).
3. Add the DNS records (see [DNS](#dns)).
4. Enable: add `- mesh` to `kustomization.yaml`, and
   `{ name: zenohd-tls, keys: [ca, cert, key] }` to `ansible/cluster.yml`
   `external_secrets`. The playbook fails until the Secret exists.
5. Later, when the web client is ready, uncomment `ws.yaml` in
   `mesh/kustomization.yaml`.

Until `deploy-site.sh` has replaced `MIDDLEWARE_REV`, the pod sits in
ErrImageNeverPull, which harms nothing else.

**Pin the middleware input.** nixos-x86-squashfs's `middleware` input
floats (`deploy-site.sh` runs `nix flake update middleware`), so the
zenohd version, and with it the ACL's semantics, can move on any deploy.
Pin it to a rev whose zenohd is the one `mesh/check.sh` passed with:

```
nix eval github:jvishnefske/middleware/<rev>#packages.x86_64-linux.zenohd.version   # "1.10.1"
```

## Certificates

The Zenoh CA is its **own** TPM key (label `zenoh-ca`), separate from the
NATS CA, and only zenohd trusts it. NATS certs cannot connect. On the Wyse:

```
sudo edge-ca init-zenoh                                     # once: the Zenoh CA
sudo edge-ca server mesh.updates.<SITE_HOST> --profile zenoh  # Secret zenohd-tls; restarts mesh-zenohd
sudo edge-ca issue --ou kd240 <id> --csr board.csr --out board.crt   # CN kd240-<id>
sudo edge-ca issue --ou tool  <id> --csr laptop.csr --out laptop.crt # CN tool-<id>
```

- The key never leaves the device: `issue` requires a CSR (EC P-256).
- Client certs carry the clientAuth EKU. The router's cert carries
  serverAuth and SAN `mesh.updates.<SITE_HOST>`, which must equal the host
  in the locator exactly.
- zenohd never reloads certs. `server` restarts it.
  `close_link_on_expiration` drops a link when its cert expires.
- A client's TLS dir holds `ca` (the Zenoh CA), `cert` and `key`, with no
  file extensions. That is the layout `mwctl --zenoh-tls-dir` and the
  KD240 read.

## Enrolling and revoking (a PR)

Zenoh sees only the cert's **CN**. It ignores the OU, so the role is in
the CN, and the ACL lists every CN:

- **Enroll:** add the CN to the `kd240` or `tool` subject in
  `mesh/zenohd.json5`, one per line. The new ConfigMap hash rolls the pod.
  The `*-UNENROLLED` placeholders may stay or go once a list has a real
  CN. A list must never be empty, because zenohd refuses to start. The
  placeholders grant nothing in practice, because edge-ca can't issue
  them: ids are `[a-z0-9-]` only.
- **Revoke:** remove the line. There is no CRL. A CA-valid cert whose CN is
  not listed still completes the TLS handshake, but every message is denied
  (verified, see below).
- **Keep the lists disjoint.** Across subjects, any allow wins, so a CN in
  both lists would get both roles.
- Ids are not credentials, and this repo is public. Use opaque ids.

## DNS

- `mesh.updates.<SITE_HOST>`: an A record, plus `CAA 0 issue ";"` and
  `CAA 0 issuewild ";"` on that **same name**. zenoh-link-tls adds the
  public webpki roots to a client's trust store, with no way to turn that
  off. The CAA records forbid any public CA from issuing for the name.
  Without DNSSEC, CAA is a mitigation, not a guarantee.
- `updates.<SITE_HOST>` already exists for the release channel.
- The LAN has no hairpin NAT. A KD240 on the bench needs a split-horizon
  entry for `mesh.updates.<SITE_HOST>` that points to the Wyse
  (192.168.1.185).

## ACL (`mesh/zenohd.json5`)

The default is deny. Keys under `@mw` are verbatim: `*` and `**` never
match the `@mw` chunk, so the rules name `@mw/node/*/sys/...` literally.
A rule covers a message only if the rule's key includes the message's
key. A wide query such as `@mw/node/**` is not covered by a narrow allow,
and gets nothing.

| role | may | may not |
|---|---|---|
| `kd240` | declare queryables on `@mw/node/*/sys/**`, be queried there and reply; put `app/proc/*/state`, `app/telemetry/**`; receive tools' subscriptions to those keys (`kd-subscribers`) | query anything; `app/unit/**` writes; `sys/trust/claim`; subscribe |
| `tool` | query the per-node keys below; see queryables and replies; subscribe to and receive `app/proc/*/state`, `app/telemetry/**` | bare `sys/*`; `sys/trust/claim`; `sys/log` or any unlisted key; any put (`app/proc/*/cmd`, `/program`, `app/unit/**`) |
| `browser` | probe only: query `sys/info`, `sys/manifest`, `sys/manifest/cm`, `sys/trust/info` (per node) | everything else: `loader/*`, `payload/*`, `fw/*`, `trust/claim`, `app/**`; any put |

The tool keys (all `@mw/node/*/…`) are `sys/info`, `sys/board`,
`sys/manifest`, `sys/manifest/cm`, `sys/peers`, `sys/trust/info`,
`sys/payload/{status,binding,start,stop,bind}`,
`sys/loader/{begin,chunk,commit}` and
`sys/fw/{begin,chunk,commit,reboot,confirm,abort}`.

The browser only probes and never signs; deploys go through mwctl with a
tool cert. `sys/trust/info` is there for the autoprobe (W4c), which shows
a device's trust state. It is read only and public: a public key and a
counter, and posix-node answers it in every trust state.

`sys/trust/claim` stays local to the board, and so do remote operator
commands (`app/proc/*/cmd`, `/program`). `app/telemetry/**` is a
placeholder: no such key exists yet.

## What clients must do

- **Per-node keys only.** Declare and query `@mw/node/<zid16>/sys/<key>`.
  middleware's responder already declares both the bare key and the
  per-node key. The bare declaration is dropped here, and so is a query on
  bare `sys/info`, so a `Target::Any` request without a node gets nothing
  remotely. Over the mesh:
  - mwctl: `mwctl --zenoh tls/mesh.updates.<SITE_HOST>:443 --zenoh-tls-dir D info --node <zid16>`
  - The node is required. `mwctl scout` is multicast-only and refuses
    `--zenoh`.
- **A prefix queryable works too.** One declaration of
  `@mw/node/<id>/sys/**`, as the RT1050 firmware makes, is visible and
  answers the listed keys. The unlisted keys still never reach it.
- **The KD240 links router-to-router.** Its zenohd gets a second
  endpoint, `connect: tls/mesh.updates.<SITE_HOST>:443`, with
  `transport.link.tls` set to `root_ca_certificate`, `connect_certificate`,
  `connect_private_key` and `enable_mtls: true`. Its own listener stays on
  loopback.
- **Device errors arrive as errors.** A device's error reply (for example
  `Denied`) reaches the asker. It is not turned into a timeout.

## Checking the config (`mesh/check.sh`)

The check runs `mesh/zenohd.json5` in a real zenohd on loopback, with
throwaway certs. Those are two CAs: the "zenoh" CA signs the server cert
(SAN `localhost`), `kd240-t1`, `tool-t1` and `rogue-t1` (a valid CA but an
unlisted CN), and an unrelated CA signs `other-t1`. The live file is never
modified.

Prerequisites:

- `zenohd` of the deployed version: `ZENOHD=/path` or on PATH, for example
  `nix build github:jvishnefske/middleware/<rev>#zenohd`;
- `openssl`, `curl`, `bash`;
- for level 3, a Python with `eclipse-zenoh` of the **same** version:

```
python3 -m venv /tmp/zv && /tmp/zv/bin/pip install eclipse-zenoh==1.10.1
ZENOHD=./result/bin/zenohd apps/vishnefske/mesh/check.sh                     # level 2
ZENOHD=./result/bin/zenohd apps/vishnefske/mesh/check.sh --acl /tmp/zv/bin/python  # 2 and 3
```

The two levels:

- **Level 2:** the config loads (a bad ACL makes zenohd exit), and both
  ports listen. The handshakes are TLS 1.3 with `openssl s_client`, plus a
  ws upgrade with `curl`.
- **Level 3:** `mesh/acl_test.py`, run twice: once with the devices as
  clients of the router, and once behind a second zenohd that links to it
  router-to-router with `kd240-t1`, the KD240's way.

The test CNs (`kd240-t1`, `tool-t1`) are added to each subject's list
next to whatever is enrolled, and level 3 also adds the `observer-t1`
subject. That needs `id: "<role>", ... cert_common_names: [` on one line,
and `rules: [`, `subjects: [` and `policies: [` alone on their lines.

Environment variables:
- `CONF=<file>` checks another copy of `zenohd.json5`.
- `WORK=<dir>` sets the work directory. It must be absent or empty, and
  only what the script made is removed.
- `KEEP=1` keeps the work directory, including `zenohd.log` with the
  ACL's decisions at trace level (`is unauthorized to`). That log also
  holds the per-key permission cache lines, so the count is not a count of
  dropped messages.

### Findings (zenohd 1.10.1, eclipse-zenoh 1.10.1, 2026-11-20)

All level 2 and 3 cases pass: 75 with the devices as clients, 74 behind a
router. The cases cover every key in `tool-query` and `ws-query`
positively, from a list written in the test, so a typo in a config key
fails.

Each put is checked against `observer-t1`, a subject that check.sh adds
for the test alone and that may receive `app/**`. Without it, a denied put
would have nobody permitted to receive it, and the case could never fail.

Mutations (`CONF=` copies) fail as they should:
- `deny-unit-writes` deleted and `kd-publish` widened to `app/**`: the
  device's `cmd`, `program` and `unit` puts reach the observer in client
  mode. Behind a router they still don't, because the cloud never lets
  those subscriptions out to the KD240 (`kd-subscribers`), so its router
  never forwards them. That is a second, real barrier.
- A typo in a `tool-query` or `ws-query` key fails that key's case.
- `loader/begin` and `payload/start` put back into `ws-query`, with
  `trust/info` taken out: the three matching ws cases fail in both modes.
- A copy with the placeholders replaced by real CNs passes, so the check
  works on an enrolled config.

1. **Handshakes.**
   - A listed CN completes TLS 1.3.
   - With no client cert, the server sends `certificate required`. Under
     TLS 1.3 that alert comes after the handshake, on the first read.
   - A cert from the other CA gets `unknown ca`.
   - `rogue-t1` (right CA, unlisted CN) completes the handshake. The router
     logs "did not match any configured ACL subject. Default permission
     `Deny`", and that cert can do nothing:
     - its queries get no reply;
     - it receives no publications;
     - its queryable stays invisible to tools.
   - A server `cert` holding only the leaf, as `edge-ca` writes it, works.
     No chain is needed, because the Zenoh CA is the root.
2. **ws** upgrades on `/mesh` with no cert. Any path is accepted.
3. **Error replies (case 8): delivered.** A device's `reply_err` reaches
   the asker:
   - for a per-node query and for `@mw/node/*/…`;
   - for a tool. The browser was also checked while it could still query
     `payload/start`; it no longer can;
   - in both topologies.

   The reply rules (`kd-replies` ingress, `asker-see` egress) on the queried
   key cover error replies too. A device's `Denied` therefore surfaces as an
   error, not a timeout.
4. **Bare against per-node keys: as the blueprint said.**
   - A query on bare `sys/info`, from a tool or from the browser, gets
     nothing. With the ACL off, it gets an answer.
   - `@mw/node/<id>/sys/info` gets an answer, and `@mw/node/*/sys/info`
     gets one from every device.
   - `@mw/node/**` gets nothing. With the ACL off, it gets every key,
     `trust/claim` included.
5. **Router-to-router lost publications (fixed): `kd-subscribers`.**
   - Under the blueprint's ACL, queries, replies and error replies worked
     with the devices behind a KD240 router, but a device's put on
     `app/proc/*/state` never reached a subscribed tool.
   - The cause: the router dropped the tool's `Declare Subscriber (egress)`
     to the KD240 link, and a router forwards a put only to subscribers it
     knows about. With the devices as direct clients it worked, because a
     client sends its puts to the router regardless.
   - The fix: allow egress `declare_subscriber` on the publishable keys
     toward `kd240`.
6. **Devices can't query each other through the mesh.** `kd240` has no
   query ingress. Sessions behind the same KD240 router talk locally, which
   is outside this ACL.
7. **Denies.**
   - `app/unit/**` puts, in both directions, are dropped.
   - `app/proc/*/cmd` puts from a tool, the rogue or the browser never reach
     a device.
   - The browser receives no state, and its put on `state` never reaches a
     tool.
   - A session's own puts reach that session's own subscribers inside the
     session. The router never sees them, so the test counts only other
     sessions' payloads.

## Risks

- **The ws listener is unauthenticated, so what it can read is public.**
  Anyone who reaches it can read `sys/info`, the manifest, the manifest
  pages (`sys/manifest/cm`) and `sys/trust/info` of any node on the mesh.
  That reveals the node ids, the installed payloads, and each node's
  trust state and public key.

  Nothing else gets through: no loader, payload, firmware or claim key,
  and no put. A ws peer therefore can't take over an upload with
  `loader/begin`, and can't reach the devices' signed control path
  (`payload/start|stop|bind`) at all. Deploys go through mwctl with a
  tool cert.
- **Session exhaustion.** `max_sessions: 128` is shared by the tls and ws
  listeners, so browser sessions can lock out devices and tools. `ws.yaml`
  allows 4 open WebSockets per client IP (Traefik `inFlightReq`) plus a
  rateLimit. Many source IPs can still fill the table, and the per-IP
  limit only sees real client IPs if Traefik's Service keeps them
  (`externalTrafficPolicy: Local`).

  This is one reason ws ships disabled.
- **A CN cannot be bound to a zid16.** A valid `kd240` cert can declare
  another node's keys. Closing that needs per-device rules.
- **There is no revocation besides the CN list.**
- **The NetworkPolicy is untested.** If Traefik's connections don't come
  from the labelled pods, both routes stay closed. The post-deploy check
  would show that.
- **One replica, on the Wyse.**

## After the deploy

- `mwctl --zenoh tls/mesh.updates.<SITE_HOST>:443 --zenoh-tls-dir D info --node <zid16>`
  with a connected KD240 answers.
- `openssl s_client -tls1_3 -connect mesh.updates.<SITE_HOST>:443 -servername mesh.updates.<SITE_HOST>`
  with no cert ends in `certificate required`.
