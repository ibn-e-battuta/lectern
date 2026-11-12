using Lectern.Data;
using Microsoft.EntityFrameworkCore;

namespace Lectern.Services;

/// <summary>A document and what its folder currently holds.</summary>
public sealed record DocInfo(Doc Doc, DocState? State, string? FolderPath)
{
    public string Title => State?.Title ?? Doc.Title ?? "Not synced yet";
    public int Pages => State?.LivePages().Count() ?? 0;
    public int Removed => State?.Pages.Values.Count(p => p.Removed is not null) ?? 0;
    /// <summary>The folder relative to the courses folder (where it is, or where it will go).</summary>
    public string Folder => Doc.OutputPath ?? Doc.Folder ?? "";
    public string Course => Folder.Split('/')[0] is { Length: > 0 } c ? c : "Other";
    public string? FirstPage => State?.LivePages().Select(p => p.Guid).FirstOrDefault();
    public string? PdfFile => FolderPath is not null && State?.PdfName is { } n && File.Exists(Path.Combine(FolderPath, n)) ? Path.Combine(FolderPath, n) : null;
    public string? ChangesFile => FolderPath is not null && File.Exists(Path.Combine(FolderPath, "CHANGES.md")) ? Path.Combine(FolderPath, "CHANGES.md") : null;
    public DateTime? LastSync => State?.LastSync is { } s && DateTime.TryParse(s, out var d) ? d : Doc.LastSyncAt?.ToLocalTime();
}

/// <summary>The list of documents to keep in sync.</summary>
public sealed class DocsService(IDbContextFactory<LecternDb> dbFactory, LecternOptions options, BitdocsClient client)
{
    public event Action? Changed;

    public void NotifyChanged() => Changed?.Invoke();

    public async Task<List<Doc>> ListAsync()
    {
        await using var db = await dbFactory.CreateDbContextAsync();
        return await db.Docs.AsNoTracking().OrderBy(d => d.Id).ToListAsync();
    }

    public async Task<List<DocInfo>> InfoAsync() => (await ListAsync()).Select(Info).ToList();

    public DocInfo Info(Doc d)
    {
        var folder = FolderOf(d);
        return new DocInfo(d, folder is null ? null : DocState.Load(folder), folder);
    }

    /// <summary>The absolute folder a document was last synced to, if it exists.</summary>
    public string? FolderOf(Doc d)
    {
        if (d.OutputPath is null) return null;
        var p = Path.IsPathRooted(d.OutputPath) ? d.OutputPath : Path.Combine(options.CoursesPath, d.OutputPath);
        return Directory.Exists(p) ? p : null;
    }

    public async Task<Doc?> GetAsync(string guid)
    {
        await using var db = await dbFactory.CreateDbContextAsync();
        return await db.Docs.AsNoTracking().FirstOrDefaultAsync(d => d.Guid == guid);
    }

    /// <summary>Checks a share link and adds it. Returns the new document, or an error to show.</summary>
    public async Task<(Doc? Doc, string? Error)> AddAsync(string url, string? folder)
    {
        var link = ShareLink.Parse(url);
        if (link is null) return (null, "That isn't a bitdocs share link (…bitdocs.ai/share/d/…).");
        if (!TryCleanFolder(folder, out var clean, out var folderError)) return (null, folderError);
        if (await GetAsync(link.Guid) is not null) return (null, "That document is already in your list.");
        var (title, error) = await client.TitleAsync(link);
        if (title is null) return (null, error is null ? "That link doesn't exist or isn't shared publicly." : $"Couldn't reach bitdocs: {error}");
        return (await InsertAsync(link, clean, title), null);
    }

    /// <summary>Adds links already checked (from a page scan). Skips ones already in the list.</summary>
    public async Task<List<Doc>> AddManyAsync(IEnumerable<(ShareLink Link, string? Title)> links, string? folder)
    {
        if (!TryCleanFolder(folder, out var clean, out var error)) throw new ArgumentException(error);
        var added = new List<Doc>();
        foreach (var (link, title) in links)
            if (await GetAsync(link.Guid) is null) added.Add(await InsertAsync(link, clean, title));
        return added;
    }

    private async Task<Doc> InsertAsync(ShareLink link, string? folder, string? title)
    {
        await using var db = await dbFactory.CreateDbContextAsync();
        var doc = new Doc { Host = link.Host, Guid = link.Guid, Folder = folder, Title = title };
        db.Docs.Add(doc);
        await db.SaveChangesAsync();
        Changed?.Invoke();
        return doc;
    }

    public async Task<string?> MoveAsync(string guid, string? folder)
    {
        if (!TryCleanFolder(folder, out var clean, out var error)) return error;
        if (clean is null) return "Choose a folder.";
        await using var db = await dbFactory.CreateDbContextAsync();
        var doc = await db.Docs.FirstOrDefaultAsync(d => d.Guid == guid);
        if (doc is null) return "Unknown document.";
        doc.Folder = clean;
        await db.SaveChangesAsync();
        Changed?.Invoke();
        return null;
    }

    /// <summary>Stops syncing a document. Its files stay.</summary>
    public async Task RemoveAsync(string guid)
    {
        await using var db = await dbFactory.CreateDbContextAsync();
        await db.Docs.Where(d => d.Guid == guid).ExecuteDeleteAsync();
        Changed?.Invoke();
    }

    public async Task SaveResultAsync(string guid, DocResult? result, string? error)
    {
        await using var db = await dbFactory.CreateDbContextAsync();
        var doc = await db.Docs.FirstOrDefaultAsync(d => d.Guid == guid);
        if (doc is null) return;
        if (result is not null)
        {
            doc.Title = result.Title;
            var rel = PyCompat.RelPath(result.Out, options.CoursesPath);
            doc.OutputPath = rel.StartsWith("..", StringComparison.Ordinal) ? result.Out : rel;
            doc.LastSyncAt = DateTime.UtcNow;
        }
        doc.LastError = error;
        await db.SaveChangesAsync();
    }

    /// <summary>A folder inside the courses folder, written with forward slashes. Null means "named after the document".</summary>
    public static bool TryCleanFolder(string? folder, out string? clean, out string? error)
    {
        clean = null;
        error = null;
        var f = (folder ?? "").Replace('\\', '/').Trim().Trim('/');
        if (f.Length == 0) return true;
        var parts = f.Split('/', StringSplitOptions.RemoveEmptyEntries).Select(p => p.Trim()).ToArray();
        if (parts.Any(p => p is ".." or "." || p.StartsWith('.')))
        {
            error = "The folder must be inside your courses folder, without \"..\" or hidden folders.";
            return false;
        }
        clean = string.Join('/', parts);
        return true;
    }

    /// <summary>Course folders and their sub-folders, for the folder box.</summary>
    public List<string> FolderSuggestions()
    {
        var root = options.CoursesPath;
        var list = new List<string>();
        try
        {
            foreach (var a in Directory.EnumerateDirectories(root).Select(Path.GetFileName).Order(StringComparer.OrdinalIgnoreCase))
            {
                if (a is null || a.StartsWith('.')) continue;
                list.Add(a);
                foreach (var b in Directory.EnumerateDirectories(Path.Combine(root, a)).Select(Path.GetFileName).Order(StringComparer.OrdinalIgnoreCase))
                    if (b is not null && !b.StartsWith('.') && b is not ("assets" or "Docs")) list.Add(a + "/" + b);
            }
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
        return list.Take(400).ToList();
    }
}
