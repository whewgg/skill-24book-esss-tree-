# -*- coding: utf-8 -*-
"""_paths.py — 库外路径的唯一解析处。

本 skill 内部一律用**相对路径**（ROOT 由 __file__ 推导）：文档、索引头部与生成物
都不写盘符，整个 skill 目录可以整体搬移（`tools/selftest.py` 有 lint 守着这条）。

唯一的例外是「点校本扫描件」这一**库外**目录——原件是用户自己的资料，不在本库内、
随机器而变，所以既不能写死在文档里，也不能假定它就在某处。解析顺序：

  1. 环境变量 `ERSHISI_PDF_DIR`（换机器／换盘时设这一个就行）；
  2. 本库**旁边**或**上一级**的「二十四史」目录（把库与原件放一起即可整体搬移）；
  3. 都找不到 → 返回 `None`，**调用方必须降级而不是报错**。

不要在文档、索引头部或生成物里写这个目录的具体路径。
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def pdf_dir():
    """返回点校本扫描件根目录；找不到返回 None。

    逐个候选检查，**优先返回确实含 PDF 的那个**——否则「本库旁边恰好有个同名空目录」
    会把真正的原件目录挡掉，脚本静默拿到空集。都不含 PDF 时退回第一个存在的目录，
    由调用方降级（`check_tables.py` 会用上一版 06 的册名）。
    """
    cands = []
    env = os.environ.get('ERSHISI_PDF_DIR')
    if env:
        cands.append(Path(env))
    cands.append(ROOT.parent / '二十四史')
    cands.append(ROOT.parent.parent / '二十四史')
    first_dir = None
    for c in cands:
        try:
            if not c.is_dir():
                continue
            if first_dir is None:
                first_dir = c
            if any(f.suffix.lower() == '.pdf' for f in c.iterdir()):
                return c
        except (OSError, ValueError):
            # OSError：路径过长/非法字符/权限；ValueError：路径含 NUL 等
            continue
    return first_dir
