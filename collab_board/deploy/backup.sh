#!/usr/bin/env bash
# 协同看板 · 每日 pg_dump 备份（crontab: 0 3 * * * /www/wwwroot/collab_board/deploy/backup.sh）
# 保留最近 7 份；恢复演练：pg_restore/psql 导入临时库核对行数。
set -euo pipefail

BACKUP_DIR="${BOARD_BACKUP_DIR:-/www/backup}"
STAMP="$(date +%F)"
CONTAINER_PROJECT="$(basename "$(dirname "$(dirname "$0")")")"  # collab_board

mkdir -p "$BACKUP_DIR"
cd "$(dirname "$0")/.."

docker compose exec -T db pg_dump -U board -d board -Fc \
  > "${BACKUP_DIR}/board-db-${STAMP}.dump"

# 保留最近 7 份
ls -1t "${BACKUP_DIR}"/board-db-*.dump 2>/dev/null | tail -n +8 | xargs -r rm -f --

echo "[backup] board-db-${STAMP}.dump done -> ${BACKUP_DIR}"
