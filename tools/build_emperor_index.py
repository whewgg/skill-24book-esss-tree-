# -*- coding: utf-8 -*-
"""
build_emperor_index.py — 生成 07-帝王本纪索引.md

**为什么要有这一份**：02/03 两份人物索引按列传式首句「X，字Y」抽名，而帝王本纪的卷首是
「太祖武皇帝，沛国谯人也，姓曹，讳操」这种**自称行**——只写「讳某」，从不写连姓全名。
于是历代帝王**一个都不进** 02/03，「猜皇帝／定位某帝」这条路上连出三类事故：
  · 检索「赵匡胤」→ 命中的全是《通鉴》臣僚传里顺带提到的句子，本纪钉不住；
  · 检索「刘恒」→ 02/03 零命中，被当成"查无此人"；
  · 检索年号「熙宁」→ `locate --count book` 只能数出「宋史 1065 处」，说不出这是宋神宗。
本脚本把每卷帝纪的卷首自称行机械拆成 `名／号／年号` 三类检索键，产出一张可直接 Grep 的
帝王总表；`rag.py locate`（--source 含 index）与 `rag.py <关键词>` 都会读它并给出本纪锚点。

用法: python tools/build_emperor_index.py
"""
import re
import sys
import collections

from _lib import ROOT                      # noqa: E402
from rag import iter_corpus, locate, chunks_labeled   # noqa: E402

OUT = ROOT / '07-帝王本纪索引.md'
VOL_TABLE = ROOT / '05-卷名总表.md'

# ---------------------------------------------------------------- 卷首自称行
# 实测形态四种：「太祖武皇帝，沛国谯人也」／「世祖光武皇帝讳秀，字文叔」／
# 「宣皇帝名懿，字仲达」／「孝景皇帝者，孝文之中子也」（译文用「是」）。
# ① 前缀字数下限必须是 **0**：「武皇帝讳炎」「景皇帝名师」这类单字前缀实测存在，
#    写成 {1,40} 会把整卷晋书帝纪漏掉；
# ② IDENT_FULL 与 IDENT 同形，多带一个捕获组＝**自称短语**（直到「皇帝／帝者」为止），
#    号／谥由它剥出来，不再用惰性 {1,20} 截——「秦始皇帝者」会被惰性切成「秦始」。
# ③「皇帝」后面还可以**直接跟本名**：「孝昭皇帝演，字延安」（北齐书）「懿宗昭圣恭惠孝皇帝漼，」
#    （旧唐书）「废帝海陵庶人亮，字元功」（金史）——旧写法只认句读／名讳，这几十卷整卷漏收；
# ④「皇帝」后面先跟「姓」：「齐高祖神武皇帝姓高氏，讳欢」（北史）「高祖文皇帝，姓杨氏，讳坚」
#    少这一味，隋书／北史／旧唐书高祖全列出不来。
IDENT = re.compile(r'^([^\s，。、；：「」『』（）()《》"\']{0,40}?(?:皇帝|帝者))'
                   r'(?:者)?(?:[，、名讳是姓]|([一-鿿]{1,5})(?=[，、字]))')
IDENT_FULL = IDENT
# **无「皇帝」二字**的自称行（本表 ⑥ 条）：「高祖，沛丰邑中阳里人，姓刘氏」（史记／汉书）
# 「吕太后者，高祖微时妃也」「郁林王昭业字元尚」「末帝，讳瑱」「卫绍王讳永济」——
# 号＋本名直接连写，中间没有句读。只允许**行首**，且号必须落在帝／后／王／主／公／祖／宗上。
# ⚠ 号里**不得有「的」**：白话卷首（本表 ⑥ 条的「译」行）会写出「殷的始祖契，母亲名叫简狄」
# 「秦国的祖先，是帝颛顼的后裔」（实测 01 史记卷三／卷五、15 北史卷一、22 金史卷一）。
# 放开「的」就会抽出「殷的始祖」「金朝的祖」这类不存在的号当检索键。
IDENT2 = re.compile(r'^((?:(?!的)[一-鿿]){1,7}?(?:太后|皇后|帝|王|主|公|侯|祖|宗))'
                    r'(?:者)?(?:[，、名讳是姓字]|([一-鿿]{1,4})(?=[，、字]))')
# IDENT2 只在**确实是本纪／帝纪的文件**上启用；「本纪第六」这类卷题不点名，放开就会把
# 后妃纪、志、表、序里的「某某帝／某某后」当成帝王（实测假阳性：「是岁，皇太后诏曰…」）。
BENJI_OK = re.compile(r'本纪|帝纪|帝史|纪第|[一-鿿]{1,4}纪[一二三四五六七八九十]{1,3}$')
BENJI_BAD = re.compile(r'皇后|后妃|外戚|列传|本传|志第|表第|序|叙|目录|载记|世家|外纪|论|赞')
# 「名曰轩辕」「名为政」「名叫简狄」——名后先允许一个「曰／为／叫」，否则会把「曰轩辕」
# 「叫简狄」当成整截本名（实测 01 史记卷一／卷三）。
# 捕获到**「字」之前**为止：「讳道成字绍伯」若只按句读切，会连名带字吞成「道成字绍伯」。
# 「名讳忽必烈」＝「名」＋「讳」连着写（实测新元史卷七），不先把「名讳」当一个词就会抽出「讳忽必烈」。
# ⛔ 「名」后那个「曰／为／叫」是**可选**的：文言「少帝，名重贵」「皇帝名治」「宣统皇帝名溥仪」
#    是旧五代史／新唐书／清史稿的通行自称式，砍掉裸「名」会把 11 个真本名键一起砍掉
#    （实测：重贵／李治／司马睿／司马昱／德宗／嗣源／祁钰／翊钧／蒙哥／也孙铁木儿／溥仪），
#    少帝那一行还会改抽到行内后文的「考讳敬儒」（那是他爹）。
#    绳下类假名（「以刑名绳下，」＝动宾短语非自称）单独用**左邻守卫**拦：(?<!刑)。
GIVEN = re.compile(r'(?:名讳|讳|(?<!刑)名[曰为叫]?)([一-鿿]{1,8}?)(?=[，、。；\n字]|$)')
BORN = re.compile(r'(?:本名|本讳|初名|初讳|更讳|又讳|一名|小名|小字|小讳)'
                  r'([一-鿿]{1,8})(?=[，、。；\n]|$)')
# 姓氏两种写法：「姓杨氏，讳坚」／「姓曹，讳操」。**不许**写成 `姓(X){1,3}?氏?[，、]`——
# 那个可选氏会把「姓高名欢，」一路吞到逗号、得出「高名欢」（实测北齐书太祖本纪）。
# {1,4}：复姓要装得下（实测 26 清史稿卷一「姓爱新觉罗氏，讳努尔哈齐」，上限 3 时抽不到姓，
# 于是「爱新觉罗努尔哈齐」这类键全缺）。
SUR_CAND = (re.compile(r'姓([一-鿿]{1,4})氏'),
            re.compile(r'(?<!名)姓([^，、。氏\s]{1,2})(?=[，、])'))
FULLNAME = re.compile(r'姓([一-鿿])名([一-鿿]{1,3})(?=[，、。；\n]|$)')
STOP_SUR = set('也其之者氏名字年月此是')
# 庙号只取**行首**（「太祖武…」→太祖，「理宗建道…」→理宗）。全句扫描会把
# 「孝惠皇帝，高祖太子也」里的「高祖」当成本卷之号——那是他爹。
TEMPLE = re.compile(r'^([一-鿿]{1,2}[祖宗])')
# 「讳某」首字不得是虚词（实测假阳性：曰轩辕、且汉）
STOP_GIVEN_FIRST = set('曰为是之其此的字名讳年号位')

# ---------------------------------------------------------------- 年号两段式
# ① 全库播种：凡「X元年」取 2／3／4 字前缀窗口，按「跨书数 ≥2 或 总次数 ≥8」定词表；
#    ② 各卷回标：词表＋严格左边界（前一字不得是汉字），拦掉「唐天成／平兴国／中祥符」
#       这类错位切片；整卷抽空时退回标点左边界（见 make_era_re / find_eras）。
STOP_LAST = set('十百千万年月份帝王公侯后氏时世者所焉亦主公子孙父子兄姐弟妻妾位殿宫府州郡县官至也'
                '祖宗制称')
# ⚠ 这一串只能收**绝不会出现在真年号里**的字：曾加过「圣武睿哲英孝穆惠庄僖」和「本」，
# 结果把「建武／武德／孝建／本始／圣历」这批真年号一起从词表里删了（实测）。
# 谥号／庙号字样改由 `era_junk` 在回标时拦（帝、宗、晋、魏…＋ ERA_KEEP 兜住「后元／汉安／天汉」）。
STOP_ANY = set('之已而则其皆凡以于为与及或亦每不今既将且又复并是在诏追只据准作焉盖号为'
               '自从')
STOP_FISCAL = set('赋租田税役给复免宽丁夫科派赈济蠲豁省屯戍')

# 书 → 朝代（朝代只作**检索提示**，跨朝书以篇名为准；三国志按「魏书／蜀书／吴书」细分）
DYN = {
    '01史记文白': '汉', '02汉书文白': '西汉', '03后汉书文白': '东汉',
    '05晋书文白': '晋', '06宋书文白': '刘宋', '07南齐书文白': '南齐',
    '08梁书文白': '萧梁', '09陈书文白': '陈', '10魏书文白': '北魏',
    '11北齐书文白': '北齐', '12周书文白': '北周', '13隋书文白': '隋',
    '14南史文白': '南朝', '15北史文白': '北朝', '16旧唐书文白': '唐',
    '17新唐书文白': '唐', '18旧五代史': '五代', '19新五代史': '五代',
    '20宋史文白': '宋', '21辽史': '辽', '22金史文白': '金', '23元史': '元',
    '24明史文白': '明', '25新元史文白': '元', '26清史稿文白': '清',
}

# 自称行里没有「姓」字时补的**国姓**（只列国姓唯一的书；见 parse() 里的说明）。
# 魏书给两姓：孝文帝太和十九年改拓跋为元，「拓跋宏」「元宏」两种叫法都在用。
GIVEN_SUR = {
    '02汉书文白': ('刘',), '03后汉书文白': ('刘',), '04三国志文白': ('曹',),
    '05晋书文白': ('司马',), '06宋书文白': ('刘',), '07南齐书文白': ('萧',),
    '08梁书文白': ('萧',), '09陈书文白': ('陈',), '10魏书文白': ('拓跋', '元'),
    '11北齐书文白': ('高',), '12周书文白': ('宇文',), '13隋书文白': ('杨',),
    '16旧唐书文白': ('李',), '17新唐书文白': ('李',), '20宋史文白': ('赵',),
    '21辽史': ('耶律',), '22金史文白': ('完颜',), '24明史文白': ('朱',),
}


def era_vocab():
    """全库年号词表 → {年号: 出现次数}。

    为什么按 2／3／4 字三个窗口都记一笔：正文常写「…我朝康熙元年…」，只取贪婪 4 字窗口
    会切成「朝康熙」，于是「康熙」这类年号永远进不了词表（实测清／金／北宋年号大面积缺席）。
    反过来，同一处「晋太康元年」会同时播种「晋太康」「太康」两条——所以词表**留频次**，
    由 `find_eras` 在同一位置挑高频那条（见下）。
    """
    books, hits = collections.defaultdict(set), collections.Counter()
    for _tag, path in iter_corpus('data'):
        parts = path.parts
        book = parts[-3] if len(parts) > 3 else path.stem
        try:
            text = path.read_text('utf-8', errors='replace')
        except OSError:
            continue
        for w in (2, 3, 4):
            pat = re.compile(r'([一-鿿]{%d})元年' % w)
            for m in pat.finditer(text):
                e = m.group(1)
                if e[-1] in STOP_LAST or (set(e) & (STOP_ANY | STOP_FISCAL)):
                    continue
                books[e].add(book)
                hits[e] += 1
    return {e: hits[e] for e in books if len(books[e]) >= 2 or hits[e] >= 8}


ERA_SEEK = re.compile(r'([一-鿿]{1,4})(?:元|[一二三四五六七八九十]{1,3})年')
# 论赞／史论／注释尾块：这里的「X年」是追谥、修史、后世评论的年月，不是本卷在位年号。
# 实测：明史卷四的「乾隆元年上尊谥曰恭皇帝」、卷一的「正统…崇祯十七年」全在这类尾块里。
TAIL_RE = re.compile(r'太史公曰|史臣曰|赞曰|论曰|述曰|本纪末|【注释】|【解析】|【题解】|【译文】')


def body_only(text):
    m = TAIL_RE.search(text)
    return text[:m.start()] if m else text


# 实测「真年号＋朝代／庙号字样」的错位切片（晋太康、隋开皇、唐广明、宋大定、孝宗弘治、
# 武帝元封、安帝隆安、是岁太清、宋自永初、梁中大通…）。这些前缀字恰恰是**汉字**，
# 左边界拦不住；也不能整条从词表里删——同一串在别处就是干净的「太康元年」。
# 所以只在**回标排序**时降档：同一位置上有干净候选就不看带脏字的候选。
# 注意 汉／后 不能进脏字表（真年号「汉安」「天汉」「后元」），故用 ERA_KEEP 兜住。
ERA_JUNK_IN = set('晋魏唐宋隋秦金辽齐梁陈周清帝宗主朝')
ERA_JUNK_HEAD = set('晋魏唐宋隋秦金辽齐梁陈周清我是此伊夫盖自从')
ERA_JUNK_PRE = ('后唐', '后汉', '后周', '后晋', '后秦', '后燕', '后金', '后梁', '前秦', '前燕',
                '前赵', '后赵', '南朝', '北朝', '我朝', '国朝', '皇朝', '胜国')
ERA_KEEP = {'后元', '汉安', '天汉'}


def era_junk(e):
    if e in ERA_KEEP:
        return False
    return (e[0] in ERA_JUNK_HEAD or e.startswith(ERA_JUNK_PRE)
            or bool(set(e) & ERA_JUNK_IN))


def find_eras(text, vocab, hay='', cap=8):
    """本卷年号：同一处只认**全库频次最高**的那个候选（并列取更长的）。

    为什么不用左边界拦错位：实测「唐天成元年」「晋太康元年」「成宗大德三年」这类
    带前朝／庙号前缀的写法，前一字恰恰就是汉字，标点左边界拦不住它（拦了会把
    「天成／太康／大德」一起丢掉）。改成按候选比频次：「天成」全库频次远高于
    「唐天成」，于是自然选中真年号；「大中祥符」同理胜过「中祥符」。
    同频次档里再比一档「不含朝代／庙号字样」（见 `era_junk`）：「元帝初」「帝初」
    两条都脏时整处跳过——宁缺不滥，真年号「初元」会在「初元二年」那处被抽到。
    抽取范围先经 `body_only` 剥掉论赞尾块：实测明史卷一因此不再把「崇祯十七年」
    （赞曰里追述亡国）算进太祖年号。
    `hay`＝这一卷自己的书名＋篇名＋路径：真年号前面的「大X／皇X／胜国」是**它朝纪年**
    （实测《明史·恭闵帝本纪》末尾「大清乾隆元年，诏廷臣集议，追谥…」给建文帝挂上了
    乾隆；《新唐书·礼乐志》「胜国东平王……大周长寿命」给德宗挂上武周年号），
    这个朝代字若不出现本卷自己的名字里，整处跳过。
    """
    out, seen = [], set()
    for m in ERA_SEEK.finditer(body_only(text)):
        run = m.group(1)
        cands = [run[-k:] for k in (4, 3, 2) if k <= len(run) and run[-k:] in vocab]
        cands = [c for c in cands if not era_junk(c)]
        if not cands:
            continue
        e = max(cands, key=lambda x: (vocab[x], len(x)))
        pre = run[:len(run) - len(e)]
        mm = re.search(r'(?:大|皇)([一-鿿])$', pre)
        if (mm and mm.group(1) not in hay) or re.search(r'(?:胜国|胜朝)$', pre):
            continue
        if e not in seen:
            seen.add(e)
            out.append(e)
        if len(out) >= cap:
            break
    return out


def clean(s):
    return re.sub(r'[\s　]+', '', s or '')


def head_lines(fp, n=8):
    out = []
    try:
        with fp.open(encoding='utf-8', errors='replace') as fh:
            for i, ln in enumerate(fh):
                if i >= n:
                    break
                out.append(ln.rstrip())
    except OSError:
        pass
    return out


def pian_from(head):
    """从卷题剥实质篇名：去书名字与版本标记（「文白对照／译文／白话」）。"""
    s = re.sub(r'^《?[一-鿿]{1,8}》?[·＿_ ]*', '', head or '')
    s = re.sub(r'文白对照|对照|现代文|译文|白话|文白|翻译|现代汉语', '', s)
    return clean(s).strip('·＿_ -')


LABEL_RE = re.compile(r'^[【\[·\-—（(]?(?:原文|译文|白话|待核|OCR稿|注|注释|题解|解题|导读'
                      r'|赏析|对照|翻译|附录|备考)[】\]）)]?$')


def title_of(lines):
    """卷内**篇名行**（「武帝纪第一」「仁宗一」「## 恭闵帝」）。

    实测四种排版：明史用 `## 恭闵帝`；三国志／宋史是裸行「武帝纪第一」「仁宗一」；
    晋书用 `## 武帝`；也有文件根本没有篇名行（只有 `# 卷三·帝纪第三`）。
    旧写法把第一条 `#` 卷题当成了篇名，明史所有行都显示「卷四·本纪第四译文」，
    于是「文件名作成祖本纪、正文实为恭闵帝」的错位被彻底盖住（实测 24 明史文件名的
    篇名整体错后一卷，见本表 ⑤ 条）。篇名行不带句读，正文段落一律跳过。
    """
    for l in lines:
        raw = l.strip()
        if not raw or raw.startswith('# '):
            continue
        if raw.startswith('##'):
            s = clean(raw.lstrip('#'))
            if (not s.startswith('卷') and len(s) <= 16
                    and not LABEL_RE.match(s) and not re.search(r'[【】\[\]()（）]', s)):
                return s                 # 「## 【注释】」这类小节标题不是篇名（实测汉书全卷）
            continue
        if raw.startswith('#'):
            continue
        s = clean(raw).strip('>').strip('*').strip()
        # 「卷二十九·本纪第二十九」这类**卷题重述**不是篇名（实测元史）：出处里已经标过卷，
        # 再当篇名拼一次就成了《元史·卷二十九·卷二十九·本纪第二十九》。
        if not s or len(s) > 16 or LABEL_RE.match(s) or s.startswith('卷'):
            continue
        if re.search(r'[，。、；：！？""''《》]', s):
            continue
        return s
    return ''


def hao_of(ident, direct=''):
    """自称短语（去掉句读、「者」与「皇帝／帝者」尾巴）＝本卷之号。

    IDENT2（无「皇帝」二字）另有一路：号本身止于「帝／王／后／祖／宗…」，若紧跟的那一小截
    又是一个封号（「废帝**郁林王**，讳昭业」），连起来才是这一卷的号。
    """
    m = IDENT_FULL.match(ident)
    t = ''
    if m:
        t = clean(m.group(1))
        if t.endswith('者'):             # 「孝景皇帝者」「秦始皇帝者」——者是语助，不算号
            t = t[:-1]
        if t.endswith('皇帝') and t.endswith('始皇帝'):
            t = t[:-1]                   # 「秦始皇帝」是一个号，剥「皇帝」会切成「秦始」（实测史记卷六）
        else:
            for suf in ('皇帝', '帝者'):
                if t.endswith(suf) and len(t) - len(suf) >= 2:
                    t = t[:-len(suf)]
                    break
    elif direct and re.search(r'[帝王后主公侯]$', direct) and len(direct) <= 5:
        t = direct
    else:
        m2 = IDENT2.match(ident)
        t = clean(m2.group(1)) if m2 else ''
    return t


def _cut(s):
    """自称行摘：到本卷自己的**史臣赞／校注尾块**为止。

    「高祖，沛丰邑中阳里人…（史臣曰…）」「王莽始建国元年…（魏收自云其先出汉高贤…）」这类
    文件的卷首块把论赞、注文一起吞进来了，截到「（史臣／赞／魏收／按／题解」之前才是自称行。
    """
    pos = [i for i in (s.find(k) for k in ('（史臣', '(史臣', '（魏收', '（按', '（赞', '【题解',
                                 '【注释', '【译文', '【解析')) if i > 0]
    return s[:min(pos)] if pos else s[:80]


def filename_pian(fp):
    """文件名里自带的篇名（「004_卷四_成祖本纪_文白」→「成祖」），用于标出错位。"""
    s = re.sub(r'^\d+[_\-]?', '', fp.stem)
    s = re.sub(r'[（(][^）)]*[)）]', '', s)          # 「卷一（梁书）·太祖纪一」的括注是史书内篇目
    s = re.sub(r'文白对照|现代汉语|现代文|译文|白话|翻译|校注|注释|对照|文白', '', s)
    # 南史有几卷的文件名直接抄了卷首那两个「名讳」字（「002_卷二·名讳高祖第五」），
    # 归一化后「名讳高祖」对不上任何篇名 → 白白挂一条 ⚠（实测）。文件名里的这个前缀不是篇名。
    s = re.sub(r'^(?:卷[一二三四五六七八九十百零〇\d]+)?[·＿_\-]?(?:名讳|讳|小字|字)', '', s)
    return re.sub(r'[·＿_\-—\s]', '', s)


# 比对用的归一化：只留汉字，剥掉卷次／序数／通名／谥号尾字，最后再剥尾缀数字。
# 为什么要归一化到这么狠：旧写法直接拿字符串子串比，「五帝本纪第一」被切成「五帝第一」、
# 「（梁书）·太祖」连着括注、元史连着重复卷次，**233 行里挂了 87 条 ⚠，其中真错位只有明史那
# 一批**（实测）。通名剥掉后剩下的才是「这一卷写的是谁」，那才是能跟正文对照的东西。
def pian_key(s):
    s = re.sub(r'[^一-鿿]', '', s or '')
    s = re.sub(r'[第卷][一二三四五六七八九十百零〇]*', '', s)
    s = re.sub(r'本纪|帝纪|世纪|本传|列传|译文|白话|文白', '', s)
    s = re.sub(r'[纪传书帝王上中下]', '', s)
    return re.sub(r'[一二三四五六七八九十百零〇]+$', '', s)


def misalign(fp, rec):
    """文件名篇名 vs 正文篇名：对不上才标 ⚠（标错＝把读者往错卷上带，宁可漏标）。"""
    k = pian_key(filename_pian(fp))
    if len(k) < 2 or len(k) > 8:
        return ''                       # 空／过长（文件名尾巴抄了正文首句）都不作判断依据
    body = pian_key(rec['title'] + rec['pian0'] + ''.join(rec['temple'])
                    + rec['hao'] + '、'.join(rec['names']))
    if k in body:
        return ''
    disp = re.sub(r'^卷[一二三四五六七八九十百零〇]+', '', filename_pian(fp))
    return '⚠文件名作「%s」' % disp


def parse(fp):
    """一个帝纪文件 → dict（出处／号／名／年号／自称行／路径），无自称行返回 None。"""
    _t, book, lim, juan, pian0, rel = locate(fp)
    lines = head_lines(fp)
    head = next((l[2:].strip() for l in lines[:8] if l.startswith('# ')), '')
    title = title_of(lines)
    text = fp.read_text('utf-8', errors='replace')
    _mode, _conf, labeled = chunks_labeled(text)
    # 卷首自称行：① 行首要剥掉**成串的**「＞」「*」「【原文】」——旧写法 `lstrip('>').strip()`
    #    只剥得开一层，宋史光宗／宁宗那种「＞ ＞ 光宗…皇帝，讳惇」嵌套引用整卷漏收（实测）；
    # ② 先扫原文／待核块，抽不到再退一步看译文块（晋书卷六至卷十、辽史部分卷的语料只有白话
    #    留在卷首），这类行在「自称行摘」前标一个「译」，与照录原文的用法区分开。
    benji = bool(BENJI_OK.search(pian0 + ' ' + title + ' ' + juan)) and \
        not BENJI_BAD.search(pian0 + ' ' + title)
    ident, direct, trans = '', '', False
    for rnd in (0, 1):
        for l1, l2, h, ch, kind in labeled[:8]:
            if kind not in ('原文', '待核') and not (rnd or kind == '译文'):
                continue
            for ln in ch.split('\n'):
                s = re.sub(r'^[\s>＞*　·|]+', '', ln).strip()
                s = re.sub(r'^【[^】]{0,6}】', '', s).strip()
                m = IDENT.match(s)
                if not m and benji:
                    m = IDENT2.match(s)
                    # ⛔ IDENT2 抽出的「号」里带「为」＝这句是**封废记事**，不是自称行
                    #    （实测南齐书卷二「封宋帝为汝阴王，…」／梁书卷二「封齐帝为巴陵王，…」，
                    #     照收就会造出「为汝阴王」「为巴陵王」两个不存在的号与检索键）。
                    # ⚠ 两个捕获组都要查：废号在 **group(2)**——g1「封宋帝」字面干净、
                    #    g2「为汝阴王」才是真正来路（旧写法只查 g1 ＝ 闸门形同虚设，
                    #    实测 07 就此留下「为汝阴王、…」「为巴陵王、…」两行废号）。
                    if m and ('为' in m.group(1) or '为' in (m.group(2) or '')):
                        m = None
                if m:
                    ident, direct, trans = _cut(s), m.group(2) or '', kind == '译文'
                    break
            if ident:
                break
        if ident or rnd:
            break
    if not ident:
        return None
    # 本名只取**行内第一个**「讳／名」：实测本纪常在同一行里追述后妃、宗族（「母曰庄圣太后，
    # 怯烈氏，讳唆鲁禾帖尼」「衍，字万世，…名讳阮，字…」），全句扫会把别人的名字当成本帝。
    gm = GIVEN.search(ident)
    givens = [x for x in ([gm.group(1)] if gm else []) + BORN.findall(ident)
              if x and x[0] not in STOP_GIVEN_FIRST]
    sur = [s for pat in SUR_CAND for s in pat.findall(ident) if s[0] not in STOP_SUR]
    if direct.endswith('氏'):                     # 「则天皇后武氏」「高皇后吕氏」＝只给出姓
        sur = [direct[:-1]] + sur
        direct = ''
    names = []
    for s, n in FULLNAME.findall(ident):          # 「姓高名欢」＝直接给出连姓全名
        if (s + n) not in names:
            names.append(s + n)
    # 「本名至少两字」：实测「讳，字德象」（魏书卷九）「讳，字仲谋」（北史卷二十四）这类
    # **语料脱字**卷，单字本名会拼出「元愉」「刘准」这种张冠李戴的假键（隋炀帝→杨广为、
    # 陈伯宗→陈宗），宁可不给名键。
    # ⚠ 但**单字讳＋同行姓氏**必须收：本纪的通行写法就是「姓曹，讳操」「姓杨氏，讳坚」，
    #    拦掉单字等于把「曹操」「杨坚」「刘彻」「司马炎」全从检索键里删了——而「按本名查本纪」
    #    正是这张表存在的理由（实测旧版 07 检索「曹操」「杨坚」零命中）。
    for g in list(givens) + ([direct] if len(direct) >= 2 else []):
        if not g:
            continue
        if len(g) >= 2 and g not in names:
            names.append(g)
        if sur and (sur[0] + g) not in names:
            names.append(sur[0] + g)
    # 国姓兜底：晋书／后汉书式自称行只写「讳秀」「讳炎」，**全行没有「姓」字**，
    # 「刘秀」「司马炎」照样查不到。对**国姓唯一**的书按文件夹补一刀。
    # ⚠ 14 南史／15 北史／18,19 五代／23,25 元史／26 清史稿 一律不通配：一书跨多数
    #    （南史含宋齐梁陈四姓），猜错姓氏＝造出历史上不存在的名字。
    folder0 = rel.replace('\\', '/').split('/')[1]
    if not sur:
        for gs in GIVEN_SUR.get(folder0, ()):
            for g in givens:
                if len(g) == 1 and (gs + g) not in names:
                    names.append(gs + g)
    hao = hao_of(ident, direct)
    temple = [clean(x) for x in dict.fromkeys(TEMPLE.findall(hao) + TEMPLE.findall(title))
              if clean(x)]
    pian0 = clean(pian0) or pian_from(head)
    # 05 的篇名对元史这类书会把卷次重复一遍（「卷一·本纪第一」），出处里已经标过卷，剥掉。
    pian0 = re.sub(r'^卷[一二三四五六七八九十百零〇\d]+[·＿_\-]?', '', pian0)
    if '序纪' in (pian0 + title):                  # 魏书卷一序纪：一篇里十几位先祖，不属一帝
        return None
    rel = rel.replace('\\', '/')
    rec = dict(book=book, lim=lim, juan=clean(juan), pian0=pian0, title=clean(title),
               names=names, temple=temple, hao=hao, ident=ident, flag='', trans=trans,
               rel=rel, folder=rel.split('/')[1])
    rec['flag'] = misalign(fp, rec)
    return rec


# 非「本纪」类里认帝纪的卷题形态：「武帝纪第一」「太祖纪一」「帝纪第三」「纪第十」。
# 为什么收紧：`'纪' in 前八行` 会把《通鉴》整批编年卷拉进来（它的卷题作「唐纪八」
# 「魏纪一」，实测候选从 331 膨胀到 800，且这些卷根本没有自称行）。
JUAN_TITLE = re.compile(r'(?:帝纪|本纪|纪第|[一-鿿]{1,4}纪[一二三四五六七八九十]{1,3})')


def load_files():
    """候选文件：05 类「本纪」全部 ∪ 卷题／篇名行是帝纪形态的其他类文件。

    为什么要补后者：三国志（篇名只到「魏书一」）、旧五代史（「太祖纪一」）、新唐书卷十
    这些**真帝纪**在 05 里不归「本纪」类，只按类筛会把魏太祖、朱温、李克用整批漏掉（实测）。
    """
    keep, seen = [], set()
    for line in VOL_TABLE.read_text('utf-8').split('\n'):
        f = line.split('｜')
        if len(f) != 5 or not f[0][:2].isdigit():
            continue
        rel = 'data/' + f[4].replace('\\', '/')
        if rel in seen:
            continue
        fp = ROOT / rel
        if not fp.exists():
            continue
        if f[2] == '本纪':
            seen.add(rel)
            keep.append(fp)
        elif '通鉴' not in rel and JUAN_TITLE.search(''.join(head_lines(fp))):
            # 卷题／篇名行里带帝纪形态的非本纪类文件：魏书一是「武帝纪第一」、旧五代史的
            # 「太祖纪一」在 05 里都不归本纪类，只看类会把这批真帝纪整批漏掉。
            seen.add(rel)
            keep.append(fp)
    return keep


# ⚠ 语料缺文补录（2026-09-25）——**本纪原文不在库**、但确是历代帝王者，不能让他们"静默缺席"。
#   背景：《三国志》卷四（三少帝纪）在库中被卷五（后妃传第五）的正文顶替（各 83 行、逐行仅 1 个
#   异体字之差），于是齐王芳／高贵乡公髦／陈留王奂三帝**本名在任何索引里都命中不了**
#   （「齐王讳芳」「常道乡公讳璜」全库零命中；02/03 按体例本就不收帝王）。用户实测报「无法命中曹髦」。
#   点校本原件目录不可得（`_paths.pdf_dir()` 返回 None），**无原文不得自造** ⇒ 只登记、不改语料：
#   照常给出检索键（本名／号／年号／朝代连称）让本表**命中得到**，把「自称行摘」栏换成 ⚠ 说明＋
#   替代锚点（编年取《资治通鉴》，那是在库的）。`selftest` 有闸钉住这三行不得消失。
MISSING_BENJI = [
    dict(folder='04三国志文白', keys='曹芳、齐王芳、魏齐王、正始、嘉平', dyn='曹魏', label='齐王',
         cite='《三国志·卷四·魏书四·三少帝纪第四》', eras='正始、嘉平',
         note='本纪原文不在库（卷四正文被后妃传第五顶替）；立为齐王见《资治通鉴·卷73》行83，'
              '即位与废黜编年见《资治通鉴·卷74–76》（魏纪）',
         rel='data/04三国志文白/现代文/004_文白对照_卷四·魏书四.md'),
    dict(folder='04三国志文白', keys='曹髦、高贵乡公、魏高贵乡公、正元、甘露', dyn='曹魏', label='高贵乡公',
         cite='《三国志·卷四·魏书四·三少帝纪第四》', eras='正元、甘露',
         note='本纪原文不在库（卷四正文被后妃传第五顶替）；即位见《资治通鉴·卷76》行159，'
              '「司马昭之心，路人所知也」与南阙之死见《资治通鉴·卷77》行245',
         rel='data/04三国志文白/现代文/004_文白对照_卷四·魏书四.md'),
    dict(folder='04三国志文白', keys='曹奂、陈留王、常道乡公、魏元帝、景元、咸熙', dyn='曹魏', label='元皇帝',
         cite='《三国志·卷四·魏书四·三少帝纪第四》', eras='景元、咸熙',
         note='本纪原文不在库（卷四正文被后妃传第五顶替）；即位见《资治通鉴·卷77》行257，'
              '卒与谥见《资治通鉴·卷84》行271（「定谥号为魏元皇帝」）',
         rel='data/04三国志文白/现代文/004_文白对照_卷四·魏书四.md'),
]

# 已有行需要追加 ⚠ 的（(folder, rel) → 后缀）：卷四那条行是照"错置的正文"抽出来的，
# 它把后妃传的开头（武宣卞皇后）当成了曹魏卷四的本名键——必须就地揭穿，否则读者按它去引就错。
EXTRA_FLAG = {
    ('04三国志文白', 'data/04三国志文白/现代文/004_文白对照_卷四·魏书四.md'):
        '⚠本卷正文在库中实为《后妃传第五》（三少帝纪原文不在库，见本节末尾缺文补录）',
}


def missing_rows(folder):
    """该书节的「⚠ 语料缺文补录」行（格式同数据行，第 5 栏换成 ⚠ 说明）。"""
    return ['%s｜%s·%s｜%s｜年号 %s｜%s｜%s｜⚠语料缺文补录'
            % (e['keys'], e['dyn'], e['label'], e['cite'], e['eras'],
               '⚠ ' + e['note'], e['rel'])
            for e in MISSING_BENJI if e['folder'] == folder]


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    vocab = era_vocab()
    files = load_files()
    recs = [r for r in (parse(fp) for fp in files) if r]
    # 同书内按文件名（即卷序）排；书名顺序＝语料目录顺序
    recs.sort(key=lambda r: (r['folder'], r['rel'].split('/')[-1]))
    for r in recs:
        r['eras'] = find_eras((ROOT / r['rel']).read_text('utf-8', errors='replace'), vocab,
                              r['book'] + r['pian0'] + r['title'] + r['rel'])
    # 同一部书里两卷的卷首自称行**逐字相同**＝语料把同一篇排进了两卷（实测宋书卷九／卷十都以
    # 「顺皇帝讳准，字仲谋，小字智观」开头）。这类卷次不能当两位皇帝引，标出来。
    by_ident = collections.defaultdict(list)
    for r in recs:
        by_ident[(r['book'], clean(r['ident']))].append(r)
    for group in by_ident.values():
        if len(group) < 2:
            continue
        for r in group:
            others = '、'.join(x['juan'] or x['pian0'] for x in group if x is not r)
            r['flag'] = ((r['flag'] + '；') if r['flag'] else '') + \
                        '⚠自称行与同书 %s 重复' % others

    def dyn_of(r):
        if r['folder'] == '04三国志文白':
            for k, v in (('魏书', '曹魏'), ('蜀书', '蜀汉'), ('吴书', '孙吴')):
                if k in (r['pian0'] + r['title'] + r['rel']):
                    return v
        return DYN.get(r['folder'], '')

    out = [
        '# 07-帝王本纪索引（自动生成）',
        '',
        '> **用途**：定位「某卷帝纪是哪个皇帝」「某年号／某庙号／某本名是哪一朝谁」。',
        '> **为什么单独一份**：02/03 人物索引按列传式首句「X，字Y」抽名，帝王本纪的卷首是',
        '> 「太祖武皇帝…姓曹，讳操」这种自称行（只写「讳某」，不写连姓全名），',
        '> 所以**历代帝王一个都不进 02/03**。按本名查帝王零命中 ≠ 查无此人，是本表没建时的体例盲区。',
        '> **行格式**：`检索键｜朝代·号｜《书·卷·篇名》｜年号 …｜自称行摘｜路径［｜⚠标记］`',
        '> 检索键＝本名（姓＋讳／单讳／本名初名）＋庙号＋朝代连称（如「宋太祖」）＋本卷年号；',
        '> 自称行摘＝该卷开头那一行的原文（引用前仍须 Read 该文件逐字照录）。',
        '> ⛔ 七条纪律：',
        '> ① **庙号跨朝重复**——「太祖／太宗／世宗／高宗」必然多行命中，先定朝代再引；',
        '>    只用庙号作答＝没做完核查。',
        '> ② **一篇本纪常分多卷**（宋史·太祖＝卷一至三，明史·太祖＝卷一至二），本表只收',
        '>    **有卷首自称行的那一卷**；事迹在续卷，按同书相邻卷次继续读。',
        '> ③ **年号列是机器抽取**（全库「X元年」播种＋各卷回标）：可能不全，也会带上前朝／',
        '>    邻国纪年（明史卷一里的「至正」是元顺帝年号），甚至混进本卷末尾提到的后世纪年',
        '>    （追谥、修史年月）。同一**年号跨帝／跨政权**通用时（天复、天祐、宝历…）本表',
        '>    按卷列出多处，须回正文定年。',
        '> ④ **无本纪的君主不在本表**：追尊帝、僭伪政权（晋书载记）、三国蜀吴之主（三国志作',
        '>    "传"）、史书本纪缺失者，走 05-卷名总表 ＋ 列传／载记，别拿"本表无"当"史无其人"。',
        '>    **例外＝末尾的「⚠语料缺文补录」**：那是本纪**原文不在库**的**语料缺陷登记**（史书上',
        '>    有本纪，只是本库缺），照常给出检索键因此**能命中**；第 5 栏是替代锚点，不是自称行。',
        '> ⑤ **行尾的「⚠」有四种**：「⚠文件名作『X』」＝语料**文件名与本卷内容对不上**',
        '>    （实测 24 明史文白的文件名篇名整体错后一卷：004_卷四_成祖本纪 正文实为卷四',
        '>    恭闵帝／建文帝）；「⚠自称行与同书 X 重复」＝同一篇本纪被排进了两卷',
        '>    （实测 06 宋书卷九与卷十都以「顺皇帝讳准」开头）；「⚠本卷正文在库中实为〈另一篇〉」',
        '>    ＝**邻卷正文顶替**（实测 04 三国志卷四的三少帝纪正文＝卷五后妃传正文）；',
        '>    「⚠语料缺文补录」＝见 ④ 的例外条。都以本表的《书·卷·篇名》与自称行为准，',
        '>    **不要按文件名猜是谁**；引文出处写史卷名，不写文件名。',
        '> ⑥ **自称行摘以「译」开头＝这一卷卷首抽不到文言，只用白话兜底**（实测 05 晋书卷六／',
        '>    卷九／卷十、21 辽史卷九：校注本把「原文／译文」标在中间，卷首只剩白话）。',
        '>    这种行**只能当定位线索**，禁止当引文照录——照录必须 Read 该文件取 [原文] 块。',
        '> ⑦ **「⚠语料缺文补录」行的第 5 栏是缺陷说明＋替代锚点，同样禁止当引文**；要引该帝之事，',
        '>    按它给的《资治通鉴》卷次去 Read 原文件。**当前登记三行：曹芳／曹髦／曹奂**（三国志卷四）。',
        '> 生成：`tools/build_emperor_index.py`；语料更新后重跑 `tools/make_indexes.py`。',
    ]
    cur = None
    for r in recs:
        if r['folder'] != cur:
            if cur is not None:
                out += missing_rows(cur)          # 上一书节收尾：⚠ 语料缺文补录
            cur = r['folder']
            out += ['', '## %s ｜ %s' % (cur, r['book']), '']
        d = dyn_of(r)
        label = (r['temple'][0] if r['temple'] else '') or r['hao'] or r['title'] or '—'
        keys = [x for x in ([n for n in r['names'] if len(n) >= 2]
                            + r['temple']
                            + [(d + t) for t in r['temple'] if d]
                            + ([r['hao']] if 2 <= len(r['hao']) <= 10 else [])
                            + ([r['title']] if r['title'] and r['title'] not in r['pian0'] else [])
                            + r['eras'])
                if x and len(x) <= 8 and '卷' not in x and '·' not in x and '【' not in x
                and '(' not in x and '（' not in x and '"' not in x and '名' != x[0]
                and '曰' not in x and x[-1] not in '云者']
        keys = list(dict.fromkeys(keys))
        pian = r['pian0']
        if r['title'] and r['title'] not in pian:
            pian = (pian + '·' + r['title']) if pian else r['title']
        cite = '《%s%s%s》' % (r['book'],
                              ('·' + r['juan']) if r['juan'] else '',
                              ('·' + pian) if pian else '')
        extra = EXTRA_FLAG.get((r['folder'], r['rel']))
        flag = '；'.join(x for x in (r['flag'], extra) if x)
        out.append('%s｜%s｜%s｜年号 %s｜%s%s｜%s%s' % (
            '、'.join(keys), (d + '·' if d else '') + label, cite,
            '、'.join(r['eras']) or '本卷未抽到', '译' if r['trans'] else '',
            clean(r['ident'])[:46], r['rel'],
            ('｜' + flag) if flag else ''))
    if cur is not None:
        out += missing_rows(cur)
    OUT.write_text('\n'.join(out) + '\n', encoding='utf-8')
    print('OK 07-帝王本纪索引：%d 行（候选 %d 卷，%d 卷无卷首自称行未收）｜年号词表 %d 条'
          % (len(recs), len(files), len(files) - len(recs), len(vocab)))


if __name__ == '__main__':
    main()
