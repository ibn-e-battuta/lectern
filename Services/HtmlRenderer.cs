using System.Globalization;
using System.Net;
using System.Text;
using System.Text.Json;

namespace Lectern.Services;

/// <summary>Renders a bitdocs page (ProseMirror JSON) as HTML for the reader and the PDF.</summary>
/// <param name="asset">The URL to use for an image or file.</param>
/// <param name="link">Rewrites a link; the flag says whether it leaves Lectern (opens in a new tab).</param>
public sealed class HtmlRenderer(Func<string, string> asset, Func<string, (string Href, bool External)> link)
{
    private static readonly Dictionary<string, string> Callouts = new()
    {
        ["info"] = "note", ["note"] = "note", ["warning"] = "warning", ["error"] = "danger",
        ["danger"] = "danger", ["success"] = "tip", ["tip"] = "tip",
    };

    public string Render(JsonElement? body) => Blocks(Js.Get(body, "content"));

    private static string E(string? s) => WebUtility.HtmlEncode(s ?? "");

    /// <summary>Only web, mail and in-page links; never javascript: or data: URLs from a document.</summary>
    public static bool SafeUrl(string url)
    {
        var colon = url.IndexOf(':');
        var slash = url.IndexOfAny(['/', '?', '#']);
        if (colon < 0 || (slash >= 0 && slash < colon)) return true; // relative
        var scheme = url[..colon].ToLowerInvariant();
        return scheme is "http" or "https" or "mailto" or "file";
    }

    private string Blocks(JsonElement? nodes)
    {
        var sb = new StringBuilder();
        foreach (var n in Js.Items(nodes)) sb.Append(Block(n));
        return sb.ToString();
    }

    private string Inline(JsonElement? nodes)
    {
        var sb = new StringBuilder();
        foreach (var n in Js.Items(nodes))
        {
            switch (Js.Str(n, "type"))
            {
                case "text": sb.Append(Marks(E(Js.Str(n, "text")), Js.Items(Js.Get(n, "marks")))); break;
                case "hardBreak": sb.Append("<br>"); break;
                default: sb.Append(Inline(Js.Get(n, "content"))); break;
            }
        }
        return sb.ToString();
    }

    private string Marks(string text, List<JsonElement> marks)
    {
        foreach (var m in marks)
        {
            text = Js.Str(m, "type") switch
            {
                "code" or "codeInline" => $"<code>{text}</code>",
                "bold" or "strong" => $"<strong>{text}</strong>",
                "italic" or "em" => $"<em>{text}</em>",
                "strike" or "strikethrough" => $"<s>{text}</s>",
                "underline" => $"<u>{text}</u>",
                "highlight" => $"<mark>{text}</mark>",
                _ => text,
            };
        }
        foreach (var m in marks)
        {
            if (Js.Str(m, "type") != "link") continue;
            var href = Js.Str(Js.Get(m, "attrs"), "href") ?? "";
            if (href.Length == 0) continue;
            var (h, external) = link(href);
            if (!SafeUrl(h)) continue;
            var target = external ? " target=\"_blank\" rel=\"noopener\"" : "";
            text = $"<a href=\"{E(h)}\"{target}>{text}</a>";
        }
        return text;
    }

    private string Block(JsonElement n)
    {
        var t = Js.Str(n, "type");
        var a = Js.Get(n, "attrs");
        var c = Js.Get(n, "content");
        var items = Js.Items(c);
        switch (t)
        {
            case "paragraph":
            {
                var inner = Inline(c);
                return inner.Trim().Length > 0 ? $"<p>{inner}</p>" : "";
            }
            case "heading":
            {
                var lvl = Math.Clamp(Js.PyInt(Js.First(a, "level"), 1), 1, 6);
                return $"<h{lvl} id=\"h-{E(Js.PyStr(Js.Get(a, "id")))}\">{Inline(c)}</h{lvl}>";
            }
            case "codeBlock" or "code_block":
            {
                var lang = Js.PyStr(Js.Get(a, "language")) ?? "";
                if (lang is "null" or "plaintext" or "auto") lang = "";
                var code = E(string.Concat(items.Select(MarkdownConverter.Plain)));
                var label = lang.Length > 0 ? $"<span class=\"lang\">{E(lang)}</span>" : "";
                return $"<div class=\"code\">{label}<button class=\"copy\" type=\"button\">Copy</button><pre><code>{code}</code></pre></div>";
            }
            case "bulletList":
                return "<ul>" + string.Concat(items.Select(li => $"<li>{Blocks(Js.Get(li, "content"))}</li>")) + "</ul>";
            case "orderedList":
                return $"<ol start=\"{Js.PyInt(Js.First(a, "start"), 1)}\">" + string.Concat(items.Select(li => $"<li>{Blocks(Js.Get(li, "content"))}</li>")) + "</ol>";
            case "todoList" when !items.Any(li => Js.Has(Js.Get(li, "attrs"), "checked")):
                return "<ul>" + string.Concat(items.Select(li => $"<li>{Blocks(Js.Get(li, "content"))}</li>")) + "</ul>";
            case "taskList" or "todoList":
                return "<ul class=\"tasks\">" + string.Concat(items.Select(li =>
                    $"<li class=\"task\"><input type=\"checkbox\" disabled {(Js.Truthy(Js.Get(Js.Get(li, "attrs"), "checked")) ? "checked" : "")}> {Blocks(Js.Get(li, "content"))}</li>")) + "</ul>";
            case "blockquote": return $"<blockquote>{Blocks(c)}</blockquote>";
            case "hint" or "callout" or "callOut" or "infoBox" or "panel" or "alert" or "note":
            {
                var kind = Callouts.GetValueOrDefault((Js.PyStr(Js.First(a, "type")) ?? "note").ToLowerInvariant(), "note");
                return $"<div class=\"callout {kind}\">{Blocks(c)}</div>";
            }
            case "horizontalRule" or "divider": return "<hr>";
            case "table": return Table(items);
            case "atomic": return Atomic(Js.Get(a, "data"));
            case "image":
            {
                var src = Js.PyStr(Js.First(a, "src")) ?? "";
                var url = src.Length > 0 ? asset(src) : "";
                return url.Length > 0 && SafeUrl(url)
                    ? $"<figure><img src=\"{E(url)}\" alt=\"{E(Js.PyStr(Js.First(a, "alt")))}\" loading=\"lazy\"></figure>" : "";
            }
            case "details": return $"<details>{Blocks(c)}</details>";
            case "detailsSummary" or "summary": return $"<summary>{Inline(c)}</summary>";
        }
        if (items.Count > 0)
            return items.All(x => Js.Str(x, "type") is "text" or "hardBreak") ? $"<p>{Inline(c)}</p>" : Blocks(c);
        return "";
    }

    private string Table(List<JsonElement> rows)
    {
        var sb = new StringBuilder("<div class=\"table\"><table>");
        for (var i = 0; i < rows.Count; i++)
        {
            sb.Append("<tr>");
            foreach (var cell in Js.Items(Js.Get(rows[i], "content")))
            {
                var tag = Js.Str(cell, "type") == "tableHeader" || (i == 0 && rows.Count > 1) ? "th" : "td";
                var ca = Js.Get(cell, "attrs");
                var span = "";
                if (Js.PyInt(Js.Get(ca, "colspan"), 1) > 1) span += $" colspan=\"{Js.PyInt(Js.Get(ca, "colspan"), 1)}\"";
                if (Js.PyInt(Js.Get(ca, "rowspan"), 1) > 1) span += $" rowspan=\"{Js.PyInt(Js.Get(ca, "rowspan"), 1)}\"";
                sb.Append($"<{tag}{span}>{Blocks(Js.Get(cell, "content"))}</{tag}>");
            }
            sb.Append("</tr>");
        }
        return sb.Append("</table></div>").ToString();
    }

    private string Atomic(JsonElement? d)
    {
        var kind = Js.PyStr(Js.Get(d, "kind"));
        var title = Js.PyStr(Js.First(d, "title")) ?? kind ?? "";
        switch (kind)
        {
            case "divider": return "<hr>";
            case "image":
            {
                var src = Cdn(Js.PyStr(Js.First(d, "image", "url")));
                if (src.Length == 0) return "";
                var url = asset(src);
                if (!SafeUrl(url)) return "";
                var cap = Js.Truthy(Js.Get(d, "caption")) ? $"<figcaption>{E(Js.PyStr(Js.Get(d, "caption")))}</figcaption>" : "";
                return $"<figure><img src=\"{E(url)}\" alt=\"{E(title)}\" loading=\"lazy\">{cap}</figure>";
            }
            case "file":
            {
                var src = asset(Cdn(Js.PyStr(Js.First(d, "filepath", "url"))));
                var size = Js.Get(d, "filesize") is { } fs && Js.Truthy(fs) ? " · " + HumanSize(fs) : "";
                return SafeUrl(src) ? $"<p class=\"file\">📎 <a href=\"{E(src)}\" download>{E(title)}</a><span class=\"muted\">{size}</span></p>" : "";
            }
            case "link":
            {
                var url = Js.PyStr(Js.First(d, "url")) ?? "";
                if (!SafeUrl(url)) return "";
                var desc = Js.PyStr(Js.First(d, "description")) ?? "";
                if (desc.Length > 240) desc = desc[..240];
                return $"<a class=\"card\" href=\"{E(url)}\" target=\"_blank\" rel=\"noopener\"><strong>{E(title)}</strong><span>{E(desc)}</span><span class=\"muted\">{E(Js.PyStr(Js.First(d, "domain")))}</span></a>";
            }
        }
        if (kind is not null && MarkdownConverter.VideoKinds.Contains(kind))
        {
            var fp = Cdn(Js.PyStr(Js.First(d, "filepath", "file")));
            var url = Js.PyStr(Js.First(d, "url", "src")) ?? "";
            if (fp.Length > 0 && asset(fp) is var local && SafeUrl(local))
                return $"<figure><video controls preload=\"metadata\" src=\"{E(local)}\"></video><figcaption>{E(title)}</figcaption></figure>";
            if (url.Length > 0 && SafeUrl(url))
                return $"<p class=\"file\">🎬 <a href=\"{E(url)}\" target=\"_blank\" rel=\"noopener\">{E(title.Length > 0 ? title : url)}</a></p>";
        }
        return "";
    }

    private static string Cdn(string? p) =>
        string.IsNullOrEmpty(p) ? "" : p.StartsWith("http", StringComparison.Ordinal) ? p : MarkdownConverter.UploadsUrl + "/" + p.TrimStart('/');

    public static string HumanSize(JsonElement v)
    {
        double n;
        if (v.ValueKind == JsonValueKind.Number) n = v.GetDouble();
        else if (!double.TryParse(Js.PyStr(v), NumberStyles.Float, CultureInfo.InvariantCulture, out n)) return "";
        foreach (var unit in new[] { "B", "KB", "MB", "GB" })
        {
            if (n < 1024) return $"{n:0} {unit}";
            n /= 1024;
        }
        return $"{n:0.0} TB";
    }

    /// <summary>All the text of a page, for search snippets.</summary>
    public static string Text(JsonElement? body) =>
        body is { } b ? System.Text.RegularExpressions.Regex.Replace(MarkdownConverter.Plain(b), @"\s+", " ").Trim() : "";
}
