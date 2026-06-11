#!/bin/zsh
set -e

cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt

PERSON="${AIUSAGE_PERSON:-$(whoami)}"
TODAY="$(date +%F)"
OUT_DIR="${AIUSAGE_OUT:-$HOME/Desktop}"

echo "导出 AI 使用记录"
echo "人员: $PERSON"
echo "日期: $TODAY"
echo "输出: $OUT_DIR"
echo

python aiusage.py export-day \
  --person "$PERSON" \
  --date "$TODAY" \
  --out "$OUT_DIR" \
  --verbose

echo
echo "完成。导出的 zip 已生成到: $OUT_DIR"
echo "按任意键关闭..."
read -k 1
