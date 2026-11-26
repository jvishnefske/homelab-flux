# homelab-flux

Flux GitOps for the two-node homelab k3s cluster (Wyse 5070 server + PC Engines
APU2 agent; NixOS hosts from `jvishnefske/nixos-x86-squashfs`).

```
clusters/homelab/         Flux entry point: flux-system and the Kustomizations
                          infra-controllers -> infra-configs -> apps, and zenoh-routes
infrastructure/
  controllers/            cert-manager (HelmRelease), k3s Traefik tuning (HelmChartConfig)
  configs/                Let's Encrypt ClusterIssuer, CoreDNS overrides
apps/vishnefske/          vishnefske.com and its services (namespace vishnefske)
  admin-tpm/              TPM signing for the admin console (committed, not enabled)
apps/zenoh-routes/        the zenoh-routes namespace, reconciled on its own
ansible/                  out-of-band Secrets and one-time setup (cluster.yml)
justfile                  operator shortcuts (Artifactory)
```

**This repo is public. Nothing secret or personal goes in it.** Hostnames, the
Let's Encrypt e-mail and the owner's e-mail are substituted by Flux from the
`cluster-vars` Secret (`postBuild.substituteFrom`); credentials live in
Secrets created out of band (see [Secrets](#secrets)).

## Services

Every host below is `<name>.${SITE_HOST}`; TLS comes from Let's Encrypt
through cert-manager, and Traefik routes by host. Manifests are in
`apps/vishnefske/`.

| Host | Service | Login | Manifest |
| --- | --- | --- | --- |
| (apex) | static site | none | `site.yaml` |
| (apex) `/rust` | EmitRust transpiler frontend, on a VM outside the cluster (hand-set endpoint) | none | `rust.yaml`, routed by `site.yaml` |
| `auth` | Keycloak, realm `vishnefske` | the identity provider for the rest | `keycloak.yaml` |
| `git` | Gerrit | Keycloak (OAuth) | `gerrit.yaml` |
| `git2` | Gitea | Keycloak (OIDC) | `gitea.yaml` |
| `timer` | Pomodoro timer | Keycloak (client `pomodoro`), owner only; anyone can watch | `pomodoro.yaml` |
| `admin` | middleware admin console: NATS channels, mTLS certificate requests and board enrolment | Keycloak (client `admin`), owner only | `admin.yaml` |
| `grafana` | Grafana, with the pub/sub dashboard (`grafana/pubsub.json`) | Keycloak (client `grafana`), owner only | `monitoring.yaml` |
| `hub` | JFrog Container Registry (Docker, Helm, generic) | its own admin account | `artifactory.yaml` |
| `nats` | NATS over websocket, for browsers | NATS's own config (`nats-config` Secret) | `nats.yaml` |
| `pubsub` | edge pub/sub: NATS over mutual TLS (TLS passthrough, `:443`) | client certificate from the edge CA | `pubsub.yaml`, [PUBSUB.md](apps/vishnefske/PUBSUB.md) |

In-cluster only: PostgreSQL (`postgres.yaml`, one server for Keycloak, Gitea
and Artifactory; it also creates a database for Gerrit, which keeps its data
in NoteDb instead) and Prometheus (`monitoring.yaml`, scrapes the admin
console's `/metrics` through its Service with a bearer token).

### Admin console

The console and its board enrolment are described in
`vishnefske.com/admin/README.md`. Its machine endpoints are kept off the
public Ingress by the IngressRoute `admin-internal` in `admin.yaml`:

- `/metrics` always answers 403 through Traefik (Prometheus doesn't use
  the Ingress).
- `/api/signer/` (the signer on the Wyse) is allowed only from the pod and
  service CIDRs and the LAN. The comment in `admin.yaml` explains why
  Traefik's view of the client address can be trusted here, when it can't,
  and the `curl` check to run from outside the LAN.
- `POST /api/enroll` stays public. Operators enrol boards from anywhere with
  a Keycloak bearer token.

Enrolment answers 503 until the admin pod can sign with the Zenoh CA key
in the Wyse's TPM. `apps/vishnefske/admin-tpm/` provides that: a device plugin
for `/dev/tpmrm0`, the tpm2-pkcs11 store, and the `mw-pki` settings. It is
committed but not enabled. [ADMIN-TPM.md](apps/vishnefske/ADMIN-TPM.md) has
the security trade-off, the NixOS changes it needs and the enable steps.

## Images

App images built by us come from Nix in the private `vishnefske.com` repo and
are preloaded on the nodes by NixOS (`services.k3s.images`), so their
manifests use `imagePullPolicy: Never` and there is no registry for them. The
tag is that repo's git revision, set in one place:
`apps/vishnefske/kustomization.yaml` (`images:`). `deploy-site.sh` in the
NixOS repo bumps it after the images are on the nodes.

Upstream images are pinned by tag (some also by digest) and pulled on first
use (`imagePullPolicy: IfNotPresent`): Prometheus and Grafana from Docker Hub;
Artifactory through its Helm chart (`artifactory-jcr`), with the
`artifactory-secrets` Job's images pinned by digest; and, only with
`admin-tpm/`, `squat/generic-device-plugin`.

## Secrets

- `ansible/cluster.yml` writes every Secret that holds a credential from a
  private directory that is never committed (`secrets.env` and a few files),
  and does the one-time setup (Gitea's Keycloak login source). It is
  idempotent; `--check --diff` shows drift without changing anything.
- `pubsub-tls` and `pubsub-users` belong to `edge-ca` on the Wyse; the
  playbook only checks that they exist.
- Artifactory needs no `secrets.env` entry: a Job generates its Secret once
  and creates its database. `just artifactory-password` shows the admin
  password; `just artifactory-db` reruns the database setup.
- `ADMIN_TPM_PIN` (the TPM token PIN, Secret `admin` key `MW_PKI_PIN`) is
  optional and only used with `admin-tpm/` enabled.

Every Secret, its keys and the Keycloak clients to create are listed in
[`apps/vishnefske/SECRETS.md`](apps/vishnefske/SECRETS.md).

## Placement

The router forwards 80/443 to the APU2. Traefik runs on both nodes with
`externalTrafficPolicy: Local`, and the static site has a replica on each
node behind an `internalTrafficPolicy: Local` Service, so the site keeps
being served by the APU2 alone while the Wyse (control plane) is down.
PostgreSQL, Keycloak, Gitea, Gerrit, NATS, the edge pub/sub and Artifactory
are pinned to the Wyse (RAM). The admin console, Pomodoro, Prometheus and
Grafana are not pinned. With `admin-tpm/` enabled, the admin console is
pinned to the Wyse too, because the TPM is there.
