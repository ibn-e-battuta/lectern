namespace Lectern.Services;

/// <summary>Syncs on the schedule. Runs missed while the machine slept or the container was stopped happen as soon as it's back.</summary>
public sealed class Scheduler(SettingsService settings, SyncRunner runner, ActivityLog log) : BackgroundService
{
    protected override async Task ExecuteAsync(CancellationToken ct)
    {
        var schedule = await settings.ScheduleAsync();
        log.Info($"Lectern {AppInfo.Version} started. Schedule: {schedule.Describe()}");
        if (await settings.LastScheduledRunAsync() is null) await settings.SetLastScheduledRunAsync(DateTime.Now);
        var shown = schedule.Describe();

        while (!ct.IsCancellationRequested)
        {
            try
            {
                schedule = await settings.ScheduleAsync();
                if (schedule.Describe() != shown) log.Info($"Schedule changed: {shown = schedule.Describe()}");
                var now = DateTime.Now;
                var last = await settings.LastScheduledRunAsync() ?? now;
                DateTime? due = null;
                if (schedule.Enabled)
                {
                    if (schedule.Mode == "every")
                    {
                        var next = last.AddHours(schedule.Hours);
                        if (next <= now) due = next;
                    }
                    else if (schedule.LastDue(now) is { } d && d > last) due = d;
                }
                if (due is not null)
                {
                    log.Info($"Scheduled sync (due {due:yyyy-MM-dd HH:mm})");
                    await settings.SetLastScheduledRunAsync(now);
                    await runner.RunAsync(null, "schedule", "Syncing all docs", ct);
                }
            }
            catch (OperationCanceledException) when (ct.IsCancellationRequested) { break; }
            catch (Exception e) { log.Error($"scheduler: {e.Message}"); }

            try { await Task.Delay(TimeSpan.FromSeconds(30), ct); }
            catch (OperationCanceledException) { break; }
        }
    }
}
