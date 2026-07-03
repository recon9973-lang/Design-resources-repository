# -*- coding: utf-8 -*-
"""PPTX 생성 공통 헬퍼: AI 시대 마케팅 6주 실무 스터디."""
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE
from pptx.oxml.ns import qn

SW, SH = Inches(13.333), Inches(7.5)
MX = Inches(0.6)
CW = Inches(12.133)

FONT = "맑은 고딕"
MONO = "Consolas"

TAG_COLORS = {
    "웹검색": "2563EB",
    "사이트": "059669",
    "유튜브": "DC2626",
    "뉴스": "D97706",
    "라이브 시연": "7C3AED",
    "미션": "DB2777",
    "실습": "0891B2",
    "툴": "4F46E5",
    "백업": "64748B",
}


def C(h):
    return RGBColor.from_string(h)


def set_font(run, size=14, bold=False, color="1F2937", name=FONT, italic=False):
    f = run.font
    f.size = Pt(size)
    f.bold = bold
    f.italic = italic
    f.name = name
    f.color.rgb = C(color)
    rPr = run._r.get_or_add_rPr()
    ea = rPr.find(qn("a:ea"))
    if ea is None:
        ea = rPr.makeelement(qn("a:ea"), {})
        rPr.append(ea)
    ea.set("typeface", FONT)


def add_rect(slide, x, y, w, h, fill, line=None, rounded=False, radius=None):
    shp = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE, x, y, w, h
    )
    shp.fill.solid()
    shp.fill.fore_color.rgb = C(fill)
    if line:
        shp.line.color.rgb = C(line)
        shp.line.width = Pt(0.75)
    else:
        shp.line.fill.background()
    shp.shadow.inherit = False
    if rounded and radius is not None:
        try:
            shp.adjustments[0] = radius
        except Exception:
            pass
    return shp


def add_textbox(slide, x, y, w, h, paras, anchor=MSO_ANCHOR.TOP, wrap=True):
    """paras: list of dicts {text | runs, size, bold, color, align, sb, sa, ls, name, italic}."""
    tb = slide.shapes.add_textbox(x, y, w, h)
    tf = tb.text_frame
    tf.word_wrap = wrap
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    first = True
    for p in paras:
        para = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        para.alignment = p.get("align", PP_ALIGN.LEFT)
        if p.get("sb") is not None:
            para.space_before = Pt(p["sb"])
        if p.get("sa") is not None:
            para.space_after = Pt(p["sa"])
        para.line_spacing = p.get("ls", 1.12)
        runs = p.get("runs")
        if runs is None:
            runs = [(p.get("text", ""), {})]
        for txt, opt in runs:
            r = para.add_run()
            r.text = txt
            set_font(
                r,
                size=opt.get("size", p.get("size", 14)),
                bold=opt.get("bold", p.get("bold", False)),
                color=opt.get("color", p.get("color", "1F2937")),
                name=opt.get("name", p.get("name", FONT)),
                italic=opt.get("italic", p.get("italic", False)),
            )
    return tb


def new_deck():
    prs = Presentation()
    prs.slide_width = SW
    prs.slide_height = SH
    return prs


def blank(prs):
    return prs.slides.add_slide(prs.slide_layouts[6])


def add_chip(slide, x, y, text, fill, w=None, size=10.5, color="FFFFFF"):
    w = w or Inches(0.22 + 0.135 * max(len(text), 3))
    h = Inches(0.30)
    add_rect(slide, x, y, w, h, fill, rounded=True, radius=0.5)
    add_textbox(
        slide, x, y + Inches(0.015), w, h,
        [{"text": text, "size": size, "bold": True, "color": color, "align": PP_ALIGN.CENTER}],
        anchor=MSO_ANCHOR.MIDDLE,
    )
    return w


def add_footer(slide, th, page_hint=""):
    add_textbox(
        slide, MX, Inches(7.12), CW, Inches(0.3),
        [{"runs": [
            (th.get("footer", ""), {"size": 9.5, "color": "94A3B8"}),
            (("   |   " + page_hint) if page_hint else "", {"size": 9.5, "color": "94A3B8"}),
        ]}],
    )


def s_head(slide, th, title, sub=None, badge=None):
    add_rect(slide, MX, Inches(0.52), Inches(0.10), Inches(0.52), th["accent"])
    add_textbox(
        slide, MX + Inches(0.26), Inches(0.44), Inches(10.2), Inches(0.7),
        [{"text": title, "size": 23, "bold": True, "color": th["ink"]}],
    )
    badge = badge if badge is not None else th.get("badge")
    if badge:
        add_chip(slide, SW - Inches(1.75), Inches(0.52), badge, th["accent2"], w=Inches(1.15))
    top = 1.22
    if sub:
        add_textbox(
            slide, MX + Inches(0.26), Inches(1.10), Inches(11.2), Inches(0.4),
            [{"text": sub, "size": 12.5, "color": "6B7280"}],
        )
        top = 1.58
    add_footer(slide, th)
    return top


def s_note(slide, th, note, y=6.62):
    add_rect(slide, MX, Inches(y), CW, Inches(0.44), th["soft"], rounded=True, radius=0.25)
    add_textbox(
        slide, MX + Inches(0.18), Inches(y), CW - Inches(0.36), Inches(0.44),
        [{"runs": [("TIP  ", {"size": 10.5, "bold": True, "color": th["accent"]}),
                   (note, {"size": 11, "color": "374151"})]}],
        anchor=MSO_ANCHOR.MIDDLE,
    )


# ---------------- slide builders ----------------

def s_title(prs, th, kicker, title, subtitle=None, meta=None, badge=None):
    slide = blank(prs)
    add_rect(slide, 0, 0, SW, SH, th["title_bg"])
    add_rect(slide, 0, SH - Inches(0.14), SW, Inches(0.14), th["accent2"])
    add_rect(slide, MX + Inches(0.05), Inches(2.02), Inches(0.62), Inches(0.075), th["accent2"])
    add_textbox(
        slide, MX + Inches(0.05), Inches(2.25), Inches(11.5), Inches(0.5),
        [{"text": kicker, "size": 16, "bold": True, "color": th["accent2_lt"]}],
    )
    add_textbox(
        slide, MX + Inches(0.05), Inches(2.72), Inches(12.0), Inches(1.9),
        [{"text": title, "size": 38, "bold": True, "color": "FFFFFF", "ls": 1.08}],
    )
    if subtitle:
        add_textbox(
            slide, MX + Inches(0.05), Inches(4.55), Inches(11.6), Inches(1.2),
            [{"text": subtitle, "size": 16.5, "color": "CBD5E1", "ls": 1.25}],
        )
    if meta:
        add_textbox(
            slide, MX + Inches(0.05), Inches(6.55), Inches(11.6), Inches(0.5),
            [{"text": meta, "size": 11.5, "color": "8DA2C0"}],
        )
    if badge or th.get("badge"):
        b = badge or th.get("badge")
        wch = Inches(0.5 + 0.16 * len(b))
        add_rect(slide, SW - wch - Inches(0.6), Inches(0.55), wch, Inches(0.42), th["accent2"], rounded=True, radius=0.5)
        add_textbox(
            slide, SW - wch - Inches(0.6), Inches(0.55), wch, Inches(0.42),
            [{"text": b, "size": 13, "bold": True, "color": "FFFFFF", "align": PP_ALIGN.CENTER}],
            anchor=MSO_ANCHOR.MIDDLE,
        )
    return slide


def s_big(prs, th, text, sub=None, kicker=None):
    slide = blank(prs)
    add_rect(slide, 0, 0, SW, SH, th["title_bg"])
    add_rect(slide, 0, SH - Inches(0.14), SW, Inches(0.14), th["accent2"])
    if kicker:
        add_textbox(
            slide, Inches(1.2), Inches(2.15), Inches(10.9), Inches(0.5),
            [{"text": kicker, "size": 15, "bold": True, "color": th["accent2_lt"], "align": PP_ALIGN.CENTER}],
        )
    add_textbox(
        slide, Inches(1.0), Inches(2.75), Inches(11.3), Inches(2.2),
        [{"text": text, "size": 30, "bold": True, "color": "FFFFFF", "align": PP_ALIGN.CENTER, "ls": 1.22}],
    )
    if sub:
        add_textbox(
            slide, Inches(1.6), Inches(5.05), Inches(10.1), Inches(1.4),
            [{"text": sub, "size": 15, "color": "CBD5E1", "align": PP_ALIGN.CENTER, "ls": 1.3}],
        )
    return slide


def s_bullets(prs, th, title, items, sub=None, note=None, badge=None, size=15.5):
    slide = blank(prs)
    top = s_head(slide, th, title, sub, badge)
    paras = []
    for it in items:
        lvl, txt = it[0], it[1]
        opt = it[2] if len(it) > 2 else {}
        if lvl == 0:
            paras.append({
                "runs": [("●  ", {"size": size - 4, "color": th["accent"]}),
                         (txt, {"size": opt.get("size", size), "bold": opt.get("bold", False),
                                "color": opt.get("color", "1F2937")})],
                "sb": 6, "sa": 5, "ls": 1.18,
            })
        elif lvl == 1:
            paras.append({
                "runs": [("      –  ", {"size": size - 3, "color": "9CA3AF"}),
                         (txt, {"size": opt.get("size", size - 2.5), "bold": opt.get("bold", False),
                                "color": opt.get("color", "4B5563")})],
                "sb": 1, "sa": 2, "ls": 1.15,
            })
        else:  # heading-style row
            paras.append({
                "text": txt, "size": opt.get("size", size + 1), "bold": True,
                "color": opt.get("color", th["ink"]), "sb": 12, "sa": 4,
            })
    add_textbox(slide, MX + Inches(0.15), Inches(top + 0.12), Inches(11.7), Inches(6.4 - top), paras)
    if note:
        s_note(slide, th, note)
    return slide


def _fill_cell(cell, text, size, bold, color, align=PP_ALIGN.LEFT, fill=None):
    if fill:
        cell.fill.solid()
        cell.fill.fore_color.rgb = C(fill)
    cell.vertical_anchor = MSO_ANCHOR.MIDDLE
    cell.margin_left = Inches(0.08)
    cell.margin_right = Inches(0.08)
    cell.margin_top = Inches(0.03)
    cell.margin_bottom = Inches(0.03)
    tf = cell.text_frame
    tf.word_wrap = True
    lines = str(text).split("\n")
    first = True
    for ln in lines:
        p = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        p.alignment = align
        p.line_spacing = 1.08
        r = p.add_run()
        r.text = ln
        set_font(r, size=size, bold=bold, color=color)


def s_table(prs, th, title, headers, rows, sub=None, widths=None, note=None,
            badge=None, fsize=12, hsize=12, first_bold=True, tall=False):
    slide = blank(prs)
    top = s_head(slide, th, title, sub, badge)
    n_r, n_c = len(rows) + 1, len(headers)
    avail_h = (6.5 if not note else 6.4) - top
    gt = slide.shapes.add_table(n_r, n_c, MX, Inches(top + 0.08), CW, Inches(min(avail_h, 0.42 * n_r)))
    table = gt.table
    if widths:
        total = sum(widths)
        for i, w in enumerate(widths):
            table.columns[i].width = Emu(int(CW * w / total))
    for j, htxt in enumerate(headers):
        _fill_cell(table.cell(0, j), htxt, hsize, True, "FFFFFF",
                   PP_ALIGN.CENTER, th["ink"])
    for i, row in enumerate(rows):
        fill = "FFFFFF" if i % 2 == 0 else th["soft"]
        for j, val in enumerate(row):
            bold = first_bold and j == 0
            _fill_cell(table.cell(i + 1, j), val, fsize, bold,
                       th["ink"] if bold else "374151", PP_ALIGN.LEFT, fill)
    table.rows[0].height = Pt(24)
    if note:
        s_note(slide, th, note)
    return slide


def s_cards(prs, th, title, cards, sub=None, cols=2, note=None, badge=None,
            body_size=11.5, head_size=14.5):
    slide = blank(prs)
    top = s_head(slide, th, title, sub, badge)
    n = len(cards)
    rows = (n + cols - 1) // cols
    gap = 0.22
    bottom = 6.55 if note else 7.0
    avail_h = bottom - top - 0.08
    ch = (avail_h - gap * (rows - 1)) / rows
    cw = (12.133 - gap * (cols - 1)) / cols
    for idx, card in enumerate(cards):
        r, c = divmod(idx, cols)
        x = Inches(0.6 + c * (cw + gap))
        y = Inches(top + 0.08 + r * (ch + gap))
        add_rect(slide, x, y, Inches(cw), Inches(ch), card.get("bg", "F8FAFC"),
                 line="D8E0EA", rounded=True, radius=0.055)
        cy = y + Inches(0.14)
        cx = x + Inches(0.18)
        tag = card.get("tag")
        head_y = cy
        if tag:
            tw = add_chip(slide, cx, cy, tag, card.get("tag_color", TAG_COLORS.get(tag, th["accent"])))
            head_y = cy
            add_textbox(
                slide, cx + tw + Inches(0.14), cy - Inches(0.015), Inches(cw) - tw - Inches(0.5), Inches(0.4),
                [{"text": card.get("head", ""), "size": head_size, "bold": True, "color": th["ink"]}],
            )
            body_y = cy + Inches(0.42)
        else:
            add_textbox(
                slide, cx, cy, Inches(cw - 0.36), Inches(0.4),
                [{"text": card.get("head", ""), "size": head_size, "bold": True, "color": th["ink"]}],
            )
            body_y = cy + Inches(0.40)
        paras = []
        for ln in card.get("lines", []):
            if isinstance(ln, tuple):
                label, val = ln
                paras.append({"runs": [(label + "  ", {"size": body_size, "bold": True, "color": th["accent"]}),
                                       (val, {"size": body_size, "color": "374151"})],
                              "sa": 3, "ls": 1.14})
            else:
                paras.append({"runs": [("· ", {"size": body_size, "color": "9CA3AF"}),
                                       (ln, {"size": body_size, "color": "374151"})],
                              "sa": 3, "ls": 1.14})
        add_textbox(slide, cx, body_y, Inches(cw - 0.36), y + Inches(ch) - body_y - Inches(0.08), paras)
    if note:
        s_note(slide, th, note)
    return slide


def s_prompt(prs, th, title, code, sub=None, note=None, badge=None, size=11.5):
    slide = blank(prs)
    top = s_head(slide, th, title, sub, badge)
    bottom = 6.5 if note else 6.95
    add_rect(slide, MX, Inches(top + 0.06), CW, Inches(bottom - top - 0.06), "0F172A",
             rounded=True, radius=0.03)
    paras = []
    for i, line in enumerate(code.strip("\n").split("\n")):
        color = "7DD3FC" if (line.strip().endswith(":") and len(line) < 30) else "E2E8F0"
        if line.startswith("#"):
            color = "94A3B8"
        paras.append({"text": line if line else " ", "size": size, "color": color,
                      "name": MONO, "sa": 1.5, "ls": 1.12})
    add_textbox(slide, MX + Inches(0.3), Inches(top + 0.28), CW - Inches(0.6),
                Inches(bottom - top - 0.5), paras)
    if note:
        s_note(slide, th, note)
    return slide


def s_twocol(prs, th, title, left, right, sub=None, note=None, badge=None, body_size=13):
    slide = blank(prs)
    top = s_head(slide, th, title, sub, badge)
    bottom = 6.5 if note else 6.95
    h = bottom - top - 0.06
    w = (12.133 - 0.26) / 2
    for i, col in enumerate([left, right]):
        x = Inches(0.6 + i * (w + 0.26))
        y = Inches(top + 0.06)
        hd_color = col.get("color", th["accent"] if i == 0 else th["accent2"])
        add_rect(slide, x, y, Inches(w), Inches(h), "F8FAFC", line="D8E0EA", rounded=True, radius=0.04)
        add_rect(slide, x, y, Inches(w), Inches(0.52), hd_color, rounded=True, radius=0.04)
        add_rect(slide, x, y + Inches(0.26), Inches(w), Inches(0.26), hd_color)
        add_textbox(slide, x + Inches(0.2), y, Inches(w - 0.4), Inches(0.52),
                    [{"text": col["head"], "size": 15, "bold": True, "color": "FFFFFF"}],
                    anchor=MSO_ANCHOR.MIDDLE)
        paras = []
        for it in col["items"]:
            lvl, txt = (it[0], it[1]) if isinstance(it, tuple) else (0, it)
            if lvl == 0:
                paras.append({"runs": [("●  ", {"size": body_size - 4, "color": hd_color}),
                                       (txt, {"size": body_size, "color": "1F2937"})],
                              "sb": 5, "sa": 3, "ls": 1.16})
            else:
                paras.append({"runs": [("     – ", {"size": body_size - 3, "color": "9CA3AF"}),
                                       (txt, {"size": body_size - 1.5, "color": "4B5563"})],
                              "sa": 2, "ls": 1.12})
        add_textbox(slide, x + Inches(0.22), y + Inches(0.68), Inches(w - 0.44), Inches(h - 0.85), paras)
    if note:
        s_note(slide, th, note)
    return slide


BUILDERS = {
    "title": s_title,
    "big": s_big,
    "bullets": s_bullets,
    "table": s_table,
    "cards": s_cards,
    "prompt": s_prompt,
    "twocol": s_twocol,
}


def build_deck(path, theme, slides):
    prs = new_deck()
    for name, kwargs in slides:
        BUILDERS[name](prs, theme, **kwargs)
    prs.save(path)
    return len(slides)


THEME_STUDENT = {
    "ink": "14213D",
    "accent": "2563EB",
    "accent2": "F59E0B",
    "accent2_lt": "FBBF24",
    "soft": "EEF2F7",
    "title_bg": "14213D",
    "badge": None,
}

THEME_INSTRUCTOR = {
    "ink": "292524",
    "accent": "D97706",
    "accent2": "B45309",
    "accent2_lt": "FCD34D",
    "soft": "FDF3E3",
    "title_bg": "292524",
    "badge": "강사용",
}
