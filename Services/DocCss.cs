namespace Lectern.Services;

/// <summary>Styles for document content: shared by the reader (scoped to <c>.content</c>) and the PDF.</summary>
public static class DocCss
{
    public const string Content = """
        .content{--note:#2f6fb0;--warning:#b7791f;--danger:#c0392b;--tip:#2e8b57;--mark:#ffe58a}
        .content h1{font-size:1.9em;line-height:1.25;margin:.2em 0 .6em}
        .content h2{font-size:1.4em;margin:1.8em 0 .5em;padding-bottom:.2em;border-bottom:1px solid var(--line)}
        .content h3{font-size:1.15em;margin:1.4em 0 .4em}
        .content p,.content li{line-height:1.65}
        .content img,.content video{max-width:100%;height:auto;border-radius:8px;border:1px solid var(--line)}
        .content figure{margin:1.2em 0}.content figcaption{color:var(--muted);font-size:.9em;margin-top:.3em}
        .content code{font:0.88em/1.5 ui-monospace,SFMono-Regular,Menlo,"DejaVu Sans Mono",monospace;background:var(--code);padding:.1em .35em;border-radius:4px}
        .content .code{position:relative;margin:1em 0}
        .content .code pre{background:var(--code);border:1px solid var(--line);border-radius:8px;padding:14px 16px;overflow:auto;margin:0}
        .content .code pre code{background:none;padding:0;font-size:.86em}
        .content .code .lang{position:absolute;top:6px;left:12px;font-size:.7em;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
        .content .code .lang+.copy+pre{padding-top:26px}
        .content .copy{position:absolute;top:6px;right:8px;font-size:.75em;border:1px solid var(--line);background:var(--panel);color:var(--muted);border-radius:6px;padding:2px 8px;cursor:pointer}
        .content .callout{border-left:4px solid var(--note);background:var(--panel);padding:.6em 1em;border-radius:0 8px 8px 0;margin:1em 0}
        .content .callout.warning{border-color:var(--warning)}.content .callout.danger{border-color:var(--danger)}.content .callout.tip{border-color:var(--tip)}
        .content .callout p{margin:.4em 0}
        .content blockquote{margin:1em 0;padding:.2em 1em;border-left:3px solid var(--line);color:var(--muted)}
        .content .table{overflow-x:auto;margin:1em 0}.content table{border-collapse:collapse;width:100%;font-size:.93em}
        .content th,.content td{border:1px solid var(--line);padding:6px 10px;text-align:left;vertical-align:top}.content th{background:var(--panel)}
        .content td p,.content th p{margin:.2em 0}
        .content hr{border:0;border-top:1px solid var(--line);margin:2em 0}
        .content .card{display:flex;flex-direction:column;gap:2px;border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin:.8em 0;text-decoration:none;color:var(--text);background:var(--panel)}
        .content .card strong{color:var(--accent)}
        .content .file a{font-weight:600}
        .content a{color:var(--accent)}
        .content mark{background:var(--mark);color:inherit;border-radius:2px}
        .content ul.tasks{list-style:none;padding-left:1.2em}
        """;

    /// <summary>The PDF's HTML. Placeholders: {title} {synced} {source} {toc} {body}.</summary>
    public const string PdfTemplate = $$"""
        <!doctype html><html><head><meta charset="utf-8"><title>{title}</title><style>
        :root{--bg:#fff;--panel:#f3f1ec;--text:#1f2328;--muted:#6a6f76;--line:#e3e0d8;--accent:#c2581b;--code:#f0eee8}
        *{box-sizing:border-box}
        body{margin:0;background:#fff;color:var(--text);font:11pt/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,"Noto Sans","DejaVu Sans",sans-serif}
        .muted{color:var(--muted)}
        {{Content}}
        @page{size:A4;margin:16mm 15mm 18mm}
        .cover{height:240mm;display:flex;flex-direction:column;justify-content:center}
        .cover h1{font-size:28pt;margin:0 0 8pt;border:0}
        .toc{page-break-before:always}.toc ol{list-style:none;padding:0}.toc li{margin:3pt 0}
        .toc a{color:var(--text);text-decoration:none}
        .page{page-break-before:always}
        .content img{max-height:200mm}
        .content .copy{display:none}
        pre{white-space:pre-wrap;word-break:break-word}
        h2,h3{page-break-after:avoid}figure,.code,table{page-break-inside:avoid}
        </style></head><body><div class="content">
        <div class="cover"><h1>{title}</h1><p class="muted">Synced {synced}<br>{source}</p></div>
        <div class="toc"><h2>Contents</h2><ol>{toc}</ol></div>
        {body}
        </div></body></html>
        """;
}
