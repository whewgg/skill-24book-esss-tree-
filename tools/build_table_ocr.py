# -*- coding: utf-8 -*-
r"""06-表卷OCR机读稿 生成器
================================================
对点校本扫描 PDF 的"表"册逐页做 OCR，产出**机器识别辅助稿**：
  · 每册一个 md，头部带醒目的"未校对"声明
  · 每页按竖排古籍阅读顺序还原（列自右向左，列内自上而下）
  · 逐页缓存（jsonl），支持断点续跑
  · 多进程并行

⚠ 本稿为机器识别结果，**含识别误差**，仅供检索定位；引用须核对 PDF 原件。

运行环境：需 pymupdf + rapidocr + **onnxruntime-directml**（本机为 AMD RX 7800 XT，走 DirectML；
      实测较 CPU 后端快 15–23 倍且输出一致。设环境变量 OCR_DML=0 可回退 CPU。）

用法：
  python build_table_ocr.py --pilot 162            # 只跑某一册（试跑）
  python build_table_ocr.py                        # 全量（默认 3 进程）
  python build_table_ocr.py --nos 162,224          # 指定若干册
"""
import argparse
import json
import multiprocessing as mp
import os
import re
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
from _paths import pdf_dir                                    # noqa: E402  库外原件目录（不写死）

# 点校本扫描件根目录：原件不在本库内，路径不写死——解析规则见 tools/_paths.py。
# 本脚本靠它读 PDF，故目录缺失时**不能静默继续**（见 load_manifest）。
PDF_DIR = pdf_dir()
OUTDIR = ROOT / '06-表卷OCR机读稿'
CACHE = ROOT / 'tools' / '_ocr_cache'
LOG = ROOT / 'tools' / '_ocr_progress.log'

_ENGINE = None
_DOCS = {}


def log(msg):
    line = '[%s] %s' % (time.strftime('%H:%M:%S'), msg)
    with LOG.open('a', encoding='utf-8') as f:
        f.write(line + '\n')
    print(line, flush=True)


# ---------------------------------------------------------------- 引擎

def get_engine():
    global _ENGINE
    if _ENGINE is None:
        from rapidocr import RapidOCR, OCRVersion, ModelType
        nth = int(os.environ.get('OCR_THREADS', '6'))
        use_dml = os.environ.get('OCR_DML', '1') != '0'
        # 实测选定：PP-OCRv5 server 检测 + PP-OCRv5 server 识别
        # （对照过 v6-small 检测/识别、chinese_cht v3 识别——后者误识最多，已弃）
        # 执行后端：DirectML（本机 AMD RX 7800 XT）——实测较 CPU 快 15–23 倍，
        # 且输出逐字一致；本机无 NVIDIA 卡，CUDA 不可用。设 OCR_DML=0 可回退 CPU。
        params = {
            'Det.ocr_version': OCRVersion.PPOCRV5,
            'Det.model_type': ModelType.SERVER,
            'Rec.ocr_version': OCRVersion.PPOCRV5,
            'Rec.model_type': ModelType.SERVER,
            'Rec.lang_type': 'ch',
            'EngineConfig.onnxruntime.intra_op_num_threads': nth,
            'EngineConfig.onnxruntime.inter_op_num_threads': 1,
            'EngineConfig.onnxruntime.use_dml': use_dml,
        }
        _ENGINE = RapidOCR(params=params)
    return _ENGINE


def get_page_image(pdf_no, page_no):
    """取该页的图像（优先原生嵌入图，避免放大失真）。返回 numpy 数组。"""
    import numpy as np
    import pymupdf
    from PIL import Image

    doc = _DOCS.get(pdf_no)
    if doc is None:
        doc = pymupdf.open(str(PDF_DIR / PDF_FILES[pdf_no]))
        if len(_DOCS) > 3:
            _DOCS.clear()
        _DOCS[pdf_no] = doc
    page = doc[page_no]

    img = None
    best_area = 0
    for xref, *_ in page.get_images(full=True):
        info = doc.extract_image(xref)
        area = info['width'] * info['height']
        if area > best_area:
            best_area = area
            img = Image.open(__import__('io').BytesIO(info['image']))
    if img is None or best_area < 400 * 600:
        pix = page.get_pixmap(dpi=200)
        img = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
    if page.rotation:
        img = img.rotate(-page.rotation, expand=True)
    if img.mode != 'RGB':
        img = img.convert('RGB')
    # 限制最大边，避免超大图拖慢
    if max(img.size) > 4000:
        r = 4000 / max(img.size)
        img = img.resize((int(img.width * r), int(img.height * r)), Image.LANCZOS)
    return np.array(img)


# ---------------------------------------------------------------- 阅读顺序

def order_boxes(items):
    """items: [[cx, cy, w, text, score], ...] → 按竖排列序分组的行列表。"""
    if not items:
        return []
    items = sorted(items, key=lambda b: -b[0])
    widths = [b[2] for b in items]
    tol = max(18.0, 0.85 * statistics.median(widths))
    cols, cur = [], [items[0]]
    for b in items[1:]:
        if abs(b[0] - cur[-1][0]) <= tol:
            cur.append(b)
        else:
            cols.append(cur)
            cur = [b]
    cols.append(cur)
    out = []
    for c in cols:
        c.sort(key=lambda b: b[1])
        out.append(' '.join(b[3] for b in c if b[3]))
    return [ln for ln in out if ln.strip()]


def worker(task):
    pdf_no, page_no = task
    try:
        import numpy as np
        eng = get_engine()
        arr = get_page_image(pdf_no, page_no)
        res = eng(arr)
        items = []
        if res is not None and res.txts is not None:
            for b, t, s in zip(res.boxes, res.txts, res.scores):
                xs = [p[0] for p in b]
                ys = [p[1] for p in b]
                w = max(xs) - min(xs)
                items.append([round(float(sum(xs)) / 4, 1), round(float(sum(ys)) / 4, 1),
                              round(float(w), 1), str(t), round(float(s), 3)])
        return pdf_no, page_no, items, None
    except Exception as e:
        return pdf_no, page_no, [], '%s: %s' % (type(e).__name__, e)


# ---------------------------------------------------------------- 组装

HEAD = """# {title}（OCR 辅助稿）

> ⚠️ **本文件为机器识别稿，未经校对，含识别误差。**
> 用途：**仅供检索定位**——找到关键词所在页码后，请回查 PDF 原件核对。
> 原件：`{pdfname}`（点校本扫描件册名；原件不在本库内，按册名自行对号取件）
> 识别引擎：PP-OCRv5 det(server) + PP-OCRv5 rec(server)；页码为 **PDF 物理页序**（从 1 起）。
> 排版：竖排古籍按"列自右向左、列内自上而下"还原，同一列内容以空格分隔。
> **凡引用，必核对原件。** 本稿不得作为引用出处。

---
"""


def assemble(pdf_no, meta):
    cache = CACHE / ('%s.jsonl' % pdf_no)
    if not cache.exists():
        return False
    pages = {}
    bad = []
    for ln in cache.read_text(encoding='utf-8').splitlines():
        if not ln.strip():
            continue
        r = json.loads(ln)
        pages[r['p']] = r
        if r.get('err'):
            bad.append(r['p'] + 1)
    if not pages:
        return False
    body = [HEAD.format(title=meta['title'], pdfname=meta['fn'])]
    for p in sorted(pages):
        body.append('### 第 %d 页' % (p + 1))
        lines = order_boxes(pages[p]['boxes'])
        if lines:
            body.extend(lines)
        else:
            body.append('（本页未识别到文字）')
        body.append('')
    if bad:
        body.append('> 识别异常页：%s' % ', '.join(str(b) for b in bad))
    OUTDIR.mkdir(exist_ok=True)
    fp = OUTDIR / ('%s_%s.md' % (pdf_no, meta['short']))
    fp.write_text('\n'.join(body), encoding='utf-8', newline='\n')
    return True


# ---------------------------------------------------------------- 册目

PDF_FILES = {}
META = {}


def load_manifest():
    """扫描点校本原件目录（见 `tools/_paths.pdf_dir()`，**不写死路径**）下的 PDF，取文件名含「表」者为表册。
    原先读 07-点校本PDF对照.md；该文件已并入 06，故改为直读目录，无外部依赖。"""
    BOOKS = ('史記', '漢書', '後漢書', '三國志', '晉書', '宋書', '南齊書', '梁書', '陳書', '魏書',
             '北齊書', '周書', '隋書', '南史', '北史', '舊唐書', '新唐書', '舊五代史', '新五代史',
             '宋史', '遼史', '金史', '元史', '明史', '清史稿')
    PDF_FILES.clear()
    META.clear()
    if PDF_DIR is None or not PDF_DIR.exists():
        log('未找到点校本原件目录——本脚本必须读 PDF 才能 OCR；'
            '请把原件放在本库旁的「二十四史」目录，或设环境变量 ERSHISI_PDF_DIR 后重跑')
        return []
    for fp in sorted(PDF_DIR.iterdir()):
        if '表' not in fp.name:
            continue
        if fp.suffix.lower() != '.pdf' and 'downloading' not in fp.name.lower():
            continue
        stem = re.sub(r'\.(PDF|pdf)(\.baiduyun\.p\.downloading)?$', '', fp.name, flags=re.I)
        segs = stem.split('·')
        m = re.match(r'.*?(\d{3})', segs[0])
        if not m:
            continue
        no = m.group(1)
        book = ''
        for s in segs:
            s2 = re.sub(r'[（(].*?[)）]', '', s)
            for b in BOOKS:
                if b in s2:
                    book = b
                    break
            if book:
                break
        if not book:
            continue
        vol = ''
        mv = re.search(r'第\s*([0-9〇零一二三四五六七八九十百]+)\s*冊', stem)
        if mv:
            vol = '第%s冊' % mv.group(1)
        juan = ''
        mr = re.search(r'(卷[^·（(]+)', stem)
        if mr:
            juan = mr.group(1).strip()
        PDF_FILES[no] = fp.name
        META[no] = {
            'fn': fp.name,
            'title': '%s %s %s' % (book, vol, juan),
            'short': '%s_%s' % (vol, juan.replace('/', '-')),
        }
    return list(PDF_FILES)


def main():
    ap = argparse.ArgumentParser()
    # 配比实测（史记002 密排页，热身法）：2进程×6线程=0.245s/页 最快；
    # 1进程=0.42s、3进程×5线程=0.265s、4进程×2线程=0.308s——DML 跨进程扩展性差，进程多反慢。
    ap.add_argument('--workers', type=int, default=2)
    ap.add_argument('--pilot', type=str, default='')
    ap.add_argument('--nos', type=str, default='')
    ap.add_argument('--limit', type=int, default=0)
    args = ap.parse_args()

    nos = load_manifest()
    if args.pilot:
        nos = [n for n in nos if n == args.pilot]
    elif args.nos:
        want = set(args.nos.split(','))
        nos = [n for n in nos if n in want]
    log('册数=%d 引擎=PP-OCRv5(server)det+rec 进程=%d' % (len(nos), args.workers))

    try:
        import pymupdf
    except ImportError:
        log('× 需要 pymupdf（读 PDF 页数与页面对象）。请先装：pip install pymupdf')
        return 1
    CACHE.mkdir(parents=True, exist_ok=True)
    tasks = []
    for no in nos:
        cache = CACHE / ('%s.jsonl' % no)
        done = set()
        if cache.exists():
            for ln in cache.read_text(encoding='utf-8').splitlines():
                if not ln.strip():
                    continue
                try:
                    r = json.loads(ln)
                except json.JSONDecodeError:
                    continue      # 上次被中断时写坏的末行：跳过，不要因此整个断点续跑崩掉
                if not r.get('err'):
                    done.add(r['p'])
        d = pymupdf.open(str(PDF_DIR / PDF_FILES[no]))
        n = d.page_count
        d.close()
        todo = [p for p in range(n) if p not in done]
        if args.limit:
            todo = todo[:args.limit]
        tasks += [(no, p) for p in todo]
        log('  %s %s 共%d页 待处理%d页' % (no, META[no]['title'], n, len(todo)))

    if not tasks:
        log('无待处理页，直接组装')
    t0 = time.time()
    done_n = 0
    bufs = {}
    pending = {}
    for no, _p in tasks:
        pending[no] = pending.get(no, 0) + 1
    if tasks:            # 没有待处理页就别起进程池——子进程的 _init 会加载 OCR 引擎，纯属浪费
        with mp.Pool(args.workers, initializer=_init) as pool:
            for pdf_no, page_no, items, err in pool.imap_unordered(worker, tasks, chunksize=2):
                bufs.setdefault(pdf_no, []).append(
                    {'p': page_no, 'boxes': items, 'err': err})
                pending[pdf_no] -= 1
                done_n += 1
                if len(bufs[pdf_no]) >= 20:
                    _flush(pdf_no, bufs[pdf_no])
                    bufs[pdf_no] = []
                if pending[pdf_no] <= 0:          # 本册完成 → 落盘并组装，便于分批取用
                    if bufs.get(pdf_no):
                        _flush(pdf_no, bufs[pdf_no])
                        bufs[pdf_no] = []
                    if assemble(pdf_no, META[pdf_no]):
                        log('  ✔ 组装完成 → 06-表卷OCR机读稿/%s_%s.md' % (
                            pdf_no, META[pdf_no]['short']))
                if done_n % 50 == 0 or done_n == len(tasks):
                    el = time.time() - t0
                    rate = el / max(done_n, 1)
                    log('进度 %d/%d  均速%.2fs/页  已用%.0f分钟  预计剩余%.0f分钟' % (
                        done_n, len(tasks), rate, el / 60,
                        (len(tasks) - done_n) * rate / 60))
    for no, rows in bufs.items():
        if rows:
            _flush(no, rows)
    # 兜底组装：某册的页**已全部缓存**时 tasks 里没有它的页，上面的循环不会触发组装
    # （旧版此时会打出"无待处理页，直接组装"，然后**什么也不做**——.md 永远补不出来）。
    for no in nos:
        out_md = OUTDIR / ('%s_%s.md' % (no, META[no]['short']))
        if not out_md.exists() and assemble(no, META[no]):
            log('  ✔ 补组装（页已缓存、稿缺失）→ %s' % out_md.name)
    log('全部完成，用时 %.0f 分钟' % ((time.time() - t0) / 60))
    return 0


def _init():
    # 子进程（Windows spawn 会重新导入本模块）需重建册目表与引擎
    if not PDF_FILES:
        load_manifest()
    get_engine()


def _flush(pdf_no, rows):
    with (CACHE / ('%s.jsonl' % pdf_no)).open('a', encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    mp.freeze_support()
    main()
