#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_engine_pin_freshness.py — 引擎 pin SHA 漂移新鲜度检查（轻量治理 / R3）

────────────────────────────────────────────────────────────────────────────
背景
────
心镜仓 CI 的契约测试硬编码 pin 了引擎仓 hanjun88/xinjing-relationship-engine
的 commit SHA（见 .github/workflows/ci.yml 中 Checkout xinjing-relationship-engine
的 ref 字段）。这保证了契约测试可复现，但存在 R3 结构性风险：

  若引擎修改了核心代码（arbitrate() 返回契约 / crisis_scan / MetaArbiter 等），
  CI 仍会 checkout 旧 pin 并跑通测试，而生产跑的是新代码——破坏性契约变更
  不会被捕获。

本脚本在心镜仓单仓内做最轻量的漂移检测（方案 C）：
  1. 从 ci.yml 解析当前 pin SHA（单一事实来源，不在脚本里重复硬编码）。
  2. 获取引擎 master HEAD。
  3. 比较 pin...master 之间变更的文件列表。
  4. 如果变更触及「核心路径」→ 输出 WARNING（contract may be stale），
     提示人工 bump pin；否则输出 safe。
  5. 永远 exit 0（warning 不阻塞流水线，配合 CI step 的 continue-on-error）。

────────────────────────────────────────────────────────────────────────────
Pin Bump 流程（什么时候 / 改哪里 / 怎么验证）
────
【什么时候 bump】
  - 本脚本在 CI log 中输出 "WARNING: pin behind master AND core changed"
    时，说明引擎核心代码已变更但心镜仓仍在测旧版本。
  - 触发条件：引擎 master 相对 pin 的 diff 中出现了 CORE_PATH_PREFIXES
    列出的路径（引擎核心包 / backend 契约层）。

【改哪个文件】
  - 唯一需要改的文件：.github/workflows/ci.yml
  - 找到 job `test-real-engine` 中 step
    "Checkout xinjing-relationship-engine (pinned SHA)" 的 `ref:` 字段。
  - 把旧 SHA 换成引擎 master 的新 SHA（40 位完整 commit hash，不是短 hash）。
  - 示例：
        ref: 4c4d8da3e5463bc0016c9e267227e6875854f45d   ← 旧
        ref: deea1fb16dfaf34ab82239f005978e9541e8269b   ← 新

【怎么验证】
  1. 本地跑一次本脚本：
         python scripts/check_engine_pin_freshness.py
     期望输出 "pin up to date" 或 "pin behind master, but core unchanged — safe"。
  2. push 到 main，观察 CI job `test-real-engine`：
     - "Checkout xinjing-relationship-engine (pinned SHA)" 会 checkout 新 SHA。
     - "Run engine contract tests" 必须全绿；如果新引擎核心改了契约，
       契约测试会红，这时需要同步修心镜仓的测试 / engine_client.py 适配层。
  3. 不要 bump 到 master branch name（@main / @master）—— pin 必须是固定 SHA，
     否则失去可复现性。

────────────────────────────────────────────────────────────────────────────
用法
────
  # CI 模式（引擎已被 actions/checkout 到本地路径，用 git diff）：
  python scripts/check_engine_pin_freshness.py --engine-path xinjing-engine

  # 本地模式（用 gh api，需要 gh 已认证）：
  python scripts/check_engine_pin_freshness.py

  # 显式指定 workflow 文件路径：
  python scripts/check_engine_pin_freshness.py --workflow .github/workflows/ci.yml
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# ── 常量 ────────────────────────────────────────────────────────────────────
ENGINE_REPO = "hanjun88/xinjing-relationship-engine"
ENGINE_BRANCH = "master"

# 「核心路径」前缀：这些路径下的文件变更 = 可能影响 arbitrate() 契约。
# 注意：
#   - xinjing-relationship-engine/engine/ 是 contract test 实际 import 的包
#     （engine_client.py 做 `from engine.meta_arbiter import ...`，
#      CI 把 XINJING_ENGINE_PATH 指向 xinjing-engine/xinjing-relationship-engine）。
#   - backend/ 是引擎仓的服务端层（api/contracts/orchestrator），
#     其契约变更同样可能影响心镜仓的集成假设。
#   - divination_consumer/ 等旁路边车目录不影响 arbitrate() 契约，不列入。
CORE_PATH_PREFIXES = (
    "xinjing-relationship-engine/engine/",
    "backend/",
)


# ── 从 ci.yml 解析 pin SHA ──────────────────────────────────────────────────
def parse_pin_sha(workflow_path: Path) -> str:
    """从 GitHub Actions workflow 中提取引擎仓 checkout 的 pin SHA。

    定位策略：找到 `repository: hanjun88/xinjing-relationship-engine` 这一行，
    然后在其后的 `with:` 块里找 `ref: <40位hex>`。
    """
    text = workflow_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    pin: str | None = None
    for i, line in enumerate(lines):
        if "repository:" in line and ENGINE_REPO in line:
            # 向下扫描最多 15 行找 ref:
            for j in range(i, min(i + 15, len(lines))):
                m = re.search(r"^\s*ref:\s*([0-9a-f]{40})\s*$", lines[j])
                if m:
                    pin = m.group(1)
                    break
            if pin:
                break
    if not pin:
        print(f"ERROR: 无法在 {workflow_path} 中找到引擎仓 {ENGINE_REPO} 的 pin SHA",
              file=sys.stderr)
        sys.exit(1)
    return pin


# ── 模式 A：本地 git diff（CI 中引擎已 checkout 到 --engine-path）──────────
def check_via_local_git(engine_path: Path, pin_sha: str) -> tuple[str, list[str]]:
    """在本地引擎 clone 上 fetch master 并 diff。

    返回 (master_sha, changed_files)。
    """
    # fetch 最新 master（shallow clone 也能 fetch 到 master tip）
    subprocess.run(
        ["git", "-C", str(engine_path), "fetch", "--depth=100",
         "origin", ENGINE_BRANCH],
        check=True, capture_output=True, text=True,
    )
    # master tip SHA
    master_sha = subprocess.check_output(
        ["git", "-C", str(engine_path), "rev-parse", "FETCH_HEAD"],
        text=True,
    ).strip()
    # diff --name-only pin...master（三点 diff：从 merge-base 到 master）
    # 用两点 diff 也可：`git diff --name-only {pin} {master}` 直接比两树。
    # 三点更精确（只看 master 自分叉以来的变更），但 shallow clone 可能
    # 没有 merge-base 对象；退化为两点 diff 更稳。
    try:
        diff_out = subprocess.check_output(
            ["git", "-C", str(engine_path), "diff", "--name-only",
             f"{pin_sha}...{master_sha}"],
            text=True, stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        # 三点失败（shallow 缺历史）→ 退化为两点
        diff_out = subprocess.check_output(
            ["git", "-C", str(engine_path), "diff", "--name-only",
             pin_sha, master_sha],
            text=True,
        )
    changed = [ln for ln in diff_out.splitlines() if ln.strip()]
    return master_sha, changed


# ── 模式 B：gh api（本地开发，gh 已认证）────────────────────────────────────
def check_via_gh_api(pin_sha: str) -> tuple[str, list[str]]:
    """用 gh api 获取 master HEAD + compare 差异。"""
    # master HEAD
    master_info = subprocess.check_output(
        ["gh", "api", f"repos/{ENGINE_REPO}/commits/{ENGINE_BRANCH}",
         "--jq", ".sha"],
        text=True,
    ).strip()
    # compare pin...master
    compare = subprocess.check_output(
        ["gh", "api",
         f"repos/{ENGINE_REPO}/compare/{pin_sha}...{master_info}",
         "--jq", "[.files[].filename]"],
        text=True,
    ).strip()
    # compare 返回 JSON 数组字符串
    import json
    changed = json.loads(compare) if compare else []
    return master_info, changed


# ── 主逻辑 ──────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(description="Check engine pin freshness (warning-only).")
    ap.add_argument("--workflow", default=".github/workflows/ci.yml",
                    help="path to GitHub Actions workflow file (default: .github/workflows/ci.yml)")
    ap.add_argument("--engine-path", default=None,
                    help="path to a local clone of the engine repo (CI mode). "
                         "If omitted, uses `gh api` (local dev mode).")
    args = ap.parse_args()

    workflow_path = Path(args.workflow)
    if not workflow_path.exists():
        # 允许从 repo root 或 scripts/ 目录运行
        alt = Path(__file__).resolve().parent.parent / ".github/workflows/ci.yml"
        if alt.exists():
            workflow_path = alt
        else:
            print(f"ERROR: workflow file not found: {workflow_path}", file=sys.stderr)
            sys.exit(1)

    pin_sha = parse_pin_sha(workflow_path)
    pin_short = pin_sha[:7]

    print(f"[engine-pin-freshness] engine repo   : {ENGINE_REPO}")
    print(f"[engine-pin-freshness] pinned SHA    : {pin_sha} ({pin_short})")
    print(f"[engine-pin-freshness] core prefixes : {CORE_PATH_PREFIXES}")

    # 获取 master + changed files
    if args.engine_path:
        engine_path = Path(args.engine_path)
        if not (engine_path / ".git").exists():
            print(f"ERROR: --engine-path {engine_path} is not a git repo", file=sys.stderr)
            sys.exit(1)
        master_sha, changed = check_via_local_git(engine_path, pin_sha)
        mode = "git(local)"
    else:
        master_sha, changed = check_via_gh_api(pin_sha)
        mode = "gh api"

    master_short = master_sha[:7]
    print(f"[engine-pin-freshness] master HEAD   : {master_sha} ({master_short})  [via {mode}]")
    print(f"[engine-pin-freshness] files changed : {len(changed)}")

    # ── 判定 ──
    if pin_sha == master_sha:
        print("[engine-pin-freshness] STATUS: pin up to date — no drift.")
        return

    # pin 落后于 master：检查核心路径是否变更
    core_changed = [
        f for f in changed
        if any(f.startswith(prefix) for prefix in CORE_PATH_PREFIXES)
    ]

    if core_changed:
        print("[engine-pin-freshness] ⚠️  WARNING: pin behind master AND core paths changed!")
        print("[engine-pin-freshness]    Core files changed since pin:")
        for f in core_changed[:20]:
            print(f"      - {f}")
        if len(core_changed) > 20:
            print(f"      ... and {len(core_changed) - 20} more")
        print("[engine-pin-freshness]    → Contract may be stale. Consider bumping the pin in")
        print(f"      {workflow_path} (ref: field under {ENGINE_REPO} checkout step).")
        print("[engine-pin-freshness]    See header comment of this script for bump procedure.")
    else:
        non_core = [f for f in changed if f not in core_changed]
        print("[engine-pin-freshness] STATUS: pin behind master, but core paths unchanged — safe.")
        if non_core:
            print("[engine-pin-freshness]    Changed (non-core) files:")
            for f in non_core[:10]:
                print(f"      - {f}")
            if len(non_core) > 10:
                print(f"      ... and {len(non_core) - 10} more")

    # 永远 exit 0 —— warning 不阻塞 CI
    return


if __name__ == "__main__":
    main()
