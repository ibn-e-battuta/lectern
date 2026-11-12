namespace Lectern.Services;

public sealed record SearchHit(string Title, string DocTitle, string DocGuid, string? PageGuid, string File, List<string> Snippets, int Score);

/// <summary>Full-text search over the downloaded Markdown pages. Every word must match.</summary>
public sealed class SearchService(DocsService docs)
{
    public async Task<List<SearchHit>> SearchAsync(string query, int limit = 60)
    {
        var terms = query.Split(' ', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries).Select(t => t.ToLowerInvariant()).ToArray();
        if (terms.Length == 0) return [];
        var hits = new List<SearchHit>();
        foreach (var info in await docs.InfoAsync())
        {
            if (info.FolderPath is not { } folder || info.State is not { } st) continue;
            var byPath = st.Pages.Where(p => p.Value.Path is not null).ToDictionary(p => p.Value.Path!, p => (p.Key, p.Value.Title));
            foreach (var file in Walk(folder))
            {
                string text;
                try { text = await File.ReadAllTextAsync(file); }
                catch (IOException) { continue; }
                var low = text.ToLowerInvariant();
                if (!terms.All(low.Contains)) continue;
                var snippets = new List<string>();
                foreach (var ln in text.Split('\n'))
                {
                    var l = ln.ToLowerInvariant();
                    if (ln.StartsWith("<!--", StringComparison.Ordinal) || !terms.Any(l.Contains)) continue;
                    var k = terms.Where(l.Contains).Min(t => l.IndexOf(t, StringComparison.Ordinal));
                    var s = ln.Trim();
                    if (s.Length > 200)
                    {
                        var from = Math.Max(0, k - 70);
                        s = (k > 70 ? "…" : "") + ln.Trim()[from..Math.Min(ln.Trim().Length, k + 130)] + "…";
                    }
                    snippets.Add(s);
                    if (snippets.Count >= 3) break;
                }
                var rel = PyCompat.RelPath(file, folder);
                var (pageGuid, title) = byPath.TryGetValue(rel, out var p) ? (p.Key, p.Title) : (null, Path.GetFileNameWithoutExtension(file));
                title ??= Path.GetFileNameWithoutExtension(file);
                var score = terms.Sum(t => Count(low, t)) + (terms.Any(t => title.Contains(t, StringComparison.OrdinalIgnoreCase)) ? 20 : 0);
                hits.Add(new SearchHit(title, info.Title, info.Doc.Guid, pageGuid, file, snippets, score));
            }
        }
        return hits.OrderByDescending(h => h.Score).Take(limit).ToList();
    }

    private static IEnumerable<string> Walk(string dir)
    {
        foreach (var f in Directory.EnumerateFiles(dir, "*.md").Order(StringComparer.Ordinal))
            if (Path.GetFileName(f) is not ("README.md" or "CHANGES.md")) yield return f;
        foreach (var d in Directory.EnumerateDirectories(dir).Order(StringComparer.Ordinal))
            if (Path.GetFileName(d) is { } n && !n.StartsWith('.') && n != "assets")
                foreach (var f in Walk(d)) yield return f;
    }

    private static int Count(string text, string term)
    {
        var n = 0;
        for (var i = text.IndexOf(term, StringComparison.Ordinal); i >= 0; i = text.IndexOf(term, i + term.Length, StringComparison.Ordinal)) n++;
        return n;
    }
}

public sealed record FoundLink(ShareLink Link, string Status, string? Title);

/// <summary>Finds bitdocs share links in a pasted course page (text or HTML) or a page Lectern can open.</summary>
public sealed class LinkScanner(DocsService docs, BitdocsClient client)
{
    public async Task<(List<FoundLink> Found, string? Error)> ScanAsync(string input, CancellationToken ct = default)
    {
        var text = input.Trim();
        // A single URL that isn't a share link: open the page and look in it.
        if (Uri.TryCreate(text, UriKind.Absolute, out var uri) && uri.Scheme is "http" or "https" && !text.Contains(' ') && ShareLink.Parse(text) is null)
        {
            try { text = await client.GetStringAsync(text, ct); }
            catch (Exception e) when (e is HttpRequestException or TaskCanceledException)
            {
                return ([], $"Couldn't open that page ({e.Message}). If it needs a login, open it in your browser, select all, copy, and paste it here.");
            }
        }
        var known = (await docs.ListAsync()).Select(d => d.Guid).ToHashSet();
        var seen = new HashSet<string>();
        var found = new List<FoundLink>();
        foreach (System.Text.RegularExpressions.Match m in ShareLink.BitdocsLink().Matches(text.Replace("&amp;", "&")))
        {
            if (ShareLink.Parse(m.Value) is not { } link || !seen.Add(link.Guid)) continue;
            if (known.Contains(link.Guid)) { found.Add(new FoundLink(link, "added", null)); continue; }
            var (title, _) = await client.TitleAsync(link, ct);
            found.Add(new FoundLink(link, title is null ? "missing" : "new", title));
        }
        return (found, null);
    }
}
