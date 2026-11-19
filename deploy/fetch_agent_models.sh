#!/usr/bin/env bash
# Downloads the small plate models into deploy/agent-models/ so agent.Dockerfile can copy them in.
# Run once before building the agent image (re-running skips files that are already there).
set -euo pipefail
cd "$(dirname "$0")"
G=https://github.com/ankandrew
get() { [ -s "$1" ] || { mkdir -p "$(dirname "$1")"; curl -fL --retry 3 -o "$1.part" "$2" && mv "$1.part" "$1"; }; }
get agent-models/open-image-models/yolo-v9-t-640-license-plate-end2end/yolo-v9-t-640-license-plates-end2end.onnx \
    "$G/open-image-models/releases/download/assets/yolo-v9-t-640-license-plates-end2end.onnx"
get agent-models/fast-plate-ocr/cct-s-v2-global-model/cct_s_v2_global.onnx \
    "$G/cnn-ocr-lp/releases/download/arg-plates/cct_s_v2_global.onnx"
get agent-models/fast-plate-ocr/cct-s-v2-global-model/cct_s_v2_global_plate_config.yaml \
    "$G/cnn-ocr-lp/releases/download/arg-plates/cct_s_v2_global_plate_config.yaml"
echo "Plate models ready in deploy/agent-models/"
