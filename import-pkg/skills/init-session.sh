#!/usr/bin/env bash
# 为一次包引入任务创建隔离的工作目录和 session.json
#
# 用法：
#   SESSION_DIR=$(bash init-session.sh <pkgname>)
#   SESSION_DIR=$(bash init-session.sh <pkgname> <upstream_url>)
#   SESSION_DIR=$(bash init-session.sh <pkgname> --version <ver>)
#   SESSION_DIR=$(bash init-session.sh <pkgname> --hash <commit_hash>)
#
# Session ID 推导规则：
#   提供 upstream_url  → sha256(url) 前 8 位，同一 URL 永远复用同一 session
#   提供 --version     → sha256(pkgname@version) 前 8 位
#   提供 --hash        → commit hash 前 8 位
#   不提供任何标识符   → timestamp+rand（每次新建，向后兼容）

set -euo pipefail

PKG="${1:-}"
if [[ -z "$PKG" ]]; then
  echo "[ERROR] 用法: $0 <pkgname> [<upstream_url> | --version <ver> | --hash <hash>]" >&2
  exit 1
fi

# ── 解析参数，推导 session key ────────────────────────────────────────────────
SESSION_KEY=""
UPSTREAM_URL=""
VERSION=""
shift  # 消费 pkgname

if [[ $# -gt 0 ]]; then
  case "$1" in
    --version)
      VERSION="${2:-}"
      if [[ -z "$VERSION" ]]; then
        echo "[ERROR] --version 需要一个版本号参数" >&2; exit 1
      fi
      SESSION_KEY=$(echo "${PKG}@${VERSION}" | sha256sum | head -c8)
      echo "[init-session] key source : pkgname@version (${PKG}@${VERSION})" >&2
      ;;
    --hash)
      COMMIT="${2:-}"
      if [[ -z "$COMMIT" ]]; then
        echo "[ERROR] --hash 需要一个 commit hash 参数" >&2; exit 1
      fi
      SESSION_KEY="${COMMIT:0:8}"
      echo "[init-session] key source : commit hash (${SESSION_KEY})" >&2
      ;;
    --*)
      echo "[ERROR] 未知参数: $1" >&2; exit 1
      ;;
    *)
      # 位置参数视为 upstream_url
      UPSTREAM_URL="$1"
      SESSION_KEY=$(echo "${UPSTREAM_URL}" | sha256sum | head -c8)
      echo "[init-session] key source : upstream_url hash (${SESSION_KEY})" >&2
      ;;
  esac
fi

# ── 确定 session 目录 ─────────────────────────────────────────────────────────
if [[ -n "$SESSION_KEY" ]]; then
  # 有确定性 key：固定路径，天然幂等
  SESSION_ID="${PKG}-${SESSION_KEY}"
  SESSION_DIR="/tmp/claude-ws/${SESSION_ID}"
  CONTAINER="oe-build-env-${SESSION_ID}"

  # 如果目录和容器都存在，直接复用
  if [[ -d "${SESSION_DIR}" ]] && [[ -f "${SESSION_DIR}/session.json" ]]; then
    EXISTING_CONTAINER=$(python3 -c \
      "import json; print(json.load(open('${SESSION_DIR}/session.json'))['container'])" \
      2>/dev/null || true)
    if [[ -n "${EXISTING_CONTAINER}" ]] && docker inspect "${EXISTING_CONTAINER}" &>/dev/null 2>&1; then
      echo "[init-session] reusing existing session: ${SESSION_DIR}" >&2
      echo "[init-session] container : ${EXISTING_CONTAINER}" >&2
      echo "${SESSION_DIR}"
      exit 0
    fi
  fi
else
  # 无标识符：每次新建（向后兼容原有行为）
  TIMESTAMP=$(date +%Y%m%d-%H%M%S)
  RAND=$(head -c4 /dev/urandom | xxd -p)
  SESSION_ID="${PKG}-${TIMESTAMP}-${RAND}"
  SESSION_DIR="/tmp/claude-ws/${SESSION_ID}"
  CONTAINER="oe-build-env-${SESSION_ID}"
fi

# ── 新建 session ──────────────────────────────────────────────────────────────
TMP_DIR="${SESSION_DIR}/tmp"
mkdir -p "${SESSION_DIR}" "${TMP_DIR}"

cat > "${SESSION_DIR}/session.json" <<EOF
{
  "session_id": "${SESSION_ID}",
  "container":  "${CONTAINER}",
  "tmp_dir":    "${TMP_DIR}",
  "pkgname":    "${PKG}",
  "upstream_url": "${UPSTREAM_URL}",
  "version":    "${VERSION}"
}
EOF

echo "[init-session] session_id : ${SESSION_ID}"  >&2
echo "[init-session] work_dir   : ${SESSION_DIR}" >&2
echo "[init-session] container  : ${CONTAINER}"   >&2
echo "[init-session] tmp_dir    : ${TMP_DIR}"      >&2

# ── 注册 pkgname → session_dir 映射（供 agent teammates 查找）────────────────
# 查找 .claude/skills 目录（向上遍历）
REGISTRY=$(python3 -c "
import pathlib, sys
for p in [pathlib.Path('$PWD')] + list(pathlib.Path('$PWD').parents):
    c = p / '.claude' / 'skills' / 'pkg-introduce' / 'session_registry.json'
    if c.parent.exists():
        print(c); break
")
python3 - <<PYEOF
import json, os
reg = {}
if os.path.exists("${REGISTRY}"):
    try:
        reg = json.load(open("${REGISTRY}"))
    except Exception:
        reg = {}
reg["${PKG}"] = "${SESSION_DIR}"
json.dump(reg, open("${REGISTRY}", "w"), indent=2, ensure_ascii=False)
PYEOF

echo "${SESSION_DIR}"
