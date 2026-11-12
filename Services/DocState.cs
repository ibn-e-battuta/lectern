using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace Lectern.Services;

/// <summary>
/// <c>.sync-state.json</c> in each document folder: what was downloaded, so the next sync rewrites only what changed.
/// Lyceum also uses this file to recognise a Lectern document folder.
/// </summary>
public sealed class DocState
{
    public const string FileName = ".sync-state.json";

    [JsonPropertyName("pages")] public Dictionary<string, PageState> Pages { get; set; } = [];
    /// <summary>Source URL → file path (relative to the document folder).</summary>
    [JsonPropertyName("assets")] public Dictionary<string, string> Assets { get; set; } = [];
    [JsonPropertyName("asset_files")] public List<string>? AssetFiles { get; set; }
    [JsonPropertyName("title")] public string? Title { get; set; }
    [JsonPropertyName("source")] public string? Source { get; set; }
    [JsonPropertyName("linkmap_hash")] public string? LinkmapHash { get; set; }
    [JsonPropertyName("last_sync")] public string? LastSync { get; set; }
    [JsonPropertyName("pdf_name")] public string? PdfName { get; set; }

    private static readonly JsonSerializerOptions Options = new()
    {
        WriteIndented = true,
        IndentSize = 1,
        Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };

    public static DocState? Load(string folder)
    {
        var path = Path.Combine(folder, FileName);
        if (!File.Exists(path)) return null;
        try { return JsonSerializer.Deserialize<DocState>(File.ReadAllText(path), Options); }
        catch (JsonException) { return null; }
    }

    public void Save(string folder)
    {
        var path = Path.Combine(folder, FileName);
        File.WriteAllText(path + ".tmp", JsonSerializer.Serialize(this, Options));
        File.Move(path + ".tmp", path, overwrite: true);
    }

    /// <summary>Pages that exist locally, in reading order.</summary>
    public IEnumerable<(string Guid, PageState Page)> LivePages() =>
        Pages.Where(p => p.Value.Path is not null && p.Value.Removed is null)
            .Select(p => (p.Key, p.Value))
            .OrderBy(p => SortKey(p.Value.Path!), StringComparer.Ordinal);

    /// <summary>A folder's "00 - " page comes before its siblings' sub-pages.</summary>
    public static string SortKey(string path) => path.Replace("/00 - ", "/!");

    /// <summary>How deep a page sits in the outline (a folder's own page is at the folder's level).</summary>
    public static int Depth(string path) =>
        Math.Max(0, path.Count(c => c == '/') - (Path.GetFileName(path).StartsWith("00 - ", StringComparison.Ordinal) ? 1 : 0));
}

public sealed class PageState
{
    [JsonPropertyName("hash")] public string? Hash { get; set; }
    [JsonPropertyName("path")] public string? Path { get; set; }
    [JsonPropertyName("title")] public string? Title { get; set; }
    /// <summary>Converter version the page was written with.</summary>
    [JsonPropertyName("cv")] public int? Cv { get; set; }
    [JsonPropertyName("assets")] public List<string>? Assets { get; set; }
    /// <summary>When the page disappeared online (the local copy is kept).</summary>
    [JsonPropertyName("removed")] public string? Removed { get; set; }
}
