using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace Lectern.Services;

/// <summary>Turns a bitdocs page (ProseMirror JSON) into Markdown.</summary>
/// <param name="asset">Gives the local path for an image or file URL (or returns the URL). The flag is true for videos.</param>
/// <param name="link">Rewrites links to other pages of the document to local paths.</param>
public sealed partial class MarkdownConverter(Func<string, bool, string> asset, Func<string, string>? link = null)
{
    /// <summary>Where bitdocs keeps uploaded files.</summary>
    public const string UploadsUrl = "https://d3jwyy9rhyhl55.cloudfront.net";

    public static readonly string[] VideoKinds = ["video", "audio", "embed", "youtube", "vimeo", "loom", "wistia", "iframe"];

    private const string QuoteSafe = "/:.%-_()";
    private static readonly HashSet<string> KnownMarks =
    [
        "code", "codeInline", "bold", "strong", "italic", "em", "strike", "strikethrough", "underline", "link",
        "textStyle", "highlight", "color", "fontSize", "subscript", "superscript", "comment",
    ];

    private readonly Func<string, string> _link = link ?? (h => h);

    /// <summary>Node types this converter doesn't know; their text is kept.</summary>
    public HashSet<string> UnknownTypes { get; } = [];

    // ---------- inline ----------

    public string Inline(JsonElement? nodes)
    {
        var sb = new StringBuilder();
        foreach (var n in Js.Items(nodes))
        {
            var t = Js.Str(n, "type");
            switch (t)
            {
                case "text": sb.Append(Marked(Js.Str(n, "text") ?? "", Js.Items(Js.Get(n, "marks")))); break;
                case "hardBreak": sb.Append("  \n"); break;
                case "image" or "inlineImage": sb.Append(Image(n)); break;
                case "mention":
                    sb.Append('@').Append(Js.PyStr(Js.First(Js.Get(n, "attrs"), "label", "name", "id")) ?? "");
                    break;
                case "math" or "mathInline" or "katex" or "inlineMath":
                    sb.Append('$').Append(Js.PyStr(Js.First(Js.Get(n, "attrs"), "latex", "value")) ?? Plain(n)).Append('$');
                    break;
                case "emoji":
                    sb.Append(Js.PyStr(Js.First(Js.Get(n, "attrs"), "emoji", "name")) ?? "");
                    break;
                default:
                    if (!string.IsNullOrEmpty(t)) UnknownTypes.Add(t);
                    sb.Append(Inline(Js.Get(n, "content")));
                    break;
            }
        }
        return sb.ToString();
    }

    private string Marked(string text, List<JsonElement> marks)
    {
        var types = new Dictionary<string, JsonElement>();
        foreach (var m in marks) types[Js.PyStr(Js.Get(m, "type")) ?? "None"] = m;

        if (types.ContainsKey("code") || types.ContainsKey("codeInline"))
        {
            var tick = text.Contains('`') ? "``" : "`";
            text = tick + text + tick;
        }
        else
        {
            var core = text.Trim();
            var lead = text[..(text.Length - text.TrimStart().Length)];
            var trail = text[text.TrimEnd().Length..];
            if (core.Length > 0)
            {
                if (types.ContainsKey("bold") || types.ContainsKey("strong")) core = $"**{core}**";
                if (types.ContainsKey("italic") || types.ContainsKey("em")) core = $"*{core}*";
                if (types.ContainsKey("strike") || types.ContainsKey("strikethrough")) core = $"~~{core}~~";
                if (types.ContainsKey("underline")) core = $"<u>{core}</u>";
            }
            text = lead + core + trail;
        }
        if (types.TryGetValue("link", out var lm))
        {
            var href = Js.Str(Js.Get(lm, "attrs"), "href") ?? "";
            if (href.Length > 0)
            {
                href = _link(href);
                if (!href.StartsWith("http", StringComparison.Ordinal) && !href.StartsWith("mailto:", StringComparison.Ordinal) && !href.StartsWith('#'))
                    href = $"<{href}>"; // local path with spaces
                text = $"[{text}]({href})";
            }
        }
        foreach (var k in types.Keys)
            if (!KnownMarks.Contains(k)) UnknownTypes.Add("mark:" + k);
        return text;
    }

    public static string Plain(JsonElement n)
    {
        if (Js.Str(n, "type") == "text") return Js.Str(n, "text") ?? "";
        var sb = new StringBuilder();
        foreach (var c in Js.Items(Js.Get(n, "content"))) sb.Append(Plain(c));
        return sb.ToString();
    }

    private string Image(JsonElement n)
    {
        var a = Js.Get(n, "attrs");
        var src = Js.PyStr(Js.First(a, "src", "url")) ?? "";
        var alt = (Js.PyStr(Js.First(a, "alt", "title")) ?? "image").Replace("]", "");
        if (src.Length == 0) return "";
        return $"![{alt}]({PyCompat.Quote(asset(src, false), QuoteSafe)})";
    }

    // ---------- blocks ----------

    public string Blocks(JsonElement? nodes, string indent = "")
    {
        var parts = new List<string>();
        foreach (var n in Js.Items(nodes))
        {
            var s = Block(n, indent);
            if (!string.IsNullOrEmpty(s)) parts.Add(s);
        }
        return string.Join("\n\n", parts);
    }

    [GeneratedRegex(@"^\*\*(.+)\*\*$")]
    private static partial Regex BoldOnly();

    [GeneratedRegex(@"\.(mp4|mov|m4v|webm|mp3|m4a|wav)(\?|$)", RegexOptions.IgnoreCase)]
    private static partial Regex MediaFile();

    private string Block(JsonElement n, string indent)
    {
        var t = Js.Str(n, "type");
        var a = Js.Get(n, "attrs");
        var c = Js.Get(n, "content");
        var items = Js.Items(c);
        switch (t)
        {
            case "doc": return Blocks(c, indent);
            case "paragraph": return Prefix(Inline(c), indent);
            case "heading":
            {
                var lvl = Js.PyInt(Js.First(a, "level"), 1);
                var text = Inline(c).Trim();
                var m = BoldOnly().Match(text);
                if (m.Success && !text.Contains('\n') && !m.Groups[1].Value.Contains("**")) text = m.Groups[1].Value; // headings are bold already
                return indent + new string('#', Math.Clamp(lvl, 1, 6)) + " " + text;
            }
            case "codeBlock" or "code_block":
            {
                var lang = Js.PyStr(Js.First(a, "language", "lang")) ?? "";
                if (lang is "null" or "plaintext" or "auto") lang = "";
                var code = string.Concat(items.Select(Plain));
                var fence = code.Contains("```") ? "````" : "```";
                return Prefix($"{fence}{lang}\n{code.TrimEnd('\n')}\n{fence}", indent);
            }
            case "bulletList" or "orderedList" or "taskList" or "todoList":
            {
                var start = Js.PyInt(Js.First(a, "start"), 1);
                var lines = new List<string>();
                for (var i = 0; i < items.Count; i++)
                {
                    var li = items[i];
                    var liAttrs = Js.Get(li, "attrs");
                    string marker;
                    if (t == "orderedList") marker = $"{start + i}. ";
                    else if (t == "taskList" || Js.Str(li, "type") is "taskItem" or "todoItem" || Js.Has(liAttrs, "checked"))
                        marker = Js.Truthy(Js.Get(liAttrs, "checked")) ? "- [x] " : "- [ ] ";
                    else marker = "- ";
                    var body = Blocks(Js.Get(li, "content"), "");
                    var pad = new string(' ', marker.Length);
                    var parts = body.Split('\n');
                    var sb = new StringBuilder(indent + marker + parts[0]);
                    foreach (var ln in parts.Skip(1)) sb.Append('\n').Append(ln.Trim().Length > 0 ? indent + pad + ln : "");
                    lines.Add(sb.ToString());
                }
                return string.Join("\n", lines);
            }
            case "blockquote":
                return string.Join("\n", Blocks(c).Split('\n').Select(ln => ln.Length > 0 ? indent + "> " + ln : indent + ">"));
            case "atomic": return Atomic(Js.Get(a, "data"), indent);
            case "callout" or "callOut" or "infoBox" or "panel" or "alert" or "note" or "hint":
            {
                var kind = (Js.PyStr(Js.First(a, "type", "variant")) ?? "NOTE").ToUpperInvariant();
                kind = kind switch { "INFO" => "NOTE", "SUCCESS" => "TIP", "DANGER" => "CAUTION", "ERROR" => "CAUTION", _ => kind };
                var inner = Blocks(c);
                return string.Join("\n", new[] { indent + $"> [!{kind}]" }
                    .Concat(inner.Split('\n').Select(ln => ln.Length > 0 ? indent + "> " + ln : indent + ">")));
            }
            case "horizontalRule" or "divider": return indent + "---";
            case "image" or "imageBlock" or "figure":
                return Js.Truthy(Js.First(a, "src", "url")) ? indent + Image(n) : Blocks(c, indent);
            case "table": return Table(n, indent);
            case "details" or "toggle" or "collapsible":
            {
                var summary = "";
                var body = new List<JsonElement>();
                foreach (var x in items)
                {
                    if (Js.Str(x, "type") is "detailsSummary" or "summary" or "toggleTitle") summary = Inline(Js.Get(x, "content"));
                    else body.Add(x);
                }
                return $"{indent}<details>\n<summary>{summary}</summary>\n\n{BlocksOf(body)}\n\n</details>";
            }
            case "detailsContent": return Blocks(c, indent);
        }
        if (t is "video" or "audio" or "youtube" or "vimeo" or "loom" or "iframe" && Js.Truthy(Js.First(a, "src", "url")))
            return Video(a, indent);
        if (t is "embed" or "link_preview" or "linkPreview" or "bookmark" or "file" or "attachment" or "smartEmbed")
        {
            var url = Js.PyStr(Js.First(a, "src", "url", "href")) ?? "";
            var title = Js.PyStr(Js.First(a, "title", "name", "filename")) ?? url;
            if (t is "file" or "attachment" && url.Length > 0) url = asset(url, false);
            return indent + (url.Length > 0 ? $"[{t}: {title}]({url})" : "");
        }
        if (t is "math" or "mathBlock" or "katex" or "blockMath")
            return indent + "$$\n" + (Js.PyStr(Js.First(a, "latex", "value")) ?? Plain(n)) + "\n$$";

        // Unknown block: keep what content it has.
        UnknownTypes.Add(t ?? "None");
        if (items.Count > 0 && items.All(x => Js.Str(x, "type") is "text" or "hardBreak")) return Prefix(Inline(c), indent);
        return Blocks(c, indent);
    }

    private string BlocksOf(List<JsonElement> nodes)
    {
        var parts = new List<string>();
        foreach (var n in nodes)
        {
            var s = Block(n, "");
            if (!string.IsNullOrEmpty(s)) parts.Add(s);
        }
        return string.Join("\n\n", parts);
    }

    private string Atomic(JsonElement? d, string indent)
    {
        var kind = Js.PyStr(Js.Get(d, "kind"));
        if (kind == "divider") return indent + "---";
        if (kind is not null && VideoKinds.Contains(kind)) return Video(d, indent);
        var path = kind == "link"
            ? Js.PyStr(Js.First(d, "url")) ?? ""
            : Js.PyStr(Js.First(d, "image", "filepath", "url")) ?? "";
        if (path.Length > 0 && !path.StartsWith("http", StringComparison.Ordinal)) path = UploadsUrl + "/" + path.TrimStart('/');
        var title = Js.PyStr(Js.First(d, "title")) ?? kind ?? "file";
        if (path.Length == 0) return "";
        var local = kind == "link" ? path : PyCompat.Quote(asset(path, false), QuoteSafe);
        switch (kind)
        {
            case "image":
            {
                var s = indent + $"![{title.Replace("]", "")}]({local})";
                if (Js.Truthy(Js.Get(d, "caption"))) s += "\n\n" + indent + $"*{Js.PyStr(Js.Get(d, "caption"))}*";
                return s;
            }
            case "link":
            {
                var url = Js.PyStr(Js.First(d, "url")) ?? "";
                var desc = (Js.PyStr(Js.First(d, "description")) ?? "").Trim();
                var line = indent + $"🔗 [{title.Replace("]", "")}]({url})";
                if (desc.Length > 0) line += "  \n" + indent + $"*{(desc.Length > 300 ? desc[..300] + "…" : desc)}*";
                return line;
            }
            case "file": return indent + $"📎 [{title.Replace("]", "")}]({local})";
        }
        UnknownTypes.Add("atomic:" + (kind ?? "None"));
        return indent + $"[{kind ?? "None"}: {title}]({local})";
    }

    /// <summary>Uploaded video and audio files are downloaded; hosted players stay links.</summary>
    private string Video(JsonElement? d, string indent)
    {
        var title = (Js.PyStr(Js.First(d, "title", "name", "kind")) ?? "video").Replace("]", "");
        var fp = Js.PyStr(Js.First(d, "filepath", "file")) ?? "";
        var url = Js.PyStr(Js.First(d, "url", "src", "embed_url")) ?? "";
        if (fp.Length > 0 && !fp.StartsWith("http", StringComparison.Ordinal)) fp = UploadsUrl + "/" + fp.TrimStart('/');
        var target = fp.Length > 0 ? fp : url;
        if (target.Length == 0) return "";
        if (fp.Length > 0 || MediaFile().IsMatch(target))
            return indent + $"🎬 [{title}]({PyCompat.Quote(asset(target, true), QuoteSafe)})";
        return indent + $"🎬 [{title}]({target})";
    }

    private string Table(JsonElement n, string indent)
    {
        var rows = new List<List<string>>();
        foreach (var r in Js.Items(Js.Get(n, "content")))
        {
            var cells = new List<string>();
            foreach (var cell in Js.Items(Js.Get(r, "content")))
                cells.Add(Blocks(Js.Get(cell, "content")).Replace("\n\n", "<br>").Replace("\n", "<br>").Replace("|", "\\|").Trim());
            rows.Add(cells);
        }
        if (rows.Count == 0) return "";
        var width = rows.Max(r => r.Count);
        foreach (var r in rows) while (r.Count < width) r.Add("");
        var outLines = new List<string> { "| " + string.Join(" | ", rows[0]) + " |", "|" + string.Concat(Enumerable.Repeat("---|", width)) };
        outLines.AddRange(rows.Skip(1).Select(r => "| " + string.Join(" | ", r) + " |"));
        return string.Join("\n", outLines.Select(ln => indent + ln));
    }

    private static string Prefix(string text, string indent) =>
        indent.Length == 0 ? text : string.Join("\n", text.Split('\n').Select(ln => ln.Length > 0 ? indent + ln : ""));
}
