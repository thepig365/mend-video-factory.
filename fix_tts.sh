#!/bin/bash
# 自动修复 Mend Video Factory 的 TTS 环境

echo ">> 正在停止任何运行中的服务..."
pkill -f uvicorn || true

echo ">> 正在进入虚拟环境并修复软件包版本..."
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip uninstall -y transformers numpy
./.venv/bin/python -m pip install -r requirements_tts_coqui.txt

echo ""
echo "========================================"
echo "✅ 环境修复完成！"
echo "现在你可以运行 ./factory.sh web 并重新 Generate 了。"
echo "========================================"

