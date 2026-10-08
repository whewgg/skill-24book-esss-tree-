# -*- coding: utf-8 -*-
"""q.py — 已并入 tools/rag.py 的 `locate` 子命令，本文件只是转发薄壳。

为什么要并：原先 q.py 复制了 rag.py 的繁简转换、通称改写、语料遍历三套逻辑，
而且比 rag.py **少**了 RAG_VARIANTS 与 tools/aliases.md 两组变体——
同一个"检索入口"两套实现，导致两个入口召回不一致（2026-09-22 实测确认）。
现在实现只在 rag.py 里维护一份，老用法照旧可用：

    python tools/q.py 张居正      ≡    python tools/rag.py locate 张居正

彻底删除本文件前，需同步改掉 SKILL.md / references/maintenance.md /
tools/selftest.py 里对 q.py 的引用。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rag  # noqa: E402


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    return rag.cmd_locate(sys.argv[1:])


if __name__ == '__main__':
    sys.exit(main())
