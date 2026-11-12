using System.Net;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace Lectern.Services;

/// <summary>A document's share link: <c>https://&lt;org&gt;.bitdocs.ai/share/d/&lt;id&gt;</c>.</summary>
public sealed partial record ShareLink(string Host, string Guid)
{
    public string Url => $"https://{Host}/share/d/{Guid}";

    [GeneratedRegex(@"https?://([^/\s]+)/share/d/([A-Za-z0-9]+)")]
    private static partial Regex Pattern();

    /// <summary>Share links on bitdocs.ai (what a page scan looks for).</summary>
    [GeneratedRegex(@"https?://[A-Za-z0-9.-]+\.bitdocs\.ai/share/d/[A-Za-z0-9]+")]
    public static partial Regex BitdocsLink();

    public static ShareLink? Parse(string? text) =>
        text is not null && Pattern().Match(text) is { Success: true } m ? new ShareLink(m.Groups[1].Value, m.Groups[2].Value) : null;
}

/// <summary>Reads documents through the public share API that bitdocs' own pages use.</summary>
public sealed class BitdocsClient(HttpClient http)
{
    /// <summary>Pause between requests, to be polite.</summary>
    public static TimeSpan Delay { get; set; } = TimeSpan.FromMilliseconds(400);

    public static string UserAgent => $"Mozilla/5.0 (compatible; Lectern/{AppInfo.Version}; +https://github.com/ibn-e-battuta/lectern)";

    /// <summary>One page: title, tree of child pages and body (ProseMirror JSON).</summary>
    public async Task<JsonDocument> PageAsync(string host, string guid, CancellationToken ct = default)
    {
        await Task.Delay(Delay, ct);
        var text = await GetStringAsync($"https://{host}/api/sharefront/doc/{guid}", ct);
        return JsonDocument.Parse(text);
    }

    /// <summary>The document's title, or null when the link doesn't exist or isn't public.</summary>
    public async Task<(string? Title, string? Error)> TitleAsync(ShareLink link, CancellationToken ct = default)
    {
        try
        {
            using var doc = await PageAsync(link.Host, link.Guid, ct);
            return (Js.Str(doc.RootElement, "title") ?? link.Guid, null);
        }
        catch (HttpRequestException e) when (e.StatusCode is HttpStatusCode.NotFound or HttpStatusCode.Forbidden or HttpStatusCode.Unauthorized)
        {
            return (null, null);
        }
        catch (Exception e) when (e is HttpRequestException or TaskCanceledException or JsonException)
        {
            return (null, e.Message);
        }
    }

    public async Task<string> GetStringAsync(string url, CancellationToken ct = default)
    {
        for (var attempt = 0; ; attempt++)
        {
            try
            {
                using var req = new HttpRequestMessage(HttpMethod.Get, url);
                req.Headers.TryAddWithoutValidation("User-Agent", UserAgent);
                req.Headers.TryAddWithoutValidation("Accept", "*/*");
                using var res = await http.SendAsync(req, ct);
                res.EnsureSuccessStatusCode();
                return await res.Content.ReadAsStringAsync(ct);
            }
            catch (Exception e) when (attempt < 2 && Retryable(e))
            {
                await Task.Delay(TimeSpan.FromSeconds(2 * (attempt + 1)), ct);
            }
        }
    }

    /// <summary>Streams a file to disk (works for large videos) and renames it into place at the end.</summary>
    public async Task DownloadAsync(string url, string dest, CancellationToken ct = default)
    {
        var part = dest + ".part";
        for (var attempt = 0; ; attempt++)
        {
            try
            {
                using var req = new HttpRequestMessage(HttpMethod.Get, url);
                req.Headers.TryAddWithoutValidation("User-Agent", UserAgent);
                using var res = await http.SendAsync(req, HttpCompletionOption.ResponseHeadersRead, ct);
                res.EnsureSuccessStatusCode();
                await using (var file = File.Create(part))
                await using (var body = await res.Content.ReadAsStreamAsync(ct))
                    await body.CopyToAsync(file, 1 << 20, ct);
                File.Move(part, dest, overwrite: true);
                return;
            }
            catch (Exception e) when (Retryable(e) || e is IOException)
            {
                if (File.Exists(part)) File.Delete(part);
                if (attempt >= 2 || ct.IsCancellationRequested) throw;
                await Task.Delay(TimeSpan.FromSeconds(2 * (attempt + 1)), ct);
            }
        }
    }

    private static bool Retryable(Exception e) =>
        e is TaskCanceledException { InnerException: TimeoutException }
        || e is HttpRequestException { StatusCode: null or >= HttpStatusCode.InternalServerError or HttpStatusCode.TooManyRequests };
}
