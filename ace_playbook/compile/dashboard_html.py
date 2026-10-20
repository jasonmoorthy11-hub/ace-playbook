"""The page shell of `compile dashboard`: one inline stylesheet and the document skeleton, as plain strings (no markup logic).

Colours are custom properties on `:root`; the dark set is written twice (under the OS preference unless the page is pinned light, and
when the page is pinned dark). Nothing is loaded from outside and no script runs. Status colours are reserved and always sit beside a
glyph and a word. Imports: none.
"""

LIGHT = ("--surface:#fcfcfb;--page:#f9f9f7;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;--base:#c3c2b7;"
         "--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--good:#0ca30c;--warn:#fab219;--serious:#ec835a;--crit:#d03b3b;")
DARK = ("--surface:#1a1a19;--page:#0d0d0d;--ink:#f0efec;--ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--base:#383835;"
        "--s1:#3987e5;--s2:#d95926;--s3:#199e70;--good:#0ca30c;--warn:#fab219;--serious:#ec835a;--crit:#d03b3b;")

RULES = """
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--page);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:980px;margin:0 auto;padding:16px}
h1,h2{margin:0;font-weight:600}
h1{font-size:13px;color:var(--ink2);letter-spacing:.04em;text-transform:uppercase}
h2{font-size:15px;margin:28px 0 8px}
p{margin:4px 0}
.card{background:var(--surface);border:1px solid var(--grid);border-radius:8px;padding:16px;min-width:0}
.hero{margin-top:8px}
.day{font-size:56px;font-weight:600;line-height:1.05;font-variant-numeric:tabular-nums}
.sub{color:var(--ink2)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px;margin-top:12px}
.tile .k{color:var(--ink2);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.tile .v{font-size:28px;font-weight:600;line-height:1.2;font-variant-numeric:tabular-nums}
.tile .line{color:var(--ink2);font-size:13px}
.st{display:inline-block;min-width:1.1em;text-align:center;font-weight:700}
.good{color:var(--good)}.warn{color:var(--warn)}.serious{color:var(--serious)}.crit{color:var(--crit)}
.neutral{color:var(--ink2)}.st.muted{color:var(--muted)}
.scroll{overflow-x:auto;max-width:100%;background:var(--surface);border:1px solid var(--grid);border-radius:8px}
table{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}
th{text-align:left;color:var(--ink2);font-weight:600;white-space:nowrap}
th,td{padding:6px 10px;border-bottom:1px solid var(--grid)}
tr:last-child td{border-bottom:0}
td{vertical-align:top}
.chart{padding:12px 16px 8px}
.wrow{display:grid;grid-template-columns:minmax(90px,32%) 1fr;gap:0 12px;align-items:center;padding:4px 0}
.wl{font-size:13px;color:var(--ink);overflow-wrap:anywhere;line-height:1.3}
.wl small{display:block;font-size:12px;color:var(--ink2)}
.track,.axis{position:relative;margin:0 6px}
.track{height:24px}
.axis{height:34px}
.track::before{content:"";position:absolute;left:0;right:0;top:12px;height:1px;background:var(--grid)}
.track>*{position:absolute}
.thr{top:-4px;bottom:-4px;width:0;border-left:1px dashed var(--muted)}
.bar{top:11px;height:2px;border-radius:2px;background:var(--s1)}
.cap{top:10px;width:4px;height:4px;border-radius:50%;background:var(--s1)}
.point{top:8px;width:8px;height:8px;border-radius:50%;background:var(--s1);box-shadow:0 0 0 2px var(--surface)}
.insuf{left:0;top:4px;padding-right:6px;font-size:12px;line-height:16px;color:var(--muted);background:var(--surface)}
.tick{position:absolute;top:18px;font-size:12px;line-height:1.3;color:var(--muted);white-space:nowrap}
.tick.top{top:0}
.legend{color:var(--ink2);font-size:12px;margin:8px 8px 0}
.sw{display:inline-block;width:14px;height:2px;background:var(--s1);vertical-align:middle;margin-right:6px}
ul.notes{margin:0;padding-left:20px;overflow-wrap:anywhere}
footer{margin:28px 0 8px;color:var(--ink2);font-size:12px;overflow-wrap:anywhere}
"""

CSS = (":root{" + LIGHT + "}\n@media (prefers-color-scheme: dark){:root:not([data-theme=\"light\"]){" + DARK + "}}\n"
       ":root[data-theme=\"dark\"]{" + DARK + "}" + RULES)

PAGE = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n<title>ace status</title>\n<style>' + CSS + '</style>\n'
        '</head>\n<body>\n<main>\n<h1>ace-playbook status</h1>\n@BODY@\n</main>\n</body>\n</html>\n')


def page(body: str) -> str:
    """The whole document around `body` (already escaped HTML)."""
    return PAGE.replace("@BODY@", body)
