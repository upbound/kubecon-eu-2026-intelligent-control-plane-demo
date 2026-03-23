#!/usr/bin/env bash
# KubeCon Demo — Cache AI
# Run from: configuration-cache/
#
# Usage:
#   ./demo.sh setup          # create demo Cache XRs
#   ./demo.sh before-1       # show the raw Crossplane noise (the pain)
#   ./demo.sh after-1        # show AI diagnosis annotation (the relief)
#   ./demo.sh watch          # open a live watch pane (run in separate terminal)
#   ./demo.sh remediate      # authorize AI to auto-fix ardId
#   ./demo.sh cost-before    # show xl cache cost with no context
#   ./demo.sh cost-after     # show AI cost recommendation
#   ./demo.sh cost-optimize  # authorize AI to downscale
#   ./demo.sh cleanup        # delete demo resources

set -euo pipefail

NS="default"
CACHE1="demo-stuck"
CACHE3="demo-costly"
TIMEOUT=180

# ── colors ────────────────────────────────────────────────────────────────────

RED='\033[0;31m'
YELLOW='\033[0;33m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
RESET='\033[0m'

header()  { echo -e "\n${BOLD}${CYAN}$*${RESET}"; }
pain()    { echo -e "${YELLOW}$*${RESET}"; }
good()    { echo -e "${GREEN}$*${RESET}"; }
label()   { echo -e "${DIM}$*${RESET}"; }
ok()      { echo -e "  ${GREEN}✓${RESET} $*"; }
fail()    { echo -e "  ${RED}✗${RESET} $*"; }

# ── helpers ───────────────────────────────────────────────────────────────────

ai_annotations() {
  local name=$1
  kubectl get cache "$name" -n "$NS" -o json 2>/dev/null \
    | python3 -c "
import sys, json

RESET  = '\033[0m'
BOLD   = '\033[1m'
GREEN  = '\033[0;32m'
CYAN   = '\033[0;36m'
YELLOW = '\033[0;33m'
DIM    = '\033[2m'

a  = json.load(sys.stdin)['metadata'].get('annotations', {})
ai = {k: v for k, v in a.items() if k.startswith('cache.mbcp.cloud/')}

print('{')
items = list(ai.items())
for i, (k, v) in enumerate(items):
    comma = ',' if i < len(items) - 1 else ''
    short_k = k.replace('cache.mbcp.cloud/', '')
    print(f'  {DIM}{k!r}{RESET}: {GREEN}{BOLD}{v!r}{RESET}{comma}')
print('}')
"
}

wait_for_annotation() {
  local name=$1 key=$2
  local escaped="${key//\./\\.}"
  local deadline=$((SECONDS + TIMEOUT))
  printf "  ${DIM}Waiting for %s${RESET}" "$key"
  while [[ $SECONDS -lt $deadline ]]; do
    local val
    val=$(kubectl get cache "$name" -n "$NS" \
      -o "jsonpath={.metadata.annotations.${escaped}}" 2>/dev/null || true)
    if [[ -n "$val" && "$val" != "null" ]]; then
      echo -e " ${GREEN}✓${RESET}"
      return 0
    fi
    printf "."
    sleep 4
  done
  echo -e " ${RED}✗ timed out${RESET}"
  return 1
}

_reset_demo_state() {
  for name in "$CACHE1" "$CACHE3"; do
    kubectl annotate cache "$name" -n "$NS" \
      cache.mbcp.cloud/last-diagnosed- \
      cache.mbcp.cloud/diagnosis- \
      cache.mbcp.cloud/auto-remediated- \
      cache.mbcp.cloud/cost-analysis-timestamp- \
      cache.mbcp.cloud/cost-recommendation- \
      cache.mbcp.cloud/cost-optimized- \
      2>/dev/null || true
    kubectl label cache "$name" -n "$NS" \
      allow-auto-remediation- \
      allow-cost-optimization- \
      2>/dev/null || true
  done
}

# ── setup ─────────────────────────────────────────────────────────────────────

cmd_setup() {
  header "=== Setup: creating demo Cache XRs ==="

  if kubectl get cache "$CACHE1" "$CACHE3" -n "$NS" &>/dev/null; then
    label "  Caches already exist — resetting demo state..."
    _reset_demo_state
    ok "annotations and authorization labels cleared"
  else
    label "  Cleaning up any previous demo resources..."
    kubectl delete cache "$CACHE1" "$CACHE3" -n "$NS" --ignore-not-found 2>/dev/null || true
    kubectl wait --for=delete cache/"$CACHE1" cache/"$CACHE3" \
      -n "$NS" --timeout=60s 2>/dev/null || true
    ok "previous resources cleared"
    echo ""
  fi

  kubectl apply -f - <<EOF
apiVersion: data.mbcp.cloud/v1alpha2
kind: Cache
metadata:
  name: $CACHE1
  namespace: $NS
  labels:
    ccoe.mbcp.cloud/solution: demo
    ai: enabled
spec:
  providerConfigRef:
    kind: ClusterProviderConfig
    name: azure-provider
  parameters:
    application: demo-stuck-app
    ardId: ARD-MISSING
    sku: s
EOF
  ok "$CACHE1 applied (ardId=ARD-MISSING — no ResourceGroup will match)"

  kubectl apply -f - <<EOF
apiVersion: data.mbcp.cloud/v1alpha2
kind: Cache
metadata:
  name: $CACHE3
  namespace: $NS
  labels:
    ccoe.mbcp.cloud/solution: demo
    ai: enabled
    cost-analysis: enabled
spec:
  providerConfigRef:
    kind: ClusterProviderConfig
    name: azure-provider
  parameters:
    application: demo-costly-app
    ardId: ARD-001
    sku: xl
EOF
  ok "$CACHE3 applied (sku=xl — cost optimization target)"
  echo ""
  label "  Give Crossplane ~30s to reconcile before running before-1."
}

# ── before-1: the pain ────────────────────────────────────────────────────────

cmd_before_1() {
  header "=== WITHOUT AI: what the SRE sees when $CACHE1 is stuck ==="
  echo ""

  label "── kubectl get cache ──"
  kubectl get cache "$CACHE1" -n "$NS"
  echo ""

  label "── status.conditions ──"
  kubectl get cache "$CACHE1" -n "$NS" -o yaml \
    | python3 -c "
import sys, yaml

RED   = '\033[0;31m'
RESET = '\033[0m'
DIM   = '\033[2m'

doc   = yaml.safe_load(sys.stdin)
conds = doc.get('status', {}).get('conditions', [])
for c in conds:
    status = c.get('status','?')
    color  = RED if status == 'False' else DIM
    print(f\"{color}  [{c.get('type','?')}] status={status} reason={c.get('reason','?')}{RESET}\")
    msg = c.get('message','')
    if msg:
        print(f\"{color}    {msg}{RESET}\")
" 2>/dev/null || kubectl get cache "$CACHE1" -n "$NS" -o yaml | grep -A 40 'conditions:'
  echo ""

  label "── kubectl get events ──"
  kubectl get events -n "$NS" \
    --field-selector "involvedObject.name=$CACHE1" \
    --sort-by='.lastTimestamp' 2>/dev/null | tail -10 \
    || pain "  (no events yet)"
  echo ""

  pain "  ^ Unready resources. Which layer? Which provider? Why? — You're on your own."
  echo ""
  label "  Now run: ./demo.sh after-1"
}

# ── after-1: the relief ───────────────────────────────────────────────────────

cmd_after_1() {
  header "=== WITH AI: diagnosis for $CACHE1 ==="
  wait_for_annotation "$CACHE1" "cache.mbcp.cloud/last-diagnosed"
  wait_for_annotation "$CACHE1" "cache.mbcp.cloud/diagnosis"
  echo ""
  label "── AI annotations ──"
  ai_annotations "$CACHE1"
  echo ""
  good "  Root cause identified. Remediation steps in the annotation."
}

# ── watch ─────────────────────────────────────────────────────────────────────

cmd_watch() {
  header "=== Live watch — run in a separate terminal ==="
  kubectl get cache -n "$NS" -w \
    -o custom-columns='NAME:.metadata.name,ACTIVE:.status.backend.active,CLOUD-READY:.status.backend.cloud.ready,DIAGNOSIS:.metadata.annotations.cache\.mbcp\.cloud/diagnosis,COST:.metadata.annotations.cache\.mbcp\.cloud/cost-recommendation' \
  | while IFS= read -r line; do
      if [[ "$line" == NAME* ]]; then
        echo -e "${BOLD}${CYAN}${line}${RESET}"
      elif echo "$line" | grep -q "true.*healthy"; then
        echo -e "${GREEN}${line}${RESET}"
      elif echo "$line" | grep -q "true"; then
        echo -e "${GREEN}${line}${RESET}"
      elif echo "$line" | grep -q "RedisCache:\|PrivateEndpoint:\|Fallback\|Remediation"; then
        echo -e "${YELLOW}${line}${RESET}"
      elif echo "$line" | grep -q "<none>"; then
        echo -e "${DIM}${line}${RESET}"
      else
        echo "$line"
      fi
    done
}

# ── remediate ────────────────────────────────────────────────────────────────

cmd_remediate() {
  header "=== Stage 3: Auto-Remediation — authorizing AI to fix $CACHE1 ==="
  echo ""
  label "  Stage 2 diagnosed the ARD mismatch. Now we authorize the AI to fix it."
  echo ""
  label "  Setting allow-auto-remediation=true on $CACHE1..."
  kubectl label cache "$CACHE1" -n "$NS" \
    "allow-auto-remediation=true" --overwrite
  ok "allow-auto-remediation label set — remediation WatchOperation will now fire"
  echo ""
  label "  Waiting for AI to patch spec.parameters.ardId..."
  wait_for_annotation "$CACHE1" "cache.mbcp.cloud/auto-remediated"
  echo ""
  label "── AI remediation result ──"
  ai_annotations "$CACHE1"
  echo ""
  good "  ardId patched. Crossplane is now reconciling with the correct environment."
  label "  Watch pane: cache should transition toward Ready."
}

# ── cost-before ───────────────────────────────────────────────────────────────

cmd_cost_before() {
  header "=== WITHOUT AI: what the SRE sees for $CACHE3 ==="
  echo ""

  label "── kubectl get cache ──"
  kubectl get cache "$CACHE3" -n "$NS"
  echo ""

  label "── spec.parameters.sku + status.backend ──"
  kubectl get cache "$CACHE3" -n "$NS" -o json \
    | python3 -c "
import sys, json

RED   = '\033[0;31m'
CYAN  = '\033[0;36m'
DIM   = '\033[2m'
RESET = '\033[0m'

obj = json.load(sys.stdin)
sku = obj.get('spec', {}).get('parameters', {}).get('sku', '?')
b   = obj.get('status', {}).get('backend', {})
active = b.get('active', '?')
print(f'  spec.parameters.sku:   {CYAN}{sku}{RESET}')
print(f'  status.backend.active: {DIM}{active}{RESET}')
print(f'  cloud.ready:           {DIM}{b.get(\"cloud\", {}).get(\"ready\", \"?\")}{RESET}')
"
  echo ""

  pain "  ^ SKU xl. Is this justified? What does it cost? No way to tell without analysis."
  echo ""
  label "  Now run: ./demo.sh cost-after"
}

# ── cost-after ────────────────────────────────────────────────────────────────

cmd_cost_after() {
  header "=== WITH AI: cost analysis for $CACHE3 ==="
  wait_for_annotation "$CACHE3" "cache.mbcp.cloud/cost-analysis-timestamp"
  wait_for_annotation "$CACHE3" "cache.mbcp.cloud/cost-recommendation"
  echo ""
  label "── AI cost annotations ──"
  ai_annotations "$CACHE3"
  echo ""
  good "  Cost analysis complete. Recommendation ready."
  label "  Now run: ./demo.sh cost-optimize"
}

# ── cost-optimize ─────────────────────────────────────────────────────────────

cmd_cost_optimize() {
  header "=== Stage 4: Cost Optimization — authorizing AI to downscale $CACHE3 ==="
  echo ""
  label "  AI identified a downscale opportunity. Now we authorize it."
  echo ""
  label "  Setting allow-cost-optimization=true on $CACHE3..."
  kubectl label cache "$CACHE3" -n "$NS" \
    "allow-cost-optimization=true" --overwrite
  ok "allow-cost-optimization label set — cost-optimizer WatchOperation will now fire"
  echo ""
  label "  Waiting for AI to patch spec.parameters.sku..."
  wait_for_annotation "$CACHE3" "cache.mbcp.cloud/cost-optimized"
  echo ""
  label "── AI cost optimization result ──"
  ai_annotations "$CACHE3"
  echo ""
  good "  SKU downgraded. Crossplane is now reconciling with the cost-optimized tier."
  label "  Watch pane: cache should reprovision at lower SKU."
}

# ── cleanup ───────────────────────────────────────────────────────────────────

cmd_cleanup() {
  header "=== Cleanup ==="
  kubectl delete cache "$CACHE1" "$CACHE3" -n "$NS" --ignore-not-found
  ok "done"
}

# ── dispatch ──────────────────────────────────────────────────────────────────

case "${1:-help}" in
  setup)         cmd_setup ;;
  before-1)      cmd_before_1 ;;
  after-1)       cmd_after_1 ;;
  remediate)     cmd_remediate ;;
  cost-before)   cmd_cost_before ;;
  cost-after)    cmd_cost_after ;;
  cost-optimize) cmd_cost_optimize ;;
  watch)         cmd_watch ;;
  cleanup)       cmd_cleanup ;;
  *)
    echo -e "${BOLD}Usage:${RESET} $0 {setup|before-1|after-1|remediate|cost-before|cost-after|cost-optimize|watch|cleanup}"
    echo ""
    echo -e "${BOLD}Full demo flow:${RESET}"
    echo -e "  ${CYAN}Terminal A:${RESET}  ./demo.sh watch"
    echo -e "  ${CYAN}Terminal B:${RESET}  ./demo.sh setup"
    echo -e ""
    echo -e "  ${BOLD}Stage 2 — Intelligent Assistance:${RESET}"
    echo -e "               ./demo.sh before-1      ${DIM}# the noise — stuck resources, no context${RESET}"
    echo -e "               ./demo.sh after-1       ${DIM}# AI diagnosis — root cause + remediation steps${RESET}"
    echo -e ""
    echo -e "  ${BOLD}Stage 3 — Intelligent Control:${RESET}"
    echo -e "               ./demo.sh remediate     ${DIM}# authorize AI → ardId auto-patched → cache recovers${RESET}"
    echo -e ""
    echo -e "  ${BOLD}Stage 4 — Cost Optimization (Assistance + Control):${RESET}"
    echo -e "               ./demo.sh cost-before   ${DIM}# the noise — xl cache at \$700/mo, no context${RESET}"
    echo -e "               ./demo.sh cost-after    ${DIM}# Assistance: AI recommends downscale${RESET}"
    echo -e "               ./demo.sh cost-optimize ${DIM}# Control:    authorize AI → sku optimized${RESET}"
    echo -e ""
    echo -e "               ./demo.sh cleanup"
    ;;
esac
