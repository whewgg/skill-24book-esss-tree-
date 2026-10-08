# -*- coding: utf-8 -*-
"""
selftest.py — 索引体检脚本

检查 00–08 九个索引文件与语料是否齐备、规模是否合理、关键样本能否命中；
校验 04 书节目录的行号/窗数自洽、01/05/06 路径可达、06 册表与机读稿目录对齐；
并检查 06 表卷 OCR 机读稿的产出情况（可选组件）。
体检报告写入 tools/_selftest_report.txt 并打印。
退出码：0 = 全部通过；1 = 有未通过项。

用法: python tools/selftest.py
（建议在重建索引后、或怀疑索引损坏时运行）
"""
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (文件, 最少行数, [(必含样本, 样本说明)])
CHECKS = [
    ('00-目录索引.md', 50, [('书目总表', '书目总表')]),
    ('01-卷目速查.md', 4000, [('项羽本纪', '史记篇目'), ('资治通鉴_卷290', '通鉴卷号路径')]),
    ('02-人物字号索引.md', 30000, [
        ('魏徵｜字：玄成', '唐·魏徵（自动通道）'),
        ('王羲之｜字：逸少', '"之"尾字回归样本'),
        ('万俟卨｜字：元忠', '复姓样本'),
        ('申时行｜字：汝默', '明·申时行'),
        ('端沐赐｜', '异文标注样本'),
    ]),
    ('03-人物出现索引.md', 10000, [
        ('尉迟敬德｜', '篇名通道样本（无字传主）'),
        ('皋陶｜', 'extra_names 手工通道样本'),
        ('审食其｜', 'extra_names 手工通道样本'),
        ('⚠同名异人', '同名异人标注'),
        ('取文件（Windows/PowerShell', 'Windows/PowerShell 取文件提示（禁 find）'),
    ]),
    # 下限 19000：2026-09-23 起 04 剔除了「志／表」类非叙事卷（-4038 窗），窗口 19711、行 19803
    ('04-故事精选索引.md', 19000, [
        ('书节目录', '书节目录（防偏食机制）'),
        ('取文件（Windows/PowerShell', 'Windows/PowerShell 取文件提示（禁 find）'),
        ('## 00资治通鉴', '通鉴书节'),
        ('## 26清史稿文白', '清史稿书节'),
    ]),
    ('05-卷名总表.md', 4000, [
        ('释老', '魏书释老志'),
        ('食货', '各史食货志'),
        ('灵征八上', '魏书灵征志（志类判定样本）'),
        ('取文件（Windows/PowerShell', 'Windows/PowerShell 取文件提示（禁 find）'),
    ]),
    ('06-表卷与原件索引.md', 100, [
        ('# 06-表卷与原件索引（自动生成）', '05 同款标题（自动生成）'),
        ('# 表卷可用性', '段一：表卷可用性'),
        ('有表体', '表体分级'),
        ('军机大臣年表', '清史稿年表样本'),
        ('表体见 06 机读稿 224册', '占位卷→机读稿册号映射（明史宰辅年表）'),
        ('表体见 06 机读稿 161册', '占位卷→机读稿册号映射（宋史宰辅表）'),
        ('# OCR 机读辅助稿', '段二：机读稿册表'),
        ('机器识别，非原文', '机器识别声明'),
        ('點校本二十四史224·', '原件 PDF 名定位（明史表册）'),
        ('取文件（Windows/PowerShell', 'Windows/PowerShell 取文件提示（禁 find）'),
    ]),
    # 下限 300：07 现 355 行（248 数据行）；抽取层塌掉（如 483 候选全落空）时行数会骤降
    ('07-帝王本纪索引.md', 300, [
        ('# 07-帝王本纪索引（自动生成）', '标题（自动生成）'),
        ('七条纪律', '纪律块（七条）'),
        ('杨坚', '本名键（隋书卷一）'),
        ('《隋书·卷一', '隋书高祖本纪出处'),
        ('秦始皇', '「秦始皇帝」整号特判（不切「秦始」）'),
        ('自称行摘以「译」开头', '译行纪律说明'),
        ('⚠语料缺文补录', '缺文补录行（三国志卷四三少帝，帝王按本名可命中的兜底）'),
    ]),
    # 下限 280：08 现 288 行（254 案 ＋ 表头/分节）；案行骤减＝cases.md 或生成器塌了
    ('08-案件索引.md', 280, [
        ('# 08-案件索引（自动生成）', '标题（自动生成）'),
        ('取文件（Windows/PowerShell', 'Windows/PowerShell 取文件提示（禁 find）'),
        ('本库未载的著名案件', '「本库未载」清单行（零命中案集中登记，防凭记忆补卷次）'),
        ('胡惟庸案', '明初四大案之一'), ('蓝玉案', '明初四大案之一'),
        ('空印案', '明初四大案之一'), ('郭桓案', '明初四大案之一'),
        ('李善长案', '明初八大案诸说兼收'), ('汪广洋案', '明初八大案诸说兼收'),
        ('杨宪案', '明初八大案诸说兼收'), ('方孝孺案', '明初八大案诸说兼收'),
        ('巫蛊之祸', '汉案'), ('乌台诗案', '宋案（本库无此名，靠检索词「逮赴台狱」落地）'),
        # 2026-09-25 用户贴清单回核时**漏收后补**的两案——点名钉住，防止再被"以为收了"
        ('缇萦救父', '用户清单回核补收（淳于意／除肉刑）'),
        ('嵇康案', '用户清单回核补收（吕安案／司马昭）'),
        ('⚠ 本库零命中', '零命中案的显式标记'),
    ]),
]

# 禁含样本：已知噪声一旦复现即说明过滤规则被破坏
FORBIDDEN = [
    ('02-人物字号索引.md', '一块写｜', '白话量词噪声'),
    ('02-人物字号索引.md', '平素称其｜', '"以字相称"类噪声'),
    ('02-人物字号索引.md', '为武信君｜', '"号为X"残留'),
    ('03-人物出现索引.md', '一之｜', '称谓残留人名'),
    ('03-人物出现索引.md', '时人多以｜', '白话介词短语'),
    ('07-帝王本纪索引.md', '为汝阴王', '封废记事误判为自称行（IDENT2 g2 漏检）'),
    ('07-帝王本纪索引.md', '为巴陵王', '封废记事误判为自称行（IDENT2 g2 漏检）'),
    ('07-帝王本纪索引.md', '绳下、', '「以刑名绳下」假本名（裸名左邻守卫失效）'),
    ('07-帝王本纪索引.md', '殷的始祖', '白话卷首误判为文言自称行'),
]


def main():
    # `--quick`＝快档（默认全量）。**跳过全部 subprocess 冒烟**（rag locate/verify、factcheck、
    # pick_story、内嵌 rag selftest、谜题跳板）与全库同名簇扫描——它们占全量 ~95% 的时间
    # （实测全量 122 s），而这些闸门每天过一遍就够；日常改文档/索引后需要的是"结构有没有坏"
    # 的秒级 rc。**判据零重复**：快档跑的是同一套检查，只是少跑几段——不另写一份判据（抄一份必漂移）。
    QUICK = '--quick' in sys.argv
    lines = ['索引体检报告' + ('（快档 --quick：只查结构，跳过 subprocess 冒烟）' if QUICK else ''),
             '=' * 40]
    fails = 0

    # 体检副作用闸（2026-09-29 加）：体检内部会调用 `pick_story.py` 抽签，**必须全是 `--no-log`**
    # ——它**不许动用户的抽签台账**，否则会悄悄吃掉 `--no-repeat` 的一次轮换（记录被吃掉且无任何提示）。
    # 做法：开头拍两本账的 sha256，收尾再拍一次比对。**成本≈0**（纯内存哈希两个 <1 KB 的文件，不起子进程）。
    import hashlib
    _LEDGERS = (ROOT / 'tools' / '_draw_log.md', ROOT / 'tools' / 'story_log.md')
    _ledger_before = {p: (hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None)
                      for p in _LEDGERS}

    for fname, min_lines, samples in CHECKS:
        p = ROOT / fname
        if not p.exists():
            lines.append('FAIL  %s 不存在' % fname)
            fails += 1
            continue
        text = p.read_text(encoding='utf-8', errors='replace')
        n = text.count('\n') + 1
        if n >= min_lines:
            lines.append('OK    %s（%d 行）' % (fname, n))
        else:
            lines.append('WARN  %s 仅 %d 行（要求≥%d），可能未重建或损坏' % (fname, n, min_lines))
            fails += 1
        for kw, desc in samples:
            if kw in text:
                lines.append('        OK   「%s」%s' % (kw, desc))
            else:
                lines.append('        FAIL 「%s」未命中（%s）——请重跑相应 build 脚本' % (kw, desc))
                fails += 1

    # SKILL.md frontmatter 必须合法且带足触发词——否则 skill 根本不会被加载
    skill = ROOT / 'SKILL.md'
    if skill.exists():
        raw = skill.read_text(encoding='utf-8')
        parts = raw.split('---')
        fm = parts[1] if len(parts) > 2 else ''
        desc = ''
        bad = ''
        try:
            import yaml
            d = yaml.safe_load(fm)
            if not isinstance(d, dict) or not d.get('description'):
                bad = 'frontmatter 缺 description'
            else:
                desc = d['description']
        except ImportError:
            # 无 yaml 时退化为引号配平检查（正是曾经踩过的坑：description 内混入裸双引号）
            line = next((l for l in fm.splitlines() if l.startswith('description')), '')
            if not line:
                bad = '未找到 description 行'
            elif line.count('"') != 2:
                bad = 'description 内含裸 ASCII 双引号（%d 个，应为 2 个）' % line.count('"')
            else:
                desc = line.split('"', 2)[1]
        except Exception as e:
            bad = 'YAML 解析失败：%s' % str(e)[:90]
        if bad:
            lines.append('FAIL  SKILL.md %s——skill 可能无法被触发，务必修复！' % bad)
            fails += 1
        else:
            lines.append('OK    SKILL.md frontmatter 合法（description %d 字）' % len(desc))
            for kw in ('宰辅年表', '年表', '世系表', '字什么', '讲个历史故事', 'OCR辅助稿'):
                if kw not in desc:
                    lines.append('        WARN description 缺触发词「%s」（降低被选中概率）' % kw)
            # 讲史**口语**触发词（2026-09-26 用户报「其他 skill 多轮后无法调用讲故事技能」）：
            # 根因＝触发词段里只有「讲个历史故事」，而用户实际说的是「讲个故事／讲一个故事／
            # 给我讲一个故事／讲故事」——一个都没进触发词段。多轮＋其他 skill 之后，模型对
            # skill 的选择更依赖浅层关键词匹配，这几个说法直接失联。这几位不是"降低概率"而是
            # "能不能被选中"，故为 FAIL（丢一个就重犯）。
            STORY_KWS = ('讲历史故事', '讲个历史故事', '讲个故事', '讲一个故事', '给我讲一个故事')
            miss_story = [k for k in STORY_KWS if k not in desc]
            if miss_story:
                lines.append('FAIL  description 缺讲史触发词：%s——用户这么说时 skill 不会被加载'
                             '（2026-09-26 实测故障根因：触发词段只有「讲个历史故事」）' % '、'.join(miss_story))
                fails += 1
            elif '历史故事' not in desc:
                lines.append('FAIL  description 未把触发场景锚定在「历史故事」——本技能只管历史（正史）故事，'
                             '写成泛化的「故事」会让非历史故事请求也命中')
                fails += 1
            else:
                lines.append('OK    description 讲史触发词齐全且锚定「历史故事」'
                             '（讲历史故事／讲个历史故事／讲个故事／讲一个故事／给我讲一个故事）')
    # description 与正文必须一致：曾出现 description 写「「讲个故事」≠讲史请求、不得擅自等同」，
    # 与正文「讲个故事＝直接抽签开讲」直接冲突——模型信 description，于是自己发挥（2026-09-23 实测）。
    _fm = (ROOT / 'SKILL.md').read_text(encoding='utf-8')[:2500] if (ROOT / 'SKILL.md').exists() else ''
    if '不得擅自等同' in _fm or '「讲个故事」≠' in _fm:
        lines.append('FAIL  SKILL.md 的 description 仍含「讲个故事≠讲史请求」旧口径，与正文「讲个故事＝直接抽签开讲」冲突')
        fails += 1
    if '读不到选签表' not in _fm:
        lines.append('FAIL  description 未写明「读不到选签表时必须明说、不得编造」——模型只会「演」抽签')
        fails += 1
    # 「我读不到选签表」是**讲史取不到号**时的专用免责语。2026-09-24 实测：某只加载 description 的环境
    # 收到六道线索的「猜皇帝」，回答只有一句「我读不到选签表」——逃生阀被搬到人物题上＝答非所问，
    # 比硬猜更糟（它看起来像"守规矩"）。故 description 里这句必须**贴着讲史限定语**出现，
    # 并且必须另给一条人物题适用的降级口径（查不到库⇒答"无法确定"）。
    _dline = next((l for l in (ROOT / 'SKILL.md').read_text(encoding='utf-8', errors='replace').splitlines()
                   if l.startswith('description')), '')
    _i = _dline.find('读不到选签表')
    if _i >= 0:
        _w = _dline[max(0, _i - 60):_i + 60]
        if not ('讲史' in _w or '故事' in _w):
            lines.append('FAIL  description 的「读不到选签表」没有限定在讲史场景——'
                         '这句会被猜人物题借用（实测某环境对六道线索只回一句「我读不到选签表」）')
            fails += 1
        elif '我查不到原文库' not in _dline:
            lines.append('FAIL  description 缺"跑不了检索⇒答无法确定"的降级口径——'
                         '无工具环境只剩讲史那一句可说，人物题必然答非所问')
            fails += 1
        else:
            lines.append('OK    description 的两条降级口径各归各位（讲史＝读不到选签表；查不了库＝无法确定）')
    # description 精简后只能留"触发面"，**程序性规则的正主必须是正文**（正文在 skill 触发时才加载，
    # 但它是唯一写细节的地方；description 只负责"被选中"）。2026-09-24 把 description 从 898 字压到
    # 527 字时，逐条核过每条被删规则在正文仍在——这道闸把那次核对固化：日后无论谁再精简 description，
    # 只要正文里丢了这条规则就 FAIL（曾经「读不到选签表」就只剩 description 一处，靠本闸逼出正文补写）。
    _body = (ROOT / 'SKILL.md').read_text(encoding='utf-8').split('---', 2)[-1] if (ROOT / 'SKILL.md').exists() else ''
    BODY_RULES = [('(L mod 27)+1', '零工具环境按字数取号的公式'),
                  ('选签 第 k 条', '取号后首行的凭证格式'),
                  ('禁止惯性连带', '不许顺着上一个故事讲相关人物'),
                  ('非真实历史（演义／小说／虚构）', '虚构内容的强制标注'),
                  ('只讲人物的事，不讲制度条目', '讲史禁讲志／表类制度记述'),
                  ('独有引语反查', '「这是谁」类谜题的检索分岔'),
                  ('读不到选签表', '拿不到选签表时必须明说、不得编造'),
                  ('我查不到原文库', '跑不了检索时的降级口径（铁律 10）'),
                  ('搬到人物题', '讲史专用免责语不得外用到人物题'),
                  ('讲全的判据', '讲全规则（窗口只是情节核心段，须前后补全）'),
                  ('不逐句引原文', '表达纪律（讲史＝讲故事，不是史料摘抄）'),
                  ('用户主动要接着听', '续讲规则（用户要续讲＝顺着往下讲，不是惯性连带）'),
                  ('--next', '续讲取点工具（给上一签路径→后续读取范围）'),
                  ('应点讲', '点名故事＝rag 定位＋首行写《书名·卷次》出处'),
                  ('--person', '按人物抽（点名人物→本传多窗抽一个）')]
    lost = [d for k, d in BODY_RULES if k not in _body]
    if lost:
        lines.append('FAIL  SKILL.md 正文缺规则：%s —— description 只写触发词，'
                     '细则必须在正文，否则不加载正文的环境就会凭记忆作答' % '；'.join(lost))
        fails += 1
    else:
        lines.append('OK    SKILL.md 正文承载全部讲史／猜人物细则（%d 条，description 可安全精简）'
                     % len(BODY_RULES))

    # 检索纪律要点（2026-09-29 加）：三条都是实测踩出来的**假阳性／语料边界**——
    #   王畿（地理概念被 02/03 当成人名）、穷河源（库外引语的对应表述）、本库边界（1840 前／官方立场）。
    # 与 BODY_RULES 同款用意：日后谁再瘦身，把这三条删了或改没了，这里就 FAIL。
    RETRIEVAL_RULES = [('王畿', '普通词被当人名（搜「王畿」会命中明代学者王畿字汝中）'),
                       ('穷河源', '题面引语出自库外典籍时要换"史书里的对应表述"'),
                       ('本库边界', '语料边界：1840 年前正史＋通鉴，现代史／世界史零命中')]
    lost_r = [d for k, d in RETRIEVAL_RULES if k not in _body]
    if lost_r:
        lines.append('FAIL  SKILL.md 正文缺检索纪律：%s —— 这三条是实测踩出来的假阳性／语料边界，'
                     '不许在瘦身时被删' % '；'.join(lost_r))
        fails += 1
    else:
        lines.append('OK    SKILL.md 正文含三条检索纪律（同名异指／库外引语换表述／语料边界）')

    # 环境能力分流＋降级层级（2026-10-01 加，AstrBot 报障的根因）：
    #   群 bot 收到「讲一个故事」只回两行「选签 第 N 条…」＋首句，**正文一个字没有**。
    #   日志（.astrbot/logs/backend.log 179041–179060）显示链路是：先试 `cd … && python rag.py`
    #   → PowerShell 不认 `&&`；再试 `; python rag.py` → **脚本在 tools\ 下**，路径错；
    #   然后改用文件读取工具读 SKILL.md → **读到的却是 1-alt-2 选签表** → 报签号收工。
    #   两个坑都在分流段：(a) 它把 1-alt-2 读成"面向零工具环境的一条平级选项"，
    #   而原分流规则只提到 1-alt、压根没给 1-alt-2 归属；(b) 没有一处告诉模型
    #   "命令报错先修写法，别据此判定环境不能执行"。
    #   本组断言把三层降级**层级**与两条排错口径锁死，防止日后瘦身时又漂回去。
    DISPATCH_RULES = [('有没有能跑命令的工具', '分流判据＝手上有没有 shell/exec 工具（不是"猜"）'),
                      ('末位兜底', '1-alt-2 只是末位兜底，不是平级捷径'),
                      ('PowerShell 5.1', 'Windows 命令不许用 &&（本机 shell 是 PowerShell 5.1）'),
                      ('先修写法', '命令报错先改写法（换 ;／补 tools\\／用绝对路径），'
                                   '不得据此判定"环境不能执行"'),
                      # 2026-10-01 二次扩容（当天 08:03 群 bot 八步全烧在"探路"上）：四条新纪律
                      ('去掉末尾的', '给"从系统提示词的 SKILL.md 绝对路径推出 skill 根目录"的机械办法'),
                      ('第一条命令就直接执行抽签器', '禁止先探路：第一刀就执行 pick_story.py'),
                      ('别去读脚本的源码', '读脚本源码＝研究不是执行，白烧一步'),
                      ('3.1 MB', '警告 04 有 3.1 MB、读取工具约 256 KB 上限，禁止整读'),
                      ('严禁伪造', '签号是算出来的——跑不了就如实说，不许手搓签号')]
    lost_d = [d for k, d in DISPATCH_RULES if k not in _body]
    if lost_d:
        lines.append('FAIL  SKILL.md 正文缺「环境能力分流／降级层级」要点：%s —— '
                     '实测 2026-10-01 群 bot 因缺这几句，从脚本流程滑到末位兜底表，'
                     '只报签号不讲故事' % '；'.join(lost_d))
        fails += 1
    else:
        lines.append('OK    SKILL.md 正文写明分流判据与三层降级（脚本 ＞ 04 ＞ 选签表），'
                     '并含 Windows 命令排错口径')

    # 1-alt-2 段必须**自带**"末位兜底"定位（不只依赖上面的分流段）：这一段由
    #   tools/make_story_pool.py 生成（它是该段的所有者），生成器若把标题改回
    #   「零工具环境的选签表」，模型又会把它读成平级选项 —— 故在**生成物**上也设一道。
    _sec_m = re.search(r'\n1-alt-2\. .*?需要换一批时改脚本里的 `SEED` 重跑。\n', _body, re.S)
    if not _sec_m:
        lines.append('FAIL  SKILL.md 里找不到 1-alt-2 段（选签表）——它由 make_story_pool.py 生成，'
                     '标题格式见该脚本 PAT')
        fails += 1
    else:
        _sec = _sec_m.group(0)
        _need = [('末位兜底', '段标题须自带"末位兜底"定位'),
                 ('手上没有执行工具，且连', '段首须写清适用条件（无执行工具 **且** 读不到 04）'),
                 ('只把首句照念一遍就当讲完了＝不合格', '须明确"只报首句＝不合格"')]
        _bad_sec = [d for k, d in _need if k not in _sec]
        if _bad_sec:
            lines.append('FAIL  1-alt-2 段缺要点：%s —— 该段会被本体生成器重写，'
                         '请连同 tools/make_story_pool.py 一起改' % '；'.join(_bad_sec))
            fails += 1
        else:
            lines.append('OK    1-alt-2 段自带「末位兜底」定位与"只报首句＝不合格"的警告')

    # 生成器回归（2026-09-29 加）：`tools/make_story_pool.py` **拥有 SKILL.md 的 1-alt-2 段**（选签表），
    # 但它此前**不在任何回归里** → D1（build_section 只复现前三句，跑一次静默删掉三处安全文案，
    # 其中「读不到选签表」还是 BODY_RULES 必需串）与 D2（`pat.sub(sec, s)` 把替换串里的 `\p`
    # 当替换模板转义 → PatternError）潜伏了两轮都没人发现。
    # 这里调它的 `--check`（**非破坏性：只算不写**）核对"生成器现在会写成什么" vs "文档现文"
    # 是否逐字节相等——等价于问「跑一次会不会改 SKILL.md」。成本实测 0.49 s，故**快档也跑**。
    msp = ROOT / 'tools' / 'make_story_pool.py'
    if not msp.exists():
        lines.append('FAIL  缺 tools/make_story_pool.py——它拥有 SKILL.md 的 1-alt-2 段（选签表），'
                     '缺了就没法校验文档会不会被生成器静默改写')
        fails += 1
    else:
        # ⚠ **进程内调用**，不走 subprocess：实测 `--check` 全程 0.45 s，其中 **0.41 s 只是
        #   "起一个 Python 进程"的固定开销**（空脚本 `python -c pass` 也是 0.41 s），检查本身 0.04 s。
        #   判据仍只有一份——调的是生成器自己的 `check()`，没在 selftest 里另抄一遍。
        #   省掉的是"闸门成本"，不是"检查内容"。（本段故意没有 subprocess.run。）
        try:
            sys.path.insert(0, str(ROOT / 'tools'))
            import make_story_pool as _msp
            ok_g, msg_g = _msp.check()
            if ok_g:
                lines.append('OK    make_story_pool 生成器未漂移：%s（跑一次不改动文档）' % msg_g)
            else:
                lines.append('FAIL  make_story_pool 生成器已漂移——**跑一次就会改写 SKILL.md**：%s'
                             % ' '.join(msg_g.split())[:300])
                fails += 1
        except Exception as e:
            lines.append('FAIL  make_story_pool 核对失败：%s' % str(e)[:120])
            fails += 1

    # 外移文档的**指路守卫**（2026-09-29 用户问"猜谜外移后还能命中吗"）：
    # references/ 下的规程**不会自动进上下文**——skill 触发时只加载 description ＋ SKILL.md 正文，
    # 那几份文件要靠**正文指路 ＋ 模型主动 Read** 才命中。查下来现状是好的（指路在、节号 §4／§6
    # 与文件小节对得上、正文关键锚点有 BODY_RULES 守着），但**"指路行本身"此前没有任何断言**：
    # 一旦被删，正文别处仍有「人物辨识」「独有引语反查」等词，旧断言照过 ⇒ 整套外移规程**静默失联**。
    # 故锁死：正文必须指向每一份 references 文档；操作型文档还要带"先读"类提示。
    REF_DOCS = ('dynasty-disambiguation.md', 'pseudo-history-checklist.md',
                'guess-protocol.md', 'maintenance.md')
    _skill_route = (ROOT / 'SKILL.md').read_text(encoding='utf-8', errors='replace')
    missing_ref = [d for d in REF_DOCS if d not in _skill_route]
    # 负向注入（内存串，不落盘）：判据必须能抓出"指路被去掉"
    probe_missing = [d for d in REF_DOCS if d not in _skill_route.replace('guess-protocol.md', 'XX')]
    if missing_ref:
        lines.append('FAIL  SKILL.md 正文未指向 references/%s —— 外移的规程读不到'
                     '（skill 触发只加载 description＋正文，references 靠指路＋主动 Read）'
                     % '、'.join(missing_ref))
        fails += 1
    elif not probe_missing:
        lines.append('FAIL  指路守卫的负向注入失败：去掉指路后判据没报缺失——判据无效')
        fails += 1
    elif not re.search(r'guess-protocol\.md[\s\S]{0,220}先读', _skill_route):
        lines.append('FAIL  SKILL.md 提到 guess-protocol.md 却没写"先读"类提示——'
                     '模型可能知道有这文件却不读（外移规程＝指路＋主动 Read 才命中）')
        fails += 1
    else:
        lines.append('OK    外移规程指路齐备（%d 份 references 都被正文指向；guess-protocol 带'
                     '"先读"提示；负向注入有效）' % len(REF_DOCS))

    # 文档引用完整性：SKILL.md 与 references/*.md 里反引号标出的文件路径必须真实存在
    refs_dir = ROOT / 'references'
    doc_files = [ROOT / 'SKILL.md'] + (sorted(refs_dir.glob('*.md')) if refs_dir.is_dir() else [])
    # 根目录的「讲解／指南」（唐代/宋代/明代宗室世系讲解、宰相首辅军机查询指南）**会进
    # `--source index` 检索源**，却原先不在任何 lint 覆盖内——一并纳入反引号引用检查（2026-09-22）。
    doc_files += sorted(ROOT.glob('*讲解.md')) + sorted(ROOT.glob('*指南.md'))
    data_dir = ROOT / 'data'
    # 语料卷名常以裸文件名出现（如 001_卷一·本纪第一.md，同名分布在多部书），
    # 故除路径候选外，再允许「data/ 下任一文件的同名 basename」作为存在证明。
    data_names = set()
    if data_dir.is_dir():
        for _dp, _dn, _fn in os.walk(data_dir):
            data_names.update(_fn)
    dangling = []
    for rf in doc_files:
        if not rf.exists():
            continue
        for tok in sorted(set(re.findall(r'`([^`\s]+?\.(?:md|py|txt))`', rf.read_text(encoding='utf-8')))):
            # 跳过：通配符/占位符、URL、后缀模式（_译.md）、目录（结尾 / 或 \，可能按需创建）
            if any(c in tok for c in '<>*…') or tok.startswith(('http', '_')) \
                    or tok.endswith(('/', '\\')):
                continue
            cands = [ROOT / tok, ROOT / 'tools' / tok, ROOT / 'tools' / 'facts' / tok, refs_dir / tok,
                     ROOT / '06-表卷OCR机读稿（简体）' / tok, ROOT / '06-表卷OCR机读稿' / tok]
            if any(c.exists() for c in cands):
                continue
            if tok.startswith(('data\\', 'data/')):
                if (data_dir / tok[5:]).exists():
                    continue
            elif tok in data_names:
                continue
            dangling.append('%s 引用 %s' % (rf.name, tok))
    if dangling:
        lines.append('FAIL  文档引用了不存在的文件（%d 处，全列）：%s'
                     % (len(dangling), '；'.join(dangling)))
        fails += 1
    else:
        lines.append('OK    SKILL.md 与 references/ 引用的文件均存在（%d 个文档）' % len(doc_files))

    # 文档单行长度闸（E4-4，2026-10-01 落地）：排除 frontmatter（description）、表格行、
    # 生成型索引 03/08。硬闸：>1200 B 必须 0（一行塞不下这么多规则，多半是误并多段）；
    # 基线闸：>600 B ≤ 12（现状 5：SKILL.md 1、02 索引 1、maintenance 3），防长行泛滥。
    _gen_idx = ('03-人物出现索引.md', '08-案件索引.md')
    _long_docs = [ROOT / 'SKILL.md'] + sorted(ROOT.glob('0[0-9]-*.md')) \
        + sorted(ROOT.glob('*讲解.md')) + sorted(ROOT.glob('*指南.md')) \
        + sorted(refs_dir.glob('*.md')) + sorted((ROOT / 'tools').glob('*.md'))
    _over1200, _over600 = [], []
    for _d in _long_docs:
        if not _d.exists() or _d.name in _gen_idx:
            continue
        _in_fm = False
        for _i, _ln in enumerate(_d.read_text(encoding='utf-8', errors='replace').splitlines(), 1):
            _s = _ln.strip()
            if _d.name == 'SKILL.md' and _s == '---':
                _in_fm = not _in_fm
                continue
            if _in_fm or _s.startswith('|'):
                continue
            _b = len(_ln.encode('utf-8'))
            if _b > 1200:
                _over1200.append('%s:%d(%dB)' % (_d.name, _i, _b))
            elif _b > 600:
                _over600.append('%s:%d(%dB)' % (_d.name, _i, _b))
    if _over1200:
        lines.append('FAIL  文档单行 >1200 B（%d 处，通常是把多段误并成一行）：%s'
                     % (len(_over1200), '、'.join(_over1200[:8])))
        fails += 1
    elif len(_over600) > 12:
        lines.append('FAIL  文档单行 >600 B 已有 %d 处（上限 12，防长行泛滥）：%s'
                     % (len(_over600), '、'.join(_over600[:8])))
        fails += 1
    else:
        lines.append('OK    文档单行长度在限内（>1200B＝0；>600B 共 %d 处 ≤12）' % len(_over600))

    # 通称表右端词存在性（防幻觉，2026-10-01 落地）：`aliases_events.md` 是「后世通称→史文用词」，
    # 若右端词在库里**写错/不存在**，通称改写就静默失效 → 模型搜不到 → 退回零命中瞎编。
    # 维护纪律要求"右端词上线前必须实测"，这里把它变成闸门：全库扫一遍（实测 **4.0 s**），
    # 任何右端词零命中即 FAIL。快档也跑（改通称表正是"改文档"的一种，须立刻抓出）。
    _alias_p = ROOT / 'tools' / 'aliases_events.md'
    if not _alias_p.exists():
        lines.append('FAIL  缺 tools/aliases_events.md（通称改写表，缺了「破釜沉舟」这类后世通称就搜不到原文）')
        fails += 1
    else:
        _rw = []
        for _ln in _alias_p.read_text(encoding='utf-8', errors='replace').splitlines():
            _ls = _ln.strip()
            if not _ls or _ls.startswith('#'):
                continue
            _rw += [p.strip() for p in _ln.strip().split('｜') if p.strip()][1:]
        _rw = sorted(set(_rw))
        _seen = set()
        for _dp, _dn, _fns in os.walk(data_dir):
            for _fn in _fns:
                if not _fn.endswith('.md'):
                    continue
                try:
                    _txt = open(os.path.join(_dp, _fn), encoding='utf-8', errors='replace').read()
                except Exception:
                    continue
                for _w in _rw:
                    if _w in _txt:
                        _seen.add(_w)
        _miss = [w for w in _rw if w not in _seen]
        if _miss:
            lines.append('FAIL  通称表右端词在库里零命中（%d 个）——改写会失效、模型退回零命中瞎编：%s'
                         % (len(_miss), '、'.join(_miss)))
            fails += 1
        else:
            lines.append('OK    通称表 %d 个右端词全部实测存在（扫 4408 文件）' % len(_rw))

    # 04 窗口完整性（防幻觉，2026-10-01 落地）：`04` 每个窗口都指向「文件｜起行｜止行」；
    # 若路径不存在、或行号越出文件范围，**抽签抽到它 → 模型 Read 不到 → 硬编**。
    # 现状 19711 窗全 OK，这里锁成基线：重建索引后若冒出坏窗口即 FAIL。
    # 成本 2.6 s（扫 4408 文件行数），故放全量（重建索引后本来就该跑全量）。
    if not QUICK:
        _wl = {}
        for _dp, _dn, _fns in os.walk(data_dir):
            for _fn in _fns:
                if not _fn.endswith('.md'):
                    continue
                _p = os.path.join(_dp, _fn)
                try:
                    with open(_p, encoding='utf-8', errors='replace') as _f:
                        _wl[os.path.relpath(_p, data_dir).replace('/', '\\')] = sum(1 for _ in _f)
                except Exception:
                    pass
        _bad, _nw = [], 0
        _idx04 = ROOT / '04-故事精选索引.md'
        if _idx04.exists():
            for _ln in _idx04.read_text(encoding='utf-8', errors='replace').splitlines():
                _ls = _ln.strip()
                if not _ls or _ls.startswith(('#', '>', '格式')):
                    continue
                _ps = [p.strip() for p in _ls.split('｜')]
                if len(_ps) < 4:
                    continue
                _nw += 1
                _path, _s, _e = _ps[0], _ps[1], _ps[2]
                _n = _wl.get(_path)
                if _n is None:
                    _bad.append('路径不存在 %s' % _path)
                    continue
                try:
                    _si, _ei = int(_s), int(_e)
                except ValueError:
                    _bad.append('行号非数字 %s(%s,%s)' % (_path, _s, _e))
                    continue
                if _si < 1 or _ei < _si or _ei > _n:
                    _bad.append('%s %d-%d（文件 %d 行）' % (_path, _si, _ei, _n))
        if _bad:
            lines.append('FAIL  04 窗口指向坏位置（%d 个）——抽到就 Read 不到、必然硬编：%s'
                         % (len(_bad), '；'.join(_bad[:5])))
            fails += 1
        else:
            lines.append('OK    04 窗口 %d 个全部指向有效位置（路径存在＋行号在文件范围内）' % _nw)

    # 索引头部（前 22 行）反引号里的 .py 引用必须真实存在——索引头部由生成脚本产出，
    # 脚本改名/删除后头部即成死链；上面那段 lint 只覆盖 SKILL.md 与 references/，
    # 索引正文过大（05 达数 MB）不宜整扫，故只查头部（2026-09-22 加）。
    head_dangling = []
    for fname in ('00-目录索引.md', '01-卷目速查.md', '02-人物字号索引.md', '03-人物出现索引.md',
                  '04-故事精选索引.md', '05-卷名总表.md', '06-表卷与原件索引.md',
                  '07-帝王本纪索引.md', '08-案件索引.md'):
        fp = ROOT / fname
        if not fp.exists():
            continue
        head = '\n'.join(fp.read_text(encoding='utf-8').splitlines()[:22])
        for tok in sorted(set(re.findall(r'`([^`\s]+?\.py)`', head))):
            if any(c in tok for c in '<>*…') or tok.startswith(('http', '_')):
                continue
            cands = [ROOT / tok, ROOT / 'tools' / tok, ROOT / 'tools' / Path(tok).name]
            if not any(c.exists() for c in cands):
                head_dangling.append('%s 引用 %s' % (fname, tok))
    if head_dangling:
        lines.append('FAIL  索引头部引用了不存在的脚本：%s' % '；'.join(head_dangling))
        fails += 1
    else:
        lines.append('OK    00–08 索引头部引用的脚本均存在')

    # 路径可搬移 lint：**面向检索的文档里不得出现盘符绝对路径**
    #   （用户明确要求：文档里写死文件位置，换机器或搬目录后就找不到。
    #     `references/` 不在扫描范围内——「环境注意（本机）」节允许记本机解释器路径。）
    ABSPATH_RE = re.compile(r'(?<![A-Za-z])[A-Za-z]:[\\/]')
    lint_targets = [ROOT / 'SKILL.md'] + sorted(ROOT.glob('0[0-8]-*.md'))
    draft_dir2 = ROOT / '06-表卷OCR机读稿'
    if draft_dir2.is_dir():
        lint_targets += sorted(draft_dir2.glob('*.md'))
    offenders = []
    for f in lint_targets:
        if not f.exists():
            continue
        for i, ln in enumerate(f.read_text(encoding='utf-8', errors='replace').splitlines(), 1):
            m = ABSPATH_RE.search(ln)
            if m:
                offenders.append('%s:%d 出现 %s…' % (f.name, i, ln[max(0, m.start() - 12):m.start() + 22].strip()))
    if offenders:
        lines.append('FAIL  文档里有盘符绝对路径 %d 处（搬目录即失效，请改成相对本 skill 目录的写法）：%s'
                     % (len(offenders), '；'.join(offenders[:4]) + ('…' if len(offenders) > 4 else '')))
        fails += 1
    else:
        lines.append('OK    SKILL.md／00–08 索引／06 机读稿头部均无盘符绝对路径（%d 个文件）' % len(lint_targets))

    # 库外路径解析器：check_tables.py / build_table_ocr.py 都靠它，缺了会 ImportError
    if (ROOT / 'tools' / '_paths.py').exists():
        lines.append('OK    tools/_paths.py 在位（库外原件目录的唯一解析处，库内路径一律相对）')
    else:
        lines.append('FAIL  缺少 tools/_paths.py——check_tables.py / build_table_ocr.py 将无法导入')
        fails += 1

    # 06 OCR 机读稿目录（可选组件：未做 OCR 时不应判失败，仅提示）
    draft = ROOT / '06-表卷OCR机读稿'
    n_draft = len(list(draft.glob('*.md'))) if draft.exists() else 0
    if n_draft:
        lines.append('OK    06-表卷OCR机读稿/ 已产出 %d 册机读稿' % n_draft)
        simp = ROOT / '06-表卷OCR机读稿（简体）'
        n_simp = len(list(simp.glob('*.md'))) if simp.exists() else 0
        if n_simp < n_draft:
            lines.append('        WARN 简体检索版仅 %d 册（繁体稿 %d 册）——请跑 make_ocr_simplified.py；'
                         '否则用简体关键词搜表卷会落空' % (n_simp, n_draft))
        else:
            # 册数相等还不够：只重跑了繁体（build_table_ocr）而没重跑简体时，文件名可能已变
            # （重新分册/改名），简体目录里留的是旧名 → **简体检索静默落空**。故必须比文件名集合。
            tn = {p.name for p in draft.glob('*.md')}
            sn = {p.name for p in simp.glob('*.md')}
            if tn != sn:
                lines.append('FAIL  简体机读稿与繁体不同步（册数 %d=%d 但文件名不一致）：'
                             '仅繁体有 %s；仅简体有 %s —— 请重跑 make_ocr_simplified.py'
                             % (n_simp, n_draft, sorted(tn - sn)[:2], sorted(sn - tn)[:2]))
                fails += 1
            else:
                lines.append('OK    06-表卷OCR机读稿（简体）/ %d 册，与繁体稿逐名对齐'
                             '（简体检索用，勿据以引用）' % n_simp)
    else:
        lines.append('        INFO 06-表卷OCR机读稿/ 尚无产出（表卷 OCR 为可选组件）')

    data = ROOT / 'data'
    nfiles = sum(1 for _ in data.rglob('*.md')) if data.exists() else 0
    if nfiles >= 4400:
        lines.append('OK    data/ 语料 %d 个 md 文件' % nfiles)
    else:
        lines.append('FAIL  data/ 语料仅 %d 个 md 文件（要求≥4400）' % nfiles)
        fails += 1

    # 同书内「正文开头雷同」＝整卷正文被错置/复制的信号（2026-09-25 加）。
    # 实例：《三国志》卷四（三少帝纪）正文与卷五（后妃传）正文相同——卷四正文因此不在库，
    #       帝王本纪（齐王芳／高贵乡公髦／陈留王奂）全部落空（「齐王讳芳」全库零命中）。
    # 判据：同目录、去掉变体后缀（_译/_en/_白话…）后**词干不同**、正文≥600 字，而**正文开头
    #       600 字完全相同**。实测前缀取 400–2000 字结果恒为同样 2 组，无误报；故锁基线。
    # 基线 = 已知的两处（修复语料后此处应改为 INFO 并下调基线）。
    DUP_BASELINE = {('04三国志文白/现代文', '004_文白对照_卷四·魏书四', '005_文白对照_卷五·魏书五'),
                    ('23元史/现代文', '088_卷八十八_志第三十八_百官四', '090_卷九十_志第四十_百官六')}
    if data.exists():
        import hashlib
        from collections import defaultdict
        _var = re.compile(r'(_译|_en|_白话|_现代汉语|_文白对照|_文白)$')
        _g = defaultdict(list)
        for _p in data.rglob('*.md'):
            try:
                _ls = [l.strip() for l in _p.read_text(encoding='utf-8', errors='replace').splitlines()
                       if l.strip()]
            except OSError:
                continue
            _b = ''.join(_ls[2:]) if len(_ls) > 2 else ''.join(_ls)
            if len(_b) < 600:
                continue
            _pre = re.sub(r'\s', '', _b)[:600]
            _g[(str(_p.parent.relative_to(data)).replace('\\', '/'),
                hashlib.md5(_pre.encode()).hexdigest())].append(
                _var.sub('', _p.stem))
        _found = set()
        for (_d, _), _v in _g.items():
            if len(_v) > 1 and len(set(_v)) > 1:
                _found.add((_d,) + tuple(sorted(set(_v))))
        _new = sorted(g for g in _found if g not in DUP_BASELINE)
        if _new:
            lines.append('FAIL  data/ 出现新的「整卷正文开头雷同」：%s——同书不同卷正文 600 字开头'
                         '完全相同，多为复制/错置，须核对原件（基线见 selftest.py DUP_BASELINE）'
                         % '；'.join('%s【%s】' % (d, '＋'.join(v)) for d, *v in _new))
            fails += 1
        else:
            _gone = DUP_BASELINE - _found
            lines.append('OK    data/ 无新增「整卷正文开头雷同」（基线 %d 处已知：三国志卷四／卷五、'
                         '元史卷八十八／卷九十）%s'
                         % (len(DUP_BASELINE),
                            '；另有 %d 处基线已消失，可下调基线' % len(_gone) if _gone else ''))

    # 06 表卷映射完整性：占位/仅文字卷中，属点校本二十四史范围内者应带机读稿册号
    t06 = ROOT / '06-表卷与原件索引.md'
    if t06.exists():
        rows = []
        for l in t06.read_text(encoding='utf-8').splitlines():
            p = l.split('｜')
            if len(p) >= 6 and p[3] in ('占位', '仅文字'):
                rows.append(p)
        mapped = sum(1 for p in rows if '表体见 06 机读稿' in p[4])
        # 新元史不在点校本二十四史内，无 PDF/OCR，允许无册号
        unmapped_books = {p[0] for p in rows if '表体见 06 机读稿' not in p[4]}
        lines.append('OK    06 占位/仅文字 %d 卷，其中 %d 卷已映射机读稿册号' % (len(rows), mapped))
        unexpected = unmapped_books - {'25新元史文白'}
        if unexpected:
            lines.append('        WARN 以下书有占位卷未映射册号（应属点校本范围，检查 check_tables.py 卷次解析）：%s'
                         % '、'.join(sorted(unexpected)))
        else:
            lines.append('        OK   未映射者仅《新元史》（点校本未收，符合预期）')
        # 机读稿第二段必须显式声明「机器识别」，否则用户可能误当原文引用
        t06t = t06.read_text(encoding='utf-8')
        if '机器识别' not in t06t:
            lines.append('        FAIL 06 第二段缺「机器识别」声明——用户可能把机读稿当原文引用！')
            fails += 1
        else:
            lines.append('        OK   06 已显式标注「机器识别」')
        # 结构不变量（模仿 05）：一级段用 `# `、数据行纯 `｜` 分隔，不得混入 markdown 表格
        md_table = [l for l in t06t.splitlines() if l.strip().startswith('|') or re.match(r'^\s*\|?[-\s:|]{5,}\|', l)]
        if md_table:
            lines.append('        FAIL 06 混入 markdown 表格行 %d 条——应与 05 同款（纯 ｜ 数据行，无表头/分隔线）' % len(md_table))
            fails += 1
        else:
            lines.append('        OK   06 数据行均为 ｜ 分隔（与 05 同款，无 markdown 表格）')

    # 05 结构不变量（曾出三个真 bug，务必守住）
    t05 = ROOT / '05-卷名总表.md'
    if t05.exists():
        from collections import Counter
        raw = t05.read_text(encoding='utf-8').splitlines()
        data = [l for l in raw if l.strip() and not l.startswith(('#', '>')) and '｜' in l]
        # ① 不得有完全重复行（曾因主题速查段与全卷目段重复而溢出 1649 行）
        dup = [k for k, v in Counter(data).items() if v > 1]
        if dup:
            lines.append('FAIL  05 有 %d 条完全重复行（主题速查与全卷目重复），请重跑 build_volume_index.py' % len(dup))
            fails += 1
        else:
            lines.append('OK    05 无重复行（%d 条数据行）' % len(data))
        # ② 类型标注不得自相矛盾：类型=志/表 但「路径**或篇名**」含列传
        #    （曾错 811 条：kind_of 把「列传」判在「志」之前；
        #      bug 变体又会把列传信息留在篇名列而路径列已净化，故两列都查）
        five = [l for l in data if len(l.split('｜')) == 5]
        bad_kind = [l for l in five
                    if l.split('｜')[2] in ('志', '表')
                    and ('列传' in l.split('｜')[3] or '列传' in l.split('｜')[4])]
        if bad_kind:
            lines.append('FAIL  05 有 %d 条类型误判（志/表 但路径或篇名含列传），检查 kind_of 的判定顺序' % len(bad_kind))
            fails += 1
        else:
            lines.append('OK    05 类型标注无「志/表↔列传」矛盾（路径与篇名两列均已查）')
        # ②b 类型分布绝对锁：任何 kind_of 回归都会让志≈编年+本纪合计或表≠166
        #     （确立值由 build_volume_index.py 在健康态跑出，随书库增补而更新）
        kind_dist = Counter(l.split('｜')[2] for l in five)
        expect = {'志': 767, '列传': 2432, '表': 166}
        drift = {k: (kind_dist.get(k, 0), v) for k, v in expect.items() if kind_dist.get(k, 0) != v}
        if drift:
            lines.append('FAIL  05 类型分布漂移 %s（若确为书库增补，请同步 selftest 期望值）'
                         % '；'.join('%s 现%d≠期%d' % (k, a, b) for k, (a, b) in drift.items()))
            fails += 1
        else:
            lines.append('OK    05 类型分布锁定（志767/列传2432/表166）')
        # ③ 篇名推测串不应泛滥（曾有 871 条把文件名里现成的「列传第X」覆盖成内容推测）
        guess = [l for l in five if '篇名未载' in l.split('｜')[3]]
        if len(guess) > 200:
            lines.append('FAIL  05 有 %d 条「篇名未载」推测串（>200 说明文件名中的真篇名被覆盖），'
                         '检查 extract 是否误弃 cand' % len(guess))
            fails += 1
        else:
            lines.append('OK    05 篇名推测串 %d 条（≤200，文件名真篇名未遭覆盖）' % len(guess))

    # 04 书节目录自洽：声明的行号区间必须真的落在对应书节，且「N 窗」＝区间内窗口行数
    #   （曾出 bug：lo 取到书节标题行，导致 N 比实际窗口数多 1；此处永久锁死）
    t04 = ROOT / '04-故事精选索引.md'
    if t04.exists():
        raw4 = t04.read_text(encoding='utf-8', errors='replace').splitlines()
        spans = []
        for ln in raw4:
            m = re.match(r'^(\S+)｜(\d+)–(\d+)｜(\d+) 窗$', ln.strip())
            if m:
                spans.append((m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))))
        bad = []
        for bk, lo, hi, n in spans:
            rows = [x for x in raw4[lo - 1:hi] if x.startswith(bk + '\\')]
            if not (0 < lo <= len(raw4) and 0 < hi <= len(raw4)) or len(rows) != n \
                    or raw4[lo - 2].strip() != '## ' + bk:
                bad.append('%s(声明%d–%d｜%d窗，实得%d窗)' % (bk, lo, hi, n, len(rows)))
        if len(spans) != 27 or bad:
            lines.append('FAIL  04 书节目录不自洽：%d 条书节%s——请重跑 build_story_index.py'
                         % (len(spans), ('，异常：' + '；'.join(bad[:6])) if bad else '（应为 27 条）'))
            fails += 1
        else:
            lines.append('OK    04 书节目录自洽（27 部书节，行号区间与「N 窗」均与正文吻合，合计 %d 窗）'
                         % sum(s[3] for s in spans))

    # 讲史入口**不得自相矛盾**：曾同时存在两条互斥的取窗路径——`00-目录索引.md` 授权
    # "Read `04` 随机 offset 取行 → 即可开讲"，而 `SKILL.md` 要求走 `pick_story.py` 抽签，
    # `04` 的书节目录标题又写"先在此选书"。模型于是**先自选一窗、又抽一窗 → 讲两遍故事**，
    # 且自选必然偏向通鉴/史记。措辞改对只是一时，此处锁死：重犯即 FAIL。
    #
    # 2026-09-23 升级：把"讲个故事"误触发也纳入。用户只说"讲个故事"（未限定历史），
    # 模型却同时走了 skill 抽签路径＋自身语言能力自由发挥——两条路各讲一遍、还把小说带进来。
    # 故授权词从"自己取窗"扩到"自由发挥/凭记忆/自己讲"一类，触发面从"故事精选索引/以『讲故事』
    # 开头"扩到含"讲个故事/讲史/抽签/pick_story"的行，并加**入口裁决规则在位**的正向断言。
    #
    # 判据用**语义**而非固定串（写死串会误伤：生成脚本的**注释**里为说明"别再这么写"，
    # 本身就会引用那个句式；我第一版就因此自造了两个假阳性）。
    NEG_WORDS = ('不要', '禁止', '不许', '不得', '不是', '别再', '严禁', '不准', '勿')
    AUTH_WORDS = ('随机', '自己挑', '自己选', '先在此选书', '凭印象', '自行挑',
                  '自由发挥', '凭记忆', '自己讲', '自己发挥', '即兴', '自行创作')
    TRIG_WORDS = ('故事精选索引', '讲故事', '讲个故事', '讲一个故事', '讲史', '抽签', 'pick_story')

    # ---- D4（2026-09-29 修）：判据从「按物理行」改成「按逻辑块」 ----
    # 旧实现逐行找授权词，**折行**会把授权词与否定词切到两行，于是两个方向都错：
    #   ① 假阳：`讲史时可以自己挑书，` ／ `但不要挑通鉴` —— 首行含授权词、无否定词 ⇒ 误报；
    #   ② 假绿：`讲史的时候，` ／ `你可以自己挑一本书` —— 授权词所在行不含触发词 ⇒ 漏报。
    # 故先按 markdown 语义切块（空行／新列表项／新标题／表格行 起新块；缩进续行并入上一块），
    # 再在**块**级别判「触发词 ∧ 授权词 ∧ ¬否定词」。报错仍给块的**起始行号**（保持 文件:行 格式）。
    _BLK_START = re.compile(r'^\s*(?:[-*+]\s|\d+[.)]\s|#{1,6}\s|\|)')

    def _blocks(text):
        """md → [(块的起始行号, 块文本)]。只切块，不判断。"""
        out, start, buf = [], 0, []

        def flush():
            if buf:
                out.append((start, '\n'.join(buf)))
                del buf[:]

        for i, ln in enumerate(text.splitlines(), 1):
            if not ln.strip():
                flush()
                continue
            if _BLK_START.match(ln) and buf:
                flush()
            if not buf:
                start = i
            buf.append(ln)
        flush()
        return out

    def _entry_viol(ts):
        """讲史语境**块**里"授权绕过脚本/自由发挥"的违规清单（按逻辑块判，见上面的 D4 说明）。"""
        bad = []
        for ln0, blk in _blocks(ts):
            if any(t in blk for t in TRIG_WORDS):
                if any(a in blk for a in AUTH_WORDS) and not any(n in blk for n in NEG_WORDS):
                    bad.append((ln0, blk.replace('\n', ' ').strip()[:56]))
        return bad

    # 反问用户"要讲什么朝代/类型"＝把抽签这一步推回给用户，与"直接抽签开讲"互斥。
    ASK_WORDS = ('先澄清', '先问', '询问用户', '问用户', '想听哪个', '想听什么',
                 '你想听', '先确认用户', '问一下用户')

    def _ask_viol(ts):
        """讲史语境**块**里"反问用户要什么"的违规清单（按逻辑块判，同 D4）。"""
        bad = []
        for ln0, blk in _blocks(ts):
            if any(t in blk for t in TRIG_WORDS):
                if any(a in blk for a in ASK_WORDS) and not any(n in blk for n in NEG_WORDS):
                    bad.append((ln0, blk.replace('\n', ' ').strip()[:56]))
        return bad

    anti = []
    # ⚠ 2026-10-01：本条原要求 SKILL.md 含字面串「不能执行命令」。改写分流段时该措辞换成了
    #   「确实没有执行工具」（判据从"命令能不能跑"改为"手上有没有那个工具"，见 DISPATCH_RULES），
    #   旧串随之消失、此处误报 FAIL。**改的是措辞不是语义**，故把被断言的串同步到现文；
    #   分流段本身的守卫交给上面的 DISPATCH_RULES（语义判据，不是字面绑定）。
    for f, need in ((ROOT / 'SKILL.md', ('pick_story.py', '一次抽签只讲一个故事', '先裁决入口', '非真实历史', '不反问', '确实没有执行工具', '禁止惯性连带', '特征匹配', '全打勾', '人物辨识', '避唐讳', '照录必加引号', '「读」不受此限', '趣味', '评点', '让人愿意听完', '独有引语反查')),
                    (ROOT / '00-目录索引.md', ('讲故事：**必须走', 'pick_story.py', '不反问')),
                    (ROOT / '04-故事精选索引.md', ('不是选书依据', 'pick_story.py'))):
        if not f.exists():
            anti.append('缺 %s' % f.name)
            continue
        ts = f.read_text(encoding='utf-8', errors='replace')
        for k in need:
            if k not in ts:
                anti.append('%s 未写明「%s」' % (f.name, k))
        for i, msg in _entry_viol(ts):
            anti.append('%s:%d 又在授权绕过脚本/自由发挥：「%s」' % (f.name, i, msg))
        for i, msg in _ask_viol(ts):
            anti.append('%s:%d 又在反问用户要什么（应直接抽签开讲）：「%s」' % (f.name, i, msg))

    # 同一判据再扫**整个模型可见面**：根目录 md（`_` 前缀的私有/报告除外）＋ references/，
    # 它们都在 `--source index` 检索源里、都会被模型读到——只在三处关键文档上设闸会漏。
    wide = [p for p in sorted(ROOT.glob('*.md')) if not p.name.startswith('_')]
    wide += sorted((ROOT / 'references').glob('*.md'))
    for f in wide:
        for i, msg in _entry_viol(f.read_text(encoding='utf-8', errors='replace')):
            anti.append('%s:%d 又在授权绕过脚本/自由发挥：「%s」' % (f.name, i, msg))
        for i, msg in _ask_viol(f.read_text(encoding='utf-8', errors='replace')):
            anti.append('%s:%d 又在反问用户要什么（应直接抽签开讲）：「%s」' % (f.name, i, msg))

    # 负向注入：判据必须能抓到"授权凭记忆/想象自己讲"的措辞——没被负向测过的闸等于没有闸。
    # 探针用**内存字符串**、不落盘，避免自污染 `--source index` 检索源。
    probes = [
        ('讲个故事时，你可以自由发挥、凭记忆创作一段即可', True),
        ('讲故事：先在此选书，凭印象挑一个开讲', True),
        ('讲史时自行创作、即兴发挥也可以', True),
        ('讲故事：必须走 pick_story.py，禁止凭记忆发挥', False),
        ('讲史入口裁决：不得自由发挥，只照窗口复述', False),
        # 2026-09-29：模型把「严禁凭记忆」写成唯一否定词（同句没有「不要/禁止」）时被判违规——
        # 这是判据的盲区（严禁＝显式禁止，语义上就是 NEG）。补进 NEG_WORDS 后正负两向都要成立。
        ('讲史时严禁凭印象自己挑书', False),
        ('讲史时凭印象自己挑书开讲', True),
        # D4 回归（2026-09-29）：**折行**样本——旧「按物理行」判据在这两条上必然翻车，
        # 「按逻辑块」才对。①是折行假阳（授权词在首行、否定词在次行），②是折行假绿
        # （触发词在首行、授权词在次行）。
        ('讲史时可以自己挑书，\n但不要挑通鉴', False),
        ('讲史的时候，\n你可以自己挑一本书', True),
    ]
    for txt, expect_hit in probes:
        got = bool(_entry_viol(txt))
        if got != expect_hit:
            anti.append('负向注入判据失效：%r 应%s实%s' % (txt, '命中' if expect_hit else '放过', '命中' if got else '放过'))

    ask_probes = [
        ('讲个故事时，先问用户想听哪个朝代', True),
        ('讲史前先澄清一句：想听正史还是演义', True),
        ('讲个故事不反问，直接抽签开讲', False),
        ('讲故事：不反问朝代，直接抽签开讲', False),
        # D4 回归：折行样本（同 _entry_viol）
        ('讲史的时候，\n先问用户想听哪个朝代', True),
        ('讲史时可以闲聊几句，\n但不要先问用户想听哪个朝代', False),
    ]
    for txt, expect_hit in ask_probes:
        got = bool(_ask_viol(txt))
        if got != expect_hit:
            anti.append('反问判据负向注入失效：%r 应%s实%s' % (txt, '命中' if expect_hit else '放过', '命中' if got else '放过'))

    if anti:
        lines.append('FAIL  讲史入口自相矛盾（%d 项）：%s' % (len(anti), '；'.join(anti[:5])))
        fails += 1
    else:
        lines.append('OK    讲史入口单一（只指向 pick_story.py，无"自己取窗/自由发挥"授权，且入口裁决规则在位）')

    # 索引规模口径：文档声称的「02 记字条目数 / 03 人数」必须与实测吻合。
    #   曾出现同一件事三个数：00 写"3.94 万条"、SKILL 写"4.1 万条"、实测 4.07 万。
    #   数字漂移不会让任何功能坏掉，但会让"库有多大"这类判断失真，故此处锁死。
    def _scale_claim(fp, fname, unit):
        for ln in fp.read_text(encoding='utf-8', errors='replace').splitlines():
            if fname in ln:
                m = re.search(r'(\d+(?:\.\d+)?)\s*万' + unit, ln)
                if m:
                    return float(m.group(1))
        return None

    t02 = ROOT / '02-人物字号索引.md'
    t03 = ROOT / '03-人物出现索引.md'
    if t02.exists() and t03.exists():
        n_zi = len([l for l in t02.read_text(encoding='utf-8', errors='replace').splitlines()
                    if '｜字：' in l])
        n_ppl = len({l.split('｜')[0] for l in t03.read_text(encoding='utf-8', errors='replace')
                     .splitlines() if '｜' in l and not l.startswith(('#', '>'))})
        bad_scale = []
        for fname, fp, doc, unit, real in (
                ('SKILL.md', ROOT / 'SKILL.md', '02-人物字号索引.md', '条', n_zi / 1e4),
                ('SKILL.md', ROOT / 'SKILL.md', '03-人物出现索引.md', '人', n_ppl / 1e4),
                ('00-目录索引.md', ROOT / '00-目录索引.md', '02-人物字号索引.md', '条', n_zi / 1e4),
                ('00-目录索引.md', ROOT / '00-目录索引.md', '03-人物出现索引.md', '人', n_ppl / 1e4)):
            got = _scale_claim(fp, doc, unit)
            if got is None:
                if fname == 'SKILL.md':      # SKILL 的索引表必须写规模；00 写了才核
                    bad_scale.append('%s 未写「%s」的规模' % (fname, doc))
                continue
            if abs(got - real) > 0.035:
                bad_scale.append('%s 对「%s」作 %.2f 万%s，实测 %.2f 万' % (fname, doc, got, unit, real))
        if bad_scale:
            lines.append('FAIL  索引规模口径不一致：%s——改完索引请同步文档数字' % '；'.join(bad_scale))
            fails += 1
    else:
        lines.append('OK    索引规模口径一致（02 记字 %.2f 万条／03 %.2f 万人，SKILL 与 00 的声称均吻合）'
                     % (n_zi / 1e4, n_ppl / 1e4))

    # 「绕进去出不来」的结构性防线：脚本不得**无界**。
    #   (a) `while True` —— 必然无界，除非内部有 break（约定：用带条件的循环代替）；
    #   (b) 跨脚本 `subprocess.run` 必须带 `timeout=`——否则子脚本一挂（死循环/等文件锁），
    #       父脚本永远等下去：没有输出、没有退出码、看不出卡在哪一步。`make_indexes.py` 曾两处都没超时。
    unb = []
    for f in sorted((ROOT / 'tools').glob('*.py')):
        src = f.read_text(encoding='utf-8', errors='replace')
        # ⚠ 只认**语句行**：剥掉 # 注释、要求行首是 while。第一版扫全文，
        #   把本文件自己的注释与报错文案（"含 `while True`"）判成了真循环——假阳性。
        #   这是"字面量误伤"的第三种形态：文档授权句／生成脚本注释／代码注释与文案。
        for ln_i, code_ln in enumerate(src.splitlines(), 1):
            code = code_ln.split('#')[0].strip()
            if code.startswith('while') and re.search(r'\bwhile\s+True\b', code):
                unb.append('%s:%d `while True`（无界循环，除非块内有 break 请改用带条件循环）'
                           % (f.name, ln_i))
        for m in re.finditer(r'subprocess\.run\(', src):
            stmt = src[m.start():m.start() + 400].split('\n\n')[0]
            if 'timeout=' not in stmt:
                unb.append('%s:%d subprocess.run 未设 timeout=' % (f.name, src[:m.start()].count('\n') + 1))
    if unb:
        lines.append('FAIL  存在"可能出不来的循环"（%d 处）：%s' % (len(unb), '；'.join(unb[:5])))
        fails += 1
    else:
        lines.append('OK    无界循环防线：无 `while True`，所有跨脚本调用均设 timeout=')

    # 重建链的依赖顺序：make_indexes.py 把顺序固化成一条命令，顺序错会产出**看起来正常**的
    # 错索引（03 人物出现索引要读 05 卷名总表拿卷次；06 要读 05 ＋ 机读稿目录）。
    # ⚠ 2026-09-23 修：CHAIN 原把 build_person_mentions 排在 build_volume_index 之前。
    try:
        mi = (ROOT / 'tools' / 'make_indexes.py').read_text(encoding='utf-8', errors='replace')
        chain = re.findall(r'\("([\w.]+\.py)"', mi)
        need = ['build_name_index.py', 'build_volume_index.py', 'build_person_mentions.py',
                'build_story_index.py', 'check_tables.py', 'build_emperor_index.py']
        # 下游 → 必须先跑完的上游（03 读 02＋05 取卷次；06 读 05＋机读稿目录；07 读 05 取本纪卷）
        DEP = {'build_person_mentions.py': ('build_name_index.py', 'build_volume_index.py'),
               'check_tables.py': ('build_volume_index.py',),
               'build_emperor_index.py': ('build_volume_index.py',)}
        missing = [x for x in need if x not in chain]
        order_bad = ['%s 早于其依赖 %s' % (d, u) for d, ups in DEP.items() for u in ups
                     if d in chain and u in chain and chain.index(u) > chain.index(d)]
        if missing or order_bad:
            lines.append('FAIL  make_indexes.py 重建链有误：缺 %s；顺序错 %s——'
                         '请照 references/maintenance.md §1 的依赖顺序改 CHAIN（现：%s）'
                         % (missing or '无', '；'.join(order_bad) or '无', '→'.join(chain)))
            fails += 1
        else:
            lines.append('OK    make_indexes.py 重建链依赖顺序正确（%s）' % '→'.join(chain))
    except OSError as e:
        lines.append('        WARN make_indexes.py 读取失败：%s' % str(e)[:60])

    # 01/05/06 路径可达性：索引指向的文件必须真实存在（索引与语料不同步时立刻暴露）
    ddir = ROOT / 'data'
    if ddir.exists():
        path_sets = {}
        for fname, how in (('01-卷目速查.md', 'plain'), ('05-卷名总表.md', 'col5'),
                           ('06-表卷与原件索引.md', 'col6')):
            p = ROOT / fname
            if not p.exists():
                continue
            paths = []
            for ln in p.read_text(encoding='utf-8', errors='replace').splitlines():
                if not ln.strip() or ln.startswith(('#', '>')):
                    continue
                # ⚠ 01 的卷题级行形如「<路径>｜含：…」（传主/内容提示），**路径只取 ｜ 之前**；
                #    旧判定用 endswith('.md') 会在加提示后失效（2026-09-22 修）。
                if how == 'plain' and '\\' in ln:
                    cand = ln.split('｜')[0].strip()
                    if cand.endswith('.md'):
                        paths.append(cand)
                else:
                    c = ln.split('｜')
                    i = {'col5': 4, 'col6': 5}.get(how, -1)
                    if i > 0 and len(c) > i and c[i].strip().endswith('.md'):
                        paths.append(c[i].strip())
            path_sets[fname] = set(paths)
            miss = [x for x in paths if not (ddir / x).exists()]
            if miss:
                lines.append('FAIL  %s 有 %d 条路径在 data/ 下不存在（如 %s）——索引与语料不同步'
                             % (fname, len(miss), miss[0]))
                fails += 1
            else:
                lines.append('OK    %s 路径全部可达（%d 条）' % (fname, len(paths)))
        a, b = path_sets.get('01-卷目速查.md'), path_sets.get('05-卷名总表.md')
        if a and b and a != b:
            lines.append('FAIL  01 与 05 覆盖的文件集合不一致（差 %d 个）——两表应同为全库清单'
                         % len(a ^ b))
            fails += 1
        # 反向：data/ 下每个 md 都必须已进索引（语料增补而漏重跑时，正向检查查不出来）
        if a:
            corpus_rel = {str(p.relative_to(ddir)).replace('/', '\\')
                          for p in ddir.rglob('*.md')}
            extra = corpus_rel - a
            if extra:
                lines.append('FAIL  data/ 有 %d 个 md 未收录进 01/05 索引（如 %s）——'
                             '语料增补后未重跑 build_volume_index.py'
                             % (len(extra), sorted(extra)[0]))
                fails += 1
            else:
                lines.append('OK    data/ 语料与 01/05 索引双向一致（%d 个 md）' % len(corpus_rel))
        # 01 与 05 的「（含：…）」提示必须逐条一致——两表同源（同一次 build 产出），
        # 任一表被手工改动或脚本改动只落一边时会立刻暴露（2026-09-22 加）。
        h05, h01 = {}, {}
        p05 = ROOT / '05-卷名总表.md'
        p01 = ROOT / '01-卷目速查.md'
        if p05.exists():
            for l in p05.read_text(encoding='utf-8').splitlines():
                c = l.split('｜')
                if len(c) == 5 and '\\' in c[4]:
                    m = re.search(r'（含：(.+)）$', c[3])
                    if m:
                        h05[c[4].strip()] = m.group(1)
        if p01.exists():
            for l in p01.read_text(encoding='utf-8').splitlines():
                # ⚠ 01 的**头部说明行**本身含「｜含：…」字样，必须要求左侧是真实路径（含反斜杠）
                left, _, hint = l.partition('｜含：')
                if hint and '\\' in left:
                    h01[left.strip()] = hint.strip()
        if h05 != h01:
            d = sorted(set(h05) ^ set(h01))
            lines.append('FAIL  01 与 05 的「含：」内容提示不一致（仅一侧有 %d 条，如 %s）'
                         % (len(d), d[0] if d else '键相同但值不同'))
            fails += 1
        else:
            lines.append('OK    01 与 05 的「含：」内容提示逐条一致（%d 条）' % len(h05))

    # 06 册表 vs 机读稿目录：册号必须双向对齐，且第一段引用的册号都能回查到原件 PDF
    if t06.exists() and 't06t' in dir():
        simp = ROOT / '06-表卷OCR机读稿（简体）'
        avail = {q.name.split('_')[0] for q in simp.glob('*.md')} if simp.exists() else set()
        nsec = sum(1 for l in t06t.splitlines() if l.startswith('# ')) - 1
        if nsec != 2:
            lines.append('        WARN 06 正文一级段为 %d 段，头部声明「两段」——请同步头部与 check_tables.py' % nsec)
        if avail:
            tbl = {l.split('｜')[0] for l in t06t.splitlines() if re.match(r'^\d{3}｜', l)}
            ref = set(re.findall(r'机读稿\s*(\d{3})册', t06t))
            if tbl != avail:
                lines.append('FAIL  06 册表与机读稿目录不一致：目录有册表缺 %s；册表有目录缺 %s'
                             % (sorted(avail - tbl) or '无', sorted(tbl - avail) or '无'))
                fails += 1
            elif ref - tbl:
                lines.append('FAIL  06 第一段引用了册表中不存在的册号 %s——原件 PDF 将无处可查'
                             % sorted(ref - tbl))
                fails += 1
            else:
                lines.append('OK    06 册表 %d 册与机读稿目录一致，第一段引用册号均可回查原件 PDF' % len(tbl))

    # 统一检索入口冒烟测试：rag.py locate 必须能跑通并命中已知样本
    # （locate 原 tools/q.py 已并入 rag.py 作子命令；q.py 只是转发薄壳）
    rp2 = ROOT / 'tools' / 'rag.py'
    if rp2.exists() and not QUICK:
        import subprocess
        try:
            r = subprocess.run([sys.executable, str(rp2), 'locate', '魏徵', '--source', 'index', '--limit', '2'],
                               capture_output=True, timeout=180)
            out = r.stdout.decode('utf-8', 'replace')
            if r.returncode == 0 and '字：玄成' in out:
                lines.append('OK    tools/rag.py locate 可用（跨源定位，魏徵样本命中）')
            else:
                lines.append('FAIL  tools/rag.py locate 异常（rc=%d）：%s' % (r.returncode, out[:150]))
                fails += 1
        except Exception as e:
            lines.append('FAIL  tools/rag.py locate 调用失败：%s' % str(e)[:90])
            fails += 1

        # 选项写在查询词**前面**也必须生效（2026-09-24 实测）：原先 cmd_locate 无条件取
        # args[0] 当查询词，`locate --first 曹髦` 于是拿字面量 '--first' 去全库搜 → 0 命中，
        # 输出长得像"库里查无此人"——这类假阴性正是逼模型凭记忆瞎编的源头。
        try:
            _kw = ['魏徵', '--first']
            a = subprocess.run([sys.executable, str(rp2), 'locate'] + _kw,
                               capture_output=True, timeout=180)
            b = subprocess.run([sys.executable, str(rp2), 'locate'] + _kw[::-1],
                               capture_output=True, timeout=180)
            _o = b.stdout.decode('utf-8', 'replace')
            # 只比**去掉耗时**后的输出：首行末尾「（1.37s）」两次跑必然不同，
            # 整串相等是自测写错了（2026-09-24 首次跑即因 0.02s 之差假 FAIL）。
            _strip = lambda s: re.sub(r'（[0-9.]+s）', '', s.decode('utf-8', 'replace'))
            if a.returncode == 0 and _strip(a.stdout) == _strip(b.stdout) and '「魏徵」' in _o:
                lines.append('OK    rag.py locate 选项与词的位置无关（前后两种写法同结果）')
            else:
                lines.append('FAIL  rag.py locate 把写在词前的选项当成了查询词：'
                             'rc=%d/%d，%s' % (a.returncode, b.returncode,
                                               b.stdout[:120].decode('utf-8', 'replace')))
                fails += 1
        except Exception as e:
            lines.append('FAIL  rag.py locate 选项位置冒烟失败：%s' % str(e)[:90])
            fails += 1

        # ★ 「独有引语反查」跳板必须**真能跑通**，不能只是文档里的一句愿望。
        #   背景（2026-09-24）：SKILL.md 原先对「这是谁」类谜题只写了"先检索人名"，
        #   而谜题里**根本没有名字可查**（名字正是待猜项）→ 规则无从执行、模型回落记忆，
        #   于是石勒被认成拓跋珪。补上的正路是：拿题干里的**独有引语**当检索词反查。
        #   本项实跑 SKILL.md 里那句示例，要求它今天**仍然唯一命中**（候选=1 且落在
        #   晋书卷105 石勒本传）——语料一变、引语不再唯一，跳板就悄悄失效了；
        #   只查"串还在不在"（上面的 need 列表）抓不到这一点，故必须实跑。
        try:
            # ⚠ 检索词从 SKILL.md **现文**里取，不在脚本里另抄一份——
            #   抄一份就会漂移：文档换了示例词、闸门却还在测老词，通过得毫无意义。
            #   本闸门验的是"文档此刻承诺的那条路，今天走不走得通"。
            _sk = (ROOT / 'SKILL.md').read_text(encoding='utf-8', errors='replace')
            _mm = re.search(r'`python tools\\rag\.py "([^"]+)"`', _sk)
            probe_word = _mm.group(1) if _mm else None
            if not probe_word:
                lines.append('FAIL  SKILL.md「独有引语反查」条里找不到可实测的示例命令'
                             '（应形如 `python tools\\rag.py "…"`）——跳板无从验证')
                fails += 1
            else:
                ra = subprocess.run([sys.executable, str(rp2), probe_word],
                                    capture_output=True, timeout=300)
                oa = (ra.stdout + ra.stderr).decode('utf-8', 'replace')
                ma = re.search(r'候选\s*(\d+)\s*文件', oa)
                n_cand = int(ma.group(1)) if ma else -1
                if ra.returncode == 0 and n_cand == 1 and '有据' in oa:
                    lines.append('OK    谜题反查跳板可用（文档示例「%s」唯一命中且判有据）' % probe_word)
                else:
                    lines.append('FAIL  谜题反查跳板失效（文档示例「%s」rc=%d，候选=%s，有据=%s）'
                                 '——示例词已不再唯一，「这是谁」类问题又没有可执行的检索路径了'
                                 % (probe_word, ra.returncode, n_cand, '有据' in oa))
                    fails += 1
            # 负向反测：判据必须能区分"唯一"与"不唯一"。拿一个必然多命中的普通词跑，
            # 若它也报"唯一"，说明上面的 n_cand 提取是假的（假通过）。
            rn = subprocess.run([sys.executable, str(rp2), '高祖', '--count'],
                                capture_output=True, timeout=300)
            on = (rn.stdout + rn.stderr).decode('utf-8', 'replace')
            mn = re.search(r'^\s*(\d+)\s+\d+\s+高祖', on, re.M)
            n_neg = int(mn.group(1)) if mn else -1
            if n_neg > 1:
                lines.append('OK    反查判据可区分（普通词「高祖」命中 %d 个文件，不会被误判为唯一）' % n_neg)
            else:
                lines.append('FAIL  反查判据失效：普通词「高祖」竟报 %s，唯一性判据不可信' % n_neg)
                fails += 1
        except Exception as e:
            lines.append('FAIL  谜题反查跳板冒烟失败：%s' % str(e)[:90])
            fails += 1

        # 零命中路径（SKILL.md 规则⑥：「零命中的名词一律答本库未载，禁止凭记忆解释」）：
        #   光断言"这句话在文档里"是假闸门；必须实跑一个**文档现文里点名的零命中词**，
        #   确认工具今天仍输出「判定：无据 / 本库未见」。（2026-09-24 用户："不会就说说不会，看看能不能成功"）
        try:
            # ⚠ 词从 SKILL.md **现文**取，不在脚本里另抄（抄了就会漂移）。
            _sk2 = (ROOT / 'SKILL.md').read_text(encoding='utf-8', errors='replace')
            _mz = re.search(r'⑥[^\n]{0,160}?（如「([^」]+)」', _sk2)
            zero_word = _mz.group(1) if _mz else None
            if not zero_word:
                lines.append('FAIL  SKILL.md 规则⑥ 里找不到可实测的零命中示例词（应形如「戒石铭」）'
                             '——"不会就说不会"这条路无从验证')
                fails += 1
            else:
                rz = subprocess.run([sys.executable, str(rp2), zero_word],
                                    capture_output=True, timeout=300)
                oz = (rz.stdout + rz.stderr).decode('utf-8', 'replace')
                if '判定：无据' in oz and '本库未见' in oz:
                    lines.append('OK    零命中路径可用（文档示例「%s」→ 工具输出「判定：无据／本库未见」）'
                                 % zero_word)
                else:
                    lines.append('FAIL  零命中路径失效：文档示例「%s」未得「判定：无据」'
                                 '——"不会就说不会"在工具层已不成立' % zero_word)
                    fails += 1
            # 负向反测：判据须能区分"零命中"与"有命中"。拿必然命中的词跑，
            #   若它也被判零命中，说明上面的字符串判据是假通过。
            rn2 = subprocess.run([sys.executable, str(rp2), '苏轼', '--count'],
                                 capture_output=True, timeout=300)
            on2 = (rn2.stdout + rn2.stderr).decode('utf-8', 'replace')
            if '零命中' not in on2:
                lines.append('OK    零命中判据可区分（「苏轼」有命中，不会被误判为无据）')
            else:
                lines.append('FAIL  零命中判据失效：「苏轼」竟报零命中，判据不可信')
                fails += 1
        except Exception as e:
            lines.append('FAIL  零命中路径冒烟失败：%s' % str(e)[:90])
            fails += 1

        # 事件／制度类名词：SKILL.md 点名的示例（空印案）必须真能走「通称改写」、并且**取到刑法志原文**。
        #   为什么加：2026-09-25 用户贴截图——某环境问「空印案」，答成**宋代转运使留空白印章**、
        #   配一整套"制度性腐败"解释、**全篇零出处**；而空印案是明洪武十五年，《明史·卷九十四》
        #   有原文「十五年空印事发」。规则（事件类也要带出处）光写在正文是"字还在"，这里验"路能走"：
        #   词从 SKILL.md **现文**取（不另抄），实跑一遍，要求输出既有『通称改写』又有那句原文。
        try:
            _m_ev = re.search(r'事件／制度／案件类[^\n]{0,200}?如「([^」]+)」', _sk2)
            ev_word = _m_ev.group(1) if _m_ev else None
            if not ev_word:
                lines.append('FAIL  SKILL.md「事件／制度／案件类」规则里找不到可实测的示例词'
                             '（应形如「空印案」）——"事件类也要带出处"这条路无从验证')
                fails += 1
            else:
                rev = subprocess.run([sys.executable, str(rp2), ev_word],
                                     capture_output=True, timeout=300)
                oev = (rev.stdout + rev.stderr).decode('utf-8', 'replace')
                if '通称改写' not in oev or '十五年空印事发' not in oev:
                    lines.append('FAIL  事件类通称端到端失效：「%s」未走通称改写、或未取到刑法志原文'
                                 '（要求同时出现「通称改写」与「十五年空印事发」）——'
                                 '检查 tools/aliases_events.md 里该行是否被删或改坏' % ev_word)
                    fails += 1
                else:
                    lines.append('OK    事件类通称端到端可用（「%s」→ 通称改写 → 取到'
                                 '《明史·卷九十四》「十五年空印事发」原文）' % ev_word)
        except Exception as e:
            lines.append('FAIL  事件类通称端到端冒烟失败：%s' % str(e)[:90])
            fails += 1

        # 猜谜题交付闸（tools/check_guess.py）——硬条件：给答案须全中并声明「无不符」；
        #   有不符合（含「本库未载」）就只能答「无法唯一确定」。直接调 check() 做正负向单测（不跑 subprocess）。
        try:
            import importlib.util as _iu
            _spec = _iu.spec_from_file_location('_cg', str(ROOT / 'tools' / 'check_guess.py'))
            _cg = _iu.module_from_spec(_spec)
            _spec.loader.exec_module(_cg)
            # ⚠ 合格样例用**中性名**，不用真实史实人名——示例一旦携带某题的错误答案就成了误导。
            #   曾误用「赵佶」当合格样例：那是本题（六条线索猜皇帝）里答错的人，该题正解是刘粲。
            #   教训：闸门只看文本形态、不判真假，示例数据必须中性，否则闸门自己传播错答案。
            _p1 = _cg.check('答案：某甲\n① ✓ ② ✓ ③ ✓ ④ ✓ ⑤ ✓ ⑥ ✓\n逐条符合，无不符。')[0]
            _p2 = _cg.check('无法唯一确定。\n候选：朱棣（第6条不符）。')[0]
            _n1 = _cg.check('答案：孟昶\n⑤ 本库未见。')[0]
            _n2 = _cg.check('答案：朱棣\n⑥ 题面或有偏差，宜按前锋都督理解。')[0]
            if _p1 and _p2 and not _n1 and not _n2:
                lines.append('OK    猜谜交付闸可判（全中／弃权→合格；含「未见」仍给答案、替题面打补丁→不合格）')
            else:
                lines.append('FAIL  猜谜交付闸判据异常（正1=%s 正2=%s 负1=%s 负2=%s）'
                             % (_p1, _p2, _n1, _n2))
                fails += 1
        except Exception as e:
            lines.append('FAIL  猜谜交付闸冒烟失败：%s' % str(e)[:90])
            fails += 1

        # `--book` 逗号多值：正向（两书都纳入）＋负向（其中一个写错必须报错，不能静默缩窄范围）。
        # ⚠ 2026-09-23 修：retrieve() 早就能收列表，但 cmd_query/cmd_locate 只传字符串，
        #   `--book A,B` 被当成一个不存在的长串 → 扫 0 文件 → 假"无据"。只看"空范围"那道闸
        #   抓不到"多值里某一个写错"（scanned 仍 >0），故两项都要在这里钉死。
        try:
            rmb = subprocess.run([sys.executable, str(rp2), 'locate', '苏轼', '--source', 'data',
                                  '--book', '20宋史文白,24明史文白', '--files-only', '--limit', '0'],
                                 capture_output=True, timeout=180)
            omb = rmb.stdout.decode('utf-8', 'replace').replace('/', '\\')
            ok_books = ('20宋史文白' in omb) and ('24明史文白' in omb)
            # ⚠ 只判"两书都在"会被"--book 被整个忽略"骗过（不筛时全库都含这两本）；
            #   故同时要求**输出里不出现第三本书**——才算真的限住了范围。
            others = {l.split('\\')[1] for l in omb.splitlines()
                      if l.startswith('data\\') and len(l.split('\\')) > 2} - {'20宋史文白', '24明史文白'}
            if rmb.returncode == 0 and ok_books and not others:
                lines.append('OK    --book 逗号多值可用（两书都纳入，未夹带第三本）')
            else:
                lines.append('FAIL  --book 逗号多值失效（rc=%d，命中两书=%s，夹带=%s）：%s'
                             % (rmb.returncode, ok_books, '、'.join(sorted(others))[:60] or '无', omb[:120]))
                fails += 1
            rmc = subprocess.run([sys.executable, str(rp2), 'locate', '苏轼', '--source', 'data',
                                  '--book', '20宋史文白,24明史白话', '--files-only', '--limit', '3'],
                                 capture_output=True, timeout=180)
            omc = (rmc.stdout + rmc.stderr).decode('utf-8', 'replace')
            if rmc.returncode == 2 and '没匹配到任何文件' in omc:
                lines.append('OK    --book 多值里写错一个会报错（不静默缩窄范围）')
            else:
                lines.append('FAIL  --book 多值写错未拦住（rc=%d）：%s' % (rmc.returncode, omc[:120]))
                fails += 1
        except Exception as e:
            lines.append('FAIL  --book 多值冒烟失败：%s' % str(e)[:90])
            fails += 1

        # zhconv 可用性断言：繁简互通是文档承诺的能力，缺了会**静默**降级（原先体检测不出）。
        # 判据：查「张居正」（简体）必须同时命中 简体与繁体 机读稿——繁体命中只能靠 zhconv 转出「張居正」。
        try:
            r2 = subprocess.run([sys.executable, str(rp2), 'locate', '张居正', '--source', 'ocr',
                                 '--limit', '0', '--files-only'],
                                capture_output=True, timeout=180)
            o2 = r2.stdout.decode('utf-8', 'replace').replace('/', '\\')
            has_s = '06-表卷OCR机读稿（简体）\\' in o2
            has_t = '06-表卷OCR机读稿\\' in o2
            if r2.returncode == 0 and has_s and has_t:
                lines.append('OK    繁简互通可用（zhconv 在位：简体词同时命中繁体机读稿）')
            else:
                lines.append('FAIL  繁简互通失效（zhconv 缺失或降级？）——简体命中=%s 繁体命中=%s。'
                             '文档承诺的「繁简互通」不成立，务必检查隔离 venv 的 site-packages 注入'
                             % (has_s, has_t))
                fails += 1
        except Exception as e:
            lines.append('FAIL  繁简互通冒烟失败：%s' % str(e)[:90])
            fails += 1

        # 选项校验闸：拼错的取值型选项绝不能静默按默认值跑完（原先 `--source datta`
        # 被当作合法值 → 扫 0 文件 → 打印「判定：无据」＝一个错别字换来一个错误结论；
        # `--top` 漏参数时 `int(True)` 会静默变成 1）。
        try:
            for bad, why in ((['苏轼', '--source', 'datta', '--count'], '拼错的 --source'),
                             (['苏轼', '--top'], '缺参数的 --top'),
                             (['苏轼', '--book', '  '], '空 --book')):
                rb = subprocess.run([sys.executable, str(rp2)] + bad,
                                    capture_output=True, timeout=180)
                if rb.returncode == 2:
                    continue
                lines.append('FAIL  %s 未被拦下（rc=%d，应为 2）：%s'
                             % (why, rb.returncode, rb.stdout.decode('utf-8', 'replace')[:120]))
                fails += 1
            if not any(l.startswith('FAIL') and '未被拦下' in l for l in lines):
                lines.append('OK    检索选项校验生效（拼错/缺参/空值均退出码 2，不产出假"无据"）')
        except Exception as e:
            lines.append('FAIL  检索选项校验冒烟失败：%s' % str(e)[:90])
            fails += 1

        # 引文核验的标点容错：把真引文的句读去掉再核，必须仍判 OK。
        # ⚠ 2026-09-23 修：两段扫描的文件级预筛都按**带标点的字节子串**判，于是省略标点的
        #   真引文（模型抄写时常发生）判成「查无此文＝疑似编造」FAIL——按铁律 8「FAIL 必改」，
        #   等于逼作者删掉真原文。反向也要钉住：措辞不同的伪造引文仍须 FAIL。
        try:
            import tempfile
            tmpd2 = tempfile.gettempdir()
            vp = os.path.join(tmpd2, 'st_verify_punct.txt')
            with open(vp, 'w', encoding='utf-8') as _fh:
                _fh.write('《宋史·卷三百三十八》：「苏轼，字子瞻，眉州眉山人。」\n'
                          '同传又云「苏轼字子瞻眉州眉山人」（同一句去标点写法）。\n')
            rv = subprocess.run([sys.executable, str(rp2), 'verify', vp],
                                capture_output=True, timeout=300)
            ov = rv.stdout.decode('utf-8', 'replace')
            if rv.returncode == 0 and '未逐字出现' not in ov and '查无此文' not in ov:
                lines.append('OK    verify 容忍引文标点差异（真引文去标点仍判 OK）')
            else:
                lines.append('FAIL  verify 把去标点的真引文判成查无此文（rc=%d）：%s'
                             % (rv.returncode, ov[:200]))
                fails += 1
            fp2 = os.path.join(tmpd2, 'st_verify_fake.txt')
            with open(fp2, 'w', encoding='utf-8') as _fh:
                _fh.write('《宋史·卷三百三十八》：「苏轼字子瞻尝自号铁冠道人夜登赤壁而歌」。\n')
            rf = subprocess.run([sys.executable, str(rp2), 'verify', fp2],
                                capture_output=True, timeout=300)
            if rf.returncode == 1 and '疑似编造' in rf.stdout.decode('utf-8', 'replace'):
                lines.append('OK    verify 仍拦住措辞不同的伪造引文（未因宽松化而失效）')
            else:
                lines.append('FAIL  verify 宽松比对把伪造引文放过了（rc=%d）' % rf.returncode)
                fails += 1
        except Exception as e:
            lines.append('FAIL  verify 标点容错冒烟失败：%s' % str(e)[:90])
            fails += 1

        # 薄壳转发：老用法 tools/q.py 仍须可用
        qp = ROOT / 'tools' / 'q.py'
        if qp.exists():
            try:
                r3 = subprocess.run([sys.executable, str(qp), '魏徵', '--source', 'index', '--limit', '1'],
                                    capture_output=True, timeout=180)
                if r3.returncode == 0 and '魏徵' in r3.stdout.decode('utf-8', 'replace'):
                    lines.append('OK    tools/q.py 薄壳转发正常（已并入 rag.py locate）')
                else:
                    lines.append('FAIL  tools/q.py 薄壳转发异常（rc=%d）' % r3.returncode)
                    fails += 1
            except Exception as e:
                lines.append('FAIL  tools/q.py 调用失败：%s' % str(e)[:90])
                fails += 1
        else:
            lines.append('        INFO tools/q.py 已删除（rag.py locate 取代）——'
                         '请确认 SKILL.md／maintenance.md 里不再引用 q.py')
    elif not rp2.exists():      # 快档跳过时不报"缺少"——文件在，只是没跑冒烟
        lines.append('FAIL  缺少 tools/rag.py（检索与引文核验的核心工具）')
        fails += 1

    # 史实核查组件：事实卡目录 + factcheck.py 可用
    fc = ROOT / 'tools' / 'factcheck.py'
    facts = ROOT / 'tools' / 'facts'
    cards = sorted(p.stem for p in facts.glob('*.md')) if facts.is_dir() else []
    if fc.exists() and cards and not QUICK:
        import subprocess
        try:
            r = subprocess.run([sys.executable, str(fc), '--list'], capture_output=True, timeout=60)
            out = r.stdout.decode('utf-8', 'replace')
            if r.returncode == 0 and cards[0] in out:
                lines.append('OK    tools/factcheck.py 可用（事实卡 %d 张：%s）' % (len(cards), '、'.join(cards)))
            else:
                lines.append('FAIL  tools/factcheck.py --list 异常（rc=%d）' % r.returncode)
                fails += 1
        except Exception as e:
            lines.append('FAIL  tools/factcheck.py 调用失败：%s' % str(e)[:90])
            fails += 1

        # 用法错误必须退 2，不能退 1——退 1 是"史实 FAIL"的语义，调用方（SKILL.md 铁律 8）
        # 会把它当史实问题去改稿，而真正的原因是文件/卡名写错。⚠ 2026-09-23 修：原先
        # 路径不存在时直接抛 FileNotFoundError，退出码也是 1。
        try:
            import tempfile
            cases = [(['nope_st.txt', '--person', cards[0]], '稿件路径不存在'),
                     (['-', '--person', '查无此人卡'], '事实卡不存在'),
                     (['-', '--person', '../%s' % cards[0]], '卡名带路径')]
            bad_rc = []
            for argv, why in cases:
                r = subprocess.run([sys.executable, str(fc)] + argv,
                                   capture_output=True, timeout=60, input=b'')
                if r.returncode != 2:
                    bad_rc.append('%s（rc=%d≠2）' % (why, r.returncode))
            if bad_rc:
                lines.append('FAIL  tools/factcheck.py 用法错误未退码 2：%s——'
                             '退 1 会被当成"史实核查不通过"，误导作者去改正确的稿子' % '；'.join(bad_rc))
                fails += 1
            else:
                lines.append('OK    tools/factcheck.py 用法错误退码 2（与史实 FAIL 的 1 严格区分）')

            # 年表闸要真扫到年份：三位数年份（「300年」）与「元年/N年」纪年都曾静默漏检。
            tmpf = tempfile.gettempdir()
            fp = os.path.join(tmpf, 'st_fact_year.txt')
            with open(fp, 'w', encoding='utf-8') as _fh:
                _fh.write('李密于 300 年卒于家。\n')
            r = subprocess.run([sys.executable, str(fc), fp, '--person', '李密'],
                               capture_output=True, timeout=60)
            o = r.stdout.decode('utf-8', 'replace')
            if r.returncode == 1 and '人物已卒' in o:
                lines.append('OK    tools/factcheck.py 认出三位数年份并判超卒年 FAIL（李密 300 年样本）')
            else:
                lines.append('FAIL  tools/factcheck.py 漏检三位数年份「300 年」（rc=%d）——'
                             '年份正则过窄，超出卒年的错误会静默通过' % r.returncode)
                fails += 1
            for body, want in (('泰始元年，征太子洗马。', '泰始265'), ('泰始2年上表。', '泰始266')):
                fp = os.path.join(tmpf, 'st_fact_era.txt')
                with open(fp, 'w', encoding='utf-8') as _fh:
                    _fh.write(body + '\n')
                r = subprocess.run([sys.executable, str(fc), fp, '--person', '李密'],
                                   capture_output=True, timeout=60)
                o = r.stdout.decode('utf-8', 'replace')
                m = re.search(r'检出纪年\s*\d+\s*处：(.*)', o)
                got = m.group(1) if m else ''
                if want not in got:
                    lines.append('FAIL  纪年换算失准：%r 应含 %s，实得「%s」——年号纪年漏检＝错年不报'
                                 % (body, want, got[:40]))
                    fails += 1

            # 公元前支持（2026-09-25）：负数 born/died 曾被 isdigit() 判成"缺值"→ 退码 2，
            # 西汉人物的**整张卡直接失效**；负 era（`高祖|-206`）也曾被正则丢弃。
            # 依赖 tools/facts/刘恒.md 存在（该卡即西汉卡样板）。
            if '刘恒' in cards:
                fp = os.path.join(tmpf, 'st_fact_bc.txt')
                with open(fp, 'w', encoding='utf-8') as _fh:
                    _fh.write('高祖十一年，立为代王，都中都。公元前157年，帝崩。\n')
                r = subprocess.run([sys.executable, str(fc), fp, '--person', '刘恒'],
                                   capture_output=True, timeout=60)
                o = r.stdout.decode('utf-8', 'replace')
                m = re.search(r'检出纪年\s*\d+\s*处：(.*)', o)
                got = m.group(1) if m else ''
                if r.returncode == 2 or '高祖-196' not in got or '-157' not in got or '结论' not in o:
                    lines.append('FAIL  公元前支持失效（rc=%d，纪年「%s」）——负数生卒被判"缺值"'
                                 '或负年号未换算，西汉人物整卡不可用' % (r.returncode, got[:40]))
                    fails += 1
                else:
                    lines.append('OK    tools/factcheck.py 支持公元前（负年号换算「高祖-196」、'
                                 '公元前年份「-157」、负区间 office_window 不崩）')

                    # sourced_theme 曾落在标量分支 → 卡里多行只留最后一行（"正史支点"只印一条）。
                    # 断言：刘恒卡 6 条 sourced_theme 必须**全部**出现在输出里。
                    n_theme = sum(1 for l in o.splitlines() if l.startswith('INFO 可用的正史支点：'))
                    if n_theme < 6:
                        lines.append('FAIL  sourced_theme 多值键失效：刘恒卡有 6 条，输出只印 %d 条——'
                                     '重复行被标量分支覆盖，其余支点静默丢失' % n_theme)
                        fails += 1
                    else:
                        lines.append('OK    sourced_theme 为多值键（刘恒卡 6 条全部输出，不再只留末条）')
        except Exception as e:
            lines.append('FAIL  tools/factcheck.py 年份闸冒烟失败：%s' % str(e)[:90])
    elif not (fc.exists() and cards):
        lines.append('        WARN 缺少 tools/factcheck.py 或 tools/facts/ 事实卡（写史稿无法机械核查）')

    # 讲史抽签器：必须能解析 04 并抽出窗口；且歧义朝名闸必须生效
    ps = ROOT / 'tools' / 'pick_story.py'
    if ps.exists() and not QUICK:
        import subprocess
        try:
            # --no-log：体检的试抽不得写进 tools/_draw_log.md，否则会污染 --no-repeat 轮换
            r = subprocess.run([sys.executable, str(ps), '--seed', '1', '--no-log'],
                               capture_output=True, timeout=120)
            out = r.stdout.decode('utf-8', 'replace')
            r2 = subprocess.run([sys.executable, str(ps), '--dynasty', '宋'], capture_output=True, timeout=120)
            out2 = (r2.stdout + r2.stderr).decode('utf-8', 'replace')
            if r.returncode == 0 and '■' in out and 'Read' in out and '签号' in out and '讲全' in out:
                if '歧义朝名' in out2 and r2.returncode != 0:
                    lines.append('OK    tools/pick_story.py 抽签可用（含签号＋讲全扩读指引），'
                                 '且歧义朝名闸生效（「宋」被拦下）')
                else:
                    lines.append('FAIL  tools/pick_story.py 歧义朝名闸未生效（--dynasty 宋 应被拒绝）')
                    fails += 1
            else:
                lines.append('FAIL  tools/pick_story.py 抽签异常（rc=%d）：%s' % (r.returncode, out[:150]))
                fails += 1

            # --check 后置校验冒烟（正向＋负向）：这是"只讲一遍＋虚构必标注"的机械闸，
            # 没被负向测过等于没有闸。样本写 %TEMP%、不落项目目录，避免自污染。
            import tempfile
            tmpd = tempfile.gettempdir()
            chk = [  # (样本名, 内容, 期望退出码)
                ('ok_once', '#A1B2C3\n项羽力能扛鼎，学书不成去学剑。（《史记·项羽本纪》）', 0),
                ('ok_fiction', '⚠ 非真实历史（演义）：话说关羽过五关斩六将，斩了孔秀、孟坦……（此为《三国演义》情节）', 0),
                ('bad_nomark', '话说关羽过五关斩六将，斩了孔秀、孟坦、韩福……', 1),
                ('bad_twice', '#A1B2C3\n……一个故事……\n#D4E5F6\n……又一个故事……', 1),
                # 应点讲（2026-09-29 加）：用户点名要某个故事 → 无签号但有确定出处，也算合格
                ('ok_respond', '应点讲：《资治通鉴·卷8》（秦纪）\n项羽乃悉引兵渡河，皆沈船，破釜、甑……', 0),
                ('ok_respond2', '《史记·卷八十一·廉颇蔺相如列传》\n蔺相如奉璧西入秦，臣请完璧归赵……', 0),
                # 1-alt 降级选自（2026-10-01 加）：无执行工具的环境按 SKILL.md「1-alt」从 `04`
                # 手算取号，首行凭证是「引用 04 第 N 行（L＝N 字）」。**此前 --check 不认它**，
                # 会把这条降级路径产出的合格回答判 FAIL（"闸门不认文档认可的路径"）——补上。
                ('ok_alt04', '引用 04 第 3804 行（L＝5 字）\n宣皇帝名懿，字仲达，河内温县孝敬里人。\n《晋书·卷一·帝纪第一》', 0),
                # 负向：只是**提到** 04 不算凭证 —— 判据要求"引用 04 第 <数字> 行"的固定形。
                ('bad_alt04', '我看了一下 04 索引，讲这个吧。\n从前有位皇帝……', 1),
                ('bad_nothing', '话说项羽破釜沉舟，大破秦军于巨鹿。', 1),   # 无签号/无标注/非应点 ⇒ 仍须 FAIL
            ]
            chk_bad = []
            for name, body, expect in chk:
                fp = os.path.join(tmpd, 'ps_check_%s.txt' % name)
                with open(fp, 'w', encoding='utf-8') as _fh:
                    _fh.write(body)
                rc = subprocess.run([sys.executable, str(ps), '--check', fp],
                                    capture_output=True, timeout=60)
                if rc.returncode != expect:
                    chk_bad.append('%s(rc=%d≠%d)' % (name, rc.returncode, expect))
            if not chk_bad:
                lines.append('OK    tools/pick_story.py --check 生效（签号唯一＋虚构必标注＋应点讲＋'
                             '1-alt 降级选自，正负向均对）')
            else:
                lines.append('FAIL  tools/pick_story.py --check 判据失效：%s' % '、'.join(chk_bad))
                fails += 1

            # --next 续讲取点冒烟（2026-09-29 加）：用户说"继续／后来呢"时，模型必须能机械
            # 拿到"后续在哪"，而不是自己挑窗。给上一签路径（+止行）→ 必须给出紧邻卷次。
            # ⚠ 本行写死了 18旧五代史 的 101/102 两份文件；语料增删若动了它们，请同步改这里。
            try:
                rn = subprocess.run([sys.executable, str(ps), '--next',
                                     r'18旧五代史\现代文\101_卷一百一（汉书）·隐帝纪上_现代汉语.md', '49'],
                                    capture_output=True, timeout=60)
                on = (rn.stdout + rn.stderr).decode('utf-8', 'replace')
                if rn.returncode == 0 and '续讲取点' in on and '紧邻卷次' in on and '102_' in on:
                    lines.append('OK    tools/pick_story.py --next 续讲取点可用（给出紧邻卷次 102）')
                else:
                    lines.append('FAIL  tools/pick_story.py --next 异常（rc=%d）：%s'
                                 % (rn.returncode, on[:120]))
                    fails += 1
            except Exception as e:
                lines.append('FAIL  tools/pick_story.py --next 调用失败：%s' % str(e)[:90])
                fails += 1

            # --person 按人抽冒烟（2026-09-29 加）："讲苏辙的故事"必须能定位本传、并拿到多窗候选
            # （一个人的故事不止一个）。⚠ 依赖 rag.py locate（已带 timeout）。
            try:
                rp3 = subprocess.run([sys.executable, str(ps), '--person', '苏辙',
                                      '--seed', '1', '--no-log'], capture_output=True, timeout=120)
                op = (rp3.stdout + rp3.stderr).decode('utf-8', 'replace')
                if rp3.returncode == 0 and '候选窗' in op and '签号' in op and '339' in op:
                    lines.append('OK    tools/pick_story.py --person 按人抽可用（苏辙本传多窗候选，首行仍带签号）')
                else:
                    lines.append('FAIL  tools/pick_story.py --person 异常（rc=%d）：%s'
                                 % (rp3.returncode, op[:120]))
                    fails += 1
            except Exception as e:
                lines.append('FAIL  tools/pick_story.py --person 调用失败：%s' % str(e)[:90])
                fails += 1
        except Exception as e:
            lines.append('FAIL  tools/pick_story.py 调用失败：%s' % str(e)[:90])
            fails += 1

        # --flat 与 --count 必须能同时用；空路径的 --check 必须当用法错误拦下。
        # ⚠ 2026-09-23 修：① flat 分支返回元组而非列表，`--flat --count 2` 直接 ValueError；
        #   ② `--check ''` 因 `if args.check:` 判假值，静默**当成一次抽签**跑完并写台账——
        #     一次拼错的参数校验会往 _draw_log.md 添一条从没讲过的记录，污染 --no-repeat 轮换。
        try:
            log = ROOT / 'tools' / '_draw_log.md'
            n0 = len(log.read_text(encoding='utf-8', errors='replace').splitlines()) if log.exists() else 0
            r = subprocess.run([sys.executable, str(ps), '--flat', '--count', '2',
                                '--seed', '11', '--no-log'], capture_output=True, timeout=180)
            o = r.stdout.decode('utf-8', 'replace')
            books = re.findall(r'^■ (\S+)', o, re.M)
            codes = re.findall(r'签号\s+#([0-9A-F]{6})', o)
            if r.returncode == 0 and len(codes) == 2 and len(set(books)) == 2:
                lines.append('OK    --flat --count 2 正常（2 签、跨 2 部不同书）')
            else:
                lines.append('FAIL  --flat --count 2 异常（rc=%d，签号=%s，书=%s）——'
                             'flat 模式取窗逻辑一旦崩，讲史会退回人工选书' % (r.returncode, codes, books))
                fails += 1
            r = subprocess.run([sys.executable, str(ps), '--check', ''],
                               capture_output=True, timeout=60)
            n1 = len(log.read_text(encoding='utf-8', errors='replace').splitlines()) if log.exists() else 0
            if r.returncode == 2 and n1 == n0:
                lines.append('OK    --check 空路径退码 2 且不写台账（不会误当成抽签污染轮换）')
            else:
                lines.append('FAIL  --check 空路径未拦住（rc=%d，台账行数 %d→%d）——'
                             '拼错的参数校验被当成抽签，且会往 _draw_log.md 写垃圾行'
                             % (r.returncode, n0, n1))
                fails += 1
        except Exception as e:
            lines.append('FAIL  pick_story --flat／--check 冒烟失败：%s' % str(e)[:90])
            fails += 1

        # 冒烟后清理：抽签若留下台账行会污染轮换，--seed 抽签不写台账，此处无需处理
        # 同名文件体检：报数并**锁定基线**（同名簇扩大=跨书同名检索坑变多，须人工确认）
        cn = ROOT / 'tools' / 'check_dupnames.py'
        if cn.exists():
            try:
                r3 = subprocess.run([sys.executable, str(cn)], capture_output=True, timeout=180)
                o3 = r3.stdout.decode('utf-8', 'replace')
                first = o3.splitlines()[0] if o3.splitlines() else '（无输出）'
                lines.append('OK    跨书同名体检：%s' % first)
                m = re.search(r'重名 basename (\d+) 种 ｜ 涉及文件 (\d+) 个', o3)
                if m and (int(m.group(1)), int(m.group(2))) != (89, 187):
                    lines.append('FAIL  跨书同名簇漂移：现 %s 种 / %s 个 ≠ 基线 89 / 187——'
                                 '若为语料增补请同步更新 selftest 期望值（本行与 SKILL.md「取文件」节）'
                                 % (m.group(1), m.group(2)))
                    fails += 1
            except Exception as e:
                lines.append('        WARN tools/check_dupnames.py 调用失败：%s' % str(e)[:90])
    elif not ps.exists():
        lines.append('        WARN 缺少 tools/pick_story.py（讲史将退回人工选书，易偏食资治通鉴）')

    # 通鉴 纪→朝代 映射三处同源：00-目录索引.md（权威表）／rag.py.TZJ_JI／pick_story.py.TZJ_JI
    # 只改一处会出现"表里说刘宋、抽签抽到汉纪、引文写成宋代"——这类错答通顺到看不出来，必须每次体检都跑。
    t00 = ROOT / '00-目录索引.md'
    if t00.exists():
        import importlib.util

        def _norm_regime(s):
            return re.sub(r'（[^）]*）', '', s).replace('*', '').replace(' ', '').strip()

        def _load(path, attr):
            spec = importlib.util.spec_from_file_location('_m_' + path.stem, str(path))
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            return getattr(m, attr)

        tbl = {}
        for l in t00.read_text(encoding='utf-8').splitlines():
            if not l.startswith('| `'):
                continue
            p = [x.strip() for x in l.strip().strip('|').split('|')]
            if len(p) < 5 or not p[0].endswith('纪`'):
                continue
            rng = re.findall(r'\d+', p[1])
            if len(rng) < 2:
                continue
            tbl[p[0].strip('`')] = (_norm_regime(p[3]), int(rng[0]), int(rng[1]), int(p[2]))
        if len(tbl) != 16:
            lines.append('FAIL  00-目录索引.md 通鉴纪表只解析出 %d 行（应 16）——表格格式被改坏' % len(tbl))
            fails += 1
        else:
            bad = []
            # ① 卷范围/文件数与实际语料
            d = ROOT / 'data' / '00资治通鉴'
            for ji, (reg, lo, hi, cnt) in tbl.items():
                sub = d / ji
                if not sub.is_dir():
                    bad.append('%s 子目录不存在' % ji)
                    continue
                fs = list(sub.glob('*.md'))
                ns = sorted(int(m.group(1)) for m in
                            (re.search(r'卷(\d+)', f.stem) for f in fs) if m)
                if not ns:
                    bad.append('%s 无卷文件' % ji)
                elif (min(ns), max(ns), len(fs)) != (lo, hi, cnt):
                    bad.append('%s 表作 %d–%d/%d 卷，实际 %d–%d/%d 卷'
                               % (ji, lo, hi, cnt, min(ns), max(ns), len(fs)))
            # ② 与 rag.py 的 TZJ_JI、pick_story.py 的 TZJ_JI_OF_DYNASTY 一致
            #    （两张表方向相反，名字不同是刻意的：纪→政权 / 朝名→纪）
            try:
                rag_tz = _load(ROOT / 'tools' / 'rag.py', 'TZJ_JI')
            except Exception as e:
                rag_tz = None
                bad.append('rag.py TZJ_JI 载入失败：%s' % str(e)[:60])
            if rag_tz is not None:
                if set(rag_tz) != set(tbl):
                    bad.append('rag.py TZJ_JI 的纪名集合与 00 表不符（差：%s）'
                               % '、'.join(sorted(set(rag_tz) ^ set(tbl))))
                else:
                    for ji, reg in rag_tz.items():
                        if _norm_regime(reg) != tbl[ji][0]:
                            bad.append('%s：rag.py 作「%s」，00 表作「%s」' % (ji, reg, tbl[ji][0]))
            try:
                ps_tz = _load(ROOT / 'tools' / 'pick_story.py', 'TZJ_JI_OF_DYNASTY')
            except Exception as e:
                ps_tz = None
                bad.append('pick_story.py TZJ_JI_OF_DYNASTY 载入失败：%s' % str(e)[:60])
            if ps_tz is not None:
                for ji, (reg, _lo, _hi, _c) in tbl.items():
                    owners = [k for k, v in ps_tz.items() if v and ji in v]
                    if not owners:
                        bad.append('%s 在 pick_story.TZJ_JI_OF_DYNASTY 里无任何政权指向' % ji)
                        continue
                    parts = reg.split('／')
                    if not any(o == reg or o in parts or o in reg for o in owners):
                        bad.append('%s：00 表作「%s」，pick_story.TZJ_JI_OF_DYNASTY 只认 %s'
                                   % (ji, reg, '／'.join(owners)))
            if bad:
                lines.append('FAIL  通鉴纪→朝代 三处映射不一致（%d 项）：%s'
                             % (len(bad), '；'.join(bad[:6]) + ('…' if len(bad) > 6 else '')))
                fails += 1
            else:
                lines.append('OK    通鉴纪→朝代一致：00 表 16 纪（卷范围/文件数实测吻合）'
                             '＝rag.py TZJ_JI＝pick_story.py TZJ_JI_OF_DYNASTY')

        # 00 书目总表「朝代（政权）」列 ↔ pick_story.DYNASTY（原为**人工对齐、无机器校验**，
        # dynasty-disambiguation.md §1 同源）。此处机器比对，抓「新增书忘了加 DYNASTY」与对应错位。
        try:
            dy = _load(ROOT / 'tools' / 'pick_story.py', 'DYNASTY')
        except Exception as e:
            dy = None
            lines.append('        WARN pick_story.DYNASTY 载入失败：%s' % str(e)[:60])
        if dy:
            folder_of, bolds_of = {}, {}
            for l in t00.read_text(encoding='utf-8').splitlines():
                if not l.startswith('| ') or '| ---' in l:
                    continue
                c = [x.strip() for x in l.strip().strip('|').split('|')]
                if len(c) < 5 or not c[0].isdigit():
                    continue
                folder = c[3].strip('`').strip()
                folder_of[folder] = c[1]
                bolds_of[folder] = re.findall(r'\*\*([^*]+)\*\*', c[2])
            plain = {'多朝通纪', '通史', '十国', '十国世家', '南朝', '北朝', '五代', '三国', '先秦'}
            mismatch, unknown_pol = [], []
            for folder, bolds in bolds_of.items():
                for b in bolds:
                    if b in plain:
                        continue
                    if b not in dy:
                        unknown_pol.append('%s(%s)' % (b, folder))
                    elif folder not in dy[b]:
                        mismatch.append('%s→%s' % (folder, b))
            ghost = [f for v in dy.values() for f in v if not (ROOT / 'data' / f).is_dir()]
            allval = {f for v in dy.values() for f in v}
            uncovered = sorted(p.name for p in (ROOT / 'data').iterdir()
                               if p.is_dir() and p.name not in allval)
            bad3 = []
            if mismatch:
                bad3.append('政权对应错位 %d 处（%s）' % (len(mismatch), '、'.join(mismatch[:3])))
            if ghost:
                bad3.append('DYNASTY 有幻影文件夹 %s' % '、'.join(ghost[:3]))
            if uncovered:
                bad3.append('data/ 有 %d 部书未被任何政权覆盖（%s）——新增书须同步 DYNASTY'
                            % (len(uncovered), '、'.join(uncovered[:3])))
            if bad3:
                lines.append('FAIL  00 书目总表 ↔ pick_story.DYNASTY 不一致：%s' % '；'.join(bad3))
                fails += 1
            else:
                lines.append('OK    00 书目总表 ↔ pick_story.DYNASTY 一致（%d 部书 / %d 个政权；'
                             '文件夹无幻影、无未覆盖）' % (len(folder_of), len(dy)))
            if unknown_pol:
                lines.append('        INFO 00 表粗体词不在 DYNASTY（复合描述，属正常）：%s'
                             % '、'.join(sorted(set(unknown_pol))))
    else:
        lines.append('FAIL  缺少 00-目录索引.md（通鉴纪→朝代无处可查）')
        fails += 1

    # 07 帝王本纪索引：02/03 不收帝王（本纪只写「讳某」），本表是**按本名查本纪的唯一总表**。
    # 键集塌了或废键复活 = "猜皇帝"重来（旧病：查「杨坚」端上来《隋书·卷六十》崔仲方传）。
    # 行为层（★本纪钉没钉住／假锚点）由下面的 rag.py 自检覆盖，这里只锁**表本身**。
    p07 = ROOT / '07-帝王本纪索引.md'
    if p07.exists():
        t07 = p07.read_text(encoding='utf-8', errors='replace')
        l07 = t07.splitlines()
        bad07 = []
        for kw, cite in (('杨坚', '《隋书·卷一'), ('曹操', '《三国志·卷一'),
                         ('司马炎', '《晋书·卷三'), ('刘秀', '《后汉书·卷一上'),
                         ('赵匡胤', '《宋史·卷一'), ('溥仪', '《清史稿·卷二十五')):
            if not any(kw in L and cite in L for L in l07):
                bad07.append('%s 未与 %s 同行（本名键丢失）' % (kw, cite))
        for junk, why in (('为汝阴王', '封废记事'), ('为巴陵王', '封废记事'),
                          ('绳下、', '「以刑名绳下」假本名')):
            if junk in t07:
                bad07.append('废键「%s」复现（%s）' % (junk, why))
        # ⚠ 语料缺文补录（2026-09-25）：三国志卷四（三少帝纪）正文在库中被后妃传第五顶替，
        #   齐王芳／高贵乡公髦／陈留王奂的本名原本**任何索引都命中不了**（用户实测「无法命中曹髦」）。
        #   修法是 07 照常给检索键、第 5 栏改替代锚点。这三行是「帝王按本名查得到」的兜底，不得消失；
        #   同时卷四那条（照错置正文抽出的「武宣卞皇后」）必须就地标 ⚠。
        for key, who in (('曹芳', '齐王芳'), ('曹髦', '高贵乡公'), ('曹奂', '陈留王')):
            row = [L for L in l07 if L.startswith(key + '、') or ('、' + key + '、') in L]
            if not any('⚠语料缺文补录' in L for L in row):
                bad07.append('%s（%s）缺「⚠语料缺文补录」行——三少帝本纪原文不在库（三国志卷四'
                             '正文被后妃传第五顶替），此行是唯一能按本名命中的兜底' % (key, who))
        if not any('三少帝纪原文不在库' in L and '004_文白对照_卷四·魏书四.md' in L for L in l07):
            bad07.append('三国志卷四那条行未标「正文实为后妃传第五」——读者按它去引会把'
                         '武宣卞皇后当成曹魏卷四的本名键')
        n07 = sum(1 for L in l07 if '｜' in L and '《' in L and not L.startswith(('#', '>')))
        if n07 < 240:
            bad07.append('数据行仅 %d（应 248 量级——抽取层或语料出问题）' % n07)
        if bad07:
            lines.append('FAIL  07-帝王本纪索引：%s——重跑 tools/build_emperor_index.py'
                         % '；'.join(bad07))
            fails += 1
        else:
            lines.append('OK    07-帝王本纪索引：%d 行｜本名键样本 6/6｜废键 0｜缺文补录 3（曹芳／曹髦／曹奂）' % n07)
    else:
        lines.append('FAIL  缺少 07-帝王本纪索引.md（按本名查帝王＝零命中，本表是唯一总表）')
        fails += 1

    # 08 案件索引 ↔ tools/cases.md：表与数据源必须同数量、同案名，且生成器的
    # 三道闸（检索词剥 `!` 排除串／按语书名不编／零命中如实标）都得亮在产物里。
    # 为什么单独查：08 的「出处」是脚本回扫语料得出的，人工改不动——一旦生成器
    # 的排除语法失效，「杨乃武」这类同形假阳性（《晋书》骈句「殷杨乃武」）就会
    # 重新顶掉《清史稿》真记载，读者照着引等于引错书。
    p08 = ROOT / '08-案件索引.md'
    pcases = ROOT / 'tools' / 'cases.md'
    if p08.exists() and pcases.exists():
        t08 = p08.read_text(encoding='utf-8', errors='replace')
        l08 = t08.splitlines()
        rows08 = [L for L in l08 if '｜' in L and not L.startswith(('#', '>'))]
        specs, bad08 = [], []
        for i, ln in enumerate(pcases.read_text(encoding='utf-8').splitlines(), 1):
            s = ln.strip()
            if not s or s.startswith('#'):
                continue
            f = [x.strip() for x in s.split('｜')]
            if len(f) != 7:
                bad08.append('cases.md:%d 字段 %d≠7' % (i, len(f)))
                continue
            specs.append(f)
        if len(rows08) != len(specs):
            bad08.append('08 数据行 %d ≠ cases.md 案 %d（有案未落地或用例被吞）'
                         % (len(rows08), len(specs)))
        m = re.search(r'覆盖 (\d+) 案', t08)
        if not m or int(m.group(1)) != len(specs):
            bad08.append('表头「覆盖 X 案」%s ≠ cases.md 案数 %d'
                         % (m.group(1) if m else '缺失', len(specs)))
        if '⚠按语书名待核' in t08:
            bad08.append('仍有按语《书·卷／篇》被判必错——本库查不到的书名卷次不得写进按语')
        if '本库未载的著名案件' not in t08:
            bad08.append('缺「本库未载的著名案件」清单行（零命中案须集中登记，防凭记忆补卷次）')
        by_name = {}
        for L in rows08:
            by_name.setdefault(L.split('｜')[0], L)
        n_excl = 0
        for f in specs:
            name, terms_s = f[0], f[5]
            row = by_name.get(name)
            if row is None:
                bad08.append('案「%s」在 08 中无行' % name)
                continue
            cols = row.split('｜')
            excl = [x for t in terms_s.split('/') if '!' in t for x in t.split('!')[1:]]
            if not excl:
                continue
            n_excl += len(excl)
            if '!' in cols[3]:
                bad08.append('%s｜检索词列未剥除 `!` 排除串（%s）——读者复制即零命中'
                             % (name, cols[3]))
            # 「出处」区＝别名/按语之前的部分（按语里提到假阳性是正当的）
            src_zone = row.split('｜别名：')[0].split('｜编者按')[0]
            for x in excl:
                if x in src_zone:
                    bad08.append('%s｜出处摘句含被排除串「%s」——split_terms 的 `!` 语法失效，'
                                 '同形假阳性顶掉了真记载' % (name, x))
        if not any('⚠ 本库零命中' in L for L in rows08):
            lines.append('        WARN cases.md 已无零命中案——「⚠ 本库零命中」标记样本消失，'
                         '若日后有新案零命中将无法被发现')
        if bad08:
            lines.append('FAIL  08-案件索引：%s——重跑 tools/build_case_index.py' % '；'.join(bad08))
            fails += 1
        else:
            lines.append('OK    08-案件索引：%d 案与 cases.md 逐案对齐｜`!` 排除串 %d 个均已剥出检索词列、'
                         '未复现于出处摘句｜按语必错 0' % (len(specs), n_excl))
    else:
        lines.append('FAIL  缺少 08-案件索引.md 或 tools/cases.md（问「某案出处在哪」无入口）')
        fails += 1

    # RAG 检索／核验器：防瞎编的关键阀门，必须可用
    rp = ROOT / 'tools' / 'rag.py'
    if rp.exists() and not QUICK:
        import subprocess
        try:
            # 两档：全量约 17 s（含全库伪造样本扫描），--quick 只跑前 2 条检索用例
            # 约 4.5 s——本文件是自箱子进程冒烟，用快档；全量由人手工跑。
            r4 = subprocess.run([sys.executable, str(rp), 'selftest', '--quick'],
                                capture_output=True, timeout=300)
            o4 = r4.stdout.decode('utf-8', 'replace')
            bad = [l for l in o4.splitlines() if l.startswith('FAIL')]
            if r4.returncode == 0 and not bad:
                lines.append('OK    tools/rag.py 检索/核验自检通过（%d 项）'
                             % len([l for l in o4.splitlines() if l.startswith('OK')]))
            else:
                lines.append('FAIL  tools/rag.py 自检未过：%s' % ('；'.join(bad) or o4[:150]))
                fails += 1
        except Exception as e:
            lines.append('FAIL  tools/rag.py 调用失败：%s' % str(e)[:90])
            fails += 1
    elif not rp.exists():
        lines.append('FAIL  缺少 tools/rag.py（将退回纯 Grep，无法核验引文真伪）')
        fails += 1

    # 「猜人物」防瞎编三闸（2026-09-24 实测定位）：旧 verify 只查「引文在不在库里＋出自哪本书」，
    # 于是 ① 同书内把魏徵的话记在李勣名下 rc=0 放行、②「海瑞，字刚峰」「陈玉成，字荣光」这类
    # **无引号的身份断言**抽到 0 条引文也 rc=0 放行；③「谜」的候选只取自 03，无字又附叙在他传的
    # 人（实测陈玉成／石达开等 21 人全库 10–160 处而 03 曾零条目）连候选栏都进不去，工具反而把
    # 段内的别人（塔齐布／王洪）顶成唯一候选——这三条正是"人物题带着假锚通过交付闸"的直接源头。
    # 那批人已登记 `tools/extra_names.md` 并重跑 03；口径未变，故下面的 ○ 栏断言仍留着兜盲区。
    rp = ROOT / 'tools' / 'rag.py'
    vp = ROOT / 'tools' / '_verify_probe.txt'
    if rp.exists() and not QUICK:
        import subprocess
        GATE = [
            ('同书张冠李戴', '《旧唐书·卷七十一》列传第三十一李勣传载，勣对曰：'
             '「此乃奇谋深策，何谓常谈？」\n', 1),
            ('把号当字', '海瑞，字刚峰，琼山人\n', 1),
            ('真稿不得误报', '《旧唐书·卷七十一》魏徵传载，魏徵对曰：'
             '「此乃奇谋深策，何谓常谈？」\n', 0),
            ('省称真稿不得误报', '《旧唐书·卷七十一》魏徵传：贞观三年，徵上疏曰：'
             '「此乃奇谋深策，何谓常谈？」\n', 0),
            # 2026-09-24 三轮（用户拿"猜汉献帝／曹髦"的假答案实测）：
            #  ④ 归因式**三字短引语**旧闸抽不到（QUOTE_RES 要 ≥4 字），「史载其『目有疾』」
            #     这种把《梁书·萧恢传》的句子钉到曹操头上的写法整段放行；
            #  ⑤ 「名（某帝）」与 07 的号对不上（刘宏是东汉孝灵帝，不是汉顺帝）无人查。
            ('归因短句张冠李戴', '他的敌人眼睛有病。曹操，史载其「目有疾」，晚年失明。\n', 1),
            ('帝号与名错配', '他的祖父刘宏（汉顺帝），虽非大诗人。\n', 1),
            ('帝号正确不得误报', '他的祖父曹丕（魏文帝）善诗，《燕歌行》出其手。\n', 0),
        ]
        for tag, body, want in GATE:
            try:
                vp.write_text(body, encoding='utf-8')
                r5 = subprocess.run([sys.executable, str(rp), 'verify', str(vp)],
                                    capture_output=True, timeout=300)
                if r5.returncode == want:
                    lines.append('OK    verify「%s」rc=%d（符合预期）' % (tag, want))
                else:
                    lines.append('FAIL  verify「%s」rc=%d，应为 %d —— 人物归属／字号闸失效，'
                                 '猜人物会带着假锚通过交付闸（铁律 8 形同虚设）'
                                 % (tag, r5.returncode, want))
                    fails += 1
            except Exception as e:
                lines.append('        WARN verify「%s」调用失败：%s' % (tag, str(e)[:90]))
            finally:
                if vp.exists():
                    vp.unlink()
        # 「谜」候选栏去假锚（2026-09-24 二轮实测，每条钉一件事）：
        #   ① 真答案必须进强候选（`extra_names.md` 登记 19 位太平天国头目＋重跑 03 的成果）；
        #   ② 截短形／词组撞名不得再顶候选（「★ 国事」＝「练国事」被 variants() 炸掉姓氏、
        #      又以 797 卷溜过旧频率闸 800；「李瀚／刘松／松山」是李瀚章／刘松山的截短）；
        #   ③ ○ 栏（03 未立目者的唯一出口）必须还能捞出真人，且不再冒时间切词／人名＋虚字粘连。
        # 「轻佻／靳准／长安」＝2026-09-24 实测的十六国题（答案 刘粲，见 `谜` 三条硬锚）：
        #   钉住排序改写的成果——贴锚真人（刘粲 距 2 字）必须排在常遇撞词（司马模 距 64／袁绍 距 134）
        #   之前，且 02 已屏蔽的假名「成汉」（政权名撞词，旧版 03 列 31 卷）不得再顶候选。
        RIDDLE = [
            (['轻佻', '靳准', '长安'],
             ['#    ★ 刘粲（段内 1·距 2 字', '★ 司马模'],
             ['#    ★ 成汉（']),
            (['陈玉成弃蕲州窜陷广济'],
             ['陈玉成（段内 1·距 0 字', '不是答案'],
             ['★ 国事']),
            (['任柱、赖文光等各股捻军窜扰孝感'],
             ['★ 李鸿章', '已折叠的截短形'],
             ['#    ★ 国事', '#    ★ 李瀚（', '#    ★ 刘松（', '#    ★ 松山（',
              '#    ★ 张锡（']),
            (['凤台生员苗沛霖藉团聚众'],
             ['○ 蒋霨远', '不是答案'],
             ['○ 秋季', '○ 翁同书以', '○ 江西肃', '○ 广西官', '○ 寇死']),
        ]
        for words, must, mustnot in RIDDLE:
            clue = ' '.join(words)
            try:
                r6 = subprocess.run([sys.executable, str(rp), '谜'] + words,
                                    capture_output=True, timeout=600)
                o6 = r6.stdout.decode('utf-8', 'replace')
                miss = [m for m in must if m not in o6]
                bad = [b for b in mustnot if b in o6]
                # must 的**书写顺序**就是期望的输出顺序：钉住「贴锚真人排在常遇撞词之前」
                pos = [o6.find(m) for m in must]
                order_bad = pos != sorted(pos)
                if not o6.strip():
                    lines.append('FAIL  谜「%s」无输出（rc=%d）——候选栏一旦空，模型只能凭记忆猜人'
                                 % (clue, r6.returncode))
                    fails += 1
                elif miss or bad or order_bad:
                    lines.append('FAIL  谜「%s」缺 %s／冒出 %s%s —— 假锚回到候选栏，'
                                 '工具会把撞词的词组或别人的截短名推成答案，正是瞎编温床'
                                 % (clue, '、'.join(miss) or '—', '、'.join(bad) or '—',
                                    '／候选顺序颠倒（应为 %s）' % '→'.join(must) if order_bad else ''))
                    fails += 1
                else:
                    lines.append('OK    谜「%s」候选栏合规（须含 %s；已滤 %d 类假锚）'
                                 % (clue, '、'.join(must), len(mustnot)))
            except Exception as e:
                lines.append('        WARN 谜「%s」调用失败：%s' % (clue[:12], str(e)[:90]))

    lines.append('-' * 40)
    lines.append('02/03 人名字闸（两头都钉）')
    # 屏蔽表或抽取正则被改坏时会出两种事故，必须各有一道闸：
    #   ① KEEP 消失＝精度闸加太狠。2026-09-24 试过「X字Y 无逗号」尾字闸（名末字属'文/汉/抚…'即判假），
    #      一刀砍掉 1040 条命中，吴汉／赵广汉／周抚／褚伯玉／徐瑞 等真人成批不见——该闸已回滚，
    #      假名只能逐条走 name_blocklist。这组词条就是那次误杀的证物，再消失即复发。
    #   ② DROP 复现＝屏蔽表漏条目／被覆盖。这些是**政权名·书名·动词短语**撞出来的假人，
    #      03 还给「成汉」这类词记着几十卷出现，检索时会顶在真答案旁边（实测：猜刘粲时）。
    ZI_KEEP = ['吴汉｜', '赵广汉｜', '薛汉｜', '周抚｜', '褚伯玉｜', '徐瑞｜', '邓文原｜',
               '刘粲｜字：士光｜', '万年｜字：幼公｜', '元吉｜字：吉人｜']
    ZI_DROP = {'02-人物字号索引.md': ['成汉｜', '译成汉｜', '能抚｜', '阙文｜', '石文｜字：辨异｜',
                                      '石文｜字：记｜', '元吉｜字：合之｜', '万年｜字：次比｜'],
               '03-人物出现索引.md': ['成汉｜', '能抚｜', '阙文｜']}
    for fname, pfx in ZI_DROP.items():
        p = ROOT / fname
        if not p.exists():
            continue
        heads = [l for l in p.read_text(encoding='utf-8', errors='replace').splitlines()
                 if not l.startswith('#')]
        bad = [x for x in pfx if any(l.startswith(x) for l in heads)]
        if bad:
            lines.append('        FAIL %s 仍有假人 %s —— `tools/name_blocklist.md` 的条目被删或没生效，'
                         '它们会带着 03 的卷数顶进「谜」候选栏' % (fname, '、'.join(bad)))
            fails += 1
        else:
            lines.append('        OK   %s 已无 %d 个政权名/书名/动词短语假人' % (fname, len(pfx)))
    t02 = ROOT / '02-人物字号索引.md'
    if t02.exists():
        text02 = t02.read_text(encoding='utf-8', errors='replace')
        gone = [x for x in ZI_KEEP if x not in text02]
        if gone:
            lines.append('        FAIL 02 缺真人 %s —— 抽字正则被改坏或屏蔽表误伤，'
                         '真人在索引里查不到，「猜人物」就只能凭记忆编' % '、'.join(gone))
            fails += 1
        else:
            lines.append('        OK   02 真人保留闸通过（%d 名，含曾被尾字闸误杀的 吴汉／周抚／褚伯玉）'
                         % len(ZI_KEEP))

    lines.append('-' * 40)
    lines.append('禁含检查（已知噪声不得复现）')
    for fname, kw, desc in FORBIDDEN:
        p = ROOT / fname
        if not p.exists():
            continue
        if kw in p.read_text(encoding='utf-8', errors='replace'):
            lines.append('        FAIL 复现「%s」（%s）——过滤规则可能被破坏' % (kw, desc))
            fails += 1
        else:
            lines.append('        OK   未复现「%s」（%s）' % (kw, desc))

    _dirty = [p.name for p, h in _ledger_before.items()
              if (hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None) != h]
    if _dirty:
        lines.append('FAIL  体检污染了抽签台账：%s —— 体检里的 pick_story 调用必须全带 `--no-log`，'
                     '否则会悄悄吃掉 --no-repeat 的一次轮换' % '、'.join(_dirty))
        fails += 1
    else:
        lines.append('OK    体检未动抽签台账（_draw_log.md / story_log.md 前后字节一致）')

    if QUICK:
        lines.append('        INFO 快档 --quick：已跳过全部 subprocess 冒烟（rag locate/verify、'
                     'factcheck、pick_story、同名簇全库扫描、内嵌 rag selftest、谜题跳板）；'
                     '判据与全量同一套，只是少跑几段。完整验收请跑 python tools\\selftest.py（约 122 s）')
    lines.append('=' * 40)
    lines.append('结论：%s' % ('全部通过 ✓' if fails == 0 else '有 %d 项未通过，请按提示重建索引' % fails))
    report = '\n'.join(lines)
    (ROOT / 'tools' / '_selftest_report.txt').write_text(report, encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    print(report)
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
