#!/usr/bin/env bash
# Offline check of zenohd.json5 against a real zenohd (MESH.md, "Checking
# the config"). Throwaway CAs and certs, a temp copy of the config on
# 127.0.0.1, then:
#
#   level 2  zenohd parses the config (a bad ACL makes it exit) and listens
#            on both ports; the mTLS listener completes a TLS 1.3 handshake
#            with a cert from its CA, refuses none ("certificate required")
#            and one from another CA ("unknown ca"); the ws listener
#            upgrades on any path, with no cert.
#   level 3  (--acl PYTHON) acl_test.py's behaviour cases, run with a
#            Python that has eclipse-zenoh of zenohd's version; once with
#            the devices as clients of the router, once behind a second
#            zenohd that links to it router-to-router (the KD240's way).
#
# Needs: zenohd (ZENOHD=/path, or on PATH; e.g.
#   nix build github:jvishnefske/middleware/<rev>#zenohd), openssl, curl,
#   bash. For --acl: python3 -m venv V && V/bin/pip install eclipse-zenoh==<zenohd version>
#
#   ./check.sh                     level 2
#   ./check.sh --acl V/bin/python  levels 2 and 3
#
# The live zenohd.json5 is never modified. KEEP=1 keeps the work dir
# (certs, configs, zenohd logs); WORK=<dir> puts it there.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
py=""
if [[ ${1:-} == --acl ]]; then py=${2:?--acl needs a python with eclipse-zenoh}; fi
zenohd=${ZENOHD:-$(command -v zenohd || true)}
[[ -x $zenohd ]] || { echo "zenohd not found: set ZENOHD or put it on PATH" >&2; exit 2; }
for t in openssl curl; do command -v $t >/dev/null || { echo "$t not found" >&2; exit 2; }; done

work=${WORK:-$(mktemp -d)}
mkdir -p "$work"
pids=()
cleanup() {
  for p in "${pids[@]}"; do kill "$p" 2>/dev/null || true; done
  wait 2>/dev/null || true
  if [[ ${KEEP:-} == 1 ]]; then echo "kept $work"; else rm -rf "$work"; fi
}
trap cleanup EXIT

fails=0
ok()   { echo "PASS  $*"; }
fail() { echo "FAIL  $*"; fails=$((fails + 1)); }

# ---- certificates -----------------------------------------------------
# zenoh CA: server (SAN localhost, serverAuth), kd240-t1, tool-t1 and
# rogue-t1 (valid, but in no CN list). other CA: other-t1.
c=$work/certs
mkdir -p "$c"
ca() {
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 2 \
    -subj "/CN=$1" -keyout "$c/$1.key" -out "$c/$1.crt" \
    -addext "basicConstraints=critical,CA:TRUE" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" 2>/dev/null
}
leaf() { # leaf <ca> <cn> <eku> [san]
  local ext="$c/$2.ext"
  {
    echo "basicConstraints=critical,CA:FALSE"
    echo "keyUsage=critical,digitalSignature"
    echo "extendedKeyUsage=$3"
    [[ -n ${4:-} ]] && echo "subjectAltName=$4"
  } >"$ext"
  openssl req -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes \
    -subj "/OU=test/CN=$2" -keyout "$c/$2.key" -out "$c/$2.csr" 2>/dev/null
  openssl x509 -req -in "$c/$2.csr" -CA "$c/$1.crt" -CAkey "$c/$1.key" \
    -CAcreateserial -days 2 -extfile "$ext" -out "$c/$2.crt" 2>/dev/null
}
ca zenoh-ca
ca other-ca
leaf zenoh-ca server serverAuth DNS:localhost
for cn in kd240-t1 tool-t1 rogue-t1; do leaf zenoh-ca $cn clientAuth; done
leaf other-ca other-t1 clientAuth
# The Secret's layout (zenohd-tls: ca, cert = full chain leaf first, key).
mkdir -p "$work/tls"
cp "$c/zenoh-ca.crt" "$work/tls/ca"
cat "$c/server.crt" "$c/zenoh-ca.crt" >"$work/tls/cert"
cp "$c/server.key" "$work/tls/key"

# ---- config: a temp copy on loopback, with the test CNs -----------------
TLS=17447 WS=18000
sed -e "s#/etc/mesh/tls#$work/tls#g" \
    -e "s#0.0.0.0:7447#127.0.0.1:$TLS#" \
    -e "s#0.0.0.0:8000#127.0.0.1:$WS#" \
    -e 's#"kd240-unenrolled"#"kd240-t1"#' \
    -e 's#"tool-unenrolled"#"tool-t1"#' \
    "$here/zenohd.json5" >"$work/mesh.json5"
grep -q kd240-t1 "$work/mesh.json5" && grep -q tool-t1 "$work/mesh.json5" \
  || { echo "placeholder CNs not found in zenohd.json5" >&2; exit 2; }

listening() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

RUST_LOG=${RUST_LOG:-info,zenoh::net::routing::interceptor::access_control=trace} \
  "$zenohd" -c "$work/mesh.json5" >"$work/zenohd.log" 2>&1 &
zpid=$!
pids+=("$zpid")
for _ in $(seq 50); do listening $TLS && listening $WS && break; sleep 0.1; done
sleep 1
ver=$("$zenohd" --version 2>/dev/null | grep -o 'zenohd v[0-9.]*' | tail -1 || true)
echo "${ver:-zenohd (version unknown)}; level 3 needs eclipse-zenoh of the same version"

# ---- level 2 -------------------------------------------------------------
if kill -0 "$zpid" 2>/dev/null; then ok "zenohd accepts the config and stays up"
else fail "zenohd exited:"; tail -20 "$work/zenohd.log"; exit 1; fi
listening $TLS && ok "tls listener on :$TLS" || fail "tls listener on :$TLS"
listening $WS && ok "ws listener on :$WS" || fail "ws listener on :$WS"

# s_client [cert-name|-] [ca-name]: TLS 1.3 to the tls port; stays a second
# so the server's post-handshake alert (TLS 1.3 client auth) is read.
s_client() {
  local args=(-tls1_3 -connect "127.0.0.1:$TLS" -servername localhost
              -CAfile "$c/${2:-zenoh-ca}.crt" -verify_return_error)
  [[ $1 != - ]] && args+=(-cert "$c/$1.crt" -key "$c/$1.key")
  sleep 1 | timeout 5 openssl s_client "${args[@]}" 2>&1 || true
}
for cn in kd240-t1 tool-t1 rogue-t1; do
  out=$(s_client $cn)
  if grep -q "Verify return code: 0" <<<"$out" && grep -q TLSv1.3 <<<"$out" \
     && ! grep -qi "alert" <<<"$out"; then
    ok "mTLS handshake with $cn"
  else fail "mTLS handshake with $cn"; echo "$out" | grep -i -E "alert|error|verify" | head -5; fi
done
out=$(s_client -)
grep -qi "certificate required" <<<"$out" && ok "no client cert: certificate required" \
  || { fail "no client cert refused"; echo "$out" | grep -i -E "alert|error" | head -5; }
out=$(s_client other-t1)
grep -qi "unknown ca" <<<"$out" && ok "cert from another CA: unknown ca" \
  || { fail "cert from another CA refused"; echo "$out" | grep -i -E "alert|error" | head -5; }
out=$(curl -sS -i -N --max-time 2 -H "Connection: Upgrade" -H "Upgrade: websocket" \
  -H "Sec-WebSocket-Version: 13" -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" \
  "http://127.0.0.1:$WS/mesh" 2>&1 || true)
grep -q "^HTTP/1.1 101" <<<"$out" && ok "ws upgrade on /mesh, no cert" \
  || { fail "ws upgrade on /mesh"; echo "$out" | head -5; }

# ---- level 3 -------------------------------------------------------------
if [[ -n $py ]]; then
  # The KD240's own router: no ACL, a tcp listener for the device sessions,
  # and the mTLS link to the mesh router with kd240-t1's cert.
  cat >"$work/edge.json5" <<EOF
{
  mode: "router",
  listen: { endpoints: ["tcp/127.0.0.1:17450"] },
  connect: { endpoints: ["tls/localhost:$TLS"] },
  scouting: { multicast: { enabled: false }, gossip: { enabled: false } },
  plugins_loading: { enabled: false },
  transport: { link: { tls: {
    root_ca_certificate: "$work/tls/ca",
    connect_certificate: "$c/kd240-t1.crt",
    connect_private_key: "$c/kd240-t1.key",
    enable_mtls: true,
  } } },
}
EOF
  RUST_LOG=info "$zenohd" -c "$work/edge.json5" >"$work/edge.log" 2>&1 &
  pids+=("$!")
  for _ in $(seq 50); do listening 17450 && break; sleep 0.1; done
  sleep 1
  for mode in client router; do
    echo "--- level 3, devices as $mode"
    if "$py" -I "$here/acl_test.py" --certs "$c" --tls "tls/localhost:$TLS" \
         --ws "ws/127.0.0.1:$WS" --edge tcp/127.0.0.1:17450 --devices "$mode"; then
      ok "acl cases ($mode)"
    else fail "acl cases ($mode)"; fi
  done
  n=$(grep -c "is unauthorized to" "$work/zenohd.log" || true)
  echo "router logged $n denials (zenohd.log, \"is unauthorized to\")"
fi

if kill -0 "$zpid" 2>/dev/null; then ok "zenohd still up"; else fail "zenohd died"; fi
echo "$fails failure(s)"
[[ $fails == 0 ]]
