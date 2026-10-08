# -*- coding: utf-8 -*-
"""
rag.py — 本库检索增强（RAG）与引文核验

解决同一个问题：**模型凭记忆瞎编**。分四件事：

  1. 检索（query）  ：把一个问句里的关键词变成"证据块"——每块带《书名·卷次·篇名》、
                      文件路径:行号、以及**照录的原文**，模型只能抄，不能编。
                      同时给出「有据 / 无据」判定；无据时禁止凭记忆作答。
  2. 核验（verify） ：把已经写好的答案丢回来，逐条检查引文是否真在库里、
                      引文实际出自哪本书（抓「《宋书》vs《宋史》」这类错配）、
                      引的是文言原文还是白话译文。FAIL 即必须改。
  3. 体检（selftest）：内置断言，供 tools/selftest.py 调用。
  4. 统计（stat）   ：语料规模与书目。
  5. 定位（locate） ：只给「文件:行号」不给判定（原 tools/q.py 并入，见下）。

为什么不用向量模型（embedding）：
  本库是**文言正史**。人名（张居正/張居正）、年号（元嘉/元祐）、异体字（端木赐/端沐赐）
  上做嵌入，召回会漂移且**无法保证逐字引文**——而正史记述恰恰要求逐字照录。
  故本工具是"词法检索型 RAG"：**精确子串匹配为唯一召回依据**，繁简/异体扩展提召回，
  两级倒排（文件级过滤 → 段落级打分）提速度。零索引文件、零重建（语料一变即生效）。

用法：
  python tools\\rag.py 张居正
  python tools\\rag.py 张居正 考成法 --require-all
  python tools\\rag.py 居正 --book 24明史文白
  python tools\\rag.py 乌台诗案            # 后世通称 → 自动改按「逮赴台狱／御史狱」检索
  python tools\\rag.py 苏轼 --fast          # 索引先行：先定位书，再定向扫（约 0.1s）
  python tools\\rag.py 元嘉 --count
  python tools\\rag.py verify 答案.txt
  python tools\\rag.py verify -            # 从 stdin 读
  python tools\\rag.py stat
  python tools\\rag.py locate 张居正        # 只定位（原 tools/q.py，见 LOCATE_DOC）
  python tools\\rag.py 谜 "当并驱于中原"    # 人物谜题：锚度＋交集＋语料人名候选（猜不出＝答「无法确定」）
  python tools\\rag.py 对照 石勒 "当并驱于中原"  # 候选×线索逐条共现矩阵＋反证栏（✗＝排除依据）
  python tools\\rag.py selftest

选项：
  --book <关键词>     限定书/册（路径子串；逗号分隔多值＝任一命中即纳入，
                      写错的值会报错，不会静默缩窄范围）
  --fast              索引先行：先扫 02/03/04/06 索引定位该书、只扫该书文件夹
                      （人物/事件类查询首选；索引查不到时自动回退全库，不会漏证据）
  --source data|ocr|index|all   （默认 data；ocr＝表卷机读稿，index＝00–08 索引）
  --top N             输出证据块数（默认 8）
  --require-all       只输出包含**全部**关键词的段（AND 语义）
  --orig-only         只输出判定为「原文」的段（引用文言时的首选）
  --excerpt N         每块最多显示 N 字（默认全段，约 ≤420 字）
  --no-expand         关闭繁简/异体扩展
  --count             只报分布，不输出证据块
  --json              机读输出

输出约定：
  [E…] ★本传/★本纪  该段位于**篇名行**，本篇即此人的专传／此帝王的本纪（正文在同文件后续行）。
  # 📍 …       02/03 索引直取的「本传／字号」线索——找不到专传时先看这一行，
               不要去 01/05 里翻（那两份按卷题编，传主名不在里面）。
  # 💡 通称改写  后世通称（乌台诗案…）在本库无此词，已按 tools/aliases_events.md
               改按史文用词检索；**看到这一行就不要自行换词重试**。

退出码：query 0（无据也是 0，但会打印 STOP 提示）｜verify 有 FAIL 为 1｜selftest 通过为 0
"""
import bisect
import difflib
import json
import re
import sys
import time
from pathlib import Path

from _lib import ROOT, DATA, OCR_T, OCR_S, bootstrap_zhconv, cn2int  # noqa: E402

_zhc = bootstrap_zhconv()

# ---------------------------------------------------------------- 书目表
# 文件夹 → 书名。新增书必须同时改 00-目录索引.md
BOOKS = {
    '00资治通鉴': '资治通鉴', '01史记文白': '史记', '02汉书文白': '汉书',
    '03后汉书文白': '后汉书', '04三国志文白': '三国志', '05晋书文白': '晋书',
    '06宋书文白': '宋书', '07南齐书文白': '南齐书', '08梁书文白': '梁书',
    '09陈书文白': '陈书', '10魏书文白': '魏书', '11北齐书文白': '北齐书',
    '12周书文白': '周书', '13隋书文白': '隋书', '14南史文白': '南史',
    '15北史文白': '北史', '16旧唐书文白': '旧唐书', '17新唐书文白': '新唐书',
    '18旧五代史': '旧五代史', '19新五代史': '新五代史', '20宋史文白': '宋史',
    '21辽史': '辽史', '22金史文白': '金史', '23元史': '元史',
    '24明史文白': '明史', '25新元史文白': '新元史', '26清史稿文白': '清史稿',
}
BOOK_NAMES = set(BOOKS.values())

# 通鉴纪名 → 政权限定语（防止"宋纪"被讲成赵宋）
# ⚠ 与 00-目录索引.md「纪 → 卷范围 → 朝代」表、tools/pick_story.py 的 TZJ_JI 同源，
#    三处必须一起改；tools/selftest.py 会做一致性校验。
TZJ_JI = {
    '周纪': '东周', '秦纪': '秦', '汉纪': '西汉／东汉', '魏纪': '曹魏', '晋纪': '西晋／东晋',
    '宋纪': '刘宋', '齐纪': '南齐', '梁纪': '萧梁', '陈纪': '陈', '隋纪': '隋',
    '唐纪': '李唐', '后梁纪': '后梁（五代）', '后唐纪': '后唐（五代）',
    '后晋纪': '后晋（五代）', '后汉纪': '后汉（五代）', '后周纪': '后周（五代）',
}
# 本库未收录、但常被凭记忆引用的书——出现即判 FAIL
BANNED_BOOKS = {
    '续资治通鉴': '本库未收录（只收 27 部，见 00-目录索引.md）',
    '续资治通鉴长编': '本库未收录',
    '明通鉴': '本库未收录',
    '纲鉴易知录': '本库未收录',
    '通鉴纪事本末': '本库未收录',
    '宋史纪事本末': '本库未收录',
    '明史纪事本末': '本库未收录',
    '资治通鉴外纪': '本库未收录',
    '稽古录': '本库未收录',
    '文献通考': '本库未收录',
    '通典': '本库未收录',
    '通志': '本库未收录',
    '清史列传': '本库未收录',
    '明实录': '本库未收录',
    '清实录': '本库未收录',
    '东华录': '本库未收录',
    # 明清公案小说（写史稿件反复踩的坑）
    '海公案': '公案小说，非正史',
    '海公大红袍': '公案小说，非正史',
    '包公案': '公案小说，非正史',
    '龙图公案': '公案小说，非正史',
    '三侠五义': '小说，非正史',
}

# ---------------------------------------------------------------- 异体/通假扩展
# zhconv 处理繁简；下表处理 zhconv 不管的异体字与史书常见异写。
# 新增异体：直接在下表加一行（用 ｜ 分隔同组写法）；人物异文仍以 tools/aliases.md 为准。
RAG_VARIANTS = [
    '徵|征', '於|于', '濬|浚', '甯|宁', '谿|溪', '峯|峰', '羣|群',
    '棄|弃', '敎|教', '隂|阴|陰', '髙|高', '冊|册', '吳|吴', '曁|暨',
    '愍|闵|湣', '煬|炀', '濳|潜|潛', '禇|褚', '甦|苏|蘇',
    '曇|昙', '廵|巡', '尙|尚', '悅|悦', '冦|寇', '宼|寇',
    # 人名异写（库中写法与今人常用写法不同，是漏检高发区）
    '努尔哈赤|努尔哈齊|努尔哈齐|努爾哈赤|努爾哈齊',
    '成吉思汗|成吉斯汗|元太祖',
]

# ---------------------------------------------------------------- 事典通称 → 史文用词
# 后世/今人通称（乌台诗案、靖康之变…）在正史原文里往往**根本不出现**，
# 拿通称直接检索必然零命中，模型继而反复换词、每换一次全库重扫，几分钟就这么耗掉。
# 本表（tools/aliases_events.md，实时读取、免重建）让工具自动改按本库实际用词检索。
EVENT_ALIAS_FILE = ROOT / 'tools' / 'aliases_events.md'


def _load_event_aliases():
    """→ {通称: [史文用词, …]}；文件首词为通称，其余为本库用词。"""
    out = {}
    if not EVENT_ALIAS_FILE.exists():
        return out
    for line in EVENT_ALIAS_FILE.read_text('utf-8', errors='replace').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '｜' not in line:
            continue
        parts = [x.strip() for x in line.split('｜') if x.strip()]
        if len(parts) >= 2:
            out[parts[0]] = parts[1:]
    return out


EVENT_ALIASES = _load_event_aliases()


def event_terms(term):
    """term 是通称时返回其史文用词；否则 []。"""
    return list(EVENT_ALIASES.get(term, []))

# ---------------------------------------------------------------- 文言/白话判别
BAI_CHARS = set('的了这那们呢吗着很都就还也把被个我你他她它没')
WEN_CHARS = set('之乎者也矣焉哉乃遂亦皆毋弗勿曰其此斯乃')
_PUNCT = '，。、；：！？（）〔〕【】《》〈〉·…—～「」『』“”‘’"\'\'.,;:!?()[]{}<>'


_NORM_RE = re.compile(r'[\s%s]+' % re.escape(_PUNCT))


def norm(s):
    """去空白与标点，用于引文比对。"""
    return _NORM_RE.sub('', s)


def baihua_score(s):
    """>0 偏白话，<0 偏文言。"""
    t = norm(s)
    if len(t) < 4:
        return 0.0
    n = len(t)
    b = sum(1 for c in t if c in BAI_CHARS)
    w = sum(1 for c in t if c in WEN_CHARS)
    return (b / n) * 1.0 - (w / n) * 0.55


# ---------------------------------------------------------------- 语料遍历
def iter_corpus(source='data'):
    if source in ('data', 'all'):
        for p in sorted(DATA.rglob('*.md')):
            yield 'data', p
    if source in ('ocr', 'all'):
        for d, tag in ((OCR_S, 'ocr'), (OCR_T, 'ocr繁')):
            if d.is_dir():
                for p in sorted(d.glob('*.md')):
                    yield tag, p
    if source in ('index', 'all'):
        for p in sorted(ROOT.glob('*.md')):
            # SKILL.md 是指令不是数据；`_` 开头视为临时/私有文件（如审查报告），都不进检索。
            # ⚠ 根目录**其余** md 会进入 index 源——想放不被检索的东西，请加 `_` 前缀或放进子目录。
            if p.name != 'SKILL.md' and not p.name.startswith('_'):
                yield 'index', p


_ALIAS_FILE = ROOT / 'tools' / 'aliases.md'


def _load_alias_groups():
    """tools/aliases.md 的异文组，启动时读一次。

    原先是每次调用 expand_terms 都重读磁盘——实测 `retrieve()` 的段级循环里
    该函数被调上万次（13581 段＝13581 次读盘＋zhconv），是热路径上的纯浪费。
    """
    out = []
    if _ALIAS_FILE.exists():
        for line in _ALIAS_FILE.read_text('utf-8', errors='replace').splitlines():
            if line.startswith('#') or '｜' not in line:
                continue
            parts = set(x.strip() for x in line.split('｜') if x.strip())
            if parts:
                out.append(parts)
    return out


_ALIAS_GROUPS = _load_alias_groups()


def expand_terms(term, use_variants=True):
    """繁简 + 异体扩展，长的优先（避免短变体抢先命中）。"""
    if not use_variants:
        return [term]
    out = {term}
    if use_variants:
        for grp in RAG_VARIANTS:
            parts = grp.split('|')
            if term in parts:
                out |= set(parts)
        for parts in _ALIAS_GROUPS:
            if term in parts:
                out |= set(parts)
    if _zhc:
        for t0 in list(out):
            for tgt in ('zh-cn', 'zh-hant'):
                try:
                    v = _zhc(t0, tgt)
                except Exception:
                    continue
                if v:
                    out.add(v)
    return sorted(out, key=len, reverse=True)


# ---------------------------------------------------------------- 段落切分
MAX_CHUNK = 420
# 二级扫描复用一级读到的字节时，最多留这么多（防「的」这类超常用词把上千个文件全驻内存）
BYTES_BUDGET = 96 * 1024 * 1024
_HEAD_RE = re.compile(r'^(#+)\s*(.*)$')
_SPLIT_RE = re.compile(r'(?<=[。！？；])')


def split_long(line, limit):
    """单行过长时按句读切开，仍记同一行号。"""
    if len(line) <= limit * 2:
        return [line]
    parts, buf = [], ''
    for seg in _SPLIT_RE.split(line):
        buf += seg
        if len(buf) >= limit:
            parts.append(buf)
            buf = ''
    if buf.strip():
        parts.append(buf)
    return parts


def chunks_of(text):
    """返回 [(起行, 止行, 篇名, 段文本, 是否 > 引用块)]。
    切分规则：空行／标题／超长／**引用块与明文交界**处断开——
    文白对照卷的原文与译文常相邻无空行，不按此断开会把文言与白话混进同一段。"""
    out = []
    buf, start, end, head, isq = [], None, None, '', None
    cur_head = ''

    def flush():
        if not buf:
            return
        t = '\n'.join(buf)
        for piece in split_long(t, MAX_CHUNK):
            if piece.strip():
                out.append((start, end, cur_head, piece.strip(), bool(isq)))

    for i, raw in enumerate(text.split('\n'), 1):
        line = raw.rstrip('\r')
        if _is_marker(line):
            flush()
            buf, start, end, isq = [], None, None, None
            continue
        m = _HEAD_RE.match(line)
        if m:
            flush()
            buf, start, end, isq = [], None, None, None
            if m.group(1) == '##':
                cur_head = m.group(2).strip()
            elif m.group(1) == '#':
                h = m.group(2).strip()
                cur_head = h.split('·', 1)[1] if '·' in h else cur_head
            continue
        if not line.strip():
            flush()
            buf, start, end, isq = [], None, None, None
            continue
        q = line.lstrip().startswith('>')
        if buf and q != isq:
            flush()
            buf, start, end, isq = [], None, None, None
        if len(buf) >= 6 or sum(len(x) for x in buf) >= MAX_CHUNK:
            flush()
            buf, start, end, isq = [], None, None, None
        isq = q
        buf.append(line.strip())
        if start is None:
            start = i
        end = i
    flush()
    return out


# ---------------------------------------------------------------- 原文/译文判定
def _is_marker(line):
    """识别 【原文】/**【译文】** 之类的显式标记，返回 '原文'/'译文'/None。"""
    t = line.strip().lstrip('>').strip().strip('*').strip()
    if t in ('【原文】', '【譯文】', '【译文】'):
        return '原文' if '原' in t else '译文'
    return None


def _scores(text):
    """把文件里的段落分成 明文段 / > 引用段 / 译：段，各返回排序后的白话得分。"""
    plain, quote, tzj = [], [], False
    for raw in text.split('\n'):
        s = raw.strip()
        if not s or s.startswith('#') or _is_marker(s):
            continue
        body = s.lstrip('>').strip() if s.startswith('>') else s
        if body.startswith('译：') or body.startswith('譯：'):
            tzj = True
            body = body[2:].strip()
            if len(body) >= 12:
                quote.append(baihua_score(body))
            continue
        if len(body) >= 12 and not body.startswith('【'):
            (quote if s.startswith('>') else plain).append(baihua_score(body))
    return plain, quote, tzj


def file_mode(text):
    """文件级判模式，返回 (mode, 置信度 0–1, 文言/白话分界 mid)。

    mode:
      marker   文件用 【原文】/【译文】 显式标记（宋史等）——最可靠，依标记判
      tzj      通鉴补入卷：明文＝繁体原文，`译：` 行＝译文
      all_orig 无任何译文段（补入卷纯文言）
      normal   明文＝原文，`> ` 引用块＝译文（多数书）
      alt      `> ` 引用块＝原文，明文＝译文（部分书，须依内容判定）

    ⚠ 判据是**内容**（文言虚字 之于者也 vs 白话字 的了这那），不是 `> ` 符号——
      本库各书位置相反，只信符号必把译文当原文引用。"""
    plain, quote, tzj = _scores(text)
    n_marker = sum(1 for raw in text.split('\n') if _is_marker(raw))
    if n_marker >= 2:
        mid = 0.0
        if plain and quote:
            mp = sorted(plain)[len(plain) // 2]
            mq = sorted(quote)[len(quote) // 2]
            mid = (mp + mq) / 2
        return 'marker', 1.0, mid
    if tzj and not plain:
        return 'tzj', 0.95, 0.0
    if not quote:
        return 'all_orig', 0.9, 0.0
    if not plain:
        return 'alt', 0.8, 0.0
    mp = sorted(plain)[len(plain) // 2]
    mq = sorted(quote)[len(quote) // 2]
    mid = (mp + mq) / 2
    if mp > mq:                       # 明文更白话 → 原文在 > 块内
        return 'alt', min(1.0, 0.5 + abs(mp - mq) * 4), mid
    return 'normal', min(1.0, 0.5 + abs(mq - mp) * 4), mid


def chunks_labeled(text):
    """切段并逐段判原文/译文。返回 (mode, 置信, [(起, 止, 篇名, 文本, 判定)])。"""
    mode, conf, mid = file_mode(text)
    mks = [(i, _is_marker(raw)) for i, raw in enumerate(text.split('\n'), 1) if _is_marker(raw)]
    mls = [x[0] for x in mks]
    out = []
    for l1, l2, head, ch, _isq in chunks_of(text):
        pos = bisect.bisect_right(mls, l1) - 1
        cur = mks[pos][1] if pos >= 0 and mls[pos] <= l1 else None
        out.append((l1, l2, head, ch, chunk_kind(ch, mode, cur, mid)))
    return mode, conf, out


def chunk_kind(chunk, mode, marker, mid=0.0):
    """返回 '原文' / '译文' / '待核'。"""
    s = chunk.strip()
    if marker:
        return marker
    body = s.lstrip('>').strip()
    if body.startswith('译：') or body.startswith('譯：'):
        return '译文'
    if mode == 'all_orig':
        return '原文'
    if mode == 'tzj':
        return '原文'
    if mode == 'marker':
        sc = baihua_score(body)
        if abs(sc - mid) < 0.006 and len(norm(body)) < 20:
            return '待核'
        return '译文' if sc > mid else '原文'
    struct = ('译文' if s.startswith('>') else '原文') if mode == 'normal' \
        else ('原文' if s.startswith('>') else '译文')
    n = len(norm(body))
    if n < 8:
        return struct
    sc = baihua_score(body)
    content = '译文' if sc > mid else '原文'
    return struct if struct == content else '待核'



# ---------------------------------------------------------------- 定位与题名
_JUAN_RE = re.compile(r'卷[〇零一二三四五六七八九十百千0-9]+[上下]?')
# **帝王本纪的首段自称行**（身份锚点）。实测形态七种：
#   「太祖武皇帝，沛国谯人也，姓曹，讳操」   —— 史记／隋书式：…皇帝＋句读
#   「世祖光武皇帝讳秀，字文叔」             —— 后汉书／宋书式：…皇帝＋讳
#   「宣皇帝名懿，字仲达」                   —— 晋书式：…皇帝＋名
#   「孝景皇帝者，孝文之中子也」／「孝惠皇帝是高祖的太子」 —— …皇帝＋者（译文用「是」）
#   「齐高祖神武皇帝姓高氏，讳欢」           —— …皇帝＋**姓**（北史／旧唐书）
#   「孝昭皇帝演，字延安」「废帝海陵庶人亮」 —— …皇帝＋**直接本名**（北齐书／金史／旧唐书）
# ① 前缀字数下限必须是 **0**：「武皇帝讳炎」「景皇帝名师」这类单字前缀实测存在，
#    写成 {2,40} 会把整卷晋书帝纪漏掉（07 索引建表时踩过同一坑）。
_IDENT_RE = re.compile(r'^[^\s，。、；：「」『』（）()《》"\']{0,40}?(?:皇帝|帝者)'
                       r'(?:者)?(?:[，、名讳是姓]|([一-鿿]{1,5})(?=[，、字]))')
# **无「皇帝」二字**的本纪自称行：「高祖，沛丰邑中阳里人，姓刘氏」（史记／汉书）、
# 「吕太后者，高祖微时妃也」「卫绍王讳永济」「末帝，讳瑱」。号必须止于帝／后／王／主／公／侯／
# 祖／宗，且**不得含「的」**——白话卷首会写出「殷的始祖契，母亲名叫简狄」（实测 01 史记卷三），
# 放开「的」就会把「殷的始祖」当帝王号。这条只在**卷题确是本纪／帝纪**的文件上启用，
# 否则后妃纪、志、表里的「皇太后诏曰」全会被认成本纪开篇。
_IDENT2_RE = re.compile(r'^((?:(?!的)[一-鿿]){1,7}?(?:太后|皇后|帝|王|主|公|侯|祖|宗))'
                        r'(?:者)?(?:[，、名讳是姓字]|([一-鿿]{1,4})(?=[，、字]))')
_BENJI_HEAD_RE = re.compile(r'本纪|帝纪|帝史|纪第|[一-鿿]{1,4}纪[一二三四五六七八九十]{1,3}$')
_BENJI_BAD_RE = re.compile(r'皇后|后妃|外戚|列传|本传|志第|表第|序|叙|目录|载记|世家|外纪|论|赞')
# 身份行里的**名讳**与**姓氏**：本纪从不写帝王的连姓全名（「讳坚」而非「杨坚」），
# 所以按名检索必须先把这一行拆成可比的姓名候选，否则永远钉不住本纪。
# ⚠ 四条实测规则（与 build_emperor_index.py 同源）：
#   · 「名讳」是一个词（新元史卷七「名讳忽必烈」），要先吃掉，否则抽出「讳忽必烈」；
#   · 「名曰／名为／名叫」的后一字是虚词（史记卷一「名曰轩辕」、卷三白话「名叫简狄」）；
#   · **「名」后那个曰／为／叫是「可选」的**：文言「名重贵」「皇帝名治」「名溥仪」是通行
#     自称式，砍掉裸「名」会连真本名（溥仪／李治／蒙哥…11 个）一起砍掉——07 表键集也同步；
#     绳下类假名（「以刑名绳下，」＝动宾短语）用左邻守卫 (?<!刑) 单独拦（实测汉书卷九）；
#   · 捕获止于「字」之前，否则「讳道成字绍伯」会连名带字吞成「道成字绍伯」（南齐书卷一）。
_GIVEN_RE = re.compile(r'(?:名讳|讳|(?<!刑)名[曰为叫]?)([\u4e00-\u9fff]{1,8}?)(?=[，、。；\n字]|$)')
_BORN_RE = re.compile(r'(?:本名|本讳|初名|初讳|更讳|又讳|一名|小名|小字|小讳)'
                      r'([\u4e00-\u9fff]{1,8})(?=[，、。；\n]|$)')
_SURNAME_RE = re.compile(r'姓([\u4e00-\u9fff]{1,4})氏')
# ⚠ 第二个写法必须排除「氏」字：`姓([一-鿿]{1,2})(?=[，、])` 会把「姓杨氏，讳坚」里的
#   「杨氏」整个当姓，于是造出「杨氏坚」这种假名（实测 ident_names 单元核对）。
_SURNAME2_RE = re.compile(r'(?<!名)姓([^，、。氏\s]{1,2})(?=[，、])')
_FULLNAME_RE = re.compile(r'姓([\u4e00-\u9fff])名([\u4e00-\u9fff]{1,3})(?=[，、。；\n]|$)')
_STOP_GIVEN_FIRST = set('曰为是之其此的字名讳年号位')
_STOP_SUR = set('也其之者氏名字年月此是')
# 自称行里没有「姓」字时补的**国姓**（只列国姓唯一的书）。与 build_emperor_index.py 的
# GIVEN_SUR 同源：「宣皇帝名懿」「世祖光武皇帝讳秀」全行无姓，不补则「司马懿」「刘秀」查不到本纪。
# ⚠ 南史／北史／两五代史／元史／新元史／清史稿 不通配——一书跨多数，猜错姓＝造出假名。
BOOK_SUR = {
    '汉书': ('刘',), '后汉书': ('刘',), '三国志': ('曹',), '晋书': ('司马',),
    '宋书': ('刘',), '南齐书': ('萧',), '梁书': ('萧',), '陈书': ('陈',),
    '魏书': ('拓跋', '元'), '北齐书': ('高',), '周书': ('宇文',), '隋书': ('杨',),
    '旧唐书': ('李',), '新唐书': ('李',), '宋史': ('赵',), '辽史': ('耶律',),
    '金史': ('完颜',), '明史': ('朱',),
}


def ident_names(line, book=''):
    """本纪自称行**整行** → 可检索姓名候选集（**只收 ≥2 字**的连姓／双字名）。

    「高祖文皇帝，姓杨氏，讳坚」→ {杨坚}；「太祖武皇帝…姓曹，讳操」→ {曹操}；
    「宣皇帝讳懿，字仲达，河内温县孝敬里人，姓司马氏」→ {司马懿}；
    「孝景皇帝者，孝文之中子也」（无讳无姓）→ 空集。

    ⚠ 只看**自称行这一行**、且只取**行内第一个**「讳／名」：原先传整个 chunk（前 120 字），
      本纪首段常在同一行里追述先世后妃（「母曰庄圣太后，怯烈氏，讳唆鲁禾帖尼」＝元史卷三，
      那是皇后不是皇帝），把别人的名字当成本帝就会造出假锚点。
    ⚠ 单字本名（讳坚／讳操）**不单独成键**：「坚」两字要与姓拼成「杨坚」才算，
      否则任何以「坚」结尾的人名（王坚、刘坚）都会被 endswith 判成隋文帝。
    ⚠ 语料有脱字行（「讳，字德象」），捕获为空 → 自然不收。
    """
    head = line.strip()[:120]
    gm = _GIVEN_RE.search(head)
    givens = [x for x in ([gm.group(1)] if gm else []) + _BORN_RE.findall(head)
              if x and x[0] not in _STOP_GIVEN_FIRST]
    sur = [s for pat in (_SURNAME_RE, _SURNAME2_RE) for s in pat.findall(head)
           if s and s[0] not in _STOP_SUR]
    if not sur:
        sur = list(BOOK_SUR.get(book, ()))
    out = set()
    for s, n in _FULLNAME_RE.findall(head):      # 「姓高名欢」＝直接给出连姓全名
        out.add(s + n)
    for g in givens:
        if len(g) >= 2:
            out.add(g)
        for s in sur:
            out.add(s + g)
    return {x for x in out if len(x) >= 2}


def locate(path, text=None):
    """→ (tag, book, 限定语, 卷次, 篇名, 相对路径)

    text：该文件已读到的全文（可选）。给了就不再开文件——`retrieve()` 同一轮里已读过
    同一文件，这里再 open 一次纯属浪费（曾出现每个候选文件被读 3 遍）。
    """
    rel = path.relative_to(ROOT)
    parts = rel.parts
    if parts[0] == 'data':
        folder = parts[1]
        book = BOOKS.get(folder, folder)
        lim = ''
        if folder == '00资治通鉴' and len(parts) > 3:
            lim = TZJ_JI.get(parts[2], '')
        juan = ''
        m = _JUAN_RE.search(path.stem)
        if m:
            juan = m.group(0)
        else:
            juan = path.stem.split('_')[-1]
        head = ''
        if text is not None:
            for ln in text.split('\n', 6)[:6]:
                if ln.startswith('# '):
                    head = ln[2:].strip()
                    break
        else:
            try:
                with path.open(encoding='utf-8', errors='replace') as fh:
                    for _ in range(6):
                        ln = fh.readline()
                        if not ln:
                            break
                        if ln.startswith('# '):
                            head = ln[2:].strip()
                            break
            except OSError:
                pass
        pian = head.split('·', 1)[1] if '·' in head else ''
        pian = re.sub(r'（.*?）|\(.*?\)|译文|文白对照', '', pian).strip()
        return 'data', book, lim, juan, pian, str(rel)
    if parts[0].startswith('06-表卷OCR机读稿'):
        return 'ocr', '表卷OCR机读稿', '', path.stem.split('_')[0] + '册', '', str(rel)
    return 'index', path.stem, '', '', '', str(rel)


def looks_like_sentence(t):
    """粗判传入的是不是一整句问句（而非关键词）——用于给出可操作的提示。"""
    q = t.strip()
    if len(norm(q)) < 7:
        return False
    stop = ('为什么', '怎么', '如何', '是否', '真的', '吗', '多少', '什么', '哪', '介绍',
            '讲', '说', '有没', '是不是', '他', '她', '这', '那', '和', '与', '的')
    return any(s in q for s in stop) or ' ' in q


def citation(book, lim, juan, pian):
    seg = [book]
    if juan:
        seg.append(juan)
    if pian:
        seg.append(pian)
    s = '《' + '·'.join(seg) + '》'
    if lim:
        s += '（%s）' % lim
    return s


# ---------------------------------------------------------------- 索引直取（一次遍历）
BIO_INDEXES = ('03-人物出现索引.md', '02-人物字号索引.md')


def index_lookup(terms, indexes=None, want_books=True, want_bio=True):
    """**一次遍历**索引，同时产出「候选书文件夹」与「★本传／字号线索」。

    原先 `fast_books()` 扫 02/03/04/06、`bio_hint()` 又单独扫 02/03——同一次
    `--fast` 查询把 19.8 MiB 索引读两遍（其中 02/03 的 15.2 MiB 读两次）。
    合并后单次查询少读 ~15 MiB。本传/字号线索只在 02/03 上取（04/06 行格式不同）。

    返回 (books, hints)：books=书文件夹集合；hints=[(来源索引, 值, 命中词)]，
    顺序与旧 bio_hint 一致（03 在前、02 在后）。
    """
    if indexes is None:
        indexes = FAST_INDEXES if want_books else BIO_INDEXES
    vs = set(terms)
    for t in terms:
        vs.update(expand_terms(t))
    books = set()
    per_file = {n: [] for n in BIO_INDEXES if n in indexes}
    seen = set()
    for name in indexes:
        p = ROOT / name
        if not p.exists():
            continue
        bio_ok = want_bio and name in per_file
        for line in p.read_text('utf-8', errors='replace').splitlines():
            if not line or line.startswith(('#', '>')):
                continue
            key = line.split('｜', 1)[0]
            if key not in vs:
                continue
            if bio_ok:
                segs = line.split('｜')[1:]
                bios = [s.lstrip('★').strip() for s in segs if s.startswith('★')]
                if bios:
                    k = ('bio', key, '；'.join(bios))
                    if k not in seen:
                        seen.add(k)
                        per_file[name].append(('%s｜★本传' % name.replace('.md', ''),
                                               '；'.join(bios), key))
                for s in segs:
                    m = re.match(r'字[:：]\s*(.+)', s)
                    if m:
                        k = ('zi', key, m.group(1).strip())
                        if k not in seen:
                            seen.add(k)
                            per_file[name].append(('%s｜字号' % name.replace('.md', ''),
                                                   m.group(1).strip(), key))
            if want_books:
                for m2 in re.finditer(r'([0-9]{2}[^\｜；;]*?)\\', line):
                    bk = m2.group(1).strip('★ ')
                    if bk in BOOKS:
                        books.add(bk)
    hints = []
    for n in BIO_INDEXES:          # 保持旧 bio_hint 的输出顺序：03 先、02 后
        hints.extend(per_file.get(n, ()))
    return books, hints


# ---------------------------------------------------------------- 帝王本纪锚点（07）
# 07-帝王本纪索引.md 由 tools/build_emperor_index.py 生成，一行一卷帝纪：
#   `检索键｜朝代·号｜《书·卷·篇名》｜年号 …｜自称行摘｜路径［｜⚠标记］`
# 它是**「本名→本纪」的唯一总表**：02/03 人物索引按「X，字Y」的列传式首句抽名，历代帝王
# 一个都不进（本纪卷首只写「讳某」）。所以查「杨坚」「熙宁」这类问题**必须先读这张表**，
# 而不是拿本名去全库硬扫——实测 4408 文件全扫 1.2 秒起，且本纪通篇称「帝／太祖」，
# 连姓全名一次都不出现，逐字过滤根本捞不到本纪。
EMPEROR_INDEX = '07-帝王本纪索引.md'
_EM_CACHE = []


def _em_split(line):
    """一行 07 → dict（keys／label／cite／eras／ident／rel／flag），不是数据行返回 None。"""
    seg = line.split('｜')
    if len(seg) < 6 or seg[0].startswith(('#', '>')):
        return None
    keys = [k.strip() for k in seg[0].split('、') if k.strip()]
    eras = [e.strip() for e in re.sub(r'^年号\s*', '', seg[3]).split('、')
            if e.strip() and '未抽到' not in e]
    return {'keys': keys, 'label': seg[1], 'cite': seg[2], 'eras': eras,
            'ident': seg[4], 'rel': seg[5].strip(),
            'flag': seg[6].strip() if len(seg) > 6 else '',
            'book': (seg[5].split('/')[1] if seg[5].startswith('data/') else '')}


def emperor_rows():
    """读 07 全表（进程内缓存一次；文件不存在返回空表，不报错）。"""
    if not _EM_CACHE:
        p = ROOT / EMPEROR_INDEX
        if p.exists():
            for line in p.read_text('utf-8', errors='replace').splitlines():
                r = _em_split(line)
                if r:
                    _EM_CACHE.append(r)
    return _EM_CACHE


def emperor_hit(term, rows=None):
    """一个检索词 → 07 里键命中它的所有帝王行。

    判据**只认整键相等**，外加"键以词开头且词≥3字"（「宋太祖」→键「宋太祖」，
    「明英宗前纪」这类篇名键同理）。为什么不做双向子串：
      · 「太祖」两字会命中历朝几十卷，噪声大过信息；
      · 年号键（「熙宁」「太和」）必须整键相等，否则「元」字开头的词会串一大片。
    """
    term = term.strip()
    if len(term) < 2:
        return []
    rows = emperor_rows() if rows is None else rows
    out = []
    for r in rows:
        if any(k == term or (len(term) >= 3 and k.startswith(term)) for k in r['keys']):
            out.append(r)
    return out


def emperor_hits(terms, cap=6):
    """多关键词 → (取前 cap 行的列表, 命中总行数)。无命中返回 ([], 0)。

    多词时先取**交集**（「杨坚 开皇」＝隋书卷一那一行），交集空则退回第一个有命中的词。
    """
    rows = emperor_rows()
    per = [emperor_hit(t, rows) for t in terms]
    cands = per[0] if per else []
    for pr in per[1:]:
        if pr:
            inter = [r for r in cands if r in pr]
            cands = inter or pr
    if not cands:
        cands = next((pr for pr in per if pr), [])
    return cands[:cap], len(cands)


def emperor_via(r, terms):
    """这一行是靠哪一类键命中的：{'name'}／{'era'}／两者都有。

    ⚠ 只由**年号**命中时必须警告：年号往往跨帝沿用（「熙宁」始于神宗，而本卷写的是哲宗），
      年号命中＝定位到"那几年记在哪一卷"，**不等于**"这卷的皇帝就是始建年号的人"。
    """
    out = set()
    for t in terms:
        t = t.strip()
        if len(t) < 2:
            continue
        for k in r['keys']:
            if k == t or (len(t) >= 3 and k.startswith(t)):
                out.add('era' if k in r['eras'] else 'name')
    return out


def emperor_note(terms, prefix='# '):
    """query／locate 输出头部的帝王锚点提示行（无命中返回空列表）。"""
    rows, total = emperor_hits(terms)
    if not rows:
        return []
    out = [prefix + '⭐ 帝王本纪锚点（%s）：命中 %d 卷%s'
           % (EMPEROR_INDEX.replace('.md', ''), total,
              '，下面列前 %d 卷' % len(rows) if total > len(rows) else '')]
    era_only = False
    for r in rows:
        via = emperor_via(r, terms)
        if via == {'era'}:
            era_only = True
        out.append('%s   %s｜%s｜年号 %s%s' % (
            prefix, r['label'], r['cite'], '、'.join(r['eras']) or '—',
            '｜' + r['flag'] if r['flag'] else ''))
        out.append('%s     自称行：%s%s' % (prefix, r['ident'][:60],
                                            '（译＝卷首只有白话，禁止照录）'
                                            if r['ident'].startswith('译') else ''))
        out.append('%s     取文件：python tools\\rag.py <事件词> --book %s --orig-only'
                   % (prefix, r['book'] or '<?>'))
    out.append(prefix + '  ⚠ 庙号跨朝重复、年号跨帝通用；上表只作**定位**，'
                        '引用一律 Read 该文件 [原文] 段逐字照录（详见该表头部七条纪律）')
    if era_only:
        out.append(prefix + '  ⚠ 上面有行**仅由年号键命中**：年号常跨帝沿用（「熙宁」始于神宗，'
                            '命中的却是《宋史·哲宗》），不得据此判定始建年号之帝＝本卷之帝。')
    return out


# ---------------------------------------------------------------- 人物谜题（谜／对照）
# 「这是谁」的谜题**没有名字可查**（名字正是待猜项）——模型最容易在这里凭记忆脑补
# （实测翻车两次：石勒整段被安到拓跋珪头上、岳飞被硬凑成郭子仪后还偷改题面）。
# 两条命令把「候选发现 → 逐条验证」机械化，候选只来自语料原文：
#   谜   <线索词…>         锚度（哪条够独、哪条是软特征）＋交集卷＋命中段＋**从段落里回收的人名＝候选**
#   对照 <候选> <线索词…>   逐条共现矩阵（✔／✗／△）＋反证栏；✗＝该候选的排除依据
# ⛔ 没有候选能过全部硬锚时，正解＝**答「无法确定」**（用户明确要求：猜不出来就说不知道）。
#   两条命令的收尾都明写这句，防止"赌一个最像的"。
_KNOWN_NAMES = None


def known_names():
    """语料人名表 {人名: (03 有无 ★本传, 03 的卷数)}——取自 03-人物出现索引 首列。

    只给「谜」的**机械提候选**用：名字必须真出现在命中段（或 ±8 行邻域）里才算候选。
    候选来自原文，检索者的记忆一律不作候选来源——这是这套命令防瞎编的根本。
    """
    global _KNOWN_NAMES
    if _KNOWN_NAMES is None:
        names = {}
        p = ROOT / '03-人物出现索引.md'
        if p.exists():
            for line in p.read_text('utf-8', errors='replace').splitlines():
                if '｜' not in line or line.startswith(('#', '>')):
                    continue
                seg = line.split('｜')
                nm = seg[0].strip()
                if not (2 <= len(nm) <= 8 and all('\u4e00' <= c <= '\u9fff' for c in nm)):
                    continue
                m = re.match(r'(\d+)卷', seg[1].strip()) if len(seg) > 1 else None
                names.setdefault(nm, ('★' in line, int(m.group(1)) if m else 0))
        _KNOWN_NAMES = names
    return _KNOWN_NAMES


# 候选**频率闸**：03 的「卷数」≥ 此值 ⇒ 是「于是／司马／高兴」这类高频词被误收成条目
# （实测 3661／2065／1413 卷），它们靠近一切文字、不可能是谜面要猜的人，直接不做候选。
# 阈值 800 → 250（2026-09-24 实测）：真名上界诸葛亮 206／李世民 179／松山 141，
# 而「国事」以 797 卷**溜过了旧阈值**，在 `谜 任柱、赖文光…` 里以「★ 国事」顶进候选栏——
# 它是 `build_name_index.variants()` 把「练国事，字君豫」炸成右子串、又撞上「国」是姓的产物。
# 实测 03 分布：≥250 卷共 93 条、250–800 区间 68 条几乎全是这类词组；真名一条不损。
# ⚠ 只挡"候选生成"，不动 02/03 数据——「高兴」同时是元史真人有传，删数据会误伤（见
#   tools/name_blocklist.md 的取舍原则）；截短条目留在 02 是为全文召回，清洗只在候选侧做。
_CAND_MAX_FREQ = 250


def _positions(s, v):
    i = s.find(v)
    while i >= 0:
        yield i
        i = s.find(v, i + 1)


def _min_dist(win, clue_vs, name):
    """名字与任一线索词在窗口里的**最小字符距离**（名字不出现返回 None）。"""
    npos = list(_positions(win, name))
    if not npos:
        return None
    best = None
    for v in clue_vs:
        for k in _positions(win, v):
            d = min(abs(k - j) for j in npos)
            if best is None or d < best:
                best = d
    return best


def _covered(wins, short, long):
    """`short` 在窗口里的**每一次**出现都落在 `long` 的某次出现区间内吗？

    用于候选侧的"出现级折叠"：03 里「李瀚」「刘松」「松山」都是真人（各有本传、各有字），
    但谜面撞上的其实是「李瀚章」「刘松山」——`variants()` 的右子串炸开让截短形也立了目。
    只有当 short **从未独立出现**时才折叠掉，独立出现过就保留（宁漏不误）。
    """
    seen = False
    for w in wins:
        for i in _positions(w, short):
            seen = True
            if not any(k <= i and i + len(short) <= k + len(long)
                       for k in _positions(w, long)):
                return False
    return seen


def fold_candidates(cand, wins):
    """去掉"只是别人名字的截短形"的候选：[(名, …)] → [(名, …)]（实测 李瀚⊂李瀚章）。"""
    names = {c[0] for c in cand}
    out = []
    for c in cand:
        a = c[0]
        cover = [b for b in names if b != a and a in b
                 and (b.startswith(a) or b.endswith(a)) and _covered(wins, a, b)]
        if cover:
            continue
        out.append(c)
    return out


# 03 表（「谜」的候选来源）有**结构性盲区**：它按"传首有字"或"名入列传篇名"立目，
# 无字且附叙在他传里的人根本不在表内。实测一批太平天国头目（陈玉成／石达开／李秀成／
# 杨秀清／韦昌辉…）全库 10–160 处，03 **一条目都没有**；2026-09-24 已把 19 人登记
# `tools/extra_names.md` 并重跑 03（立目 13227→13246），**但口径没变——捻军一批
# （赖文光／张乐行／苗沛霖）与各省无名杂酋照样漏**，故本栏不能删。
# 后果不是"少一个候选"，而是**工具亲手制造假答案**：谜面「陈玉成弃蕲州窜陷广济」
# （独有引语级、全交集 1 文件）当时候选栏顶出的是「塔齐布（距 26 字）／彭玉麟／王洪」——
# 真答案印在同一段原文里，却因为不在 03 而进不了候选。
# 故补一路**弱候选**：命中段邻域里以**句首主语**位置出现的 2–4 字串，
# 再用"全库跨卷数"过滤（跨卷≥3 才算，挡掉「联合／贼众」这类偶发切词）。
# ⚠ 已知覆盖不全：**只有句首**——「凤台生员苗沛霖藉团聚众」里的苗沛霖在句中，冒不出来，
#   这种人只能靠 `locate <名> --first` 现扫（实测 93 处／32 卷量级的召回没问题）。
_SUBJ_BAD_FIRST = set('其贼军寇师官是乃时会给至既初比寻未大小诸于今彼所此司部曲众兵'
                      '若夫且虽然然而因之以谓曰云尔何安岂殆')
# 串内含这些字＝切到了句法尾巴上，不可能是人名（实测「石勒因为」「余皆」「国藩亲」）
_SUBJ_BAD_INSIDE = set('的了因是和与把被这那其之者也皆悉俱从在至为乃则就又再还')
# 串尾是这些单字＝名字被截短、后面接的是叙述动词（实测「陈玉成弃」「孔苌攻」）
# 2026-09-24 补 死清复定平灭／分肃：实测 ○ 栏冒出「寇死」（"…寇死无算"）、「江西肃清」
# （四字考语）、「向荣分（…分犯…）」「江西肃」——**长切被尾字闸挡下时，短切也会漏进来**，
# 故这些叙述动词必须逐字进表，不能只挡四字格。
_SUBJ_BAD_TAIL = set('攻伐击败战守降走奔窜杀擒虏围破陷弃夺取与亲疏长幼大小所著书云'
                     '等拒距遣命令死清复定平灭分肃')
# 弱候选的边界闸（顺序不能乱：先按 R3/R5 剔除坏切法，再算 R1，否则
# 「陈玉成」会被误判成「陈玉成弃」的截短而反被丢掉——实测踩过）：
#   R1 可延伸：串＋右邻字仍是人名/已在候选集 ⇒ 它是被切短的前缀，丢
#   R2 子串折叠：串是另一合格候选的真子串 ⇒ 丢（「玉成」⊂「陈玉成」）
#   R3/R5 禁虚字／禁动词尾
#   R6 尾缀虚字（2026-09-24 补）：串去掉**最后一个虚字**后已是 03 人名 ⇒ 它是"人名＋以/于…"
#     的粘连切法（实测「翁同书以」），丢——注意方向与 R1 相反，R1 只管右邻能延长的。
# 另设两道数值闸：首字须是 03 已用人名的首字（近似姓表，从数据现算）、
#   跨卷数落在 3–60（2026-09-24 由 600 收紧：弱候选真身实测 蒋霨远 7／陈玉成 32／石达开 37，
#   而「秋季七」169、「秋七月乙」124 这类时间切词正是从 60–600 这条缝里钻进来的）。
_SUBJ_MAX_SPREAD = 60
#   R7 人名＋叙述词（2026-09-24 补）：串的**某个前缀本身是 03 人名**、其后紧跟叙述副词
#     ⇒ 这是"已知人名＋将要/大怒/非常…"的粘连，不是新名字（实测「石勒将要」「石勒大怒」
#     「石勒非常」整批混进 ○ 栏）。只认这批高频叙述词开头，避免把「张小三」这类真名误杀。
_SUBJ_NARR_AFTER = set('将非大甚又复即辄亦尝果断皆悉尽独数频屡潜密阴素向欲始俄相共更')
_SUBJ_TAIL_FUNC = set('以之于而则且亦其在为与及又复皆悉俱从到被把者'
                      '然焉耳矣乎哉')
_SUBJ_TIME_RX = re.compile(r'^(?:[正一二三四五六七八九十冬腊]|闰)[月日]|'
                           r'[年岁时春秋冬]$', re.U)
# 句首主语**不等于人名**：实测同一栏里会浮出「饶州／龙骧将军／文武将士／暴风大雨」
# 这类地名、官名、集合名。故再加一道"非人"闸，并把整栏改名为**段内主语**、
# 明写"未必是人名"——宁可少认，也不让工具把地名当人推给模型（那才是新的瞎编源头）。
# 2026-09-24 补 官兵卒军众党贼寇境：实测「广西官（…）」以句首冒出来。
_SUBJ_NOTPERSON_RX = re.compile(
    r'[州郡县府路桥山水川原野坡岸津渡关隘园陵庙社稷寺塔井沟池官兵卒军众党贼寇境]$',
    re.U)
_SUBJ_TITLE_RX = re.compile(
    r'(?:将军|校尉|太守|刺史|丞相|司徒|司空|太尉|尚书|侍郎|郎中|大夫|令尹|侯王|王公|'
    r'将士|士卒|部曲|党与|徒众|军士|百姓|吏民|蛮夷|盗贼|贼众|官军|妻子|父母|兄弟|'
    r'风|雨|雷|霜|雪|日|月|星|云|气|色|声|音)$', re.U)


def _file_spread(needles):
    """一次全库扫描 → {串: 命中的文件数}（用于给弱候选算"跨卷强度"）。"""
    bs = {n: n.encode('utf-8') for n in needles}
    cnt = {n: 0 for n in needles}
    if not bs:
        return cnt
    for _tag, path in iter_corpus('data'):
        try:
            b = path.read_bytes()
        except OSError:
            continue
        for n, bb in bs.items():
            if bb in b:
                cnt[n] += 1
    return cnt


def seg_subjects(chunks, known, cap=6):
    """命中段（含 ±8 行邻域）里的**句首主语**串 → 弱候选 [(名, 段内次数, 跨卷数)]。

    只补 03 的盲区，不替它：**已知在 03 表里的名字一律不重复列**，弱候选一律带
    「03 未立目」标签，模型必须回读原文才能采信（见 cmd_riddle 的提示行）。
    """
    sur_first = {k[0] for k in known}
    hits, right = {}, {}
    for _p, _l1, _l2, kind, _ch, win in chunks:
        if kind != '原文':
            continue
        for s in re.split(r'[。；！？\n]', win or ''):
            s = _BRK_RE.sub('', _LEAD_RE.sub('', s)).strip().strip('，、')
            if len(s) < 5:
                continue
            m = re.match(r'^([一-鿿]{2,6})', s)
            if not m:
                continue
            run = m.group(1)
            for L in (2, 3, 4):
                nm = run[:L]
                if len(nm) < L or nm in known:
                    continue
                # R7：前缀已是 03 人名、后面接叙述副词 ⇒ "某人＋将要/大怒…" 的粘连，丢
                if any(nm[:k] in known and nm[k] in _SUBJ_NARR_AFTER
                       for k in (2, 3) if k < L):
                    continue
                if (nm[0] not in sur_first or nm[0] in _SUBJ_BAD_FIRST
                        or _SUBJ_TIME_RX.search(nm) or _SUBJ_TITLE_RX.search(nm)
                        or _SUBJ_NOTPERSON_RX.search(nm)
                        or set(nm) & _SUBJ_BAD_INSIDE or nm[-1] in _SUBJ_BAD_TAIL):
                    continue
                hits[nm] = hits.get(nm, 0) + 1
                c = run[L:L + 1]
                if c:
                    right.setdefault(nm, set()).add(c)
    keep = {n for n in hits
            if not any(n + c in known or n + c in hits for c in right.get(n, ()))
            and not (n[-1] in _SUBJ_TAIL_FUNC and n[:-1] in known)}
    spread = _file_spread(list(keep))
    out = [(n, hits[n], spread.get(n, 0)) for n in keep
           if 3 <= spread.get(n, 0) <= _SUBJ_MAX_SPREAD]
    out = [x for x in out if not any(y[0] != x[0] and x[0] in y[0] for y in out)]
    # 排序：先段内出现次数（真主语会在同段反复冒头），再**跨卷少者优先**——
    # 跨卷越宽越像常用词（实测旧写法按跨卷降序把「秋季七」169 卷顶到第一行）。
    out.sort(key=lambda x: (-x[1], x[2], x[0]))
    return out[:cap]


def _clue_files(terms):
    """一次全库字节扫描 → {线索词: 命中文件集合}（繁简/异体扩展后的任一写法即算命中）。"""
    tv = {t: [v.encode() for v in expand_terms(t)] for t in terms}
    sets = {t: set() for t in terms}
    for _tag, path in iter_corpus('data'):
        try:
            data = path.read_bytes()
        except OSError:
            continue
        for t, vs in tv.items():
            if any(v in data for v in vs):
                sets[t].add(path)
    return sets


def clue_level(n):
    """锚度分级：≤3 独有引语级｜≤11 窄｜<50 中｜≥50 宽（宽词＝软特征，不得当硬锚）。"""
    if n == 0:
        return '全库零命中（措辞可能有误）'
    if n <= 3:
        return '独有引语级'
    if n <= 11:
        return '窄'
    if n < 50:
        return '中'
    return '宽·软特征（不得当硬锚）'


# 共现/候选回收的邻域：命中段 ±8 行。为什么要放邻域——原文常以**单名简称**
# （「勒曰」）行文，连姓全名（「石勒」）多出现在紧邻的译文或上下文行（实测晋书卷105）。
_CO_WINDOW = 8


def _segments(text, clue_vs, cand_vs=None):
    """段级扫描：返回含线索的段 [(l1,l2,kind,ch,win)]。

    cand_vs 给了就只留「段内或 ±8 行邻域里同时出现候选名」的段（＝共现判据）。
    """
    lines = text.split('\n')
    _m, _c, labeled = chunks_labeled(text)
    out = []
    for l1, l2, _h, ch, kind in labeled:
        if not any(v in ch for v in clue_vs):
            continue
        lo = max(0, l1 - 1 - _CO_WINDOW)
        hi = min(len(lines), l2 + _CO_WINDOW)
        win = '\n'.join(lines[lo:hi])
        if cand_vs is not None and not any(v in win for v in cand_vs):
            continue
        out.append((l1, l2, kind, ch, win))
    return out


def riddle_core(terms, cap_files=8, cap_chunks=10):
    """「谜」核心 → dict(levels, inter, fallback, chunks, candidates, weak, folded, verdict)。

    candidates＝03 立目者（已过频率闸＋截短形折叠）；weak＝○ 栏"段内句首主语"
    （03 未立目者只能从这里冒出来）；folded＝被折掉的截短形，输出里如实交代。
    verdict='candidates'＝有候选；'none'＝**无候选，正解是「无法确定」**。
    """
    sets = _clue_files(terms)
    levels = [(t, len(sets[t]), clue_level(len(sets[t]))) for t in terms]
    inter = set.intersection(*sets.values()) if sets else set()
    fallback = False
    if not inter and len(terms) >= 3:
        cnt = {}
        for t in terms:
            for p in sets[t]:
                cnt[p] = cnt.get(p, 0) + 1
        fb = {p for p, c in cnt.items() if c >= len(terms) - 1}
        if fb:
            inter, fallback = fb, True
    tv = {t: expand_terms(t) for t in terms}
    allv = [v for vs in tv.values() for v in vs]

    def _cov(seg):
        return sum(1 for vs in tv.values() if any(v in seg[4] for v in vs))

    file_segs = {}
    for p in sorted(inter)[:cap_files]:
        try:
            text = p.read_bytes().decode('utf-8', 'replace')
        except OSError:
            continue
        file_segs[p] = _segments(text, allv)
    # 选段：① **每条线索先占一个代表段**——否则多线索谜题会只取到其中一处
    #    （实测「门臣祭酒+鹿死谁手」旧写法全取到制度那一段、漏掉引语那一段）；
    # ② 再按"覆盖线索数"补满剩余名额。
    picked, seen_keys = [], set()
    for t in terms:
        for p, segs in file_segs.items():
            hit = [s for s in segs if any(v in s[3] for v in tv[t])]
            if hit:
                best = max(hit, key=_cov)
                k = (p, best[0])
                if k not in seen_keys:
                    seen_keys.add(k)
                    picked.append((p,) + tuple(best))
                break
    rest = [( _cov(s), p, s) for p, segs in file_segs.items()
            for s in segs if (p, s[0]) not in seen_keys]
    rest.sort(key=lambda x: -x[0])
    for _c, p, s in rest:
        if len(picked) >= cap_chunks:
            break
        k = (p, s[0])
        if k not in seen_keys:
            seen_keys.add(k)
            picked.append((p,) + tuple(s))
    chunks = picked[:cap_chunks]
    names = known_names()
    wins = [w for _p, _l1, _l2, _k, _ch, w in chunks]
    # 硬锚（锚度 <50 的那几条）单独再量一次距离：并列时先按"贴硬锚"排，
    # 但**任何线索**的距离是第一序——旧实现把段内次数排在距离之前，实测（2026-09-24
    # 谜「轻佻 靳准 长安」）真答案「刘粲」距「轻佻」仅 2 字（原文「相国刘粲轻佻」＝
    # 线索直陈此人），却被压到第 10 位，头顶上排的是「司马模（段内 3·距 64 字）」
    # 「袁绍（距 134 字）」这类本卷常遇的隔句撞词。
    hardv = [v for t, n, _lv in levels if n < 50 for v in tv[t]]
    cand = []
    for nm, (star, freq) in names.items():
        if freq >= _CAND_MAX_FREQ:
            continue                    # 高频词误收条（于是／司马／国事…），见 _CAND_MAX_FREQ
        in_ch = 0
        best = None
        bestn = None
        for _p, _l1, _l2, _k, ch, win in chunks:
            if nm in ch:
                in_ch += 1
            d = _min_dist(win, allv, nm)
            if d is not None and (best is None or d < best):
                best = d
            if hardv:
                d2 = _min_dist(win, hardv, nm)
                if d2 is not None and (bestn is None or d2 < bestn):
                    bestn = d2
        if in_ch == 0 and best is None:
            continue
        cand.append((nm, in_ch, best if best is not None else 10 ** 6, star, freq,
                     bestn if bestn is not None else 10 ** 6))
    before = [c[0] for c in cand]
    cand = fold_candidates(cand, wins)       # 截短形折叠（李瀚→李瀚章）
    folded = [n for n in before if n not in {c[0] for c in cand}]
    # 排序：有硬锚时＝先"贴线索最近"，再"贴硬锚最近"，再段内次数、★；
    # 全是宽词（无硬锚）时沿用旧口径：先段内次数，再距离（「石勒/徐光」与八行外的
    # 「严震」分开靠的就是次数＋距离）。
    if hardv:
        cand.sort(key=lambda x: (x[2], x[5], -x[1], -int(x[3]), x[0]))
    else:
        cand.sort(key=lambda x: (-x[1], x[2], -int(x[3]), x[0]))
    # 弱候选：03 未立目、但确实以句首主语出现在命中段里的人（补 03 盲区，见 seg_subjects）
    weak = seg_subjects(chunks, names) if chunks else []
    # 截断必须**分段内／邻域各自截**：旧写法 `cand[:15]` 在按距离排序后会先把「段内 3·距 64 字」
    # 这类贴段真人挤出前 15，实测（2026-09-24 谜「轻佻 靳准 长安」）改完排序后 司马模／袁绍／
    # 卢志／范隆 四名从候选栏**整个消失**——真答案排到前面了，别的真人却被丢了，
    # 这比排序错更糟（漏候选会直接导致「无法确定」或凭记忆编）。
    _main = [c for c in cand if c[1]][:25]
    _aux = [c for c in cand if not c[1]][:8]
    return {'terms': terms, 'levels': levels, 'inter': sorted(inter), 'fallback': fallback,
            'chunks': chunks, 'candidates': _main + _aux, 'weak': weak, 'folded': folded,
            'verdict': 'candidates' if (cand or weak) else 'none'}


def _no_opts(cmd, argv):
    """「谜／对照」不接受任何选项：原先只把 `--xxx` 滤掉，**选项的取值却留在了线索里**
    （实测 `谜 轻佻 相国 --top 3` 把「3」当第三条线索，锚度 671 文件，交集与候选全被污染）。
    宁可报错——静默吞掉会让模型以为设置生效了。"""
    bad = [a for a in argv if a.startswith('--')]
    if bad:
        print('× %s 不接受选项（收到 %s）。%s' % (cmd, ' '.join(bad),
              '线索词一律当字面量用，无需选项。'))
        return True
    return False


def cmd_riddle(argv):
    if _no_opts('谜', argv):
        return 2
    terms = list(argv)
    if not terms:
        print('用法：python tools\\rag.py 谜 <线索词1> [线索词2 …]')
        print('  例：python tools\\rag.py 谜 "当并驱于中原"      # 独有引语一句定人')
        print('      python tools\\rag.py 谜 羯人 奴隶 门臣祭酒   # 多条线索找候选')
        return 2
    r = riddle_core(terms)
    print('# rag 谜：%d 条线索 → 候选（**全部来自语料原文**，不来自检索者记忆）' % len(terms))
    print('# ① 锚度（命中文件数＝这条线索有多"独"）：')
    for t, n, lv in r['levels']:
        print('#    「%s」%d 文件 · %s' % (t, n, lv))
    for ln in emperor_note(terms):
        print(ln)
    if r['chunks']:
        if r['fallback']:
            print('# ② 无全交集；退一步取"命中 %d/%d 条"的卷 %d 个，命中段如下：'
                  % (len(terms) - 1, len(terms), len(r['inter'])))
        else:
            print('# ② 全交集文件 %d 个；命中段如下（附 ±8 行邻域作候选回收范围）：' % len(r['inter']))
        allv = [v for t in terms for v in expand_terms(t)]
        for p, l1, l2, kind, ch, _win in r['chunks']:
            _tag, b, lim, juan, pian, rel = locate(p)
            print('#    %s %s:%d-%d [%s] %s'
                  % (citation(b, lim, juan, pian), rel, l1, l2, kind, _frag(ch, allv)))
    else:
        print('# ② 全交集文件 0 个——这些线索**无法同时落到任何一卷**。')
    print('# ③ 候选（段内含线索±8 行里**真实出现的人名**；★＝03 有本传；括号内 03 卷数＝这词'
          '在全库有多"常用"，越大越可能不是人名而是撞词；距＝到任一线索的最小字数，'
          '并列时再按"到硬锚的距离"与段内次数排）：')
    if r['candidates'] or r.get('weak'):
        if r['candidates']:
            main = [c for c in r['candidates'] if c[1]]
            aux = [c for c in r['candidates'] if not c[1]]
            for nm, in_ch, dist, star, freq, _distn in main:
                print('#    %s%s（段内 %d·距 %d 字·03 %d 卷）'
                      % ('★ ' if star else '  ', nm, in_ch, dist, freq))
            if aux:
                print('#    （备查·仅邻域出现：%s）'
                      % '、'.join('%s·%d卷' % (x[0], x[4]) for x in aux[:8]))
            if r.get('folded'):
                print('#    （已折叠的截短形：%s——它们各自也是 03 立目的真人，'
                      '但本段里那几次出现全部嵌在更长的名字里，故不重复列）'
                      % '、'.join(r['folded'][:8]))
        if r.get('weak'):
            print('#    —— 以下为**段内句首主语**（03 未立目者只能从这里冒出来；'
                  '**未必是人名**，也可能是漏网的地名／官名）——')
            for nm, c, sp in r['weak']:
                print('#    ○ %s（段内句首 %d 次·全库 %d 卷出现）' % (nm, c, sp))
            print('#    ⚠ 这一栏**不是候选结论**：先跑 '
                  '`python tools\\rag.py locate <名> --first` 定起读行、再 Read 原文判它是不是人；'
                  '确为重要而 03 漏收者，登记 `tools\\extra_names.md` 后重跑 03 索引。')
        print('# ⚠ 候选表来自 03，而 03 按"有字／名入篇名"立目——**无字又附叙在他传的人不在表内**'
              '（2026-09-24 实测：陈玉成／石达开／李秀成等 21 人全库 10–160 处而 03 零条目，'
              '已登记 `tools\\extra_names.md` 重跑入表；**立目口径没变，下一批无字附叙者照样漏**）。'
              '故**「不在候选」≠「不是答案」**，禁止因为某个名字没进候选就把它排除掉。')
        print('# ⚠ 候选≠答案：逐个跑 `python tools\\rag.py 对照 <名> <线索…>` 验证；'
              '**没有候选能过全部硬锚，就答「无法确定」**——不许赌一个"最像的"。')
    else:
        print('#    ⛔ 无候选（03 立目者一个没有，段内句首主语也没冒出来）。')
        print('# ⛔ **正解＝「无法确定」**：如实说明"本库线索不足以确定此人"'
              '（可列出哪几条线索没找到对应）；**禁止凭记忆猜**。')
    return 0


def contrast_core(cand, terms, cap_each=3):
    """「对照」核心 → dict(cand, cand_n, rows)；row.status ∈ hit(✔)／miss(✗)／void(△)。"""
    cv = expand_terms(cand)
    cb = [v.encode() for v in cv]
    tv = {t: expand_terms(t) for t in terms}
    tb = {t: [v.encode() for v in tv[t]] for t in terms}
    cand_n = 0
    clue_n = {t: 0 for t in terms}
    cof = {t: [] for t in terms}
    for _tag, path in iter_corpus('data'):
        try:
            data = path.read_bytes()
        except OSError:
            continue
        c = any(v in data for v in cb)
        if c:
            cand_n += 1
        for t in terms:
            if any(v in data for v in tb[t]):
                clue_n[t] += 1
                if c:
                    cof[t].append(path)
    rows = []
    for t in terms:
        segs = 0
        exs = []
        for p in cof[t]:
            try:
                text = p.read_bytes().decode('utf-8', 'replace')
            except OSError:
                continue
            for l1, l2, kind, ch, _win in _segments(text, tv[t], cv):
                segs += 1
                if len(exs) < cap_each:
                    _tag, b, lim, juan, pian, rel = locate(p, text)
                    exs.append((citation(b, lim, juan, pian), rel, l1, kind,
                                _frag(ch, tv[t] + cv)))
        st = 'void' if clue_n[t] == 0 else ('miss' if segs == 0 else 'hit')
        rows.append({'clue': t, 'status': st, 'segs': segs, 'co_files': len(cof[t]),
                     'clue_n': clue_n[t], 'examples': exs})
    return {'cand': cand, 'cand_n': cand_n, 'rows': rows}


def cmd_contrast(argv):
    if len(argv) < 2:
        print('用法：python tools\\rag.py 对照 <候选名> <线索词1> [线索词2 …]')
        print('  例：python tools\\rag.py 对照 石勒 "当并驱于中原"')
        print('  逐条给出共现 ✔／✗／△ 与出处；✗（硬锚对不上）＝该候选的排除依据。')
        return 2
    if _no_opts('对照', argv):
        return 2
    cand, terms = argv[0], list(argv[1:])
    if not terms:
        print('× 至少要给一条线索词：python tools\\rag.py 对照 <候选名> <线索词…>')
        return 2
    r = contrast_core(cand, terms)
    print('# rag 对照：「%s」× %d 条线索（共现＝同段或 ±8 行邻域内两词同时出现）'
          % (cand, len(terms)))
    if r['cand_n']:
        print('# 候选命中 %d 文件' % r['cand_n'])
    else:
        print('# ⛔ 候选「%s」全库零命中：名字写法可能有误，或本库无此人——先核对名字。' % cand)
    n_miss = n_void = 0
    for i, row in enumerate(r['rows'], 1):
        t = row['clue']
        if row['status'] == 'hit':
            print('[%d] ✔ 「%s」共现 %d 段（线索全库 %d 文件）' % (i, t, row['segs'], row['clue_n']))
            for cite, rel, l1, kind, ex in row['examples']:
                print('      %s %s:%d [%s] %s' % (cite, rel, l1, kind, ex))
        elif row['status'] == 'miss':
            n_miss += 1
            print('[%d] ✗ 「%s」共现 0 段（候选 %d 文件／线索 %d 文件，无同段或邻域）'
                  '——**若该条是硬锚，即排除该候选**' % (i, t, r['cand_n'], row['clue_n']))
        else:
            n_void += 1
            print('[%d] △ 「%s」全库零命中——题面措辞可能有误，不得当硬锚' % (i, t))
    print('# 反证栏：✗ %d 条、△ %d 条（对不上必须写出来；全打勾≠真核对）' % (n_miss, n_void))
    if n_miss or n_void:
        print('# 结论建议：有 ✗／△⇒**先排除「%s」**（除非对应线索确非硬锚）；'
              '若所有候选都过不了全部硬锚，**答「无法确定」**。' % cand)
    else:
        print('# 结论建议：全部线索共现 ✔——「%s」通过；仍须与其他候选比，再过 verify。' % cand)
    return 0


# ---------------------------------------------------------------- 检索
class Evidence:
    __slots__ = ('score', 'tag', 'book', 'lim', 'juan', 'pian', 'rel', 'l1', 'l2', 'kind',
                 'text', 'cover', 'bio')

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))


def retrieve(terms, source='data', book_filter=None, top=8, require_all=False,
             orig_only=False, use_variants=True, want_count=False):
    """两级检索：① 文件级过滤＋统计 df（算 IDF）② 段级打分取 top。"""
    # 每个原始关键词只展开一次。原先 4 处调用点里有两处在逐段循环体内，
    # 实测 20宋史文白 的 13581 段会触发 13581 次重算（读盘＋21 组变体＋zhconv）。
    _TV = {t: list(dict.fromkeys(v for alias in [t] + event_terms(t)
                                for v in expand_terms(alias, use_variants))) for t in terms}
    all_terms = []
    for t in terms:
        for v in _TV[t]:
            if v not in all_terms:
                all_terms.append(v)

    stat = {'scanned': 0, 'chars': 0, 'cand_files': 0,
            'term_df': {}, 'term_hits': {}, 'missing': [],
            'book_scope': {}}   # --book 每个值各自纳入了多少文件（抓"多值里某个写错"）
    t_start = time.time()

    # ---- 第一级：文件级
    if isinstance(book_filter, str):
        book_filters = [book_filter] if book_filter else []
    else:
        book_filters = [b for b in (book_filter or []) if b]
    cands = []
    ocr_done = set()
    kept = 0
    for tag, path in iter_corpus(source):
        rel_ = str(path.relative_to(ROOT))
        if book_filters:
            _mb = [b for b in book_filters if b in rel_]
            if not _mb:
                continue
            for b in _mb:      # 一个文件可能同时命中多个 --book 值，分别计数
                stat['book_scope'][b] = stat['book_scope'].get(b, 0) + 1
        try:
            b = path.read_bytes()
        except OSError:
            continue
        stat['scanned'] += 1
        stat['chars'] += len(b)
        present = []
        for i, t in enumerate(all_terms):
            tb = t.encode('utf-8')
            if tb in b:
                present.append(i)
                stat['term_hits'][i] = stat['term_hits'].get(i, 0) + b.count(tb)
        if present:
            # 表卷机读稿繁简两版内容同一册：同册只留先命中的一份（简体版优先）
            if tag.startswith('ocr'):
                st = path.stem
                if st in ocr_done:
                    continue
                ocr_done.add(st)
            for i in present:
                stat['term_df'][i] = stat['term_df'].get(i, 0) + 1
            # 顺手把已读字节留给二级扫描（预算内），省掉候选文件的第二次读盘
            if kept + len(b) <= BYTES_BUDGET:
                kept += len(b)
                cands.append((tag, path, present, b))
            else:
                cands.append((tag, path, present, None))

    # ⛔ **本纪文件的逐字过滤兜底**：本纪通篇称「帝／太祖／上」，连姓全名常常一次都不出现
    #    （实测 04 三国志卷一全文无「曹操」三字、05 晋书卷三无「司马炎」、20 宋史卷一只在
    #     叙述先世时出现「赵匡胤」的上祖名），第一级按字节筛文件会把整卷本纪关在门外——
    #     身份行锚点写得再对也没有用。这里按 07 表把命中的本纪文件补进候选：
    #     `present` 留空＝这一卷只有**身份行**那一段能入选（段级 `is_ident_line` 判），
    #     不会把该卷其它正文伪装成"命中"。
    if source in ('data', 'all'):
        e_rows, _n = emperor_hits(terms)
        if e_rows:
            have = {str(p0.relative_to(ROOT)).replace('\\', '/') for _t0, p0, _pr, _r0 in cands}
            for r in e_rows:
                rel0 = r['rel']
                if rel0 in have or '\\' in rel0:
                    continue
                if book_filters and not any(bf in rel0 for bf in book_filters):
                    continue
                fp = ROOT / rel0
                try:
                    bb = fp.read_bytes()
                except OSError:
                    continue
                cands.append(('data', fp, [], bb))
                have.add(rel0)

    # 原始关键词（不是变体）是否命中
    for ti, t in enumerate(terms):
        vs = _TV[t]
        idxs = [all_terms.index(v) for v in vs if v in all_terms]
        if not any(i in stat['term_df'] for i in idxs):
            stat['missing'].append(t)

    n_files = max(1, stat['scanned'])

    def idf(i):
        df = stat['term_df'].get(i, 0)
        return max(0.0, (n_files / (1.0 + df))) ** 0.5

    stat['cand_files'] = len(cands)

    if want_count:
        rows = []
        for i, t in enumerate(all_terms):
            if i in stat['term_df']:
                rows.append((t, stat['term_df'][i], stat['term_hits'].get(i, 0)))
        rows.sort(key=lambda x: -x[1])
        stat['rows'] = rows
        stat['elapsed'] = time.time() - t_start
        return [], stat

    # ---- 第二级：段级
    ev, seen = [], set()
    for tag, path, present, raw in cands:
        if raw is None:
            try:
                text = path.read_bytes().decode('utf-8', 'replace')
            except OSError:
                continue
        else:
            text = raw.decode('utf-8', 'replace')
        mode, conf, labeled = chunks_labeled(text)
        _tag, b, lim, juan, pian, rel = locate(path, text)
        # 篇名行（本传/本纪）探测：仅在带 `# ` 卷题、且卷题是传/纪的**传记类文件**上生效。
        # 三个坑都在这儿避掉：
        #   · 通鉴等无标题书首行是编年题（「晉紀三十二」）→ 无 `# ` 标题，直接跳过；
        #   · 志/表类（卷题含"志/表"）不参与，免得把「艺文一」当成某人本传；
        #   · 只认紧邻卷题的**第一个**短行（篇名行），不能取"若干短行构成的区间"——
        #     否则本纪/表的正文首段会被圈进区间，整段误标成本传（实测踩过）。
        src_lines = text.split('\n')
        head_i = next((i for i in range(min(6, len(src_lines)))
                       if src_lines[i].startswith('# ')), None)
        bio_line, bio_kind, head_txt = 0, None, ''
        if head_i is not None:
            heading = src_lines[head_i]
            head_txt = heading
            if '列传' in heading or '传第' in heading:
                bio_kind = '本传'
            elif '本纪' in heading or '纪第' in heading:
                bio_kind = '本纪'
            if bio_kind:
                for i in range(head_i + 1, min(head_i + 9, len(src_lines))):
                    s = src_lines[i].strip()
                    if not s:
                        continue
                    if len(s) <= 40 and not s.startswith(('>', '#')):
                        bio_line = i + 1
                    break
        # 帝王本纪的**首段自称行**（「太祖武皇帝，沛国谯人也，姓曹，讳操」／「孝景皇帝者，孝文之中子也」）。
        # ⚠ 本纪的**篇名行只写庙号**（宋史卷一 篇名行＝「太祖一」），帝王**本名既不在篇名行、
        #   也不进 02/03 人物索引**（那两份按「X，字Y」的列传式首句抽名）。于是按帝名检索时，
        #   光靠篇名行永远钉不住本纪，命中的全是臣僚传里顺带提到的句子（实测「赵匡胤」top 3
        #   全在《通鉴》后周纪、「司马炎」全在通鉴魏纪）。这一行是本纪的**唯一通用锚点**。
        # 只在**开头 3 段**里找：列传正文里也会出现「太宗皇帝尝……」这种段首句，但那是第 N 段，
        # 而本纪的自称行必然紧接卷题。译文行（汉书部分卷只有白话在"原文位"）同样认，标 `[待核]`。
        # ⚠ 行首要剥**成串的**「＞」「*」「【原文】」：`strip('>')` 只剥得开一层，
        #    宋史光宗／宁宗那种「＞ ＞ 光宗…皇帝，讳惇」嵌套引用会整卷漏掉锚点（实测）。
        benji = bool(_BENJI_HEAD_RE.search((juan or '') + ' ' + (pian or ''))) and \
            not _BENJI_BAD_RE.search((pian or '') + ' ' + head_txt)
        ident_line, ident_names_ = 0, set()
        for _l1, _l2, _h, ch0, kind0 in labeled[:3]:
            if kind0 not in ('原文', '待核'):
                continue
            for _ln in ch0.split('\n'):
                s = re.sub(r'^[\s>＞*　·|]+', '', _ln).strip()
                s = re.sub(r'^【[^】]{0,6}】', '', s).strip()
                ok = bool(_IDENT_RE.match(s))
                if not ok and (benji or bio_kind == '本纪'):
                    m2 = _IDENT2_RE.match(s)
                    # 「号」里带「为」＝封废记事而非自称行（南齐书卷二「封宋帝为汝阴王」、
                    # 梁书卷二「封齐帝为巴陵王」）。与 build_emperor_index.py 的同一道闸同源。
                    # ⚠ 两个捕获组都要查：废号在 **group(2)**（g1「封宋帝」干净、g2「为汝阴王」带为），
                    #   只查 g1 会把这句当自称行、给出「汝阴王 → ★本纪《南齐书·卷二》」的假锚点。
                    ok = bool(m2) and '为' not in m2.group(1) and '为' not in (m2.group(2) or '')
                if ok:
                    ident_line, ident_names_ = _l1, ident_names(s, b)
                    break
            if ident_line:
                break
        bio_txt = src_lines[bio_line - 1] if bio_line else ''
        # 本纪身份行的**姓名等价集**（检索词「杨坚」↔ 自称行「姓杨氏，讳坚」）：一卷算一次。
        # 只有这一行允许走等价匹配——正文别处出现「坚」字不构成本纪锚点。
        name_cov = {t for t in terms
                    if ident_line and ident_names_ and any(
                        len(v) >= 2 and (v == g or v.endswith(g) or g.endswith(v))
                        for v in _TV[t] for g in ident_names_)}
        for l1, l2, head, ch, kind in labeled:
            p = [i for i in present if all_terms[i] in ch]
            # ⛔ 本纪身份行要**先过门再判分**：本纪只写「姓杨氏，讳坚」，**从不写连姓全名**，
            #   所以检索「杨坚」时那一段的 `p` 是空的——旧写法 `if not p: continue` 直接把
            #   身份行丢在门外，后面 `hit_ident` 的放宽永远轮不到（实测「杨坚」查不到隋书卷一、
            #   「刘恒」查不到汉书卷四）。这里给身份行单独开一条入口：姓名等价（name_cov）或
            #   检索词逐字落在这一行里，都算命中身份行。
            is_ident_line = (bool(ident_line) and l1 == ident_line
                             and (bool(name_cov) or bool(p)))
            if not p and not is_ident_line:
                continue
            if tag.startswith('ocr'):
                kind = 'OCR稿'          # 机器识别结果，永不当作"原文"
                head = ''               # 机读稿的 ## 小标题不是篇名
            elif tag == 'index':
                kind = '索引'           # 索引行不是原文
            pn = pian or head
            # require_all 按"原始关键词"判：每个原始词至少一个变体落在段内。
            # ⚠ 身份行那一格例外：本纪写「姓杨氏，讳坚」，检索词「杨坚」不可能逐字落在里面，
            #   姓名等价（name_cov）在此处计为覆盖，否则 --require-all 会把本纪开篇筛掉。
            if require_all:
                if not all(any(v in ch for v in _TV[t])
                           or (is_ident_line and t in name_cov) for t in terms):
                    continue
            if orig_only and kind != '原文':
                continue
            cover = len({ti for ti, t in enumerate(terms)
                         if any(v in ch for v in _TV[t])
                         or (is_ident_line and t in name_cov)})
            cnt = sum(ch.count(all_terms[i]) for i in p)
            score = cover * 1000 + sum(idf(i) for i in p) * 12 + min(cnt, 6) * 4
            score -= len(ch) * 0.25          # 同覆盖度下，段落越紧凑越相关
            if kind == '原文':
                score += 120                 # 引用要抄原文：同覆盖度时原文优先
            if kind == '待核':
                score -= 40
            if p and len(p) > 1:
                firsts = [ch.find(all_terms[i]) for i in p]
                if max(firsts) - min(firsts) <= 80:
                    score += 40
            if head:
                score += 6
            # 篇名行／首段自称行命中：检索词**出现在这两行里** ⇒ 这就是此人的专传／此帝王的
            # 本纪，必须置顶。（宋史卷 338 的篇名行是「苏轼（子过）」——只有 4 字，若不加权会被
            #   胡三省注里顺带提到的长段压到第 3 位，模型据此误判"没有苏轼的专传"。）
            # ⚠ 判据必须是"词在**这一行**"，不能只看"段起点＝篇名行"：篇名行那一段常连着正文
            #   首段（实测 E1 覆盖 2–4 行），于是臣僚传正文里提到的帝王名会被误标 ★本传——
            #   检索「杨坚」拿到「★本传《隋书·卷六十》＝崔仲方传」、检索「拓跋焘」拿到
            #   「★本传《宋书·卷八十八》＝薛安都传」，这种假锚点比没锚点更害人。
            # ⚠ 身份行的命中判据要额外放宽一层：本纪只写「姓杨氏，讳坚」，**从不写连姓全名**，
            #   所以「杨坚」在这一行里逐字找是找不到的（实测）。改用 ident_names() 拆出的
            #   名／姓＋名 作比对；仍要求词落在这**一行**内，不会波及其他段。
            hit_bio = any(v in bio_txt for t in terms for v in _TV[t])
            hit_ident = is_ident_line          # 已含"词在这一行"＋姓名等价两判
            is_bio = bool(bio_line) and l1 == bio_line and hit_bio
            is_ident = (not is_bio) and hit_ident
            if is_bio or is_ident:
                score += 900
            # ★ 标什么种类：篇名行命中沿用卷题判出的「本传／本纪」；身份行命中时卷题可能压根
            # 没有（三国志「魏书一」不含"纪"字），按路径/篇名兜底判，别把载记说成本纪。
            star_kind = bio_kind if is_bio else None
            if is_ident:
                star_kind = (bio_kind or ('载记' if '载记' in (pian + rel)
                                          else '本纪'))
            key = (rel, l1, l2)
            if key in seen:
                continue
            seen.add(key)
            ev.append(Evidence(score=score, tag=tag, book=b, lim=lim, juan=juan, pian=pn,
                               rel=rel, l1=l1, l2=l2, kind=kind, text=ch, cover=cover,
                               bio=star_kind))

    ev.sort(key=lambda e: -e.score)
    stat['elapsed'] = time.time() - t_start
    # 截断前的总块数：调用方（和 `--top 8` 的读者）必须知道**后面还有多少没显示**，
    # 否则"没看到"会被当成"库里没有"——猜人物时就是假排除。
    stat['total_ev'] = len(ev)
    return ev[:top] if top else ev, stat


def hit_window(txt, terms, ex, use_variants=True):
    """`--excerpt` 的开窗口径：以**关键词所在位置**为中心切窗，不从段首截。

    从段首截会把关键词连同主语一起切掉——实测 `轻佻 --top 0 --excerpt 44`：
    104 块里约六成显示出来的片段里根本没有「轻佻」二字，"穷举"于是等于白跑。
    前 40% 留给关键词**之前**的文字，因为「谁轻佻」的主语多半在关键词前面
    （「帝以辩轻佻无威仪」「相国轻佻」「惇谓其轻佻不可以君天下」）。
    """
    if not ex or ex >= len(txt):
        return txt
    pos = -1
    for t in terms:
        for v in expand_terms(t, use_variants):
            i = txt.find(v)
            if i >= 0 and (pos < 0 or i < pos):
                pos = i
    if pos < 0:                      # 篇名行／本纪开篇锚点：段内本就无词，只能给段首
        return txt[:ex] + '…'
    s = max(0, min(pos - int(ex * 0.4), len(txt) - ex))
    e = min(len(txt), s + ex)
    return ('…' if s else '') + txt[s:e].strip() + ('…' if e < len(txt) else '')


# ---------------------------------------------------------------- 索引先行（快速定位）
FAST_INDEXES = ('02-人物字号索引.md', '03-人物出现索引.md', '04-故事精选索引.md',
                '06-表卷与原件索引.md')
# 候选书抽取已并入上面的 index_lookup()：原先 fast_books() 与 bio_hint() 各扫一遍索引，
# 同一次 --fast 查询要把 19.8 MiB 索引读两遍（02/03 的 15.2 MiB 读了两次）。


# ---------------------------------------------------------------- query
# query 的**已知选项**：值型（`--key value`）与开关型（无值）
# ⚠ 必须白名单校验：原先 `elif i + 1 < len(argv): opts[key] = argv[i+1]` 照收任何 --xx，
#   于是 `--sourse index` 被默默吞掉、源仍是 data（用户以为切到索引了）。
VALUE_OPTS = ('source', 'book', 'top', 'excerpt')
FLAG_OPTS = ('require-all', 'orig-only', 'no-expand', 'fast', 'count', 'json')


def cmd_query(argv):
    terms, opts = [], {}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a.startswith('--'):
            key = a[2:]
            if key in ('require-all',):
                opts['require_all'] = True
            elif key in ('orig-only',):
                opts['orig_only'] = True
            elif key in ('no-expand',):
                opts['no_expand'] = True
            elif key in ('fast',):
                opts['fast'] = True
            elif key in ('count',):
                opts['count'] = True
            elif key in ('json',):
                opts['json'] = True
            elif key in VALUE_OPTS and i + 1 < len(argv) and not argv[i + 1].startswith('--'):
                opts[key.replace('-', '_')] = argv[i + 1]
                i += 1
            else:
                near = difflib.get_close_matches(key, VALUE_OPTS + FLAG_OPTS, n=1)
                print('× 未知选项 %s。可用：%s'
                      % (a, ' '.join('--' + x for x in FLAG_OPTS + VALUE_OPTS)))
                if near:
                    print('  是想写 --%s 吗？' % near[0])
                elif key in VALUE_OPTS:
                    print('  （--%s 缺参数）' % key)
                # 继续跑会给出"无据/默认值"这类**看起来正常**的结果，比报错危险得多
                return 2
        else:
            terms.extend(a.split())
        i += 1
    if not terms:
        print(__doc__)
        return 2

    src = opts.get('source', 'data')
    if src not in ('data', 'ocr', 'index', 'all'):
        print('--source 必须是 data、ocr、index 或 all')
        return 2
    for key in ('top', 'excerpt'):
        if key in opts:
            try:
                opts[key] = int(opts[key])
            except ValueError:
                print('--%s 必须是非负整数' % key)
                return 2
            if opts[key] < 0:
                print('--%s 不能为负数' % key)
                return 2
    if 'book' in opts and not opts['book'].replace('，', ',').strip(' ,'):
        print('--book 不能为空')
        return 2
    orig_terms = list(terms)

    # 通称 → 史文用词：后世通称（乌台诗案…）在正史里往往**根本不出现**，
    # 直接检索必然零命中；若由模型自行反复换词，每换一次就全库重扫一遍，几分钟就没了。
    # 这里按 tools/aliases_events.md 自动改写一次，并在输出头部注明改写过程。
    ev_extra = list(dict.fromkeys(v for t in terms for v in event_terms(t)))
    ev_note = ([t for t in terms if event_terms(t)], ev_extra) if ev_extra else None

    top = int(opts.get('top', 8))
    src = opts.get('source', 'data')
    want_count = bool(opts.get('count'))
    # `--book` 支持逗号分隔（英文/中文逗号都收）：原先只当**单个子串**传下去，
    # `--book 20宋史文白,24明史文白` 会去匹配一个根本不存在的长串 → 扫 0 文件
    # → 触发下面的"范围为空"护栏。retrieve() 本来就收列表，这里补上拆分即可。
    _book_raw = opts.get('book')
    if isinstance(_book_raw, str):
        book_filter = [b.strip() for b in _book_raw.replace('，', ',').split(',') if b.strip()] or None
    else:
        book_filter = _book_raw
    fast_used = []
    hints = []
    if not want_count:
        hint_terms = list(dict.fromkeys(orig_terms[:2] + terms))
        if opts.get('fast') and not book_filter:
            # 一次遍历 02/03/04/06：既抽候选书，也顺手拿本传/字号线索（原先要扫两遍）
            fb, hints = index_lookup(hint_terms)
            if fb:
                book_filter, fast_used = sorted(fb), sorted(fb)
        else:
            _, hints = index_lookup(hint_terms, indexes=BIO_INDEXES, want_books=False)
    user_book = opts.get('book')          # 用户显式写的（区别于 --fast 从索引推出来的）
    ev, stat = retrieve(terms, source=src, book_filter=book_filter, top=top,
                        require_all=bool(opts.get('require_all')),
                        orig_only=bool(opts.get('orig_only')),
                        use_variants=not opts.get('no_expand'),
                        want_count=want_count)

    # ⛔ 检索范围为空 ⇒ 绝不能输出"无据"：那是**书名写错**，不是库里没有。
    #    实测 `--book 20宋史白话`（少一个字）会扫 0 文件 → 「判定：无据」＋
    #    "⛔ 本库未见「苏轼」…应回答：本库未载" —— 一个错别字换来一个错误结论。
    _ubs = ([b.strip() for b in str(user_book).replace('，', ',').split(',') if b.strip()]
            if user_book else [])
    # ⚠ 多值里**某一个**写错时 scanned 仍 >0，旧的空范围护栏不响 → 范围静默变窄，
    #   结论变成"只在剩下的那本书里没找到"＝另一种形式的假"无据"。逐个值单独核。
    if len(_ubs) > 1:
        miss = [b for b in _ubs if stat['book_scope'].get(b, 0) == 0]
        if miss:
            print('# ⛔ --book 多值里有 %d 个没匹配到任何文件：%s' % (len(miss), '、'.join(miss)))
            print('   已纳入：%s'
                  % '、'.join('%s(%d)' % (b, stat['book_scope'].get(b, 0)) for b in _ubs
                              if stat['book_scope'].get(b, 0)))
            print('   可用书文件夹名：%s' % '、'.join(sorted(BOOKS)))
            print('   ⚠ 范围已被静默缩窄——基于它的"无据"结论不成立，请先修正书名。')
            # 非 data 源（index/ocr）本来就可能整源无该书，只警告不拦
            return 2 if src == 'data' else 0

    if stat['scanned'] == 0 and (user_book or fast_used):
        bad = '＋'.join(_ubs) if _ubs else '＋'.join(fast_used)
        print('# ⛔ 检索范围为空：「%s」没有匹配到任何文件（书名可能写错）。' % bad)
        print('   可用书文件夹名：%s' % '、'.join(sorted(BOOKS)))
        print('   ⚠ 这**不是**"本库无载"，只是你限定的范围是空的——修正后重跑。')
        return 2

    if opts.get('json'):
        print(json.dumps({
            'terms': terms, 'original_terms': orig_terms,
            'event_alias': ev_note, 'fast_books': fast_used,
            'emperor_hint': emperor_note(terms, prefix=''),
            'bio_hints': [{'source': a, 'key': c, 'value': b} for a, b, c in hints],
            'evidence': [
                {'cite': citation(e.book, e.lim, e.juan, e.pian), 'path': e.rel,
                 'lines': [e.l1, e.l2], 'kind': e.kind, 'score': round(e.score, 1),
                 'is_bio': e.bio, 'text': e.text} for e in ev],
            'missing_terms': stat['missing'], 'cand_files': stat['cand_files'],
            'has_evidence': bool(ev)}, ensure_ascii=False, indent=1))
        return 0

    if want_count:
        print('# 关键词分布（全库 %d 文件 / %.1f M字 / %.2fs）'
              % (stat['scanned'], stat['chars'] / 1e6, stat['elapsed']))
        print('%8s %8s  %s' % ('文件数', '出现数', '写法'))
        for t, df, hits in stat.get('rows', []):
            print('%8d %8d  %s' % (df, hits, t))
        if stat['missing']:
            print('# ⛔ 零命中：%s' % '、'.join(stat['missing']))
        # 年号／帝名的分布数只说明"哪本书提得多"，**说不出来自哪个皇帝**——把 07 的锚点附上。
        for line in emperor_note(terms):
            print(line)
        return 0

    print('# rag query 「%s」 → 源 %s｜扫描 %d 文件 / %.1f M字｜候选 %d 文件｜%.2fs%s'
          % (' '.join(terms), src, stat['scanned'], stat['chars'] / 1e6,
             stat['cand_files'], stat['elapsed'],
             '｜--fast 定向：%s' % '＋'.join(fast_used) if fast_used else ''))
    for line in emperor_note(terms):
        print(line)
    if ev_note:
        print('# 💡 通称改写：「%s」是后世通称，本库原文不用此词；'
              % '／'.join(ev_note[0]))
        print('#    已同时按史文用词检索 → %s' % '、'.join(ev_note[1]))
        print('#    （对照表：tools/aliases_events.md）')
    if stat['missing']:
        print('# ⚠ 零命中关键词：%s（连繁简/异体变体都没有）' % '、'.join(stat['missing']))
    # 本传线索：01/05 按卷题编，传主名只在篇名行里，故直接问 02/03 索引
    for src_name, val, key in hints:
        print('# 📍 %s：%s = %s' % (src_name, key, val))
    print()
    if not ev:
        print('=== 判定：无据（0 段） ===')
        print('⛔ 本库未见「%s」。' % ' '.join(terms))
        print('   禁止凭记忆作答。应回答：本库未载／正史无此记载，并说明已检索的关键词与范围。')
        if len(terms) == 1 and looks_like_sentence(terms[0]):
            print('   ⚠ 上述参数像是一整句问句——本工具只做**精确子串匹配**，不做分词。')
            print('     请拆成关键词重跑：python tools\\rag.py <人名> <事件/年号> [--book <书>]')
            print('     例：python tools\\rag.py 张居正 抄家 / python tools\\rag.py 元嘉 北伐')
        else:
            print('   ⛔ 不要再自行换词反复全库重试（每换一次就重扫 247MB，几分钟就没了）。')
            print('      先看上面是否有「通称改写」提示；若无，换 1 次写法即可，仍无则如实说无载。')
            print('   已试过的写法变体：%s' % '、'.join(expand_terms(terms[0])[:6]))
        return 0
    for n, e in enumerate(ev, 1):
        print('[E%d] %s%s  %s:%d-%d  [%s]  分 %.1f'
              % (n, '★%s ' % e.bio if e.bio else '', citation(e.book, e.lim, e.juan, e.pian),
                 e.rel, e.l1, e.l2, e.kind, e.score))
        if e.bio:
            what = {'本传': '本篇即该人物的**专传**',
                    '本纪': '此为该帝王**本纪的开篇**（自称／身份行在此，「讳某」即本名）',
                    '载记': '此为**载记**（僭伪／非正统政权之君）开篇'}.get(
                        e.bio, '此为该帝王本纪的开篇')
            print('      ⭐ 此为篇名行／开篇行——%s，正文在同文件后续行，引用请继续读该文件。' % what)
        if e.kind == 'OCR稿':
            print('      ⚠ 机器识别稿（错率 5%–15%），**非原文**，只能作定位线索；'
                  '引用表内数据须标"机器识别稿，字形待核"')
        txt = e.text
        ex = opts.get('excerpt')
        if ex:
            txt = hit_window(txt, terms, int(ex))
        for line in txt.split('\n'):
            print('      ' + line)
        print()
    cov = len(terms) - len(stat['missing'])
    print('=== 判定：有据（%d 段｜关键词覆盖 %d/%d） ===' % (len(ev), cov, len(terms)))
    _tot = stat.get('total_ev', len(ev))
    if _tot > len(ev):
        # 不写这一行，"榜上没有"就会被读成"库里没有"——假排除的源头。
        print('⚠ 另有 %d 段**按分数排在后面没显示**（不是没有命中）。'
              '要穷举这个特征词的全部出处：`--top 0 --excerpt 60`。' % (_tot - len(ev)))
    print('引用纪律：只抄上面标 [原文] 的段，逐字照录、不改写；[译文] 段是后人所译，'
          '引用须注明"译文"；[待核] 段务必先 Read 原文件复核再引。')
    return 0


# ---------------------------------------------------------------- verify
QUOTE_RES = [
    re.compile(r'「([^「」]{4,})」'),
    re.compile(r'『([^『』]{4,})』'),
    re.compile(r'“([^“”]{4,})”'),
    re.compile(r'"([^"]{4,})"'),
]
# 2–3 字的短引语一般不核（多是名物术语，逐条查全库噪音大）。但**归因句式**里的短句是
# 瞎编的高发区：实测答案写「曹操，史载其『目有疾』，晚年失明」——三字句被上面的 {4,}
# 直接放过，而全库「目有疾」只有《梁书·萧恢传》两处，与曹操毫无关系。
# 故紧跟"史载／史称／所谓"一类归因词的短引语照样入核（≥2 字）。
ATTRIB_Q_RX = re.compile(
    r'(?:史载|史称|史文|原文|本传|所谓|古语|谚曰|谚云|语出|载在|有句)'
    r'[^「』”"’]{0,3}[「『“"]([^」』”"’\n]{2,3})[」』”"’]')
BOOK_RES = [
    re.compile(r'《([^《》]{1,40})》'),
    re.compile(r'《资治通鉴》\s*([^，。；\s]{1,12}纪)[^\n]{0,8}?(卷[〇零一二三四五六七八九十百千0-9]+)'),
]
POST_CITE_WIN = 120      # 引文之后多远内找它的《书名·卷次》（见 cmd_verify 的出处归属）
# cn2int 已统一到 _lib（见文件头 import）；本文件不再自带实现。


_BOOK_DIR = {v: k for k, v in BOOKS.items()}


def book_dir(book):
    return _BOOK_DIR.get(book)


def juan_exists(book, juan):
    """该书的卷次是否真实存在（抓《宋史·卷九百》这类伪造卷号）。"""
    folder = book_dir(book)
    if not folder or not juan:
        return None
    n = cn2int(re.sub(r'^卷', '', juan).rstrip('上下中'))
    if n is None:
        return None
    d = DATA / folder
    if not d.is_dir():
        return None
    for p in d.rglob('*.md'):
        m = _JUAN_RE.search(p.stem)
        if m and cn2int(re.sub(r'^卷', '', m.group(0)).rstrip('上下中')) == n:
            return True
    return False


def search_needles(needles, folders=None, source='data', max_hits=3):
    """一次遍历语料，为多个待查文字找出处。

    每条引文过两道判据：① 严格——按原文（去首尾标点）逐字定位，覆盖绝大多数情况；
    ② 宽松——① 未命中时改用"去标点后整串比对"，容"引文句读位置与库中不同"这类差异，
      避免把真引文误判成编造。
    ⚠ 两道判据**共用同一遍语料扫描**。早先 ② 是独立的第二遍全库，"有一条未逐字命中
      ＝ 247 MB 再读一遍"；而 ② 里又无条件切段评分，两者叠加实测把 verify 拖到 34 s。
      现在：② 用单字探针预筛 → 命中候选文件先比归一化整串 → 真命中才 locate/切段。
    返回 {needle: [(rel, 起行, 止行, 判定, 书名, 卷次, 篇名)]}
    """
    strict = {n: n.strip().strip(_PUNCT) for n in needles}
    pats = {n: (strict[n].encode('utf-8') if len(norm(strict[n])) >= 3 else None)
            for n in needles}

    def _probes(n):
        """② 的字节预筛：取引文里的若干单字，**全部**出现才值得解码做归一化比对。
        ⚠ 不能用"无标点长片段"当探针——引文整串不带句读时它就等于整串，而库里同一句
          带句读（「苏轼，字子瞻」里根本不含连续片段「轼字」），真引文会被预筛掉、误判编造。"""
        w = norm(strict[n])
        return [c.encode('utf-8') for c in sorted(set(w))[:4]] if len(w) >= 3 else None

    probes = {n: _probes(n) for n in needles}
    found = {n: [] for n in needles}
    missed = set(needles)
    wants = {n: norm(strict[n]) for n in needles}

    # 单遍扫描：逐字判据先跑，宽松判据只在"探针字全在、且逐字未中"的文件上跑，
    # 且**先比归一化整串、命中才解码切段**（chunks_labeled 很贵）。
    for tag, path in iter_corpus(source):
        rel = str(path.relative_to(ROOT))
        if folders and not any(f in rel for f in folders):
            continue
        try:
            b = path.read_bytes()
        except OSError:
            continue
        here = [n for n in missed if pats[n] and pats[n] in b]
        # 宽松预筛：本文件没有逐字命中，但引文的探针字都在——可能是句读位置差异
        loose = [n for n in missed if probes[n] and n not in here and not found[n]
                 and all(p in b for p in probes[n])]
        if not here and not loose:
            continue
        text = b.decode('utf-8', 'replace')
        labeled = None
        if here:
            _t, book, _l, juan, pian, _r = locate(path, text)
            labeled = chunks_labeled(text)[2]
            for n in here:
                pat = strict[n]
                hit = None
                for l1, l2, head, ch, kind in labeled:
                    if pat in ch or norm(pat) in norm(ch):
                        hit = (rel, l1, l2, kind, book, juan, pian or head)
                        break
                if hit:
                    found[n].append(hit)
                    if len(found[n]) >= max_hits:
                        missed.discard(n)
        normalized = None
        for n in loose:          # 预筛已保证这些引文在本文件未逐字命中、且库里尚无命中
            want = wants[n]
            if normalized is None:
                normalized = norm(text)
            if want not in normalized:
                continue
            if labeled is None:
                _t, book, _l, juan, pian, _r = locate(path, text)
                labeled = chunks_labeled(text)[2]
            for l1, l2, head, ch, kind in labeled:
                if want in norm(ch):
                    found[n].append((rel, l1, l2, kind, book, juan, pian or head))
                    break
            else:
                # 整文件有、单个段落没有 ⇒ 引文跨了切分边界（长引文／原文译文交界）。
                # 判"查无此文＝疑似编造"是冤枉，故给出定位行并标 [待核]，交人工复核。
                ln = _norm_line_of(text, normalized, want)
                found[n].append((rel, ln, ln, '待核', book, juan, pian))
            if len(found[n]) >= max_hits:
                missed.discard(n)
    return found


def _norm_line_of(text, normalized, want):
    """归一化串在 `normalized` 里的偏移 → 原文件行号（1 起，找不到给 1）。"""
    off = normalized.find(want)
    if off < 0:
        return 1
    acc, lines = 0, text.split('\n')
    for i, line in enumerate(lines, 1):
        acc += len(norm(line))
        if acc > off:
            return i
    return len(lines)


def find_text(needle, folders=None, max_hits=2, source='data'):
    """在语料里找一段文字，返回 [(rel, 行号, 起止, 判定, 书名, 卷次, 篇名)]。"""
    return search_needles([needle], folders=folders, source=source,
                          max_hits=max_hits)[needle]


def book_of_cite(spec):
    """把《...》里的内容归到本库 27 部中的某一部；不属则返回 (None, 原始串)。

    ⚠ 遍历 `BOOK_NAMES` 必须**排序**：它是 set，迭代顺序随字符串哈希（进程间不稳定），
    对《宋》《梁》这类简称，`b.startswith(head)` 会命中多部书，不排序则归属不确定。
    """
    head = re.split(r'[·・\s（(]', spec.strip())[0]
    for b in sorted(BOOK_NAMES):
        if head == b or head.startswith(b) or b.startswith(head):
            return b, spec
    for b in BANNED_BOOKS:
        if head.startswith(b) or b.startswith(head):
            return b, spec
    # 载记/纪传写法：《资治通鉴·宋纪一》
    for b in sorted(BOOK_NAMES):
        if spec.startswith(b):
            return b, spec
    return None, spec


# ---------------------------------------------------------- verify 的三道"人物"闸
# 实测（2026-09-24）：旧 verify 只查「引文在不在库里＋出自哪本书」，于是
#   ① 把魏徵的话写成「《旧唐书·卷七十一》李勣传载，勣对曰：…」⇒ rc=0 放行（同书内张冠李戴）；
#   ② 「陈玉成，字荣光」「海瑞，字刚峰」这类**无引号的身份断言** ⇒ 抽到 0 条引文，rc=0 放行。
# 而"猜人物"的答案恰恰由这两类句子构成——所以铁律 8 的交付闸在人物题上是**形同虚设**的。
# 下面三道闸补：说话人归属、声称篇名、字号断言。
_SAY_RX = re.compile(r'([一-鿿]{1,5}?)((?:又|再|复)?(?:对曰|曰|谓|言|上疏|上书|奏疏|奏|'
                     r'让|辞|谢|敕|谕|戒|数|责|泣|笑|叹))')
_SAY_STOP = set('乃遂因即又再复大皆悉咸相共其之耳亦公帝王后主贼众官军兵吏左右前后请尝未')
# ⚠ 尾部**不含「记」**：2026-09-29 实测——「记」是高频普通动词（"同卷还记了一笔怪事"／
#   "书里还记了一笔"），`([一-鿿]{1,9})记` 会把「同卷还」当成篇名 → 正常叙述被篇名闸判
#   "传名与实际不符"（假 FAIL，连报两条）。本库 27 部正史的篇名尾部只用 本纪／列传／载记／传／志
#   （"载记"已单列），没有单以「记」结尾的篇名，故去掉它不会漏判真篇名。
_PIAN_TAIL_RX = re.compile(r'([一-鿿]{1,9})(本纪|列传|载记|传|志)')
# 篇名栏若只由"编号＋类名"组成（列传二百六十二／志第三）就没有专名可比，必须跳过，
# 否则清史稿／通鉴的每条引文都会被冤枉。
_PIAN_PLAIN_RX = re.compile(r'^(?:[〇一二三四五六七八九十百千0-9]|第|上|中|下|'
                            r'本纪|列传|载记|传|志|表|书|记|赋|序|论|疏|策|'
                            r'文苑|儒林|循吏|酷吏|佞幸|孝义|孝友|良吏|宦者|外戚|宗室|'
                            r'皇后|后妃|礼乐|礼|乐|天文|历|五行|兵|刑|刑法|食货|地理|河渠|'
                            r'艺文|经籍|舆服|仪卫|祥瑞|灾异|外国|蛮夷|西南|西北|南|北|东|西|'
                            r'史记|汉书|列女|逸民|独行|方技|艺术|孝弟|音乐|兵卫|卫|道|路|州)+$')
_ZI_RX = re.compile(r'([一-鿿]{2,6})[，,、]字([一-鿿]{1,6})(?=[，,、。；\s\n]|$)')


def claim_pian(win):
    """引文前窗里**声称**的篇名（「……李勣传载」→「李勣」）。

    取最后一个匹配——引文前常有「列传第三十一李勣传」这类**卷次＋篇名**连写，
    旧写法非贪婪 `{1,6}?` 会停在「第」上，而 `「李勣」 in 「第三十一李勣」` 为真 ⇒
    把张冠李戴**判成相符**（实测）；故剥掉「第X」序数前缀与残留数字，只留专名。
    """
    last = None
    for m in _PIAN_TAIL_RX.finditer(win):
        last = m
    if not last:
        return ''
    s = re.sub(r'^[一-鿿]{0,4}第[一二三四五六七八九十百千0-9〇]*', '', last.group(1))
    while s and s[0] in '〇一二三四五六七八九十百' and len(s) > 1:
        s = s[1:]
    return s if len(s) >= 2 else ''


def say_subject(text, pos, known):
    """引文起笔前 40 字里的**说话人** → (名, 全名)；判不准时返回 ('', '')（宁漏不误）。"""
    w = text[max(0, pos - 40):pos]
    m = None
    for m in _SAY_RX.finditer(w):
        pass
    if not m:
        return '', ''
    nm = m.group(1)
    while len(nm) > 1 and nm[0] in _SAY_STOP:
        nm = nm[1:]
    if not nm or nm[-1] in _SAY_STOP:
        return '', ''
    # 只认 03 立目者（03＝02 甲区 ∪ 列传篇名传主 ∪ extra_names，覆盖足够广）；
    # ⚠ 这里**不能**用 person_index_entry 兜底：它每次调用都要把 02/03 两份索引整个读一遍，
    #    放在逐引文的循环里会把 verify 拖垮（实测同类错误把 verify 拖到 34 s）。
    if len(nm) >= 2 and nm in known:
        return nm, nm
    # 单字（「勣对曰」）：往前拼出全名，且**必须**在 03 里立目才敢据此报错
    for c in sorted(set(re.findall(r'[一-鿿]{1,3}' + nm, w)), key=len, reverse=True):
        for j in range(len(c) - 1):
            s = c[j:]
            if len(s) >= 2 and s in known:
                return nm, s
    return '', ''


def nearby_person(text, pos, known):
    """引文前 24 字内**最近的一个 03 立目人名**（不要求后面跟"曰"）——给归因闸用。
    「曹操，史载其『目有疾』」这种句子没有"曰"，`say_subject` 抓不到主语。"""
    w = text[max(0, pos - 24):pos]
    best = None
    for m in re.finditer(r'[一-鿿]{2,4}', w):
        s = m.group(0)
        for j in range(len(s) - 1):
            for L in (4, 3, 2):
                c = s[j:j + L]
                if len(c) == L and c in known and (best is None or m.end() > best[0]):
                    best = (m.end(), c)
    return best[1] if best else ''


# 「与亮曰」「破亮军」里的单字省称是真指此人；「徐世勣」里的「勣」是**别人名字的中间字**。
# 故"独立成词"＝左邻不是汉字，**或**左邻是下列句法词（称谓／连词／使令／攻防动词）。
_NAME_PRE = ('与|及|同|共|遣|命|令|谓|语|示|白|召|从|随|遇|为|说|归|见|使|请|乃|则|因|遂|而|'
             '亦|皆|表|拜|擢|诛|杀|击|破|讨|败|擒|斩|拒|御|战|让|谢|叱|骂|责|问|答|对|兄|弟|'
             '父|子|妻|师|臣|将|率|闻|知')


def win_text(rel, a, b, pad=4):
    """命中处连读窗口（±pad 行），供"这句话到底是谁说的"判据使用。"""
    try:
        ls = (ROOT / rel).read_text('utf-8', errors='replace').splitlines()
    except OSError:
        return ''
    return '\n'.join(ls[max(0, a - 1 - pad):min(len(ls), b + pad)])


def name_in_window(nm, full, t):
    """窗口里是不是**真提到这个人**（而不是撞上别人名字里的一个字）。

    ⚠ 单字判据必须加"左邻成词"闸：实测答案写「李勣对曰」而引文落在《旧唐书·魏徵传》，
      窗口里有「时徐世勣尚为李密拥众」——只查 `'勣' in t` 会把**徐世勣**当成李勣而放行；
      要求左邻非汉字或为句法词后，"徐世勣"不再算证据（实测已拦下）。
    """
    if full and full in t:
        return True
    # 左邻必须"成词"：非汉字（句首／逗号／引号）或句法词，否则「徐世勣」里的「勣」会算成李勣
    return re.search(r'(?:(?<![一-鿿])|(?:%s))%s'
                     % (_NAME_PRE, re.escape(nm)), t) is not None


# 「名（某帝）」式断言：猜帝王最常错的是**名与号对不上**（实测答案写「祖父刘宏（汉顺帝）」，
# 而刘宏是东汉孝灵帝；汉顺帝是刘保）。07 一行之内同时给出本名与号，故可整行回查。
_IMH_RX = re.compile(r'[（(][^（()）\n]{0,6}?([一-鿿]{1,3}?)(?:帝|皇帝|天子)[）)]')


def imperial_hao_mismatch(text):
    """→ [(名, 答案写的号, 07 实际号)]。只在**名进了 07 的检索键**、且号字整行不出现时报；
    名不在 07（无本纪者，如追尊帝、蜀吴之主）时不判——07 不收 ≠ 史无其人。"""
    rows = emperor_rows()
    if not rows:
        return []
    out, seen = [], set()
    for m in _IMH_RX.finditer(text):
        hao = m.group(1)
        # 括号前那串汉字里**由短到长**试后缀取键：'祖父刘宏（汉顺帝）' 若整串去配会拿到
        # '祖父刘宏'，不在 07 里 ⇒ 闸被绕过；真键是后缀 '刘宏'。
        pre = re.search(r'[一-鿿]{2,4}$', text[:m.start()])
        if not pre:
            continue
        nm = next((c for c in (pre.group(0)[-L:] for L in (2, 3, 4)
                               if L <= len(pre.group(0)))
                   if any(c in r['keys'] for r in rows)), '')
        if not nm or (nm, hao) in seen:
            continue
        seen.add((nm, hao))
        hits = [r for r in rows if nm in r['keys']]
        blob = ''.join(r['label'] + r['cite'] + '、'.join(r['keys']) + r['ident']
                       for r in hits)
        if hao[-1] in blob:
            continue
        out.append((nm, hao, '／'.join(sorted({r['label'] for r in hits}))))
    return out


def cmd_verify(argv):
    if not argv:
        print(__doc__)
        return 2
    src = argv[-1]
    if src == '-':
        text = sys.stdin.read()
        name = '<stdin>'
    else:
        p = Path(src)
        if not p.exists():
            print('找不到文件：%s' % src)
            return 2
        text = p.read_text(encoding='utf-8', errors='replace')
        name = p.name

    quotes = []
    seen = set()
    for rx, lo in [(r, 4) for r in QUOTE_RES] + [(ATTRIB_Q_RX, 2)]:
        for m in rx.finditer(text):
            q = m.group(1).strip()
            nq = norm(q)
            if len(nq) < lo or nq in seen:
                continue
            seen.add(nq)
            pre = text[:m.start()]
            # 上一条引文的《》出处**不能算到本条头上**：原先 pre 一路找到文首，于是
            # 「A」（《三国志》）……「B」里的 B 继承 A 的书名 → 假 FAIL
            # （实测「目有疾」被注成《三国志》）。与 post 侧"在下一个引号处截断"对称：
            # pre 从**上一个闭引号**之后算起。
            _cut = max(pre.rfind(c) for c in '」』”"')
            if _cut >= 0:
                pre = pre[_cut + 1:]
                # 紧跟在闭引号后的《》是**上一条**引文的出处（「A」（《书》）「B」），
                # 不能留给 B 继承——所以把这段尾巴一并让掉。
                # ⚠ 但**只让掉"紧贴"的那种**：中间夹了句号／分号就说明这是一句新话的开头
                # （`「A」。《晋书·卷一百二》「B」`），此时《晋书》是 B 的出处；旧写法
                # `^[^「』”\n]{0,20}》` 会把 `。《晋书·卷一百二》` 整段当尾巴吃掉，
                # 实测 B 被回落到上一条的《资治通鉴》⇒ 假 FAIL「书名错配」。
                _t = re.match(r'^[（(〔【\[\s]{0,3}《[^》]{1,20}》', pre)
                if _t:
                    pre = pre[_t.end():]
            pre_cites = BOOK_RES[0].findall(pre)
            pre_near = pre_cites[-1] if pre_cites else ''
            # 出处注在引文**之后**同样通行（`SKILL.md` 的示范就是「引文」＋《书名·卷次》）。
            # 只看 pre 会把每条引文都归到**上一条**的出处，混源稿件因此产生假 FAIL。
            # 故向后取一小窗内的首个《》，并在下一个引号处截断，避免越界抓走别条的出处。
            rest = text[m.end():m.end() + POST_CITE_WIN]
            for _stop in ('「', '『', '“'):
                _i = rest.find(_stop)
                if _i >= 0:
                    rest = rest[:_i]
            # ⚠ 再截到「下一个列表项／标题／引用块」之前：列表式答案（`- 《书》：「引文」` 逐条罗列）
            #   会把**下一条引文**的书名当成这一条的，于是 pre≠post ⇒ 假 WARN「前后不一致、
            #   无法判定归属」（2026-09-24 实测：一份 16 条引文的答案报出 5 条此类 WARN）。
            _m2 = re.search(r'\n\s*(?:[-*+]\s|#{1,6}\s|\d+[.、]|>)', rest)
            if _m2:
                rest = rest[:_m2.start()]
            post_cites = BOOK_RES[0].findall(rest)
            post_near = post_cites[0] if post_cites else ''
            quotes.append({'raw': q, 'nq': nq, 'pos': m.start(),
                           'attrib': rx is ATTRIB_Q_RX,
                           'pre': pre_near, 'post': post_near})

    book_cites = []
    for spec in BOOK_RES[0].findall(text):
        b, _ = book_of_cite(spec)
        book_cites.append((spec, b))
    # 通鉴 纪名+卷号的另一种写法
    for ji, juan in BOOK_RES[1].findall(text):
        book_cites.append(('%s%s' % (ji, juan), '资治通鉴'))

    print('# rag verify  %s' % name)
    print('# 抽取：引文 %d 条｜《》书名 %d 处' % (len(quotes), len(book_cites)))
    print()
    names = known_names()          # 归属闸用它判"前文那个词是不是人名"
    # ⚠ 03 **不含历代帝王**（本纪卷首只写「讳某」，见 07 表头），所以拿 07 的首键（本名）
    #   补进来——否则「曹操，史载其『目有疾』」这类断言连主语都认不出，闸必然空转。
    #   复制一份再补：known_names 是进程内缓存，直接改会污染「谜」的候选表。
    names = dict(names)
    for _r in emperor_rows():
        if 2 <= len(_r['keys'][0]) <= 4:
            names.setdefault(_r['keys'][0], (False, 0))

    b_ok, b_warn, b_fail = [], [], []

    # --- 书目检查
    uniq = {}
    for spec, b in book_cites:
        uniq.setdefault(spec, b)
    for spec, b in uniq.items():
        if b in BANNED_BOOKS:
            b_fail.append('《%s》——%s' % (spec.split('·')[0].split('・')[0], BANNED_BOOKS[b]))
        elif b is None:
            b_warn.append('《%s》不在本库 27 部书目内，无法回库核对' % spec)
        else:
            # ⚠ 必须只把**卷号 token**交给 juan_exists。
            #   若把整串（如「宋史」后面的「卷九百·列传第九十七」）递进去，cn2int 读到 `·`
            #   会判成"解析不了"→ juan_exists 返回 None → 被当成"不是 False"而放行，
            #   于是**伪造卷号只要后面跟个篇名就能过闸**（实测：`《宋史·卷九百》` FAIL，
            #   `《宋史·卷九百·列传第九十七》` 却 rc=0）。这正是铁律 8 声称要拦的错。
            m = re.search(r'卷[〇零一二三四五六七八九十百千0-9]+', spec)
            ok = juan_exists(b, m.group(0)) if m else None
            if m and ok is False:
                b_fail.append('《%s》无 %s 这一卷——卷号有误' % (b, m.group(0)))
            else:
                b_ok.append('《%s》→ 本库《%s》%s'
                            % (spec, b, '（卷次存在）' if (m and ok) else ''))

    # 歧义朝名：单说「宋朝」而未加限定，是最常见的隐性错答。
    # 名单取自 `SKILL.md`「朝代／政权消歧」的歧义朝名——此处只用**名单**、不复刻映射，
    # 免得再多出一份与 00 表／脚本字典并行的副本（那是漂移之源）。
    for amb in ('宋', '汉', '唐', '周', '魏', '齐', '梁', '晋', '金'):
        if re.search(r'(?<![刘赵东西南北前后曹萧李高宇文石朱郭柴司孫孙])%s朝' % amb, text):
            b_warn.append('出现「%s朝」而未加限定——同名异代（详表见 `SKILL.md`「朝代／政权消歧」＋'
                          '`00-目录索引.md`），首见必须写明政权（写「刘宋」不写「宋」）' % amb)
    for amb in ('宋史纪事本末', '通鉴纪事本末', '明史纪事本末'):
        if amb in text:
            b_fail.append('提到「%s」——本库未收录（只收 27 部）' % amb)

    # --- 引文检查（先在**被引书文件夹**内查：快且准；未中再全库）
    # ⚠ 必须**批量**查：原先逐段各调一次 find_text，N 段引文 = N 遍全库扫描
    #   （实测 6 条查无此文要 10.9 s，时间全花在重复读同一批文件上）。
    #   现在按「被引书」分组各扫一次 + 未命中的合并再扫一次，判定逻辑逐条不变。
    q_ok, q_warn, q_fail = [], [], []
    plans = []                      # [(q, cited_book, segs, ei)]
    for q in quotes:
        ei = q['raw'] if len(q['raw']) <= 40 else q['raw'][:40] + '…'
        # 出处归属：引文前后各取一个候选。两者不一致时**不判归属**（只 WARN），
        # 因为此时无法确定哪个才是这条引文的出处，硬判会冤枉作者。
        b_pre = b_post = None
        if q.get('pre'):
            cb, _ = book_of_cite(q['pre'])
            b_pre = cb if cb in BOOK_NAMES else None
        if q.get('post'):
            cb, _ = book_of_cite(q['post'])
            b_post = cb if cb in BOOK_NAMES else None
        cited_book = None
        if b_pre and b_post and b_pre != b_post:
            q_warn.append('引文「%s」前后分别注为《%s》《%s》且不一致——无法判定归属，'
                          '请人工确认哪一个是它的出处' % (ei, b_pre, b_post))
        else:
            cited_book = b_pre or b_post
        # 省略号分段；**保留原始标点**（库中原文带标点，先去标点会反而匹配不上）
        segs = [x.strip() for x in re.split(r'…+|\.\.\.+', q['raw'])]
        segs = [x for x in segs if len(norm(x)) >= 3] or [q['raw'].strip()]
        plans.append((q, cited_book, segs, ei))

    hit_map = {}                    # seg → [命中…]
    by_book = {}
    for _q, cb, segs, _ei in plans:
        if cb:
            by_book.setdefault(cb, []).extend(segs)
    for cb, seglist in by_book.items():
        for s, hits in search_needles(seglist, folders=[book_dir(cb)], max_hits=1).items():
            if hits:
                hit_map.setdefault(s, []).extend(hits)
    rest, seen_seg = [], set()
    for _q, _cb, segs, _ei in plans:
        for s in segs:
            if s not in hit_map and s not in seen_seg:
                seen_seg.add(s)
                rest.append(s)
    if rest:                        # 书内没查到（或没注明出处）→ 合并一次全库扫描
        for s, hits in search_needles(rest, max_hits=2).items():
            if hits:
                hit_map.setdefault(s, []).extend(hits)

    for q, cited_book, segs, ei in plans:
        where = [h for s in segs for h in hit_map.get(s, [])]
        missing = [s for s in segs if not hit_map.get(s)]
        if missing:
            if len(q['nq']) >= 8:
                q_fail.append('引文「%s」在本库任何文件都未逐字出现（%d 字）——长引文查无此文＝疑似编造'
                              % (ei, len(q['nq'])))
            else:
                q_warn.append('引文「%s」未逐字命中（%d 字）——短句可能是你自己的叙述，仍建议核对'
                              % (ei, len(q['nq'])))
            continue
        books_hit = sorted({h[4] for h in where})
        kinds = sorted({h[3] for h in where})
        loc = '%s:%d-%d' % (where[0][0], where[0][1], where[0][2])
        if cited_book and cited_book not in books_hit:
            q_fail.append('引文「%s」实际出自《%s》（%s），答案却注为《%s》——书名错配，正是「宋书/宋史」式的翻车'
                          % (ei, '／'.join(books_hit), loc, cited_book))
            continue
        tag = '《%s》%s' % ('／'.join(books_hit), loc)
        if '译文' in kinds and '原文' not in kinds:
            q_warn.append('引文「%s」在库中位于**白话译文**段（%s）——若声称是文言原文即错' % (ei, tag))
        elif '待核' in kinds:
            q_warn.append('引文「%s」的原文/译文属性待核（%s）——引用前请 Read 原文件' % (ei, tag))
        else:
            q_ok.append('引文「%s」→ %s（%s）' % (ei, tag, '／'.join(kinds)))
        # ---- 归属闸：这句话在库里**是不是这个人说的**（引文真、书也对，人头安错照样 rc=0）
        nm, full = say_subject(text, q['pos'], names)
        if nm and where:
            wins = [win_text(h[0], h[1], h[2]) for h in where[:3]]
            if not any(name_in_window(nm, full, t) for t in wins):
                h = where[0]
                q_fail.append('引文「%s」答案记在「%s」名下，但它命中处（《%s·%s》%s:%d，篇名「%s」）'
                              '连上下各 4 行都没有此人**独立成词**的记载——疑似张冠李戴：'
                              '要么换掉说话人，要么另找此人亲口说过该话的卷次'
                              % (ei, full or nm, h[4], h[5], h[0], h[1], h[6]))
        # ---- 归因闸：「史载其『X』」这类**没有"曰"的史文引用**（旧归属闸靠 曰/云 找主语，
        #      这种句式抓不到 ⇒ 伪造的短句带着真出处一起放行；实测「目有疾」出自《梁书·萧恢传》）。
        if q.get('attrib') and where:
            nm3 = nearby_person(text, q['pos'], names)
            if nm3:
                wins3 = [win_text(h[0], h[1], h[2]) for h in where[:3]]
                if not any(name_in_window(nm3, nm3, t) for t in wins3):
                    h = where[0]
                    q_fail.append('答案以「%s」归因说「%s」，但该句在库中只出现在《%s·%s》%s:%d'
                                  '（篇名「%s」），其上下各 4 行没有「%s」——**这句史文不是他的**，'
                                  '要么另找出处，要么删掉这处引证'
                                  % (nm3, ei, h[4], h[5], h[0], h[1], h[6], nm3))
        # ---- 篇名闸：答案声称的传名 vs 该卷实际篇名
        cp = claim_pian(text[max(0, q['pos'] - 46):q['pos']])
        if cp and where:
            named = [h for h in where
                     if h[4] != '资治通鉴' and h[6] and not _PIAN_PLAIN_RX.match(h[6])]
            if named and not any(cp in h[6] or h[6] in cp for h in named):
                h = named[0]
                q_fail.append('答案把引文系于《%s》的「%s传」，而该卷此段实际篇名为「%s」——'
                              '**传名与实际不符**（出处对不上，须改成真篇名）'
                              % (h[4], cp, h[6]))

    # --- 字号断言：猜人物最常写的就是「X，字Y」这类**没有引号**的句子，
    #     旧 verify 抽不到引文 ⇒ 整段放行（实测「陈玉成，字荣光」rc=0）。
    for nm2, zi in sorted(set(_ZI_RX.findall(text))):
        e = person_index_entry(nm2)
        if e['zi'] and zi not in e['zi']:
            q_fail.append('答案写「%s，字%s」，而 02 记其字为「%s」——把号当字或记错'
                          % (nm2, zi, '／'.join(e['zi'])))
        elif not e['in02'] and not e['in03']:
            q_warn.append('答案写「%s，字%s」，但 02/03 未立目「%s」——字无从核对，'
                          '须先 `locate %s --first` 定起读行、Read 原文再落笔'
                          % (nm2, zi, nm2, nm2))

    # --- 帝号闸：「名（某帝）」与 07 帝王本纪索引对不上
    for nm3, hao, real in imperial_hao_mismatch(text):
        q_fail.append('答案写「%s（%s帝）」，但 07 里「%s」只对应 %s——**名与帝号不匹配**，'
                      '须回 07／本纪原文复核是谁' % (nm3, hao, nm3, real))

    print('—— 书目 ——')
    for x in b_ok:
        print('OK   ' + x)
    print('—— 引文 ——')
    for x in q_ok:
        print('OK   ' + x)
    for x in b_warn + q_warn:
        print('WARN ' + x)
    for x in b_fail + q_fail:
        print('FAIL ' + x)
    print()
    nf, nw = len(b_fail) + len(q_fail), len(b_warn) + len(q_warn)
    if nf:
        print('# 结论：FAIL %d / WARN %d —— **必须修改后重跑**。'
              '凡查无此文者，删掉引文，或改引库中真实原文；书名错配者改书名。' % (nf, nw))
        return 1
    print('# 结论：无 FAIL（WARN %d）。WARN 逐条确认后即可交付。' % nw)
    return 0


# ---------------------------------------------------------------- stat / selftest
def cmd_stat(argv):
    n, ch, per = 0, 0, {}
    for tag, path in iter_corpus('data'):
        rel = path.relative_to(ROOT).parts
        bk = BOOKS.get(rel[1], rel[1]) if len(rel) > 1 else '?'
        try:
            b = path.read_bytes()
        except OSError:
            continue
        n += 1
        ch += len(b)
        per[bk] = per.get(bk, 0) + 1
    print('# 语料 %d 文件 / %.1f M字' % (n, ch / 1e6))
    for bk, c in sorted(per.items(), key=lambda x: -x[1]):
        print('%5d  %s' % (c, bk))
    print('# 书目 %d 部（应 27 部）' % len(per))
    return 0 if len(per) == 27 else 1


SELFTEST_CASES = [
    # (关键词, 期望命中文件数下限, 说明)
    (['刘裕', '寄奴'], 1, '刘宋武帝小名（06宋书）'),
    (['张居正', '考成法'], 1, '明史·张居正传'),
    (['元嘉'], 1, '年号（歧义朝名锚点）'),
    (['努尔哈赤'], 1, '清史稿'),
    (['端木赐'], 1, '异文并录'),
]


def cmd_selftest(argv):
    fails = 0
    quick = '--quick' in argv
    t0 = time.time()
    # 1. 书目
    per = set()
    for tag, path in iter_corpus('data'):
        r = path.relative_to(ROOT).parts
        if len(r) > 1:
            per.add(BOOKS.get(r[1], r[1]))
    if len(per) < 27:
        print('FAIL  书目仅 %d 部（应 27）' % len(per))
        fails += 1
    else:
        print('OK    书目 27 部')
    # 2. 检索必须命中
    cases = SELFTEST_CASES[:2] if quick else SELFTEST_CASES
    for terms, least, desc in cases:
        ev, st = retrieve(terms, top=3)
        if len(ev) >= least:
            print('OK    检索 %s（%s）→ %d 段' % ('+'.join(terms), desc, len(ev)))
        else:
            print('FAIL  检索 %s（%s）→ 0 段' % ('+'.join(terms), desc))
            fails += 1
    # 3. 伪造语句必须查无（全库宽松扫描，较慢；--quick 跳过）
    if not quick:
        fake = '朕闻张居正通权达变可与共谋天下事'
        hits = find_text(fake, max_hits=1)
        if hits:
            print('FAIL  伪造样本竟然命中：%s' % hits[0][0])
            fails += 1
        else:
            print('OK    伪造样本查无此文（越不像真的越该查无）')
    # 4. 原文/译文判别
    for rel, want in (('06宋书文白/现代文/001_卷一·本纪第一.md', '原文'),
                      ('20宋史文白/现代文/001_卷一·本纪第一.md', '原文'),
                      ('01史记文白/现代文/001_卷一·五帝本纪第一_文白对照.md', '原文')):
        p = DATA / rel
        if not p.exists():
            continue
        text = p.read_text('utf-8', 'replace')
        mode, conf, labeled = chunks_labeled(text)
        probe = next((x for x in labeled if len(norm(x[3])) >= 25), None)
        if probe is None:
            continue
        k0 = probe[4]
        if k0 == want:
            print('OK    原文/译文判定 %s：模式 %s（置信 %.2f）样本段＝%s'
                  % (rel.split('/')[0], mode, conf, k0))
        else:
            print('FAIL  原文/译文判定 %s：样本段判为 %s，应为 %s（模式 %s）'
                  % (rel.split('/')[0], k0, want, mode))
            fails += 1
    # 5. 事典通称表：结构必须合法，且右端「史文用词」必须真在本库
    #    （右端词若零命中＝换个词还是零命中，等于把"搜不到"换个姿势再来一遍）
    if EVENT_ALIAS_FILE.exists():
        malformed = []
        for l in EVENT_ALIAS_FILE.read_text('utf-8', errors='replace').splitlines():
            s = l.strip()
            if not s or s.startswith('#'):
                continue
            parts = [x.strip() for x in s.split('｜')]
            if len(parts) < 2 or any(not p for p in parts):
                malformed.append(s[:40])
        if malformed:
            print('FAIL  aliases_events.md 格式不合（应「通称｜史文用词…」且无空段）：%s'
                  % '；'.join(malformed[:3]))
            fails += 1
        else:
            print('OK    通称表结构合法（%d 条通称，%d 个史文用词，详见 tools/aliases_events.md）'
                  % (len(EVENT_ALIASES), len({w for v in EVENT_ALIASES.values() for w in v})))
    else:
        print('FAIL  缺 tools/aliases_events.md（后世通称将退回"反复换词"的旧毛病）')
        fails += 1
    # 5b. 篇名行（本传）置顶回归：宋史卷 338 是苏轼本传，必须排第一
    #     （它只有「苏轼（子过）」4 字，不加权就会被胡三省注里的长段压下去——
    #      这正是"找不到苏轼专传"的成因）
    ev_su, _ = retrieve(['苏轼'], book_filter='20宋史文白', top=3)
    if ev_su and ev_su[0].bio == '本传' and '338' in ev_su[0].rel:
        print('OK    本传置顶：苏轼 → %s（篇名行「%s」）'
              % (ev_su[0].rel, ev_su[0].text.strip()[:20]))
    else:
        print('FAIL  本传置顶失效：苏轼 首位＝%s（bio=%s），应为宋史 338 卷本传'
              % (ev_su[0].rel if ev_su else '无', ev_su[0].bio if ev_su else '-'))
        fails += 1
    # 5b2. 帝王本纪锚点回归：本纪卷首只写「讳某／名某」、通篇称「帝／上」，按**本名**查时
    #      02/03 不收录（那两份按列传式「X，字Y」抽名），只能靠 07 锚点＋身份行姓名等价。
    #      旧病（实测）：查「杨坚」钉不住《隋书·卷一》，端上来的却是《隋书·卷六十》崔仲方传
    #      ——这是"猜皇帝"最害人的形态（看着有据、其实是臣僚传）。
    for _kw, _bk, _tp in (('杨坚', '13隋书文白', 1), ('司马炎', '05晋书文白', 1),
                          ('曹操', '04三国志文白', 1), ('刘秀', '03后汉书文白', 5)):
        _ev, _ = retrieve([_kw], top=_tp)
        _hit = next((e for e in _ev if e.bio == '本纪' and _bk in (e.rel or '')), None)
        if _hit:
            print('OK    本纪锚点：%s → ★本纪《%s·%s》' % (_kw, _hit.book, _hit.juan))
        else:
            print('FAIL  本纪锚点失效：%s 前 %d 条无 %s ★本纪（旧病：答臣僚传）'
                  % (_kw, _tp, _bk))
            fails += 1
    # 假锚点不得复现：①《宋书·卷八十八》薛安都传正文提到「拓跋焘」，曾被误标 ★本传；
    #                 ②《南齐书·卷二》卷首「封宋帝为汝阴王」是封废记事，曾被误当自称行（★本纪）。
    for _kw, _kind, _bk in (('拓跋焘', '本传', '06宋书文白'),
                            ('汝阴王', '本纪', '07南齐书文白')):
        _ev, _ = retrieve([_kw], top=8)
        _bad = [e for e in _ev if e.bio == _kind and _bk in (e.rel or '')]
        if _bad:
            print('FAIL  假锚点复现：%s → ★%s %s（应无此标注）' % (_kw, _kind, _bad[0].rel))
            fails += 1
        else:
            print('OK    假锚点拦截：%s 无 ★%s %s' % (_kw, _kind, _bk))
    # 5b3. 穷举口径回归（2026-09-24）。猜人物要的是「符合某条线索的人**全部**列出再逐
    #      一判否」，靠 `--top 0` 全量返回。旧实现有两处会把"穷举"变成**假排除**：
    #        ① 截断后不报"还有多少段没显示" → 「榜上没有」被读成「库里没有」；
    #        ② `--excerpt` 从**段首**截 → 长段的关键词连同主语一起被切掉（实测「轻佻
    #           --top 0 --excerpt 44」，104 块里约六成片段里根本没有「轻佻」二字），
    #           穷举等于白跑。现改为以关键词为中心开窗（hit_window）。
    _kw = '轻佻'
    _ev0, _st0 = retrieve([_kw], top=0)
    _sp = [(str(e.rel).replace('\\', '/'), e.l1, e.l2) for e in _ev0]
    _nlines = 0
    _miss = []
    for _p in sorted(DATA.rglob('*.md')):
        _rel = str(_p.relative_to(ROOT)).replace('\\', '/')
        for _i, _l in enumerate(_p.read_text('utf-8', errors='ignore').splitlines(), 1):
            if _kw not in _l:
                continue
            _nlines += 1
            if not any(_rel == r and a <= _i <= b for r, a, b in _sp):
                _miss.append('%s:%d' % (_rel, _i))
    if _miss:
        print('FAIL 穷举漏段：--top 0 未覆盖 %d 处「%s」（如 %s）' % (len(_miss), _kw, _miss[0]))
        fails += 1
    elif not _st0.get('total_ev') or _st0['total_ev'] != len(_ev0):
        print('FAIL 穷举计数失效：total_ev=%s 但 top=0 返回 %d 段'
              % (_st0.get('total_ev'), len(_ev0)))
        fails += 1
    else:
        print('OK    穷举口径：--top 0 → %d 块覆盖全库含「%s」的每一行（真值 %d 行，零漏）'
              % (len(_ev0), _kw, _nlines))
    # 只验"段内确有该词"的块：★本传／身份行锚点段内本来就没这个词，不算漏
    _vv = expand_terms(_kw)
    _long = [e.text for e in _ev0 if len(e.text) > 120 and any(v in e.text for v in _vv)]
    _badw = [t for t in _long if not any(v in hit_window(t, [_kw], 60) for v in _vv)]
    if _badw:
        print('FAIL --excerpt 开窗漏词：%d/%d 个长段窗口内没有「%s」（主语看不到）'
              % (len(_badw), len(_long), _kw))
        fails += 1
    else:
        print('OK    --excerpt 以词开窗：%d 个长段（>120 字）窗口内均含关键词，主语可见' % len(_long))
    # 5b4. 人物谜题双命令回归（谜／对照）：候选必须来自语料（非记忆）、硬锚对不上必须判否、
    #      无解必须给「无法确定」出口（用户明确要求：猜不出来就说不知道）。
    _r = riddle_core(['当并驱于中原'])
    _hc = next((c for c in _r['candidates'] if c[0] == '石勒'), None)
    if _hc:
        print('OK    谜：独有引语反查 → 候选含石勒（段内 %d·距 %d 字，取自语料人名表）'
              % (_hc[1], _hc[2]))
    else:
        print('FAIL  谜：候选未含石勒（got=%s）' % [c[0] for c in _r['candidates'][:6]])
        fails += 1
    _k1 = contrast_core('石勒', ['当并驱于中原'])
    if _k1['rows'] and _k1['rows'][0]['status'] == 'hit':
        print('OK    对照：石勒 ×「当并驱于中原」共现 ✔（%d 段）' % _k1['rows'][0]['segs'])
    else:
        print('FAIL  对照：石勒 ×「当并驱于中原」未判共现')
        fails += 1
    _k2 = contrast_core('拓跋珪', ['当并驱于中原'])
    if _k2['rows'] and _k2['rows'][0]['status'] != 'hit':
        print('OK    对照负例：拓跋珪 ×「当并驱于中原」判否——硬锚一票否决可用')
    else:
        print('FAIL  对照负例失效：错候选竟判共现')
        fails += 1
    _r2 = riddle_core(['当并驱于中原', '之乎者也zzq9'])
    if _r2['verdict'] == 'none':
        print('OK    谜无解出口：含零命中线索 → 答「无法确定」（不产出候选）')
    else:
        print('FAIL  谜无解出口失效：仍给候选 %s' % [c[0] for c in _r2['candidates'][:3]])
        fails += 1
    if not quick:
        # 5c. 通称改写端到端：乌台诗案 改按史文用词后必须真能取到原文
        _t = event_terms('乌台诗案')
        evs, _ = retrieve(_t or ['台狱'], top=6)
        if _t and any('逮赴台狱' in e.text for e in evs):
            print('OK    通称改写端到端：乌台诗案 → %s，命中原文「逮赴台狱」' % '／'.join(_t))
        else:
            print('FAIL  通称改写端到端失效：乌台诗案 改写后未取到「逮赴台狱」')
            fails += 1
        # 5d. 通称表右端词全量实测（一次多词扫描；仅对零命中者再用变体复核）
        words = sorted({w for v in EVENT_ALIASES.values() for w in v})
        seen_w = dict.fromkeys(words, False)
        for _tag, _p in iter_corpus('data'):
            try:
                _b = _p.read_bytes()
            except OSError:
                continue
            for w in words:
                if not seen_w[w] and w.encode('utf-8') in _b:
                    seen_w[w] = True
                    if all(seen_w.values()):
                        break
            if all(seen_w.values()):
                break
        still = [w for w in words if not seen_w[w] and not find_text(w, max_hits=1)]
        if still:
            print('FAIL  通称表右端词在本库零命中（换个词还是零命中）：%s' % '、'.join(still))
            fails += 1
        else:
            print('OK    通称表 %d 个史文用词均在本库实测存在' % len(words))
    print('# selftest %s（%.1fs）' % ('通过' if not fails else 'FAIL %d 项' % fails, time.time() - t0))
    return 0 if not fails else 1


# ---------------------------------------------------------------- locate（原 tools/q.py，并入为子命令）
PAGE_RE = re.compile(r'^###\s*第\s*(\d+)\s*页')
LOCATE_DOC = """\
locate — 只定位（原 tools/q.py，已并入本工具）：跨 data／ocr／index 三源找词，
给出可直接 Read 的「文件:行号」。**只定位、不给真伪判定**；引用仍须 Read 原文照录。

  python tools\\rag.py locate 张居正
  python tools\\rag.py locate 乌台诗案              # 后世通称自动加查史文用词
  python tools\\rag.py locate 均田 --count juan     # 只看分布：按卷统计命中次数
  python tools\\rag.py locate 张居正 --source ocr   # 只查机读稿（带 PDF 页码）
  python tools\\rag.py locate 魏徵 --source index   # 只查索引（字号/传记入口）
  python tools\\rag.py locate 食货 --book 24明史文白 --limit 5
  python tools\\rag.py locate 陈玉成 --first         # 起行定位：哪一卷、第几行起、有没有专传
                                      #（选项写在词前也一样认：`locate --first 陈玉成`）

选项：
  --source data|ocr|index|all   默认 data,ocr
  --book <关键词>               限定书/册（子串匹配）；逗号分隔可多值（任一命中即纳入）
  --limit N                     每源最多列 N 条，0 = 不限（默认 30）
  --context N                   片段字符数（默认 40）
  --files-only                  只列文件（去掉逐行输出）
  --count book|juan|source      只统计不列条目
  --first                       只报"从哪一卷哪一行开始读"＋体例判定（问某人记载在哪时用）

与 query 的分工：要证据块／有据无据判定／引文核验，用默认 query 与 verify；
locate 只在"我要知道这个词出现在哪些文件的哪些行"时用。
"""


def _loc_hit_lines(path, pats, skip_notes=False):
    """逐行找命中：返回 [(行号, 行文本, 页码或 None)]。

    全程 bytes 比对，**只有命中行才解码**（原先每行都先 decode 再判是否命中，
    索引源里 03 有 1.27 万行，等于白付一遍解码）。页标记 `### 第 N 页` 行
    仍须解码以推进页号，但这类行极少。
    """
    sep = '｜'.encode('utf-8')
    res = []
    page = None
    for i, raw in enumerate(path.read_bytes().split(b'\n'), 1):
        if raw.startswith(b'###'):
            m = PAGE_RE.match(raw.decode('utf-8', 'replace'))
            if m:
                page = int(m.group(1))
            continue
        if skip_notes and raw.startswith(b'>'):
            continue
        if skip_notes and not (sep in raw or raw.rstrip().endswith(b'.md')):
            continue          # 索引里的用法说明不是数据行
        if not any(p in raw for p in pats):
            continue          # 未命中行不解码
        res.append((i, raw.decode('utf-8', 'replace').rstrip('\r'), page))
    return res


_LEAD_RE = re.compile(r'^[\s>＞*　·|]+')
_BRK_RE = re.compile(r'^【[^】]{0,6}】')
# 开传式起句：**名字本身**（不给后拓字留宽度）紧跟「，」或「者，」，再接一小句就断句。
# 例：「魏徵，字玄成，钜鹿曲城人也。」「洪秀全，广东花县人。」「刘备者，…」
# ⚠ 不能只看"名字在段首"，也不能写成 `^[一-鿿]{2,6}，…`：实测「石达开窜湖南，鸿宾遣…」
#    会被当成开传（名字只有"石达开"三字，后面全是叙事）。
# ⚠ 陈玉成全库 112 处／32 卷**一处开传都没有**（附叙在洪秀全、李秀成诸传里）——
#    判宽了就会把"无专传"误报成"有专传"，而这正是本判据要回答的问题。
_OPEN_TAIL = re.compile(r'^者?，[^，。]{1,24}[。，]|^，字')


def is_bio_open(line, terms_v):
    """这一行是不是**开传式起句**：该词本身就是句首，后面立刻「，」／「者，」断句。"""
    s = _BRK_RE.sub('', _LEAD_RE.sub('', (line or '').strip())).strip()
    if s.startswith('#'):
        return False                        # 篇名行「## 海瑞」／目录行不算
    for v in terms_v:
        if v and s.startswith(v) and _OPEN_TAIL.match(s[len(v):]):
            return True
    return False


def _rel_disp(rel):
    """索引里的相对路径（`24明史文白\现代文\…`）→ 可直接 Read 的 `data\…` 写法。"""
    r = (rel or '').replace('/', '\\')
    return r if r.startswith('data') else 'data\\' + r


def person_index_entry(term):
    """02／03 里以 term 为**条目键**（行首整段相等）的行 → {'in02','in03','paths','zi'}。

    paths＝★本传／字号出处文件（相对路径，反斜杠写法照索引原样），zi＝02 记的"字"。
    查人物时最费时间的错误是：02/03 零命中 → 以为"号写错了／库里没这人" → 反复换词重试。
    实情往往是**此人无"字"且未入列传篇名**，本就不进这两份索引
    （实测 陈玉成：全库 112 处／32 卷，02/03 皆无条目——他附叙在洪秀全传里，无字无专传）。
    """
    out = {'in02': False, 'in03': False, 'paths': [], 'zi': []}
    head = (term + '｜').encode('utf-8')
    for name in BIO_INDEXES:
        p = ROOT / name
        if not p.exists():
            continue
        for line in p.read_bytes().split(b'\n'):
            if not line.startswith(head):
                continue
            segs = line.decode('utf-8', 'replace').rstrip('\r').split('｜')[1:]
            out['in02' if name.startswith('02') else 'in03'] = True
            for s in segs:
                s = s.strip()
                if s.startswith('★'):
                    out['paths'] += [x for x in s[1:].split('；') if x.endswith('.md')]
                elif s.startswith('字：') or s.startswith('字:'):
                    v = s[2:].strip()
                    if v and v not in out['zi']:
                        out['zi'].append(v)      # 跨书同一人会有多行（旧唐书／新唐书），去重
                elif s.endswith('.md') and name.startswith('02'):
                    out['paths'].append(s)       # 02 的"字"出处＝其本传所在卷
    seen, keep = set(), []
    for x in out['paths']:
        if x not in seen:
            seen.add(x)
            keep.append(x)
    out['paths'] = keep
    return out


def bio_open_line(rel, terms_v):
    """在一个文件里找**开传式起句**（「某某，字X，某地人也」／「某某者，…」）。

    返回 (行号, 原文行) 或 None。只认段首、且名字后立刻跟「，…。」或「者，」的行：
    实测清史稿「陈玉成率悍党踞黄梅，」也是段首，那是叙事不是开传——照段首判会把
    "无专传"误报成"有专传"。篇名行（「## 海瑞」）与目录行（「海瑞**〔何以尚〕…」）同理排除。
    """
    p = DATA / rel.replace('\\', '/')
    if not p.exists():
        return None
    for i, raw in enumerate(p.read_text('utf-8', 'replace').splitlines(), 1):
        if not is_bio_open(raw, terms_v):
            continue
        return (i, raw)
    return None


def _frag(text, terms_v, ctx=46):
    """命中行的片段：剥掉引用标记，从第一个变体前一点切起。"""
    s = _BRK_RE.sub('', _LEAD_RE.sub('', (text or '').strip())).strip()
    pos = [s.find(v) for v in terms_v if v and v in s]
    i = max(0, (min(pos) if pos else 0) - ctx // 4)
    return ('…' if i > 0 else '') + s[i:i + ctx] + ('…' if len(s) > i + ctx else '')


def loc_first(term, agg, terms_v, elapsed):
    """`locate --first` 的报告：这个人／这个词**从哪一卷、哪一行开始读**。

    一次给全四件事：① 02/03 有没有条目（没有≠号写错≠史无其人）；② 专传/附传的**开传行**；
    ③ 无专传时给提及最密的卷与**首见行**；④ 编年首见与各卷分布。
    """
    rows = [(k, v) for k, v in agg.items() if k[0] == 'data'] or list(agg.items())
    tot = sum(v['n'] for _k, v in rows)
    print('# rag locate --first 「%s」 → 变体 %s｜命中 %d 处 / %d 卷册（%.2fs）'
          % (term, '、'.join(terms_v), tot, len(rows), elapsed))
    ent = person_index_entry(term)
    if ent['in02'] or ent['in03']:
        which = '＋'.join([n.replace('.md', '') for n, ok in
                           (('02-人物字号索引', ent['in02']), ('03-人物出现索引', ent['in03'])) if ok])
        print('📍 索引：%s 有此条目%s' % (which, '（字：%s）' % '／'.join(ent['zi']) if ent['zi'] else ''))
    else:
        print('📍 索引：02／03 **均未收「%s」这一条**。' % term)
        print('   这两份只收「原文里有『X，字Y』」或「名字进了列传篇名」的人；无字、又只在')
        print('   他人传里被提及者**本就不进表**——不是号写错，更不是史无其人。**别再换词重试**，')
        print('   直接照下面的卷／行 Read。')
        print('   · 无字无专传的**臣民**要长期可查 → 登记 tools/extra_names.md 后重跑 03；')
        print('   · **帝王**按体例本就不进 02/03，看下面的 ⭐ 帝王本纪锚点（若没有该行＝本库缺其本纪，')
        print('     2026-09-25 实测：《三国志》卷四三少帝纪正文在库中被后妃传第五顶替，曹芳／曹髦／')
        print('     曹奂即属此列，07 里以「⚠语料缺文补录」行给出替代锚点）。')
    # 帝王锚点：locate 原先不查 07，于是**帝王**永远只看到"02/03 均未收、无开传式起句"，
    # 像是查无此人（用户实测报「无法命中曹髦」）。这里补上 07 的行（无命中则不打印）。
    for _ln in emperor_note([term], prefix=''):
        print(_ln)
    if not rows:
        print('⛔ 全库零命中（异体／繁简／避讳已并查）：本库确实没有这个词的记载。')
        return 0
    # ① 有专传／字出处 → 到那一卷找开传行；没有索引条目时，退回"全库扫到的开传式行"
    hit_open = None
    for rel in ent['paths']:
        got = bio_open_line(rel, terms_v)
        if got:
            hit_open = (rel, got[0], got[1])
            break
    if not hit_open:
        cands = [(v['open'][1], v['open'][0], v['open'][2]) for _k, v in rows if v.get('open')]
        if cands:
            cands.sort(key=lambda x: (x[0], x[1]))
            hit_open = cands[0]
    start = ('', 0)
    if hit_open:
        rel, ln, raw = hit_open
        start = (rel, ln)
        yi = raw.lstrip().startswith(('>', '＞'))
        print('📍 起读（专传开传行）：%s:%d' % (_rel_disp(rel), ln))
        print('     「%s」%s' % (_frag(raw, terms_v, 56), '　⚠ 该行是译文，禁照录' if yi else ''))
        print('     Read 该文件从第 %d 行起；引用只照录 [原文] 段。' % ln)
    else:
        # 起读卷**先排掉本纪**：本纪按年记事、每卷都点名，提及数最大却不等于"记载他最多"
        nonbj = [kv for kv in rows if not kv[1].get('benji')]
        pool = nonbj or rows
        main = max(pool, key=lambda kv: (kv[1]['n'], -kv[1]['seq']))
        mv = main[1]
        ln, rel, raw = mv['first']
        start = (rel, ln)
        yi = (raw or '').lstrip().startswith(('>', '＞'))
        print('📍 起读（无专传 → 列传里提及最密的卷）：%s ｜%d 处｜首见 %s:%d'
              % (main[0][1], mv['n'], rel, ln))
        print('     %s%s' % (_frag(raw, terms_v), '　⚠ 该行是译文，禁照录' if yi else ''))
        print('   ⚠ 全库 %d 处里**没有一处开传式起句**（名字后面直接跟动词／叙事）：'
              '此人无专传，只在他人纪传里被提及——按上行号往下读即可，不必再找"某某传"。' % tot)
        print('     Read 该文件从第 %d 行起；引用只照录 [原文] 段。' % ln)
    earliest = min(rows, key=lambda kv: (kv[1]['book'], kv[1]['seq']))
    el, erel, eraw = earliest[1]['first']
    if (erel, el) != start:
        print('📍 编年首见：%s ｜%s:%d（按年记事，非传记）' % (earliest[0][1], erel, el))
        print('     %s%s' % (_frag(eraw, terms_v),
                             '　⚠ 译文' if (eraw or '').lstrip().startswith(('>', '＞')) else ''))
    per = sorted(rows, key=lambda kv: (kv[1]['book'], kv[1]['seq']))
    show = per[:10]
    print('📍 各卷分布（按书·卷序）：%s'
          % '｜'.join('%s %d' % (k[1].split('·', 1)[-1], v['n']) for (k, v) in show)
          + ('｜…另 %d 卷' % (len(per) - len(show)) if len(per) > len(show) else ''))
    return 0


def cmd_locate(argv):
    args = list(argv)
    if not args or args[0] in ('-h', '--help'):
        print(LOCATE_DOC)
        return 0
    term = args[0] if not args[0].startswith('--') else None
    rest = args[1:] if term is not None else list(args)

    # 选项白名单：未知/拼错的选项原先被**默默忽略**（`--limt 2` 会静默用默认 30，
    # 让人以为设置生效了）。宁可报错——"看起来正常"比报错危险。
    LOC_KNOWN = ('--source', '--book', '--limit', '--context', '--files-only', '--count',
                 '--first')
    # 选项写在查询词**前面**时（`locate --first 曹髦`）：原先无条件取 `args[0]` 当查询词，
    # 于是拿字面量 '--first' 去全库搜 → 0 命中 → 看起来像"库里查无此人"。
    # 这类假阴性正是逼人凭记忆瞎编的那一环，故改成选项与词的位置无关。
    if term is None:
        _flagonly = ('--files-only', '--first')
        _i = 0
        while _i < len(rest):
            a = rest[_i]
            if not a.startswith('--'):
                term = rest.pop(_i)
                break
            _i += 1
            if a not in _flagonly and _i < len(rest) and not rest[_i].startswith('--'):
                _i += 1                      # 跳过该选项的取值
        if term is None:
            print('× 缺查询词：`locate <名> [--first]`（选项写在前后都可以）。')
            return 2
    for _a in rest:
        if _a.startswith('--') and _a not in LOC_KNOWN:
            _near = difflib.get_close_matches(_a, LOC_KNOWN, n=1)
            print('× 未知选项 %s。可用：%s' % (_a, ' '.join(LOC_KNOWN)))
            if _near:
                print('  是想写 %s 吗？' % _near[0])
            return 2

    def opt(name, default=None, cast=str):
        if name in rest:
            i = rest.index(name)
            return cast(rest[i + 1]) if i + 1 < len(rest) and not rest[i + 1].startswith('--') else True
        return default

    def opt_int(name, default):
        """取整数选项。选项后缺参数时 opt() 返回 True——直接 int(True) 会静默变成 1。"""
        v = opt(name, default)
        if isinstance(v, bool):
            sys.exit('× %s 缺参数（应写 %s <数字>）' % (name, name))
        try:
            n = int(v)
        except (TypeError, ValueError):
            sys.exit('× %s 需整数，收到 %r' % (name, v))
        if n < 0:
            sys.exit('× %s 不能为负' % name)
        return n

    srcs = opt('--source', 'data,ocr')
    srcs = {'all': ['data', 'ocr', 'index']}.get(srcs, str(srcs).split(','))
    # `--book` 支持逗号分隔多值（英文/中文逗号都收）：原先只按单个子串比对，
    # `--book 20宋史文白,24明史文白` 去匹配一个不存在的长串 → 扫 0 文件 →
    # 触发下面的"范围为空"护栏（那不是"库里没有"，是书名写错了）。
    _book_raw = opt('--book')
    book = ([b.strip() for b in str(_book_raw).replace('，', ',').split(',') if b.strip()]
            if _book_raw else None)
    limit = opt_int('--limit', 30)
    ctx = opt_int('--context', 40)
    files_only = '--files-only' in rest
    count_by = opt('--count', None)
    # `--first`：**起行定位**——"这个人／词从哪一卷哪一行开始读"。给主载卷、首现行号、
    # 以及**有没有开传式起句**（无专传的人常常在索引里查不到，模型便反复换词重试；
    # 一次 `--first` 就把"卷＋行＋体例"三件事说完）。
    first_mode = '--first' in rest
    if first_mode and count_by:
        print('× --first 与 --count 不能同用：--first 出"从哪一卷哪一行读起"，--count 出分布统计。')
        return 2

    # 变体走本文件的 expand_terms：比原 q.py 自带的 variants() 多出 RAG_VARIANTS
    # 与 tools/aliases.md 两组扩展（原先两个入口召回不一致，就差在这里）。
    terms_v = expand_terms(term)
    alias_note = event_terms(term)
    if alias_note:
        terms_v = sorted(set(terms_v) | set(alias_note), key=len, reverse=True)
    pats = [p.encode('utf-8') for p in terms_v]

    t0 = time.time()
    rows, cnt, seen_files, book_scope = [], {}, set(), {}
    agg = {}        # --first：{(源, 卷) → 计数／首现行／开传行}
    n_hits = 0        # 真实命中处数；rows 里混着「另有 N 处」提示行，不能拿 len(rows) 当命中数
    n_in_scope = 0    # --book 限定后**实际纳入范围的文件数**（0 ⇒ 书名写错，不是"库里没有"）
    for src in srcs:
        n_src = 0
        for tag, path in iter_corpus(src):
            rel = str(path.relative_to(ROOT))
            if book:
                _mb = [b for b in book if b in rel]
                if not _mb:
                    continue
                for b in _mb:      # 逐值计数：抓"多值里某一个写错"
                    book_scope[b] = book_scope.get(b, 0) + 1
            n_in_scope += 1
            hits = _loc_hit_lines(path, pats, skip_notes=(src == 'index'))
            if not hits:
                continue
            seen_files.add(rel)
            if src == 'data':
                relp = path.relative_to(DATA).parts
                bookkey = relp[0]                         # 如 24明史文白 / 00资治通鉴
                sub = '' if (len(relp) > 1 and relp[1] == '现代文') or len(relp) == 2 else relp[1] + '·'
                juan = _JUAN_RE.search(path.stem)
                where = '%s·%s%s' % (bookkey, sub, juan.group(0) if juan else path.stem)
            elif src.startswith('ocr'):
                bookkey = where = '%s册' % path.stem.split('_')[0]
            else:
                bookkey = where = path.stem
            for ln, text, page in hits:
                n_src += 1
                if first_mode:
                    a = agg.get((src, where))
                    if a is None:
                        _m = re.match(r'^(\d+)', path.stem) or re.search(r'卷(\d+)', path.stem)
                        a = agg[(src, where)] = {
                            'n': 0, 'book': bookkey, 'seq': int(_m.group(1)) if _m else 9999,
                            'benji': bool(_BENJI_HEAD_RE.search(path.stem)),
                            'first': None, 'open': None}
                    a['n'] += 1
                    if a['first'] is None:
                        a['first'] = (ln, rel, text)
                    # 开传式起句（「某某，字X，某地人也」）＝这一卷有他的专传；只段首提到不算
                    if a['open'] is None and is_bio_open(text, terms_v):
                        a['open'] = (ln, rel, text)
                    continue
                if count_by:
                    key = where if count_by == 'juan' else (tag if count_by == 'source' else bookkey)
                    cnt[key] = cnt.get(key, 0) + 1
                    continue
                if files_only:
                    continue
                if limit and n_src > limit:
                    continue
                frag = text.strip()
                if len(frag) > ctx:
                    pos = [frag.find(v) for v in terms_v]
                    pos = [x for x in pos if x >= 0] or [0]
                    i = max(0, min(pos) - ctx // 3)
                    frag = ('…' if i > 0 else '') + frag[i:i + ctx] + '…'
                pg = '第%d页·' % page if page else ''
                rows.append('%s｜%s｜%s%s:%d｜%s' % (tag, where, pg, rel, ln, frag))
        n_hits += n_src
        if not count_by and not files_only and not first_mode and limit and n_src > limit:
            rows.append('  …（%s 另有 %d 处，--limit 0 看全部）' % (src, n_src - limit))

    # ⛔ 范围为空 ⇒ 不能只说"命中 0 处"：那是**书名写错**，不是库里没有
    if book and len(book) > 1:
        miss = [b for b in book if book_scope.get(b, 0) == 0]
        if miss:
            print('× --book 多值里有 %d 个没匹配到任何文件：%s' % (len(miss), '、'.join(miss)))
            print('  已纳入：%s'
                  % '、'.join('%s(%d)' % (b, book_scope.get(b, 0)) for b in book
                              if book_scope.get(b, 0)))
            print('  可用书文件夹名：%s' % '、'.join(sorted(BOOKS)))
            print('  ⚠ 范围已被静默缩窄——"命中 0 处"不等于本库无此记载。')
            if 'data' in srcs:
                return 2
    if book and n_in_scope == 0:
        print('× --book「%s」没有匹配到任何文件——书名可能写错了。' % '、'.join(book))
        print('   可用书文件夹名：%s' % '、'.join(sorted(BOOKS)))
        print('   ⚠ 这**不是**"本库无此记载"，只是你限定的范围是空的。')
        return 2

    if first_mode:
        if alias_note:
            print('# 💡 通称改写：「%s」是后世通称，本库原文不用此词；已加查史文用词 %s'
                  '（对照表 tools/aliases_events.md）' % (term, '、'.join(alias_note)))
        loc_first(term, agg, terms_v, time.time() - t0)
        return 0
    print('# 查询「%s」→ 变体 %s' % (term, '、'.join(terms_v)))
    if alias_note:
        print('# 💡 通称改写：「%s」是后世通称，本库原文不用此词；已加查史文用词 %s'
              '（对照表 tools/aliases_events.md）' % (term, '、'.join(alias_note)))
    if count_by:
        for k, v in sorted(cnt.items(), key=lambda x: -x[1]):
            print('%6d  %s' % (v, k))
        print('# 合计 %d 处（%.2fs）' % (sum(cnt.values()), time.time() - t0))
    elif files_only:
        for f in sorted(seen_files):
            print(f)
        print('# 命中文件 %d 个（%.2fs）' % (len(seen_files), time.time() - t0))
    else:
        for r in rows:
            print(r)
        print('# 命中 %d 处、%d 个文件（%.2fs）' % (n_hits, len(seen_files), time.time() - t0))
    return 0


# ---------------------------------------------------------------- main
def cmd_emperor(argv):
    """`rag.py emperor <关键词>…`：只查 07-帝王本纪索引，**不扫语料**（毫秒级）。

    关键词＝本名／庙号／朝代连称／年号任一。零命中时给出三种解释，防止把"表里查不到"
    当成"史书没这个人"。
    """
    terms, cap = [], 8
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == '--top' and i + 1 < len(argv):
            try:
                cap = max(1, int(argv[i + 1]))
            except ValueError:
                print('--top 必须是正整数')
                return 2
            i += 2
            continue
        if a.startswith('--'):
            print('× emperor 只认 --top，未知选项：%s' % a)
            return 2
        terms.extend(a.split())
        i += 1
    if not terms:
        print('用法：python tools\\rag.py emperor <本名|庙号|朝代连称|年号>…')
        print('例：emperor 杨坚 ／ emperor 熙宁 ／ emperor 宋太祖 ／ emperor 元嘉 泰始')
        return 2
    if not emperor_rows():
        print('× 没有可读的 %s —— 先跑 python tools\\build_emperor_index.py 生成。'
              % EMPEROR_INDEX)
        return 2
    rows, total = emperor_hits(terms, cap=cap)
    print('# rag emperor 「%s」 → %s（全表 %d 卷帝纪）'
          % (' '.join(terms), EMPEROR_INDEX, len(emperor_rows())))
    if not rows:
        print('× 本表无「%s」的检索键。三种可能，按顺序排除：' % ' '.join(terms))
        print('   ① 此人**无本纪**：追尊帝、僭伪政权（晋书作"载记"）、三国蜀吴之主'
              '（三国志作"传"，实测「刘备」「孙权」都不在本表）→ 走 05-卷名总表＋列传／载记；')
        print('   ② 用的是**通称或后世叫法**（「康熙」「汉武帝」）→ 换庙号／讳／年号再查；')
        print('   ③ 该卷卷首抽不到自称行（语料缺文言，见该表纪律④⑥）→ 直接 Grep 该书文件夹。')
        print('   ⛔ 「本表无」≠「史无其人」，不得据此回答"正史未载"。')
        return 1
    print('  命中 %d 卷%s：' % (total, '（列前 %d）' % len(rows) if total > len(rows) else ''))
    for n, r in enumerate(rows, 1):
        print('[%d] %s｜%s' % (n, r['label'], r['cite']))
        print('     年号：%s' % ('、'.join(r['eras']) or '本表未抽到'))
        print('     自称行：%s' % r['ident'])
        print('     文件：%s' % r['rel'])
        if r['flag']:
            print('     %s' % r['flag'])
        if r['ident'].startswith('译'):
            print('     ⚠ 卷首只有白话：这一行**不能当引文照录**，须 Read 文件取 [原文] 段')
        if emperor_via(r, terms) == {'era'}:
            print('     ⚠ 仅由**年号**命中：年号跨帝沿用（熙宁始于神宗而出现在《哲宗》本纪），'
                  '本卷之帝≠始建年号之帝，须回正文定年')
    print('=== 判定：定位成功（引用前仍须 Read 上列文件逐字照录 [原文]） ===')
    return 0


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    argv = sys.argv[1:]
    if not argv or argv[0] in ('-h', '--help'):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == 'query':
        return cmd_query(rest)
    if cmd == 'verify':
        return cmd_verify(rest)
    if cmd == 'stat':
        return cmd_stat(rest)
    if cmd == 'selftest':
        return cmd_selftest(rest)
    if cmd == 'locate':
        return cmd_locate(rest)
    if cmd == 'emperor':
        return cmd_emperor(rest)
    if cmd in ('谜', 'riddle'):
        return cmd_riddle(rest)
    if cmd in ('对照', 'contrast'):
        return cmd_contrast(rest)
    return cmd_query(argv)


if __name__ == '__main__':
    sys.exit(main())
