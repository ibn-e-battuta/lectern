using System.Text.Json;

namespace Lectern.Services;

/// <summary>A page of a document, for the reader, search and PDF.</summary>
public sealed record ReaderPage(string Guid, string Title, string Path, int Depth);

/// <summary>A synced document as the reader shows it.</summary>
public sealed record ReaderDoc(DocInfo Info, List<ReaderPage> Pages)
{
    public string Guid => Info.Doc.Guid;
    public string Title => Info.Title;
}

/// <summary>Reads synced documents back for the in-app reader: outlines, pages as HTML, and file links.</summary>
public sealed class DocReader(DocsService docs, LecternOptions options)
{
    public async Task<List<ReaderDoc>> DocsAsync() =>
        (await docs.InfoAsync()).Where(i => i.State is not null && i.FolderPath is not null).Select(ToReader).ToList();

    public static ReaderDoc ToReader(DocInfo info) =>
        new(info, info.State!.LivePages().Select(p => new ReaderPage(p.Guid, p.Page.Title ?? p.Guid, p.Page.Path!, DocState.Depth(p.Page.Path!))).ToList());

    /// <summary>The page's original JSON, saved next to the Markdown during sync.</summary>
    public static JsonDocument? Raw(DocInfo info, string pageGuid)
    {
        if (info.FolderPath is null || pageGuid.IndexOfAny(['/', '\\', '.']) >= 0) return null;
        var p = Path.Combine(info.FolderPath, ".raw", pageGuid + ".json");
        if (!File.Exists(p)) return null;
        try { return JsonDocument.Parse(File.ReadAllText(p)); }
        catch (JsonException) { return null; }
    }

    /// <summary>The page as HTML for the reader. Images and files load through <c>files/</c>; page links stay in the reader.</summary>
    public string RenderHtml(ReaderDoc doc, string pageGuid, IReadOnlyDictionary<string, string> pageToDoc)
    {
        using var raw = Raw(doc.Info, pageGuid);
        if (raw is null) return "<p class=\"muted\">This page hasn't been downloaded yet. Run a sync.</p>";
        var origin = Origin(doc.Info.State);
        var renderer = new HtmlRenderer(
            url => doc.Info.State!.Assets.TryGetValue(url, out var rel) ? FileUrl(Path.Combine(doc.Info.FolderPath!, rel)) : url,
            href =>
            {
                var m = DocSync.PageLink().Match(href);
                if (m.Success && pageToDoc.TryGetValue(m.Groups[1].Value, out var d)) return ($"read/{d}/{m.Groups[1].Value}", false);
                if (href.StartsWith('/')) return (origin + href, true);
                return (href, !href.StartsWith('#'));
            });
        return renderer.Render(Js.Get(raw.RootElement, "body_json"));
    }

    /// <summary>https://&lt;org&gt;.bitdocs.ai for a document, from its share link.</summary>
    public static string Origin(DocState? st)
    {
        var src = st?.Source ?? "";
        var i = src.IndexOf("://", StringComparison.Ordinal);
        if (i < 0) return "https://bitdocs.ai";
        var slash = src.IndexOf('/', i + 3);
        return slash < 0 ? src : src[..slash];
    }

    /// <summary>Link to a file in the courses folder, served by Lectern.</summary>
    public string FileUrl(string absolutePath) => "files/" + PyCompat.Quote(PyCompat.RelPath(absolutePath, options.CoursesPath));
}
