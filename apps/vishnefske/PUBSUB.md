# Edge pub/sub (mTLS NATS)

Public endpoint `tls://pubsub.<SITE_HOST>:443`. Traefik passes the TLS stream
through by SNI; NATS terminates it and requires a client certificate from the
edge CA **and** an entry in its user list. Best effort (core NATS, no
persistence): a subscriber only gets what is published while it's connected.

## Certificates (on the Wyse)

The CA key was generated in the Wyse's TPM 2.0 (tpm2-pkcs11, non-extractable);
`edge-ca` is installed there by the NixOS config (`modules/edge-ca.nix`).

```
sudo edge-ca issue <id>              # device; key generated, bundle at /persist/edge-ca/bundles/<id>.tgz
sudo edge-ca issue <id> device.csr   # device keeps its own key (preferred): sign its CSR
sudo edge-ca issue --backend <id>    # trusted consumer/controller
sudo edge-ca revoke <id>             # rejected from the next connection on
sudo edge-ca list
sudo edge-ca server pubsub.<SITE_HOST>   # (re)issue the broker's server cert
```

`issue`/`revoke` rewrite the `pubsub-users` Secret and restart the broker
(clients reconnect within seconds). Certificates last 2 years; reissue to renew.

## Subjects

| who | publish | subscribe |
|---|---|---|
| device `<id>` | `edge.<id>.>` (e.g. `edge.<id>.telemetry`, `edge.<id>.msg.*`), replies to requests | `cmd.<id>.>`, `_INBOX.<id>.>` |
| backend | `cmd.>`, `edge.>`, `_INBOX.>` | `edge.>`, `cmd.>`, `_INBOX.>` |

## Client settings

- TLS-first handshake (the broker sends nothing before TLS):
  nats.go `nats.TLSHandshakeFirst()`, natscli `--tlsfirst`, nats.py
  `tls_handshake_first=True`, nats.rs `.tls_first()`.
- Client cert + key, and `ca.crt` from the bundle as the only trusted root.
- Devices: inbox prefix `_INBOX.<id>` (nats.go `nats.CustomInboxPrefix`,
  natscli `--inbox-prefix`), or requests can't receive their replies.

```
nats --server tls://pubsub.<SITE_HOST>:443 --tlsfirst \
  --tlscert <id>.crt --tlskey <id>.key --tlsca ca.crt \
  pub edge.<id>.telemetry '{"temp":21.5}'
```
