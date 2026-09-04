#!/bin/bash
# Put a freshly compiled model on the car and prove it works. (Claude, 2026-09-02; hardened 09-03)
#
#   ~/install_hat_model.sh ~/Downloads/grove.hef
#
# Keeps the previous model so a bad build can be undone in one command.
set -u
HEF="${1:-$HOME/Downloads/grove.hef}"
HOST=robocar

[ -f "$HEF" ] || { echo "No such file: $HEF"; exit 1; }

# A HEF starts with the 4-byte magic \x01HEF. The DFC wheel arrived truncated
# once before and pip only said "invalid wheel", so verify shape before shipping.
MAGIC=$(head -c 4 "$HEF" | xxd -p)
[ "$MAGIC" = "01484546" ] || { echo "  NOT a valid HEF (magic=$MAGIC, want 01484546)"; exit 1; }
LOCAL_MD5=$(md5 -q "$HEF")
printf "  model: %s (%.1f MB) md5 %s\n" "$(basename "$HEF")" \
    "$(echo "scale=1; $(stat -f%z "$HEF")/1048576" | bc)" "$LOCAL_MD5"

ssh -o ConnectTimeout=10 "$HOST" true 2>/dev/null || {
    echo "  cannot reach the Pi over ssh"; exit 1; }

echo "  backing up any existing model ..."
ssh "$HOST" 'mkdir -p ~/oakd_project/models && cd ~/oakd_project/models && \
    { [ -f grove.hef ] && cp -f grove.hef grove.hef.prev && echo "    kept grove.hef.prev" || echo "    no previous model"; }'

echo "  copying ..."
scp -q "$HEF" "$HOST":~/oakd_project/models/grove.hef || { echo "  copy failed"; exit 1; }

# Prove the bytes survived the wire before trusting the model.
REMOTE_MD5=$(ssh "$HOST" 'md5sum ~/oakd_project/models/grove.hef' | awk '{print $1}')
if [ "$LOCAL_MD5" != "$REMOTE_MD5" ]; then
    echo "  CHECKSUM MISMATCH  local=$LOCAL_MD5  pi=$REMOTE_MD5"
    echo "  the transfer corrupted; rolling back"
    ssh "$HOST" '[ -f ~/oakd_project/models/grove.hef.prev ] && mv ~/oakd_project/models/grove.hef.prev ~/oakd_project/models/grove.hef'
    exit 1
fi
echo "    checksum verified on the Pi"

# Order matters: index 0/1/2 must match data.yaml names: ['trees','shrubs','people']
printf "trees\nshrubs\npeople\n" | ssh "$HOST" 'cat > ~/oakd_project/models/grove_classes.txt'
echo "  installed grove.hef + grove_classes.txt"

echo
echo "  === self-test on real recorded frames ==="
ssh "$HOST" 'python3 ~/oakd_project/hailo_backend.py' 2>&1 | sed 's/^/    /'

echo
echo "  If that listed trees/shrubs with an fps figure, the HAT is live."
echo "  NOTE: this HEF normalizes on-chip - the backend must feed RAW uint8 RGB,"
echo "        not values already divided by 255."
echo "  NOTE: preprocessing must MATCH the calibration this HEF was built with."
echo "        grove_lb.hef = letterbox (mAP50 0.795);  grove.hef = plain resize (0.618)"
echo "        measured on the float ONNX over 261 val images at conf 0.001."
echo "  Next lap uses it automatically:  ~/survey.sh --live --fps 1 --radius 3.0"
echo "  To roll back:  ssh $HOST 'mv ~/oakd_project/models/grove.hef.prev ~/oakd_project/models/grove.hef'"
