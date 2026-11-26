# Admin console: TPM signing for board enrolment

`admin-tpm/` gives the admin pod what `POST /api/enroll` needs to sign a
board's CSR with the **Zenoh (mesh) CA** key in the Wyse's TPM. Until it is
enabled, `/api/enroll` answers 503 and nothing else changes. (Certificate
requests from the console, `/api/certs`, keep going through the Wyse signer,
which runs `edge-ca` as root on the host. They don't need this.)

| File | What it adds |
|---|---|
| `admin-tpm/kustomization.yaml` | a kustomize `Component` (resources and patches below) |
| `admin-tpm/device-plugin.yaml` | DaemonSet `tpm-device-plugin` ([squat/generic-device-plugin](https://github.com/squat/generic-device-plugin), pinned by digest) on the Wyse: advertises `/dev/tpmrm0` as the resource `squat.ai/tpmrm` |
| `admin-tpm/admin-patch.yaml` | patches Deployment `admin`: pinned to the Wyse, requests one `squat.ai/tpmrm`, mounts the tpm2-pkcs11 store (read-only) and the Zenoh CA certificate from the host, sets the `mw-pki` environment, and takes `MW_PKI_PIN` from Secret `admin` |

The admin container stays unprivileged: same uid 1000, no capabilities,
read-only root. The device plugin hands it the one device node and nothing
else. The plugin itself runs as root (it needs the kubelet's socket
directory) but has no privileges or capabilities and no API token.

## Status: committed, not enabled

`apps/vishnefske/kustomization.yaml` doesn't reference `admin-tpm/`, so Flux
doesn't apply it. A Component can't be built alone. To render it, build
a copy of this directory with the component on. This changes nothing in the
repo:

```
t=$(mktemp -d) && cp -r apps/vishnefske "$t/" \
  && echo 'components: [admin-tpm]' >> "$t/vishnefske/kustomization.yaml" \
  && kubectl kustomize "$t/vishnefske"; rm -rf "$t"
```

## What the pod gets

`edge-ca` on the Wyse (`modules/edge-ca.nix` in nixos-x86-squashfs) sets up
the TPM this way, and the patch follows it:

| What | On the Wyse | In the pod |
|---|---|---|
| TPM resource manager | `/dev/tpmrm0`, `root:tss` 0660 (NixOS `security.tpm2`) | same path, via `squat.ai/tpmrm`; `TPM2_PKCS11_TCTI` and `TPM2TOOLS_TCTI` = `device:/dev/tpmrm0` |
| tpm2-pkcs11 store (sqlite DB of TPM-wrapped key blobs) | `/persist/edge-ca/tpm2-pkcs11`, root only (`umask 077`; `/persist/edge-ca` is 0700) | `/var/lib/tpm2-pkcs11`, read-only; `TPM2_PKCS11_STORE` |
| token | label `edge-ca`, holding the keys `ca` (NATS CA) and `zenoh-ca` (Zenoh CA) | `PKCS11_TOKEN=edge-ca`, `MW_PKI_KEY=zenoh-ca` |
| user PIN | `/persist/secrets/edge-ca.pin`, root only | `MW_PKI_PIN` from Secret `admin`, key `MW_PKI_PIN` |
| SO PIN | `/persist/edge-ca/so.pin` | not mounted |
| Zenoh CA certificate | `/persist/edge-ca/zenoh/ca.crt`, 0644 (`edge-ca init-zenoh`) | `/etc/mw-pki/ca.crt`; `MW_PKI_CA` |

The image sets `MW_PKI_BIN` and `PKCS11_MODULE` (tpm2-pkcs11's
`libtpm2_pkcs11.so`). The server passes only `PKCS11_MODULE`,
`PKCS11_TOKEN`, `MW_PKI_PIN`, `SOFTHSM2_CONF`, `TPM2_PKCS11_STORE`,
`TPM2_PKCS11_TCTI`, `TPM2TOOLS_TCTI` and `TSS2_LOG` to `mw-pki`. Because of
that, edge-ca's `TPM2_PKCS11_BACKEND=esysdb` can't be set: tpm2-pkcs11
probes FAPI first, fails, and falls back to the sqlite store. That works,
and `TSS2_LOG=fapi+NONE` hides the error it logs.

## Security trade-off

Today only root on the Wyse can use the CA keys. With this component on, the
**internet-facing** admin pod can ask the TPM to sign anything with the
Zenoh CA key. The key still can't be extracted: the blobs in the store are
wrapped by this TPM and useless anywhere else, and the PIN without the TPM
does nothing. But anyone who gets code execution in the admin pod (a Node
bug, a dependency) has a **signing oracle** for as long as they keep it.
They can mint client certificates the mesh router trusts (`kd240-*`,
`tool-*`; the router ACL then decides what each CN may do). Revoking those
certificates means editing the ACL and restarting the router, because there
is no CRL. A rotated key means re-enrolling every board.

What limits it:

- `/api/enroll` checks the operator's Keycloak token (client `mwctl`,
  audience `admin`, the owner's verified email) and the CN format before it
  runs `mw-pki`. That stops callers, but not an attacker already inside the
  pod.
- The container is unprivileged, and the TPM is the only device it gets.
- **The shared token is the weak point.** The PIN unlocks the whole `edge-ca`
  token, and that token also holds `ca`, the **NATS edge CA** key. With the
  layout above, a compromised pod can sign NATS device and backend certs
  too (the backend role may publish to `cmd.>`). `mw-pki --key zenoh-ca`
  only limits what the server *means* to use. Fix this with a separate
  token (below) before enabling, unless that risk is acceptable.

## Host side (NixOS module, nixos-x86-squashfs)

Nothing in this repo can change the Wyse. These go in `modules/edge-ca.nix`.

**Required with any layout:**

1. Pin the gids that the patch's `supplementalGroups` names, so they can't
   drift between rebuilds (`tss` is allocated dynamically on NixOS):
   ```nix
   users.groups.tss.gid = 176;             # owns /dev/tpmrm0 (0660)
   users.groups.edge-ca-admin.gid = 2110;  # may read the token store
   ```
   If `getent group tss` on the Wyse already shows another number, use
   that number in both places instead of 176.
2. Let `edge-ca-admin` read the store. kubelet bind-mounts the store
   directory itself, so `/persist/edge-ca` can stay 0700:
   ```nix
   systemd.tmpfiles.rules = [
     "z /persist/edge-ca/tpm2-pkcs11 0750 root edge-ca-admin -"
     "z /persist/edge-ca/tpm2-pkcs11/tpm2_pkcs11.sqlite3 0640 root edge-ca-admin -"
   ];
   ```
   `edge-ca` runs with `umask 077`. sqlite rewrites the DB in place, so the
   mode survives `addkey`, and tmpfiles reapplies it on every boot.
3. The PIN stays root-only on the host. The pod gets it from the Secret, not
   from `/persist/secrets`.

**Recommended: a token for the admin alone.** Give the Zenoh CA its own
tpm2-pkcs11 store and token, so the pod's mount and PIN hold only the key it
uses. For example, store `/persist/edge-ca/mesh-pkcs11`, token `mesh-ca`, key
`zenoh-ca`, PIN `/persist/secrets/mesh-ca.pin`, and the group as above. In
`edge-ca`, the zenoh profile then exports its own `TPM2_PKCS11_STORE`, uses
`zkey='pkcs11:token=mesh-ca;object=zenoh-ca;type=private'`, and needs a second
OpenSSL config whose `pkcs11-module-token-pin` is the new PIN file. A
tpm2-pkcs11 key can't move between tokens, so this means a **new** Zenoh CA
(`init-zenoh` again) and reissuing anything it signed. That is cheap while the
mesh isn't enabled yet. Then change two lines in `admin-tpm/admin-patch.yaml`:
the `tpm2-pkcs11` hostPath and `PKCS11_TOKEN`. `ADMIN_TPM_PIN` becomes the
new token's PIN.

## Secret

| secrets.env key | Secret / key | Value |
|---|---|---|
| `ADMIN_TPM_PIN` | `admin` / `MW_PKI_PIN` | the token's user PIN: `sudo cat /persist/secrets/edge-ca.pin` on the Wyse (or the separate token's PIN) |

`ansible/cluster.yml` maps it as `optional_env`, so the playbook works
without it while the component is off. Copying the PIN puts it in two more
places: the private secrets directory and the cluster's Secret store. Each
is still useless without the Wyse's TPM and store.

## Enable

1. **Image**: the `vishnefske/admin` image in `kustomization.yaml` `images:`
   has `mw-pki` and tpm2-pkcs11, and sets `MW_PKI_BIN` and `PKCS11_MODULE`.
2. **Host**: apply the [host side](#host-side-nixos-module-nixos-x86-squashfs)
   and rebuild the Wyse. Run `sudo edge-ca init-zenoh` if
   `/persist/edge-ca/zenoh/ca.crt` doesn't exist. Check that the group can
   read the store:
   ```
   sudo -u nobody -g edge-ca-admin test -r /persist/edge-ca/tpm2-pkcs11/tpm2_pkcs11.sqlite3 && echo ok
   ```
3. **Volume**: `admin-data` is a local-path volume, and it is bound to the node
   where the pod first ran. If that node is the APU2, the pinned pod will never
   schedule. Check with
   `kubectl -n vishnefske get pv $(kubectl -n vishnefske get pvc admin-data -o jsonpath='{.spec.volumeName}') -o jsonpath='{.spec.nodeAffinity}'`.
   If it says the APU2, copy out `/data` (requests and `audit.log`), delete
   the PVC, and restore after step 5.
4. **Secret**: add `ADMIN_TPM_PIN=<pin>` to `secrets.env`, then
   `cd ansible && ansible-playbook cluster.yml`.
5. **Enable**: add `components: [admin-tpm]` to
   `apps/vishnefske/kustomization.yaml` and push.
6. **Verify**: `kubectl get node wyse -o jsonpath='{.status.allocatable}'`
   lists `squat.ai/tpmrm`, the admin pod runs on the Wyse, and an
   enrolment (`just provision` in the middleware repo) gets a certificate
   that chains to `/persist/edge-ca/zenoh/ca.crt`.

If `mw-pki` reports that the database is read-only, this tpm2-pkcs11 build
writes to its store on open. Drop `readOnly` from the store mount, and make
the tmpfiles modes `0770` and `0660`. That is less good: the pod could then
damage the store, so back up `/persist/edge-ca` first.

To disable, remove the `components:` line. The DaemonSet and the patch
go away on the next reconcile.
