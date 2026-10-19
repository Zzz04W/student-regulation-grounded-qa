#!/usr/bin/env bash
# 发布到 GitHub
# 用法：GITHUB_TOKEN=<your_pat> ./scripts/publish.sh [仓库名] [public|private]
# 所需 Token 权限：repo（创建仓库并推送）
set -euo pipefail

REPO_NAME="${1:-student-regulation-grounded-qa}"
VISIBILITY="${2:-public}"
TOKEN="${GITHUB_TOKEN:?请设置 GITHUB_TOKEN 环境变量}"

PRIVATE=false
[ "$VISIBILITY" = "private" ] && PRIVATE=true

echo "==> 获取 GitHub 用户名"
USER_LOGIN=$(curl -s -H "Authorization: Bearer $TOKEN" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/user | grep '"login"' | head -1 | sed 's/.*"login" *: *"\([^"]*\)".*/\1/')

if [ -z "$USER_LOGIN" ]; then
  echo "错误：Token 无效或权限不足（需要 repo 权限）" >&2
  exit 1
fi
echo "    用户：$USER_LOGIN"

echo "==> 创建仓库 $REPO_NAME（$VISIBILITY）"
HTTP=$(curl -s -o /tmp/gh_resp.json -w "%{http_code}" \
  -X POST https://api.github.com/user/repos \
  -H "Authorization: Bearer $TOKEN" \
  -H "Accept: application/vnd.github+json" \
  -d "{\"name\":\"$REPO_NAME\",\"description\":\"带出处引用的制度知识问答系统：检索-判断-生成-校验四环节编排，含拒答验证与 RAG/技能对比\",\"private\":$PRIVATE,\"auto_init\":false}")

if [ "$HTTP" != "201" ] && [ "$HTTP" != "200" ]; then
  echo "警告：创建仓库返回 $HTTP，尝试直接推送（仓库可能已存在）" >&2
  cat /tmp/gh_resp.json | head -5
fi

echo "==> 推送代码"
git remote remove origin 2>/dev/null || true
git remote add origin "https://${USER_LOGIN}:${TOKEN}@github.com/${USER_LOGIN}/${REPO_NAME}.git"
git branch -M main
git push -u origin main

# 清理含 token 的远程地址，改用凭据安全的写法
git remote set-url origin "https://github.com/${USER_LOGIN}/${REPO_NAME}.git"

echo "==> 完成：https://github.com/${USER_LOGIN}/${REPO_NAME}"
