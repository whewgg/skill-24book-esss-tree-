# -*- coding: utf-8 -*-
r"""生成「06-表卷OCR机读稿（简体）」。

背景：点校本扫描件是**繁体**，OCR 辅助稿照录为繁体；而本库其余语料（data/）是**简体**。
用户按习惯用简体关键词搜表卷内容时会大面积落空（实测：搜「张居正」在辅助稿中零命中，
而稿中有「張居正」）。故生成一份**仅供检索**的简体版；引用仍须回查繁体原稿或 PDF 原件。

运行（需 zhconv，装在隔离 venv 里）：
  python tools/make_ocr_simplified.py
"""
import sys

from _lib import ROOT, bootstrap_zhconv  # noqa: E402

SRC = ROOT / '06-表卷OCR机读稿'
DST = ROOT / '06-表卷OCR机读稿（简体）'

# zhconv 走 _lib 的统一加载（库外路径只留一处）。缺了给一句能照做的提示。
convert = bootstrap_zhconv()
if convert is None:
    sys.exit('× 需要 zhconv（繁→简）。请用隔离 venv 的解释器跑本脚本，或 pip install zhconv。')

HEAD = """# {title}（OCR 辅助稿·简体检索版）

> ⚠️ 本文件是**繁体 OCR 辅助稿的简体检索版**，为便于用简体关键词检索而生成。
> **不可据以引用**：正文经繁→简转换，且原稿本身即机器识别结果（错率约 5–15%）。
> 检索到目标页码后，请回查 **繁体原稿**（`06-表卷OCR机读稿/`）与 **PDF 原件**核对。
> 页码为 PDF 物理页序（从 1 起），与繁体原稿一一对应。
> 繁体原稿：`06-表卷OCR机读稿/{fname}`

---
"""


def main():
    if not SRC.exists():
        print('无 OCR 辅助稿目录')
        return 1
    DST.mkdir(exist_ok=True)
    n_page = 0
    files = sorted(SRC.glob('*.md'))
    for fp in files:
        text = fp.read_text(encoding='utf-8')
        # 繁体原稿头部（到第一个页标为止）替换为简体版专用头部
        marker = '\n### 第 '
        i = text.find(marker)
        body = text[i + 1:] if i >= 0 else text
        title = convert(fp.stem, 'zh-hans')
        head = HEAD.format(title=title, fname=fp.name)
        body_s = convert(body, 'zh-hans')
        # 必须显式 newline='\n'：默认的 Windows 转换会把全库统一的 LF 变成 CRLF，
        # 与繁体原稿、data 语料不一致（跨机做逐字节比对就会假报警）。
        (DST / fp.name).write_text(head + body_s, encoding='utf-8', newline='\n')
        n_page += body.count('\n### 第 ') + 1      # body 起首就是「### 第 1 页」，count 会漏算第一页
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    print('生成 %d 个简体检索版文件，合计约 %d 页' % (len(files), n_page))
    return 0


if __name__ == '__main__':
    sys.exit(main())
