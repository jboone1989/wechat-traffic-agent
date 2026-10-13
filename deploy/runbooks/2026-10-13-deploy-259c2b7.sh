#!/usr/bin/env bash
# 2026-10-13 部署 main@259c2b7：omnipost 发布链路 + X 读者反应回流
#
# 用法（在生产服务器上，以 ubuntu 用户执行）：
#   cd /home/ubuntu/src/content-agent && git fetch origin main
#   git show origin/main:deploy/runbooks/2026-10-13-deploy-259c2b7.sh > /tmp/deploy.sh
#   bash /tmp/deploy.sh
#
# 每个检查点失败都会停止；切换后失败会自动把 current 指回旧版本并重启。
# 不读取任何 .env/secrets 内容，不改 systemd 单元文件，不碰 omnipost/agent-runtime/virtualbrain。
set -euo pipefail

SHA=259c2b7
OLD=/home/ubuntu/content-agent/releases/05c99a1-square-data-review
NEW=/home/ubuntu/content-agent/releases/${SHA}-omnipost-x-feedback
CURRENT=/home/ubuntu/content-agent/current
STATE=/home/ubuntu/content-agent-state
PY=/home/ubuntu/content-agent-venv-current/bin/python
DEPS=/home/ubuntu/agent-runtime/current/src:/home/ubuntu/virtualbrain/current/src
DB=$STATE/content/automatic_website/articles.sqlite3

say() { printf '\n=== %s ===\n' "$*"; }
pause() { read -r -p "$* 继续？[y/N] " ok; [[ "$ok" == y ]] || { echo "已停止"; exit 1; }; }
point_current() { ln -sfn "$1" "$CURRENT.new" && mv -T "$CURRENT.new" "$CURRENT"; }
rollback() {
  say "回滚到 $OLD"
  point_current "$OLD"
  sudo -n systemctl restart content-agent.service; sleep 30
  systemctl is-active content-agent.service || true
  exit 1
}

say "A. 建新版本 $NEW（不影响线上）"
cd /home/ubuntu/src/content-agent
git fetch origin main
git merge-base --is-ancestor "$SHA" origin/main || { echo "$SHA 不在 origin/main 上，停止"; exit 1; }
[[ -e "$NEW" ]] || git worktree add --detach "$NEW" "$SHA"
echo "旧版本中未跟踪/被忽略的非缓存文件："
(cd "$OLD" && git status --short --ignored | grep -E '^(\?\?|!!)' | grep -vE '__pycache__|\.pyc$|\.pytest_cache' || true)
# .env.ferro-llm 是运行所需的凭据文件：只复制，不读取
[[ -f "$OLD/.env.ferro-llm" ]] && cp -p "$OLD/.env.ferro-llm" "$NEW/.env.ferro-llm"
pause "上面列出的文件里，除 .env.ferro-llm 外如果还有运行需要的文件，请先手动复制到 $NEW。"

say "B. 冒烟测试（检查点 1）"
cd "$NEW"
export PYTHONPATH="$NEW/src:$DEPS"
$PY -c "import wechat_traffic_agent.content_goal_loop, wechat_traffic_agent.platforms.binance_square_editorial, wechat_traffic_agent.platforms.x.x_outcomes, wechat_traffic_agent.platforms.omnipost_client; print('imports ok')"
if $PY -c "import pytest" 2>/dev/null; then
  $PY -m pytest -q -p no:cacheprovider tests/test_x_outcomes.py tests/test_square_editorial.py \
    tests/test_omnipost_client.py tests/test_social_performance.py tests/test_content_mind.py \
    tests/test_binance_square_distribution.py tests/test_x_social_square_gate.py \
    tests/test_backfill_omnipost_x_syndication.py
else
  echo "venv 里没有 pytest，只做了导入检查"
fi

say "C. 切换并重启（检查点 2）"
echo "旧目标：$(readlink -f "$CURRENT")"
pause "即将切换 current 并重启 content-agent.service。"
sudo -n true || { echo "sudo -n 需要密码，未切换，停止"; exit 1; }
point_current "$NEW"
echo "新目标：$(readlink -f "$CURRENT")"
sudo -n systemctl restart content-agent.service || rollback
sleep 45
systemctl is-active --quiet content-agent.service || rollback
if journalctl -u content-agent.service --no-pager --since "-2min" | grep -qE 'Traceback|ImportError|ModuleNotFoundError'; then
  journalctl -u content-agent.service --no-pager --since "-2min" | grep -E -A5 'Traceback|ImportError|ModuleNotFoundError' | head -40
  rollback
fi
journalctl -u content-agent.service --no-pager --since "-2min" | tail -10

say "D. 补登记 omnipost 已发出的 X 帖子（检查点 3）"
export PYTHONPATH="$NEW/src:/home/ubuntu/agent-runtime/current/src"
python3 -c "import sqlite3;s=sqlite3.connect('$DB');d=sqlite3.connect('$DB.bak-20261013-omnipost-backfill');s.backup(d);d.close();s.close();print('backup ok')"
$PY scripts/backfill_omnipost_x_syndication.py --state-root "$STATE"
pause "预览结果是否合理（ledger_pairs≈46、attached>0、unmatched_jobs 很少）？"
$PY scripts/backfill_omnipost_x_syndication.py --state-root "$STATE" --apply
$PY scripts/backfill_omnipost_x_syndication.py --state-root "$STATE"   # attached 应为 0

say "E. X 指标采集定时器"
systemctl is-enabled content-agent-x-outcomes.timer || true
journalctl -u content-agent-x-outcomes.service --no-pager -n 25 | tail -25 || true
pause "如果上面日志显示定时器是被有意停用的，请选 N。启用 content-agent-x-outcomes.timer？"
sudo -n systemctl enable --now content-agent-x-outcomes.timer
systemctl list-timers --all --no-pager | grep -i x-outcomes || true

say "完成。回滚方法：ln -sfn $OLD $CURRENT.new && mv -T $CURRENT.new $CURRENT && sudo systemctl restart content-agent.service"
