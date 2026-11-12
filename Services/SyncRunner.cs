using Lectern.Data;
using Microsoft.EntityFrameworkCore;

namespace Lectern.Services;

public enum SyncStart { Started, Queued, Busy }

/// <summary>
/// Runs syncs one at a time, in the background: each document, then the change log, PDFs and a history snapshot.
/// </summary>
public sealed class SyncRunner(
    IDbContextFactory<LecternDb> dbFactory,
    DocsService docs,
    SettingsService settings,
    BitdocsClient client,
    ActivityLog log,
    PdfBuilder pdf,
    HistoryService history,
    LecternOptions options,
    IHostApplicationLifetime lifetime)
{
    private readonly SemaphoreSlim _gate = new(1, 1);

    /// <summary>What is running now, for the status pill ("Syncing all docs"…); null when idle.</summary>
    public string? Running { get; private set; }
    public string? Current { get; private set; }

    public event Action? Changed;

    private readonly Lock _queueGate = new();
    private readonly HashSet<string> _queued = [];

    /// <summary>
    /// Starts a sync in the background. When one is already running, specific documents are queued and synced
    /// right after it (returns <see cref="SyncStart.Queued"/>); a full sync is refused.
    /// </summary>
    public SyncStart TryStart(IReadOnlyCollection<string>? guids, string trigger, string what)
    {
        if (!_gate.Wait(0))
        {
            if (guids is null) return SyncStart.Busy;
            lock (_queueGate) _queued.UnionWith(guids);
            return SyncStart.Queued;
        }
        Running = what;
        Changed?.Invoke();
        _ = Task.Run(async () =>
        {
            try { await RunLockedAsync(guids, trigger); }
            finally { Release(); }
        });
        return SyncStart.Started;
    }

    /// <summary>Waits for any running job, then syncs (the scheduler uses this so a due run is never skipped).</summary>
    public async Task RunAsync(IReadOnlyCollection<string>? guids, string trigger, string what, CancellationToken ct)
    {
        await _gate.WaitAsync(ct);
        Running = what;
        Changed?.Invoke();
        try { await RunLockedAsync(guids, trigger); }
        finally { Release(); }
    }

    /// <summary>Rebuilds one document's PDF in the background.</summary>
    public bool TryRebuildPdf(string guid)
    {
        if (!_gate.Wait(0)) return false;
        Running = "Building PDF";
        Changed?.Invoke();
        _ = Task.Run(async () =>
        {
            try
            {
                if (await docs.GetAsync(guid) is { } d) await pdf.BuildAsync(docs.Info(d), force: true, lifetime.ApplicationStopping);
            }
            catch (Exception e) { log.Error($"PDF export failed: {e.Message}"); }
            finally { Release(); }
        });
        return true;
    }

    private void Release()
    {
        Running = null;
        Current = null;
        _gate.Release();
        docs.NotifyChanged();
        Changed?.Invoke();
        // Documents added while that ran.
        List<string> next;
        lock (_queueGate)
        {
            next = _queued.ToList();
            _queued.Clear();
        }
        if (next.Count > 0 && TryStart(next, "added", $"Downloading {next.Count} new doc(s)") != SyncStart.Started)
            lock (_queueGate) _queued.UnionWith(next);
    }

    private async Task RunLockedAsync(IReadOnlyCollection<string>? guids, string trigger)
    {
        var ct = lifetime.ApplicationStopping;
        var all = await docs.ListAsync();
        var selected = guids is null ? all : all.Where(d => guids.Contains(d.Guid)).ToList();
        if (selected.Count == 0)
        {
            log.Info("Nothing to sync: add a document first.");
            return;
        }
        var opts = await settings.OptionsAsync();
        var run = new SyncRun { StartedAt = DateTime.UtcNow, Trigger = trigger, Docs = selected.Count };

        // Documents that share a folder each get a sub-folder named after their title.
        static string? Norm(string? f) => f?.Trim('/').ToLowerInvariant();
        var targets = all.Select(d => Norm(d.Folder)).Where(f => f is not null).ToList();

        var results = new List<DocResult>();
        var unknown = new HashSet<string>();
        foreach (var d in selected)
        {
            if (ct.IsCancellationRequested) break;
            Current = d.Title ?? d.Url;
            Changed?.Invoke();
            var shared = d.Folder is not null && targets.Count(t => t == Norm(d.Folder)) > 1;
            try
            {
                var sync = new DocSync(client, log, options.CoursesPath, opts.Videos, new ShareLink(d.Host, d.Guid),
                    d.Folder, shared, docs.FolderOf(d) ?? (d.OutputPath is null ? null : Path.Combine(options.CoursesPath, d.OutputPath)));
                var r = await sync.RunAsync(ct);
                results.Add(r);
                unknown.UnionWith(r.UnknownTypes);
                await docs.SaveResultAsync(d.Guid, r, null);
                log.Info($"Done '{r.Title}': {r.Changes.Count} change(s)");
                foreach (var c in r.Changes) log.Info($"   {c.Kind}: {c.Path}");
            }
            catch (Exception e) when (e is not OperationCanceledException || !ct.IsCancellationRequested)
            {
                run.Failed++;
                log.Error($"syncing {d.Url}: {e.Message}");
                await docs.SaveResultAsync(d.Guid, null, e.Message);
            }
        }
        Current = null;
        if (unknown.Count > 0) log.Info("Note: unhandled node types (content kept as text): " + string.Join(", ", unknown.Order(StringComparer.Ordinal)));

        foreach (var r in results)
            foreach (var c in r.Changes)
                run.Changes.Add(new ChangeEntry
                {
                    DocGuid = r.Guid, DocTitle = r.Title, DocFolder = PyCompat.RelPath(r.Out, options.CoursesPath),
                    Kind = c.Kind, Path = c.Path,
                    Diff = r.Diffs.TryGetValue(c.Path, out var diff) ? string.Join("\n", diff) : null,
                });
        run.FinishedAt = DateTime.UtcNow;
        await using (var db = await dbFactory.CreateDbContextAsync())
        {
            db.SyncRuns.Add(run);
            await db.SaveChangesAsync(CancellationToken.None);
        }
        docs.NotifyChanged();

        // After the sync: PDFs and a history snapshot. Each step is optional and never fails the sync.
        if (opts.Pdf)
        {
            Running = "Building PDFs";
            Changed?.Invoke();
            foreach (var r in results)
            {
                try
                {
                    if (await docs.GetAsync(r.Guid) is { } d) await pdf.BuildAsync(docs.Info(d), force: r.Changes.Count > 0, ct);
                }
                catch (Exception e) when (e is not OperationCanceledException) { log.Warn($"PDF export failed for '{r.Title}': {e.Message}"); }
            }
        }
        if (opts.History)
        {
            try
            {
                var summary = string.Join("; ", results.Where(r => r.Changes.Count > 0).Select(r => $"{r.Title}: {r.Changes.Count} change(s)"));
                var folders = (await docs.ListAsync()).Select(docs.FolderOf).OfType<string>();
                history.Commit($"Sync {DateTime.Now:yyyy-MM-dd HH:mm}{(summary.Length > 0 ? " - " + summary : "")}", folders);
            }
            catch (Exception e) { log.Warn($"history snapshot failed: {e.Message}"); }
        }
    }
}
