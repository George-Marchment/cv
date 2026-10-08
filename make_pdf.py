#!/usr/bin/env python3
"""Build a minimalist, academic-style LaTeX/PDF CV from index.html.

The content is taken verbatim from the HTML page; only the layout changes.
The PhD summary is deliberately left out.

sudo apt install texlive-latex-extra

Usage:
    python3 make_pdf.py                 # writes cv.tex and cv.pdf
    python3 make_pdf.py --tex-only      # only writes cv.tex
    python3 make_pdf.py --photo         # include imgs/portrait.png in the header
"""

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag

HERE = Path(__file__).resolve().parent

# Numbered labels for publication lists, chosen from keywords in the sub-heading.
PUB_LABELS = [("journal", "J"), ("conference", "C"), ("book", "B"), ("poster", "P")]

# Sections whose entry titles are not set in bold (long titles read better plain).
PLAIN_TITLE_SECTIONS = {"Talks"}

DATE_RE = re.compile(r"(?:[A-Za-zéû]+\s+)?\d{4}(?:--(?:\d{4}|present))?")

# --------------------------------------------------------------------------- #
# Text helpers
# --------------------------------------------------------------------------- #

LATEX_SPECIALS = {
    "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
    "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}
UNICODE_FIXES = {"\u2011": "-", "\u00a0": " ", "\u2009": " ", "\u2019": "'",
                 "\u2013": "--", "\u2014": "---"}


def escape(text):
    for k, v in UNICODE_FIXES.items():
        text = text.replace(k, v)
    text = "".join(LATEX_SPECIALS.get(c, c) for c in text)
    return re.sub(r"(\d)-(\d)", r"\1--\2", text)  # 2018-2021 -> en-dash


def escape_url(url):
    url = url.strip()
    if not re.match(r"^[a-z]+:", url):
        url = "https://" + url
    return url.replace("\\", "/").replace("%", r"\%").replace("#", r"\#")


def tidy(s):
    s = re.sub(r"\s+", " ", s).strip()
    return re.sub(r"\s+([,.;:)])", r"\1", s)


def inline(node, bold=r"\textbf{%s}"):
    """Convert an HTML fragment to inline LaTeX."""
    if isinstance(node, NavigableString):
        if node.__class__.__name__ == "Comment":
            return ""
        return escape(re.sub(r"\s+", " ", str(node)))
    if not isinstance(node, Tag):
        return ""
    inner = lambda: "".join(inline(c, bold) for c in node.children)
    name = node.name
    if name in ("i", "em"):
        return r"\emph{%s}" % inner().strip()
    if name in ("b", "strong"):
        return bold % inner().strip()
    if name == "u":  # the author's own name
        return r"\textbf{%s}" % inner().strip()
    if name == "a":
        href = node.get("href", "").strip()
        text = " ".join(node.get_text(" ").split())
        if text.rstrip("/") == href.rstrip("/"):
            return r"\url{%s}" % escape_url(href)
        label = re.match(r"link to (.+)", text, re.I)
        if label:  # print the address so it survives on paper
            return r"%s: \url{%s}" % (escape(label.group(1).capitalize()), escape_url(href))
        return r"\href{%s}{%s}" % (escape_url(href), inner().strip())
    if name == "br":
        return " "
    return inner()


def split_br(node):
    """Split the children of `node` into groups separated by <br>."""
    groups, cur = [], []
    for c in node.children:
        if isinstance(c, Tag) and c.name == "br":
            groups.append(cur)
            cur = []
        else:
            cur.append(c)
    groups.append(cur)
    return groups


# --------------------------------------------------------------------------- #
# Block renderers
# --------------------------------------------------------------------------- #

def render_entry(node, plain_title=False):
    """An entry is a title line followed by 'a | b | date' meta lines."""
    groups = split_br(node)
    title = tidy("".join(inline(c, bold="%s") for c in groups[0]))
    lines = []
    for g in groups[1:]:
        raw = tidy("".join(inline(c) for c in g))
        parts = [re.sub(r"^\\emph\{(.*)\}$", r"\1", p.strip())
                 for p in raw.split("|") if p.strip()]
        if not parts:
            continue
        dates = [p for p in parts if DATE_RE.fullmatch(p)]
        others = [p for p in parts if p not in dates]
        lines.append((r" \enspace$\cdot$\enspace ".join(others), " ".join(dates)))

    if not plain_title:
        title = r"\textbf{%s}" % title
    n_dates = sum(1 for _, d in lines if d)
    out = []
    if n_dates == 1:
        date = next(d for _, d in lines if d)
        out.append(r"\entry{%s}{%s}" % (title, date))
        out += [r"\meta{%s}{}" % t for t, _ in lines if t]
    else:
        out.append(r"\entry{%s}{}" % title)
        out += [r"\meta{%s}{%s}" % (t, d) for t, d in lines]
    return "\n".join(out) + "\n"


def entry_source(li):
    """Return the node holding an entry, or None if the <li> is a citation."""
    p = li.find("p", class_=["tags", "tag2"])
    if p is not None:
        return p
    if li.find("br") is not None:
        return li
    return None


def render_list(ul, heading, section):
    items = ul.find_all("li")
    if not items:
        return ""
    if all(entry_source(li) is None for li in items):
        label = next((l for k, l in PUB_LABELS if k in heading.lower()), None)
        opts = r"label={[%s\arabic*]}" % label if label else r"label=\textbullet"
        body = "\n".join(r"  \item %s" % tidy(inline(li, bold="%s")) for li in items)
        return "\\begin{publist}[%s]\n%s\n\\end{publist}\n" % (opts, body)
    plain = section in PLAIN_TITLE_SECTIONS
    return "\n".join(render_entry(entry_source(li) or li, plain) for li in items)


def render_phd(card):
    """The PhD card: position, thesis title and supervisors (no summary)."""
    title = escape(card.h2.get_text(" ", strip=True))
    thesis = tidy(inline(card.find("h3", class_="no-nav")))
    sup_ul = None
    for h4 in card.find_all("h4"):
        if "supervisor" in h4.get_text().lower():
            sup_ul = h4.find_next_sibling("ul")
    info = card.find("p", class_="tags")
    parts = [p.strip() for p in tidy(inline(info)).split("|")] if info else []
    date = " ".join(p for p in parts if DATE_RE.fullmatch(p))
    out = [r"\entry{\textbf{%s}}{%s}" % (title, date)]
    out += [r"\meta{%s}{}" % p for p in parts if p and not DATE_RE.fullmatch(p)]
    out.append(r"\meta{Thesis: \emph{%s}}{}" % thesis)
    if sup_ul is not None:
        names = [tidy(inline(li)) for li in sup_ul.find_all("li")]
        out.append(r"\meta{Supervisors: %s}{}" % " and ".join(names))
    return "\n".join(out) + "\n"


def render_card(card, section):
    out = []
    heading = ""
    for el in card.find_all(["h3", "h4", "ul"], recursive=False):
        if el.name in ("h3", "h4"):
            heading = el.get_text(" ", strip=True)
            out.append(r"\subsection*{%s}" % escape(heading))
        else:
            out.append(render_list(el, heading, section))
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Document
# --------------------------------------------------------------------------- #

PREAMBLE = r"""\documentclass[10pt,a4paper]{article}
\usepackage[T1]{fontenc}
\usepackage[utf8]{inputenc}
\usepackage{lmodern}
\usepackage[margin=2cm]{geometry}
\usepackage{xcolor}
\usepackage{graphicx}
\usepackage{enumitem}
\usepackage{titlesec}
\usepackage{xurl}
\usepackage[hidelinks]{hyperref}
\usepackage{microtype}

\definecolor{linkblue}{RGB}{20,60,140}
\hypersetup{colorlinks=true, urlcolor=linkblue, linkcolor=linkblue}
\urlstyle{same}
\pagestyle{plain}
\setlength{\parindent}{0pt}

\titleformat{\section}{\large\scshape}{}{0em}{}[\vspace{-0.6ex}\titlerule]
\titlespacing*{\section}{0pt}{2.2ex}{1.2ex}
\titleformat{\subsection}{\normalsize\itshape}{}{0em}{}
\titlespacing*{\subsection}{0pt}{1.4ex}{0.6ex}

\newlist{publist}{enumerate}{1}
\setlist[publist]{leftmargin=2.6em, labelsep=0.6em, itemsep=0.5ex,
                  topsep=0.3ex, align=left}

% \entry{title}{date}: title line with an optional right-aligned date
\newcommand{\entry}[2]{\par\addvspace{0.9ex}%
  \begin{minipage}[t]{\dimexpr\linewidth-3cm}\raggedright #1\strut\end{minipage}%
  \hfill\begin{minipage}[t]{2.8cm}\raggedleft #2\end{minipage}\par}
% \meta{text}{date}: secondary line under an entry
\newcommand{\meta}[2]{\nopagebreak{\small\color{black!75}%
  \if\relax\detokenize{#2}\relax #1\else
  \begin{minipage}[t]{\dimexpr\linewidth-3cm}\raggedright #1\strut\end{minipage}%
  \hfill\begin{minipage}[t]{2.8cm}\raggedleft #2\end{minipage}\fi\par}}
"""


def build_tex(html, photo=None):
    soup = BeautifulSoup(html, "html.parser")
    prof = soup.find(id="profile")

    name = tidy(escape(prof.find(id="name").contents[0]))
    email = prof.find(id="email").get_text(strip=True)
    designation = tidy(escape(prof.find(id="designation").contents[0]))
    college = tidy(escape(prof.find(id="college").get_text(" ")))
    lab = tidy(escape(prof.find(id="lab").get_text(" ")))

    about, links = "", []
    for block in prof.find_all(id="about"):
        label = block.find("p").get_text(strip=True).lower()
        if block.find("ul"):
            links = [inline(a) for a in block.find_all("a")]
        elif label == "about":
            block.find("p").extract()
            about = tidy(inline(block))

    mail_href = escape_url("mailto:" + email.replace("[at]", "@"))
    header_text = "\n".join([
        r"{\LARGE\scshape %s}\\[1.2ex]" % name,
        r"%s, %s, %s\\[0.4ex]" % (designation, college, lab),
        r"\href{%s}{%s}\\[0.4ex]" % (mail_href, escape(email)),
        r" \enspace$\cdot$\enspace ".join(links),
    ])
    if photo:
        header = "\n".join([
            r"\begin{minipage}[c]{0.78\linewidth}", header_text, r"\end{minipage}\hfill",
            r"\begin{minipage}[c]{0.18\linewidth}\raggedleft",
            r"\includegraphics[width=\linewidth]{%s}" % photo.as_posix(),
            r"\end{minipage}",
        ])
    else:
        header = "\\begin{center}\n%s\n\\end{center}" % header_text

    body = [header, ""]
    if about:
        body += [r"\medskip", about, ""]

    cards = soup.select("#info-cards > .card")
    phd = [c for c in cards if c.find("h3", class_="no-nav")]
    education = []
    for card in cards:
        title = card.h2.get_text(" ", strip=True)
        if card in phd:
            education.append(render_phd(card))
        elif title == "Diplomas":
            education.append(render_card(card, title))
            body += [r"\section*{Education}"] + education
        else:
            body += [r"\section*{%s}" % escape(title), render_card(card, title)]

    return (PREAMBLE + "\n\\begin{document}\n\n" + "\n".join(body)
            + "\n\\end{document}\n")


def compile_pdf(tex_path, pdf_path):
    engine = next((e for e in ("latexmk", "pdflatex", "tectonic") if shutil.which(e)), None)
    if engine is None:
        sys.exit("No LaTeX engine found (install texlive or tectonic); %s was written." % tex_path)
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "cv.tex"
        shutil.copy(tex_path, src)
        cmd = {
            "latexmk": ["latexmk", "-pdf", "-interaction=nonstopmode", "-quiet", "cv.tex"],
            "pdflatex": ["pdflatex", "-interaction=nonstopmode", "cv.tex"],
            "tectonic": ["tectonic", "cv.tex"],
        }[engine]
        for _ in range(2 if engine == "pdflatex" else 1):
            res = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True)
        if res.returncode != 0 or not (Path(tmp) / "cv.pdf").exists():
            sys.exit(res.stdout[-3000:] + res.stderr[-3000:])
        shutil.copy(Path(tmp) / "cv.pdf", pdf_path)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("html", nargs="?", default=HERE / "index.html", type=Path)
    ap.add_argument("-o", "--output", default=HERE / "cv.pdf", type=Path)
    ap.add_argument("--tex-only", action="store_true", help="only write the .tex file")
    ap.add_argument("--photo", action="store_true", help="include imgs/portrait.png")
    args = ap.parse_args()

    photo = (HERE / "imgs" / "portrait.png") if args.photo else None
    tex = build_tex(args.html.read_text(encoding="utf-8"), photo)
    tex_path = args.output.with_suffix(".tex")
    tex_path.write_text(tex, encoding="utf-8")
    print("wrote", tex_path)
    if not args.tex_only:
        compile_pdf(tex_path, args.output)
        print("wrote", args.output)


if __name__ == "__main__":
    main()
