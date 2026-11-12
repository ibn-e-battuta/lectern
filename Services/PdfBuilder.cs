using System.Diagnostics;
using System.Net;
using System.Text;

namespace Lectern.Services;

/// <summary>One PDF per document (cover, contents, every page), printed by headless Chromium.</summary>
public sealed class PdfBuilder(LecternOptions options, ActivityLog log)
{
    private static readonly string[] Candidates =
    [
        "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/usr/bin/microsoft-edge",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        @"C:\Program Files\Google\Chrome\Application\chrome.exe",
        @"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ];

    public string? Chromium => !string.IsNullOrEmpty(options.Chromium) && File.Exists(options.Chromium)
        ? options.Chromium
        : Candidates.FirstOrDefault(File.Exists);

    /// <summary>Builds the PDF when it's missing or the document changed. Returns its path, or null.</summary>
    public async Task<string?> BuildAsync(DocInfo info, bool force, CancellationToken ct)
    {
        if (info.State is not { } st || info.FolderPath is not { } folder) return null;
        var title = st.Title ?? info.Doc.Guid;
        var pdf = Path.Combine(folder, PyCompat.SafeName(title) + ".pdf");
        if (st.PdfName is { } oldName && oldName != Path.GetFileName(pdf) && File.Exists(Path.Combine(folder, oldName)))
        {
            File.Delete(Path.Combine(folder, oldName)); // the document was renamed: drop the PDF with the old name
            force = true;
        }
        if (File.Exists(pdf) && !force)
        {
            Remember(folder, Path.GetFileName(pdf));
            return pdf;
        }
        if (Chromium is not { } chrome)
        {
            log.Warn($"no Chrome, Chromium or Edge found; skipping the PDF for '{title}'");
            return null;
        }

        var doc = DocReader.ToReader(info);
        var ids = doc.Pages.Select(p => p.Guid).ToHashSet();
        var origin = DocReader.Origin(st);
        var renderer = new HtmlRenderer(
            url => st.Assets.TryGetValue(url, out var rel) ? new Uri(Path.Combine(folder, rel)).AbsoluteUri : url,
            href =>
            {
                var m = DocSync.PageLink().Match(href);
                if (m.Success && ids.Contains(m.Groups[1].Value)) return ("#p-" + m.Groups[1].Value, false);
                return (href.StartsWith('/') ? origin + href : href, true);
            });

        var toc = new StringBuilder();
        var body = new StringBuilder();
        foreach (var p in doc.Pages)
        {
            toc.Append($"<li style=\"margin-left:{p.Depth * 1.2:0.#}em\"><a href=\"#p-{p.Guid}\">{WebUtility.HtmlEncode(p.Title)}</a></li>");
            using var raw = DocReader.Raw(info, p.Guid);
            body.Append($"<section class=\"page\" id=\"p-{p.Guid}\">{renderer.Render(raw is null ? null : Js.Get(raw.RootElement, "body_json"))}</section>");
        }
        var html = DocCss.PdfTemplate
            .Replace("{title}", WebUtility.HtmlEncode(title))
            .Replace("{synced}", WebUtility.HtmlEncode(st.LastSync ?? ""))
            .Replace("{source}", WebUtility.HtmlEncode(st.Source ?? ""))
            .Replace("{toc}", toc.ToString())
            .Replace("{body}", body.ToString());

        var tmp = Directory.CreateTempSubdirectory("lectern-pdf-").FullName;
        try
        {
            var src = Path.Combine(tmp, "doc.html");
            await File.WriteAllTextAsync(src, html, ct);
            var outPdf = Path.Combine(tmp, "out.pdf");
            var psi = new ProcessStartInfo(chrome) { RedirectStandardError = true, RedirectStandardOutput = true, UseShellExecute = false };
            if (OperatingSystem.IsLinux()) { psi.ArgumentList.Add("--no-sandbox"); psi.ArgumentList.Add("--disable-dev-shm-usage"); }
            foreach (var arg in new[]
                     {
                         "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                         "--user-data-dir=" + Path.Combine(tmp, "profile"), "--allow-file-access-from-files",
                         "--no-pdf-header-footer", "--print-to-pdf-no-header", "--virtual-time-budget=15000",
                         "--print-to-pdf=" + outPdf, new Uri(src).AbsoluteUri,
                     })
                psi.ArgumentList.Add(arg);
            psi.Environment["HOME"] = tmp;
            using var proc = Process.Start(psi)!;
            _ = proc.StandardOutput.ReadToEndAsync(ct);
            _ = proc.StandardError.ReadToEndAsync(ct);
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
            timeout.CancelAfter(TimeSpan.FromMinutes(4));
            try { await proc.WaitForExitAsync(timeout.Token); }
            catch (OperationCanceledException)
            {
                proc.Kill(entireProcessTree: true);
                if (ct.IsCancellationRequested) throw;
            }
            if (!File.Exists(outPdf) || new FileInfo(outPdf).Length < 1000)
            {
                log.Warn($"Chromium did not produce a PDF for '{title}'");
                return null;
            }
            File.Move(outPdf, pdf, overwrite: true);
            Remember(folder, Path.GetFileName(pdf));
            log.Info($"PDF: {PyCompat.RelPath(pdf, options.CoursesPath)}");
            return pdf;
        }
        finally
        {
            try { Directory.Delete(tmp, recursive: true); } catch (IOException) { } catch (UnauthorizedAccessException) { }
        }
    }

    private static void Remember(string folder, string name)
    {
        var st = DocState.Load(folder);
        if (st is null || st.PdfName == name) return;
        st.PdfName = name;
        st.Save(folder);
    }
}
