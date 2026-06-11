#!/bin/zsh
set -e

cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt

echo "启动 AI Usage Dashboard..."
echo "浏览器会自动打开。如果没有打开，请访问终端里显示的 Local URL。"
echo

streamlit run app.py
