using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace Lectern.Services;

/// <summary>What one document's sync did.</summary>
public sealed record DocResult(string Guid, string Title, string Out, List<DocChange> Changes, Dictionary<string, List<string>> Diffs, HashSet<string> UnknownTypes);

/// <summary>One line of a document's change log. <see cref="Path"/> is relative to the document folder.</summary>
public sealed record DocChange(string Kind, string Path);

/// <summary>
/// Mirrors one bitdocs document into a folder: every page becomes a numbered Markdown file, with its images and
/// attachments in <c>assets/</c> and links between pages rewritten to the local files.
/// </summary>
public sealed partial class DocSync
{
    /// <summary>Bump to re-render every page after converter changes.</summary>
    public const int ConverterVersion = 6;

    private static readonly JsonElement EmptyBody = JsonDocument.Parse("""{"type": "doc", "content": []}""").RootElement;

    private readonly BitdocsClient _client;
    private readonly ActivityLog _log;
    private readonly string _root;
    private readonly bool _videos;
    private readonly string _host;
    private readonly string _rootGuid;
    private readonly string? _folder;
    private readonly bool _shared;
    private readonly string? _previousFolder;

    private string _out = "";
    private DocState _state = new();
    private readonly HashSet<string> _seen = [];
    private readonly HashSet<string> _dirsToPrune = [];
    private readonly List<DocChange> _changes = [];
    private readonly Dictionary<string, List<string>> _diffs = [];
    private readonly HashSet<string> _unknown = [];
    private readonly List<Job> _jobs = [];
    private readonly List<JsonDocument> _owned = [];
    private Dictionary<string, string> _linkmap = [];
    private bool _force;

    private sealed record Job(JsonElement Page, string Dir, string Base, JsonElement Node, string Title, bool IsRoot);

    /// <param name="root">The courses folder; relative document folders are inside it.</param>
    /// <param name="folder">Where the user wants the document, relative to <paramref name="root"/>; null for a folder named after it.</param>
    /// <param name="shared">Several documents use this folder, so each gets a sub-folder named after its title.</param>
    /// <param name="previousFolder">Where this document was synced last time, to move it if the folder changed.</param>
    public DocSync(BitdocsClient client, ActivityLog log, string root, bool downloadVideos,
        ShareLink link, string? folder, bool shared, string? previousFolder)
    {
        _client = client;
        _log = log;
        _root = root;
        _videos = downloadVideos;
        _host = link.Host;
        _rootGuid = link.Guid;
        _folder = folder;
        _shared = shared;
        _previousFolder = previousFolder;
    }

    private string SourceUrl(string guid) => $"https://{_host}/share/d/{guid}";

    public async Task<DocResult> RunAsync(CancellationToken ct = default)
    {
        try
        {
            var root = (await FetchAsync(_rootGuid, ct)).RootElement;
            var title = FullTitle(root, root);
            if (title.Length == 0) title = _rootGuid;
            if (_folder is not null)
            {
                _out = Path.IsPathRooted(_folder) ? _folder : Path.Combine(_root, _folder);
                if (_shared) _out = Path.Combine(_out, PyCompat.SafeName(title));
            }
            else _out = Path.Combine(_root, PyCompat.SafeName(title));
            _out = Path.GetFullPath(_out);
            Directory.CreateDirectory(_out);
            MoveFromPreviousFolder();

            _state = DocState.Load(_out) ?? new DocState();
            _state.AssetFiles ??= _state.Assets.Values.Distinct().Order(StringComparer.Ordinal).ToList();
            _state.Title = title;
            _state.Source = SourceUrl(_rootGuid);

            _log.Info($"Syncing '{title}' -> {Rel(_out)}");
            // Phase 1: fetch every page and decide where it goes.
            _jobs.Add(new Job(root, _out, "00 - " + PyCompat.SafeName(title), root, title, true));
            await WalkAsync(Js.Get(root, "tree"), _out, ct);
            _linkmap = [];
            foreach (var j in _jobs)
                if (!(j.IsRoot && !HasContent(Js.Get(j.Page, "body_json") ?? EmptyBody)))
                    _linkmap[Js.Str(j.Page, "guid") ?? Js.Str(j.Node, "guid") ?? ""] = Path.Combine(j.Dir, j.Base + ".md");
            var lmHash = PyCompat.Sha256(PyCompat.Dumps(_linkmap.ToDictionary(p => p.Key, p => PyCompat.RelPath(p.Value, _out))));
            _force = _state.LinkmapHash != lmHash;

            // Phase 2: write. If it fails partway, keep what was done so the next run doesn't report those pages as new again.
            try
            {
                foreach (var j in _jobs) await WritePageAsync(j, ct);
            }
            catch
            {
                _state.Save(_out);
                WriteChangelog();
                throw;
            }
            _state.LinkmapHash = lmHash;

            // Pages that vanished online: report them, keep the files.
            foreach (var (guid, info) in _state.Pages)
                if (!_seen.Contains(guid) && info.Removed is null)
                {
                    info.Removed = Now();
                    _changes.Add(new DocChange("removed online (local copy kept)", info.Path ?? ""));
                }

            _state.LastSync = Now();
            CleanupUnusedAssets();
            _state.Save(_out);
            WriteChangelog();
            WriteIndex(root);
            foreach (var d in _dirsToPrune.OrderByDescending(d => d.Length)) PruneEmpty(d, _out);
            return new DocResult(_rootGuid, title, _out, _changes, _diffs, _unknown);
        }
        finally
        {
            foreach (var d in _owned) d.Dispose();
        }
    }

    private async Task<JsonDocument> FetchAsync(string guid, CancellationToken ct)
    {
        var doc = await _client.PageAsync(_host, guid, ct);
        _owned.Add(doc);
        return doc;
    }

    private static string Now() => DateTime.Now.ToString("yyyy-MM-ddTHH:mm:ss");

    private string Rel(string path) => PyCompat.RelPath(path, _root);

    // ---------- moving a document when its folder changes ----------

    private DocState? Owns(string folder)
    {
        var st = DocState.Load(folder);
        return st is not null && (st.Source ?? "").TrimEnd('/').EndsWith("/" + _rootGuid, StringComparison.Ordinal) ? st : null;
    }

    private void MoveFromPreviousFolder()
    {
        var candidates = new List<string?> { _previousFolder };
        if (_shared) candidates.Add(Path.GetDirectoryName(_out)); // it used to sit directly in the shared folder
        foreach (var old in candidates)
        {
            if (old is null || Path.GetFullPath(old).TrimEnd('/') == _out.TrimEnd('/')) continue;
            var st = Owns(old);
            if (st is null) continue;
            if (File.Exists(Path.Combine(_out, DocState.FileName)))
            {
                _log.Warn($"'{Rel(_out)}' also has an older copy in {Rel(old)} (not moved, both exist)");
                return;
            }
            var items = st.Pages.Values.Where(v => v.Path is not null).Select(v => v.Path!)
                .Concat(st.AssetFiles ?? []).Concat(st.Assets.Values)
                .Concat([".raw", "README.md", "CHANGES.md", DocState.FileName, PyCompat.SafeName(st.Title ?? "") + ".pdf", st.PdfName ?? ""])
                .Where(x => x.Length > 0).Distinct().Order(StringComparer.Ordinal);
            var moved = 0;
            var dirs = new HashSet<string>();
            foreach (var rel in items)
            {
                var src = Path.Combine(old, rel);
                var dst = Path.Combine(_out, rel);
                if (!(File.Exists(src) || Directory.Exists(src)) || File.Exists(dst) || Directory.Exists(dst)) continue;
                Directory.CreateDirectory(Path.GetDirectoryName(dst)!);
                if (Directory.Exists(src)) Directory.Move(src, dst); else File.Move(src, dst);
                moved++;
                dirs.Add(Path.GetDirectoryName(src)!);
            }
            var outInsideOld = _out.StartsWith(old.TrimEnd('/') + "/", StringComparison.Ordinal);
            foreach (var d in dirs.OrderByDescending(d => d.Length)) PruneEmpty(d, old, includeStop: !outInsideOld);
            _log.Info($"Moved {moved} item(s) of this doc from {Rel(old)}");
            _changes.Add(new DocChange("moved folder", $"{Rel(old)} -> {Rel(_out)}"));
            return;
        }
    }

    // ---------- pages ----------

    private async Task WalkAsync(JsonElement? node, string dir, CancellationToken ct)
    {
        var children = Js.Items(Js.Get(node, "children"))
            .Select((c, i) => (c, i))
            .OrderBy(x => Js.Get(x.c, "sortorder") is { ValueKind: JsonValueKind.Number } n ? n.GetDouble() : 0)
            .ThenBy(x => x.i)
            .Select(x => x.c).ToList();
        for (var i = 0; i < children.Count; i++)
        {
            var child = children[i];
            var guid = Js.Str(child, "guid") ?? "";
            var page = (await FetchAsync(guid, ct)).RootElement;
            var fullTitle = FullTitle(page, child);
            var baseName = $"{i + 1:00} - {PyCompat.SafeName(fullTitle)}";
            if (Js.Truthy(Js.Get(child, "children")))
            {
                var sub = Path.Combine(dir, baseName);
                Directory.CreateDirectory(sub);
                _jobs.Add(new Job(page, sub, "00 - " + PyCompat.SafeName(fullTitle), child, fullTitle, false));
                await WalkAsync(child, sub, ct);
            }
            else _jobs.Add(new Job(page, dir, baseName, child, fullTitle, false));
        }
    }

    /// <summary>Titles in the tree are cut short with "…"; the page's first heading has the whole title.</summary>
    public static string FullTitle(JsonElement page, JsonElement child)
    {
        var t = Js.PyStr(Js.First(page, "title")) ?? Js.PyStr(Js.First(child, "title")) ?? "";
        foreach (var n in Js.Items(Js.Get(Js.Get(page, "body_json"), "content")).Take(3))
        {
            if (Js.Str(n, "type") != "heading") continue;
            var h = string.Concat(Js.Items(Js.Get(n, "content")).Where(x => Js.Str(x, "type") == "text").Select(x => Js.Str(x, "text") ?? "")).Trim();
            if (h.Length > 0 && (t.EndsWith("...", StringComparison.Ordinal) || t.EndsWith('…') || t.Length == 0)) return h;
            break;
        }
        return t;
    }

    private static bool HasContent(JsonElement body) =>
        Js.Items(Js.Get(body, "content")).Any(n => Js.Truthy(Js.Get(n, "content")) || Js.Str(n, "type") != "paragraph");

    [GeneratedRegex(@"^(?:https?://[^/]+)?/(?:a/docs|share/d)/([A-Za-z0-9]+)(#.*)?$")]
    public static partial Regex PageLink();

    private async Task WritePageAsync(Job job, CancellationToken ct)
    {
        var guid = Js.Str(job.Page, "guid") ?? Js.Str(job.Node, "guid") ?? "";
        _seen.Add(guid);
        var body = Js.Get(job.Page, "body_json") ?? EmptyBody;
        var hash = PyCompat.Hash(body);
        var mdPath = Path.Combine(job.Dir, job.Base + ".md");
        var rel = PyCompat.RelPath(mdPath, _out);
        _state.Pages.TryGetValue(guid, out var prev);

        // A near-empty root page isn't written.
        if (job.IsRoot && !HasContent(body))
        {
            _state.Pages[guid] = new PageState { Hash = hash, Path = null, Title = Js.Str(job.Page, "title") };
            return;
        }

        var sameContent = prev is not null && prev.Hash == hash;
        if (sameContent && prev!.Path == rel && File.Exists(mdPath) && prev.Cv == ConverterVersion && !_force
            && (prev.Assets ?? []).All(a => File.Exists(Path.Combine(_out, a))))
        {
            prev.Removed = null;
            return;
        }
        var rerenderOnly = sameContent && prev!.Path == rel;

        var assetsDir = Path.Combine(job.Dir, "assets");
        var pageAssets = new List<string>();

        string? oldText = null;
        if (prev?.Path is { } prevPath && !rerenderOnly && File.Exists(Path.Combine(_out, prevPath)))
            oldText = await File.ReadAllTextAsync(Path.Combine(_out, prevPath), ct);

        var conv = new MarkdownConverter(
            (url, video) => video && !_videos ? url : Asset(url, assetsDir, job.Dir, pageAssets, ct),
            href =>
            {
                var m = PageLink().Match(href);
                if (m.Success && _linkmap.TryGetValue(m.Groups[1].Value, out var target))
                    return PyCompat.RelPath(target, job.Dir) + m.Groups[2].Value;
                return href.StartsWith('/') ? $"https://{_host}{href}" : href;
            });
        var md = conv.Blocks(Js.Get(body, "content"));
        _unknown.UnionWith(conv.UnknownTypes);

        var title = job.Title.Length > 0 ? job.Title : Js.Str(job.Page, "title") ?? guid;
        if (!md.TrimStart().StartsWith("# ", StringComparison.Ordinal)) md = $"# {title}\n\n{md}";
        var header = $"<!-- source: {SourceUrl(guid)} | synced: {DateTime.Now:yyyy-MM-dd HH:mm} -->\n\n";
        var newText = header + md.TrimEnd() + "\n";
        await File.WriteAllTextAsync(mdPath, newText, ct);
        if (oldText is not null && TextDiff.Unified(oldText, newText) is { } diff) _diffs[rel] = diff;

        // The original JSON is kept for the reader, the PDF and future re-conversion.
        var rawDir = Path.Combine(_out, ".raw");
        Directory.CreateDirectory(rawDir);
        await File.WriteAllTextAsync(Path.Combine(rawDir, guid + ".json"), job.Page.GetRawText(), ct);

        if (prev?.Path is { } oldPath && oldPath != rel)
        {
            var old = Path.Combine(_out, oldPath);
            if (File.Exists(old)) File.Delete(old); // our own file at its old name
            _dirsToPrune.Add(Path.GetDirectoryName(old)!);
            _changes.Add(new DocChange("renamed/moved", $"{oldPath} -> {rel}"));
        }
        else if (rerenderOnly) { } // only the converter changed: not a content update
        else _changes.Add(new DocChange(prev is not null ? "updated" : "new", rel));

        _state.Pages[guid] = new PageState
        {
            Hash = hash, Path = rel, Title = title, Cv = ConverterVersion,
            Assets = pageAssets.Distinct().Order(StringComparer.Ordinal).ToList(),
        };
    }

    /// <summary>
    /// The local path for an image or file, downloading it if needed. A failed download keeps the online URL.
    /// Syncs run on a background task, so waiting for the download here blocks nothing else.
    /// </summary>
    private string Asset(string url, string assetsDir, string mdDir, List<string> pageAssets, CancellationToken ct)
    {
        if (url.StartsWith('/')) url = $"https://{_host}{url}";
        if (!url.StartsWith("http", StringComparison.Ordinal)) return url;
        var known = _state.Assets.TryGetValue(url, out var k) ? Path.Combine(_out, k) : null;
        var pathPart = PyCompat.UrlPath(url);
        var ext = PyCompat.SplitExt(pathPart).Ext;
        ext = ext.Length > 6 ? ext[..6] : ext;
        if (ext.Length == 0) ext = ".bin";
        var baseName = pathPart[(pathPart.LastIndexOf('/') + 1)..];
        var stem = PyCompat.SafeName(PyCompat.SplitExt(baseName).Stem, 50);
        var name = $"{stem}-{Convert.ToHexStringLower(SHA1.HashData(Encoding.UTF8.GetBytes(url)))[..8]}{ext}";
        Directory.CreateDirectory(assetsDir);
        var dest = Path.Combine(assetsDir, name);
        try
        {
            if (!File.Exists(dest))
            {
                if (known is not null && File.Exists(known)) File.Copy(known, dest); // page moved: bring its asset along
                else _client.DownloadAsync(url, dest, ct).GetAwaiter().GetResult();
            }
            var rel = PyCompat.RelPath(dest, _out);
            _state.Assets[url] = rel;
            if (!_state.AssetFiles!.Contains(rel)) _state.AssetFiles.Add(rel);
            pageAssets.Add(rel);
            return PyCompat.RelPath(dest, mdDir);
        }
        catch (Exception e) when (e is HttpRequestException or IOException or UnauthorizedAccessException
                                      || e is TaskCanceledException && !ct.IsCancellationRequested)
        {
            _log.Warn($"asset download failed ({e.Message}): {url}");
            return url;
        }
    }

    /// <summary>Deletes downloaded files that no page uses any more.</summary>
    private void CleanupUnusedAssets()
    {
        var pages = _state.Pages.Values;
        if (pages.Any(v => v.Path is not null && v.Assets is null)) return; // not every page was written by this version yet
        var used = pages.SelectMany(v => v.Assets ?? []).ToHashSet();
        var keep = new List<string>();
        foreach (var rel in _state.AssetFiles ?? [])
        {
            if (used.Contains(rel)) { keep.Add(rel); continue; }
            var p = Path.Combine(_out, rel);
            if (File.Exists(p)) File.Delete(p);
            _dirsToPrune.Add(Path.GetDirectoryName(p)!);
        }
        _state.AssetFiles = keep.Distinct().Order(StringComparer.Ordinal).ToList();
        _state.Assets = _state.Assets.Where(p => used.Contains(p.Value)).ToDictionary(p => p.Key, p => p.Value);
    }

    /// <summary>Removes <paramref name="dir"/> and its empty parents, up to (not including) <paramref name="stop"/>.</summary>
    public static void PruneEmpty(string dir, string stop, bool includeStop = false)
    {
        stop = Path.GetFullPath(stop).TrimEnd('/');
        var d = Path.GetFullPath(dir).TrimEnd('/');
        while (d.StartsWith(stop, StringComparison.Ordinal) && (d != stop || includeStop) && Directory.Exists(d))
        {
            if (Directory.EnumerateFileSystemEntries(d).Any(x => Path.GetFileName(x) != ".DS_Store")) return;
            var ds = Path.Combine(d, ".DS_Store");
            if (File.Exists(ds)) File.Delete(ds);
            Directory.Delete(d);
            if (d == stop) return;
            d = Path.GetDirectoryName(d)!;
        }
    }

    // ---------- CHANGES.md and README.md ----------

    private void WriteChangelog()
    {
        if (_changes.Count == 0) return;
        var p = Path.Combine(_out, "CHANGES.md");
        var old = File.Exists(p) ? ChangesHeading().Replace(File.ReadAllText(p), "", 1) : "";
        var lines = new List<string> { $"## {DateTime.Now:yyyy-MM-dd HH:mm}" };
        foreach (var c in _changes)
        {
            lines.Add($"- **{c.Kind}**: {c.Path}");
            lines.AddRange(TextDiff.Block(_diffs.GetValueOrDefault(c.Path), "  "));
        }
        File.WriteAllText(p, "# Changes\n\n" + string.Join("\n", lines) + "\n\n" + old);
    }

    [GeneratedRegex(@"^# Changes\n+")]
    private static partial Regex ChangesHeading();

    /// <summary>README.md: a table of contents of the local copy.</summary>
    private void WriteIndex(JsonElement root)
    {
        var lines = new List<string>
        {
            $"# {_state.Title ?? Js.Str(root, "title") ?? ""}", "",
            $"Source: {SourceUrl(_rootGuid)}  ",
            $"Last sync: {_state.LastSync}", "",
        };
        var pages = _state.Pages.Values.Where(v => v.Path is not null && v.Removed is null)
            .Select(v => (Path: v.Path!, v.Title)).OrderBy(p => p.Path, StringComparer.Ordinal).ThenBy(p => p.Title, StringComparer.Ordinal);
        foreach (var (path, title) in pages)
            lines.Add($"{new string(' ', 2 * DocState.Depth(path))}- [{(string.IsNullOrEmpty(title) ? path : title)}]({PyCompat.Quote(path)})");
        File.WriteAllText(Path.Combine(_out, "README.md"), string.Join("\n", lines) + "\n");
    }
}
